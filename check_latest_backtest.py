#!/usr/bin/env python3
"""检查最新回测结果的退出原因"""

import json
import pandas as pd

# 读取最新回测数据
with open('user_data/backtest_results/temp_05-43-56/backtest-result-2026-05-03_05-43-56.json') as f:
    data = json.load(f)

df = pd.DataFrame(data['strategy']['WyckoffV7_TrueMethod']['trades'])

print("=" * 80)
print("最新回测结果分析（WY_PNF_SHADOW_PROTECT=1）")
print("=" * 80)

print(f"\n总交易数: {len(df)}")
print(f"总利润: {df['profit_abs'].sum():.2f} USDT")
print(f"胜率: {(df['profit_abs'] > 0).sum() / len(df) * 100:.1f}%")

print("\n【退出原因分布】")
exit_reasons = df['exit_reason'].value_counts()
for reason, count in exit_reasons.items():
    if 'pnf' in reason.lower() or 'shadow' in reason.lower():
        print(f"  ⭐ {reason}: {count}")
    else:
        print(f"  {reason}: {count}")

print("\n【Entry Tag 分布】")
entry_tags = df['enter_tag'].value_counts()
for tag, count in entry_tags.items():
    if tag in ['wyckoff_axis_support', 'wyckoff_sos_entry']:
        print(f"  ✓ {tag}: {count} (符合 PNF Shadow Protect 条件)")
    else:
        print(f"  {tag}: {count}")

# 检查是否有 PNF 相关的新退出类型
pnf_exits = [r for r in exit_reasons.index if 'pnf' in r.lower() or 'shadow' in r.lower() or 'timeout' in r.lower()]
if pnf_exits:
    print(f"\n✓ 发现 PNF 相关退出类型: {pnf_exits}")
else:
    print("\n⚠️  没有发现 PNF 相关的新退出类型")
