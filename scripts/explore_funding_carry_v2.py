"""
探索性——非结论，禁止引用为证据。

延续 explore_funding_carry.py 的 flat 结果，测试：
1. 不同回溯窗口（3/7/14/30日 trailing mean funding）
2. 极值切法（前10%/后10% vs 三分位）
而不是只用单一 7日+三分位的切法。目的仍是看数据长相，判断要不要转确认阶段。
"""
import glob
import os

import pandas as pd

DATA_DIR = "./user_data/data/binance/futures"


def load_pair(pair_file_prefix):
    price_path = f"{DATA_DIR}/{pair_file_prefix}-1h-futures.feather"
    funding_path = f"{DATA_DIR}/{pair_file_prefix}-1h-funding_rate.feather"
    if not (os.path.exists(price_path) and os.path.exists(funding_path)):
        return None
    price = pd.read_feather(price_path)[["date", "close"]]
    funding = pd.read_feather(funding_path)[["date", "open"]].rename(columns={"open": "funding_rate"})
    df = price.merge(funding, on="date", how="inner")
    df["pair"] = pair_file_prefix
    return df


funding_files = sorted(glob.glob(f"{DATA_DIR}/*-1h-funding_rate.feather"))
pairs = [os.path.basename(f).replace("-1h-funding_rate.feather", "") for f in funding_files]
frames = [d for p in pairs if (d := load_pair(p)) is not None]
all_df = pd.concat(frames, ignore_index=True).sort_values(["pair", "date"])

daily = (
    all_df.set_index("date")
    .groupby("pair")
    .resample("1D")
    .agg({"close": "last", "funding_rate": "mean"})
    .reset_index()
)
daily = daily.dropna(subset=["close", "funding_rate"])
daily["date_only"] = daily["date"].dt.date

for horizon in (7, 14):
    daily[f"fwd_ret_{horizon}d"] = daily.groupby("pair")["close"].transform(lambda s, h=horizon: s.shift(-h) / s - 1)
    # 市场中性口径：减去当天截面均值，剥离"哪个币恰好落在哪个档"的构成效应（同一币不同基期收益差异极大）
    daily[f"fwd_ret_{horizon}d_mn"] = daily[f"fwd_ret_{horizon}d"] - daily.groupby("date_only")[f"fwd_ret_{horizon}d"].transform("mean")

WINDOWS = [3, 7, 14, 30]
for w in WINDOWS:
    daily[f"fr_trail{w}"] = daily.groupby("pair")["funding_rate"].transform(lambda s, w=w: s.rolling(w).mean())

print("=" * 70)
for w in WINDOWS:
    for horizon in (7, 14):
        col_fr = f"fr_trail{w}"
        col_fwd = f"fwd_ret_{horizon}d_mn"  # 市场中性口径
        valid = daily.dropna(subset=[col_fr, col_fwd]).copy()
        width_by_day = valid.groupby("date_only")["pair"].transform("nunique")
        valid = valid[width_by_day >= 8].copy()
        if valid.empty:
            print(f"window={w}d horizon={horizon}d: no valid obs after width filter")
            continue
        valid["fr_rank"] = valid.groupby("date_only")[col_fr].rank(pct=True)

        # 三分位
        tercile = pd.cut(valid["fr_rank"], [0, 0.33, 0.67, 1.0], labels=["low", "mid", "high"], include_lowest=True)
        tstats = valid.groupby(tercile, observed=True)[col_fwd].agg(["mean", "count"])

        # 极值十分位（前10% vs 后10%）
        decile = pd.cut(valid["fr_rank"], [0, 0.10, 0.90, 1.0], labels=["bottom10", "mid80", "top10"], include_lowest=True)
        dstats = valid.groupby(decile, observed=True)[col_fwd].agg(["mean", "count"])

        print(f"\n--- window={w}d trailing funding, forward {horizon}d MARKET-NEUTRAL return (width>=8, n={len(valid)}) ---")
        print("tercile:")
        print(tstats.to_string())
        print("decile (extremes):")
        print(dstats.to_string())
        if "bottom10" in dstats.index and "top10" in dstats.index:
            spread = dstats.loc["top10", "mean"] - dstats.loc["bottom10", "mean"]
            print(f"top10 - bottom10 spread: {spread:+.4%}")
