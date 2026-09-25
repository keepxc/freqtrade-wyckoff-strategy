# -*- coding: utf-8 -*-
"""拉取 H1/H2 所需的币安 USDT-M 永续 1h K 线（宇宙定义见 PREREG_H1_H2.md §3）。

输出 data/klines_1h.parquet：symbol, open_time(UTC), open, close
"""
import json
import re
import sys
import time
import urllib.request

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
D = "research/ai500_h1/data"
PROXY = "http://127.0.0.1:10809"
START_MS = int(pd.Timestamp("2026-09-04", tz="UTC").timestamp() * 1000)
# V3 配置黑名单（config_ai500_v3_dryrun pair_blacklist，按 base 匹配）
BLACK = re.compile(
    r"^(BTCDOM|.*DOWN|.*UP|.*BULL|.*BEAR|AAOI|AAPL|AMC|AMZN|ARM|ASTS|AVGO|AXTI|BABA|BMNR|COIN|CRCL|CRDO|CRM|"
    r"CRWV|DELL|DKNG|EWY|GOOGL|HOOD|INTC|IONQ|LLY|MA|MARA|META|MSFT|MSTR|MU|NBIS|NVDA|ORCL|PLTR|RGTI|RIOT|"
    r"SMCI|TSLA|V|WDC|PAXG|XAUT|NATGAS)$")

info = json.load(open(f"{D}/exchangeInfo_20260925.json", encoding="utf-8"))
syms = [s for s in info["symbols"]
        if s["contractType"] == "PERPETUAL" and s["quoteAsset"] == "USDT" and not BLACK.match(s["baseAsset"])]
print(f"宇宙候选 {len(syms)}")

opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": PROXY}))
frames, empty, failed = [], [], []
for i, s in enumerate(syms):
    url = (f"https://fapi.binance.com/fapi/v1/klines?symbol={s['symbol']}&interval=1h"
           f"&startTime={START_MS}&limit=1500")
    for attempt in range(3):
        try:
            with opener.open(url, timeout=20) as r:
                k = json.loads(r.read())
            break
        except Exception as e:
            k = None
            time.sleep(2)
    if k is None:
        failed.append(s["symbol"])
        continue
    if not k:
        empty.append(s["symbol"])
        continue
    df = pd.DataFrame(k).iloc[:, [0, 1, 4]]
    df.columns = ["open_time", "open", "close"]
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df[["open", "close"]] = df[["open", "close"]].astype(float)
    df["symbol"] = s["baseAsset"]
    df["status"] = s["status"]
    frames.append(df)
    time.sleep(0.3)
    if i % 100 == 0:
        print(i, s["symbol"])

out = pd.concat(frames, ignore_index=True)
out.to_parquet(f"{D}/klines_1h.parquet", index=False)
print(f"写出 {out.symbol.nunique()} 个 symbol / {len(out)} 行；空 {len(empty)}；失败 {len(failed)} {failed[:20]}")
print("最新 K 线:", out.open_time.max())
