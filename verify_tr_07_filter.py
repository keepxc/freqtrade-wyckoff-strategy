#!/usr/bin/env python3
"""
验证 TR 0.7 过滤逻辑是否正确实施

测试点：
1. TR position < 0.7 → 目标位正常计算
2. TR position >= 0.7 → 目标位设为 0（阻止入场）
3. 日志输出过滤统计
"""

import pandas as pd
import numpy as np
from wyckoff.pnf_target_calculator import PNFTargetCalculator


def test_tr_07_filter():
    """测试 TR 0.7 过滤逻辑"""
    
    # 创建测试数据
    bars = 10
    dataframe = pd.DataFrame({
        'close': [100, 105, 110, 115, 120, 125, 130, 135, 140, 145],
        'high': [102, 107, 112, 117, 122, 127, 132, 137, 142, 147],
        'low': [98, 103, 108, 113, 118, 123, 128, 133, 138, 143],
        'climatic_type': ['none'] * bars,
        'is_SOS_bar': [False] * bars,
        'utad_detected': [False] * bars,
        'sow_bar_detected': [False] * bars,
    })
    
    # 添加 ATR
    dataframe['atr_20'] = 5.0
    dataframe['atr'] = 5.0
    
    # 创建计算器
    calculator = PNFTargetCalculator()
    
    # 计算目标位
    result = calculator.calculate_targets(dataframe)
    
    # 验证结果
    print("=" * 80)
    print("TR 0.7 过滤验证")
    print("=" * 80)
    
    # 显示关键列
    display_cols = ['close', 'tr_low', 'tr_high', 'tr_position', 
                    'target_conservative', 'target_standard']
    
    print("\n数据样本：")
    print(result[display_cols].to_string())
    
    # 统计过滤情况
    total_bars = len(result)
    filtered_bars = (result['tr_position'] >= 0.7).sum()
    zero_target_bars = (result['target_conservative'] == 0).sum()
    
    print(f"\n统计结果：")
    print(f"  总 K 线数: {total_bars}")
    print(f"  TR >= 0.7 的 K 线数: {filtered_bars}")
    print(f"  目标位为 0 的 K 线数: {zero_target_bars}")
    
    # 验证逻辑
    print(f"\n验证结果：")
    
    # 检查 TR >= 0.7 的 K 线是否目标位为 0
    tr_high_mask = result['tr_position'] >= 0.7
    targets_zero = (result.loc[tr_high_mask, 'target_conservative'] == 0).all()
    
    if targets_zero:
        print("  ✅ TR >= 0.7 的 K 线目标位全部为 0（过滤生效）")
    else:
        print("  ❌ TR >= 0.7 的 K 线目标位未全部为 0（过滤失败）")
        print(f"     问题 K 线：")
        problem_bars = result.loc[tr_high_mask & (result['target_conservative'] > 0)]
        print(problem_bars[display_cols].to_string())
    
    # 检查 TR < 0.7 的 K 线是否目标位正常
    tr_low_mask = result['tr_position'] < 0.7
    targets_positive = (result.loc[tr_low_mask, 'target_conservative'] > 0).any()
    
    if targets_positive:
        print("  ✅ TR < 0.7 的 K 线有正常目标位（未被误杀）")
    else:
        print("  ⚠️  TR < 0.7 的 K 线目标位全部为 0（可能是数据问题）")
    
    print("\n" + "=" * 80)
    
    return targets_zero


if __name__ == '__main__':
    success = test_tr_07_filter()
    exit(0 if success else 1)
