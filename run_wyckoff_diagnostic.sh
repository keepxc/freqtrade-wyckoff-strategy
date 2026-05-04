#!/bin/bash

# Wyckoff 检测诊断快速启动脚本

echo "=========================================="
echo "Wyckoff 结构检测诊断工具"
echo "=========================================="
echo ""
echo "此脚本将运行回测并输出详细的 SC/BC 检测诊断信息"
echo ""

# 检查是否在正确的目录
if [ ! -f "user_data/strategies/WyckoffV7_TrueMethod.py" ]; then
    echo "❌ 错误：未找到策略文件"
    echo "请在项目根目录运行此脚本"
    exit 1
fi

# 备份原始检测器（如果还没备份）
if [ ! -f "wyckoff/climatic_action_detector.py.original" ]; then
    echo "📦 备份原始检测器..."
    cp wyckoff/climatic_action_detector.py wyckoff/climatic_action_detector.py.original
fi

# 临时替换为诊断版本
echo "🔧 启用诊断版检测器..."
cp wyckoff/climatic_action_detector_diagnostic.py wyckoff/climatic_action_detector.py

# 运行回测（使用较短的时间范围以加快速度）
echo ""
echo "🚀 开始运行诊断回测..."
echo "   时间范围: 2024-01-01 至 2026-03-30"
echo "   策略: WyckoffV7_TrueMethod"
echo ""

docker compose run --rm freqtrade backtesting \
    --config user_data/config_backtest.json \
    --strategy WyckoffV7_TrueMethod \
    --timerange 20240101-20260330 \
    --cache none \
    2>&1 | tee wyckoff_diagnostic_output.log

# 恢复原始检测器
echo ""
echo "🔄 恢复原始检测器..."
cp wyckoff/climatic_action_detector.py.original wyckoff/climatic_action_detector.py

echo ""
echo "=========================================="
echo "诊断完成！"
echo "=========================================="
echo ""
echo "📊 诊断报告已保存到: wyckoff_diagnostic_output.log"
echo ""
echo "🔍 查看关键信息："
echo "   grep '🔍 Wyckoff' wyckoff_diagnostic_output.log"
echo "   grep 'SC (Selling Climax)' wyckoff_diagnostic_output.log"
echo "   grep 'BC (Buying Climax)' wyckoff_diagnostic_output.log"
echo ""
echo "📈 查看条件通过率："
echo "   grep '条件' wyckoff_diagnostic_output.log | grep '%'"
echo ""
