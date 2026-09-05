#!/usr/bin/env python3
"""
补齐 MACD whisper 离线复现所需的多周期 OHLCV
============================================
为什么需要它
------------
现有 user_data/ai500_local/klines_hist/*.feather 只有 [t, close, quote_volume,
taker_buy_quote_volume, net_flow]，且只覆盖 2026-08-18 起的 16 天。这对 whisper
复现有两处致命不足：

1. **缺 high/low/volume** —— 自适应周期引擎的三个乘数需要它们：
     f_get_volatility_multiplier → ATR14（要 high/low）
     f_get_trend_strength_multiplier → ta.dmi(14,14)（要 high/low）
     f_get_volume_multiplier → volume
2. **历史太短** —— whisper 的 p4 来自 1d 周期，而
     MACD 本身要 26+9 ≈ 35 根日线
     regime 乘数的 ta.ema(close,200) 要 200 根日线
   16 天只有 16 根，p4 完全由 f_ema 的播种值主导 → 不可信。
   而 p4 恰好是 karisik 买/卖两支的**唯一判别项**，错了整个出场逻辑就复现不了。

起始日期怎么定
--------------
事件窗口从 2026-08-18 起。要让窗口内**任意**时点都有 300 根前置 bar：
  15m  300×15m = 75h    → 需 2026-08-14 前   本脚本取 2026-08-01
  1h   300h    = 12.5d  → 需 2026-08-05 前   本脚本取 2026-07-15
  4h   1200h   = 50d    → 需 2026-06-29 前   本脚本取 2026-06-01
  1d   300d             → 需 2025-10-22 前   本脚本取 2025-09-01
（300 = 策略里的 MACD_FETCH_LIMIT，与实盘取数长度一致，避免口径分叉。）

落盘
----
user_data/ai500_local/ohlcv/{SYMBOL}-{TF}.feather
列: t(UTC), open, high, low, close, volume, quote_volume
已存在且覆盖到目标终点的文件直接跳过 —— 可反复运行做增量补齐。

用法
----
  python3 scripts/ai500_fetch_ohlcv.py                 # 全部标的
  python3 scripts/ai500_fetch_ohlcv.py --symbols BTCUSDT,ETHUSDT
  python3 scripts/ai500_fetch_ohlcv.py --force         # 忽略已有文件重拉
"""
import argparse
import os
import sys
import time

import pandas as pd
import requests

BASE = "https://fapi.binance.com"
KLINES = BASE + "/fapi/v1/klines"
EXINFO = BASE + "/fapi/v1/exchangeInfo"

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL = os.path.join(_ROOT, "user_data", "ai500_local")
OUT_DIR = os.path.join(LOCAL, "ohlcv")
SYMS_FILE = os.path.join(LOCAL, "ohlcv_symbols.txt")

# 本机直连 fapi 可用；需要代理时设 LOCAL_PROXY
_P = os.environ.get("LOCAL_PROXY")
PROXY = {"http": _P, "https": _P} if _P else None

# tf -> 起始日期（见文件头「起始日期怎么定」）
TF_START = {
    "15m": "2026-08-01",
    "1h": "2026-07-15",
    "4h": "2026-06-01",
    "1d": "2025-09-01",
}
LIMIT = 1500                 # fapi 单次上限；weight=10
SLEEP = 0.3                  # 2400 weight/min 上限 → 200 req/min 足够安全
RETRY = 4
TIMEOUT = 25


def get(params):
    """带退避重试。限流时等待而非快速重试，宁慢不丢。"""
    last = None
    for i in range(RETRY):
        try:
            r = requests.get(KLINES, params=params, proxies=PROXY, timeout=TIMEOUT)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (418, 429):
                wait = 10 * (i + 1)
                print(f"  [warn] {r.status_code} 限流，等 {wait}s", flush=True)
                time.sleep(wait)
                continue
            last = f"HTTP {r.status_code} {r.text[:120]}"
            if r.status_code == 400:      # 标的不存在，不必重试
                return None
        except Exception as e:            # noqa: BLE001
            last = repr(e)
        time.sleep(2.0 * (i + 1))
    print(f"  [warn] 放弃 {params.get('symbol')} {params.get('interval')}: {last}",
          flush=True)
    return None


def fetch_tf(symbol: str, tf: str, start_ms: int, end_ms: int) -> pd.DataFrame | None:
    """分页拉满 [start, end)。以 openTime 推进，避免重复/漏页。"""
    rows, cur = [], start_ms
    while cur < end_ms:
        j = get({"symbol": symbol, "interval": tf,
                 "startTime": cur, "endTime": end_ms, "limit": LIMIT})
        if j is None:
            return None
        if not j:
            break
        rows.extend(j)
        nxt = int(j[-1][0]) + 1
        if nxt <= cur:            # 防御：不推进就跳出，避免死循环
            break
        cur = nxt
        time.sleep(SLEEP)
        if len(j) < LIMIT:
            break
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=[
        "t", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"])
    df["t"] = pd.to_datetime(df.t.astype("int64"), unit="ms", utc=True)
    for c in ("open", "high", "low", "close", "volume", "quote_volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = (df[["t", "open", "high", "low", "close", "volume", "quote_volume"]]
          .drop_duplicates(subset=["t"]).sort_values("t").reset_index(drop=True))
    # 最后一根可能未收盘 —— 复现时必须只用已收盘 bar，这里直接砍掉，
    # 避免下游误用（同源铁律：信号时刻只能看已收盘数据）
    return df.iloc[:-1] if len(df) > 1 else df


def valid_symbols() -> set:
    try:
        r = requests.get(EXINFO, proxies=PROXY, timeout=TIMEOUT)
        if r.status_code == 200:
            return {s["symbol"] for s in r.json()["symbols"]
                    if s.get("status") == "TRADING"}
    except Exception as e:                # noqa: BLE001
        print(f"[warn] exchangeInfo 拉取失败（跳过标的校验）: {e!r}", flush=True)
    return set()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", help="逗号分隔；缺省读 ohlcv_symbols.txt")
    ap.add_argument("--force", action="store_true", help="忽略已有文件重拉")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    if args.symbols:
        syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
    elif os.path.exists(SYMS_FILE):
        syms = [l.strip() for l in open(SYMS_FILE, encoding="utf-8") if l.strip()]
    else:
        sys.exit(f"[fatal] 没有 --symbols，也找不到 {SYMS_FILE}")

    live = valid_symbols()
    if live:
        bad = [s for s in syms if s not in live]
        if bad:
            print(f"[info] 剔除 {len(bad)} 个非在架标的: {', '.join(bad[:10])}"
                  f"{' ...' if len(bad) > 10 else ''}", flush=True)
        syms = [s for s in syms if s in live]

    end_ms = int(pd.Timestamp.utcnow().timestamp() * 1000)
    print(f"[info] {len(syms)} 标的 × {len(TF_START)} 周期 | 代理 "
          f"{'on' if PROXY else 'off'}", flush=True)

    t0, done, skip, fail = time.time(), 0, 0, []
    for i, sym in enumerate(syms, 1):
        for tf, start in TF_START.items():
            path = os.path.join(OUT_DIR, f"{sym}-{tf}.feather")
            if os.path.exists(path) and not args.force:
                skip += 1
                continue
            start_ms = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
            df = fetch_tf(sym, tf, start_ms, end_ms)
            if df is None or df.empty:
                fail.append(f"{sym}-{tf}")
                continue
            df.to_feather(path)
            done += 1
        if i % 10 == 0:
            print(f"[info]   {i}/{len(syms)}  新增 {done} 跳过 {skip} 失败 "
                  f"{len(fail)}  ({time.time()-t0:.0f}s)", flush=True)

    print(f"\n[done] 新增 {done} | 跳过 {skip} | 失败 {len(fail)} "
          f"| 耗时 {(time.time()-t0)/60:.1f} min", flush=True)
    if fail:
        print(f"[warn] 失败: {', '.join(fail[:20])}"
              f"{' ...' if len(fail) > 20 else ''}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
