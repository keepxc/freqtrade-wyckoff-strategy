"""
探索性——非结论，禁止引用为证据。

维度D最小命题：市场状态本身有没有可预测的持续性。
  - 状态变量1: Kaufman效率比 ER = |close_t - close_{t-N}| / sum|daily_move|
    （趋势性状态，真正待答的问题）
  - 状态变量2: 已实现波动率 RV = std(daily_ret, N) （阳性对照——波动聚集
    是公认事实，若RV持续性测不出强阳性说明方法有问题）

方法：逐币、逐窗口N，取相邻非重叠窗口的状态值序列 (state_k, state_{k+1})，
算Spearman秩相关；对(state_k, state_{k+1})对序列做移动区块自举90%CI。
另报中位数二分的转移概率 P(高|高)（相对无条件50%的偏离）。

逐币独立报告（时序命题不合并），汇总只报符号一致数与中位数。
"""
import glob
import os

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

DATA_DIR = "./user_data/data/binance/futures"
N_BOOT = 2000
WINDOWS = (10, 20, 40)


def load_daily_close(path):
    df = pd.read_feather(path)[["date", "close"]]
    daily = df.set_index("date")["close"].resample("1D").last().dropna()
    return daily


def window_states(daily_close, N):
    """相邻非重叠N日窗口的 ER 与 RV 序列"""
    ret = daily_close.pct_change()
    n_win = len(daily_close) // N
    ers, rvs = [], []
    for k in range(n_win):
        seg_close = daily_close.iloc[k * N:(k + 1) * N]
        seg_ret = ret.iloc[k * N:(k + 1) * N].dropna()
        if len(seg_close) < N or len(seg_ret) < N - 2:
            ers.append(np.nan)
            rvs.append(np.nan)
            continue
        net = abs(seg_close.iloc[-1] - seg_close.iloc[0])
        path = seg_close.diff().abs().sum()
        ers.append(net / path if path > 0 else np.nan)
        rvs.append(seg_ret.std())
    return np.array(ers), np.array(rvs)


def block_bootstrap_spearman_ci(x, y, n_boot=N_BOOT, block_len=5, seed=42):
    """对(x_k, y_k)配对序列做移动区块自举（块长5对，覆盖相邻窗口对的序列依赖）"""
    rng = np.random.RandomState(seed)
    mask = ~(np.isnan(x) | np.isnan(y))
    x, y = x[mask], y[mask]
    n = len(x)
    if n < 15:
        return None, None, None
    r, _ = spearmanr(x, y)
    n_blocks = max(1, n // block_len)
    stats = []
    for _ in range(n_boot):
        idx = []
        for _ in range(n_blocks):
            start = rng.randint(0, max(1, n - block_len))
            idx.extend(range(start, min(start + block_len, n)))
        xb, yb = x[idx], y[idx]
        if np.std(xb) > 0 and np.std(yb) > 0:
            rb, _ = spearmanr(xb, yb)
            stats.append(rb)
    if not stats:
        return r, None, None
    return r, np.percentile(stats, 5), np.percentile(stats, 95)


def persistence_of(series):
    """相邻窗口对 (state_k, state_{k+1})"""
    return series[:-1], series[1:]


def transition_prob_high_high(series):
    mask = ~np.isnan(series)
    s = series[mask]
    if len(s) < 15:
        return None
    med = np.median(s)
    hi = s >= med
    prev, nxt = hi[:-1], hi[1:]
    if prev.sum() == 0:
        return None
    return (prev & nxt).sum() / prev.sum()


def main():
    files = sorted(glob.glob(f"{DATA_DIR}/*-1h-futures.feather"))
    print("=" * 78)
    print("探索性——非结论，禁止引用为证据")
    print("维度D最小命题：状态持续性（ER=趋势状态待答项 / RV=波动阳性对照）")
    print("=" * 78)

    for N in WINDOWS:
        print(f"\n### 窗口 N={N} 天（相邻非重叠窗口，逐币Spearman秩相关 + 区块自举90%CI）")
        rows = []
        for f in files:
            pair = os.path.basename(f).replace("-1h-futures.feather", "")
            daily = load_daily_close(f)
            ers, rvs = window_states(daily, N)
            er_x, er_y = persistence_of(ers)
            rv_x, rv_y = persistence_of(rvs)
            er_r, er_lo, er_hi = block_bootstrap_spearman_ci(er_x, er_y)
            rv_r, rv_lo, rv_hi = block_bootstrap_spearman_ci(rv_x, rv_y)
            er_hh = transition_prob_high_high(ers)
            if er_r is None and rv_r is None:
                continue
            rows.append((pair, er_r, er_lo, er_hi, rv_r, rv_lo, rv_hi, er_hh,
                         int(np.sum(~(np.isnan(er_x) | np.isnan(er_y))))))

        for (pair, er_r, er_lo, er_hi, rv_r, rv_lo, rv_hi, er_hh, n_pairs) in rows:
            er_note = "" if er_lo is None else f" CI=[{er_lo:+.3f},{er_hi:+.3f}]{'*' if (er_lo > 0 or er_hi < 0) else ''}"
            rv_note = "" if rv_lo is None else f" CI=[{rv_lo:+.3f},{rv_hi:+.3f}]{'*' if (rv_lo > 0 or rv_hi < 0) else ''}"
            hh = "N/A" if er_hh is None else f"{er_hh:.2f}"
            er_s = "N/A" if er_r is None else f"{er_r:+.3f}"
            rv_s = "N/A" if rv_r is None else f"{rv_r:+.3f}"
            print(f"  {pair:<18} n_pairs={n_pairs:>3}  ER持续:{er_s}{er_note:<22}  "
                  f"RV持续(对照):{rv_s}{rv_note:<22}  P(ER高|ER高)={hh}")

        er_vals = [r[1] for r in rows if r[1] is not None]
        rv_vals = [r[4] for r in rows if r[4] is not None]
        if er_vals and rv_vals:
            er_pos = sum(1 for v in er_vals if v > 0)
            rv_pos = sum(1 for v in rv_vals if v > 0)
            print(f"  -- 汇总: ER持续性中位数={np.median(er_vals):+.3f} (正号{er_pos}/{len(er_vals)}币)   "
                  f"RV持续性中位数={np.median(rv_vals):+.3f} (正号{rv_pos}/{len(rv_vals)}币)")

    print("\n" + "=" * 78)
    print("以上为探索性输出，不构成任何采纳/证伪判断")
    print("解读提示：若RV(对照)不显著为正则方法有问题；ER是否持续才是本轮待答项")
    print("=" * 78)


if __name__ == "__main__":
    main()
