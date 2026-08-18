"""
探索性——非结论，禁止引用为证据。

维度E最小命题：BTC过去K小时的方向性变化，能否预测其他资产未来K小时的收益，
且预测力超出该资产自身动量已解释的部分（排除"只是共同贝塔"的可能）。

方法：对每个 alt 币、每个 K，比较：
  (a) corr(BTC_pastK_ret, alt_fwdK_ret)          -- BTC领先信号
  (b) corr(alt_pastK_ret, alt_fwdK_ret)          -- alt自身动量(基线对照)
  (c) 双变量OLS: alt_fwdK_ret ~ BTC_pastK_ret + alt_pastK_ret，看BTC系数
      是否在控制了自身动量后仍然显著跳出（partial effect）

跨资产聚合时用区块自举(按日期分块)算CI，不用naive n。
"""
import glob
import os

import numpy as np
import pandas as pd

DATA_DIR = "./user_data/data/binance/futures"
N_BOOT = 2000
BLOCK_LEN = 14  # 天


def load_pair(pair_file_prefix):
    price_path = f"{DATA_DIR}/{pair_file_prefix}-1h-futures.feather"
    if not os.path.exists(price_path):
        return None
    price = pd.read_feather(price_path)[["date", "close"]]
    price["pair"] = pair_file_prefix
    return price


def build_hourly():
    files = sorted(glob.glob(f"{DATA_DIR}/*-1h-futures.feather"))
    pairs = [os.path.basename(f).replace("-1h-futures.feather", "") for f in files]
    frames = [d for p in pairs if (d := load_pair(p)) is not None]
    all_df = pd.concat(frames, ignore_index=True).sort_values(["pair", "date"])
    return all_df, pairs


def build_signals(all_df, K):
    wide = all_df.pivot(index="date", columns="pair", values="close").sort_index()
    ret_past = wide.pct_change(K)          # 过去K小时收益（到当前bar为止）
    ret_fwd = wide.shift(-K) / wide - 1     # 未来K小时收益
    return ret_past, ret_fwd


def block_bootstrap_corr_ci(x, y, dates, n_boot=N_BOOT, block_len_hours=BLOCK_LEN * 24, seed=42):
    rng = np.random.RandomState(seed)
    df = pd.DataFrame({"x": x, "y": y, "date": dates}).dropna()
    df["date_block"] = (pd.to_datetime(df["date"]).astype("int64") // (block_len_hours * 3600 * 10**9))
    blocks = df["date_block"].unique()
    if len(blocks) < 5 or len(df) < 30:
        return None, None
    corrs = []
    for _ in range(n_boot):
        sampled = rng.choice(blocks, size=len(blocks), replace=True)
        sub = pd.concat([df[df["date_block"] == b] for b in sampled])
        if len(sub) > 10 and sub["x"].std() > 0 and sub["y"].std() > 0:
            corrs.append(sub["x"].corr(sub["y"]))
    if not corrs:
        return None, None
    return np.percentile(corrs, 5), np.percentile(corrs, 95)


def main():
    all_df, pairs = build_hourly()
    if "BTC_USDT_USDT" not in pairs:
        print("BTC数据缺失，无法作为领先信号")
        return
    alts = [p for p in pairs if p != "BTC_USDT_USDT"]

    print("=" * 70)
    print("探索性——非结论，禁止引用为证据")
    print("维度E最小命题：BTC领先信号 vs alt自身动量")
    print("=" * 70)

    for K in (4, 8, 24, 48):
        ret_past, ret_fwd = build_signals(all_df, K)
        btc_past = ret_past["BTC_USDT_USDT"]

        pooled_btc_x, pooled_alt_x, pooled_y, pooled_dates = [], [], [], []
        for alt in alts:
            if alt not in ret_past.columns or alt not in ret_fwd.columns:
                continue
            df = pd.DataFrame({
                "btc_past": btc_past,
                "alt_past": ret_past[alt],
                "alt_fwd": ret_fwd[alt],
            }).dropna()
            if len(df) < 50:
                continue
            pooled_btc_x.append(df["btc_past"])
            pooled_alt_x.append(df["alt_past"])
            pooled_y.append(df["alt_fwd"])
            pooled_dates.append(pd.Series(df.index, index=df.index))

        if not pooled_y:
            print(f"K={K}h: 数据不足")
            continue

        btc_x = pd.concat(pooled_btc_x).reset_index(drop=True)
        alt_x = pd.concat(pooled_alt_x).reset_index(drop=True)
        y = pd.concat(pooled_y).reset_index(drop=True)
        dates = pd.concat(pooled_dates).reset_index(drop=True)

        corr_btc = btc_x.corr(y)
        corr_alt = alt_x.corr(y)

        # 双变量OLS（标准化后系数即偏相关方向）
        X = np.column_stack([np.ones(len(y)), btc_x.values, alt_x.values])
        beta, *_ = np.linalg.lstsq(X, y.values, rcond=None)
        beta_btc, beta_alt = beta[1], beta[2]

        lo, hi = block_bootstrap_corr_ci(btc_x, y, dates)
        ci_note = "N/A" if lo is None else (f"[{lo:+.4f},{hi:+.4f}]" + (" CI不含零" if (hi < 0 or lo > 0) else " CI跨零"))

        print(f"\nK={K}h  (pooled n={len(y)}, {len(pooled_y)}个alt币):")
        print(f"  corr(BTC领先, alt未来)       = {corr_btc:+.4f}   自举CI={ci_note}")
        print(f"  corr(alt自身动量, alt未来)   = {corr_alt:+.4f}")
        print(f"  双变量OLS系数: beta_BTC={beta_btc:+.4f}  beta_alt自身={beta_alt:+.4f}")

    print("\n" + "=" * 70)
    print("以上为探索性输出，不构成任何采纳/证伪判断")
    print("=" * 70)


if __name__ == "__main__":
    main()
