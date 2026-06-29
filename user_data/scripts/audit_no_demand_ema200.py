"""
audit_no_demand_ema200.py
审计：EMA200 附近 No Demand bar 的胜率和后续走势
方法论：
  - daily_ema200_persist：日线 close < EMA200 时激活，需要连续 5 天 close > EMA200 才重置
  - No Demand bar：窄价差 + 低成交量 + 偏低收盘（熊线特征）+ 靠近 EMA200
  - 统计信号后 6H / 12H / 24H 的价格变化（向下 = 空头获利）
"""
import pandas as pd
import numpy as np
from pathlib import Path

DATA_DIR = Path("user_data/data/binance")
H1_FILE  = DATA_DIR / "futures/BTC_USDT_USDT-1h-futures.feather"
D1_FILE  = DATA_DIR / "futures/BTC_USDT_USDT-1d-futures.feather"

# ── 加载数据 ──────────────────────────────────────────────────────────────────
df1h = pd.read_feather(H1_FILE)
df1d = pd.read_feather(D1_FILE)

df1h['date'] = pd.to_datetime(df1h['date']).dt.tz_localize(None)
df1d['date'] = pd.to_datetime(df1d['date']).dt.tz_localize(None)

df1h = df1h.sort_values('date').reset_index(drop=True)
df1d = df1d.sort_values('date').reset_index(drop=True)

# ── 日线 EMA200 ────────────────────────────────────────────────────────────────
df1d['ema200'] = df1d['close'].ewm(span=200, adjust=False).mean()

# ── daily_ema200_persist（复原策略逻辑）─────────────────────────────────────────
# 激活：close < ema200；重置：连续 5 天 close > ema200
df1d['above_ema'] = (df1d['close'] > df1d['ema200']).astype(int)

persist_vals = np.zeros(len(df1d), dtype=bool)
# 先激活规则
activated = False
consec_above = 0

for i in range(len(df1d)):
    if df1d['close'].iloc[i] < df1d['ema200'].iloc[i]:
        activated = True
        consec_above = 0
    else:
        if activated:
            consec_above += 1
            if consec_above >= 5:
                activated = False
                consec_above = 0
    persist_vals[i] = activated

df1d['ema200_persist'] = persist_vals

# ── 合并到 1H（日线 +1 天偏移，避免未来函数）──────────────────────────────────
df1d_shifted = df1d[['date', 'ema200', 'ema200_persist']].copy()
df1d_shifted['date'] = df1d_shifted['date'] + pd.Timedelta(days=1)
df1d_shifted.columns = ['merge_date', 'ema200', 'ema200_persist']

df1h['merge_date'] = df1h['date'].dt.normalize()
df1h = df1h.merge(df1d_shifted, on='merge_date', how='left')
df1h['ema200'] = df1h['ema200'].ffill()
df1h['ema200_persist'] = df1h['ema200_persist'].ffill().fillna(False)

# ── 1H 指标 ───────────────────────────────────────────────────────────────────
df1h['atr14'] = (df1h['high'] - df1h['low']).rolling(14, min_periods=3).mean()
df1h['vol_ma10'] = df1h['volume'].rolling(10, min_periods=3).mean()
df1h['spread'] = df1h['high'] - df1h['low']
df1h['close_pos'] = np.where(
    df1h['spread'] > 0,
    (df1h['close'] - df1h['low']) / df1h['spread'],
    0.5
)

# ── No Demand bar 定义 ─────────────────────────────────────────────────────────
# 1. 偏低收盘（close_pos < 0.45：熊线或十字星）
# 2. 窄价差（spread < atr14 × 0.55）
# 3. 低成交量（volume < vol_ma10 × 0.75）
# 4. EMA200 persist 激活
# 5. 价格在 EMA200 的 [0%, +3%] 以内（反弹触及 EMA200 区域）
df1h['dist_ema_pct'] = (df1h['close'] - df1h['ema200']) / df1h['ema200'] * 100

no_demand_mask = (
    df1h['ema200_persist'] &
    (df1h['close_pos'] < 0.45) &
    (df1h['spread'] < df1h['atr14'] * 0.55) &
    (df1h['volume'] < df1h['vol_ma10'] * 0.75) &
    (df1h['dist_ema_pct'] >= 0.0) &   # 价格在 EMA200 上方（反弹触及）
    (df1h['dist_ema_pct'] <= 3.0)      # 不超过 3%
)

signals = df1h[no_demand_mask].copy()
print(f"时间范围: {df1h['date'].min()} → {df1h['date'].max()}")
print(f"EMA200_persist 激活 bar 数: {df1h['ema200_persist'].sum()}")
print(f"No Demand @ EMA200 信号数: {len(signals)}")
print(f"信号年均频率: {len(signals) / ((df1h['date'].max() - df1h['date'].min()).days / 365):.1f} 次/年")
print()

# ── 后续走势分析 ──────────────────────────────────────────────────────────────
results = []
close_arr = df1h['close'].values
date_arr  = df1h['date'].values
n = len(df1h)

for idx in signals.index:
    entry_close = close_arr[idx]
    entry_date  = date_arr[idx]
    row = {}
    row['date']      = pd.Timestamp(entry_date)
    row['close']     = entry_close
    row['ema200']    = df1h.loc[idx, 'ema200']
    row['dist_pct']  = df1h.loc[idx, 'dist_ema_pct']
    row['volume_ratio'] = (df1h.loc[idx, 'volume'] / df1h.loc[idx, 'vol_ma10']
                           if df1h.loc[idx, 'vol_ma10'] > 0 else np.nan)

    for h in [6, 12, 24, 48]:
        target_idx = idx + h
        if target_idx < n:
            fwd_close = close_arr[target_idx]
            row[f'ret_{h}h'] = (fwd_close - entry_close) / entry_close * 100
        else:
            row[f'ret_{h}h'] = np.nan

    results.append(row)

df_res = pd.DataFrame(results)
print("=" * 60)
print("后续价格变化（空头视角：负值 = 获利）")
print("=" * 60)

for h in [6, 12, 24, 48]:
    col = f'ret_{h}h'
    valid = df_res[col].dropna()
    wins = (valid < 0).sum()   # 价格下跌 = 空头获利
    total = len(valid)
    avg_ret = valid.mean()
    med_ret = valid.median()
    pct_dn_1 = (valid < -1.0).sum() / total * 100
    pct_dn_2 = (valid < -2.0).sum() / total * 100
    print(f"\n{h:>3}H 后:")
    print(f"  样本数  : {total}")
    print(f"  空头胜率: {wins/total*100:.1f}%  (价格下跌)")
    print(f"  平均涨跌: {avg_ret:+.2f}%")
    print(f"  中位数  : {med_ret:+.2f}%")
    print(f"  跌幅>1% : {pct_dn_1:.1f}%")
    print(f"  跌幅>2% : {pct_dn_2:.1f}%")

# ── 按年分布 ───────────────────────────────────────────────────────────────────
print()
print("=" * 60)
print("按年信号分布")
print("=" * 60)
df_res['year'] = df_res['date'].dt.year
for yr, grp in df_res.groupby('year'):
    wins_24 = (grp['ret_24h'] < 0).sum()
    total   = grp['ret_24h'].notna().sum()
    avg     = grp['ret_24h'].mean()
    print(f"  {yr}: {len(grp):>3} 次信号  |  24H胜率 {wins_24/total*100:.0f}%  |  avg ret {avg:+.2f}%")

# ── 与现有 wsdown 信号的重叠估计 ─────────────────────────────────────────────
print()
print("=" * 60)
print("信号聚集性（同一 48H 窗口内的重复信号）")
print("=" * 60)
df_res_s = df_res.sort_values('date').reset_index(drop=True)
df_res_s['gap'] = df_res_s['date'].diff().dt.total_seconds() / 3600
clusters = (df_res_s['gap'] > 48).sum() + 1
print(f"  信号总数: {len(df_res_s)}")
print(f"  独立信号群（48H 间隔）: {clusters}")
print(f"  平均每群信号数: {len(df_res_s)/clusters:.1f}")

# ── 价格区间分析 ───────────────────────────────────────────────────────────────
print()
print("=" * 60)
print("信号发生时的 EMA200 区间分布")
print("=" * 60)
bins = [0.0, 0.5, 1.0, 1.5, 2.0, 3.0]
labels = ['0-0.5%', '0.5-1%', '1-1.5%', '1.5-2%', '2-3%']
df_res['dist_bin'] = pd.cut(df_res['dist_pct'], bins=bins, labels=labels)
for lbl, grp in df_res.groupby('dist_bin', observed=True):
    wins = (grp['ret_24h'] < 0).sum()
    total = grp['ret_24h'].notna().sum()
    wr = wins / total * 100 if total > 0 else 0
    print(f"  距 EMA200 {lbl}: {len(grp):>3} 次  |  24H胜率 {wr:.0f}%  |  avg {grp['ret_24h'].mean():+.2f}%")

print()
print("审计完成")
