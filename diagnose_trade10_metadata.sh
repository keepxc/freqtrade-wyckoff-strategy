#!/bin/bash
# Trade #10 元数据链完整审计
# 验证 P0 Fix 是否生效 + TR 边界诊断

echo "=========================================="
echo "Trade #10 完整法医鉴定"
echo "=========================================="
echo ""

LOG_FILE="${1:-dryrun.log}"

if [ ! -f "$LOG_FILE" ]; then
    echo "❌ 日志文件不存在: $LOG_FILE"
    exit 1
fi

echo "📁 日志文件: $LOG_FILE"
echo ""

echo "🔍 Phase 1: 入场时刻 - P&F 目标位锁定"
echo "=========================================="
echo ""
echo "1.1 检查 leverage() 是否锁定目标位："
grep -A 3 "✅ P&F 目标位已锁定.*BTC/USDT" "$LOG_FILE" | grep -A 3 "78244\|78245" || echo "❌ 未找到 Trade #10 的目标位锁定记录"
echo ""

echo "1.2 检查入场时的 TR 边界（如果有诊断日志）："
grep "TR 边界.*78244\|78245" "$LOG_FILE" || echo "ℹ️  无 TR 边界诊断日志（需要添加）"
echo ""

echo "1.3 检查 confirm_trade_entry 调用（预期：无）："
grep "confirm_trade_entry.*BTC/USDT.*78244\|78245" "$LOG_FILE" || echo "✅ 确认 confirm_trade_entry 未被调用"
echo ""

echo "🔍 Phase 2: 持仓期间 - TR 重算检测"
echo "=========================================="
echo ""
echo "2.1 检查 PhaseContext 重算事件："
# 查找 Trade #10 开仓后到平仓前的 PhaseContext 更新
grep -A 2 "PhaseContext.*BTC/USDT" "$LOG_FILE" | grep -A 2 "2026-05-0[12]" | head -20
echo ""

echo "2.2 检查 target_conservative 值的变化："
# 如果有多次计算，会看到不同的值
grep "target_conservative.*BTC/USDT" "$LOG_FILE" | grep "2026-05-0[12]" | head -10
echo ""

echo "🔍 Phase 3: 退出时刻 - 元数据链验证"
echo "=========================================="
echo ""
echo "3.1 检查退出日志中的 [元数据链] 标记："
grep -B 5 -A 10 "Exited.*#10\|exit.*79288" "$LOG_FILE" | grep -A 5 "元数据链\|pnf_target"
echo ""

echo "3.2 检查退出时使用的目标位值："
grep -B 3 -A 3 "pnf_target_1.*BTC/USDT.*79288" "$LOG_FILE" || echo "⚠️  未找到 pnf_target_1 退出日志"
echo ""

echo "3.3 完整退出上下文："
grep -B 10 -A 5 "79288.2" "$LOG_FILE" | head -30
echo ""

echo "=========================================="
echo "🎯 关键诊断问题"
echo "=========================================="
echo ""
echo "Q1: leverage() 是否在入场时锁定了目标位？"
LOCKED=$(grep "✅ P&F 目标位已锁定" "$LOG_FILE" | grep "78244\|78245" | wc -l)
if [ $LOCKED -gt 0 ]; then
    echo "✅ 是 - P0 Fix 生效"
    grep "✅ P&F 目标位已锁定" "$LOG_FILE" | grep "78244\|78245"
else
    echo "❌ 否 - P0 Fix 未生效，需要调试"
fi
echo ""

echo "Q2: 退出时是否使用了元数据链？"
META_CHAIN=$(grep "\[元数据链\]" "$LOG_FILE" | grep "79288\|#10" | wc -l)
if [ $META_CHAIN -gt 0 ]; then
    echo "✅ 是 - 使用了锁定的目标位"
else
    echo "❌ 否 - 可能使用了 DataFrame 回退路径（目标位可能已漂移）"
fi
echo ""

echo "Q3: 入场时锁定的目标位是多少？"
echo "（从 leverage() 日志中提取）"
grep "✅ P&F 目标位已锁定" "$LOG_FILE" | grep "78244\|78245" | grep -oP "conservative=\K[0-9.]+" || echo "❌ 无法提取"
echo ""

echo "Q4: 退出时使用的目标位是多少？"
echo "（从 custom_exit 日志中提取）"
grep "target_cons\|conservative" "$LOG_FILE" | grep "79288\|#10" | head -3 || echo "❌ 无法提取"
echo ""

echo "Q5: TR 边界是什么？（需要添加诊断日志）"
echo "预期应该看到："
echo "  - TR Low: ~75000 (5月初低点)"
echo "  - TR High: ~80000 (当前高点)"
echo "  - TR Height: ~5000"
echo "但实际可能只识别了："
echo "  - TR Low: ~77000 (局部低点)"
echo "  - TR High: ~79000 (局部高点)"
echo "  - TR Height: ~2000"
echo ""

echo "=========================================="
echo "📋 下一步行动"
echo "=========================================="
echo ""
if [ $LOCKED -eq 0 ]; then
    echo "🚨 P0 优先级："
    echo "   - leverage() 未锁定目标位，需要调试 dry-run 环境"
    echo "   - 检查 **kwargs 是否包含 dataframe 和 trade"
elif [ $META_CHAIN -eq 0 ]; then
    echo "⚠️  P0 优先级："
    echo "   - 目标位已锁定，但退出时未使用元数据链"
    echo "   - 检查 custom_exit 中的元数据读取逻辑"
else
    echo "✅ P0 已验证通过，进入 P1/P2 优化："
    echo ""
    echo "P1: 添加 TR 边界诊断日志"
    echo "   - 在 pnf_target_calculator.py 中记录 TR 来源"
    echo "   - 在 leverage() 中记录锁定的 TR 边界"
    echo ""
    echo "P2: 优化 TR 检测窗口"
    echo "   - 扩大 fallback_window: 120 → 240 根"
    echo "   - 改进 swing high/low 检测周期"
    echo "   - 添加成交量验证（SC-AR 段的 effort）"
fi
echo ""
