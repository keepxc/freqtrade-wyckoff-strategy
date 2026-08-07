"""
纳指前十对照组数据准备：2021-06-30 时点权重前十（跳过不可交易的 ADBE/CMCSA，
按权重顺位替补 NFLX/INTC，用户确认 2026-08-07），拉取 Nasdaq 官方API 正股
历史，转换为 freqtrade feather 格式，写入 user_data/data/binance/。
"""
import time
from pathlib import Path

import pandas as pd
import requests

# 2021-06-30 NPORT-P 时点权重顺位（跳过ADBE/CMCSA不可交易，顺延NFLX/INTC）
TICKERS = ["AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "NVDA", "PYPL", "NFLX", "INTC"]
BSTOCK_MAP = {
    "AAPL": "AAPLB", "MSFT": "MSFTB", "AMZN": "AMZNB", "GOOGL": "GOOGLB",
    "META": "METAB", "TSLA": "TSLAB", "NVDA": "NVDAB", "PYPL": "PYPLB",
    "NFLX": "NFLXB", "INTC": "INTCB",
}
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
DATA_DIR = Path(r"E:\freqtrade\user_data\data\binance")


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
    ok, skipped = [], []
    for tk in TICKERS:
        df, err = fetch_nasdaq_daily(tk)
        if err:
            print(f"[SKIP] {tk}: {err}")
            skipped.append(tk)
            continue
        out = df[["date", "open", "high", "low", "close", "volume"]].dropna().reset_index(drop=True)
        bstock = BSTOCK_MAP[tk]
        fname = DATA_DIR / f"{bstock}_USDT-1d.feather"
        out.to_feather(fname)
        ok.append(tk)
        print(f"[OK] {tk} -> {bstock}: {len(out)} rows -> {fname.name}  ({out['date'].min().date()} -> {out['date'].max().date()})")
        time.sleep(0.5)

    print(f"\n成功 {len(ok)}/{len(TICKERS)}：{ok}")
    if skipped:
        print(f"跳过：{skipped}")


if __name__ == "__main__":
    main()
