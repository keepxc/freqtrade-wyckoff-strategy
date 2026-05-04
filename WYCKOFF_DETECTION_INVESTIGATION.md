# Wyckoff 结构检测失效调查报告

## 问题定义

根据回测日志分析，发现一个严重问题：

```
PhaseContext: TR_精确 = 0   ← 死路
ClimaticAction: SC = 0, BC = 0  ← 死路
rolling_window: 120根极值     ← 唯一生效路径
Wyckoff 结构占比: 0%        ← 从未启用
```

**核心发现**：策略实际上是一个纯价格极值的 PnF 系统，Wyckoff 层完全是摆设。

4年回测期间（2022-01-01 至 2026-05-02），Wyckoff 结构检测从未触发过一次。

## 可能原因（按概率排序）

### 1. PhaseContext 检测逻辑存在 bug（最可能）

**症状**：`tr_precise` 字段从未被写入

**可能原因**：
- 条件判断过于严格，永远返回空
- 字段名拼写错误
- 返回值没有被正确赋值
- 代码路径存在但永远不会走到赋值语句

**验证方法**：
- 在 `PhaseContext.compute()` 的每个分支末尾加 `logger.debug`
- 确认代码究竟走到哪一步停住

### 2. SC/BC 检测阈值过于严格（高概率）

**症状**：气候性行动（Climatic Action）的量/价阈值设置过高

**可能原因**：
- BTC 的历史行情从未满足条件
- SC 锚点路径在代码里存在，但实际上是一段死代码

**当前阈值**：
```python
# SC 检测参数
sc_lookback = 60          # 新低判断回看窗口
sc_trend_bars = 20        # 趋势确认窗口
sc_trend_min_drop = 0.05  # 趋势确认最小跌幅（5%）
sc_volume_mult = 2.0      # 成交量倍数门槛
sc_atr_mult = 1.5         # ATR宽幅门槛
sc_close_pos_min = 0.30   # 最低收盘位置

# BC 检测参数
bc_lookback = 60
bc_trend_bars = 20
bc_trend_min_rise = 0.05  # 趋势确认最小涨幅（5%）
bc_volume_mult = 2.0
bc_atr_mult = 1.5
bc_close_pos_max = 0.70   # 最高收盘位置
```

**验证方法**：
- 打印 SC/BC 检测时的实际候选值 vs 阈值
- 确认是"完全没进入检测循环"还是"进入了但每次都差一点"

### 3. 日志统计的字段 ≠ 实际执行路径的字段（中等概率）

**症状**：之前"30.3% PhaseContext"是统计了日志中某个中间计算字段

**可能原因**：
- 统计对象和执行对象脱节
- 真正触发 TR 精确赋值的路径从未被统计

### 4. PhaseContext 对象被正确计算，但 TR 结果未向上传递（低概率）

**症状**：PhaseContext 内部逻辑运行正常，但返回值在调用层被忽略

**可能原因**：
- fallback 直接走了 rolling_window
- 导致计算了但没用

## 立即需要做的三件事

### ① 在 PhaseContext.compute() 的每个分支末尾加 logger.debug

**目标**：确认代码究竟走到哪一步停住

**实施**：
- 是条件判断失败？
- 还是赋值后字段没读到？

### ② 打印 SC/BC 检测时的实际候选值 vs 阈值

**目标**：确认是"完全没进入检测循环"还是"进入了但每次都差一点"

**实施**：
- 使用 `wyckoff/climatic_action_detector_diagnostic.py`
- 或运行 `diagnose_wyckoff_detection.py`

### ③ 在 PnF target 计算处加日志

**目标**：等下一笔入场触发，直接读到真实路径

**实施**：
```python
logger.info(f"TR来源={source}, low={tr_low}, high={tr_high}")
```

## 核心问题重新定义

之前所有关于"axis_mult 调优"、"TR 窗口扩大"的讨论，都建立在"Wyckoff 结构检测是生效的"这个**错误前提**上。

现在明确了——策略实际上是一个纯价格极值的 PnF 系统，Wyckoff 层完全是摆设。

**修复的真正问题变成**：

> Wyckoff 结构检测为什么从未触发？

而不是如何调参。

## 两种可能性及处理方式

### 可能性 A：代码 bug

**特征**：
- PhaseContext 或 SC 检测里有条件永远为 False
- 或者赋值结果被后面的代码覆盖掉了

**处理方式**：
- 加几行日志就能定位
- 然后修代码

### 可能性 B：设计问题

**特征**：
- 阈值本身就不符合 BTC 1H 图的特性
- BTC 的"气候性行动"可能在日图/周图尺度才显著
- 1H 图的量价噪声导致检测条件永远无法满足

**处理方式**：
- 需要重新校准阈值
- 甚至重新思考在什么时间框架上做 Wyckoff 检测

## 建议先从最快能验证的入手

把 SC/BC 检测的阈值判断条件和实际候选值都打出来，看是差一点还是差很远：

- **差一点**：是阈值问题
- **差很远**：是结构问题

## 诊断工具

### 1. 诊断脚本

```bash
python diagnose_wyckoff_detection.py
```

功能：
- 分析每个 SC/BC 条件的通过率
- 找出"接近但未触发"的候选K线
- 输出实际值 vs 阈值的统计

### 2. 诊断版检测器

在策略中临时替换：

```python
# 原来
from wyckoff.climatic_action_detector import ClimaticActionDetector

# 改为
from wyckoff.climatic_action_detector_diagnostic import ClimaticActionDetector
```

然后运行回测，查看详细的条件通过率日志。

### 3. 在策略中添加临时日志

在 `populate_indicators` 中添加：

```python
# 在 ClimaticActionDetector.detect() 之后
if 'climatic_type' in dataframe.columns:
    sc_count = (dataframe['climatic_type'] == 'SC').sum()
    bc_count = (dataframe['climatic_type'] == 'BC').sum()
    logger.info(f"[诊断] SC检测到 {sc_count} 次, BC检测到 {bc_count} 次")
    
    # 打印一些候选值统计
    if 'volume_sma_20' in dataframe.columns:
        volume_ratio = dataframe['volume'] / dataframe['volume_sma_20']
        logger.info(f"[诊断] 成交量倍数: max={volume_ratio.max():.2f}x, mean={volume_ratio.mean():.2f}x")
    
    if 'bar_range' in dataframe.columns and 'atr_20' in dataframe.columns:
        range_ratio = dataframe['bar_range'] / dataframe['atr_20']
        logger.info(f"[诊断] 宽幅倍数: max={range_ratio.max():.2f}x, mean={range_ratio.mean():.2f}x")
```

## 下一步行动

1. **立即执行**：运行诊断版检测器，获取详细的条件通过率
2. **分析结果**：确认是哪个条件成为瓶颈
3. **决策分支**：
   - 如果是代码bug → 修复代码
   - 如果是阈值过严 → 校准阈值（可能需要降低到 1.5x 成交量，1.2x ATR）
   - 如果是时间框架问题 → 考虑在日线图上做 Wyckoff 检测，1H 图只做执行

## 预期发现

根据经验判断，最可能的情况是：

**成交量和宽幅条件过于严格**

BTC 1H 图上：
- 成交量 > 2.0x 均量的情况可能很少（可能只有 5-10% 的K线）
- 宽幅 > 1.5x ATR 的情况也可能很少
- 两者同时满足的情况更少
- 再加上趋势前提、新高新低、收盘位置等条件，最终通过率接近 0

**建议的阈值调整方向**：
- 成交量倍数：2.0x → 1.5x
- 宽幅倍数：1.5x → 1.2x
- 趋势确认跌幅/涨幅：5% → 3%

但在调整之前，**必须先运行诊断，用数据说话**。

## 参考文件

- `wyckoff/climatic_action_detector.py` - 原始检测器
- `wyckoff/climatic_action_detector_diagnostic.py` - 诊断版检测器
- `wyckoff/phase_context_detector.py` - Phase 上下文检测器
- `diagnose_wyckoff_detection.py` - 独立诊断脚本
- `user_data/strategies/WyckoffV7_TrueMethod.py` - 主策略文件

## 总结

这个发现改变了整个问题定义。之前所有讨论（axis_mult、窗口大小、元数据链锁定）都在调一个实际上从未工作过的系统。

真正要回答的问题只有一个：

> **Wyckoff 检测层为什么 4 年内一次都没触发？**

现在有了诊断工具，可以快速定位问题所在。
