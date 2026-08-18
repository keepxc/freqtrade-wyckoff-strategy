"""
探索性——非结论，禁止引用为证据。

v3 电池之后的第三轮探索：不改判据，只加新角度看数据长相，帮助决定要不要
以及往哪个方向转正式确认阶段。

新增角度：
  A. 更极端切分 5/90/5（v3 只测了 10/80/10）
  B. funding 度量换成"最近一次结算值"而非 trailing 均值（更贴近实盘可获得的信息）
  C. 前瞻窗口细化到 3/7/14/21 日，看衰减/收敛曲线（v3 只测了 7/14）
  D. E5 观察到的后半段衰减，按币种拆开看是否集中在少数高流动性币
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
    # 最近一次结算值：同一天内最后一条 funding 记录
    last_settle = (
        all_df.set_index("date")
        .groupby("pair")["funding_rate"]
        .resample("1D")
        .last()
        .reset_index()
        .rename(columns={"funding_rate": "funding_rate_last"})
    )
    daily = daily.merge(last_settle, on=["pair", "date"], how="left")
    daily = daily.dropna(subset=["close", "funding_rate"])
    daily["date_only"] = daily["date"].dt.date
    return daily


def add_signals(daily):
    for horizon in (3, 7, 14, 21):
        daily[f"fwd_ret_{horizon}d"] = daily.groupby("pair")["close"].transform(
            lambda s, h=horizon: s.shift(-h) / s - 1
        )
        daily[f"fwd_ret_{horizon}d_mn"] = daily[f"fwd_ret_{horizon}d"] - daily.groupby("date_only")[
            f"fwd_ret_{horizon}d"
        ].transform("mean")
    for w in (7, 14):
        daily[f"fr_trail{w}"] = daily.groupby("pair")["funding_rate"].transform(
            lambda s, w=w: s.rolling(w).mean()
        )
    return daily


def cell(daily, fr_col, horizon, cuts, labels):
    col_fwd = f"fwd_ret_{horizon}d_mn"
    valid = daily.dropna(subset=[fr_col, col_fwd]).copy()
    width_by_day = valid.groupby("date_only")["pair"].transform("nunique")
    valid = valid[width_by_day >= 8].copy()
    valid["fr_rank"] = valid.groupby("date_only")[fr_col].rank(pct=True)
    valid["decile"] = pd.cut(valid["fr_rank"], cuts, labels=labels, include_lowest=True)
    return valid


def spread(valid, ret_col, hi_label, lo_label):
    g = valid.groupby("decile", observed=True)[ret_col].mean()
    if hi_label not in g.index or lo_label not in g.index:
        return None, 0
    n = len(valid[valid["decile"].isin([hi_label, lo_label])])
    return g[hi_label] - g[lo_label], n


def main():
    daily = build_daily()
    daily = add_signals(daily)

    print("=" * 70)
    print("探索性第三轮——非结论，禁止引用为证据")
    print("=" * 70)

    print("\n[A] 更极端切分 5/90/5（fr_trail7, 各前瞻窗口）")
    for h in (3, 7, 14, 21):
        valid = cell(daily, "fr_trail7", h, [0, 0.05, 0.95, 1.0], ["bottom5", "mid90", "top5"])
        s, n = spread(valid, f"fwd_ret_{h}d_mn", "top5", "bottom5")
        print(f"  horizon={h}d: spread(top5-bottom5)={s:+.4%}  n={n}" if s is not None else f"  horizon={h}d: 数据不足")

    print("\n[B] funding 度量换成最近一次结算值（10/80/10切分，各前瞻窗口）")
    for h in (3, 7, 14, 21):
        valid = cell(daily, "funding_rate_last", h, [0, 0.10, 0.90, 1.0], ["bottom10", "mid80", "top10"])
        s, n = spread(valid, f"fwd_ret_{h}d_mn", "top10", "bottom10")
        print(f"  horizon={h}d: spread(top10-bottom10)={s:+.4%}  n={n}" if s is not None else f"  horizon={h}d: 数据不足")

    print("\n[C] 前瞻窗口衰减曲线（fr_trail7, 10/80/10切分）")
    for h in (3, 7, 14, 21):
        valid = cell(daily, "fr_trail7", h, [0, 0.10, 0.90, 1.0], ["bottom10", "mid80", "top10"])
        s, n = spread(valid, f"fwd_ret_{h}d_mn", "top10", "bottom10")
        print(f"  horizon={h}d: spread(top10-bottom10)={s:+.4%}  n={n}" if s is not None else f"  horizon={h}d: 数据不足")

    print("\n[D] 后半段衰减是否集中在少数币种（fr_trail7 x fwd14d, 10/80/10切分, 按pair拆分后半段）")
    valid = cell(daily, "fr_trail7", 14, [0, 0.10, 0.90, 1.0], ["bottom10", "mid80", "top10"])
    valid = valid.dropna(subset=["fwd_ret_14d_mn"]).sort_values("date_only")
    dates_sorted = sorted(valid["date_only"].unique())
    mid = len(dates_sorted) // 2
    second_half = valid[valid["date_only"].isin(set(dates_sorted[mid:]))]
    per_pair = (
        second_half[second_half["decile"].isin(["top10", "bottom10"])]
        .groupby(["pair", "decile"], observed=True)["fwd_ret_14d_mn"]
        .agg(["mean", "count"])
        .reset_index()
    )
    print(per_pair.to_string(index=False))

    print("\n" + "=" * 70)
    print("以上均为探索性输出，不构成任何采纳/证伪判断")
    print("=" * 70)


if __name__ == "__main__":
    main()
