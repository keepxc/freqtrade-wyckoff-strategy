#!/usr/bin/env python3
"""
验证 P&F 目标位修复是否生效

检查策略代码中是否包含必要的验证逻辑
"""

import re

def check_populate_entry_trend():
    """检查 populate_entry_trend 中是否有 P&F 验证逻辑"""
    with open('user_data/strategies/WyckoffV7_TrueMethod.py', 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 检查多头验证
    long_validation_patterns = [
        r'pnf_target_valid_long',
        r'target_conservative.*> 0',
        r'P&F 目标位验证.*多头信号被阻止',
    ]
    
    # 检查空头验证
    short_validation_patterns = [
        r'pnf_target_valid_short',
        r'target_short_conservative.*> 0',
        r'P&F 目标位验证.*空头信号被阻止',
    ]
    
    print("=" * 60)
    print("P&F 目标位修复验证")
    print("=" * 60)
    
    print("\n✅ 检查 populate_entry_trend 中的多头验证:")
    for pattern in long_validation_patterns:
        if re.search(pattern, content):
            print(f"  ✓ 找到: {pattern}")
        else:
            print(f"  ✗ 未找到: {pattern}")
    
    print("\n✅ 检查 populate_entry_trend 中的空头验证:")
    for pattern in short_validation_patterns:
        if re.search(pattern, content):
            print(f"  ✓ 找到: {pattern}")
        else:
            print(f"  ✗ 未找到: {pattern}")
    
    # 检查 final_entry 是否包含 pnf_target_valid_long
    final_entry_match = re.search(
        r'final_entry = \((.*?)\)',
        content,
        re.DOTALL
    )
    if final_entry_match:
        final_entry_code = final_entry_match.group(1)
        if 'pnf_target_valid_long' in final_entry_code:
            print("\n✅ final_entry 包含 pnf_target_valid_long 验证")
        else:
            print("\n❌ final_entry 不包含 pnf_target_valid_long 验证")
    
    # 检查 final_short_entry 是否包含 pnf_target_valid_short
    final_short_entry_match = re.search(
        r'final_short_entry = \((.*?)\)',
        content,
        re.DOTALL
    )
    if final_short_entry_match:
        final_short_entry_code = final_short_entry_match.group(1)
        if 'pnf_target_valid_short' in final_short_entry_code:
            print("✅ final_short_entry 包含 pnf_target_valid_short 验证")
        else:
            print("❌ final_short_entry 不包含 pnf_target_valid_short 验证")
    
    print("\n" + "=" * 60)
    print("验证完成！")
    print("=" * 60)

def check_confirm_trade_entry():
    """检查 confirm_trade_entry 中是否有 P&F 验证逻辑（实盘/模拟盘）"""
    with open('user_data/strategies/WyckoffV7_TrueMethod.py', 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 查找 confirm_trade_entry 方法
    confirm_match = re.search(
        r'def confirm_trade_entry\(.*?\):(.*?)(?=\n    def |\Z)',
        content,
        re.DOTALL
    )
    
    if confirm_match:
        confirm_code = confirm_match.group(1)
        
        print("\n✅ 检查 confirm_trade_entry 中的验证（实盘/模拟盘）:")
        
        patterns = [
            (r'target_cons.*<= 0', '多头目标验证'),
            (r'target_short_cons.*<= 0', '空头目标验证'),
            (r'Entry blocked.*Invalid P&F target', '阻止入场日志'),
            (r'return False', '返回 False 阻止入场'),
        ]
        
        for pattern, desc in patterns:
            if re.search(pattern, confirm_code):
                print(f"  ✓ 找到: {desc}")
            else:
                print(f"  ✗ 未找到: {desc}")

if __name__ == '__main__':
    check_populate_entry_trend()
    check_confirm_trade_entry()
    
    print("\n📝 说明:")
    print("  - populate_entry_trend: 回测模式验证（必须）")
    print("  - confirm_trade_entry: 实盘/模拟盘验证（已实现）")
    print("\n下一步: 运行回测验证修复是否生效")
    print("  docker compose run --rm freqtrade backtesting \\")
    print("    --config user_data/config_backtest.json \\")
    print("    --strategy WyckoffV7_TrueMethod \\")
    print("    --timerange 20260419-20260502 \\")
    print("    --cache none")
