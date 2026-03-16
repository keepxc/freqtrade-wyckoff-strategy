# Validation & Engineering Moat Framework

验证与工程护城河框架文档（RM-4 / FUT-E 系列）

## 概述

本框架为 WyckoffV7_TrueMethod 策略提供专业级验证与监控能力，确保系统可持续迭代。

## 组件清单

### 1. 单元测试 (FUT-E1 ~ E4)

位置：`tests/test_*.py`

- `test_daily_event_detector.py` - DailyEventDetector 单元测试
- `test_event_sequence_state_machine.py` - EventSequenceStateMachine 单元测试
- `test_market_env_detector.py` - MarketEnvironmentDetector 单元测试
- `test_composite_man_detector.py` - CompositeManIntentDetector 单元测试

运行方式：
```bash
# 运行所有测试
pytest tests/ -v

# 运行特定测试
pytest tests/test_daily_event_detector.py -v

# 运行并显示输出
pytest tests/test_market_env_detector.py -v -s
```

### 2. 参数敏感性测试框架 (FUT-E5)

位置：`tests/test_parameter_sensitivity.py`

功能：
- 测试关键参数变化对系统行为的影响
- 识别脆弱参数（小变化导致大影响）
- 识别鲁棒参数（变化影响可控）
- 生成敏感性报告

使用示例：
```python
from tests.test_parameter_sensitivity import ParameterSensitivityTester
from wyckoff.market_env_detector import MarketEnvironmentDetector

tester = ParameterSensitivityTester(MarketEnvironmentDetector)

# 测试单个参数
result = tester.test_parameter_sensitivity(
    dataframe=df,
    parameter_name='half_window',
    test_values=[10, 15, 20, 25, 30],
    metric_func=lambda df: (df['market_env'] == 'markup').sum() / len(df),
    baseline_value=20
)

# 测试多个参数
parameter_configs = {
    'half_window': [10, 15, 20, 25, 30],
    'markup_thresh': [1.0, 1.25, 1.5, 1.75, 2.0],
}

results = tester.test_multiple_parameters(df, parameter_configs, metric_func)
report = tester.generate_report(results)
print(report)
```

### 3. 检测器监控面板 (FUT-E6)

位置：`wyckoff/detector_monitoring_panel.py`

功能：
- 统计各检测器的激活频率
- 检测信号冲突（多个互斥信号同时激活）
- 监控 fallback 机制触发率
- 生成诊断报告

使用示例：
```python
from wyckoff.detector_monitoring_panel import DetectorMonitoringPanel

panel = DetectorMonitoringPanel()

# 分析回测结果
results = panel.analyze_dataframe(backtest_df)

# 生成报告
report = panel.generate_report(results)
print(report)

# 保存报告
panel.save_report(results, 'detector_monitoring_report.md')
```

### 4. Dry Run 漂移监控 (FUT-E7)

位置：`wyckoff/dryrun_drift_monitor.py`

功能：
- 监控 dry-run 与回测结果的差异
- 检测字段缺失、数值漂移、逻辑不一致
- 生成漂移诊断报告
- 为 live trading 提供预警

使用示例：
```python
from wyckoff.dryrun_drift_monitor import DryRunDriftMonitor

monitor = DryRunDriftMonitor()

# 对比回测与 dry-run
results = monitor.compare_backtest_vs_dryrun(backtest_df, dryrun_df)

# 生成报告
report = monitor.generate_report(results)
print(report)

# 保存报告
monitor.save_report(results, 'dryrun_drift_report.md')
```

### 5. Enter Tag 胜率跟踪 (FUT-E8)

位置：`wyckoff/enter_tag_winrate_tracker.py`

功能：
- 统计各 enter_tag 的历史胜率
- 监控胜率漂移（时间窗口对比）
- 识别退化信号（胜率下降）
- 为信号质量评估提供数据

使用示例：
```python
from wyckoff.enter_tag_winrate_tracker import EnterTagWinRateTracker

tracker = EnterTagWinRateTracker()

# 分析交易记录
results = tracker.analyze_enter_tag_performance(
    trades_df,
    reference_period=('2023-01-01', '2024-12-31')
)

# 生成报告
report = tracker.generate_report(results)
print(report)

# 保存报告
tracker.save_report(results, 'enter_tag_winrate_report.md')
```

### 6. 规则开关 + 回归对照 + 回滚模板 (FUT-E9)

位置：`wyckoff/rule_switch_template.py`

功能：
- 为新规则提供标准化开关机制
- 自动回归对照（新规则 vs 基线）
- 快速回滚能力
- 版本控制与审计日志

使用示例：
```python
from wyckoff.rule_switch_template import (
    get_rule_manager,
    is_rule_enabled,
    register_default_rules
)

# 注册规则
register_default_rules()

# 在策略中检查规则
if is_rule_enabled('market_env_routing'):
    # 使用新规则
    pass
else:
    # 使用基线逻辑
    pass

# 管理规则
manager = get_rule_manager()
manager.enable_rule('daily_gate_soft_mode', reason='Testing P1 soft mode')
manager.disable_rule('market_env_routing', reason='P4 archived')
manager.rollback_rule('wave_er_exit_enhancement', reason='Performance regression')

# 查看审计日志
audit_log = manager.get_audit_log('daily_gate_soft_mode')
```

环境变量快速开关：
```bash
# 快速启用规则（无需修改配置文件）
export WY_RULE_DAILY_GATE_SOFT_MODE_ENABLED=true
export WY_RULE_MARKET_ENV_ROUTING_ENABLED=false

# 运行回测
docker compose run --rm freqtrade backtesting ...
```

## 工作流程

### 新规则开发流程

1. **注册规则**
   ```python
   manager.register_rule(
       rule_id='my_new_rule',
       rule_name='My New Rule',
       description='Description of the rule',
       enabled=False  # 默认关闭
   )
   ```

2. **编写单元测试**
   - 在 `tests/` 目录创建测试文件
   - 覆盖核心逻辑和边界情况

3. **参数敏感性测试**
   - 使用 `ParameterSensitivityTester` 测试关键参数
   - 识别鲁棒参数范围

4. **回测验证**
   ```bash
   # 基线回测
   export WY_RULE_MY_NEW_RULE_ENABLED=false
   docker compose run --rm freqtrade backtesting ...
   
   # 新规则回测
   export WY_RULE_MY_NEW_RULE_ENABLED=true
   docker compose run --rm freqtrade backtesting ...
   ```

5. **监控面板检查**
   - 使用 `DetectorMonitoringPanel` 检查激活率、冲突率
   - 确认无异常信号

6. **Dry-run 验证**
   - 运行 dry-run
   - 使用 `DryRunDriftMonitor` 对比回测与 dry-run
   - 确认无漂移

7. **胜率跟踪**
   - 使用 `EnterTagWinRateTracker` 监控新信号胜率
   - 确认无退化

8. **上线决策**
   - 如果验证通过：`manager.enable_rule('my_new_rule')`
   - 如果有问题：`manager.rollback_rule('my_new_rule', reason='...')`

### 回归检查流程

每次修改后运行：

```bash
# 1. 单元测试
pytest tests/ -v

# 2. 回测对比
bash scripts/run_regression_backtest.sh

# 3. 生成监控报告
python scripts/generate_monitoring_reports.py

# 4. 检查漂移
python scripts/check_dryrun_drift.py
```

## 配置文件

### 规则开关配置

位置：`wyckoff/rule_switches.json`

示例：
```json
{
  "daily_gate_soft_mode": {
    "rule_id": "daily_gate_soft_mode",
    "rule_name": "Daily Gate Soft Mode",
    "enabled": false,
    "version": "1.0.0",
    "author": "system",
    "description": "Daily gate soft mode (no hard block)",
    "parameters": {
      "mode": "soft"
    },
    "audit_log": [
      {
        "action": "registered",
        "timestamp": "2026-03-14T10:00:00",
        "enabled": false
      }
    ]
  }
}
```

## 告警阈值

### 检测器监控
- 冲突率 > 10%：高严重性
- Fallback 率 > 30%：中严重性

### Dry-run 漂移
- 数值漂移 > 5%：告警
- 分布差异 > 10%：告警
- 相关性 < 0.8：告警
- 整体漂移率 > 30%：严重告警

### 胜率跟踪
- 胜率 < 40%：告警
- 负期望：严重告警
- 盈利因子 < 1.5：告警
- 连续亏损 >= 5：提示

## 最佳实践

1. **最小改动原则**
   - 一次只改一个规则
   - 保留旧逻辑路径（通过开关控制）

2. **充分验证**
   - 单元测试 → 参数敏感性 → 回测 → Dry-run → 上线
   - 不跳过任何步骤

3. **可回滚**
   - 所有新规则默认关闭
   - 保留审计日志
   - 出现问题立即回滚

4. **持续监控**
   - 定期生成监控报告
   - 跟踪胜率漂移
   - 检查 dry-run 一致性

5. **文档化**
   - 记录规则目的和预期效果
   - 记录参数敏感性测试结果
   - 记录回滚原因

## 故障排查

### 单元测试失败
1. 检查测试数据是否充足
2. 检查字段依赖是否满足
3. 检查边界条件处理

### 参数敏感性异常
1. 检查参数范围是否合理
2. 检查评估指标是否正确
3. 增加测试样本量

### Dry-run 漂移
1. 检查字段是否缺失
2. 检查计算逻辑是否一致
3. 检查数据对齐问题

### 胜率退化
1. 检查市场环境变化
2. 检查规则冲突
3. 检查参数是否过拟合

## 维护计划

- 每周：运行单元测试
- 每月：生成监控报告
- 每季度：参数敏感性复查
- 每次上线前：完整验证流程

## 联系与支持

如有问题，请查看：
- `TASK_NEW/TASKS_with_checklist.md` - 任务清单
- `AGENTS.md` - 开发指南
- `DRY_RUN_GUIDE.md` - Dry-run 指南
