#!/bin/bash
# Validation Framework Quick Test Script
# 验证框架快速测试脚本

set -e

echo "=================================="
echo "Validation Framework Test Suite"
echo "=================================="
echo ""

# 检查 pytest 是否安装
if ! command -v pytest &> /dev/null; then
    echo "❌ pytest not found. Installing..."
    pip install pytest pandas numpy
fi

echo "📋 Running Unit Tests..."
echo ""

# 运行所有单元测试
pytest tests/test_daily_event_detector.py -v --tb=short
pytest tests/test_event_sequence_state_machine.py -v --tb=short
pytest tests/test_market_env_detector.py -v --tb=short
pytest tests/test_composite_man_detector.py -v --tb=short

echo ""
echo "✅ Unit tests completed"
echo ""

# 运行参数敏感性测试示例
echo "📊 Running Parameter Sensitivity Tests..."
pytest tests/test_parameter_sensitivity.py::TestMarketEnvDetectorSensitivity::test_half_window_sensitivity -v -s

echo ""
echo "=================================="
echo "✅ All validation tests passed!"
echo "=================================="
echo ""
echo "Next steps:"
echo "1. Review test results above"
echo "2. Check tests/README_VALIDATION_FRAMEWORK.md for usage guide"
echo "3. Integrate monitoring panels into backtest workflow"
echo ""
