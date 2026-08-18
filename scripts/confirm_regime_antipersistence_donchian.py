"""
确认性检验——判据已冻结（2026-08-08，用户确认"按你说的来"）。

【命题】Donchian(20/10)突破信号触发时，入场前40天趋势状态(ER)预测该笔交易
收益；预注册方向：低ER组每笔收益 > 高ER组（40天趋势状态反持续的可交易含义）。

【冻结判据】
  主度量: ER40（信号确认bar收盘为止的40日效率比，无未来数据）
  分组: 逐币扩张分位中位数二分（当前ER40 vs 该币截至当时的全部历史ER40，
        需≥180个历史观测，不足则该信号剔除）
  模拟口径: 信号层——突破收盘确认(close>20日高点shift1) → 次日开盘入场；
        跌破10日低点shift1收盘确认 → 次日开盘出场；单币单仓、纯多头；
        数据末尾未平仓的交易剔除。费用为组间常数，不影响组间差，不计。
  宇宙: 现有16个futures币种全量，无事后剔除。
  C1 方向: mean(低ER组) > mean(高ER组)，且两组各 n≥30
  C2 效应量: 组间差(低-高) ≥ +1%/笔
  C3 显著性: 跨资产联合区块自举(按入场日期40天分块，块内全币种整体重抽，
        2000次)90% CI 下界 > 0
  映射: 三条全过→有资格进入"是否改策略代码"讨论(另一道门)；
        C1过但C2/C3不全过→⭕；方向相反→探索发现按证伪记录
  n<30 时改用区块自举分布检验并如实标注（CLAUDE.md低频路径）

【已声明的证据折价】发现(探索轮v1/v2)与本确认用同一批历史数据，无独立OOS；
无论结果如何，结论上限为"同数据确认，待增量数据复核"，不能是无保留✅。

【参考项（不进判定）】R2SLOPE / NDRIFT 同口径分组的组间差，仅作一致性参考。
"""
import glob
import os

import numpy as np
import pandas as pd

DATA_DIR = "./user_data/data/binance/futures"
N_ENTRY = 20
N_EXIT = 10
ER_WIN = 40
MIN_HIST = 180
N_BOOT = 2000
BLOCK_DAYS = 40


def load_daily(path):
    df = pd.read_feather(path)[["date", "open", "close"]]
    daily = df.set_index("date").resample("1D").agg({"open": "first", "close": "last"}).dropna()
    return daily


def rolling_er(close, win=ER_WIN):
    net = (close - close.shift(win - 1)).abs()
    path = close.diff().abs().rolling(win - 1).sum()
    return net / path.replace(0, np.nan)


def rolling_r2(close, win=ER_WIN):
    logp = np.log(close)
    t = np.arange(win)

    def _r2(x):
        if np.any(~np.isfinite(x)):
            return np.nan
        r = np.corrcoef(t, x)[0, 1]
        return r * r

    return logp.rolling(win).apply(_r2, raw=True)


def rolling_ndrift(close, win=ER_WIN):
    ret = close.pct_change()
    sd = ret.rolling(win).std()
    return (ret.rolling(win).sum().abs() / (sd * np.sqrt(win))).replace([np.inf, -np.inf], np.nan)


def expanding_percentile(series, idx, min_hist=MIN_HIST):
    """series.iloc[idx] 相对 series.iloc[:idx] 中有效历史值的分位；历史不足返回None"""
    hist = series.iloc[:idx].dropna()
    cur = series.iloc[idx]
    if pd.isna(cur) or len(hist) < min_hist:
        return None
    return (hist < cur).mean()


def simulate_pair(daily, pair):
    close = daily["close"]
    open_ = daily["open"]
    don_upper = close.rolling(N_ENTRY).max().shift(1)
    don_exit = close.rolling(N_EXIT).min().shift(1)
    er = rolling_er(close)
    r2 = rolling_r2(close)
    nd = rolling_ndrift(close)

    trades = []
    in_pos = False
    entry_price = None
    entry_info = None
    n_bars = len(daily)
    for i in range(n_bars - 1):  # 最后一根bar无法次日开盘成交
        if not in_pos:
            if not pd.isna(don_upper.iloc[i]) and close.iloc[i] > don_upper.iloc[i]:
                pct = expanding_percentile(er, i)
                if pct is None:
                    continue  # 历史不足，信号剔除（冻结规则）
                pct_r2 = expanding_percentile(r2, i)
                pct_nd = expanding_percentile(nd, i)
                in_pos = True
                entry_price = open_.iloc[i + 1]
                entry_info = {
                    "pair": pair,
                    "entry_date": daily.index[i + 1],
                    "er_pct": pct,
                    "r2_pct": pct_r2,
                    "nd_pct": pct_nd,
                }
        else:
            if not pd.isna(don_exit.iloc[i]) and close.iloc[i] < don_exit.iloc[i]:
                exit_price = open_.iloc[i + 1]
                entry_info["ret"] = exit_price / entry_price - 1
                trades.append(entry_info)
                in_pos = False
                entry_price, entry_info = None, None
    # 数据末尾未平仓交易剔除（冻结规则）
    return trades


def block_bootstrap_diff_ci(df, group_col, n_boot=N_BOOT, seed=42):
    """低组均值-高组均值 的跨资产联合区块自举90%CI（按入场日期40天分块）"""
    rng = np.random.RandomState(seed)
    df = df.copy()
    df["block"] = df["entry_date"].astype("int64") // (BLOCK_DAYS * 24 * 3600 * 10**9)
    blocks = df["block"].unique()
    grouped = {b: g for b, g in df.groupby("block")}
    diffs = []
    for _ in range(n_boot):
        sampled = rng.choice(blocks, size=len(blocks), replace=True)
        sub = pd.concat([grouped[b] for b in sampled])
        lo_g = sub[sub[group_col] == "low"]["ret"]
        hi_g = sub[sub[group_col] == "high"]["ret"]
        if len(lo_g) >= 5 and len(hi_g) >= 5:
            diffs.append(lo_g.mean() - hi_g.mean())
    diffs = np.array(diffs)
    if len(diffs) < 100:
        return None, None
    return np.percentile(diffs, 5), np.percentile(diffs, 95)


def main():
    files = sorted(glob.glob(f"{DATA_DIR}/*-1h-futures.feather"))
    all_trades = []
    for f in files:
        pair = os.path.basename(f).replace("-1h-futures.feather", "")
        daily = load_daily(f)
        all_trades.extend(simulate_pair(daily, pair))

    df = pd.DataFrame(all_trades)
    print("=" * 78)
    print("确认性检验：40天趋势状态反持续 × Donchian逐笔收益（判据已冻结）")
    print("声明：发现与确认同用一批历史数据，证据强度打折，结论上限为同数据确认")
    print("=" * 78)
    print(f"\n总模拟交易笔数: {len(df)}  (币种数: {df['pair'].nunique()})")

    df["er_group"] = np.where(df["er_pct"] >= 0.5, "high", "low")
    lo = df[df["er_group"] == "low"]["ret"]
    hi = df[df["er_group"] == "high"]["ret"]

    print(f"\n【主判据 ER40 扩张中位数二分】")
    print(f"  低ER组: n={len(lo)}  每笔均值={lo.mean():+.4%}  中位数={lo.median():+.4%}")
    print(f"  高ER组: n={len(hi)}  每笔均值={hi.mean():+.4%}  中位数={hi.median():+.4%}")
    diff = lo.mean() - hi.mean()
    print(f"  组间差(低-高) = {diff:+.4%}")

    c1 = (diff > 0) and len(lo) >= 30 and len(hi) >= 30
    c2 = diff >= 0.01
    ci = block_bootstrap_diff_ci(df, "er_group")
    if ci[0] is None:
        c3 = False
        ci_str = "自举失败(块内组样本不足)"
    else:
        c3 = ci[0] > 0
        ci_str = f"[{ci[0]:+.4%}, {ci[1]:+.4%}]"

    print(f"\n  C1 方向+样本量: 低>高={diff > 0}, n_low={len(lo)}, n_high={len(hi)} -> {'PASS' if c1 else 'FAIL'}")
    print(f"  C2 效应量(≥+1%/笔): {diff:+.4%} -> {'PASS' if c2 else 'FAIL'}")
    print(f"  C3 区块自举90%CI下界>0: CI={ci_str} -> {'PASS' if c3 else 'FAIL'}")

    if c1 and c2 and c3:
        verdict = "三条全过（同数据确认口径）→ 有资格进入'是否改策略代码'的讨论（另一道门+OOS要求）"
    elif diff < 0:
        verdict = "方向相反 → 反持续的可交易含义在逐笔层面不成立，按证伪记录探索发现的该项含义"
    else:
        verdict = "⭕ C1过但C2/C3不全过（或C1样本不足）"
    print(f"\n  冻结判据映射结果: {verdict}")

    print(f"\n【参考项（不进判定）】")
    for col, name in (("r2_pct", "R2SLOPE"), ("nd_pct", "NDRIFT")):
        sub = df.dropna(subset=[col]).copy()
        sub["g"] = np.where(sub[col] >= 0.5, "high", "low")
        l2 = sub[sub["g"] == "low"]["ret"]
        h2 = sub[sub["g"] == "high"]["ret"]
        print(f"  {name}: 低组均值={l2.mean():+.4%}(n={len(l2)})  高组均值={h2.mean():+.4%}(n={len(h2)})  "
              f"差={l2.mean() - h2.mean():+.4%}")

    print("\n" + "=" * 78)


if __name__ == "__main__":
    main()
