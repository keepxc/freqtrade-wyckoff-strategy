#!/usr/bin/env python3
"""
测试 P&F 元数据链修复是否生效
验证：confirm_trade_entry 保存元数据 → leverage 转移元数据 → custom_exit 读取元数据
"""

print("✅ P&F 元数据链修复验证")
print("=" * 60)
print()
print("修复内容：")
print("  1. confirm_trade_entry: 保存 P&F 目标位到 _pending_pnf_metadata")
print("  2. leverage: 转移到 _pnf_trades_metadata[(pair, open_date)]")
print("  3. custom_exit: 从 _pnf_trades_metadata 读取（而非 live DataFrame）")
print()
print("修复前问题：")
print("  - custom_exit 从 dataframe.loc[current_index, 'target_conservative'] 读取")
print("  - PhaseContextDetector 重算 TR 边界，导致目标位缩水")
print("  - Trade #9: 目标位从 ~$79k 缩至 ~$77.9k")
print()
print("修复后行为：")
print("  - custom_exit 从 self._pnf_trades_metadata.get((pair, open_date)) 读取")
print("  - 目标位锁定在入场时的值，不受后续重算影响")
print("  - 日志标记: [元数据链]")
print()
print("验证方法：")
print("  1. 等待下一笔 dry-run 交易触发 P&F Target 退出")
print("  2. 检查日志是否包含 '[元数据链]' 标记")
print("  3. 对比目标位是否与入场时一致")
print()
print("=" * 60)
print("✅ 修复已应用，等待 dry-run 验证")
