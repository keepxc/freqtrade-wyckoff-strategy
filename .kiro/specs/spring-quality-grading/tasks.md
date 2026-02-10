# Spring 质量分级系统 - 任务列表

**Feature**: Spring Quality Grading System  
**创建日期**: 2026-02-08  
**优先级**: P0 - 最高优先级  
**状态**: 待开始

---

## 任务概览

**总任务数**: 15  
**已完成**: 0  
**进行中**: 0  
**待开始**: 15

---

## 1. 准备工作

- [x] 1.1 创建 SpringClassifier 模块文件
- [x] 1.2 添加必要的导入和依赖
- [x] 1.3 定义 SpringClassifier 类结构

---

## 2. 核心算法实现

- [x] 2.1 实现 classify_spring_quality() 主函数
- [x] 2.2 实现跌破事件检测逻辑
- [x] 2.3 实现跌破深度计算（相对ATR）
- [x] 2.4 实现成交量比率计算
- [x] 2.5 实现快速收回检测算法
- [x] 2.6 实现 Spring 分级逻辑

---

## 3. 集成到主策略

- [x] 3.1 在主策略中初始化 SpringClassifier
- [x] 3.2 在 populate_indicators 中调用分级方法
- [x] 3.3 在 populate_entry_trend 中添加 Spring #3 入场条件
- [x] 3.4 在 populate_entry_trend 中添加 Spring #2 入场条件
- [x] 3.5 在 populate_entry_trend 中添加 Spring #1 入场条件
- [x] 3.6 添加入场标签（enter_tag）

---

## 4. 测试与验证

- [x] 4.1 运行回测验证功能
- [x] 4.2 验证 Spring 分级准确性
- [x] 4.3 验证信号数量和胜率

---

## 详细任务说明

### Task 1.1: 创建 SpringClassifier 模块文件

**描述**: 创建 `user_data/strategies/wyckoff/spring_classifier.py` 文件

**验收标准**:
- 文件创建成功
- 包含基本的类结构
- 包含必要的导入语句

**预计时间**: 5分钟

---

### Task 1.2: 添加必要的导入和依赖

**描述**: 添加 Pandas, NumPy, logging 等必要的导入

**验收标准**:
- 所有必要的库都已导入
- 没有未使用的导入

**预计时间**: 5分钟

---

### Task 1.3: 定义 SpringClassifier 类结构

**描述**: 定义 SpringClassifier 类，包含初始化方法和主要方法签名

**验收标准**:
- 类定义正确
- 包含 `__init__` 方法
- 包含 `classify_spring_quality` 方法签名

**预计时间**: 10分钟

---

### Task 2.1: 实现 classify_spring_quality() 主函数

**描述**: 实现 Spring 质量分级的主函数，协调所有子步骤

**验收标准**:
- 函数接受 DataFrame 参数
- 返回添加了新字段的 DataFrame
- 包含完整的文档字符串

**预计时间**: 15分钟

---

### Task 2.2: 实现跌破事件检测逻辑

**描述**: 检测价格跌破支撑轴线的事件

**实现要点**:
```python
breakout_mask = (
    (dataframe['low'] < dataframe['nearest_support']) &
    (dataframe['nearest_support'] > 0)
)
```

**验收标准**:
- 正确识别跌破事件
- 使用向量化操作
- 处理 nearest_support 为 0 的情况

**预计时间**: 10分钟

---

### Task 2.3: 实现跌破深度计算（相对ATR）

**描述**: 计算跌破深度，以 ATR 为单位

**实现要点**:
```python
dataframe['spring_breakout_depth'] = (
    (dataframe['nearest_support'] - dataframe['low']) / dataframe['atr']
)
```

**验收标准**:
- 深度计算正确
- 处理 ATR 为 0 的情况
- 结果为正数

**预计时间**: 10分钟

---

### Task 2.4: 实现成交量比率计算

**描述**: 计算跌破时的成交量相对于均值的比率

**实现要点**:
```python
dataframe['spring_volume_ratio'] = (
    dataframe['volume'] / dataframe['volume_sma_20']
)
```

**验收标准**:
- 比率计算正确
- 处理成交量为 0 的情况
- 结果为正数

**预计时间**: 10分钟

---

### Task 2.5: 实现快速收回检测算法

**描述**: 检测价格是否在5根K线内收回到支撑位之上

**实现要点**:
```python
for i in range(1, 6):
    future_recovery = recovery_mask.shift(-i)
    quick_recovery = breakout_mask & future_recovery & (dataframe['spring_recovery_speed'] == 0)
    dataframe.loc[quick_recovery, 'spring_recovery_speed'] = i
```

**验收标准**:
- 正确检测5根K线内的收回
- 记录收回速度（K线数）
- 超过5根K线的不记录

**预计时间**: 20分钟

---

### Task 2.6: 实现 Spring 分级逻辑

**描述**: 根据跌破深度和成交量比率，将 Spring 分为三个等级

**实现要点**:
- Spring #1: depth > 0.02 & volume > 1.5
- Spring #2: 0.01 <= depth <= 0.02 & 1.0 <= volume <= 1.5
- Spring #3: depth < 0.01 & volume < 0.8

**验收标准**:
- 分级逻辑正确
- 使用向量化操作
- 优先级正确（避免重复分级）

**预计时间**: 15分钟

---

### Task 3.1: 在主策略中初始化 SpringClassifier

**描述**: 在 WyckoffV7_TrueMethod 的 `__init__` 方法中初始化 SpringClassifier

**实现要点**:
```python
from .wyckoff.spring_classifier import SpringClassifier

def __init__(self, config: dict) -> None:
    super().__init__(config)
    # ... 现有初始化 ...
    self.spring_classifier = SpringClassifier(self)
```

**验收标准**:
- 导入语句正确
- 初始化成功
- 不影响现有功能

**预计时间**: 5分钟

---

### Task 3.2: 在 populate_indicators 中调用分级方法

**描述**: 在指标计算阶段调用 Spring 分级方法

**实现要点**:
```python
# Phase 2.2a: Spring 质量分级
if self.spring_classifier:
    dataframe = self.spring_classifier.classify_spring_quality(dataframe)
```

**验收标准**:
- 调用位置正确（在轴线系统之后）
- 有错误处理
- 添加调试日志

**预计时间**: 10分钟

---

### Task 3.3: 在 populate_entry_trend 中添加 Spring #3 入场条件

**描述**: 实现 Spring #3 的入场逻辑（最高优先级）

**实现要点**:
```python
spring_3_entry = (
    (dataframe['spring_quality'] == 'spring_3') &
    (dataframe['spring_recovery_speed'] <= 5) &
    (dataframe['wyckoff_context'] != 'distribution') &
    (dataframe['close'] > dataframe['ema_200'] * 0.95)
)
```

**验收标准**:
- 条件逻辑正确
- 移除了 bull_market 和 trend_support 限制
- 保留了最低防线

**预计时间**: 15分钟

---

### Task 3.4: 在 populate_entry_trend 中添加 Spring #2 入场条件

**描述**: 实现 Spring #2 的入场逻辑（中等优先级）

**实现要点**:
```python
spring_2_entry = (
    (dataframe['spring_quality'] == 'spring_2') &
    (dataframe['spring_recovery_speed'] <= 5) &
    (dataframe['axis_test_type'] == 'normal') &
    (dataframe['test_confidence'] > 0.4) &
    (dataframe['volume'] < dataframe['volume_sma_20'] * 0.9) &
    (dataframe['wyckoff_context'] != 'distribution') &
    (dataframe['close'] > dataframe['ema_200'] * 0.98)
)
```

**验收标准**:
- 条件逻辑正确
- 包含二次测试确认
- 包含成交量萎缩确认

**预计时间**: 15分钟

---

### Task 3.5: 在 populate_entry_trend 中添加 Spring #1 入场条件

**描述**: 实现 Spring #1 的入场逻辑（低优先级）

**实现要点**:
```python
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
```

**验收标准**:
- 条件逻辑正确
- 包含放量回升确认
- 包含二次测试确认
- 条件最严格

**预计时间**: 15分钟

---

### Task 3.6: 添加入场标签（enter_tag）

**描述**: 为不同等级的 Spring 入场添加标签

**实现要点**:
```python
dataframe.loc[spring_1_entry & final_entry, 'enter_tag'] = 'wyckoff_spring_1'
dataframe.loc[spring_2_entry & final_entry, 'enter_tag'] = 'wyckoff_spring_2'
dataframe.loc[spring_3_entry & final_entry, 'enter_tag'] = 'wyckoff_spring_3'
```

**验收标准**:
- 标签优先级正确（#3 > #2 > #1）
- 标签名称清晰
- 不覆盖其他信号的标签

**预计时间**: 10分钟

---

### Task 4.1: 运行回测验证功能

**描述**: 运行完整回测，验证 Spring 分级系统是否正常工作

**回测命令**:
```bash
docker-compose run --rm freqtrade backtesting \
  --strategy WyckoffV7_TrueMethod \
  --timerange 20230101-20251231 \
  --config user_data/config_backtest.json \
  --cache none
```

**验收标准**:
- 回测成功完成
- 没有错误或警告
- 生成回测报告

**预计时间**: 10分钟

---

### Task 4.2: 验证 Spring 分级准确性

**描述**: 检查 Spring 分级的准确性和合理性

**验证内容**:
- Spring #3 信号数量 > 5个
- Spring #2 信号数量合理
- Spring #1 信号数量合理
- 分级逻辑符合威科夫理论

**验收标准**:
- 分级数量合理
- 没有明显误判
- 日志输出清晰

**预计时间**: 15分钟

---

### Task 4.3: 验证信号数量和胜率

**描述**: 验证 Spring 入场信号的数量和胜率是否达标

**验证指标**:
- Spring #3 胜率 > 85%
- Spring #2 胜率 > 75%
- Spring #1 胜率 > 65%
- 总信号释放率提升 > 20%
- 总收益提升 > 5%

**验收标准**:
- 所有指标达标
- 回测报告详细
- 性能符合预期

**预计时间**: 20分钟

---

## 总预计时间

**开发时间**: 约 3-4 小时  
**测试时间**: 约 1 小时  
**总计**: 约 4-5 小时

---

## 依赖关系

```
1.1 → 1.2 → 1.3
         ↓
2.1 → 2.2 → 2.3 → 2.4 → 2.5 → 2.6
                              ↓
                        3.1 → 3.2
                              ↓
                        3.3 → 3.4 → 3.5 → 3.6
                                          ↓
                                    4.1 → 4.2 → 4.3
```

---

## 风险与缓解

**风险 1: 快速收回检测性能问题**
- 缓解: 使用向量化操作，避免循环
- 监控: 测量执行时间

**风险 2: Spring #3 信号过少**
- 缓解: 通过 hyperopt 优化阈值
- 监控: 统计信号数量

**风险 3: 胜率不达标**
- 缓解: 保留最低防线，调整过滤条件
- 监控: 实时跟踪胜率

---

**创建时间**: 2026-02-08  
**最后更新**: 2026-02-08  
**状态**: 待开始
