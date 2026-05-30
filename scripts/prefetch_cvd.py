"""
Pre-fetch CVD data for backtest pairs over a timerange.
Saves to user_data/data/binance/futures/{SYM}-15m-cvd.feather

CVD = rolling sum of (2 * taker_buy_vol - total_vol)

用法：
  python scripts/prefetch_cvd.py --timerange 20260520-20260530
"""
import requests, argparse, time, sys
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

BINANCE = "https://fapi.binance.com"
PROXY = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}
OUT_DIR = Path(__file__).parent.parent / "user_data" / "data" / "cvd"

WATCHLIST_PAIRS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "DOGEUSDT",
    "1000PEPEUSDT", "LITUSDT", "NFPUSDT", "LABUSDT",
    "UBUSDT", "ZECUSDT",
]

CVD_WINDOW = 40

def fetch_and_save(symbol: str, start_ms: int, end_ms: int):
    """Fetch klines with taker volume, compute CVD, save feather."""
    out_path = OUT_DIR / f"{symbol}-15m-cvd.feather"

    all_rows = []
    current_end = end_ms

    session = requests.Session()
    session.proxies.update(PROXY)

    while current_end > start_ms:
        params = {
            "symbol": symbol, "interval": "15m", "limit": 1000,
            "endTime": current_end,
        }
        try:
            r = session.get(f"{BINANCE}/fapi/v1/klines", params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"  ⚠️ {symbol}: HTTP error at {current_end}: {e}")
            break

        if not data:
            break

        for row in data:
            all_rows.append({
                "timestamp": int(row[0]),
                "taker_buy_vol": float(row[9]),
                "volume": float(row[5]),
                "volume_delta": 2.0 * float(row[9]) - float(row[5]),
            })

        earliest = int(data[0][0])
        current_end = earliest - 60000  # 1 min before earliest bar
        print(f"  {symbol}: fetched {len(data)} bars, earliest={earliest}", end="\r")
        time.sleep(0.2)

    if not all_rows:
        print(f"  {symbol}: NO DATA")
        return

    df = pd.DataFrame(all_rows)
    df = df.sort_values("timestamp")
    df["cvd"] = df["volume_delta"].rolling(CVD_WINDOW, min_periods=max(10, CVD_WINDOW//4)).sum()

    df = df.set_index("timestamp")
    df.to_feather(out_path)
    print(f"  {symbol}: saved {len(df)} bars → {out_path.name}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--timerange", default="20260520-20260530")
    args = parser.parse_args()

    start_str, end_str = args.timerange.split("-")
    start_dt = datetime.strptime(start_str, "%Y%m%d")
    end_dt = datetime.strptime(end_str, "%Y%m%d")
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Fetching CVD: {start_str} → {end_str} ({len(WATCHLIST_PAIRS)} pairs)")
    for sym in WATCHLIST_PAIRS:
        fetch_and_save(sym, start_ms, end_ms)

    print("Done.")


if __name__ == "__main__":
    main()
