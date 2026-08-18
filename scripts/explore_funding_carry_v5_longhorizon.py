"""
探索性——非结论，禁止引用为证据。

v4 发现前瞻窗口越长 spread 越强（3d:-0.25% -> 21d:-1.18%），本轮沿着这条线
继续探：把前瞻窗口推到 28/35/42/60 日，看效应是持续累积、见顶回落、还是
反转；对效应量较大的几个窗口补跑区块自举CI，看显著性是否真的随窗口拉长
改善。仍不改判据、不下结论。
"""
import glob
import os

import numpy as np
import pandas as pd

DATA_DIR = "./user_data/data/binance/futures"
N_BOOT = 2000
BLOCK_LEN = 14


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


def build_daily():
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
    return daily


HORIZONS = (14, 21, 28, 35, 42, 60)


def add_signals(daily):
    for horizon in HORIZONS:
        daily[f"fwd_ret_{horizon}d"] = daily.groupby("pair")["close"].transform(
            lambda s, h=horizon: s.shift(-h) / s - 1
        )
        daily[f"fwd_ret_{horizon}d_mn"] = daily[f"fwd_ret_{horizon}d"] - daily.groupby("date_only")[
            f"fwd_ret_{horizon}d"
        ].transform("mean")
    daily["fr_trail7"] = daily.groupby("pair")["funding_rate"].transform(lambda s: s.rolling(7).mean())
    return daily


def cell(daily, horizon):
    col_fwd = f"fwd_ret_{horizon}d_mn"
    valid = daily.dropna(subset=["fr_trail7", col_fwd]).copy()
    width_by_day = valid.groupby("date_only")["pair"].transform("nunique")
    valid = valid[width_by_day >= 8].copy()
    valid["fr_rank"] = valid.groupby("date_only")["fr_trail7"].rank(pct=True)
    valid["decile"] = pd.cut(
        valid["fr_rank"], [0, 0.10, 0.90, 1.0], labels=["bottom10", "mid80", "top10"], include_lowest=True
    )
    return valid


def spread_stat(valid, ret_col):
    g = valid.groupby("decile", observed=True)[ret_col].mean()
    if "top10" not in g.index or "bottom10" not in g.index:
        return None
    return g["top10"] - g["bottom10"]


def block_bootstrap_ci(valid, ret_col, n_boot=N_BOOT, block_len=BLOCK_LEN, seed=42):
    rng = np.random.RandomState(seed)
    dates = sorted(valid["date_only"].unique())
    n_dates = len(dates)
    n_blocks = max(1, n_dates // block_len)
    date_blocks = [dates[i * block_len:(i + 1) * block_len] for i in range(n_blocks)]
    date_blocks = [b for b in date_blocks if len(b) > 0]

    spreads = []
    for _ in range(n_boot):
        sampled_blocks = [date_blocks[i] for i in rng.randint(0, len(date_blocks), size=len(date_blocks))]
        sampled_dates = set(d for blk in sampled_blocks for d in blk)
        sub = valid[valid["date_only"].isin(sampled_dates)]
        g = sub.groupby("decile", observed=True)[ret_col].mean()
        if "top10" in g.index and "bottom10" in g.index:
            spreads.append(g["top10"] - g["bottom10"])
    spreads = np.array(spreads)
    return np.percentile(spreads, 5), np.percentile(spreads, 95)


def main():
    daily = build_daily()
    daily = add_signals(daily)

    print("=" * 70)
    print("探索性第四轮——非结论，禁止引用为证据")
    print("长前瞻窗口延伸 + 区块自举CI")
    print("=" * 70)

    for h in HORIZONS:
        valid = cell(daily, h)
        valid = valid.dropna(subset=[f"fwd_ret_{h}d_mn"])
        s = spread_stat(valid, f"fwd_ret_{h}d_mn")
        n = len(valid)
        lo, hi = block_bootstrap_ci(valid, f"fwd_ret_{h}d_mn")
        ci_note = "CI不含零" if (hi < 0 or lo > 0) else "CI跨零"
        per_period = s / h * 7 if s is not None else None  # 折算成"每7日"以便与v3主格比较
        print(f"horizon={h:>2}d: spread(top10-bottom10)={s:+.4%}  n={n:>6}  "
              f"90%CI=[{lo:+.4%}, {hi:+.4%}] ({ci_note})  折算每7日={per_period:+.4%}")

    print("\n" + "=" * 70)
    print("以上为探索性输出，不构成任何采纳/证伪判断")
    print("=" * 70)


if __name__ == "__main__":
    main()
