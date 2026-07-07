"""
TASK 78（预研）：订单流信号族电池——多空比/顶级交易员/taker买卖比

背景：用户授权更换信号族。无符号量能维度已死（量失衡比中位0.99 / TASK 69
OI四象限全灭），本电池测试的是**带方向符号**的订单流原语（不同原语，非重试）。
L2订单簿无历史数据，排除。CVD（K线taker字段）待数据下载后另测（TASK 78b）。

数据：user_data/data/oi_metrics/BTC_USDT_USDT-1h-oi_metrics.feather
  （2020-09 ~ 2026-07，1h）聚合到日级；价格用 futures 1d。
信号在 D 日收盘可知（D 日内 1h 数据聚合），前瞻收益从 D 收盘起算，因果。

指标电池（跑数前固定，禁止事后增删）：
  1 taker_ratio_level   sum_taker_long_short_vol_ratio 日均值
  2 taker_ratio_z20     同上相对过去20日的 z 分数（均值/方差 shift(1)）
  3 toptrader_level     sum_toptrader_long_short_ratio 日均值
  4 toptrader_z20       同上 z 分数
  5 global_lsr_level    count_long_short_ratio 日均值
  6 global_lsr_z20      同上 z 分数
视界：1d / 5d / 20d 前瞻收益（重叠样本，bootstrap 用块抽样近似处理）。

预注册判据（四重同过才算候选，之后还须 ETH 复制）：
  H1 Q5-Q1 前瞻收益差的 bootstrap 95% CI 不含 0
  H2 效应量：|Q5-Q1| ≥ 0.15%/天（5d≥0.75%，20d≥3.0%）
  H3 时间对半切两半 Q5-Q1 同号
  H4 熊市子集（close<EMA200）Q5-Q1 与全样本同号
多重检验申明：6指标×3视界=18检验，预期噪声假阳性≈1个，故四重判据+跨资产
复制缺一不可。分位数用全样本五分位（筛查口径，幸存者需因果分位重测）。
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
    ("ETH",  # 复制检验（2021-12 起，归档起点限制）
     os.path.join(BASE, "data", "oi_metrics", "ETH_USDT_USDT-1h-oi_metrics.feather"),
     os.path.join(BASE, "data", "binance", "futures", "ETH_USDT_USDT-1d-futures.feather")),
]
HORIZONS = [1, 5, 20]
EFFECT_FLOOR_PER_DAY = 0.0015
N_BOOT = 2000
SEED = 20260707


def zscore20(s):
    mu = s.rolling(20).mean().shift(1)
    sd = s.rolling(20).std().shift(1)
    return (s - mu) / sd


def block_bootstrap_ci(diffs_a, diffs_b, horizon, n_boot, rng):
    """Q5/Q1 两组前瞻收益均值差的块 bootstrap CI（块长=horizon 处理重叠）。"""
    def block_mean(x):
        n = len(x)
        if n == 0:
            return np.nan
        bl = max(1, horizon)
        n_blocks = max(1, n // bl)
        idx = rng.integers(0, n - bl + 1, n_blocks) if n >= bl else np.array([0])
        return np.concatenate([x[i:i+bl] for i in idx]).mean()
    stats = np.array([block_mean(rng.permutation(diffs_a)) - block_mean(rng.permutation(diffs_b))
                      for _ in range(n_boot)])
    # 置换后块均值差的分布中心即点估计差；CI 用分位数
    return np.percentile(stats, 2.5), np.percentile(stats, 97.5)


def run_asset(tag, metrics_path, price_path):
    rng = np.random.default_rng(SEED)
    m = pd.read_feather(metrics_path)
    m['date'] = pd.to_datetime(m['date']).dt.tz_localize(None)
    m['day'] = m['date'].dt.floor('D')
    daily = m.groupby('day').agg(
        taker_ratio=('sum_taker_long_short_vol_ratio', 'mean'),
        toptrader=('sum_toptrader_long_short_ratio', 'mean'),
        global_lsr=('count_long_short_ratio', 'mean'),
    ).reset_index().rename(columns={'day': 'date'})

    px = pd.read_feather(price_path).sort_values('date').reset_index(drop=True)
    px['date'] = pd.to_datetime(px['date']).dt.tz_localize(None)
    df = px[['date', 'close']].merge(daily, on='date', how='inner').sort_values('date').reset_index(drop=True)
    df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
    df['bear'] = df['close'] < df['ema200']
    for h in HORIZONS:
        df[f'fwd{h}'] = df['close'].shift(-h) / df['close'] - 1

    df['taker_ratio_z20'] = zscore20(df['taker_ratio'])
    df['toptrader_z20'] = zscore20(df['toptrader'])
    df['global_lsr_z20'] = zscore20(df['global_lsr'])

    indicators = ['taker_ratio', 'taker_ratio_z20', 'toptrader', 'toptrader_z20',
                  'global_lsr', 'global_lsr_z20']
    mid = len(df) // 2
    print(f"样本: {df['date'].iloc[0].date()} ~ {df['date'].iloc[-1].date()}  n={len(df)} 天  "
          f"熊市子集 {df['bear'].mean():.0%}")
    n_pass = 0
    for ind in indicators:
        print(f"\n--- {ind} ---")
        for h in HORIZONS:
            sub = df[[ind, f'fwd{h}', 'bear']].dropna()
            q = pd.qcut(sub[ind], 5, labels=False, duplicates='drop')
            qmeans = sub.groupby(q)[f'fwd{h}'].mean()
            q1r = sub.loc[q == 0, f'fwd{h}'].values
            q5r = sub.loc[q == q.max(), f'fwd{h}'].values
            diff = q5r.mean() - q1r.mean()
            lo, hi = block_bootstrap_ci(q5r, q1r, h, N_BOOT, rng)
            # CI 中心校准到点估计
            lo, hi = diff + (lo - (q5r.mean() - q1r.mean())), diff + (hi - (q5r.mean() - q1r.mean()))
            h1 = (lo > 0) or (hi < 0)
            h2 = abs(diff) >= EFFECT_FLOOR_PER_DAY * h
            sub1, sub2 = sub.iloc[:mid], sub.iloc[mid:]
            def qdiff(s):
                if len(s) < 50:
                    return np.nan
                qq = pd.qcut(s[ind], 5, labels=False, duplicates='drop')
                return s.loc[qq == qq.max(), f'fwd{h}'].mean() - s.loc[qq == 0, f'fwd{h}'].mean()
            d1, d2 = qdiff(sub1), qdiff(sub2)
            h3 = (not np.isnan(d1)) and (not np.isnan(d2)) and np.sign(d1) == np.sign(d2) == np.sign(diff)
            db = qdiff(sub[sub['bear']])
            h4 = (not np.isnan(db)) and np.sign(db) == np.sign(diff)
            ok = h1 and h2 and h3 and h4
            n_pass += int(ok)
            mono = qmeans.round(4).tolist()
            print(f"  fwd{h:>2d}d: Q5-Q1 {diff:+.3%} CI[{lo:+.3%},{hi:+.3%}] "
                  f"H1{'√' if h1 else '×'} H2{'√' if h2 else '×'} "
                  f"半段({d1:+.3%}/{d2:+.3%}){'√' if h3 else '×'} "
                  f"熊({db:+.3%}){'√' if h4 else '×'} "
                  f"{'** PASS **' if ok else ''}  分位均值 {mono}")
    print("\n" + "=" * 70)
    print(f"[{tag}] 四重判据通过数: {n_pass} / {len(indicators) * len(HORIZONS)}  "
          f"(多重检验预期噪声假阳性 ≈1；幸存者须跨资产复制)")


def main():
    for tag, metrics_path, price_path in ASSETS:
        if not os.path.exists(metrics_path):
            print(f"\n########## {tag}: metrics 缺失，跳过 ##########")
            continue
        print(f"\n########## {tag} ##########")
        run_asset(tag, metrics_path, price_path)


if __name__ == "__main__":
    main()
