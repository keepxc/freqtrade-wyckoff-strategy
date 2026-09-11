#!/usr/bin/env python3
"""下载全部币安 USDT 永续的 1 年 1h K 线，供涨幅榜离线复现使用。

探索性——非结论。断点续传：已存在且覆盖到目标末端的 symbol 直接跳过。
限速：weight(limit=1500)=10，2400 weight/min 上限 → 0.5s/req ≈ 1200 weight/min（半载）。
"""
import json, os, sys, time
import pandas as pd, requests

P = {"http": "http://127.0.0.1:10809", "https": "http://127.0.0.1:10809"}
OUT = "user_data/ai500_local/kl1y"
END = int(pd.Timestamp("2026-09-11 00:00", tz="UTC").timestamp() * 1000)
START = int(pd.Timestamp("2025-09-11 00:00", tz="UTC").timestamp() * 1000)
S = requests.Session()

def fetch(sym):
    out, cur = [], START
    while cur < END:
        for a in range(5):
            try:
                r = S.get("https://fapi.binance.com/fapi/v1/klines",
                          params={"symbol": sym, "interval": "1h",
                                  "startTime": cur, "limit": 1500},
                          proxies=P, timeout=30)
                if r.status_code in (429, 418):
                    time.sleep(15 * (a + 1)); continue
                r.raise_for_status(); rows = r.json(); break
            except Exception as e:
                if a == 4:
                    print("  !!", sym, e, flush=True); return out
                time.sleep(3 * (a + 1))
        if not isinstance(rows, list) or not rows: break
        out.extend(rows)
        nxt = int(rows[-1][0]) + 1
        if nxt <= cur: break
        cur = nxt
        time.sleep(0.5)
    return [r for r in out if START <= int(r[0]) <= END]

syms = sorted(json.load(open(f"{OUT}/symbols.json")).keys())
print("待下载 %d 个标的" % len(syms), flush=True)
for i, s in enumerate(syms, 1):
    p = f"{OUT}/{s}.feather"
    if os.path.exists(p):
        print("[%3d/%d] %-16s skip" % (i, len(syms), s), flush=True); continue
    rows = fetch(s)
    if not rows:
        print("[%3d/%d] %-16s EMPTY" % (i, len(syms), s), flush=True); continue
    df = pd.DataFrame({
        "t":  [int(r[0]) for r in rows],
        "open": [float(r[1]) for r in rows], "high": [float(r[2]) for r in rows],
        "low": [float(r[3]) for r in rows],  "close": [float(r[4]) for r in rows],
        "qv": [float(r[7]) for r in rows],
    }).drop_duplicates("t").sort_values("t").reset_index(drop=True)
    df.to_feather(p)
    print("[%3d/%d] %-16s %5d rows  %s -> %s" % (
        i, len(syms), s, len(df),
        pd.Timestamp(int(df.t.iloc[0]), unit="ms", tz="UTC").strftime("%m-%d"),
        pd.Timestamp(int(df.t.iloc[-1]), unit="ms", tz="UTC").strftime("%m-%d")), flush=True)
print("DONE", flush=True)
