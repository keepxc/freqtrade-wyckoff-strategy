#!/usr/bin/env python3
"""检查回测数据中的 PNF 目标值"""

import json
import pandas as pd

# 读取最近的回测数据
with open('user_data/backtest_results/temp_04-54-03/backtest-result-2026-05-03_04-54-03.json') as f:
    data = json.load(f)

df = pd.DataFrame(data['strategy']['WyckoffV7_TrueMethod']['trades'])

print("=" * 80)
print("PNF 目标值检查")
print("=" * 80)

# 检查是否有 PNF 相关字段
print("\n【DataFrame 列名】")
pnf_columns = [col for col in df.columns if 'target' in col.lower() or 'pnf' in col.lower()]
if pnf_columns:
    print(f"找到 PNF 相关列: {pnf_columns}")
else:
    print("⚠️  没有找到 PNF 相关列")

print(f"\n所有列: {list(df.columns)}")

# 检查 entry_tag 分布
print("\n【Entry Tag 分布】")
print(df['enter_tag'].value_counts())

# 检查符合 PNF Shadow Protect 条件的 entry_tag
eligible_tags = ['wyckoff_axis_support', 'wyckoff_sos']
eligible_trades = df[df['enter_tag'].isin(eligible_tags)]

print(f"\n【符合条件的交易】")
print(f"符合 entry_tag 的交易数: {len(eligible_trades)} / {len(df)}")
print(f"符合条件的 entry_tag: {eligible_tags}")

if len(eligible_trades) > 0:
    print(f"\n前5笔符合条件的交易:")
    print(eligible_trades[['pair', 'enter_tag', 'open_date', 'close_date', 'exit_reason', 'profit_abs']].head())
