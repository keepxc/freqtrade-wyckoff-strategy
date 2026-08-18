"""
探索性——非结论，禁止引用为证据。

v6[B] 发现之前所有轮次的负向spread几乎全部由 BTC/ETH/BNB 三个大市值币贡献，
alt币段无信号。本轮把假设重新定义为"少数主流币自身的funding极端值是否
预示该币自身后续跑输"——这是时序择时假设，不是跨资产选币假设，样本独立性
和验证方式都要相应改变：
  A. 改用时序分位（每个币相对自己历史的funding分位），不再用跨资产截面排名
     （只有3个币时截面排名意义不大）。
  B. 三个币分别单独看，而不是合并，检查效应是不是又进一步集中在某一个币
     （如果只是BTC一个币的历史路径巧合，样本量连"3份"都不到，只有1份）。
  C. 收益口径改回原始收益（非市场中性），因为这是单资产择时假设，"跑赢
     同侪"的市场中性口径在这里问的不是我们想问的问题（CLAUDE.md口径declare）。
  D. 对合并后的三币时序信号做单资产区块自举CI（区块按日期，同一时间三个
     币的信号有相关性，不能假装独立）。
"""
import glob
import os

import numpy as np
import pandas as pd

DATA_DIR = "./user_data/data/binance/futures"
N_BOOT = 2000
BLOCK_LEN = 14
MAJORS = ["BTC_USDT_USDT", "ETH_USDT_USDT", "BNB_USDT_USDT"]


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


def build_daily(pairs):
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


HORIZONS = (14, 21, 28)


def add_signals(daily):
    for h in HORIZONS:
        daily[f"fwd_ret_{h}d"] = daily.groupby("pair")["close"].transform(lambda s, h=h: s.shift(-h) / s - 1)
    daily["fr_trail7"] = daily.groupby("pair")["funding_rate"].transform(lambda s: s.rolling(7).mean())
    # 时序分位：每个币相对自己全历史的funding分位（扩张窗口，避免用到未来分布）
    daily["fr_ts_rank"] = daily.groupby("pair")["fr_trail7"].transform(
        lambda s: s.expanding(min_periods=90).apply(lambda x: (x.iloc[:-1] < x.iloc[-1]).mean() if len(x) > 1 else np.nan, raw=False)
    )
    return daily


def block_bootstrap_mean_ci(series_by_date, n_boot=N_BOOT, block_len=BLOCK_LEN, seed=42):
    """输入: DataFrame[date_only, value]，按日期分块自举均值CI"""
    rng = np.random.RandomState(seed)
    dates = sorted(series_by_date["date_only"].unique())
    n_blocks = max(1, len(dates) // block_len)
    date_blocks = [dates[i * block_len:(i + 1) * block_len] for i in range(n_blocks)]
    date_blocks = [b for b in date_blocks if len(b) > 0]
    means = []
    for _ in range(n_boot):
        sampled_blocks = [date_blocks[i] for i in rng.randint(0, len(date_blocks), size=len(date_blocks))]
        sampled_dates = set(d for blk in sampled_blocks for d in blk)
        sub = series_by_date[series_by_date["date_only"].isin(sampled_dates)]
        if len(sub) > 0:
            means.append(sub["value"].mean())
    means = np.array(means)
    return np.percentile(means, 5), np.percentile(means, 95)


def main():
    daily_all = build_daily(MAJORS)
    daily_all = add_signals(daily_all)

    print("=" * 70)
    print("探索性第六轮——非结论，禁止引用为证据")
    print("重新定义为主流币自身时序择时假设（非跨资产截面）")
    print("=" * 70)

    print("\n[A]+[D] 三币合并，时序分位top20%(high) vs bottom20%(low)，原始收益，区块自举CI")
    for h in HORIZONS:
        col = f"fwd_ret_{h}d"
        valid = daily_all.dropna(subset=["fr_ts_rank", col]).copy()
        high = valid[valid["fr_ts_rank"] >= 0.8]
        low = valid[valid["fr_ts_rank"] <= 0.2]
        if len(high) < 20 or len(low) < 20:
            print(f"  horizon={h}d: 样本不足 high_n={len(high)} low_n={len(low)}")
            continue
        spread = high[col].mean() - low[col].mean()
        diff_df = pd.concat([
            high[["date_only"]].assign(value=high[col].values),
            low[["date_only"]].assign(value=-low[col].values),
        ])
        # 用高低两组的"配对差"做自举: 简化为把high当+1组低当-1组分别算区间再做差的近似,
        # 这里直接对high、low分别自举均值CI,再报告点差,CI宽度供参考(非严格配对差CI)
        lo_h, hi_h = block_bootstrap_mean_ci(high[["date_only"]].assign(value=high[col].values))
        lo_l, hi_l = block_bootstrap_mean_ci(low[["date_only"]].assign(value=low[col].values))
        print(f"  horizon={h}d: high(top20%funding) mean={high[col].mean():+.4%} CI=[{lo_h:+.4%},{hi_h:+.4%}] n={len(high)}  "
              f"low(bottom20%funding) mean={low[col].mean():+.4%} CI=[{lo_l:+.4%},{hi_l:+.4%}] n={len(low)}  "
              f"spread(high-low)={spread:+.4%}")

    print("\n[B] 三币分别单独看（不合并），horizon=21d，时序分位top20% vs bottom20%")
    for pair in MAJORS:
        sub = daily_all[daily_all["pair"] == pair].dropna(subset=["fr_ts_rank", "fwd_ret_21d"])
        high = sub[sub["fr_ts_rank"] >= 0.8]
        low = sub[sub["fr_ts_rank"] <= 0.2]
        if len(high) < 10 or len(low) < 10:
            print(f"  {pair}: 样本不足 high_n={len(high)} low_n={len(low)}")
            continue
        spread = high["fwd_ret_21d"].mean() - low["fwd_ret_21d"].mean()
        print(f"  {pair}: high mean={high['fwd_ret_21d'].mean():+.4%} n={len(high)}  "
              f"low mean={low['fwd_ret_21d'].mean():+.4%} n={len(low)}  spread={spread:+.4%}")

    print("\n" + "=" * 70)
    print("以上为探索性输出，不构成任何采纳/证伪判断")
    print("=" * 70)


if __name__ == "__main__":
    main()
