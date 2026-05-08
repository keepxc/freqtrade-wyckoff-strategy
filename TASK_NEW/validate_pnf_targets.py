#!/usr/bin/env python3
"""TASK 3 验证：P&F 目标位 + 全链路联调"""
import sys
sys.path.insert(0, 'e:/freqtrade/user_data/strategies')
import pandas as pd
import numpy as np
from v8_modules.sc_detector import add_v8_sc_detection
from v8_modules.phase_detector import add_v8_phase_detection
from v8_modules.pnf_calculator import add_v8_pnf_targets
from pathlib import Path

df = pd.read_feather('user_data/data/binance/BTC_USDT-1d.feather')
df['date'] = pd.to_datetime(df['date']).dt.tz_localize(None)
df = df.sort_values('date').reset_index(drop=True)

df = add_v8_sc_detection(df)
df = add_v8_phase_detection(df)
df = add_v8_pnf_targets(df)

# ── 目标位锁定事件 ──
lock_events = df[df['v8_target_locked'] & ~df['v8_target_locked'].shift(1).fillna(False)]
print(f"目标位锁定事件: {len(lock_events)} 次\n")
print(f"{'日期':>12} {'Phase':>6} {'TR Low':>10} {'TR High':>10} {'target_1':>10} {'target_2':>10} {'target_3':>10}")
print('-' * 72)
for _, r in lock_events.iterrows():
    print(f"{str(r['date'].date()):>12} {r['v8_phase']:>6} {r['v8_tr_low']:>10.0f} "
          f"{r['v8_tr_high']:>10.0f} {r['v8_target_1']:>10.0f} "
          f"{r['v8_target_2']:>10.0f} {r['v8_target_3']:>10.0f}")

# ── 目标锁定天数 ──
locked_days = df['v8_target_locked'].sum()
print(f"\n目标锁定天数: {locked_days}/{len(df)} ({locked_days/len(df)*100:.1f}%)")

# ── Phase 分布（修复后）──
print(f"\nPhase 分布:")
for ph in ['A', 'B', 'C', 'D', 'E', 'unknown']:
    cnt = (df['v8_phase'] == ph).sum()
    print(f"  {ph}: {cnt} ({cnt/len(df)*100:.1f}%)")
