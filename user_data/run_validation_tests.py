#!/usr/bin/env python3
"""
Docker 环境验证测试运行脚本
放在 user_data 目录以便 docker 可以访问
"""
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, '/freqtrade')

print("=" * 70)
print("Validation Framework Test Suite (Docker Environment)")
print("=" * 70)
print()

# 检查并安装 pytest
try:
    import pytest
    print("✅ pytest is available")
except ImportError:
    print("❌ pytest not found, installing...")
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "pytest", "--user"], check=True)
    import pytest
    print("✅ pytest installed")

print()

# 由于 tests 目录未映射到 docker，我们直接运行简化的验证测试
print("📋 Running simplified validation tests...")
print("-" * 70)
print()

# 测试 1: 导入检测器模块
print("Test 1: Import detector modules")
try:
    from wyckoff.daily_event_detector import DailyEventDetector
    from wyckoff.event_sequence_state_machine import EventSequenceStateMachine
    from wyckoff.market_env_detector import MarketEnvironmentDetector
    from wyckoff.composite_man_detector import CompositeManIntentDetector
    print("✅ All detector modules imported successfully")
except Exception as e:
    print(f"❌ Failed to import detector modules: {e}")
    sys.exit(1)

print()

# 测试 2: 导入监控工具
print("Test 2: Import monitoring tools")
try:
    from wyckoff.detector_monitoring_panel import DetectorMonitoringPanel
    from wyckoff.dryrun_drift_monitor import DryRunDriftMonitor
    from wyckoff.enter_tag_winrate_tracker import EnterTagWinRateTracker
    from wyckoff.rule_switch_template import RuleSwitchManager, get_rule_manager
    print("✅ All monitoring tools imported successfully")
except Exception as e:
    print(f"❌ Failed to import monitoring tools: {e}")
    sys.exit(1)

print()

# 测试 3: 创建检测器实例
print("Test 3: Create detector instances")
try:
    daily_detector = DailyEventDetector()
    state_machine = EventSequenceStateMachine()
    market_env = MarketEnvironmentDetector()
    composite_man = CompositeManIntentDetector()
    print("✅ All detector instances created successfully")
except Exception as e:
    print(f"❌ Failed to create detector instances: {e}")
    sys.exit(1)

print()

# 测试 4: 创建监控工具实例
print("Test 4: Create monitoring tool instances")
try:
    panel = DetectorMonitoringPanel()
    drift_monitor = DryRunDriftMonitor()
    winrate_tracker = EnterTagWinRateTracker()
    rule_manager = get_rule_manager()
    print("✅ All monitoring tool instances created successfully")
except Exception as e:
    print(f"❌ Failed to create monitoring tool instances: {e}")
    sys.exit(1)

print()

# 测试 5: 基础功能测试
print("Test 5: Basic functionality tests")
try:
    import pandas as pd
    import numpy as np
    
    # 创建测试数据
    n = 100
    test_df = pd.DataFrame({
        'date': pd.date_range('2023-01-01', periods=n, freq='1h'),
        'open': np.random.uniform(40000, 50000, n),
        'high': np.random.uniform(50000, 52000, n),
        'low': np.random.uniform(38000, 40000, n),
        'close': np.random.uniform(40000, 50000, n),
        'volume': np.random.uniform(100, 1000, n),
        'atr': [500.0] * n,
    })
    
    # 测试 MarketEnvironmentDetector
    result = market_env.detect(test_df.copy())
    assert 'market_env' in result.columns, "Missing market_env column"
    assert 'market_env_confidence' in result.columns, "Missing confidence column"
    print("  ✅ MarketEnvironmentDetector.detect() works")
    
    # 测试 DetectorMonitoringPanel
    panel_result = panel.analyze_dataframe(result)
    assert 'total_bars' in panel_result, "Missing total_bars in panel result"
    print("  ✅ DetectorMonitoringPanel.analyze_dataframe() works")
    
    # 测试 RuleSwitchManager
    rule_manager.register_rule(
        rule_id='test_rule',
        rule_name='Test Rule',
        description='Test rule for validation',
        enabled=False
    )
    assert not rule_manager.is_enabled('test_rule'), "Rule should be disabled"
    print("  ✅ RuleSwitchManager.register_rule() works")
    
    print("✅ All basic functionality tests passed")
    
except Exception as e:
    print(f"❌ Basic functionality test failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print()

# 测试 6: 规则管理测试
print("Test 6: Rule management tests")
try:
    # 启用规则
    rule_manager.enable_rule('test_rule', reason='Testing')
    assert rule_manager.is_enabled('test_rule'), "Rule should be enabled"
    print("  ✅ Rule enable works")
    
    # 禁用规则
    rule_manager.disable_rule('test_rule', reason='Testing')
    assert not rule_manager.is_enabled('test_rule'), "Rule should be disabled"
    print("  ✅ Rule disable works")
    
    # 审计日志
    audit_log = rule_manager.get_audit_log('test_rule')
    assert len(audit_log) >= 3, "Should have at least 3 audit entries"
    print("  ✅ Audit log works")
    
    print("✅ All rule management tests passed")
    
except Exception as e:
    print(f"❌ Rule management test failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print()
print("=" * 70)
print("✅ All validation tests passed successfully!")
print("=" * 70)
print()
print("Summary:")
print("  - All detector modules imported")
print("  - All monitoring tools imported")
print("  - All instances created successfully")
print("  - Basic functionality verified")
print("  - Rule management verified")
print()
print("Note: Full unit tests (52 test cases) require the tests/ directory")
print("      to be mapped in docker-compose.yml")
print()
