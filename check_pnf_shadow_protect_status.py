"""
检查 PNF Shadow Protect 功能状态的诊断脚本
"""

import os
import sys

# 添加路径
sys.path.insert(0, os.path.abspath('.'))

from user_data.strategies.WyckoffV7_TrueMethod import WyckoffV7_TrueMethod

def check_status():
    """检查 PNF Shadow Protect 功能状态"""
    print("=" * 80)
    print("PNF Shadow Protect 功能状态检查")
    print("=" * 80)
    print()
    
    # 检查环境变量
    env_value = os.getenv('WY_PNF_SHADOW_PROTECT', '1')
    print(f"环境变量 WY_PNF_SHADOW_PROTECT: {env_value}")
    print()
    
    # 创建策略实例
    config = {'stake_currency': 'USDT'}
    strategy = WyckoffV7_TrueMethod(config)
    
    # 检查实例变量
    print(f"策略实例 pnf_shadow_protect_enabled: {strategy.pnf_shadow_protect_enabled}")
    print(f"策略实例 pnf_timeout_hours: {strategy.pnf_timeout_hours}")
    print()
    
    # 检查辅助方法是否存在
    has_activation = hasattr(strategy, '_check_pnf_warning_zone_activation')
    has_timeout = hasattr(strategy, '_check_pnf_timeout')
    has_shadow_protect = hasattr(strategy, '_check_shadow_protect_pnf_mode')
    
    print(f"辅助方法检查:")
    print(f"  - _check_pnf_warning_zone_activation: {'✅ 存在' if has_activation else '❌ 不存在'}")
    print(f"  - _check_pnf_timeout: {'✅ 存在' if has_timeout else '❌ 不存在'}")
    print(f"  - _check_shadow_protect_pnf_mode: {'✅ 存在' if has_shadow_protect else '❌ 不存在'}")
    print()
    
    # 总结
    print("=" * 80)
    if strategy.pnf_shadow_protect_enabled and has_activation and has_timeout and has_shadow_protect:
        print("✅ PNF Shadow Protect 功能已启用且所有组件正常")
    elif not strategy.pnf_shadow_protect_enabled:
        print("⚠️ PNF Shadow Protect 功能已禁用（WY_PNF_SHADOW_PROTECT=0）")
    else:
        print("❌ PNF Shadow Protect 功能异常，缺少必要组件")
    print("=" * 80)

if __name__ == '__main__':
    check_status()
