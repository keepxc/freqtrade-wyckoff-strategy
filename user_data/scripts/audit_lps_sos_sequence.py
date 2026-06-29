"""
audit_lps_sos_sequence.py
审计威科夫"危险点"三步序列：SOS₁ → LPS（无供应）→ SOS₂（显著棒触发）
对比：lps_signal 单独 vs 完整 LPS→SOS₂ 序列的胜率和收益质量

书中原文定义：
  No Supply：窄价差 + 收盘偏高 + 成交量低于前两根K线
  显著棒：价差扩张 + 放量 + 强收盘 + 收盘突破前阻力
  入场：显著棒收盘
  止损：Spring 最低点（TR Low 外侧）

代理条件（无 Phase 状态机输出）：
  多头路径门控：日线 close > EMA200（非分发区间）
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

# ── 日线 EMA200（+1天偏移避免未来函数）──────────────────────────────────────
df1d['ema200'] = df1d['close'].ewm(span=200, adjust=False).mean()
df1d['bull_market'] = df1d['close'] > df1d['ema200']
df1d_shifted = df1d[['date', 'ema200', 'bull_market']].copy()
df1d_shifted['date'] += pd.Timedelta(days=1)
df1d_shifted.columns = ['merge_date', 'ema200', 'bull_market']

df1h['merge_date'] = df1h['date'].dt.normalize()
df1h = df1h.merge(df1d_shifted, on='merge_date', how='left')
df1h['ema200']      = df1h['ema200'].ffill()
df1h['bull_market'] = df1h['bull_market'].ffill().fillna(False)

# ── 1H 基础指标 ───────────────────────────────────────────────────────────────
df1h['atr14']   = (df1h['high'] - df1h['low']).rolling(14, min_periods=3).mean()
df1h['vol_ma10'] = df1h['volume'].rolling(10, min_periods=3).mean()
df1h['spread']   = df1h['high'] - df1h['low']
df1h['move_pct'] = (df1h['close'] - df1h['open']) / df1h['open'].replace(0, np.nan) * 100
df1h['close_pos'] = np.where(
    df1h['spread'] > 0,
    (df1h['close'] - df1h['low']) / df1h['spread'], 0.5
)

# ── SOS 定义（书中显著棒 + 多头）────────────────────────────────────────────
sos_mask = (
    (df1h['close'] > df1h['open']) &
    (df1h['spread'] > df1h['atr14'] * 0.6) &
    (df1h['volume'] > df1h['vol_ma10'] * 1.2) &
    (df1h['move_pct'] > 0.5)
)
df1h['is_sos'] = sos_mask

# ── No Supply 定义（书中精确：volume < 前两根K线）────────────────────────────
# 同时满足：窄价差 + 收盘偏高 + volume < vol[i-1] AND vol[i-2]
no_supply_mask = (
    (df1h['spread'] < df1h['atr14'] * 0.55) &
    (df1h['close_pos'] > 0.50) &
    (df1h['volume'] < df1h['volume'].shift(1)) &
    (df1h['volume'] < df1h['volume'].shift(2))
)
df1h['is_no_supply'] = no_supply_mask

# ── 完整三步序列检测 ──────────────────────────────────────────────────────────
# 步骤：SOS₁（j） → LPS/No Supply（中间 1-6 bars） → SOS₂（i，触发入场）
# 额外要求：SOS₂ 收盘突破 SOS₁ 的收盘价（书中"确权"）
# 多头门控：bull_market=True

n = len(df1h)
sos1_idx   = np.full(n, -1, dtype=int)    # SOS₁ 的 index
lps_idx    = np.full(n, -1, dtype=int)    # LPS bar 的 index
seq_signal = np.zeros(n, dtype=bool)      # 完整序列触发

close_arr  = df1h['close'].values
low_arr    = df1h['low'].values
is_sos_arr = df1h['is_sos'].values
is_ns_arr  = df1h['is_no_supply'].values
bull_arr   = df1h['bull_market'].values
spread_arr = df1h['spread'].values
atr_arr    = df1h['atr14'].values
vol_arr    = df1h['volume'].values
vol_ma_arr = df1h['vol_ma10'].values
open_arr   = df1h['open'].values
move_arr   = df1h['move_pct'].values

for i in range(10, n):
    if not bull_arr[i]:
        continue
    # SOS₂：当前 bar 必须是 SOS-type
    if not is_sos_arr[i]:
        continue
    # 向前找 LPS bar（i-1 到 i-6）
    found_lps = -1
    for k in range(i - 1, max(i - 7, -1), -1):
        if is_ns_arr[k]:
            found_lps = k
            break
    if found_lps < 0:
        continue
    # LPS 之前找 SOS₁（found_lps-1 到 found_lps-8）
    found_sos1 = -1
    for j in range(found_lps - 1, max(found_lps - 9, -1), -1):
        if is_sos_arr[j]:
            found_sos1 = j
            break
    if found_sos1 < 0:
        continue
    # 书中确权：SOS₂ 收盘高于 SOS₁ 收盘（或至少高于 SOS₁ high）
    if close_arr[i] < close_arr[found_sos1]:
        continue
    # LPS bar 收盘不低于 SOS₁ low×0.99（结构保持）
    if close_arr[found_lps] < low_arr[found_sos1] * 0.99:
        continue

    seq_signal[i] = True
    sos1_idx[i]   = found_sos1
    lps_idx[i]    = found_lps

df1h['seq_signal'] = seq_signal

# ── 也复原 lps_signal（单独 LPS，不含 SOS₂）────────────────────────────────
lps_only = np.zeros(n, dtype=bool)
for i in range(6, n):
    if not is_ns_arr[i]:
        continue
    if not bull_arr[i]:
        continue
    for j in range(i - 1, max(i - 7, -1), -1):
        if is_sos_arr[j]:
            if close_arr[i] >= low_arr[j] * 0.99:
                lps_only[i] = True
            break
df1h['lps_only'] = lps_only

# ── 前向收益统计 ──────────────────────────────────────────────────────────────
def calc_returns(mask_col, label):
    idx_list = df1h.index[df1h[mask_col]].tolist()
    rows = []
    for idx in idx_list:
        entry = close_arr[idx]
        row = {'date': df1h.loc[idx, 'date'], 'close': entry}
        for h in [6, 12, 24, 48]:
            ti = idx + h
            if ti < n:
                row[f'ret_{h}h'] = (close_arr[ti] - entry) / entry * 100
            else:
                row[f'ret_{h}h'] = np.nan
        rows.append(row)
    df = pd.DataFrame(rows)

    print(f"\n{'='*60}")
    print(f"{label}  (n={len(df)})")
    print(f"{'='*60}")
    if len(df) == 0:
        print("  无信号")
        return df

    for h in [6, 12, 24, 48]:
        col = f'ret_{h}h'
        v = df[col].dropna()
        wins = (v > 0).sum()  # 多头：价格上涨 = 获利
        print(f"  {h:>3}H: 多头胜率 {wins/len(v)*100:.1f}%  "
              f"avg {v.mean():+.2f}%  med {v.median():+.2f}%  "
              f">+1% {(v>1).sum()/len(v)*100:.0f}%  >+2% {(v>2).sum()/len(v)*100:.0f}%")
    return df

print(f"数据范围: {df1h['date'].min()} → {df1h['date'].max()}")
print(f"bull_market 激活 bar 数: {df1h['bull_market'].sum()}")
print(f"SOS bar 数: {df1h['is_sos'].sum()}")
print(f"No Supply bar 数（书中定义）: {df1h['is_no_supply'].sum()}")
print(f"\nlps_only signal count (SOS1+LPS, no SOS2): {df1h['lps_only'].sum()}")
print(f"full sequence signal count (SOS1+LPS+SOS2): {df1h['seq_signal'].sum()}")

df_lps  = calc_returns('lps_only',  'LPS only (no SOS2 confirm)')
df_seq  = calc_returns('seq_signal','Full sequence (SOS1 -> LPS -> SOS2)')

# ── 年度分布 ───────────────────────────────────────────────────────────────────
if len(df_seq) > 0:
    print(f"\n{'='*60}")
    print("完整序列 按年分布")
    print(f"{'='*60}")
    df_seq['year'] = df_seq['date'].dt.year
    for yr, grp in df_seq.groupby('year'):
        v = grp['ret_24h'].dropna()
        wr = (v > 0).sum() / len(v) * 100 if len(v) else 0
        print(f"  {yr}: {len(grp):>3} 次  24H胜率 {wr:.0f}%  avg {v.mean():+.2f}%")

# ── 信号时间分布（每次信号间隔）─────────────────────────────────────────────
if len(df_seq) > 1:
    df_seq_s = df_seq.sort_values('date')
    gaps = df_seq_s['date'].diff().dt.total_seconds().dropna() / 3600
    print(f"\n完整序列信号间隔: 中位 {gaps.median():.0f}h  min {gaps.min():.0f}h  max {gaps.max():.0f}h")
    clusters = (gaps > 48).sum() + 1
    print(f"独立信号群（48H间隔）: {clusters}  平均每群 {len(df_seq)/clusters:.1f} 次")

# ── 与 lps_signal 的交集 ──────────────────────────────────────────────────────
print(f"\n{'='*60}")
print("序列信号覆盖分析")
print(f"{'='*60}")
# 完整序列信号中，lps bar 到 SOS₂ 的间距
if df1h['seq_signal'].sum() > 0:
    seq_rows = df1h[df1h['seq_signal']].copy()
    seq_rows['lps_lag'] = [i - lps_idx[i] for i in seq_rows.index]
    seq_rows['sos1_lag'] = [lps_idx[i] - sos1_idx[i] for i in seq_rows.index]
    print(f"  LPS->SOS2 lag: median {seq_rows['lps_lag'].median():.0f}H  range [{seq_rows['lps_lag'].min()}, {seq_rows['lps_lag'].max()}]")
    print(f"  SOS1->LPS lag: median {seq_rows['sos1_lag'].median():.0f}H  range [{seq_rows['sos1_lag'].min()}, {seq_rows['sos1_lag'].max()}]")

print("\n审计完成")
