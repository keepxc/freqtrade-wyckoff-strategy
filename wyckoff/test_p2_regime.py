#!/usr/bin/env python3
"""P2 Regime-Aware Authorization System 测试脚本"""

import sys
import pandas as pd
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# 禁用日志输出
import logging
logging.basicConfig(level=logging.CRITICAL)

from wyckoff.regime_authorizer import RegimeAuthorizer


class MockStrategy:
    """模拟策略对象"""
    pass


def test_basic():
    """测试基本功能"""
    print("测试 1: 基本初始化")
    strategy = MockStrategy()
    authorizer = RegimeAuthorizer(strategy)
    assert authorizer.enabled == False
    assert authorizer.strategy == strategy
    print("✅ 通过\n")


def test_rr_multiplier():
    """测试 RR Multiplier"""
    print("测试 2: RR Multiplier")
    strategy = MockStrategy()
    authorizer = RegimeAuthorizer(strategy)
    authorizer.enabled = True
    
    # Markup 应该是 1.0
    mult = authorizer.get_rr_multiplier('markup', 'trending', 'markup', False)
    assert mult == 1.0
    print(f"  Markup: {mult}")
    
    # Markdown 应该 > 1.0
    mult = authorizer.get_rr_multiplier('markdown', 'trending', 'markdown', False)
    assert mult >= 1.25
    print(f"  Markdown: {mult}")
    
    # Bear market 应该最高
    mult = authorizer.get_rr_multiplier('markdown', 'trending', 'markdown', True)
    assert mult >= 1.4
    print(f"  Bear market: {mult}")
    print("✅ 通过\n")


def test_holding_time():
    """测试持仓时间 Multiplier"""
    print("测试 3: Holding Time Multiplier")
    strategy = MockStrategy()
    authorizer = RegimeAuthorizer(strategy)
    authorizer.enabled = True
    
    # Markup 应该是 1.0
    mult = authorizer.get_holding_time_multiplier('markup', 'trending', False)
    assert mult == 1.0
    print(f"  Markup: {mult}")
    
    # Markdown 应该 < 1.0
    mult = authorizer.get_holding_time_multiplier('markdown', 'trending', False)
    assert mult <= 0.7
    print(f"  Markdown: {mult}")
    
    # Bear market 应该最短
    mult = authorizer.get_holding_time_multiplier('markdown', 'trending', True)
    assert mult <= 0.6
    print(f"  Bear market: {mult}")
    print("✅ 通过\n")


def test_signal_routing():
    """测试信号路由"""
    print("测试 4: Signal Routing")
    strategy = MockStrategy()
    authorizer = RegimeAuthorizer(strategy)
    authorizer.enabled = True
    
    # Bear market: spring 允许, sos 禁用
    assert authorizer.is_signal_allowed('spring', 'markdown', 'trending', True) == True
    print("  Bear market spring: 允许")
    
    assert authorizer.is_signal_allowed('sos', 'markdown', 'trending', True) == False
    print("  Bear market sos: 禁用")
    
    assert authorizer.is_signal_allowed('phase_d', 'markdown', 'trending', True) == False
    print("  Bear market phase_d: 禁用")
    print("✅ 通过\n")


def test_regime_summary():
    """测试 Regime 摘要"""
    print("测试 5: Regime 摘要统计")
    strategy = MockStrategy()
    authorizer = RegimeAuthorizer(strategy)
    
    df = pd.DataFrame({
        'macro_phase': ['markup'] * 60 + ['markdown'] * 40,
        'market_env': ['trending'] * 50 + ['trading_range'] * 50,
        'bear_market_mode': [False] * 90 + [True] * 10
    })
    
    summary = authorizer.get_regime_summary(df)
    assert 'macro_phase' in summary
    assert 'market_env' in summary
    assert 'bear_market_mode' in summary
    print(f"  macro_phase: {summary['macro_phase']}")
    print(f"  bear_market_mode: {summary['bear_market_mode']}")
    print("✅ 通过\n")


if __name__ == '__main__':
    print("\n" + "=" * 60)
    print("P2 Regime-Aware Authorization System 测试")
    print("=" * 60 + "\n")
    
    try:
        test_basic()
        test_rr_multiplier()
        test_holding_time()
        test_signal_routing()
        test_regime_summary()
        
        print("=" * 60)
        print("✅ 所有测试通过")
        print("=" * 60 + "\n")
        sys.exit(0)
    
    except AssertionError as e:
        print(f"\n❌ 测试失败: {e}\n")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 测试异常: {e}\n")
        import traceback
        traceback.print_exc()
        sys.exit(1)
