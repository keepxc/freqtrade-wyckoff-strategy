"""TASK 83 数据质量检查：覆盖/单调/重复/缺口/时间戳单位回归。"""
import os

import pandas as pd

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
FUT = os.path.join(BASE, "data", "binance", "futures")
UNIVERSE = ["BTC", "ETH", "BNB", "XRP", "ADA", "SOL", "DOGE", "DOT",
            "LTC", "LINK", "AVAX", "TRX", "BCH", "ETC", "ATOM"]

for sym in UNIVERSE:
    p = os.path.join(FUT, f"{sym}_USDT_USDT-1d-futures.feather")
    if not os.path.exists(p):
        print(f"{sym:6s} 缺文件")
        continue
    df = pd.read_feather(p)
    d = pd.to_datetime(df['date'])
    mono = d.is_monotonic_increasing
    dup = d.duplicated().sum()
    gaps = (d.diff().dt.days > 1).sum()
    recent = df[d.dt.year >= 2025]
    ok2025 = len(recent) > 0 and recent['close'].gt(0).all()
    print(f"{sym:6s} {d.min().date()} ~ {d.max().date()}  n={len(df):5d}  "
          f"单调{'√' if mono else '×'} 重复{dup} 缺口{gaps}  2025+行={len(recent)} {'√' if ok2025 else '×'}")
