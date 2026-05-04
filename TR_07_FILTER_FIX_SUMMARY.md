# TR 0.7 过滤逻辑修复总结

## 问题描述

文档中多次声明实施了 TR 0.7-1.0 区间过滤（负期望区域），但实际代码中完全缺失该逻辑。

### 文档声明（未实现）

- `P0_MIXED_STRATEGY_METADATA_STATUS.md` 第 85-92 行
- `WYCKOFF_FIX_ROADMAP.md` 多处提到 "TR 0.7-1.0 区间过滤"

### 实际代码（修复前）

- 只有 **0.5 分界点**：`if has_daily_swing and tr_position_1h < 0.5`
- **没有 0.7 过滤**：搜索整个代码库 `if.*tr_position.*>=.*0\.7` → 0 个匹配

---

## 修复内容

### 1. 添加 logger 导入

**文件**: `wyckoff/pnf_target_calculator.py`

```python
import logging

logger = logging.getLogger(__name__)
```

### 2. 实施 TR 0.7 过滤逻辑

**文件**: `wyckoff/pnf_target_calculator.py` (第 187-210 行)

```python
# P0 Fix (2026-05-04): TR 0.7-1.0 区间过滤（负期望区域）
# 当 TR position >= 0.7 时，将目标位设为 0，阻止入场
tr_filtered_mask = tr_position >= 0.7
tr_low_filtered = tr_low.copy()
tr_high_filtered = tr_high.copy()
tr_height_filtered = tr_height.copy()

# 将过滤掉的 K 线的目标位设为 0
tr_low_filtered[tr_filtered_mask] = 0.0
tr_high_filtered[tr_filtered_mask] = 0.0
tr_height_filtered[tr_filtered_mask] = 0.0

# 记录过滤统计
filtered_count = int(tr_filtered_mask.sum())
if filtered_count > 0:
    logger.info(
        f'🔴 TR 0.7+ 过滤: {filtered_count} 个 K 线被过滤 '
        f'(TR position >= 0.7，负期望区域)'
    )

dataframe['tr_low'] = tr_low_filtered
dataframe['tr_high'] = tr_high_filtered
dataframe['tr_height'] = tr_height_filtered
dataframe['tr_source'] = tr_source
dataframe['tr_source_detail'] = tr_source_detail
dataframe['tr_position'] = tr_position

# 计算目标位（过滤后的 TR 会导致目标位为 0）
dataframe['target_conservative'] = tr_low_filtered + tr_height_filtered * 1.0
dataframe['target_standard'] = tr_low_filtered + tr_height_filtered * 1.5
dataframe['target_aggressive'] = tr_low_filtered + tr_height_filtered * 2.0
dataframe['target_vertical'] = target_vertical
```

### 3. 过滤机制

- **TR position >= 0.7** → 将 `tr_low`, `tr_high`, `tr_height` 设为 0
- **目标位计算** → `target_conservative = 0 + 0 * 1.0 = 0`
- **入场验证** → 策略中的 `pnf_target_valid_long` 检查会阻止目标位为 0 的入场

---

## 验证结果

### 单元测试

**脚本**: `verify_tr_07_filter.py`

```
✅ TR >= 0.7 的 K 线目标位全部为 0（过滤生效）
✅ TR < 0.7 的 K 线有正常目标位（未被误杀）
```

### 回测验证

**命令**:
```bash
docker compose run --rm freqtrade backtesting \
  --config user_data/config_backtest.json \
  --strategy WyckoffV7_TrueMethod \
  --timerange 20260419-20260504 \
  --cache none
```

**日志输出**:
```
🔴 TR 0.7+ 过滤: 304 个 K 线被过滤 (TR position >= 0.7，负期望区域)
```

**回测结果**:
- 总交易数: 4 笔（全部 axis_support 和 spring）
- 总利润: +4.01% (+401 USDT)
- 胜率: 100% (4 胜 0 负)
- 最大回撤: 0.00%

---

## 影响分析

### 对 Trade #12 的影响

- **TR position**: 0.693
- **过滤阈值**: 0.7
- **结果**: 0.693 < 0.7，**不会被过滤**（差 0.007）

### 预期效果

根据 `P0_TASK1_VALIDATION_REPORT.md` 的分析：

| TR 区间 | 交易数 | 胜率 | 平均利润 | 评估 |
|---------|--------|------|---------|------|
| 0.7-1.0 | 26 (14.9%) | 38.5% | -0.31% | 🔴 负期望区域 |

过滤 TR 0.7-1.0 区间预计可以：
- 减少 26 笔负期望交易（14.9%）
- 提升整体胜率从 62.6% 到约 68-70%
- 避免高位追涨导致的亏损

---

## 文件清单

### 修改的文件

1. `wyckoff/pnf_target_calculator.py`
   - 添加 logger 导入
   - 实施 TR 0.7 过滤逻辑
   - 添加过滤统计日志

### 新增的文件

1. `verify_tr_07_filter.py` - 单元测试脚本
2. `TR_07_FILTER_FIX_SUMMARY.md` - 本文档

---

## 下一步

1. **全量回测验证**（2023-2026）
   ```bash
   docker compose run --rm freqtrade backtesting \
     --config user_data/config_backtest.json \
     --strategy WyckoffV7_TrueMethod \
     --timerange 20230101-20260504 \
     --cache none
   ```

2. **对比基线**
   - 当前基线: 1236.70% | 657笔 | Sharpe 2.46 | SQN 5.94 | DD 6.80%
   - 预期改善: 胜率提升 5-8pp，回撤降低

3. **Dry-run 验证**
   - 部署到 dry-run 服务器
   - 观察 TR 0.7+ 过滤日志
   - 验证是否减少高位追涨亏损

---

## 验收标准

- [x] 代码语法检查通过
- [x] 单元测试通过
- [x] 短周期回测验证通过（304 个 K 线被过滤）
- [ ] 全量回测验证通过（胜率 ≥ 68%）
- [ ] Dry-run 验证通过（2-4 周）

---

**修复日期**: 2026-05-04  
**修复人**: Claude (Kiro AI)  
**状态**: ✅ 已实施并通过短周期验证
