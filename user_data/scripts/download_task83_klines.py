"""
TASK 83 数据补齐：data.binance.vision 月度 1d K线归档 → Freqtrade feather

绕开 REST API 418 封禁（静态 S3 归档无请求权重限制，与 download_oi_metrics.py
同通道）。ETH/SOL 现有 feather 从 2022-01 起，跨截面宇宙需 2020 起 —— 一并
全量重建（同源数据，向前扩展）。BTC 现有 2019-09 起全量，不动。
"""
import io
import os
import sys
import zipfile

import pandas as pd
import requests

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
FUT = os.path.join(BASE, "data", "binance", "futures")
ARCHIVE = "https://data.binance.vision/data/futures/um/monthly/klines/{sym}/1d/{sym}-1d-{ym}.zip"
SYMBOLS = ["BNB", "XRP", "ADA", "DOGE", "DOT", "LTC", "LINK", "AVAX",
           "TRX", "BCH", "ETC", "ATOM", "ETH", "SOL"]
MONTHS = pd.period_range("2020-01", "2026-06", freq="M").strftime("%Y-%m")
COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore"]
PROXIES = None  # 直连先试；失败再切 {'https': 'http://127.0.0.1:7890'}


def fetch_month(sym, ym, session):
    url = ARCHIVE.format(sym=f"{sym}USDT", ym=ym)
    r = session.get(url, timeout=30, proxies=PROXIES)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    data = zf.read(zf.namelist()[0])
    has_header = data[:9] == b"open_time"
    return pd.read_csv(io.BytesIO(data), header=0 if has_header else None,
                       names=COLS)


def main():
    session = requests.Session()
    for sym in SYMBOLS:
        parts = []
        for ym in MONTHS:
            try:
                df = fetch_month(sym, ym, session)
            except Exception as e:
                print(f"  {sym} {ym}: 重试一次 ({type(e).__name__})")
                df = fetch_month(sym, ym, session)
            if df is not None:
                parts.append(df)
        if not parts:
            print(f"⚠️ {sym}: 无数据")
            continue
        raw = pd.concat(parts, ignore_index=True)
        # open_time 兼容 ms/us 时间戳（2025+ 归档切了 us）：统一归一到 ms
        ts = raw["open_time"].astype("int64")
        ts_ms = ts.where(ts < 10**14, ts // 1000)
        date = pd.to_datetime(ts_ms, unit="ms", utc=True)
        out = pd.DataFrame({
            "date": date,
            "open": raw["open"].astype(float),
            "high": raw["high"].astype(float),
            "low": raw["low"].astype(float),
            "close": raw["close"].astype(float),
            "volume": raw["volume"].astype(float),
        }).sort_values("date").drop_duplicates("date").reset_index(drop=True)
        path = os.path.join(FUT, f"{sym}_USDT_USDT-1d-futures.feather")
        out.to_feather(path)
        print(f"{sym}: {out['date'].min().date()} ~ {out['date'].max().date()}  {len(out)} 行 → 已写入")


if __name__ == "__main__":
    main()
