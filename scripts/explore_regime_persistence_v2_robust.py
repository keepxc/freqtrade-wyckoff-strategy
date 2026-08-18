"""
探索性——非结论，禁止引用为证据。

维度D稳健性复核：v1 发现40天窗口ER（趋势状态）在主流币上显著反持续
（BTC -0.36 / ETH -0.36 / BNB -0.40 等），且随窗口拉长单调加强。本轮检验
该发现是否是度量方式/窗口切法的伪影：

  R1. 换趋势度量：ER 之外加两个独立定义——
      R2SLOPE = 窗口内 log(close) 对时间回归的 R²（趋势拟合优度）
      NDRIFT  = |窗口净收益| / (日收益sd × √N)（标准化漂移，类似|t统计量|）
      三个度量若同时反持续 -> 非度量伪影；若只有ER反持续 -> ER定义的伪影。
  R2. 错开窗口起点相位：offset ∈ {0,10,20,30} 天，40天窗口。若只有 offset=0
      反持续 -> 切法伪影；若各相位一致 -> 稳健。
  R3. 前后半段分拆：offset=0 时窗口对序列一分为二，看两半符号是否一致
      （点估计，n小，只看方向）。

全部逐币计算，输出汇总（中位数/负号币数/显著负币数）+ BTC/ETH 单列。
"""
import glob
import os

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

DATA_DIR = "./user_data/data/binance/futures"
N_BOOT = 1000
N = 40
OFFSETS = (0, 10, 20, 30)


def load_daily_close(path):
    df = pd.read_feather(path)[["date", "close"]]
    return df.set_index("date")["close"].resample("1D").last().dropna()


def window_metrics(daily_close, N, offset):
    """从 offset 天开始切相邻非重叠N日窗口，返回 (ER, R2SLOPE, NDRIFT) 三个序列"""
    closes = daily_close.iloc[offset:]
    ret = closes.pct_change()
    n_win = len(closes) // N
    ers, r2s, nds = [], [], []
    for k in range(n_win):
        seg = closes.iloc[k * N:(k + 1) * N]
        seg_ret = ret.iloc[k * N:(k + 1) * N].dropna()
        if len(seg) < N or len(seg_ret) < N - 2:
            ers.append(np.nan); r2s.append(np.nan); nds.append(np.nan)
            continue
        net = abs(seg.iloc[-1] - seg.iloc[0])
        path = seg.diff().abs().sum()
        ers.append(net / path if path > 0 else np.nan)
        logp = np.log(seg.values)
        t = np.arange(len(logp))
        r = np.corrcoef(t, logp)[0, 1]
        r2s.append(r * r)
        sd = seg_ret.std()
        nds.append(abs(seg_ret.sum()) / (sd * np.sqrt(N)) if sd > 0 else np.nan)
    return np.array(ers), np.array(r2s), np.array(nds)


def boot_spearman(x, y, n_boot=N_BOOT, block_len=5, seed=42):
    rng = np.random.RandomState(seed)
    mask = ~(np.isnan(x) | np.isnan(y))
    x, y = x[mask], y[mask]
    n = len(x)
    if n < 15:
        return None, None, None, n
    r, _ = spearmanr(x, y)
    n_blocks = max(1, n // block_len)
    stats = []
    for _ in range(n_boot):
        idx = []
        for _ in range(n_blocks):
            start = rng.randint(0, max(1, n - block_len))
            idx.extend(range(start, min(start + block_len, n)))
        xb, yb = x[np.array(idx)], y[np.array(idx)]
        if np.std(xb) > 0 and np.std(yb) > 0:
            rb, _ = spearmanr(xb, yb)
            stats.append(rb)
    if not stats:
        return r, None, None, n
    return r, np.percentile(stats, 5), np.percentile(stats, 95), n


def summarize(results):
    vals = [r for (r, lo, hi, n) in results.values() if r is not None]
    sig_neg = sum(1 for (r, lo, hi, n) in results.values() if r is not None and hi is not None and hi < 0)
    neg = sum(1 for v in vals if v < 0)
    return (np.median(vals) if vals else np.nan), neg, sig_neg, len(vals)


def main():
    files = sorted(glob.glob(f"{DATA_DIR}/*-1h-futures.feather"))
    data = {}
    for f in files:
        pair = os.path.basename(f).replace("-1h-futures.feather", "")
        data[pair] = load_daily_close(f)

    print("=" * 78)
    print("探索性——非结论，禁止引用为证据")
    print(f"维度D稳健性复核：N={N}天窗口趋势状态反持续（三度量 × 四相位）")
    print("=" * 78)

    metric_names = ("ER", "R2SLOPE", "NDRIFT")

    print("\n[R1+R2] 度量 × 相位 汇总（逐币Spearman，中位数 / 负号币数 / 显著负币数）")
    all_cells = {}
    for offset in OFFSETS:
        for mi, mname in enumerate(metric_names):
            results = {}
            for pair, daily in data.items():
                metrics = window_metrics(daily, N, offset)
                s = metrics[mi]
                r, lo, hi, n = boot_spearman(s[:-1], s[1:])
                if r is not None:
                    results[pair] = (r, lo, hi, n)
            med, neg, sig_neg, total = summarize(results)
            all_cells[(offset, mname)] = results
            print(f"  offset={offset:>2}d  {mname:<8}: 中位数={med:+.3f}  负号{neg}/{total}币  显著负{sig_neg}币")

    print("\n[BTC/ETH 单列]（各度量 × 各相位的点估计）")
    for pair in ("BTC_USDT_USDT", "ETH_USDT_USDT"):
        line = f"  {pair}: "
        for mname in metric_names:
            vals = []
            for offset in OFFSETS:
                r_tuple = all_cells.get((offset, mname), {}).get(pair)
                vals.append(f"{r_tuple[0]:+.2f}" if r_tuple else "N/A")
            line += f"{mname}=[{','.join(vals)}]  "
        print(line)

    print("\n[R3] 前后半段分拆（offset=0，各度量，逐币两半Spearman点估计的符号一致性）")
    for mi, mname in enumerate(metric_names):
        both_neg, both_pos, mixed, total = 0, 0, 0, 0
        btc_note = ""
        for pair, daily in data.items():
            s = window_metrics(daily, N, 0)[mi]
            x, y = s[:-1], s[1:]
            mask = ~(np.isnan(x) | np.isnan(y))
            x, y = x[mask], y[mask]
            if len(x) < 24:
                continue
            half = len(x) // 2
            r1, _ = spearmanr(x[:half], y[:half])
            r2, _ = spearmanr(x[half:], y[half:])
            total += 1
            if r1 < 0 and r2 < 0:
                both_neg += 1
            elif r1 > 0 and r2 > 0:
                both_pos += 1
            else:
                mixed += 1
            if pair == "BTC_USDT_USDT":
                btc_note = f"(BTC: 前半{r1:+.2f} 后半{r2:+.2f})"
        print(f"  {mname:<8}: 两半均负{both_neg} / 两半均正{both_pos} / 混合{mixed} (共{total}币) {btc_note}")

    print("\n" + "=" * 78)
    print("以上为探索性输出，不构成任何采纳/证伪判断")
    print("=" * 78)


if __name__ == "__main__":
    main()
