#!/usr/bin/env python3
"""
诊断 P&F Target 为什么没有触发
"""

print("=" * 80)
print("P&F Target 诊断分析")
print("=" * 80)
print()

print("问题：")
print("  - Dry-run #9 交易：pnf_target_1 @ 77910.5 (入场 74778.9, +4.15%)")
print("  - 2026 回测：51笔交易，0笔 pnf_target 退出")
print()

print("可能原因：")
print()

print("1. P&F Target 值为 0")
print("   - 如果 target_conservative = 0，条件永远不满足")
print("   - 需要检查：PNFTargetCalculator 是否正确计算目标位")
print("   - 需要检查：TR 边界是否正确识别")
print()

print("2. P&F Target 被其他退出逻辑抢先")
print("   - 优先级 3.8: pnf_target")
print("   - 优先级 4: wyckoff_down_wave")
print("   - 如果 down_wave 先触发，P&F 永远不会执行")
print()

print("3. 利润门槛未达到")
print("   - P&F Target 需要 current_profit >= 0.01 (1%)")
print("   - #9 交易退出时 profit = 4.15%，满足条件")
print()

print("4. 元数据链未正确保存/读取")
print("   - confirm_trade_entry: 保存到 _pending_pnf_metadata")
print("   - leverage: 转移到 _pnf_trades_metadata[(pair, open_date)]")
print("   - custom_exit: 从 _pnf_trades_metadata 读取")
print("   - 如果任何一步失败，目标位 = 0")
print()

print("=" * 80)
print("验证方法：")
print("=" * 80)
print()

print("方法 1: 添加调试日志")
print("  在 custom_exit 的 P&F Target 逻辑中添加：")
print("  logger.info(f'P&F Debug: pair={pair}, profit={current_profit:.4f}, '")
print("              f'tgt_cons={_tgt_cons:.2f}, tgt_std={_tgt_std:.2f}, '")
print("              f'current_rate={current_rate:.2f}, metadata_found={pnf_metadata is not None}')")
print()

print("方法 2: 检查 PNFTargetCalculator 输出")
print("  在 populate_indicators 后添加：")
print("  logger.info(f'P&F Targets: cons={dataframe[\"target_conservative\"].iloc[-1]:.2f}, '")
print("              f'std={dataframe[\"target_standard\"].iloc[-1]:.2f}')")
print()

print("方法 3: 单步回测 #9 交易时间段")
print("  docker compose run --rm freqtrade backtesting \\")
print("    --timerange 20260419-20260502 \\")
print("    --export trades \\")
print("    --export-filename backtest_#9_debug.json")
print()

print("=" * 80)
print("下一步行动：")
print("=" * 80)
print()
print("✅ 修复已应用：custom_exit 从元数据链读取")
print("⏳ 等待验证：下一笔 dry-run 触发 P&F Target")
print("🔍 需要调查：为什么 2026 回测没有 P&F Target 退出")
print()
print("建议：添加调试日志，重新回测 2026-04-19 到 2026-05-02")
print()
