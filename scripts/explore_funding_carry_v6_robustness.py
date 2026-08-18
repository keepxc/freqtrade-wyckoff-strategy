"""
探索性——非结论，禁止引用为证据。

上一轮发现"28日窗口CI不含零"很可能是多重比较下的偶然，且62日出现符号反转。
本轮换三个独立角度做稳健性检查，不是继续挖新格子：

  A. 非重叠窗口重采样：14/21/28日前瞻改用不重叠区间(每隔h天采一个样本点)，
     避免重叠窗口人为膨胀样本数、拉窄自举CI的假象，看真正独立样本下效应
     还在不在。
  B. 大市值 vs 小市值分组：BTC/ETH/BNB 一组，其余alt一组，看效应是不是
     集中在某一类资产（如果只在小市值alt里显著，流动性/可执行性存疑）。
  C. funding 变化量（动量）而非水平值：用 fr_trail7 的7日变化量分组，
     检验"funding正在快速上升的币"是否比"funding水平高但稳定的币"更能
     预测负向收益（拥挤仓位正在形成 vs 已经形成）。
"""
import glob
import os

import numpy as np
import pandas as pd

DATA_DIR = "./user_data/data/binance/futures"
N_BOOT = 2000
BLOCK_LEN = 14
MAJORS = {"BTC_USDT_USDT", "ETH_USDT_USDT", "BNB_USDT_USDT"}


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


def add_signals(daily, horizons):
    for horizon in horizons:
        daily[f"fwd_ret_{horizon}d"] = daily.groupby("pair")["close"].transform(
            lambda s, h=horizon: s.shift(-h) / s - 1
        )
        daily[f"fwd_ret_{horizon}d_mn"] = daily[f"fwd_ret_{horizon}d"] - daily.groupby("date_only")[
            f"fwd_ret_{horizon}d"
        ].transform("mean")
    daily["fr_trail7"] = daily.groupby("pair")["funding_rate"].transform(lambda s: s.rolling(7).mean())
    daily["fr_trail7_chg"] = daily.groupby("pair")["fr_trail7"].transform(lambda s: s - s.shift(7))
    return daily


def cell(daily, fr_col, horizon):
    col_fwd = f"fwd_ret_{horizon}d_mn"
    valid = daily.dropna(subset=[fr_col, col_fwd]).copy()
    width_by_day = valid.groupby("date_only")["pair"].transform("nunique")
    valid = valid[width_by_day >= 8].copy()
    valid["fr_rank"] = valid.groupby("date_only")[fr_col].rank(pct=True)
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
    n_blocks = max(1, len(dates) // block_len)
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
    horizons = (14, 21, 28)
    daily = build_daily()
    daily = add_signals(daily, horizons)

    print("=" * 70)
    print("探索性第五轮——非结论，禁止引用为证据")
    print("=" * 70)

    print("\n[A] 非重叠窗口重采样（每隔h天取一个样本点，避免窗口重叠虚增n）")
    for h in horizons:
        valid = cell(daily, "fr_trail7", h)
        valid = valid.dropna(subset=[f"fwd_ret_{h}d_mn"]).sort_values("date_only")
        dates_sorted = sorted(valid["date_only"].unique())
        keep_dates = set(dates_sorted[::h])
        nonoverlap = valid[valid["date_only"].isin(keep_dates)]
        s = spread_stat(nonoverlap, f"fwd_ret_{h}d_mn")
        n = len(nonoverlap)
        if s is not None and n > 20:
            lo, hi = block_bootstrap_ci(nonoverlap, f"fwd_ret_{h}d_mn", block_len=max(1, h // 7))
            note = "CI不含零" if (hi < 0 or lo > 0) else "CI跨零"
            print(f"  horizon={h}d: spread={s:+.4%}  n={n}  90%CI=[{lo:+.4%},{hi:+.4%}] ({note})")
        else:
            print(f"  horizon={h}d: 样本不足 n={n}")

    print("\n[B] 大市值(BTC/ETH/BNB) vs 小市值alt 分组（fr_trail7, 各前瞻窗口, 全样本重叠窗口）")
    for h in horizons:
        valid = cell(daily, "fr_trail7", h).dropna(subset=[f"fwd_ret_{h}d_mn"])
        majors = valid[valid["pair"].isin(MAJORS)]
        alts = valid[~valid["pair"].isin(MAJORS)]
        s_maj = spread_stat(majors, f"fwd_ret_{h}d_mn")
        s_alt = spread_stat(alts, f"fwd_ret_{h}d_mn")
        print(f"  horizon={h}d: majors spread={s_maj if s_maj is None else f'{s_maj:+.4%}'} (n={len(majors)})  "
              f"alts spread={s_alt if s_alt is None else f'{s_alt:+.4%}'} (n={len(alts)})")

    print("\n[C] funding 变化量(动量)分组而非水平值（fr_trail7_chg, 各前瞻窗口）")
    for h in horizons:
        valid = cell(daily, "fr_trail7_chg", h).dropna(subset=[f"fwd_ret_{h}d_mn"])
        s = spread_stat(valid, f"fwd_ret_{h}d_mn")
        n = len(valid)
        print(f"  horizon={h}d: spread(top10-bottom10 by funding变化量)={s if s is None else f'{s:+.4%}'}  n={n}")

    print("\n" + "=" * 70)
    print("以上为探索性输出，不构成任何采纳/证伪判断")
    print("=" * 70)


if __name__ == "__main__":
    main()
