"""
空头门控漏斗审计脚本
统计 2022-01-01 至今，每一层空头条件的过滤情况

运行方式：
  docker compose run --rm --entrypoint "" freqtrade \
    sh -c "cd /freqtrade && python scripts/audit_short_funnel.py"
"""

import sys
import os
sys.path.insert(0, '/freqtrade')

import pandas as pd
import numpy as np
from wyckoff.daily_event_detector import DailyEventDetector

# ─── 加载数据 ────────────────────────────────────────────────────────────────
DATA_PATH = 'user_data/data/binance/futures/BTC_USDT_USDT-1d-futures.feather'
df_raw = pd.read_feather(DATA_PATH)
df_raw['date'] = pd.to_datetime(df_raw['date'])
df_raw = df_raw.sort_values('date').reset_index(drop=True)

# 过滤范围（含预热期）
df_raw = df_raw[df_raw['date'] >= '2021-01-01'].reset_index(drop=True)

print(f"数据范围: {df_raw['date'].iloc[0].date()} ~ {df_raw['date'].iloc[-1].date()}")
print(f"总K线数: {len(df_raw)}")
print()

# ─── 运行 DailyEventDetector ────────────────────────────────────────────────
detector = DailyEventDetector()
df = detector.detect(df_raw.copy())

# 只分析 2022-01-01 之后（排除预热期）
df_analysis = df[df['date'] >= '2022-01-01'].copy()
total_bars = len(df_analysis)

print("=" * 65)
print(f"分析区间: 2022-01-01 ~ {df_analysis['date'].iloc[-1].date()}")
print(f"分析K线数: {total_bars} 根日线")
print("=" * 65)

# ─── 漏斗统计 ────────────────────────────────────────────────────────────────

def pct(n, total):
    return f"{n:4d}  ({n/total*100:5.1f}%)"

print()
print("【1】BC 检测（派发因起点）")
bc_days = df_analysis['daily_bc_detected'].sum()
print(f"  daily_bc_detected = True : {pct(bc_days, total_bars)} 根日线")

# 按年分解 BC
print("  按年分布:")
for year in range(2022, 2027):
    mask = (df_analysis['date'].dt.year == year) & df_analysis['daily_bc_detected']
    cnt = mask.sum()
    bc_dates = df_analysis[mask]['date'].dt.date.tolist()
    print(f"    {year}: {cnt} 次  {bc_dates}")

print()
print("【2】Buying Exhaustion（BE，补充路径）")
be_days = df_analysis['buying_exhaustion'].sum()
print(f"  buying_exhaustion = True  : {pct(be_days, total_bars)} 根日线")
be_dates = df_analysis[df_analysis['buying_exhaustion']]['date'].dt.date.tolist()
print(f"  BE 触发日期: {be_dates}")

print()
print("【3】dist_cause_sufficient（DCS，分发因充分）")
dcs_days = df_analysis['dist_cause_sufficient'].sum()
print(f"  dist_cause_sufficient=True: {pct(dcs_days, total_bars)} 根日线")

# DCS 按年分解
print("  按年分布（DCS=True 的天数）:")
for year in range(2022, 2027):
    mask = (df_analysis['date'].dt.year == year) & df_analysis['dist_cause_sufficient']
    print(f"    {year}: {mask.sum()} 天")

print()
print("【4】派发序列进展（dist_sequence_progress）")
progress_counts = df_analysis['dist_sequence_progress'].value_counts()
for stage, cnt in progress_counts.items():
    print(f"  {stage:10s}: {pct(cnt, total_bars)} 根日线")

print()
print("【5】各空头信号触发（DCS=True 期间）")
df_dcs = df_analysis[df_analysis['dist_cause_sufficient']].copy()
if len(df_dcs) > 0:
    utad_cnt = df_dcs['daily_utad_detected'].sum() if 'daily_utad_detected' in df_dcs.columns else 0
    sow_cnt  = df_dcs['daily_dist_sow_detected'].sum() if 'daily_dist_sow_detected' in df_dcs.columns else 0
    be_cnt   = df_dcs['buying_exhaustion'].sum()
    print(f"  UTAD (DCS期间) : {utad_cnt}")
    print(f"  SOW  (DCS期间) : {sow_cnt}")
    print(f"  BE   (DCS期间) : {be_cnt}")
else:
    print("  DCS=True 期间无K线")

print()
print("【6】价格走势与 DCS 状态对照（关键年份）")
print()

# 2022年月度摘要
print("  2022年 月度价格 + DCS 状态:")
print(f"  {'月份':8s} {'开盘':>8s} {'收盘':>8s} {'最低':>8s} {'DCS天数':>8s} {'BC':>4s} {'BE':>4s}")
for month in range(1, 13):
    mask = (df_analysis['date'].dt.year == 2022) & (df_analysis['date'].dt.month == month)
    sub = df_analysis[mask]
    if len(sub) == 0:
        continue
    print(f"  2022-{month:02d}  {sub['open'].iloc[0]:>8.0f} {sub['close'].iloc[-1]:>8.0f} "
          f"{sub['low'].min():>8.0f} {sub['dist_cause_sufficient'].sum():>8d} "
          f"{sub['daily_bc_detected'].sum():>4d} {sub['buying_exhaustion'].sum():>4d}")

print()
print("  2025年 月度价格 + DCS 状态:")
print(f"  {'月份':8s} {'开盘':>8s} {'收盘':>8s} {'最低':>8s} {'DCS天数':>8s} {'BC':>4s} {'BE':>4s}")
for month in range(1, 13):
    mask = (df_analysis['date'].dt.year == 2025) & (df_analysis['date'].dt.month == month)
    sub = df_analysis[mask]
    if len(sub) == 0:
        continue
    print(f"  2025-{month:02d}  {sub['open'].iloc[0]:>8.0f} {sub['close'].iloc[-1]:>8.0f} "
          f"{sub['low'].min():>8.0f} {sub['dist_cause_sufficient'].sum():>8d} "
          f"{sub['daily_bc_detected'].sum():>4d} {sub['buying_exhaustion'].sum():>4d}")

print()
print("  2026年 月度价格 + DCS 状态:")
print(f"  {'月份':8s} {'开盘':>8s} {'收盘':>8s} {'最低':>8s} {'DCS天数':>8s} {'BC':>4s} {'BE':>4s}")
for month in range(1, 13):
    mask = (df_analysis['date'].dt.year == 2026) & (df_analysis['date'].dt.month == month)
    sub = df_analysis[mask]
    if len(sub) == 0:
        continue
    print(f"  2026-{month:02d}  {sub['open'].iloc[0]:>8.0f} {sub['close'].iloc[-1]:>8.0f} "
          f"{sub['low'].min():>8.0f} {sub['dist_cause_sufficient'].sum():>8d} "
          f"{sub['daily_bc_detected'].sum():>4d} {sub['buying_exhaustion'].sum():>4d}")

print()
print("=" * 65)
print("【结论】")
print(f"  全区间 ({total_bars} 根日线):")
print(f"    BC 触发    : {bc_days} 次")
print(f"    BE 触发    : {be_days} 次")
print(f"    DCS 有效天 : {dcs_days} 天 ({dcs_days/total_bars*100:.1f}%)")
print(f"  ⇒ 空头门控开启时间仅占全周期 {dcs_days/total_bars*100:.1f}%")
print(f"  ⇒ 这是设计意图：只在有分发因的窗口内做空")
print("=" * 65)
