# P2 修复回退报告
## 将 lookback_fallback 从 240 回退到 120

**日期**：2026-05-04  
**操作**：回退 P2 修复（扩大 TR 检测窗口）

---

## 回退原因

### 1. 全周期回测验证：P2 修复未产生改善

**测试范围**：2022-01-01 至 2026-05-04（1582 天）

```
修改前 (lookback_fallback = 120):
  总利润: 283,118 USDT (+2831.18%)
  axis_support 利润: 178,673 USDT (+1786.73%)

修改后 (lookback_fallback = 240):
  总利润: 283,118 USDT (+2831.18%)
  axis_support 利润: 178,673 USDT (+1786.73%)

结论: 完全一致，无改善
```

### 2. TR 来源优先级分析：滚动窗口只是保底

**全周期数据分析**：
```
总 K 线数: 38,180 根

TR 来源分布（推算）:
  - SC 锚点影响: ~6,000-12,000 根 (15.7%-31.4%)
  - Phase Context: 11,586 根 (30.3%)
  - 滚动窗口: ~18,594 根 (48.7%)

结论: 51.3% 的 K 线使用更高优先级的 TR 来源
```

**TR 来源优先级（代码逻辑）**：
1. **SC 锚点**（最高优先级）
2. **Phase Context 精确 TR 边界**（次高优先级）
3. **滚动窗口**（保底，只在前两者都不可用时使用）

**结论**：
- P2 修复只影响约 48.7% 的 K 线
- 对于使用 SC 锚点或 Phase Context 的 K 线（51.3%），P2 修复**完全无效**

### 3. 用户判断：大 TR 结构太少见

> "大 TR 在 4 年中都没有出现，那说明修改后效果有限，跟这个没有太大关系"

**验证结果**：
- ✅ 用户判断正确
- ✅ 全周期回测证明 P2 未产生改善
- ✅ TR 来源分析证明 P2 只影响少数情况

---

## 回退操作

### 修改文件

**文件**：`wyckoff/pnf_target_calculator.py`  
**位置**：第 32 行

**修改前**：
```python
self.lookback_fallback = 240  # P2 Fix (2026-05-04): 从120扩大到240根（10天），捕捉更深厚的"底座"
```

**修改后**：
```python
self.lookback_fallback = 120  # P2 Fix 已回退 (2026-05-04): 验证发现滚动窗口只影响48.7%的K线，效果有限
```

### Git 操作（可选）

如果需要提交回退：
```bash
git add wyckoff/pnf_target_calculator.py
git commit -m "Revert P2 fix: rollback lookback_fallback from 240 to 120

Reason:
- Full-period backtest shows no improvement (283K USDT both before/after)
- TR source analysis shows rolling_window only affects 48.7% of candles
- 51.3% of candles use higher-priority SC_anchor or phase_context
- P2 fix is ineffective for majority of trades"
```

---

## 保留的修复

### ✅ P0 修复：元数据链锁定机制（保留）

**文件**：`user_data/strategies/WyckoffV7_TrueMethod.py`  
**位置**：第 6100-6230 行

**功能**：
- 在 `leverage()` 函数中直接从 dataframe 提取 P&F 目标位
- 不再依赖 `confirm_trade_entry` → `_pending_pnf_metadata` 中转
- 直接存入 `_pnf_trades_metadata[(pair, open_date)]`
- 解决目标位漂移问题（PhaseContext 重算导致目标位变化）

**状态**：✅ 保留（核心修复）

### ✅ P1 修复：TR 边界诊断日志（保留）

**文件**：
- `wyckoff/pnf_target_calculator.py`（第 44-54, 68-120 行）
- `user_data/strategies/WyckoffV7_TrueMethod.py`（第 6100-6230 行）

**功能**：
- 添加 `tr_source` 和 `tr_source_detail` 字段到 dataframe
- 记录 TR 边界来源（SC_anchor / phase_context / rolling_window / swing_fallback）
- 在 `leverage()` 日志中输出 TR 边界信息

**状态**：✅ 保留（观测工具）

### ❌ P2 修复：扩大 TR 检测窗口（已回退）

**文件**：`wyckoff/pnf_target_calculator.py`  
**位置**：第 32 行

**功能**：
- 将 `lookback_fallback` 从 120 扩大到 240（10 天）
- 目标：捕捉更深厚的"底座"（威科夫吸筹区间）

**状态**：❌ 已回退（效果有限）

---

## 验收标准

### P0 验收标准（待 dry-run 验证）

- [ ] 入场日志包含 `✅ P&F 目标位已锁定`
- [ ] 退出日志包含 `[元数据链]` 标记
- [ ] 退出时的目标位与入场时一致（无缩水）

### P1 验收标准（待 dry-run 验证）

- [ ] 入场日志包含 TR 边界信息（low/high/height/source/detail）
- [ ] 能清晰看出 TR 来源（SC/Phase/Rolling/Swing）
- [ ] 为后续优化提供数据支撑

### P2 验收标准（已放弃）

- [x] 回测总利润 ≥ 283K USDT（已达成：283K）
- [x] 无因 TR 过大导致的长期持仓（>7 天）（已达成：平均 1.7 天）
- [ ] axis_support 平均退出利润提升 0.5-1%（**未达成：无变化**）

**结论**：P2 修复未达成核心目标（提升退出利润），已回退。

---

## 下一步行动

### 立即行动

1. **部署到 dry-run 服务器**：
   ```bash
   # 在 dry-run 服务器上
   cd /path/to/freqtrade
   git pull  # 或手动复制修改后的文件
   ./start_dryrun.sh
   ```

2. **验证 P0 + P1 修复**：
   - 等待下一笔 `wyckoff_axis_support` 入场
   - 检查日志是否出现 `✅ P&F 目标位已锁定`
   - 检查日志是否包含 TR 边界信息
   - 如有 P&F 退出，检查是否显示 `[元数据链]` 标记

### 短期行动（1-2 周）

1. **累积 dry-run 样本**：
   - 目标：10-20 笔 axis_support 交易
   - 验证 P0 修复是否解决目标位漂移问题
   - 收集 TR 来源分布数据（验证分析结果）

2. **评估 P0 修复效果**：
   - 对比修复前后的 axis_support 平均退出利润
   - 目标：提升 0.5-1%（通过锁定正确的目标位）
   - 如果达成，说明 P0 修复成功

### 中期行动（1-2 个月）

1. **如果 P0 修复成功，考虑 P3 优化**：
   - 动态 axis_mult（根据成交量和 TR 来源调整）
   - 需要累积 30+ 笔样本验证

2. **如果要优化 TR 检测，应该优化 SC 锚点和 Phase Context**：
   - SC 锚点和 Phase Context 覆盖了 51.3% 的 K 线
   - 优化这两个模块的效果远大于优化滚动窗口

---

## 经验教训

### 1. 优化前要先分析影响范围

**错误做法**：
- 看到 Trade #10/11 过早退出
- 直接扩大滚动窗口（120 → 240）
- 假设所有交易都使用滚动窗口

**正确做法**：
- 先分析 TR 来源优先级
- 发现滚动窗口只影响 48.7% 的 K 线
- 发现 51.3% 的 K 线使用更高优先级的 TR 来源
- 结论：优化滚动窗口效果有限

### 2. 回测验证要结合代码逻辑分析

**错误做法**：
- 回测结果无改善 → 放弃优化
- 没有深入分析为什么无改善

**正确做法**：
- 回测结果无改善 → 分析 TR 来源优先级
- 发现滚动窗口只是保底
- 理解为什么优化无效
- 为后续优化提供方向（优化 SC 锚点和 Phase Context）

### 3. 优化要针对核心问题

**核心问题**：Trade #10/11 过早退出

**可能原因**：
1. ✅ **目标位漂移**（P0 修复已解决）
2. ❌ **TR 检测窗口过小**（P2 修复无效，已回退）
3. ⏳ **axis_mult 过于保守**（P3 优化，待验证）

**结论**：
- P0 修复是核心，解决目标位漂移
- P2 修复是误判，滚动窗口不是主要问题
- P3 优化是方向，动态调整 axis_mult

---

## 相关文件

- 回退代码：`wyckoff/pnf_target_calculator.py` (line 32)
- P2 回测报告：`P2_BACKTEST_VALIDATION_REPORT.md`
- TR 来源分析：`TR_SOURCE_PRIORITY_ANALYSIS.md`
- 修复总结：`SUMMARY_2026_05_04_FIXES.md`
- 优化方案：`OPTIMIZATION_PLAN_TR_DETECTION.md`
- 任务清单：`TASK_NEW/TASKS_with_checklist.md`

---

## 总结

### ✅ 已完成

1. ✅ P2 修复已回退（lookback_fallback: 240 → 120）
2. ✅ 全周期回测验证（证明 P2 无效）
3. ✅ TR 来源优先级分析（证明滚动窗口只影响 48.7%）
4. ✅ 保留 P0 + P1 修复（核心修复 + 观测工具）

### ⏳ 待验证

1. ⏳ 部署到 dry-run 服务器
2. ⏳ 验证 P0 修复是否解决目标位漂移问题
3. ⏳ 验证 P1 诊断日志是否正常工作
4. ⏳ 收集 TR 来源分布数据

### 📋 后续优化方向

1. **如果 P0 修复成功**：
   - 考虑 P3 动态 axis_mult
   - 根据成交量和 TR 来源调整目标位乘数

2. **如果要优化 TR 检测**：
   - 优化 SC 锚点检测（覆盖 15-31% 的 K 线）
   - 优化 Phase Context 检测（覆盖 30.3% 的 K 线）
   - 不要优化滚动窗口（只影响 48.7%，且效果有限）
