#!/usr/bin/env python3
"""比较两次回测结果"""

import pandas as pd
import json

# 读取两次回测数据
with open('user_data/backtest_results/temp_05-30-48/backtest-result-2026-05-03_05-30-48.json') as f:
    data1 = json.load(f)
    
with open('user_data/backtest_results/temp_05-35-02/backtest-result-2026-05-03_05-35-02.json') as f:
    data2 = json.load(f)

# 提取策略数据
strategy_name = 'WyckoffV7_TrueMethod'
df1 = pd.DataFrame(data1['strategy'][strategy_name]['trades'])
df2 = pd.DataFrame(data2['strategy'][strategy_name]['trades'])

print("=" * 80)
print("回测结果对比分析")
print("=" * 80)

print("\n【回测1: 04-49-10】")
print(f"总交易数: {len(df1)}")
print(f"总利润: {df1['profit_abs'].sum():.2f} USDT")
print(f"胜率: {(df1['profit_abs'] > 0).sum() / len(df1) * 100:.1f}%")
print(f"平均利润: {df1['profit_abs'].mean():.2f} USDT")

print("\n【回测2: 04-54-03】")
print(f"总交易数: {len(df2)}")
print(f"总利润: {df2['profit_abs'].sum():.2f} USDT")
print(f"胜率: {(df2['profit_abs'] > 0).sum() / len(df2) * 100:.1f}%")
print(f"平均利润: {df2['profit_abs'].mean():.2f} USDT")

print("\n【差异分析】")
print(f"交易数差异: {len(df2) - len(df1)}")
print(f"利润差异: {df2['profit_abs'].sum() - df1['profit_abs'].sum():.2f} USDT")

# 检查 exit_reason 分布
print("\n【回测1 退出原因分布】")
print(df1['exit_reason'].value_counts())

print("\n【回测2 退出原因分布】")
print(df2['exit_reason'].value_counts())

# 检查是否有新的退出类型
new_exit_types = set(df2['exit_reason'].unique()) - set(df1['exit_reason'].unique())
if new_exit_types:
    print(f"\n【新增退出类型】: {new_exit_types}")
    for exit_type in new_exit_types:
        count = (df2['exit_reason'] == exit_type).sum()
        print(f"  {exit_type}: {count}次")
else:
    print("\n【新增退出类型】: 无")

# 检查 entry_tag 分布
print("\n【回测1 入场标签分布】")
print(df1['enter_tag'].value_counts())

print("\n【回测2 入场标签分布】")
print(df2['enter_tag'].value_counts())

# 检查是否完全相同
if df1.equals(df2):
    print("\n⚠️  两次回测结果完全相同！")
else:
    print("\n✓ 两次回测结果有差异")
    
    # 找出不同的交易
    if len(df1) == len(df2):
        # 比较每一行
        diff_mask = (df1 != df2).any(axis=1)
        if diff_mask.any():
            print(f"\n有 {diff_mask.sum()} 笔交易存在差异")
            print("\n差异字段统计:")
            for col in df1.columns:
                if (df1[col] != df2[col]).any():
                    print(f"  - {col}: {(df1[col] != df2[col]).sum()} 笔不同")
