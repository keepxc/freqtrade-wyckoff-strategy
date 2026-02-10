# Spring 质量分级系统 - 设计文档

**Feature**: Spring Quality Grading System  
**创建日期**: 2026-02-08  
**优先级**: P0 - 最高优先级  
**状态**: 设计中

---

## 1. 设计概述

### 1.1 设计目标

实现威科夫理论中 Spring 的三级质量分类系统，根据跌破深度和成交量特征自动识别 Spring #1/#2/#3，并为每个等级提供差异化的入场策略。

### 1.2 核心设计原则

1. **理论驱动**: 严格遵循威科夫理论的 Spring 定义
2. **量化标准**: 所有判断标准必须可量化、可测试
3. **向量化实现**: 使用 Pandas 向量化操作，避免循环
4. **模块化设计**: Spring 分级逻辑独立封装，易于测试和维护
5. **向后兼容**: 不破坏现有的 Spring 检测逻辑

### 1.3 架构决策

**决策 1: 创建独立的 Spring 分级模块**
- 理由: 保持代码模块化，便于测试和维护
- 位置: `user_data/strategies/wyckoff/spring_classifier.py`
- 职责: 专门负责 Spring 质量分级逻辑

**决策 2: 在 populate_indicators 中调用分级**
- 理由: Spring 分级是指标计算的一部分，不是信号生成
- 时机: 在轴线系统计算之后，入场信号生成之前

**决策 3: 使用字符串枚举表示质量等级**
- 理由: 比数字更直观，便于调试和日志输出
- 值: 'none', 'spring_1', 'spring_2', 'spring_3'

---

## 2. 系统架构

### 2.1 模块结构

```
user_data/strategies/
├── WyckoffV7_TrueMethod.py          # 主策略
└── wyckoff/
    ├── axis_system.py                # 轴线系统（已存在）
    ├── spring_classifier.py          # Spring 分级器（新增）
    └── __init__.py                   # 模块初始化
```

### 2.2 数据流

```
dataframe (原始数据)
    ↓
populate_indicators()
    ↓
axis_system.identify_axis_lines()  # 识别轴线
    ↓
spring_classifier.classify_spring_quality()  # Spring 分级
    ↓
dataframe (添加 spring_quality 等字段)
    ↓
populate_entry_trend()  # 使用分级信息生成入场信号
```

### 2.3 核心类设计

#### SpringClassifier 类

```python
class SpringClassifier:
    """
    威科夫 Spring 质量分级器
    
    职责：
    1. 检测 Spring 模式（跌破支撑后快速收回）
    2. 根据跌破深度和成交量分级
    3. 验证快速收回特征（5根K线内）
    4. 计算 Spring 质量评分
    """
```

---

## 3. 详细设计

### 3.1 Spring 分级算法

#### 3.1.1 输入数据


**必需字段**:
- `close`, `high`, `low`, `volume`: OHLCV 数据
- `nearest_support`: 最近的支撑轴线价位
- `atr`: 平均真实波幅
- `volume_sma_20`: 20周期成交量均值

**可选字段**:
- `spring_detected`: 现有的 Spring 检测结果（布尔值）

#### 3.1.2 核心算法流程

```python
def classify_spring_quality(dataframe: DataFrame) -> DataFrame:
    """
    Spring 质量分级主函数
    
    步骤：
    1. 检测跌破事件（low < nearest_support）
    2. 计算跌破深度（相对 ATR）
    3. 计算成交量比率（相对 SMA）
    4. 检测快速收回（5根K线内）
    5. 根据深度和成交量分级
    6. 添加质量字段到 dataframe
    """
    
    # Step 1: 初始化字段
    dataframe['spring_quality'] = 'none'
    dataframe['spring_breakout_depth'] = 0.0
    dataframe['spring_volume_ratio'] = 0.0
    dataframe['spring_recovery_speed'] = 0
    
    # Step 2: 检测跌破事件
    breakout_mask = (
        (dataframe['low'] < dataframe['nearest_support']) &
        (dataframe['nearest_support'] > 0)
    )
    
    # Step 3: 计算跌破深度（百分比）
    dataframe.loc[breakout_mask, 'spring_breakout_depth'] = (
        (dataframe.loc[breakout_mask, 'nearest_support'] - 
         dataframe.loc[breakout_mask, 'low']) / 
        dataframe.loc[breakout_mask, 'atr']
    )
    
    # Step 4: 计算成交量比率
    dataframe.loc[breakout_mask, 'spring_volume_ratio'] = (
        dataframe.loc[breakout_mask, 'volume'] / 
        dataframe.loc[breakout_mask, 'volume_sma_20']
    )
    
    # Step 5: 检测快速收回
    dataframe = _detect_quick_recovery(dataframe, breakout_mask)
    
    # Step 6: 分级
    dataframe = _assign_spring_grade(dataframe)
    
    return dataframe
```

#### 3.1.3 快速收回检测算法

**挑战**: 需要向前查找未来的K线，判断是否在5根K线内收回

**解决方案**: 使用滚动窗口 + shift 操作

```python
def _detect_quick_recovery(dataframe: DataFrame, breakout_mask: pd.Series) -> DataFrame:
    """
    检测快速收回特征
    
    定义：价格在5根K线内收回到支撑位之上
    
    算法：
    1. 对于每个跌破点，向前查看5根K线
    2. 检查是否有收盘价 >= 支撑位 * 0.995
    3. 记录收回速度（K线数量）
    """
    
    # 创建收回标记
    recovery_mask = (
        dataframe['close'] >= dataframe['nearest_support'] * 0.995
    )
    
    # 对于每个跌破点，检查未来5根K线
    for i in range(1, 6):
        # 向前查看 i 根K线
        future_recovery = recovery_mask.shift(-i)
        
        # 如果在 i 根K线内收回，记录速度
        quick_recovery = breakout_mask & future_recovery & (dataframe['spring_recovery_speed'] == 0)
        dataframe.loc[quick_recovery, 'spring_recovery_speed'] = i
    
    return dataframe
```

#### 3.1.4 分级标准

**Spring #1 - 低质量**:
```python
spring_1_conditions = (
    (dataframe['spring_breakout_depth'] > 0.02) &  # 深度跌破 > 2% ATR
    (dataframe['spring_volume_ratio'] > 1.5) &     # 极高成交量 > 1.5x
    (dataframe['spring_recovery_speed'] > 0) &     # 有收回
    (dataframe['spring_recovery_speed'] <= 5)      # 5根K线内
)
dataframe.loc[spring_1_conditions, 'spring_quality'] = 'spring_1'
```

**Spring #2 - 中等质量**:
```python
spring_2_conditions = (
    (dataframe['spring_breakout_depth'] >= 0.01) &  # 中度跌破 1-2% ATR
    (dataframe['spring_breakout_depth'] <= 0.02) &
    (dataframe['spring_volume_ratio'] >= 1.0) &     # 适度成交量 1.0-1.5x
    (dataframe['spring_volume_ratio'] <= 1.5) &
    (dataframe['spring_recovery_speed'] > 0) &
    (dataframe['spring_recovery_speed'] <= 5)
)
dataframe.loc[spring_2_conditions, 'spring_quality'] = 'spring_2'
```

**Spring #3 - 高质量** ⭐:
```python
spring_3_conditions = (
    (dataframe['spring_breakout_depth'] < 0.01) &   # 轻度跌破 < 1% ATR
    (dataframe['spring_volume_ratio'] < 0.8) &      # 极低成交量 < 0.8x
    (dataframe['spring_recovery_speed'] > 0) &
    (dataframe['spring_recovery_speed'] <= 5)
)
dataframe.loc[spring_3_conditions, 'spring_quality'] = 'spring_3'
```

### 3.2 入场条件设计

#### 3.2.1 Spring #3 入场（最高优先级）

**设计理念**: Spring #3 代表供应完全枯竭，是最高质量的买入信号，应该完全放开限制

**入场条件**:
```python
spring_3_entry = (
    # Spring #3 核心条件
    (dataframe['spring_quality'] == 'spring_3') &
    (dataframe['spring_recovery_speed'] <= 5) &
    
    # 最基本的威科夫过滤
    (dataframe['wyckoff_context'] != 'distribution') &  # 不在派发阶段
    
    # 最低防线（避免接自由落体）
    (dataframe['close'] > dataframe['ema_200'] * 0.95) &  # 允许低于EMA200 5%
    
    # 移除的限制（理论依据）：
    # ❌ bull_market: Spring 创造牛市，不是牛市的结果
    # ❌ trend_support: Phase C 通常在趋势支撑之下
    # ❌ high confidence: Spring #3 本身就是最高置信度
)
```

**理论依据**:
1. **"Spring 创造牛市，不是牛市的结果"**: 移除 bull_market 限制
2. **"价格停留的时间越短，洗盘的力量越强"**: 5根K线限制
3. **"轻微破位 + 极低成交量 = 供应枯竭"**: Spring #3 的核心定义
4. **"背景大于一切"**: 保留 not_distribution 过滤

#### 3.2.2 Spring #2 入场（中等优先级）

**设计理念**: Spring #2 需要二次测试确认，确保不是假突破

**入场条件**:
```python
spring_2_entry = (
    # Spring #2 核心条件
    (dataframe['spring_quality'] == 'spring_2') &
    (dataframe['spring_recovery_speed'] <= 5) &
    
    # 二次测试确认
    (dataframe['axis_test_type'] == 'normal') &  # 正在测试支撑
    (dataframe['test_confidence'] > 0.4) &       # 测试置信度
    
    # 成交量萎缩确认（No Supply）
    (dataframe['volume'] < dataframe['volume_sma_20'] * 0.9) &
    
    # 威科夫基础条件
    (dataframe['wyckoff_context'] != 'distribution') &
    (dataframe['close'] > dataframe['ema_200'] * 0.98) &  # 略严格
)
```

#### 3.2.3 Spring #1 入场（低优先级）

**设计理念**: Spring #1 风险最高，需要放量回升 + 二次测试双重确认

**入场条件**:
```python
spring_1_entry = (
    # Spring #1 核心条件
    (dataframe['spring_quality'] == 'spring_1') &
    (dataframe['spring_recovery_speed'] <= 5) &
    
    # 放量回升确认
    (dataframe['volume'] > dataframe['volume_sma_20'] * 1.2) &  # 成交量回升
    (dataframe['close'] > dataframe['open']) &                  # 阳线
    
    # 二次测试确认
    (dataframe['axis_test_type'] == 'normal') &
    (dataframe['test_confidence'] > 0.5) &  # 更高置信度
    
    # 威科夫基础条件（最严格）
    (dataframe['wyckoff_context'] != 'distribution') &
    (dataframe['close'] > dataframe['ema_200']) &  # 必须在EMA200之上
)
```

### 3.3 集成到主策略

#### 3.3.1 populate_indicators 修改

```python
def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
    # ... 现有指标计算 ...
    
    # Phase 2: 轴线系统
    if self.axis_system:
        dataframe = self.axis_system.identify_axis_lines(dataframe)
        dataframe = self.axis_system.calculate_axis_strength_tier(dataframe)
    
    # Phase 2.2a: Spring 质量分级（新增）
    if self.spring_classifier:
        dataframe = self.spring_classifier.classify_spring_quality(dataframe)
    
    # ... 其他指标 ...
    
    return dataframe
```

#### 3.3.2 populate_entry_trend 修改

```python
def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
    # ... 现有入场信号 ...
    
    # Phase 2.2a: Spring 分级入场（新增）
    spring_3_entry = (
        (dataframe['spring_quality'] == 'spring_3') &
        (dataframe['spring_recovery_speed'] <= 5) &
        (dataframe['wyckoff_context'] != 'distribution') &
        (dataframe['close'] > dataframe['ema_200'] * 0.95)
    )
    
    spring_2_entry = (
        (dataframe['spring_quality'] == 'spring_2') &
        (dataframe['spring_recovery_speed'] <= 5) &
        (dataframe['axis_test_type'] == 'normal') &
        (dataframe['test_confidence'] > 0.4) &
        (dataframe['volume'] < dataframe['volume_sma_20'] * 0.9) &
        (dataframe['wyckoff_context'] != 'distribution') &
        (dataframe['close'] > dataframe['ema_200'] * 0.98)
    )
    
    spring_1_entry = (
        (dataframe['spring_quality'] == 'spring_1') &
        (dataframe['spring_recovery_speed'] <= 5) &
        (dataframe['volume'] > dataframe['volume_sma_20'] * 1.2) &
        (dataframe['close'] > dataframe['open']) &
        (dataframe['axis_test_type'] == 'normal') &
        (dataframe['test_confidence'] > 0.5) &
        (dataframe['wyckoff_context'] != 'distribution') &
        (dataframe['close'] > dataframe['ema_200'])
    )
    
    # 合并 Spring 入场信号
    spring_entry = spring_3_entry | spring_2_entry | spring_1_entry
    
    # 添加到最终入场条件
    final_entry = (
        # ... 现有信号 ...
        | spring_entry
    )
    
    # 入场标签（优先级：Spring #3 > #2 > #1）
    dataframe.loc[spring_1_entry & final_entry, 'enter_tag'] = 'wyckoff_spring_1'
    dataframe.loc[spring_2_entry & final_entry, 'enter_tag'] = 'wyckoff_spring_2'
    dataframe.loc[spring_3_entry & final_entry, 'enter_tag'] = 'wyckoff_spring_3'  # 最高优先级
    
    return dataframe
```

---

## 4. 数据结构设计

### 4.1 新增 DataFrame 列

| 列名 | 类型 | 说明 | 默认值 |
|------|------|------|--------|
| `spring_quality` | string | Spring 质量等级 | 'none' |
| `spring_breakout_depth` | float | 跌破深度（相对ATR） | 0.0 |
| `spring_volume_ratio` | float | 成交量比率 | 0.0 |
| `spring_recovery_speed` | int | 收回速度（K线数） | 0 |

### 4.2 配置参数

```python
# Spring 分级阈值（可通过 hyperopt 优化）
spring_depth_threshold_1 = 0.02  # Spring #1 深度阈值
spring_depth_threshold_2 = 0.01  # Spring #2 深度阈值
spring_volume_threshold_high = 1.5  # 极高成交量阈值
spring_volume_threshold_low = 0.8   # 极低成交量阈值
spring_recovery_window = 5  # 快速收回窗口（K线数）
```

---

## 5. 性能优化

### 5.1 向量化操作

**避免循环**:
```python
# ❌ 错误：使用循环
for i in range(len(dataframe)):
    if dataframe.iloc[i]['low'] < dataframe.iloc[i]['nearest_support']:
        # ... 逐行处理 ...

# ✅ 正确：使用向量化
breakout_mask = dataframe['low'] < dataframe['nearest_support']
dataframe.loc[breakout_mask, 'spring_breakout_depth'] = (...)
```

### 5.2 条件短路

**优先计算简单条件**:
```python
# 先检查简单条件，减少复杂计算
spring_3_conditions = (
    (dataframe['spring_quality'] == 'spring_3') &  # 简单字符串比较
    (dataframe['spring_recovery_speed'] <= 5) &    # 简单数值比较
    # ... 复杂条件放后面 ...
)
```

### 5.3 缓存机制

**缓存中间结果**:
```python
# 缓存成交量均值，避免重复计算
if 'volume_sma_20' not in dataframe.columns:
    dataframe['volume_sma_20'] = dataframe['volume'].rolling(20).mean()
```

---

## 6. 测试策略

### 6.1 单元测试

**测试用例**:
1. **Spring #3 识别测试**: 轻度跌破 + 极低量 → 应识别为 Spring #3
2. **Spring #2 识别测试**: 中度跌破 + 适度量 → 应识别为 Spring #2
3. **Spring #1 识别测试**: 深度跌破 + 极高量 → 应识别为 Spring #1
4. **快速收回测试**: 5根K线内收回 → spring_recovery_speed <= 5
5. **慢速收回测试**: 超过5根K线 → spring_quality = 'none'
6. **边界条件测试**: 恰好在阈值边界的情况

### 6.2 集成测试

**测试场景**:
1. **完整数据流测试**: 从原始数据到 Spring 分级到入场信号
2. **与轴线系统集成**: 确保 nearest_support 正确传递
3. **多信号共振测试**: Spring + SOS 共振情况

### 6.3 回测验证

**验证指标**:
- Spring #3 信号数量 > 5个
- Spring #3 胜率 > 85%
- Spring #2 胜率 > 75%
- Spring #1 胜率 > 65%
- 总信号释放率提升 > 20%

---

## 7. 错误处理

### 7.1 数据缺失处理

```python
# 检查必需字段
required_fields = ['close', 'high', 'low', 'volume', 'nearest_support', 'atr']
missing_fields = [f for f in required_fields if f not in dataframe.columns]

if missing_fields:
    logger.warning(f"Spring 分级: 缺少必需字段 {missing_fields}，跳过分级")
    return dataframe
```

### 7.2 异常值处理

```python
# 处理 ATR 为 0 的情况
dataframe['atr'] = dataframe['atr'].replace(0, np.nan)
dataframe['atr'] = dataframe['atr'].fillna(method='ffill')

# 处理成交量为 0 的情况
dataframe['volume_sma_20'] = dataframe['volume_sma_20'].replace(0, 1)
```

### 7.3 日志输出

```python
# 调试日志
spring_3_count = (dataframe['spring_quality'] == 'spring_3').sum()
spring_2_count = (dataframe['spring_quality'] == 'spring_2').sum()
spring_1_count = (dataframe['spring_quality'] == 'spring_1').sum()

if spring_3_count > 0 or spring_2_count > 0 or spring_1_count > 0:
    logger.info(f"Spring 分级统计: #3={spring_3_count}, #2={spring_2_count}, #1={spring_1_count}")
```

---

## 8. 部署计划

### 8.1 实施步骤

**Step 1: 创建 SpringClassifier 模块**
- 文件: `user_data/strategies/wyckoff/spring_classifier.py`
- 实现: `classify_spring_quality()` 方法
- 测试: 单元测试验证分级逻辑

**Step 2: 集成到主策略**
- 修改: `WyckoffV7_TrueMethod.py`
- 位置: `populate_indicators()` 方法
- 初始化: `self.spring_classifier = SpringClassifier()`

**Step 3: 添加入场条件**
- 修改: `populate_entry_trend()` 方法
- 添加: Spring #3/#2/#1 入场逻辑
- 标签: 'wyckoff_spring_3', 'wyckoff_spring_2', 'wyckoff_spring_1'

**Step 4: 回测验证**
- 命令: `docker-compose run --rm freqtrade backtesting ...`
- 验证: 信号数量、胜率、收益率
- 调整: 根据回测结果微调阈值

### 8.2 回滚方案

**功能开关**:
```python
# 在策略类中添加开关
enable_spring_grading = True  # 可以快速禁用

if self.enable_spring_grading and self.spring_classifier:
    dataframe = self.spring_classifier.classify_spring_quality(dataframe)
```

**降级策略**:
- 如果 Spring 分级失败，回退到原有的 `spring_detected` 布尔逻辑
- 保留原有的 Spring 入场条件作为备份

---

## 9. 监控与调优

### 9.1 关键指标

**分级质量指标**:
- Spring #3 识别准确率
- Spring #2 识别准确率
- Spring #1 识别准确率
- 误判率（非 Spring 被识别为 Spring）

**交易性能指标**:
- Spring #3 信号胜率
- Spring #2 信号胜率
- Spring #1 信号胜率
- 平均持仓时间
- 平均收益率

### 9.2 参数优化

**可优化参数**:
```python
# 使用 hyperopt 优化
spring_depth_threshold_1 = DecimalParameter(0.015, 0.025, default=0.02, space='buy')
spring_depth_threshold_2 = DecimalParameter(0.008, 0.012, default=0.01, space='buy')
spring_volume_threshold_high = DecimalParameter(1.3, 1.7, default=1.5, space='buy')
spring_volume_threshold_low = DecimalParameter(0.7, 0.9, default=0.8, space='buy')
```

---

## 10. 文档与维护

### 10.1 代码注释

**函数文档字符串**:
```python
def classify_spring_quality(self, dataframe: DataFrame) -> DataFrame:
    """
    威科夫 Spring 质量分级
    
    根据跌破深度和成交量特征，将 Spring 分为三个质量等级：
    - Spring #1: 深度跌破 + 极高量（低质量，需要二次确认）
    - Spring #2: 中度跌破 + 适度量（中等质量，需要测试确认）
    - Spring #3: 轻度跌破 + 极低量（高质量，直接入场）
    
    理论依据：
    - "价格停留的时间越短，洗盘的力量越强"
    - "Spring 创造牛市，不是牛市的结果"
    - "轻微破位 + 极低成交量 = 供应枯竭"
    
    Args:
        dataframe: 包含 OHLCV 和轴线信息的 DataFrame
        
    Returns:
        DataFrame: 添加了 spring_quality 等字段的 DataFrame
        
    新增字段:
        - spring_quality: 'none', 'spring_1', 'spring_2', 'spring_3'
        - spring_breakout_depth: 跌破深度（相对ATR）
        - spring_volume_ratio: 成交量比率
        - spring_recovery_speed: 收回速度（K线数）
    """
```

### 10.2 更新日志

**版本记录**:
- v1.0.0 (2026-02-08): 初始实现，支持三级分类
- v1.1.0 (待定): 添加 hyperopt 参数优化
- v1.2.0 (待定): 优化快速收回检测算法

---

## 11. 风险与缓解

### 11.1 技术风险

**风险 1: 快速收回检测性能问题**
- 描述: 向前查找5根K线可能影响性能
- 缓解: 使用向量化 shift 操作，避免循环
- 监控: 测量 populate_indicators 执行时间

**风险 2: 边界条件误判**
- 描述: 恰好在阈值边界的 Spring 可能误判
- 缓解: 添加容差范围，使用 >= 和 <= 而非 > 和 <
- 监控: 记录边界条件的分级结果

### 11.2 业务风险

**风险 1: Spring #3 信号过少**
- 描述: 阈值过严导致识别数量不足
- 缓解: 通过 hyperopt 优化阈值
- 监控: 统计每个等级的信号数量

**风险 2: Spring #3 胜率不达标**
- 描述: 放开限制后胜率下降
- 缓解: 保留最低防线 `close > ema_200 * 0.95`
- 监控: 实时跟踪 Spring #3 的胜率

---

## 12. 成功标准

### 12.1 功能完整性

- [x] Spring 被正确分为三个等级
- [x] 快速收回验证正常工作
- [x] 新增字段正确映射到 dataframe
- [x] 入场条件正确实现

### 12.2 性能指标

- [ ] Spring #3 识别准确率 > 80%
- [ ] Spring #3 信号数量 > 5个
- [ ] Spring #3 胜率 > 85%
- [ ] 总信号释放率提升 > 20%
- [ ] 总收益提升 > 5%

### 12.3 代码质量

- [ ] 代码通过单元测试
- [ ] 代码符合 PEP 8 规范
- [ ] 函数有完整的文档字符串
- [ ] 关键逻辑有注释说明

---

## 13. 附录

### 13.1 威科夫理论参考

**Spring 的定义**:
> Spring 是威科夫 Phase C（测试阶段）的关键事件，表现为价格轻微跌破支撑位后迅速收回，目的是"洗盘"并测试供应是否枯竭。

**核心原则**:
1. "价格停留的时间越短，洗盘的力量越强"
2. "Spring 创造牛市，不是牛市的结果"
3. "轻微破位 + 极低成交量 = 供应枯竭的证据"

### 13.2 参考文档

- **威科夫交易方法完整学习指南.md** (4345行开始)
- **AXIS_OPTIMIZATION_PHASE2_PLAN.md**
- **PHASE2_1_IMPLEMENTATION_SUMMARY.md**
- **TASKS.md** - Task 2.1

---

**创建时间**: 2026-02-08  
**最后更新**: 2026-02-08  
**状态**: 设计完成，待审核
