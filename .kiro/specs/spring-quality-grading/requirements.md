# Spring 质量分级系统 - 需求文档

**Feature**: Spring Quality Grading System  
**创建日期**: 2026-02-08  
**优先级**: P0 - 最高优先级  
**理论基础**: 威科夫交易方法 - Phase C 测试阶段

---

## 1. 项目背景

### 1.1 威科夫理论基础

**Spring 的定义**:
- Spring 是威科夫 Phase C（测试阶段）的关键事件
- 表现为价格轻微跌破支撑位后迅速收回
- 目的是"洗盘"并测试供应是否枯竭
- 是最高质量的买入信号之一

**核心原则**:
- "价格停留的时间越短，洗盘的力量越强"
- "Spring 创造牛市，不是牛市的结果"
- "轻微破位 + 极低成交量 = 供应枯竭的证据"

### 1.2 当前问题

**问题 1: Spring 识别过于简化**
- 当前只有简单的 `spring_detected` 布尔值
- 未区分 Spring #1/#2/#3 的质量等级
- 所有 Spring 使用相同的入场条件

**问题 2: 高质量信号被过滤**
- Spring #3（最高质量）被 bull_market 限制过滤
- 错过了 Phase C 的最佳入场时机
- 导致信号释放率低（13%）

**问题 3: 缺少快速收回验证**
- 未验证价格是否"快速"收回
- 可能将长时间的跌破误判为 Spring
- 降低了信号质量

### 1.3 优化目标

1. **实现 Spring 三级分类**: 根据跌破深度和成交量分级
2. **添加快速收回验证**: 5根K线内收回的硬性逻辑
3. **分级入场条件**: Spring #3 完全放开限制
4. **提升信号质量**: Spring #3 胜率 > 85%

---

## 2. 用户故事

### 2.1 作为量化交易者
**我想要**: 系统能够识别不同质量等级的 Spring  
**以便**: 我可以对高质量的 Spring #3 使用更激进的入场策略  
**验收标准**:
- Spring 被分为三个等级：#1（低质量）、#2（中等质量）、#3（高质量）
- 每个等级有明确的量化标准
- dataframe 中有 `spring_quality` 字段

### 2.2 作为策略开发者
**我想要**: Spring #3 不受 bull_market 和 trend_support 限制  
**以便**: 我可以在 Phase C 的最佳时机入场  
**验收标准**:
- Spring #3 只保留 `not_clear_distribution` 过滤
- Spring #3 添加最低防线 `close > ema_200 * 0.95`
- Spring #3 信号数量显著增加

### 2.3 作为风险管理者
**我想要**: 系统验证 Spring 的"快速收回"特征  
**以便**: 避免将长时间的跌破误判为 Spring  
**验收标准**:
- 添加"5根K线内收回"的硬性逻辑
- 超过5根K线的跌破降级为普通跌破
- dataframe 中有 `spring_recovery_speed` 字段

---

## 3. 功能需求

### 3.1 Spring 分级检测

#### 3.1.1 Spring #1 - 低质量（深度跌破 + 极高量）

**量化标准**:
```python
# 跌破深度
breakout_depth = (support_level - low) / support_level
deep_breakout = breakout_depth > 0.02  # 超过2% ATR

# 成交量
volume_ratio = volume / volume_sma_20
extreme_volume = volume_ratio > 1.5  # 超过1.5倍均值

# Spring #1 条件
spring_1 = deep_breakout & extreme_volume & quick_recovery
```

**特征**:
- 跌破深度 > 2% ATR
- 成交量 > 1.5x SMA
- 风险最高，需要等待二次测试

**入场策略**:
- 不直接入场
- 等待放量回升
- 等待二次测试确认

#### 3.1.2 Spring #2 - 中等质量（中度跌破 + 适度量）

**量化标准**:
```python
# 跌破深度
moderate_breakout = (breakout_depth >= 0.01) & (breakout_depth <= 0.02)

# 成交量
moderate_volume = (volume_ratio >= 1.0) & (volume_ratio <= 1.5)

# Spring #2 条件
spring_2 = moderate_breakout & moderate_volume & quick_recovery
```

**特征**:
- 跌破深度 1-2% ATR
- 成交量 1.0-1.5x SMA
- 中等风险，需要测试确认

**入场策略**:
- 等待二次测试
- 测试时成交量萎缩
- 测试确认后入场

#### 3.1.3 Spring #3 - 高质量（轻度跌破 + 极低量）⭐

**量化标准**:
```python
# 跌破深度
light_breakout = breakout_depth < 0.01  # 小于1% ATR

# 成交量
low_volume = volume_ratio < 0.8  # 低于0.8倍均值

# Spring #3 条件
spring_3 = light_breakout & low_volume & quick_recovery
```

**特征**:
- 跌破深度 < 1% ATR
- 成交量 < 0.8x SMA
- 最高质量，供应完全枯竭

**入场策略**:
- 直接入场，无需等待
- 完全放开 bull_market, trend_support 限制
- 只保留 not_clear_distribution 过滤

### 3.2 快速收回验证

#### 3.2.1 时间限制

**硬性逻辑**:
```python
# 检测收回速度
recovery_bars = 当前K线索引 - 跌破K线索引
quick_recovery = recovery_bars <= 5

# 如果超过5根K线
if recovery_bars > 5:
    # 降级为普通跌破，不是 Spring
    spring_quality = 'none'
```

**理论依据**:
- "价格停留的时间越短，洗盘的力量越强"
- 长时间的跌破说明支撑不够强
- 5根K线是合理的时间窗口（1小时图 = 5小时）

#### 3.2.2 收回标准

**收回定义**:
```python
# 价格必须收回到支撑位之上
recovered = close > support_level

# 或者至少收回到支撑位的99.5%
recovered = close >= support_level * 0.995
```

### 3.3 新增字段

#### 3.3.1 spring_quality

**类型**: string  
**可能值**: 'none', 'spring_1', 'spring_2', 'spring_3'  
**说明**: Spring 的质量等级

#### 3.3.2 spring_recovery_speed

**类型**: int  
**可能值**: 0-10（K线数量）  
**说明**: 从跌破到收回的K线数量

#### 3.3.3 spring_breakout_depth

**类型**: float  
**可能值**: 0.0-1.0（百分比）  
**说明**: 跌破支撑位的深度

#### 3.3.4 spring_volume_ratio

**类型**: float  
**可能值**: 0.0-5.0  
**说明**: 跌破时的成交量比率

---

## 4. 非功能需求

### 4.1 性能要求

- Spring 分级检测不应显著增加计算时间
- 使用向量化操作优化性能
- 避免循环遍历 dataframe

### 4.2 可维护性

- 分级标准通过参数配置
- 可以通过 hyperopt 优化阈值
- 清晰的代码注释和文档

### 4.3 可测试性

- 每个分级逻辑独立测试
- 提供测试数据集
- 验证分级准确性

---

## 5. 验收标准

### 5.1 功能验收

- [ ] Spring 被正确分为三个等级
- [ ] Spring #3 识别准确率 > 80%
- [ ] 快速收回验证正常工作
- [ ] 新增字段正确映射到 dataframe

### 5.2 性能验收

- [ ] Spring #3 信号数量 > 5个（当前回测期）
- [ ] Spring #3 胜率 > 85%
- [ ] Spring #3 不受 bull_market 限制
- [ ] 总信号释放率提升 > 20%

### 5.3 回测验证

**回测命令**:
```bash
docker-compose run --rm freqtrade backtesting \
  --strategy WyckoffV7_TrueMethod \
  --timerange 20230101-20251231 \
  --config user_data/config_backtest.json \
  --cache none
```

**预期结果**:
- Spring #3 信号: > 5个
- Spring #3 胜率: > 85%
- 总收益提升: > 5%

---

## 6. 技术约束

### 6.1 依赖项

- Freqtrade 框架
- Pandas 数据处理
- TA-Lib 技术指标库
- 现有的轴线系统（axis_system.py）

### 6.2 兼容性

- 必须与现有的 Spring 检测逻辑兼容
- 不破坏其他入场信号
- 保持向后兼容性

---

## 7. 风险与缓解

### 7.1 风险识别

**风险 1: 分级标准过于严格**
- 影响: Spring #3 识别数量过少
- 缓解: 通过 hyperopt 优化阈值

**风险 2: 快速收回验证过于宽松**
- 影响: 误判普通跌破为 Spring
- 缓解: 严格的5根K线限制

**风险 3: Spring #3 放开限制后胜率下降**
- 影响: 总体收益下降
- 缓解: 保留最低防线 `close > ema_200 * 0.95`

### 7.2 回滚计划

如果优化效果不佳：
1. 通过功能开关禁用 Spring 分级
2. 回滚到原有的 Spring 检测逻辑
3. 分析问题原因并调整参数

---

## 8. 参考文档

- **威科夫交易方法完整学习指南.md** (4345行开始)
- **AXIS_OPTIMIZATION_PHASE2_PLAN.md**
- **PHASE2_1_IMPLEMENTATION_SUMMARY.md**
- **TASKS.md** - Task 2.1

---

**创建时间**: 2026-02-08  
**最后更新**: 2026-02-08  
**状态**: 待审核
