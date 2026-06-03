"""
确认税法医审计：Phase D vs Phase E 入场延迟分析
============================================================
目标：从回测入场信号中拆解每笔多头的 wave_start → entry 延迟
按 v8_phase（D/E）分组，定量回答：
  - 延迟主要发生在哪个 Phase？
  - 错过的前段涨幅（price gap）有多大？
  - min_bars 优化空间在哪里？
"""

import sys, os
sys.path.insert(0, '/freqtrade')

import pandas as pd
import numpy as np
from pathlib import Path

# ── 加载 1H 数据 ─────────────────────────────────────────────
DATA_PATH = Path('/freqtrade/user_data/data/binance/futures/BTC_USDT_USDT-1h.feather')
df_1h = pd.read_feather(DATA_PATH)
df_1h['date'] = pd.to_datetime(df_1h['date'], utc=True)
df_1h = df_1h.sort_values('date').reset_index(drop=True)

# 过滤回测范围
mask = (df_1h['date'] >= '2022-01-01') & (df_1h['date'] <= '2026-06-02')
df_1h = df_1h[mask].reset_index(drop=True)
print(f"1H 数据：{len(df_1h)} 根 K 线，{df_1h['date'].min()} → {df_1h['date'].max()}")

# ── 运行 V8 指标 ─────────────────────────────────────────────
from freqtrade.data.converter import trim_dataframes
from user_data.strategies.WyckoffV8 import WyckoffV8

strategy = WyckoffV8({'runmode': 'backtest'})
strategy.dp = None

# 加载 1D 数据
DATA_PATH_1D = Path('/freqtrade/user_data/data/binance/futures/BTC_USDT_USDT-1d.feather')
df_1d = pd.read_feather(DATA_PATH_1D)
df_1d['date'] = pd.to_datetime(df_1d['date'], utc=True)
df_1d = df_1d.sort_values('date').reset_index(drop=True)

# 通过 strategy.analyze_pair 运行指标（最干净的路径）
# 改为直接调用 populate_indicators
strategy._last_candle_seen_per_pair = {}

# 注入 informative data
strategy.dp = type('DP', (), {
    'get_pair_dataframe': lambda self, pair, tf: df_1d.copy(),
    'runmode': type('RM', (), {'value': 'backtest'})()
})()

df_analyzed = strategy.populate_indicators(df_1h.copy(), {'pair': 'BTC/USDT:USDT'})
df_analyzed = strategy.populate_entry_trend(df_analyzed, {'pair': 'BTC/USDT:USDT'})

print(f"指标计算完成，列数：{len(df_analyzed.columns)}")
print(f"enter_long=1 信号数：{df_analyzed['enter_long'].sum()}")

# ── 提取入场点 + 追溯最近 wave_start ─────────────────────────
entry_rows = df_analyzed[df_analyzed['enter_long'] == 1].copy()
print(f"\n多头入场信号：{len(entry_rows)} 笔")

records = []
ws_indices = df_analyzed.index[
    df_analyzed['is_wave_start'].fillna(False).astype(bool) &
    (df_analyzed['wave_direction'] == 'up')
].tolist()

for idx, row in entry_rows.iterrows():
    # 找该 entry bar 之前最近的 up wave_start
    prior_ws = [i for i in ws_indices if i < idx]
    if not prior_ws:
        continue
    ws_idx = prior_ws[-1]
    ws_row = df_analyzed.loc[ws_idx]

    delay_bars  = idx - ws_idx
    delay_hours = delay_bars  # 1H 数据，1 bar = 1 hour
    ws_price    = ws_row['close']
    entry_price = row['close']
    price_gap_pct = (entry_price - ws_price) / ws_price * 100  # 正 = 入场时已涨
    phase = str(row.get('v8_phase', 'unknown'))

    records.append({
        'entry_date':    row['date'],
        'phase':         phase,
        'ws_date':       ws_row['date'],
        'delay_hours':   delay_hours,
        'ws_price':      ws_price,
        'entry_price':   entry_price,
        'price_gap_pct': price_gap_pct,
        'wave_disp':     ws_row.get('wave_displacement', np.nan),
    })

df_audit = pd.DataFrame(records)
print(f"\n有效审计样本：{len(df_audit)} 笔\n")

# ── Phase 分组统计 ────────────────────────────────────────────
print("=" * 60)
print("Phase D vs Phase E 确认税分布")
print("=" * 60)
for phase in ['D', 'E', 'unknown']:
    sub = df_audit[df_audit['phase'] == phase]
    if len(sub) == 0:
        continue
    print(f"\nPhase {phase}  ({len(sub)} 笔)")
    print(f"  延迟（小时）  中位={sub['delay_hours'].median():.0f}h  "
          f"均值={sub['delay_hours'].mean():.1f}h  "
          f"P25={sub['delay_hours'].quantile(0.25):.0f}h  "
          f"P75={sub['delay_hours'].quantile(0.75):.0f}h  "
          f"最大={sub['delay_hours'].max():.0f}h")
    print(f"  入场时已涨   中位={sub['price_gap_pct'].median():.2f}%  "
          f"均值={sub['price_gap_pct'].mean():.2f}%  "
          f"P75={sub['price_gap_pct'].quantile(0.75):.2f}%  "
          f"最大={sub['price_gap_pct'].max():.2f}%")
    # 延迟分布桶
    buckets = [0, 12, 24, 48, 72, 120, 9999]
    labels  = ['0-12h', '12-24h', '24-48h', '48-72h', '72-120h', '>120h']
    counts = pd.cut(sub['delay_hours'], bins=buckets, labels=labels).value_counts().sort_index()
    print(f"  延迟分布:  " + "  ".join(f"{l}:{c}" for l, c in zip(labels, counts)))

print("\n" + "=" * 60)
print("price_gap > 3%（错过前段超过3%）的入场")
print("=" * 60)
missed = df_audit[df_audit['price_gap_pct'] > 3.0].sort_values('price_gap_pct', ascending=False)
print(f"共 {len(missed)} 笔 (占 {len(missed)/len(df_audit)*100:.1f}%)")
if len(missed) > 0:
    print(missed[['entry_date','phase','delay_hours','price_gap_pct']].head(15).to_string(index=False))

print("\n" + "=" * 60)
print("延迟 > 72h 的入场（长尾延迟）")
print("=" * 60)
long_delay = df_audit[df_audit['delay_hours'] > 72].sort_values('delay_hours', ascending=False)
print(f"共 {len(long_delay)} 笔 (占 {len(long_delay)/len(df_audit)*100:.1f}%)")
if len(long_delay) > 0:
    print(long_delay[['entry_date','phase','delay_hours','price_gap_pct']].head(15).to_string(index=False))
