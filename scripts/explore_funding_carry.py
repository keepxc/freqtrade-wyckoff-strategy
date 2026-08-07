"""
探索性——非结论，禁止引用为证据。

目的：看 funding rate carry 因子的数据长相（覆盖度、截面宽度、方向性），
决定是否值得转入确认阶段。不产出任何判据/结论，纯描述性统计。
"""
import glob
import os

import numpy as np
import pandas as pd

DATA_DIR = "./user_data/data/binance/futures"


def load_pair(pair_file_prefix):
    price_path = f"{DATA_DIR}/{pair_file_prefix}-1h-futures.feather"
    funding_path = f"{DATA_DIR}/{pair_file_prefix}-1h-funding_rate.feather"
    if not (os.path.exists(price_path) and os.path.exists(funding_path)):
        return None
    price = pd.read_feather(price_path)[["date", "close"]]
    # freqtrade 把 funding rate 存成 OHLC 形状的文件，真实值在 open 列，high/low/close 都是补零占位
    funding = pd.read_feather(funding_path)[["date", "open"]].rename(columns={"open": "funding_rate"})
    df = price.merge(funding, on="date", how="inner")
    df["pair"] = pair_file_prefix
    return df


funding_files = sorted(glob.glob(f"{DATA_DIR}/*-1h-funding_rate.feather"))
pairs = [os.path.basename(f).replace("-1h-funding_rate.feather", "") for f in funding_files]

frames = []
for p in pairs:
    d = load_pair(p)
    if d is not None:
        frames.append(d)

all_df = pd.concat(frames, ignore_index=True)
print(f"pairs with both price+funding: {all_df['pair'].nunique()} / {len(pairs)}")
print(f"overall date range: {all_df['date'].min()} -> {all_df['date'].max()}")

# 每个 pair 的数据长度
coverage = all_df.groupby("pair")["date"].agg(["min", "max", "count"])
print("\n--- per-pair coverage ---")
print(coverage)

# 每个时间点截面宽度（有多少个 pair 同时有数据）
cross_section_width = all_df.groupby("date")["pair"].nunique()
print("\n--- cross-sectional width distribution ---")
print(cross_section_width.describe())
print("width>=8 fraction of timestamps:", (cross_section_width >= 8).mean())

# 转日线，构造 trailing 7d 平均funding与forward 7d/14d收益，看方向性（纯描述性，非检验）
all_df = all_df.sort_values(["pair", "date"])
daily = (
    all_df.set_index("date")
    .groupby("pair")
    .resample("1D")
    .agg({"close": "last", "funding_rate": "mean"})
    .reset_index()
)
daily = daily.dropna(subset=["close", "funding_rate"])

daily["fr_trail7"] = daily.groupby("pair")["funding_rate"].transform(lambda s: s.rolling(7).mean())
daily["fwd_ret_7d"] = daily.groupby("pair")["close"].transform(lambda s: s.shift(-7) / s - 1)
daily["fwd_ret_14d"] = daily.groupby("pair")["close"].transform(lambda s: s.shift(-14) / s - 1)

valid = daily.dropna(subset=["fr_trail7", "fwd_ret_7d"])
print(f"\nvalid (fr_trail7, fwd_ret_7d) obs: {len(valid)} across {valid['pair'].nunique()} pairs")

# 截面分组：每天按 fr_trail7 排名分 3 组，看后续收益均值（纯描述）
# 注意：截面宽度太窄的日子（如只有1个pair）会让 pct rank 退化为恒等于1.0，
# 人为把所有观测塞进 high 桶——因此只在截面宽度>=8的日子上分桶，避免这个退化伪影。
valid = valid.copy()
valid["date_only"] = valid["date"].dt.date
width_by_day = valid.groupby("date_only")["pair"].transform("nunique")
valid = valid[width_by_day >= 8].copy()
valid["fr_rank"] = valid.groupby("date_only")["fr_trail7"].rank(pct=True)
valid["bucket"] = pd.cut(valid["fr_rank"], [0, 0.33, 0.67, 1.0], labels=["low", "mid", "high"], include_lowest=True)

print("\n--- forward 7d return by funding-rate bucket (descriptive only) ---")
print(valid.groupby("bucket", observed=True)["fwd_ret_7d"].agg(["mean", "std", "count"]))
print("\n--- forward 14d return by funding-rate bucket (descriptive only) ---")
print(valid.dropna(subset=["fwd_ret_14d"]).groupby("bucket", observed=True)["fwd_ret_14d"].agg(["mean", "std", "count"]))
