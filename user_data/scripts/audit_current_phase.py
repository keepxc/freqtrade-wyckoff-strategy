"""
审计当前 BTC 的 Phase 状态：
1. SC 是否在 59k 附近被识别
2. 当前 Phase 是什么（A/B/C/D/E）
3. TR 边界值（High/Low/Mid）
4. 距离开仓还差什么条件
"""
import sys
sys.path.insert(0, '/freqtrade')

import pandas as pd
import numpy as np
from pathlib import Path
from user_data.strategies.v8_modules.sc_detector import add_v8_sc_detection
from user_data.strategies.v8_modules.phase_detector import add_v8_phase_detection

DATA_PATH = Path('/freqtrade/user_data/data/binance/futures')
pair_file = DATA_PATH / 'BTC_USDT_USDT-1d-futures.feather'
df = pd.read_feather(pair_file)
df = df[df['date'] >= '2025-01-01'].copy().reset_index(drop=True)
print(f'日线数据: {len(df)} 根 ({df["date"].iloc[0].date()} ~ {df["date"].iloc[-1].date()})')

df = add_v8_sc_detection(df)
df = add_v8_phase_detection(df)

# 最近 30 天的状态
recent = df.tail(30).copy()
print(f'\n=== 最近 30 天 Phase 状态 ===')
print(f'{"日期":<12} {"Phase":>6} {"close":>10} {"TR_High":>10} {"TR_Low":>10} {"TR_Mid":>10} {"SC":>5} {"AR":>5}')
print('-'*75)
for _, row in recent.iterrows():
    sc_mark = '★' if row.get('v8_sc_final', False) else ''
    ar_mark = '◆' if row.get('v8_ar_confirmed', False) else ''
    phase = row.get('v8_phase', '?')
    tr_h = row.get('v8_tr_high', np.nan)
    tr_l = row.get('v8_tr_low', np.nan)
    tr_m = (tr_h + tr_l) / 2 if not (np.isnan(tr_h) or np.isnan(tr_l)) else np.nan
    print(f'{str(row["date"].date()):<12} {phase:>6} {row["close"]:>10,.0f} '
          f'{tr_h:>10,.0f} {tr_l:>10,.0f} {tr_m:>10,.0f} {sc_mark:>5} {ar_mark:>5}')

# 最新状态摘要
last = df.iloc[-1]
tr_h = last.get('v8_tr_high', np.nan)
tr_l = last.get('v8_tr_low', np.nan)
tr_m = (tr_h + tr_l) / 2 if not (np.isnan(tr_h) or np.isnan(tr_l)) else np.nan

print(f'\n=== 当前状态（{last["date"].date()}）===')
print(f'Phase: {last["v8_phase"]}')
print(f'close: {last["close"]:,.0f}')
print(f'TR High: {tr_h:,.0f}' if not np.isnan(tr_h) else 'TR High: N/A')
print(f'TR Low:  {tr_l:,.0f}' if not np.isnan(tr_l) else 'TR Low:  N/A')
print(f'TR Mid:  {tr_m:,.0f}' if not np.isnan(tr_m) else 'TR Mid:  N/A')
print(f'close > TR Mid: {last["close"] > tr_m if not np.isnan(tr_m) else "N/A"}')
print(f'close > TR High: {last["close"] > tr_h if not np.isnan(tr_h) else "N/A"}')

# 找最近一次 SC
sc_events = df[df['v8_sc_final'] == True]
if not sc_events.empty:
    last_sc = sc_events.iloc[-1]
    print(f'\n最近一次 SC: {last_sc["date"].date()}  低点: {last_sc["v8_sc_low"]:,.0f}')
    days_since_sc = (df.iloc[-1]['date'] - last_sc['date']).days
    print(f'距今: {days_since_sc} 天')

# 找 Phase B 开始时间
phase_b_rows = df[df['v8_phase'] == 'B']
if not phase_b_rows.empty:
    # 找最近连续的 Phase B 开始
    b_idx = phase_b_rows.index[-1]
    while b_idx > 0 and df.iloc[b_idx-1]['v8_phase'] == 'B':
        b_idx -= 1
    b_start = df.iloc[b_idx]
    days_in_b = (df.iloc[-1]['date'] - b_start['date']).days
    print(f'\nPhase B 开始: {b_start["date"].date()}  已等待: {days_in_b} 天')
    print(f'phase_b_min_days=20，还需等: {max(0, 20 - days_in_b)} 天才能升级')
