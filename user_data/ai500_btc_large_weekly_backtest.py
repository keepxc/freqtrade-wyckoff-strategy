#!/usr/bin/env python3
"""
AI500 线路B 大单净流信号 — 周回测脚本（TASK 127）

用法: python3 user_data/ai500_btc_large_weekly_backtest.py [--days N]
默认 N=8 天（覆盖最近一周 + 余量），用大单桶 + 币安 15m K线重算信号。

信号定义（与 TASK 127 一致）:
  入场: 2h 滚动大单净流(15m桶×8, perp) > +80M USD 且 BTC 15m 同期上涨
  出场: 滚动净流转负 / 价格较入场回落 0.5% / 持仓超 6h
  手续费: 双边 0.1%

输出: 窗口内交易笔数、胜率、累计盈亏%、最大回撤、利润集中度(前3笔占比)
       + 与基线对比 (07-03~08-31: 94笔/50%/+16.68%)

依赖: requests（freqtrade venv 自带）。网络走代理 http://127.0.0.1:7890。
"""
import json
import os
import sys
import time
import argparse
import csv
from collections import defaultdict
from datetime import datetime, timezone, timedelta

import requests

PROXY = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}
FEE = 0.001          # 双边 0.1%
THRESHOLD = 80e6     # 2h 滚动净流阈值
WIN = 8              # 15m 桶 × 8 = 2h
MAX_HOLD = 6 * 3600  # 最长持仓 6h
REFLOW_EXIT = 0.5    # 价格回落 0.5% 出场
TZ = timezone(timedelta(hours=8))

AGG_DIR = "/home/gyan/freqtrade/user_data/agg_trades"
IMB_PRICE_STEP = 78.0
IMB_RATIO_THRESH = 3.0

BASELINE = "07-03~08-31: 94笔 / 50%胜率 / +16.68% / 前3笔69%"


def http_get(url, params=None, retries=3):
    """带重试的 GET（糖哥接口偶发 SSL EOF）"""
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, proxies=PROXY, timeout=30)
            r.raise_for_status()
            return r
        except requests.exceptions.RequestException as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise last


def fetch_large_trades(days: int):
    """拉糖哥 perp 15m 大单桶，返回 [(time_ms, buyUsd, sellUsd)]"""
    url = "https://stock.tanggestock.com/api/realtime-events/large-trades"
    params = {"symbol": "BTC", "interval": "15m", "market": "perp",
              "anchor": int(time.time() * 1000)}
    r = http_get(url, params)
    r.raise_for_status()
    j = r.json()
    if j.get("status") != "ok":
        raise RuntimeError(f"接口异常: {j}")
    rows = []
    for row in j.get("rows", []):
        if "buyUsd" in row and "sellUsd" in row:
            rows.append((int(row["time"]), float(row["buyUsd"]), float(row["sellUsd"])))
    rows.sort()
    cov = j.get("coverage", {})
    print(f"[大单] {len(rows)} 桶, coverage {cov.get('fromMs')} ~ {cov.get('toMs')}")
    return rows


def fetch_btc_klines(days: int):
    """拉币安 BTCUSDT 15m K线（分批，endTime 拼接），返回 [(open_ms, close)]"""
    url = "https://fapi.binance.com/fapi/v1/klines"
    end = int(time.time() * 1000)
    start = end - days * 86400 * 1000
    out = {}
    while True:
        params = {"symbol": "BTCUSDT", "interval": "15m", "limit": 1500,
                  "endTime": end}
        r = http_get(url, params)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        for k in batch:
            out[int(k[0]) // 1000] = float(k[4])  # ms -> 秒，与大单桶 time 单位一致
        first = batch[0][0]
        if first <= start:
            break
        end = first - 60000  # 上批首根 - 1 分钟，防重叠
        time.sleep(0.3)
    items = sorted(out.items())
    print(f"[价格] {len(items)} 根 15m K线, {datetime.fromtimestamp(items[0][0], TZ).strftime('%m-%d %H:%M')} ~ {datetime.fromtimestamp(items[-1][0], TZ).strftime('%m-%d %H:%M')}")
    return items


def backtest(rows, klines, cutoff=None):
    """重算 2h 滚动净流 + 信号，返回交易明细 [(entry_ts, exit_ts, pnl%)]
    cutoff: 只统计该秒之后的桶（窗口起点）"""
    net_times = [t for t, _, _ in rows]
    netflow = {t: (b - s) for t, b, s in rows}

    # 滚动 2h 净流
    roll = {}
    for i, t in enumerate(net_times):
        roll[t] = sum(netflow[net_times[j]] for j in range(max(0, i - WIN + 1), i + 1))

    # 价格查找（<= ts 的最近一根收盘）
    kts = [t for t, _ in klines]
    kpx = {t: p for t, p in klines}

    def px_at(ts):
        lo, hi = 0, len(kts) - 1
        if ts < kts[0]:
            return None
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if kts[mid] <= ts:
                lo = mid
            else:
                hi = mid - 1
        return kpx[kts[lo]]

    trades = []
    in_pos = False
    entry_i = 0
    entry_px = 0.0
    for i, t in enumerate(net_times):
        if cutoff is not None and t < cutoff:
            continue  # 窗口起点之前的桶不产生新信号
        px = px_at(t)
        if px is None:
            continue
        if not in_pos:
            px2 = px_at(t - 2 * 3600)
            btc_up = px2 is not None and (px - px2) / px2 > 0
            if roll[t] > THRESHOLD and btc_up:
                in_pos = True
                entry_i = i
                entry_px = px
        else:
            pnl_pct = (px - entry_px) / entry_px * 100
            hit_sl = (px - entry_px) / entry_px * 100 <= -REFLOW_EXIT
            hit_net = roll[t] < 0
            hit_time = (t - net_times[entry_i]) > MAX_HOLD
            if hit_sl or hit_net or hit_time:
                trades.append((net_times[entry_i], t,
                               pnl_pct - FEE * 100))
                in_pos = False
    return trades


def stat_imbalance(days: int) -> dict:
    """统计本地 aggTrades 落盘的分层 imbalance 概况（观察数据，非信号）。

    对窗口内每个 30m 桶：按 0.1% 价格分层，算 buy/sell imbalance 最大比，
    统计 >3x 失衡的桶占比、平均强度。返回汇总 dict。
    """
    cutoff_ms = int(time.time() * 1000) - days * 86400 * 1000
    buckets = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))  # ts -> lvl -> [buy, sell]
    total = 0
    for fn in os.listdir(AGG_DIR):
        if not fn.startswith("agg_") or not fn.endswith(".csv"):
            continue
        fp = os.path.join(AGG_DIR, fn)
        if os.path.getmtime(fp) < cutoff_ms / 1000 - 86400:
            continue  # 文件整体早于窗口
        try:
            with open(fp, newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                next(reader, None)
                for row in reader:
                    if len(row) < 5:
                        continue
                    ts_ms = int(row[0])
                    if ts_ms < cutoff_ms:
                        continue
                    price = float(row[2]); qty = float(row[3])
                    maker = row[4].strip() == "1"
                    lvl = round(price / IMB_PRICE_STEP)
                    bkt = ts_ms // 1800_000 * 1800_000
                    if maker:
                        buckets[bkt][lvl][1] += qty
                    else:
                        buckets[bkt][lvl][0] += qty
                    total += 1
        except Exception as e:
            print(f"  [imb] 读文件 {fn} 失败: {e}")
    if not buckets:
        return {"total": 0, "buckets": 0, "buy_imb_ratio": 0, "sell_imb_ratio": 0,
                "avg_buy_imb": 0, "avg_sell_imb": 0, "note": "无落盘数据（采集器未运行？）"}

    buy_imb_buckets = sell_imb_buckets = 0
    buy_imbs: list[float] = []
    sell_imbs: list[float] = []
    for ts, lvls_data in buckets.items():
        lvls = sorted(lvls_data.keys())
        bmax = smax = 0.0
        for i, l in enumerate(lvls):
            b, s = lvls_data[l]
            s_below = lvls_data.get(lvls[i - 1], [0, 0])[1] if i > 0 else 0
            b_above = lvls_data.get(lvls[i + 1], [0, 0])[0] if i < len(lvls) - 1 else 0
            if b > 0 and s_below > 0:
                bmax = max(bmax, b / s_below)
            if s > 0 and b_above > 0:
                smax = max(smax, s / b_above)
        if bmax >= IMB_RATIO_THRESH:
            buy_imb_buckets += 1
        if smax >= IMB_RATIO_THRESH:
            sell_imb_buckets += 1
        buy_imbs.append(bmax)
        sell_imbs.append(smax)

    nb = len(buckets)
    return {
        "total": total, "buckets": nb,
        "buy_imb_ratio": buy_imb_buckets / nb * 100,
        "sell_imb_ratio": sell_imb_buckets / nb * 100,
        "avg_buy_imb": sum(buy_imbs) / nb,
        "avg_sell_imb": sum(sell_imbs) / nb,
        "note": f"{total:,} 笔 / {nb} 个30m桶",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=8)
    args = ap.parse_args()

    print(f"=== AI500 线路B 大单净流周回测 (TASK 127) — {datetime.now(TZ).strftime('%Y-%m-%d %H:%M %Z')} ===")
    print(f"窗口: 最近 {args.days} 天 | 阈值: {int(THRESHOLD/1e6)}M | 基线: {BASELINE}\n")

    rows = fetch_large_trades(args.days)
    klines = fetch_btc_klines(args.days)
    cutoff = int(time.time()) - args.days * 86400
    trades = backtest(rows, klines, cutoff=cutoff)

    # ⚠️ 2026-09-13 用户拍板【停用】imbalance 观察段（不恢复 aggTrades 采集）。
    #    原因：freqtrade-agg-collector 服务已 inactive，本地 user_data/agg_trades/
    #    仅剩 agg_20260831.csv（8/31 单日）。本函数读本地目录，**无数据时不报错、
    #    只是桶数变少**，于是产出「看起来正常」的僵尸统计（9/13 报告 279 桶 → 42 桶，
    #    实为 8/31 单日残量），易被误读为有效观察。
    #    stat_imbalance() 定义与 fetch_agg 相关代码保留——将来若恢复采集可直接启用。
    # imb = stat_imbalance(args.days)
    # print(f"=== 分层 imbalance 观察（{imb['note']}） ===")
    # if imb.get("note", "").startswith("无"):
    #     print(f"  {imb['note']}")
    # else:
    #     print(f"  30m桶中 buy_imb≥3x 占比: {imb['buy_imb_ratio']:.0f}% | "
    #           f"sell_imb≥3x 占比: {imb['sell_imb_ratio']:.0f}%")
    #     print(f"  平均 buy_imb 强度: {imb['avg_buy_imb']:.2f}x | "
    #           f"平均 sell_imb 强度: {imb['avg_sell_imb']:.2f}x")
    # print()

    if not trades:
        print("本窗口无交易信号（未触发 80M 净流或未匹配价格）")
        print("提示: 这是正常结果——信号本身低频，抓大放小。")
        return

    pnls = [p for _, _, p in trades]
    wins = sum(1 for p in pnls if p > 0)
    total = sum(pnls)
    cum = 0.0
    peak = 0.0
    maxdd = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        maxdd = min(maxdd, cum - peak)
    top3 = sum(sorted(pnls, reverse=True)[:3])
    top3_pct = top3 / total * 100 if total else 0

    print(f"笔数: {len(trades)} | 胜率: {wins/len(pnls)*100:.0f}% ({wins}胜/{len(pnls)-wins}负)")
    print(f"累计盈亏: {total:+.2f}% | 最大回撤: {maxdd:.2f}%")
    print(f"前3笔贡献: {top3:+.2f}% ({top3_pct:.0f}%)")
    print(f"均盈: {sum(p for p in pnls if p>0)/wins if wins else 0:+.2f}% | "
          f"均亏: {sum(p for p in pnls if p<0)/(len(pnls)-wins) if len(pnls)-wins else 0:+.2f}%")
    print("\n逐笔:")
    for e, x, p in trades:
        print(f"  {datetime.fromtimestamp(e, TZ).strftime('%m-%d %H:%M')} → "
              f"{datetime.fromtimestamp(x, TZ).strftime('%m-%d %H:%M')}  {p:+.2f}%")


if __name__ == "__main__":
    try:
        main()
    except requests.exceptions.RequestException as e:
        print(f"网络错误: {e}")
        print("提示: 检查代理 http://127.0.0.1:7890 是否可用")
        sys.exit(1)
