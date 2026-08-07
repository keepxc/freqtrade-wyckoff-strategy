"""
准备 BStocksDonchianV1 回测用数据：拉取16支正股近5年 Nasdaq 官方API 日线，
转换为 freqtrade feather 格式，写入 user_data/data/binance/<TICKER>_USD-1d.feather。

同时输出 realized_vol（与 DonchianV1 策略内 don_realized_vol 完全同口径：
rolling(20).std() * sqrt(365)，shift(1)）的分布，用于 TARGET_VOL 校准。
"""
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

TICKERS = [
    "NVDA", "TSLA", "CRCL", "MU", "SNDK", "AAPL", "AMZN", "GOOGL",
    "NFLX", "ASML", "GS", "PYPL", "COIN", "HOOD", "IBM", "NOK",
]
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
DATA_DIR = Path(r"E:\freqtrade\user_data\data\binance")
VOL_LOOKBACK = 20


def fetch_nasdaq_daily(ticker, fromdate="2021-08-07", todate="2026-08-07"):
    url = (
        f"https://api.nasdaq.com/api/quote/{ticker}/historical"
        f"?assetclass=stocks&fromdate={fromdate}&todate={todate}&limit=9999"
    )
    r = requests.get(url, headers=HEADERS, timeout=20)
    if r.status_code != 200:
        return None, f"status={r.status_code}"
    try:
        rows = r.json()["data"]["tradesTable"]["rows"]
    except Exception as e:
        return None, f"parse_error={e}"
    if not rows:
        return None, "empty_rows"
    df = pd.DataFrame(rows)
    for col in ("close", "open", "high", "low"):
        df[col] = df[col].astype(str).str.replace("$", "", regex=False).str.replace(",", "", regex=False)
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["volume"] = df["volume"].astype(str).str.replace(",", "", regex=False)
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
    df["date"] = pd.to_datetime(df["date"], format="%m/%d/%Y", utc=True)
    df = df.sort_values("date").reset_index(drop=True)
    return df, None


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    all_vols = []
    ok, skipped = [], []

    for tk in TICKERS:
        df, err = fetch_nasdaq_daily(tk)
        if err:
            print(f"[SKIP] {tk}: {err}")
            skipped.append(tk)
            continue
        out = df[["date", "open", "high", "low", "close", "volume"]].copy()
        out = out.dropna()
        fname = DATA_DIR / f"{tk}_USD-1d.feather"
        out.reset_index(drop=True).to_feather(fname)
        ok.append(tk)
        print(f"[OK] {tk}: {len(out)} rows -> {fname.name}  ({out['date'].min().date()} -> {out['date'].max().date()})")

        ret = out['close'].pct_change()
        rv = ret.rolling(VOL_LOOKBACK).std() * np.sqrt(365)
        rv = rv.dropna()
        all_vols.append(rv)
        time.sleep(0.5)

    print(f"\n成功 {len(ok)}/{len(TICKERS)}：{ok}")
    if skipped:
        print(f"跳过：{skipped}")

    vol_all = pd.concat(all_vols, ignore_index=True)
    print("\n=== realized_vol (rolling20 std * sqrt(365)) 分布，16支合并 ===")
    for q in (0.1, 0.25, 0.5, 0.75, 0.9):
        print(f"  {int(q*100)}分位: {vol_all.quantile(q):.4f}")
    print(f"  均值: {vol_all.mean():.4f}")


if __name__ == "__main__":
    main()
