"""
探索性——非结论，禁止引用为证据。

funding rate 排位跨资产方向性信号——电池格式看数据长相，非正式确认性检验。
判据设计已与用户讨论并"暂时依照此跑"，但用户明确未冻结（跑完看结果再决定是否
真正冻结走确认阶段），因此本脚本的输出仍按探索性对待，不直接写入 TASK 判定表。

机制：做空 funding 最高十分位（拥挤多头）、做多 funding 最低十分位，市场中性
（减当日截面均值），跨资产（13标的）联合。

判据（暂行，未冻结）：
  E1 方向一致性：3/7/14日窗口 x 7/14日前瞻共6格，spread(top10-bottom10)为负号 >=5/6
  E2 效应量地板：PASS格子中位数|spread| >= 0.5%/期（4次操作x0.11%=0.44%换仓成本+安全边际）
  E3 统计显著性：主格=7日窗口x14日前瞻，跨资产联合区块自举(区块长度=14日)，90%CI下界不含零
  E4 30日窗口反证：30日窗口两格若同时CI不含零且为正 -> 触发降级，无论E1-E3是否PASS
  E5 半段稳健性：主格前后半段同号
"""
import glob
import os

import numpy as np
import pandas as pd

DATA_DIR = "./user_data/data/binance/futures"
COST_FLOOR = 0.005  # 0.5%/期
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


def add_signals(daily):
    for horizon in (7, 14):
        daily[f"fwd_ret_{horizon}d"] = daily.groupby("pair")["close"].transform(
            lambda s, h=horizon: s.shift(-h) / s - 1
        )
        daily[f"fwd_ret_{horizon}d_mn"] = daily[f"fwd_ret_{horizon}d"] - daily.groupby("date_only")[
            f"fwd_ret_{horizon}d"
        ].transform("mean")
    for w in (3, 7, 14, 30):
        daily[f"fr_trail{w}"] = daily.groupby("pair")["funding_rate"].transform(
            lambda s, w=w: s.rolling(w).mean()
        )
    return daily


def cell_spread(daily, window, horizon):
    col_fr = f"fr_trail{window}"
    col_fwd = f"fwd_ret_{horizon}d_mn"
    valid = daily.dropna(subset=[col_fr, col_fwd]).copy()
    width_by_day = valid.groupby("date_only")["pair"].transform("nunique")
    valid = valid[width_by_day >= 8].copy()
    valid["fr_rank"] = valid.groupby("date_only")[col_fr].rank(pct=True)
    valid["decile"] = pd.cut(
        valid["fr_rank"], [0, 0.10, 0.90, 1.0], labels=["bottom10", "mid80", "top10"], include_lowest=True
    )
    return valid


def spread_stat(valid):
    g = valid.groupby("decile", observed=True)
    means = g[[c for c in valid.columns if c.endswith("_mn")][0]].mean()
    if "top10" not in means.index or "bottom10" not in means.index:
        return None
    return means["top10"] - means["bottom10"]


def block_bootstrap_ci(valid, ret_col, n_boot=N_BOOT, block_len=BLOCK_LEN, seed=42):
    """跨资产联合区块自举：按日期分块，块内所有资产一起抽样，保留截面相关结构。"""
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
    return np.percentile(spreads, 5), np.percentile(spreads, 95), spreads


def main():
    daily = build_daily()
    daily = add_signals(daily)

    print("=" * 70)
    print("探索性——非结论，禁止引用为证据")
    print("=" * 70)

    cells = {}
    for w in (3, 7, 14, 30):
        for h in (7, 14):
            valid = cell_spread(daily, w, h)
            valid = valid.dropna(subset=[f"fwd_ret_{h}d_mn"])
            spread = spread_stat(valid.rename(columns={f"fwd_ret_{h}d_mn": f"fwd_ret_{h}d_mn"}))
            n = len(valid)
            cells[(w, h)] = {"spread": spread, "n": n, "valid": valid}
            print(f"window={w}d horizon={h}d: spread(top10-bottom10)={spread:+.4%}  n={n}")

    # E1: 方向一致性（3/7/14窗口 x 7/14前瞻，共6格）
    six_cells = [cells[(w, h)]["spread"] for w in (3, 7, 14) for h in (7, 14)]
    neg_count = sum(1 for s in six_cells if s < 0)
    e1 = neg_count >= 5
    print(f"\nE1 方向一致性: {neg_count}/6 格为负号 -> {'PASS' if e1 else 'FAIL'}")

    # E2: 效应量地板
    pass_cells = [s for s in six_cells if s < 0]
    median_abs = np.median([abs(s) for s in pass_cells]) if pass_cells else 0
    e2 = median_abs >= COST_FLOOR
    print(f"E2 效应量地板: 负号格中位数|spread|={median_abs:.4%} (门槛{COST_FLOOR:.2%}) -> {'PASS' if e2 else 'FAIL'}")

    # E3: 主格统计显著性（7日窗口x14日前瞻，跨资产联合区块自举）
    main_valid = cells[(7, 14)]["valid"]
    lo, hi, boot_dist = block_bootstrap_ci(main_valid, "fwd_ret_14d_mn")
    e3 = hi < 0 or lo > 0  # CI 不含零
    actual = cells[(7, 14)]["spread"]
    print(f"E3 主格(7d窗口x14d前瞻) 实际spread={actual:+.4%}，区块自举90%CI=[{lo:+.4%}, {hi:+.4%}] -> "
          f"{'PASS' if e3 else 'FAIL'}(CI{'不含零' if e3 else '跨零'})")

    # E4: 30日窗口反证
    s30_7 = cells[(30, 7)]["spread"]
    s30_14 = cells[(30, 14)]["spread"]
    lo30_7, hi30_7, _ = block_bootstrap_ci(cells[(30, 7)]["valid"], "fwd_ret_7d_mn")
    lo30_14, hi30_14, _ = block_bootstrap_ci(cells[(30, 14)]["valid"], "fwd_ret_14d_mn")
    both_positive_sig = (s30_7 > 0 and lo30_7 > 0) and (s30_14 > 0 and lo30_14 > 0)
    print(f"E4 30日窗口反证: 7d前瞻spread={s30_7:+.4%} CI=[{lo30_7:+.4%},{hi30_7:+.4%}]；"
          f"14d前瞻spread={s30_14:+.4%} CI=[{lo30_14:+.4%},{hi30_14:+.4%}] -> "
          f"{'触发降级' if both_positive_sig else '未触发'}")

    # E5: 半段稳健性（主格前后半段）
    main_valid_sorted = main_valid.dropna(subset=["fwd_ret_14d_mn"]).sort_values("date_only")
    dates_sorted = sorted(main_valid_sorted["date_only"].unique())
    mid = len(dates_sorted) // 2
    first_half_dates = set(dates_sorted[:mid])
    second_half_dates = set(dates_sorted[mid:])
    s1 = spread_stat(main_valid_sorted[main_valid_sorted["date_only"].isin(first_half_dates)])
    s2 = spread_stat(main_valid_sorted[main_valid_sorted["date_only"].isin(second_half_dates)])
    e5 = (s1 is not None and s2 is not None and s1 < 0 and s2 < 0)
    print(f"E5 半段稳健性: 前半={s1:+.4%} 后半={s2:+.4%} -> {'PASS' if e5 else 'FAIL'}")

    print("\n" + "=" * 70)
    overall = e1 and e2 and e3 and (not both_positive_sig) and e5
    print(f"暂行判据综合结果（未冻结，仅供参考）: E1={e1} E2={e2} E3={e3} E4触发降级={both_positive_sig} E5={e5}")
    print(f"若按当前暂行判据映射: {'方向确认存在，值得转正式确认阶段' if overall else '⭕ 未全部通过，方向性证据不完整'}")
    print("=" * 70)


if __name__ == "__main__":
    main()
