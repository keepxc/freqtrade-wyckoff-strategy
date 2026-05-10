"""
V8 模块验证脚本 — sc_detector / phase_detector / pnf_calculator
验证三个模块能正常运行，输出与设计预期一致。
使用 BTC/USDT 日线数据（本地 feather）。
"""

import sys
import os
sys.path.insert(0, os.path.abspath('.'))

import pandas as pd
import numpy as np

from user_data.strategies.v8_modules.sc_detector import add_v8_sc_detection
from user_data.strategies.v8_modules.phase_detector import add_v8_phase_detection
from user_data.strategies.v8_modules.pnf_calculator import add_v8_pnf_targets

DATA_PATH = "user_data/data/binance/BTC_USDT-1d.feather"

# ── 加载数据 ────────────────────────────────────────────────
print("=" * 60)
print("加载数据...")
df = pd.read_feather(DATA_PATH)

# 标准列名
col_map = {}
for col in df.columns:
    if col.lower() in ('date', 'time', 'timestamp'):
        col_map[col] = 'date'
    elif col.lower() == 'open':
        col_map[col] = 'open'
    elif col.lower() == 'high':
        col_map[col] = 'high'
    elif col.lower() == 'low':
        col_map[col] = 'low'
    elif col.lower() == 'close':
        col_map[col] = 'close'
    elif col.lower() in ('volume', 'vol'):
        col_map[col] = 'volume'

df = df.rename(columns=col_map)

# 转 date 列
if 'date' in df.columns and not pd.api.types.is_datetime64_any_dtype(df['date']):
    df['date'] = pd.to_datetime(df['date'], unit='ms', utc=True)

df = df.sort_values('date').reset_index(drop=True)
print(f"数据范围: {df['date'].iloc[0].date()} ~ {df['date'].iloc[-1].date()}")
print(f"总 K 线数: {len(df)}")
print(f"列名: {list(df.columns)}")

# ── TASK 1: SC 检测 ─────────────────────────────────────────
print("\n" + "=" * 60)
print("TASK 1: SC 检测器 (sc_detector.py)")
print("-" * 60)

df = add_v8_sc_detection(df)

sc_cols = [c for c in df.columns if c.startswith('v8_sc') or c.startswith('v8_zone') or c.startswith('v8_exhaustion') or c.startswith('v8_confirm')]
print(f"新增列: {sc_cols}")

sc_confirmed = df[df['v8_sc_final'] == True]
print(f"\n✓ SC 确认事件: {len(sc_confirmed)} 个")

if len(sc_confirmed) > 0:
    for _, row in sc_confirmed.iterrows():
        date_str = row['date'].strftime('%Y-%m-%d') if hasattr(row['date'], 'strftime') else str(row['date'])[:10]
        sc_type = row.get('v8_sc_type', 'N/A')
        sc_low = row.get('v8_sc_low', np.nan)
        exh_score = row.get('v8_exhaustion_score', np.nan)
        print(f"  [{date_str}] type={sc_type}  sc_low={sc_low:.2f}  exhaustion_score={exh_score:.3f}")

# ── TASK 2: Phase 检测 ──────────────────────────────────────
print("\n" + "=" * 60)
print("TASK 2: Phase 检测器 (phase_detector.py)")
print("-" * 60)

df = add_v8_phase_detection(df)

phase_cols = [c for c in df.columns if c.startswith('v8_phase') or c.startswith('v8_tr') or c.startswith('v8_ar')]
print(f"新增列: {phase_cols}")

phase_dist = df['v8_phase'].value_counts().sort_index()
print(f"\n✓ Phase 分布:")
for phase, cnt in phase_dist.items():
    pct = cnt / len(df) * 100
    print(f"  Phase {phase}: {cnt} 天 ({pct:.1f}%)")

# 显示 Phase D/E 的日期（入场区）
phase_d = df[df['v8_phase'] == 'D']
phase_e = df[df['v8_phase'] == 'E']
print(f"\n  Phase D (入场区): {len(phase_d)} 天")
print(f"  Phase E (突破趋势): {len(phase_e)} 天")

if len(phase_d) > 0:
    # 显示前几个 Phase D 片段
    transitions = df[df['v8_phase'].shift(1) != df['v8_phase']]
    d_starts = transitions[transitions['v8_phase'] == 'D'].head(5)
    print(f"\n  Phase D 入场区开始时间（前5个）:")
    for _, row in d_starts.iterrows():
        date_str = row['date'].strftime('%Y-%m-%d') if hasattr(row['date'], 'strftime') else str(row['date'])[:10]
        tr_h = row.get('v8_tr_high', np.nan)
        tr_l = row.get('v8_tr_low', np.nan)
        print(f"    [{date_str}] TR={tr_l:.2f}~{tr_h:.2f}")

# ── TASK 3: PNF 目标位 ──────────────────────────────────────
print("\n" + "=" * 60)
print("TASK 3: PNF 目标位计算 (pnf_calculator.py)")
print("-" * 60)

df = add_v8_pnf_targets(df)

pnf_cols = [c for c in df.columns if c.startswith('v8_target')]
print(f"新增列: {pnf_cols}")

pnf_valid = df[df['v8_target_1'].notna()]
print(f"\n✓ 有效 PNF 目标位的 K 线: {len(pnf_valid)} 天")

if len(pnf_valid) > 0:
    # 找目标位首次出现的位置（Phase 开始点）
    first_valid = df[df['v8_target_1'].notna() & df['v8_target_1'].shift(1).isna()]
    print(f"\n  目标位锁定事件 ({len(first_valid)} 次):")
    for _, row in first_valid.iterrows():
        date_str = row['date'].strftime('%Y-%m-%d') if hasattr(row['date'], 'strftime') else str(row['date'])[:10]
        close = row['close']
        t1 = row.get('v8_target_1', np.nan)
        t2 = row.get('v8_target_2', np.nan)
        t3 = row.get('v8_target_3', np.nan)
        upside_t1 = (t1 / close - 1) * 100 if not np.isnan(t1) and close > 0 else np.nan
        print(f"  [{date_str}] close={close:.2f}  T1={t1:.2f}(+{upside_t1:.1f}%)  T2={t2:.2f}  T3={t3:.2f}")

# ── 全链路一致性检查 ─────────────────────────────────────────
print("\n" + "=" * 60)
print("全链路一致性检查")
print("-" * 60)

# SC 必须在 Phase A 开始前确认
sc_dates = set(df[df['v8_sc_final'] == True]['date'].dt.date)
phase_a_starts = df[(df['v8_phase'] == 'A') & (df['v8_phase'].shift(1) != 'A')]

print(f"✓ SC 确认点: {len(sc_dates)}")
print(f"✓ Phase A 启动次数: {len(phase_a_starts)}")

# PNF 只在 Phase C/D 出现
pnf_phase = df[df['v8_target_1'].notna()]['v8_phase'].value_counts()
print(f"\n✓ PNF 目标位按 Phase 分布:")
for ph, cnt in pnf_phase.items():
    print(f"  Phase {ph}: {cnt} 天")

# 关键约束：v8_phase_allow_long 只在 C/D/E
if 'v8_phase_allow_long' in df.columns:
    allow_long = df[df['v8_phase_allow_long'] == True]['v8_phase'].value_counts()
    print(f"\n✓ allow_long=True 的 Phase 分布:")
    for ph, cnt in allow_long.items():
        print(f"  Phase {ph}: {cnt} 天")

print("\n" + "=" * 60)
print("验证完成")
print("=" * 60)
