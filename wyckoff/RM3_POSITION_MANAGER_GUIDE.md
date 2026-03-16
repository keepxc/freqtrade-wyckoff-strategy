# RM-3: 动态仓位管理系统使用指南

## 概述

动态仓位管理系统（Position Manager）是 RM-3 的核心实现，旨在建立真正与交易质量一致的仓位框架。

## 核心原则

1. **质量优先**：仓位大小必须与信号质量对齐
2. **防守优先**：先做减仓防守，再考虑进攻加仓
3. **验证优先**：必须先验证质量映射，再启用动态仓位
4. **可回滚**：必须有开关，默认关闭

## 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                    Position Manager                          │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  1. 统一质量评分 (Unified Quality Score)                     │
│     ├─ Spring #3/2/1 → 95/75/55                             │
│     ├─ SOS → sos_quality_score                              │
│     ├─ Axis → axis_strength_tier + test_confidence          │
│     ├─ Phase D → phase_d_quality_score                      │
│     └─ JAC → jac_quality_score                              │
│                                                               │
│  2. 质量分层 (Quality Tier)                                  │
│     ├─ Exceptional (90-100): Spring #3, Strong Axis + JAC   │
│     ├─ High (70-89): Spring #2, Medium Axis, High SOS       │
│     ├─ Medium (50-69): Standard signals                      │
│     ├─ Low (30-49): Weak signals                            │
│     └─ Very Low (0-29): Marginal signals                    │
│                                                               │
│  3. 仓位映射 (Position Mapping) - 防守型                     │
│     ├─ Exceptional → 1.0x (暂不放大)                        │
│     ├─ High → 1.0x                                           │
│     ├─ Medium → 0.8x                                         │
│     ├─ Low → 0.5x                                            │
│     └─ Very Low → 0.3x                                       │
│                                                               │
│  4. 环境调整 (Environment Adjustment)                        │
│     ├─ Markdown → 0.7x                                       │
│     ├─ Consecutive Loss → 0.6x                              │
│     └─ Climatic Action → 0.5x                               │
│                                                               │
│  5. 风险控制 (Risk Control)                                  │
│     ├─ Min Position: 0.2x                                    │
│     └─ Max Position: 1.0x (暂不放大)                        │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

## 使用流程

### 阶段 1：质量映射验证（必须先完成）

在启用动态仓位前，必须先验证质量档位与真实绩效的映射关系。

#### 1.1 运行回测（收集数据）

```bash
# 全量回测
docker compose run --rm freqtrade backtesting \
  --config user_data/config_backtest.json \
  --strategy WyckoffV7_TrueMethod \
  --timerange 20230101-20260228 \
  --cache none \
  --export trades

# 三段验证（推荐）
# 牛市段
docker compose run --rm freqtrade backtesting \
  --config user_data/config_backtest.json \
  --strategy WyckoffV7_TrueMethod \
  --timerange 20230101-20240630 \
  --cache none \
  --export trades

# 调整段
docker compose run --rm freqtrade backtesting \
  --config user_data/config_backtest.json \
  --strategy WyckoffV7_TrueMethod \
  --timerange 20240701-20250630 \
  --cache none \
  --export trades

# 熊市段
docker compose run --rm freqtrade backtesting \
  --config user_data/config_backtest.json \
  --strategy WyckoffV7_TrueMethod \
  --timerange 20210109-20221231 \
  --cache none \
  --export trades
```

#### 1.2 分析质量映射

```bash
# 分析单个回测结果
python wyckoff/quality_bucket_analyzer.py \
  user_data/backtest_results/backtest-result-2026-03-14_10-30-00.json \
  --output quality_report_full.txt

# 分析三段回测（分别运行）
python wyckoff/quality_bucket_analyzer.py \
  user_data/backtest_results/backtest-result-bull.json \
  --output quality_report_bull.txt

python wyckoff/quality_bucket_analyzer.py \
  user_data/backtest_results/backtest-result-correction.json \
  --output quality_report_correction.txt

python wyckoff/quality_bucket_analyzer.py \
  user_data/backtest_results/backtest-result-bear.json \
  --output quality_report_bear.txt
```

#### 1.3 验证标准

质量映射必须满足以下条件才能通过验证：

1. **样本量充足**：每个分层至少 10 个样本
2. **胜率单调性**：高质量组胜率 ≥ 低质量组
3. **PF 单调性**：高质量组 Profit Factor ≥ 低质量组
4. **显著性差异**：Exceptional 与 Very Low 胜率差异 > 15%

#### 1.4 验证报告示例

```
================================================================================
Quality Bucket Analysis Report
================================================================================
Total Trades: 245
Analysis Date: 2026-03-14 10:45:23

Quality Tier Statistics:
--------------------------------------------------------------------------------
Tier            Count    WinRate    AvgProfit    PF      Sharpe    MaxDD
--------------------------------------------------------------------------------
exceptional     28          75.0%      3.45%    4.20    1.85      2.34%
high            67          68.7%      2.89%    3.45    1.62      3.12%
medium          98          61.2%      2.34%    2.78    1.34      4.56%
low             42          52.4%      1.78%    1.95    0.98      5.89%
very_low        10          40.0%      1.12%    1.23    0.45      7.23%

Validation Results:
--------------------------------------------------------------------------------
Overall: ✅ PASSED

  ✅ sufficient_samples
  ✅ win_rate_monotonic
  ✅ pf_monotonic
  ✅ significant_difference

Recommendations:
  ✅ Quality mapping is validated. Safe to enable dynamic position sizing.

================================================================================
```

### 阶段 2：启用动态仓位（验证通过后）

#### 2.1 在策略中集成

修改 `user_data/strategies/WyckoffV7_TrueMethod.py`：

```python
# 在 __init__ 中添加
from wyckoff.position_manager import create_position_manager

# 初始化仓位管理器
self.position_manager = create_position_manager(
    self,
    mode='defensive',  # 'off', 'defensive', 'balanced', 'aggressive'
)

# 在 custom_stake_amount 中使用
def custom_stake_amount(
    self,
    pair: str,
    current_time: datetime,
    current_rate: float,
    proposed_stake: float,
    min_stake: Optional[float],
    max_stake: float,
    leverage: float,
    entry_tag: Optional[str],
    side: str,
    **kwargs
) -> float:
    """
    动态仓位计算
    """
    # 如果未启用，返回默认仓位
    if self.position_manager is None:
        return proposed_stake
    
    # 获取当前数据
    dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
    if dataframe.empty:
        return proposed_stake
    
    current_idx = len(dataframe) - 1
    
    # 从 entry_tag 推断信号类型
    signal_type = self._parse_signal_type(entry_tag)
    
    # 计算动态仓位
    position_multiplier, metadata = self.position_manager.calculate_position_size(
        dataframe,
        current_idx,
        signal_type,
        trade_history=None  # 可选：传入交易历史
    )
    
    # 应用倍数
    adjusted_stake = proposed_stake * position_multiplier
    
    # 限制范围
    adjusted_stake = max(min_stake or 0, min(adjusted_stake, max_stake))
    
    # 记录日志
    logger.info(
        f"Position adjustment: {pair} {signal_type} "
        f"quality={metadata['quality_score']:.1f} "
        f"tier={metadata['quality_tier']} "
        f"multiplier={position_multiplier:.2f}x "
        f"stake={adjusted_stake:.2f}"
    )
    
    return adjusted_stake

def _parse_signal_type(self, entry_tag: Optional[str]) -> str:
    """从 entry_tag 解析信号类型"""
    if not entry_tag:
        return 'unknown'
    
    tag_lower = entry_tag.lower()
    
    if 'spring_3' in tag_lower or 'spring3' in tag_lower:
        return 'spring_3'
    elif 'spring_2' in tag_lower or 'spring2' in tag_lower:
        return 'spring_2'
    elif 'spring_1' in tag_lower or 'spring1' in tag_lower:
        return 'spring_1'
    elif 'sos' in tag_lower:
        return 'sos'
    elif 'lps' in tag_lower:
        return 'lps'
    elif 'axis' in tag_lower:
        return 'axis_support'
    elif 'phase_d' in tag_lower:
        return 'phase_d'
    elif 'jac' in tag_lower:
        return 'jac'
    else:
        return 'unknown'
```

#### 2.2 环境变量控制

```bash
# 关闭动态仓位（默认）
export WY_POSITION_MODE=off

# 启用防守型动态仓位
export WY_POSITION_MODE=defensive

# 启用平衡型（未来）
export WY_POSITION_MODE=balanced

# 启用进攻型（需要充分验证）
export WY_POSITION_MODE=aggressive
```

#### 2.3 回测验证

```bash
# 启用动态仓位回测
export WY_POSITION_MODE=defensive
docker compose run --rm freqtrade backtesting \
  --config user_data/config_backtest.json \
  --strategy WyckoffV7_TrueMethod \
  --timerange 20230101-20260228 \
  --cache none

# 对比基线（固定仓位）
export WY_POSITION_MODE=off
docker compose run --rm freqtrade backtesting \
  --config user_data/config_backtest.json \
  --strategy WyckoffV7_TrueMethod \
  --timerange 20230101-20260228 \
  --cache none
```

#### 2.4 成功标准

动态仓位系统必须满足：

1. **收益改善**：总收益 > 基线（固定仓位）
2. **风险可控**：最大回撤增幅 < 2%
3. **稳定性**：Sharpe Ratio ≥ 基线
4. **一致性**：三段回测均表现稳定

## 配置参数

### PositionConfig 参数说明

```python
@dataclass
class PositionConfig:
    base_stake: float = 1.0              # 基础仓位倍数
    
    # 质量分层倍数
    exceptional_multiplier: float = 1.0  # Exceptional 信号倍数
    high_multiplier: float = 1.0         # High 信号倍数
    medium_multiplier: float = 0.8       # Medium 信号倍数
    low_multiplier: float = 0.5          # Low 信号倍数
    very_low_multiplier: float = 0.3     # Very Low 信号倍数
    
    # 环境调整系数
    markdown_multiplier: float = 0.7     # Markdown 环境倍数
    consecutive_loss_multiplier: float = 0.6  # 连续亏损倍数
    
    # 风险控制
    min_position_size: float = 0.2       # 最小仓位（20%）
    max_position_size: float = 1.0       # 最大仓位（100%）
```

### 模式预设

#### Defensive（防守型，推荐）
- 不放大高质量信号（1.0x）
- 降低中低质量信号（0.8x, 0.5x, 0.3x）
- 环境调整激进（0.7x, 0.6x）
- 最大仓位 1.0x

#### Balanced（平衡型，未来）
- 轻度放大高质量信号（1.2x, 1.1x）
- 适度降低低质量信号（0.9x, 0.6x, 0.4x）
- 环境调整适中（0.8x, 0.7x）
- 最大仓位 1.2x

#### Aggressive（进攻型，需验证）
- 显著放大高质量信号（1.5x, 1.3x）
- 保留低质量信号（1.0x, 0.7x, 0.5x）
- 环境调整保守（0.9x, 0.8x）
- 最大仓位 1.5x

## 监控与诊断

### 统计信息

```python
# 在策略中获取统计
if self.position_manager:
    stats = self.position_manager.get_statistics()
    print(json.dumps(stats, indent=2))
```

### 日志输出

```python
# 记录统计到日志
if self.position_manager:
    self.position_manager.log_statistics()
```

### 关键指标

1. **质量分布**：各分层信号数量和平均评分
2. **平均仓位**：各分层的平均仓位倍数
3. **环境调整**：各类环境调整的触发次数
4. **仓位范围**：实际仓位的最小/最大值

## 风险警告

### 禁止事项

1. ❌ **未验证前启用**：质量映射未通过验证前，禁止启用动态仓位
2. ❌ **过度放大**：max_position_size > 1.5x 风险极高
3. ❌ **忽略环境**：不考虑市场环境的仓位调整会导致回撤失控
4. ❌ **样本不足**：少于 100 笔交易的数据不足以验证质量映射

### 风险点

1. **质量评分偏差**：评分系统可能存在系统性偏差
2. **过拟合风险**：历史表现不代表未来表现
3. **放大效应**：仓位放大会同时放大收益和风险
4. **环境误判**：市场环境识别可能出错

## 回滚方案

如果动态仓位系统表现不佳：

1. **立即关闭**：`export WY_POSITION_MODE=off`
2. **回滚代码**：注释掉 `custom_stake_amount` 中的动态仓位逻辑
3. **分析原因**：检查质量映射、环境判断、仓位倍数设置
4. **重新验证**：收集更多数据，重新验证质量映射

## 未来优化方向

### 短期（P1 完成后）
- [ ] 补充更多信号类型的质量评分
- [ ] 优化环境调整系数
- [ ] 增加交易历史分析（连续亏损检测）

### 中期（P3 完成后）
- [ ] 引入波段级 E/R 到质量评分
- [ ] 实现 Balanced 模式
- [ ] 增加仓位调整的可视化面板

### 长期（验证充分后）
- [ ] 考虑 Aggressive 模式
- [ ] 引入机器学习优化质量评分
- [ ] 实现自适应仓位调整

## 参考文档

- `TASK_NEW/TASKS_with_checklist.md` - RM-3 任务定义
- `AGENTS.md` - 项目级约束和原则
- `wyckoff/position_manager.py` - 核心实现
- `wyckoff/quality_bucket_analyzer.py` - 验证工具

## 联系与支持

如有问题或建议，请：
1. 查看本文档的常见问题部分
2. 检查日志输出和统计信息
3. 运行质量映射验证工具
4. 回滚到固定仓位模式

---

**重要提醒**：动态仓位系统是高级功能，必须在充分验证后才能启用。未经验证的启用可能导致严重亏损。
