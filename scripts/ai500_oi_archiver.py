#!/usr/bin/env python3
"""
币安 openInterestHist 归档器 —— 在 bot 机器上常驻/定时运行
==========================================================
为什么必须单独存一份
--------------------
OI（未平仓合约）**不在 K 线里**，事后无法从任何价格数据重建。
现在唯一的 OI 记录是策略 bot_loop_start 顺手写进
user_data/ai500_netflow_oi_history.csv 的快照——采集进程一断，那段就永久空洞。
（本项目 2026-08-18~08-25 的 OI 覆盖率只有 18%~67%，就是这么丢的，
  直接导致 OI 闸门实验的可用样本从 16 天缩到 9 天。）

币安 /futures/data/openInterestHist 提供 5 分钟粒度的 OI 历史，
但**只保留最近 30 天**。所以：
  - 它能补回最近 30 天的空洞（一次性救火）
  - 但只要超过 30 天没归档，那段就再也拿不回来
这就是本脚本要长期跑的理由——它不是"更好的数据源"，是"防止数据消失"。

与 bot 快照的关系
-----------------
两者互为独立源，都留着：
  bot 快照   —— 策略实际看到的值（含 vergex_fallback），决定策略当时的行为
  本脚本     —— 币安官方口径，用于交叉校验与补洞
口径不保证一致，**不要混用**；哪个用于哪个分析要显式声明。

落盘
----
user_data/ai500_oi_hist/oi_YYYYMMDD.csv.gz   （UTC 日切）
列: ts_utc, symbol, sum_oi, sum_oi_value
按 (symbol, ts_utc) 去重，重复运行幂等——可以放心多跑。

每次拉最近 500 个点 ≈ 41 小时，故：
  每 6h 跑一次 → 可容忍约 1.5 天的宕机而不丢数据
  每 24h 跑一次 → 只剩 17h 余量，机器一宕就可能丢

用法
----
  python3 scripts/ai500_oi_archiver.py              # 跑一次
  python3 scripts/ai500_oi_archiver.py --once-full  # 首次运行：尽量回补
  python3 scripts/ai500_oi_archiver.py --dry-run    # 只看会拉哪些标的

cron（建议）：
  0 */6 * * * cd /home/gyan/freqtrade && /usr/bin/python3 scripts/ai500_oi_archiver.py \
      >> user_data/ai500_oi_hist/archiver.log 2>&1
"""
import argparse
import datetime as dt
import os
import sys
import time

import pandas as pd
import requests

BASE = "https://fapi.binance.com"
EXINFO = BASE + "/fapi/v1/exchangeInfo"
OIHIST = BASE + "/futures/data/openInterestHist"

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(_ROOT, "user_data", "ai500_oi_hist")

# bot 机器直连币安（它本来就在交易），默认不走代理。
# 本地调试机需要代理时设 LOCAL_PROXY=http://127.0.0.1:10809
_P = os.environ.get("LOCAL_PROXY")
PROXY = {"http": _P, "https": _P} if _P else None

PERIOD = "5m"
LIMIT = 500                 # 500 × 5m ≈ 41.7h
SLEEP = 0.2                 # /futures/data/ 有独立 IP 限流，放慢些换稳定
RETRY = 3
TIMEOUT = 20


def get(url, params=None):
    """带退避重试。宁可慢，不要因为一次 429 丢一天数据。"""
    last = None
    for i in range(RETRY):
        try:
            r = requests.get(url, params=params, proxies=PROXY, timeout=TIMEOUT)
            if r.status_code == 200:
                return r.json()
            # 418/429 是限流，退避比重试更重要
            if r.status_code in (418, 429):
                wait = 5 * (i + 1)
                print(f"[warn] {r.status_code} 限流，等 {wait}s", flush=True)
                time.sleep(wait)
                continue
            last = f"HTTP {r.status_code} {r.text[:120]}"
        except Exception as e:                       # noqa: BLE001
            last = repr(e)
        time.sleep(1.5 * (i + 1))
    print(f"[warn] 放弃 {params}: {last}", flush=True)
    return None


def usdt_perps() -> list[str]:
    j = get(EXINFO)
    if not j:
        sys.exit("[fatal] 拉不到 exchangeInfo，检查网络/代理")
    return sorted(
        s["symbol"] for s in j["symbols"]
        if s.get("contractType") == "PERPETUAL"
        and s.get("quoteAsset") == "USDT"
        and s.get("status") == "TRADING"
    )


def merge_day(day: str, new: pd.DataFrame) -> int:
    """把 new 并入某一天的文件，按 (symbol, ts_utc) 去重。返回净新增行数。"""
    path = os.path.join(OUT_DIR, f"oi_{day}.csv.gz")
    if os.path.exists(path):
        old = pd.read_csv(path)
        old["ts_utc"] = pd.to_datetime(old.ts_utc, utc=True)
        before = len(old)
        new = pd.concat([old, new], ignore_index=True)
    else:
        before = 0
    new = (new.drop_duplicates(subset=["symbol", "ts_utc"], keep="last")
              .sort_values(["ts_utc", "symbol"]))
    new.to_csv(path, index=False, compression="gzip")
    return len(new) - before


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--once-full", action="store_true",
                    help="首次运行：同样只能拿到 30 天上限内的数据，"
                         "此开关只是放宽单标的失败容忍度")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    syms = usdt_perps()
    print(f"[info] USDT 永续 {len(syms)} 个 | 代理 {'on' if PROXY else 'off'}",
          flush=True)
    if args.dry_run:
        print("  " + ", ".join(syms[:20]) + f" ... (共 {len(syms)})")
        return 0

    rows, fail = [], []
    t0 = time.time()
    for i, s in enumerate(syms, 1):
        j = get(OIHIST, {"symbol": s, "period": PERIOD, "limit": LIMIT})
        if not j:
            fail.append(s)
        else:
            for k in j:
                rows.append((int(k["timestamp"]), s,
                             float(k["sumOpenInterest"]),
                             float(k["sumOpenInterestValue"])))
        if i % 50 == 0:
            print(f"[info]   {i}/{len(syms)}  已收 {len(rows):,} 行 "
                  f"({time.time()-t0:.0f}s)", flush=True)
        time.sleep(SLEEP)

    if not rows:
        sys.exit("[fatal] 一行都没拿到——网络或限流问题，本次不写文件")

    df = pd.DataFrame(rows, columns=["ts_ms", "symbol", "sum_oi", "sum_oi_value"])
    df["ts_utc"] = pd.to_datetime(df.ts_ms, unit="ms", utc=True)
    df = df.drop(columns=["ts_ms"])
    df["day"] = df.ts_utc.dt.strftime("%Y%m%d")

    total_new = 0
    for day, g in df.groupby("day"):
        n = merge_day(day, g.drop(columns=["day"]).copy())
        total_new += n
        print(f"[ok] {day}: +{n:,} 行 (本次抓到 {len(g):,})", flush=True)

    print(f"\n[done] 净新增 {total_new:,} 行 | 失败标的 {len(fail)} "
          f"| 耗时 {(time.time()-t0)/60:.1f} min", flush=True)
    if fail:
        print(f"[warn] 失败: {', '.join(fail[:20])}"
              f"{' ...' if len(fail) > 20 else ''}", flush=True)
        # 少量失败不算致命——下次跑还有 41h 重叠窗口可以补上
        if len(fail) > len(syms) * 0.3 and not args.once_full:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
