#!/usr/bin/env python3
"""
TASK 2 验证：Phase A-E 状态机 + SC 检出联调
"""
import sys
sys.path.insert(0, 'e:/freqtrade/user_data/strategies')
import pandas as pd
import numpy as np
from v8_modules.sc_detector import add_v8_sc_detection
from v8_modules.phase_detector import add_v8_phase_detection
from pathlib import Path

# ── 加载 ──
df = pd.read_feather('user_data/data/binance/BTC_USDT-1d.feather')
df['date'] = pd.to_datetime(df['date']).dt.tz_localize(None)
df = df.sort_values('date').reset_index(drop=True)
print(f"日线: {len(df)} 行, {df['date'].min().date()} ~ {df['date'].max().date()}")

# ── 运行 ──
df = add_v8_sc_detection(df)
df = add_v8_phase_detection(df)

# ── SC 列表 ──
sc_df = df[df['v8_sc_final']]
print(f"\n{'=' * 70}")
print(f"SC Final: {len(sc_df)} 次")
print(f"{'=' * 70}")
for _, r in sc_df.iterrows():
    print(f"  {str(r['date'].date())} | type={r['v8_sc_type']:<16} | low={r['v8_sc_low']:.0f}")

# ── Phase 分布 ──
print(f"\n{'=' * 70}")
print(f"Phase 分布")
print(f"{'=' * 70}")
dist = df['v8_phase'].value_counts()
for ph in ['A', 'B', 'C', 'D', 'E', 'unknown']:
    cnt = dist.get(ph, 0)
    pct = cnt / len(df) * 100
    bar = '█' * int(pct)
    print(f"  Phase {ph}: {cnt:>5} 天 ({pct:>5.1f}%) {bar}")

# ── Phase 时间线 ──
print(f"\n{'=' * 70}")
print(f"Phase 时间线（含 SC 事件）")
print(f"{'=' * 70}")

# 找 phase 变化的点
df['_phase_change'] = df['v8_phase'] != df['v8_phase'].shift(1)
changes = df[df['_phase_change'] | df['v8_sc_final']]

for _, r in changes.iterrows():
    sc_mark = ' ⭐ SC' if r['v8_sc_final'] else ''
    ar_mark = ' AR' if r['v8_ar_confirmed'] else ''
    tr_info = ''
    if not np.isnan(r['v8_tr_high']):
        tr_info = f" TR=[{r['v8_tr_low']:.0f}, {r['v8_tr_high']:.0f}]"
    print(f"  {str(r['date'].date())} → Phase {r['v8_phase']}{sc_mark}{ar_mark}{tr_info}")

# ── P0 锚点 Phase 分布 ──
print(f"\n{'=' * 70}")
print(f"P0 锚点附近的 Phase")
print(f"{'=' * 70}")

P0_DATES = {
    '2018-12 SC': ('2018-12-01', '2019-06-30'),
    '2020-03 SC': ('2020-03-01', '2020-10-31'),
    '2022-06 SC': ('2022-06-01', '2022-12-31'),
    '2022-11 SC': ('2022-11-01', '2023-05-31'),
}

for name, (d1, d2) in P0_DATES.items():
    mask = (df['date'] >= pd.Timestamp(d1)) & (df['date'] <= pd.Timestamp(d2))
    window = df[mask]
    phase_dist = window['v8_phase'].value_counts()
    allow_days = window['v8_phase_allow_long'].sum()
    print(f"\n  {name} ({d1}~{d2}):")
    print(f"    允许入场: {allow_days}/{len(window)} 天 ({allow_days/len(window)*100:.1f}%)")
    for ph in ['A', 'B', 'C', 'D', 'E']:
        print(f"    Phase {ph}: {phase_dist.get(ph, 0)} 天")

# ── Phase allow_long 全量 ──
total_allow = df['v8_phase_allow_long'].sum()
print(f"\n{'=' * 70}")
print(f"全量 Phase C/D/E 占比: {total_allow}/{len(df)} = {total_allow/len(df)*100:.1f}%")
print(f"  按 V8 Step 1 设计：只有这些天允许 V7 入场信号触发")
