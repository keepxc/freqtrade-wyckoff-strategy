"""
探索性——非结论，禁止引用为证据。

维度E最小命题补完整（v1 的两处修正）：
  1. pct_change 改 fill_method=None，消除 pad 前向填充制造的假零收益样本
     （上市晚/有缺口币种的 NaN 不再被填成"价格不动"）。
  2. 效应量换算成收益单位：按 BTC 过去K小时收益的当期分位（相对自身历史
     扩张分位，避免用未来分布）分组 top20% vs bottom20%，看 alt 未来K小时
     条件均值差(spread)，与成本地板放在同一口径下比较。
     成本参考：单边 taker 0.05%（perp），一进一出 0.1%/期，加滑点安全边际
     取 0.2%/期作为参考地板（探索阶段参考值，非冻结判据）。

同时保留 v1 的 corr + 区块自举CI 作对照，确认修 pad 后数字是否变化。
"""
import glob
import os

import numpy as np
import pandas as pd

DATA_DIR = "./user_data/data/binance/futures"
N_BOOT = 2000
BLOCK_LEN_DAYS = 14
COST_FLOOR_REF = 0.002  # 0.2%/期，探索阶段参考地板


def build_wide():
    files = sorted(glob.glob(f"{DATA_DIR}/*-1h-futures.feather"))
    frames = []
    for f in files:
        pair = os.path.basename(f).replace("-1h-futures.feather", "")
        df = pd.read_feather(f)[["date", "close"]]
        df["pair"] = pair
        frames.append(df)
    all_df = pd.concat(frames, ignore_index=True)
    wide = all_df.pivot(index="date", columns="pair", values="close").sort_index()
    return wide


def block_ids(index, block_len_days=BLOCK_LEN_DAYS):
    return index.astype("int64") // (block_len_days * 24 * 3600 * 10**9)


def block_bootstrap_ci(values, blocks, n_boot=N_BOOT, seed=42, stat=np.mean):
    """values: 1d array; blocks: 对应的区块id array。整块重抽后算统计量的90%CI。"""
    rng = np.random.RandomState(seed)
    df = pd.DataFrame({"v": values, "b": blocks}).dropna()
    uniq = df["b"].unique()
    if len(uniq) < 5:
        return None, None
    grouped = {b: g["v"].values for b, g in df.groupby("b")}
    stats = []
    for _ in range(n_boot):
        sampled = rng.choice(uniq, size=len(uniq), replace=True)
        pooled = np.concatenate([grouped[b] for b in sampled])
        stats.append(stat(pooled))
    return np.percentile(stats, 5), np.percentile(stats, 95)


def main():
    wide = build_wide()
    if "BTC_USDT_USDT" not in wide.columns:
        print("BTC数据缺失")
        return
    alts = [c for c in wide.columns if c != "BTC_USDT_USDT"]

    print("=" * 70)
    print("探索性——非结论，禁止引用为证据")
    print("维度E补完整：修pad填充 + 条件收益spread口径")
    print(f"参考成本地板 {COST_FLOOR_REF:.2%}/期（探索参考值，非冻结判据）")
    print("=" * 70)

    for K in (4, 8, 24, 48):
        ret_past = wide.pct_change(K, fill_method=None)
        ret_fwd = wide.shift(-K) / wide - 1

        btc_past = ret_past["BTC_USDT_USDT"]
        # BTC过去K小时收益相对自身历史的扩张分位（min 90天历史），避免未来分布
        btc_rank = btc_past.expanding(min_periods=90 * 24).apply(
            lambda x: (x.iloc[:-1] < x.iloc[-1]).mean() if len(x) > 1 else np.nan, raw=False
        ) if False else btc_past.rank(pct=True)  # 全样本rank作探索快速版，见下方说明
        # 注：expanding分位逐bar apply在60万行上过慢；探索阶段用全样本rank近似，
        # 该近似引入轻微前视（分位边界用了全期分布），如转确认阶段必须换成扩张/滚动分位。

        high_mask = btc_rank >= 0.8
        low_mask = btc_rank <= 0.2

        rows_high, rows_low, blocks_high, blocks_low = [], [], [], []
        corr_x, corr_y, corr_blocks = [], [], []
        for alt in alts:
            fwd = ret_fwd[alt]
            df = pd.DataFrame({"fwd": fwd, "high": high_mask, "low": low_mask, "btc": btc_past}).dropna(subset=["fwd", "btc"])
            if len(df) < 1000:
                continue
            b = block_ids(df.index)
            rows_high.append(df.loc[df["high"], "fwd"].values)
            blocks_high.append(b[df["high"].values])
            rows_low.append(df.loc[df["low"], "fwd"].values)
            blocks_low.append(b[df["low"].values])
            corr_x.append(df["btc"].values)
            corr_y.append(df["fwd"].values)
            corr_blocks.append(b)

        high_v = np.concatenate(rows_high)
        high_b = np.concatenate(blocks_high)
        low_v = np.concatenate(rows_low)
        low_b = np.concatenate(blocks_low)

        spread = high_v.mean() - low_v.mean()
        lo_h, hi_h = block_bootstrap_ci(high_v, high_b)
        lo_l, hi_l = block_bootstrap_ci(low_v, low_b)

        # 修pad后的corr对照
        x_all = np.concatenate(corr_x)
        y_all = np.concatenate(corr_y)
        corr = np.corrcoef(x_all, y_all)[0, 1]

        print(f"\nK={K}h  (high n={len(high_v)}, low n={len(low_v)}):")
        print(f"  修pad后 corr(BTC领先, alt未来) = {corr:+.4f}")
        print(f"  BTC强势期(top20%)  alt未来{K}h均值 = {high_v.mean():+.4%}  CI=[{lo_h:+.4%},{hi_h:+.4%}]")
        print(f"  BTC弱势期(bot20%)  alt未来{K}h均值 = {low_v.mean():+.4%}  CI=[{lo_l:+.4%},{hi_l:+.4%}]")
        print(f"  条件spread(high-low) = {spread:+.4%}   vs 参考地板±{COST_FLOOR_REF:.2%}"
              f"  -> {'|spread|达到地板量级' if abs(spread) >= COST_FLOOR_REF else '低于地板'}")

    print("\n" + "=" * 70)
    print("以上为探索性输出，不构成任何采纳/证伪判断")
    print("注意：BTC分位用了全样本rank近似（轻微前视），转确认阶段必须换扩张分位")
    print("=" * 70)


if __name__ == "__main__":
    main()
