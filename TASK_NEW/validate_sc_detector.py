#!/usr/bin/env python3
"""
TASK 1 验证脚本：运行 SC 检测器，验证 4 个 P0 锚点 + 误报率
"""
import sys
sys.path.insert(0, 'e:/freqtrade/user_data/strategies')
import pandas as pd
import numpy as np
from v8_modules.sc_detector import add_v8_sc_detection, DEFAULT_PARAMS
from pathlib import Path

# ── 加载数据 ──
data_path = Path('user_data/data/binance/BTC_USDT-1d.feather')
print(f"读取: {data_path}")
df = pd.read_feather(data_path)
df['date'] = pd.to_datetime(df['date']).dt.tz_localize(None)
df = df.drop_duplicates(subset=['date']).sort_values('date').reset_index(drop=True)
print(f"日线: {len(df)} 行, {df['date'].min().date()} ~ {df['date'].max().date()}")

# ── 运行检测 ──
df = add_v8_sc_detection(df)
print(f"SC final 检出: {df['v8_sc_final'].sum()} 次")

# ── P0 锚点验证 ──
P0_DATES = {
    '2018-12 SC': {'zone': ('2018-12-14', '2018-12-16'), 'confirm': '2018-12-17'},
    '2020-03 SC': {'zone': ('2020-03-12', '2020-03-12'), 'confirm': '2020-03-13'},
    '2022-06 SC': {'zone': ('2022-06-18', '2022-06-18'), 'confirm': '2022-06-19'},
    '2022-11 SC': {'zone': ('2022-11-09', '2022-11-09'), 'confirm': '2022-11-10'},
}

print(f"\n{'=' * 80}")
print(f"📌 P0 锚点验证")
print(f"{'=' * 80}")
print(f"{'锚点':<16} {'SC final?':>10} {'zone期间最高score':>18} {'确认K日期':>12}")
print('-' * 60)

passed = 0
for name, info in P0_DATES.items():
    zs, ze = pd.Timestamp(info['zone'][0]), pd.Timestamp(info['zone'][1])
    zc = pd.Timestamp(info['confirm'])

    zone = df[(df['date'] >= zs) & (df['date'] <= ze)]
    max_score = zone['v8_exhaustion_score'].max() if len(zone) > 0 else 0

    # 检查 confirm 日是否有 SC 确认
    confirm_row = df[df['date'] == zc]
    has_sc = bool(confirm_row['v8_sc_confirmed'].any()) if len(confirm_row) > 0 else False

    # 或检查 confirm 前后是否有 SC final
    nearby = df[(df['date'] >= zs - pd.Timedelta(days=2)) &
                (df['date'] <= zc + pd.Timedelta(days=2))]
    has_final = nearby['v8_sc_final'].any() if len(nearby) > 0 else False

    marker = '✅' if (has_sc or has_final) else '❌'
    if has_sc or has_final:
        passed += 1

    print(f"  {name:<14} {'是' if (has_sc or has_final) else '否':>10} "
          f"{max_score:>18.3f} {str(zc.date()):>12} {marker}")

# ── SC 检出列表 ──
print(f"\n{'=' * 80}")
print(f"📊 SC 检出列表")
print(f"{'=' * 80}")
sc_df = df[df['v8_sc_final']]
print(f"共 {len(sc_df)} 次 SC final:")
for _, r in sc_df.iterrows():
    print(f"  {str(r['date'].date())} | type={r['v8_sc_type']:<16} | "
          f"score={r['v8_exhaustion_score']:.3f} | low={r['v8_sc_low']:.0f}")

# ── 误报率 ──
print(f"\n{'=' * 80}")
print(f"📊 误报率")
print(f"{'=' * 80}")

years = (df['date'].max() - df['date'].min()).days / 365.25
sc_count = int(df['v8_sc_final'].sum())
annual = sc_count / years

print(f"  覆盖: {years:.1f} 年")
print(f"  SC final 总数: {sc_count}")
print(f"  年均: {annual:.1f} 次/年")
print(f"  目标: 1~3 次/年")
print(f"  P0 检出: {passed}/4")

# ── 结论 ──
print(f"\n{'=' * 80}")
print(f"🔑 结论")
print(f"{'=' * 80}")
if passed == 4 and 1 <= annual <= 3:
    print(f"  🟢 完美：4/4 检出，年均 {annual:.1f} 次在目标范围内")
elif passed == 4:
    print(f"  🟡 P0 全检出但年均 {annual:.1f} 次，需调 threshold")
elif passed >= 2:
    print(f"  🟡 {passed}/4 检出，需调参")
else:
    print(f"  🔴 检出不足，需重新设计评分模型")
