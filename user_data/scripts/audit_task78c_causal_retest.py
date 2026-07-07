"""
TASK 78c（预研）：global_lsr 反向信号——因果分位重测（幸存者最后一道门）

背景：TASK 78 电池 BTC 端 10/18 过四重判据；ETH 复制后仅 global_lsr 水平
（全市场多空比，反向）在 5d/20d 视界存活（BTC -1.90%/-5.73%，ETH -1.91%/-4.92%，
幅度惊人一致）。toptrader（BTC 端最强 -10.3%）ETH 不复制 → 判 BTC 特化/过拟合弃用。
本脚本消除最后一个口径弱点：全样本五分位 → 因果滚动百分位。

预注册判据（跑数前写定）：
  J1 crowded(过去252日百分位≥0.8) vs uncrowded(≤0.2) 前瞻收益差，
     5d/20d 视界、双资产同号为负（反向方向维持）
  J2 BTC 端 5d 或 20d 至少一档 bootstrap 95% CI 不含 0
  J3 熊市子集同号
  全过 → 进策略层（候选：DonchianV1 入场过滤）；任一失败 → 信号族关闭。
因果性：今日 global_lsr 与过去 252 日（不含今日）分布比较，收盘可知，无未来函数。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import numpy as np
import pandas as pd

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
ASSETS = [
    ("BTC",
     os.path.join(BASE, "data", "oi_metrics", "BTC_USDT_USDT-1h-oi_metrics.feather"),
     os.path.join(BASE, "data", "binance", "futures", "BTC_USDT_USDT-1d-futures.feather")),
    ("ETH",
     os.path.join(BASE, "data", "oi_metrics", "ETH_USDT_USDT-1h-oi_metrics.feather"),
     os.path.join(BASE, "data", "binance", "futures", "ETH_USDT_USDT-1d-futures.feather")),
]
WINDOW = 252
HORIZONS = [5, 20]
N_BOOT = 2000
SEED = 20260707


def causal_pct_rank(s, window):
    """今日值在过去 window 日（不含今日）中的百分位。因果。"""
    out = np.full(len(s), np.nan)
    v = s.values
    for i in range(window, len(s)):
        past = v[i - window:i]
        out[i] = (past < v[i]).mean()
    return out


def block_boot_diff(a, b, bl, n_boot, rng):
    def bm(x):
        n = len(x)
        nb = max(1, n // bl)
        idx = rng.integers(0, max(1, n - bl + 1), nb)
        return np.concatenate([x[i:i+bl] for i in idx]).mean()
    stats = np.array([bm(rng.permutation(a)) - bm(rng.permutation(b)) for _ in range(n_boot)])
    d = a.mean() - b.mean()
    return d + (np.percentile(stats, 2.5) - stats.mean()), d + (np.percentile(stats, 97.5) - stats.mean())


def main():
    rng = np.random.default_rng(SEED)
    j1 = j3 = True
    j2 = False
    for tag, mp, pp in ASSETS:
        m = pd.read_feather(mp)
        m['date'] = pd.to_datetime(m['date']).dt.tz_localize(None)
        m['day'] = m['date'].dt.floor('D')
        daily = m.groupby('day').agg(global_lsr=('count_long_short_ratio', 'mean')
                                     ).reset_index().rename(columns={'day': 'date'})
        px = pd.read_feather(pp).sort_values('date').reset_index(drop=True)
        px['date'] = pd.to_datetime(px['date']).dt.tz_localize(None)
        df = px[['date', 'close']].merge(daily, on='date', how='inner').reset_index(drop=True)
        df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
        df['bear'] = df['close'] < df['ema200']
        df['pct'] = causal_pct_rank(df['global_lsr'], WINDOW)
        for h in HORIZONS:
            df[f'fwd{h}'] = df['close'].shift(-h) / df['close'] - 1
        sub = df.dropna(subset=['pct'] + [f'fwd{h}' for h in HORIZONS])
        crowded = sub['pct'] >= 0.8
        uncrowded = sub['pct'] <= 0.2
        print(f"\n===== {tag}: n={len(sub)} 天（预热扣除后） crowded {crowded.sum()} / uncrowded {uncrowded.sum()} =====")
        for h in HORIZONS:
            a = sub.loc[crowded, f'fwd{h}'].values
            b = sub.loc[uncrowded, f'fwd{h}'].values
            d = a.mean() - b.mean()
            lo, hi = block_boot_diff(a, b, h, N_BOOT, rng)
            ci_ok = (lo > 0) or (hi < 0)
            bs = sub[sub['bear']]
            db = (bs.loc[bs['pct'] >= 0.8, f'fwd{h}'].mean()
                  - bs.loc[bs['pct'] <= 0.2, f'fwd{h}'].mean())
            print(f"  fwd{h:>2d}d: crowded-uncrowded {d:+.3%} CI[{lo:+.3%},{hi:+.3%}] "
                  f"熊市 {db:+.3%}  crowded绝对 {a.mean():+.3%} / uncrowded绝对 {b.mean():+.3%}")
            if d >= 0:
                j1 = False
            if tag == 'BTC' and ci_ok:
                j2 = True
            if not np.isnan(db) and np.sign(db) != np.sign(d):
                j3 = False
    print("\n" + "=" * 60)
    print(f"J1 双资产双视界同号为负: {'PASS' if j1 else 'FAIL'}")
    print(f"J2 BTC 至少一档 CI 不含零: {'PASS' if j2 else 'FAIL'}")
    print(f"J3 熊市子集同号: {'PASS' if j3 else 'FAIL'}")
    print(f"总判定: {'进入策略层' if (j1 and j2 and j3) else '信号族关闭'}")


if __name__ == "__main__":
    main()
