# P2: Regime-Aware Authorization System

## 概述

P2 是一个 **有限 Regime-aware 授权系统**，旨在把 `market_env` / `macro_phase` / `bear_market_mode` 从描述层升级为授权层。

### 核心原则

1. **只做 multiplier / 授权，不做硬切换**
2. **不降低信号质量阈值**
3. **不替代 Wyckoff 证据**
4. **默认 disabled，保留为预埋点**

## 设计目标

- 研究不同 regime 下的 RR multiplier
- 研究不同 regime 下的持仓时间授权
- 研究少量信号路由开关
- 保持为 future optimization 预埋点

## 约束条件

- ❌ 不做全面动态切换
- ❌ 不允许 regime 替代信号质量
- ❌ 不允许仅因环境状态就降低关键质量阈值

## 模块组成

### 1. `regime_authorizer.py` - 授权系统

核心功能：
- **RR Multiplier**：不同 regime 下的风险回报调整系数
- **Holding Time Multiplier**：持仓时间调整系数
- **Signal Routing**：信号路由开关

#### RR Multiplier 配置

```python
rr_multipliers = {
    # macro_phase 层级
    'markup': 1.0,      # 上升趋势：标准 RR
    'markdown': 1.3,    # 下降趋势：提高 RR 要求（更保守）
    'transition': 1.15, # 过渡期：略微提高 RR 要求
    
    # market_env 层级（1H 框架）
    'trending': 1.0,    # 趋势行情：标准 RR
    'trading_range': 1.1, # 震荡行情：略微提高 RR
    
    # htf_market_env 层级（4H 框架）
    'htf_markup': 1.0,     # 4H 上升：标准 RR
    'htf_markdown': 1.25,  # 4H 下降：提高 RR 要求
    'htf_transition': 1.1, # 4H 过渡：略微提高 RR
    
    # bear_market_mode（最高优先级）
    # 熊市模式：1.4（显著提高 RR 要求）
}
```

#### Holding Time Multiplier 配置

```python
holding_time_multipliers = {
    # macro_phase 层级
    'markup': 1.0,      # 上升趋势：标准持仓时间
    'markdown': 0.7,    # 下降趋势：缩短持仓时间（快进快出）
    'transition': 0.85, # 过渡期：略微缩短持仓时间
    
    # market_env 层级
    'trending': 1.0,    # 趋势行情：标准持仓时间
    'trading_range': 0.8, # 震荡行情：缩短持仓时间
    
    # bear_market_mode（最高优先级）
    # 熊市模式：0.6（显著缩短持仓时间）
}
```

#### Signal Routing 配置

```python
signal_routing = {
    # bear_market_mode 下的信号路由
    'bear_market': {
        'allow_spring': True,      # ✅ 允许 Spring 信号（熊市反转机会）
        'allow_sos': False,        # ❌ 禁用 SOS 信号（熊市突破易失败）
        'allow_lps': True,         # ✅ 允许 LPS 信号（熊市回调买点）
        'allow_buec': True,        # ✅ 允许 BUEC 信号（熊市衰竭买点）
        'allow_jac': True,         # ✅ 允许 JAC 信号（熊市跳跃买点）
        'allow_creek': True,       # ✅ 允许 Creek 信号（熊市小回调）
        'allow_phase_d': False,    # ❌ 禁用 Phase D 信号（需要明确积累）
    },
}
```

### 2. `regime_diagnostics.py` - 诊断系统

核心功能：
- 检查 P2 触发条件
- 分析 regime 分布
- 生成启动建议报告

#### 四大触发条件

##### 条件 A：特定 regime 下持续跑输基线

检查逻辑：
- 连续 **2 个自然月** Dry Run 中
- 某一 regime 内的交易：
  - 平均单笔收益持续低于对应回测基线 **30% 以上**
  - 或 Profit Factor 持续 **< 1.0**
- 且该 regime 内样本数 **≥ 20 笔交易**

##### 条件 B：特定 regime 下出现方向性误配

检查逻辑：
- 在同一 regime 下，某类核心信号满足以下任一条件：
  - **连续 20 笔** 胜率明显异常
  - **或累计 20 笔（非连续），且时间窗口 ≤ 60 天**，胜率明显异常
- 例如：
  - `wyckoff_axis_support` 胜率持续低于回测基线对应监控阈值
  - 或 `wyckoff_sow` 在特定 regime 下样本胜率 < 50%
- 且该异常不出现在其他 regime 中

##### 条件 C：环境标签长期失真或异常常开

检查逻辑：
- `bear_market_mode` 持续激活 **> 30 天**
- 或 `daily_choch_confirmed` / `macro_phase` / `htf_market_env` 的分布连续 **2 周** 明显偏离历史基线
- 且实际价格结构与标签不符（由周报 + 人工复核确认）

##### 条件 D：回测已证明某一 regime 内存在稳定结构性短板

检查逻辑：
- 三段回测复盘中，某一 regime 下：
  - 交易样本 **≥ 30 笔**
  - 平均收益持续为负，或显著低于其他 regime
  - 且问题集中在：
    - RR 不匹配
    - 持仓寿命不匹配
    - 信号路由不匹配

## 使用方法

### 1. 检查是否需要启动 P2

```bash
python check_p2_trigger.py
```

输出示例：
```
P2 触发条件诊断报告
================================================================================

生成时间: 2026-03-14T10:30:00

P2 启动建议: ❌ 暂不建议启动

✅ 未触发任何 P2 启动条件

当前系统运行正常，暂不需要启动 P2 regime-aware 授权系统

================================================================================
```

### 2. 启用 P2（仅在触发条件满足后）

在策略中启用 P2：

```python
# 在 __init__ 方法中
if self.regime_authorizer:
    self.regime_authorizer.enabled = True
    logger.info('✅ P2 RegimeAuthorizer enabled')
```

或通过环境变量：

```bash
export WY_P2_ENABLED=1
```

### 3. 应用 Regime 授权

在 `populate_entry_trend` 中应用授权：

```python
# 应用 regime-aware 授权
if self.regime_authorizer and self.regime_authorizer.enabled:
    signal_quality, signal_type = self.regime_authorizer.apply_regime_authorization(
        dataframe=dataframe,
        signal_quality=dataframe['signal_quality'],
        signal_type=dataframe['enter_tag']
    )
    dataframe['signal_quality'] = signal_quality
    dataframe['enter_tag'] = signal_type
```

### 4. 获取 Regime 摘要

```python
if self.regime_authorizer:
    summary = self.regime_authorizer.get_regime_summary(dataframe)
    logger.info(f"Regime 分布: {summary}")
```

## 验证流程

### 1. 语义检查

```bash
# 检查模块导入
python -c "from wyckoff.regime_authorizer import RegimeAuthorizer; print('✅ OK')"
python -c "from wyckoff.regime_diagnostics import RegimeDiagnostics; print('✅ OK')"
```

### 2. 触发条件检查

```bash
# 运行诊断脚本
python check_p2_trigger.py

# 查看报告
cat p2_trigger_report.json
```

### 3. 回测验证（仅在启用后）

```bash
# 全量回测
docker compose run --rm freqtrade backtesting \
  --config user_data/config_backtest.json \
  --strategy WyckoffV7_TrueMethod \
  --timerange 20230101-20260228 \
  --cache none

# 三段门禁
docker compose run --rm freqtrade backtesting \
  --config user_data/config_backtest.json \
  --strategy WyckoffV7_TrueMethod \
  --timerange 20230101-20240630 \
  --cache none

docker compose run --rm freqtrade backtesting \
  --config user_data/config_backtest.json \
  --strategy WyckoffV7_TrueMethod \
  --timerange 20240701-20250630 \
  --cache none

docker compose run --rm freqtrade backtesting \
  --config user_data/config_backtest.json \
  --strategy WyckoffV7_TrueMethod \
  --timerange 20210109-20221231 \
  --cache none
```

## 进入 P2 的最低门槛

只有同时满足以下条件，才允许正式启动 P2：

- ✅ 已出现上述任一条明确触发条件
- ✅ 样本量达到最低要求（Dry Run 交易样本 ≥ 20；回测复盘样本 ≥ 30）
- ✅ 问题已明确集中在某个 regime，而不是全局系统退化
- ✅ 已确认不是数据质量 / 执行时序 / 容器异常导致的假异常
- ✅ 已确认不更适合由 P3 / 退出层 / 仓位层解决

## P2 启动前必须先排除

- ❌ 数据质量异常（字段缺失 / merge 对齐错误 / 状态机死锁）
- ❌ 执行层偏差（滑点 / `custom_exit` 时序 / Dry Run 运行异常）
- ❌ 小样本噪声（样本数不足）
- ❌ 全局系统性退化（不是 regime 问题，而是整体系统退化）

## 启动方式

触发后，P2 第一阶段只允许做：

1. ✅ regime 分布诊断
2. ✅ RR multiplier 方案
3. ✅ 持仓时间授权方案
4. ✅ 少量信号路由开关方案

仍然禁止：

- ❌ 全面动态切换
- ❌ 用 regime 降低质量阈值
- ❌ 用 regime 替代原始 Wyckoff 证据
- ❌ 在未完成最小验证矩阵前直接接线到生产逻辑

## 风险与副作用

### 主要风险

1. **过度保守**：RR multiplier 过高可能导致错过优质信号
2. **信号路由误判**：禁用某些信号可能错过特定 regime 下的机会
3. **持仓时间过短**：可能导致过早离场，错失趋势收益

### 缓解措施

1. **默认 disabled**：只在明确触发条件后启用
2. **可回滚**：所有调整都可以通过 `enabled = False` 关闭
3. **分层验证**：先做最小验证矩阵，再逐步扩大范围
4. **监控漂移**：持续监控 Dry Run 表现，及时调整参数

## 当前状态

- ✅ 模块已实现
- ✅ 诊断脚本已完成
- ✅ 策略集成已完成
- ⚠️ **默认 disabled**
- ⚠️ **等待触发条件**

## 下一步

1. 运行 `check_p2_trigger.py` 检查触发条件
2. 如未触发，继续监控 Dry Run 表现
3. 如触发，进行最小验证矩阵测试
4. 验证通过后，逐步启用 P2 功能

## 参考文档

- `TASK_NEW/TASKS_with_checklist.md` - P2 任务清单（第 711-902 行）
- `AGENTS.md` - 工作规则和验证流程
- `check_dryrun_fields.py` - Dry Run 字段检查脚本
