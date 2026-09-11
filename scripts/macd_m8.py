#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MACD M8 动量状态机采集器（移植自 Pine 脚本 MACD v2.4 的 MTF 表格逻辑）

对监控池内每个币，用币安收盘 K 线计算 15m/1h/4h/1d 四个周期的 MACD power 分，
按原脚本 if-elif 优先级映射到 8 状态，落盘供与 V3 / B3.0 / 爆发线做消融。

用法:
    python macd_m8.py --once              # 扫描一次默认监控池
    python macd_m8.py --once --extra ARB,SUSHI   # 默认池 + 临时加币
    python macd_m8.py --loop              # 每 4h 循环（调试用）

输出（脚本同目录）:
    macd_m8.jsonl  每行 = {ts, symbol, 4 个周期的 power/zone/状态}

监控池 = B3.0 主流池(MAJORS) + AI500 score 榜 top3 + --extra 指定币
数据源: 币安 K 线（走本地代理 10809），只用已收盘 K 线（防重绘）

忠实移植的规则（与 Pine 一一对应）:
    power = (macd>signal ? +1 : -1) + (macd上升?+1:0) + (signal上升?+1:0) + histZone(±1)
    histZone: hist>0 ? (hist升?2:1) : (hist升?-1:-2)      # 🔥🚀🐋💀
    8 状态优先级: 超级拉升 > 拉升 > 黄金一击 > 回调 > 混合 > 反弹 > 刀落 > 犹豫
    周期参数(固定基准, 自适应引擎关闭): 15m:12-26-9  1h:16-34-12  4h:20-40-15  1d:12-26-9
v1 范围: 只采集 power/状态/zone。背离(pivot 5 根确认滞后)留 v1.1，先不进规则。
"""
import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT_FILE = HERE / "macd_m8.jsonl"

BINANCE_PROXY = os.environ.get("B300_PROXY", "http://127.0.0.1:7890")
UA = "Mozilla/5.0"
REQUEST_GAP = 0.35          # 币安权重友好间隔
KLINE_LIMIT = 500           # EMA 预热足够（最慢 4h: 40+15）

# B3.0 主流池（与 b300_collector.py 保持一致）
MAJORS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "LINK", "AAVE", "HYPE"]

# Pine f_get_tf_base_periods 固定基准
TF_PARAMS = {
    "15m": (12, 26, 9),
    "1h":  (16, 34, 12),
    "4h":  (20, 40, 15),
    "1d":  (12, 26, 9),
}
TF_INTERVAL = {"15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}


# ---------------- HTTP ----------------

def proxied_get_json(url: str, retries: int = 3):
    last_err = None
    for i in range(retries):
        try:
            op = urllib.request.build_opener(urllib.request.ProxyHandler(
                {"http": BINANCE_PROXY, "https": BINANCE_PROXY}))
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with op.open(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            last_err = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"GET failed: {url} ({last_err})")


def plain_get_json(url: str, retries: int = 3):
    """vergex 接口直连即可。"""
    last_err = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=12) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            last_err = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"GET failed: {url} ({last_err})")


# ---------------- MACD 核心（Pine 移植） ----------------

def ema(series, length):
    alpha = 2.0 / (length + 1)
    out, e = [], None
    for v in series:
        e = v if e is None else alpha * v + (1 - alpha) * e
        out.append(e)
    return out


def macd_state(closes, fast, slow, sig_len):
    """返回 (power, macd_above, macd_up, signal_up, zone, hist) —— 只用最后两根收盘 K。"""
    macd = [f - s for f, s in zip(ema(closes, fast), ema(closes, slow))]
    sig = ema(macd, sig_len)
    hist = [m - s for m, s in zip(macd, sig)]
    m0, m1, s0, s1 = macd[-1], macd[-2], sig[-1], sig[-2]
    h0 = hist[-1]
    macd_above = m0 > s0
    macd_up = m0 > m1
    signal_up = s0 > s1
    hist_up = h0 > hist[-2]
    zone = (2 if hist_up else 1) if h0 > 0 else (-1 if hist_up else -2)
    power = (1 if macd_above else -1) + (1 if macd_up else 0) \
        + (1 if signal_up else 0) + (1 if zone > 0 else -1 if zone < 0 else 0)
    return power, macd_above, macd_up, signal_up, zone, h0


ZONE_ICON = {2: "🔥", 1: "🚀", -1: "🐋", -2: "💀"}


def classify(p1, p2, p3, p4):
    """Pine 耳语模式 8 状态，if-elif 优先级原样保留，勿改顺序。"""
    if p1 >= 2 and p2 >= 2 and p3 >= 2 and p4 >= 2:
        return "🚀🚀超级拉升"
    if p3 >= 2 and p4 >= 2 and (p1 >= 1 or p2 >= 1):
        return "🚀拉升"
    if p3 >= 2 and p4 >= 2 and p1 <= -1:
        return "💎黄金一击"
    if p4 >= 3 and p1 <= -2:
        return "🎯回调"
    if (p1 >= 2 and p4 <= -1) or (p4 >= 2 and p1 <= -1):
        return "⚠️混合"
    if p4 <= -2 and p1 >= 2:
        return "⚡反弹"
    if p1 <= -2 and p2 <= -2 and p3 <= -2 and p4 <= -2:
        return "🩸刀落"
    return "👀犹豫"


# ---------------- 数据获取 ----------------

def fetch_klines(symbol: str, interval: str):
    """返回已收盘 K 线的 close 列表（剔除最后一根未收盘 K，防重绘）。"""
    url = (f"https://api.binance.com/api/v3/klines"
           f"?symbol={symbol}&interval={interval}&limit={KLINE_LIMIT}")
    rows = proxied_get_json(url)
    now_ms = time.time() * 1000
    closes = [float(r[4]) for r in rows if r[6] < now_ms]   # r[6]=closeTime
    if len(closes) < 120:
        raise RuntimeError(f"{symbol} {interval} 收盘K不足: {len(closes)}")
    return closes


def fetch_ai500_top(n=3):
    """AI500 score 榜 top N（VergeX 公开接口，直连）。"""
    d = plain_get_json("https://vergex.trade/trending-category?lang=zh&key=ai500")
    assets = d.get("assets") or d.get("category", {}).get("assets") or []
    assets.sort(key=lambda a: a.get("score") or 0, reverse=True)
    return [a["symbol"] for a in assets[:n]]


# ---------------- 主流程 ----------------

def scan(symbols):
    now = datetime.now(timezone(timedelta(hours=8)))
    ts = now.isoformat(timespec="seconds")
    records = []
    print(f"[macd_m8] {ts} 监控池 {len(symbols)} 币: {', '.join(symbols)}")

    for sym in symbols:
        pair = f"{sym}USDT"
        time.sleep(REQUEST_GAP)
        rec = {"ts": ts, "symbol": sym, "tfs": {}}
        ok = True
        for tf, (f, s, g) in TF_PARAMS.items():
            try:
                closes = fetch_klines(pair, TF_INTERVAL[tf])
            except Exception as e:
                rec["error"] = str(e)[:90]
                ok = False
                break
            p, above, up, s_up, zone, hist = macd_state(closes, f, s, g)
            rec["tfs"][tf] = {"power": p, "zone": zone, "zone_icon": ZONE_ICON[zone],
                              "macd_above": above, "macd_up": up,
                              "signal_up": s_up, "hist": round(hist, 6)}
        if not ok:
            print(f"  {sym:<10} ✗ {rec.get('error')}")
            records.append(rec)
            continue
        p1 = rec["tfs"]["15m"]["power"]
        p2 = rec["tfs"]["1h"]["power"]
        p3 = rec["tfs"]["4h"]["power"]
        p4 = rec["tfs"]["1d"]["power"]
        rec["state"] = classify(p1, p2, p3, p4)
        rec["powers"] = {"15m": p1, "1h": p2, "4h": p3, "1d": p4}
        records.append(rec)
        zones = " ".join(f"{tf}:{rec['tfs'][tf]['zone_icon']}" for tf in ("15m", "1h", "4h", "1d"))
        print(f"  {sym:<10} p=[{p1:>2},{p2:>2},{p3:>2},{p4:>2}]  {rec['state']:<10} {zones}")

    with open(OUT_FILE, "a", encoding="utf-8") as fp:
        for r in records:
            fp.write(json.dumps(r, ensure_ascii=False) + "\n")

    states = {}
    for r in records:
        if "state" in r:
            states[r["state"]] = states.get(r["state"], 0) + 1
    print(f"[done] {ts} 有效={sum(states.values())}/{len(records)} 状态分布: {states or '无'}")
    print(f"[落盘] {OUT_FILE.name}")
    return records


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="扫描一次后退出")
    ap.add_argument("--loop", action="store_true", help="每 4h 循环扫描")
    ap.add_argument("--extra", type=str, default="",
                    help="临时加币，逗号分隔，如 ARB,SUSHI")
    ap.add_argument("--ai500-top", type=int, default=3, help="AI500 榜取前 N（默认 3）")
    args = ap.parse_args()

    pool = list(dict.fromkeys(MAJORS))          # 去重保序
    try:
        ai500 = fetch_ai500_top(args.ai500_top)
        pool += [s for s in ai500 if s not in pool]
        print(f"[pool] AI500 top{len(ai500)}: {', '.join(ai500)}")
    except Exception as e:
        print(f"[warn] AI500 榜拉取失败，仅扫主流池: {e}", file=sys.stderr)
    if args.extra:
        pool += [s.strip().upper() for s in args.extra.split(",") if s.strip()]

    if args.loop:
        while True:
            try:
                scan(pool)
            except Exception as e:
                print(f"[error] {e}", file=sys.stderr)
            time.sleep(4 * 3600)
    else:
        scan(pool)


if __name__ == "__main__":
    main()