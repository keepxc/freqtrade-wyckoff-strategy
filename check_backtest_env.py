#!/usr/bin/env python3
"""检查回测时的环境变量设置"""

import json
import os

# 读取两次回测的元数据
with open('user_data/backtest_results/backtest-result-2026-05-03_04-49-10.meta.json') as f:
    meta1 = json.load(f)
    
with open('user_data/backtest_results/backtest-result-2026-05-03_04-54-03.meta.json') as f:
    meta2 = json.load(f)

print("=" * 80)
print("回测环境变量检查")
print("=" * 80)

print("\n【回测1: 04-49-10】")
print(f"Run ID: {meta1['WyckoffV7_TrueMethod']['run_id']}")
print(f"开始时间: {meta1['WyckoffV7_TrueMethod']['backtest_start_time']}")

print("\n【回测2: 04-54-03】")
print(f"Run ID: {meta2['WyckoffV7_TrueMethod']['run_id']}")
print(f"开始时间: {meta2['WyckoffV7_TrueMethod']['backtest_start_time']}")

print("\n【当前环境变量】")
print(f"WY_PNF_SHADOW_PROTECT: {os.getenv('WY_PNF_SHADOW_PROTECT', '未设置')}")

print("\n【策略代码中的默认值】")
print("默认值: '1' (启用)")
print("读取方式: os.getenv('WY_PNF_SHADOW_PROTECT', '1') == '1'")

print("\n【分析】")
print("如果两次回测结果完全相同，可能的原因：")
print("1. 两次回测都使用了相同的环境变量值")
print("2. 环境变量没有正确传递到 Docker 容器中")
print("3. 功能代码没有被触发（例如：没有交易达到 PNF 警告区）")

# 读取交易数据，检查是否有 PNF 相关的退出
with open('user_data/backtest_results/temp_04-49-10/backtest-result-2026-05-03_04-49-10.json') as f:
    data1 = json.load(f)
    
with open('user_data/backtest_results/temp_04-54-03/backtest-result-2026-05-03_04-54-03.json') as f:
    data2 = json.load(f)

import pandas as pd
df1 = pd.DataFrame(data1['strategy']['WyckoffV7_TrueMethod']['trades'])
df2 = pd.DataFrame(data2['strategy']['WyckoffV7_TrueMethod']['trades'])

print("\n【退出原因检查】")
print("\n回测1 退出原因:")
for reason, count in df1['exit_reason'].value_counts().items():
    if 'pnf' in reason.lower() or 'shadow' in reason.lower() or 'timeout' in reason.lower():
        print(f"  ⚠️  {reason}: {count}")
    else:
        print(f"  {reason}: {count}")

print("\n回测2 退出原因:")
for reason, count in df2['exit_reason'].value_counts().items():
    if 'pnf' in reason.lower() or 'shadow' in reason.lower() or 'timeout' in reason.lower():
        print(f"  ⚠️  {reason}: {count}")
    else:
        print(f"  {reason}: {count}")

# 检查是否有新的退出类型
new_exits = set(df2['exit_reason'].unique()) - set(df1['exit_reason'].unique())
if new_exits:
    print(f"\n✓ 回测2 中出现了新的退出类型: {new_exits}")
else:
    print("\n⚠️  回测2 中没有出现新的退出类型")
    print("   这意味着 PNF Shadow Protect 功能可能没有被触发")
