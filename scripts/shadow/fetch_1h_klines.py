#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量抓币安现货 1h K 线 → feather，供 shadow_book.py --klines 使用。

必须走代理：本机币安被 451/直连不可用，ccxt 不会读系统代理，
只能显式传 proxies（环境变量在部分 ccxt 版本上也无效）。
"""
import os
import sys
import time
import json
import pandas as pd
import ccxt

PROXY = "http://127.0.0.1:7890"
SCORE = "/home/gyan/freqtrade/user_data/ai500_score_history_v3.csv"
OUTDIR = "/home/gyan/freqtrade/user_data/data/binance_futures_1h"

os.makedirs(OUTDIR, exist_ok=True)

symbols = sorted(pd.read_csv(SCORE)["symbol"].unique().tolist())
print(f"标的数 {len(symbols)}", flush=True)

# 口径：AI500V3 在币安 USDT-M 合约上执行 → K 线必须取合约，不能取现货
ex = ccxt.binanceusdm({
    "enableRateLimit": True,
    "proxies": {
        "http": PROXY,
        "https": PROXY,
    },
    "timeout": 30000,
})
ex.load_markets()

ok, fail = [], []
for i, sym in enumerate(symbols, 1):
    pair = f"{sym}/USDT"
    try:
        ohlcv = ex.fetch_ohlcv(pair, timeframe="1h", limit=1000)
        if not ohlcv:
            fail.append((sym, "empty"))
            print(f"[{i}/{len(symbols)}] {sym} 空", flush=True)
            continue
        df = pd.DataFrame(
            ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms")
        path = os.path.join(OUTDIR, f"{sym}USDT.feather")
        df.to_feather(path)
        ok.append(sym)
        print(f"[{i}/{len(symbols)}] {sym} OK rows={len(df)} "
              f"{df['datetime'].iloc[0]} → {df['datetime'].iloc[-1]}", flush=True)
    except Exception as e:
        fail.append((sym, str(e)[:120]))
        print(f"[{i}/{len(symbols)}] {sym} FAIL {str(e)[:120]}", flush=True)
    time.sleep(0.05)

print(f"\n完成: ok={len(ok)} fail={len(fail)}", flush=True)
if fail:
    print("失败清单:", json.dumps(fail, ensure_ascii=False, indent=1), flush=True)
