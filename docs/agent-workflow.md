# Agent Workflow (Minimal)

## 1. 先分类任务
开始前先判断任务属于：
- P1：参数系统工程
- P3：波段级 E/R
- P4：审查 / future optimization
- P5 / P5-WS：平台化升级

## 2. 先读正确上下文
优先给 agent 的文件：
- `TASK_NEW/TASKS_with_checklist.md`
- `user_data/strategies/WyckoffV7_TrueMethod.py`
- 涉及的 `wyckoff/*.py`
- `user_data/config_backtest.json`
- 如涉及 dry-run：`DRY_RUN_GUIDE.md`

## 3. 先计划再改
要求 agent 先说明：
- 准备改哪些文件
- 为什么改这些位置
- 这次任务属于哪个阶段
- 如何避免回归
- 如何验证

## 4. 小步实现
默认策略：
- 单次只做一个主题
- 改动尽量集中
- 高风险逻辑加开关或保留旧路径
- 实验性增强先默认关闭
- 平台化先做 ledger / 观测，再做分析 / 提案 / 治理

## 5. 默认验证链
1. 代码级 / 字段级检查
2. 全量回测
3. 如影响筛选、防御、环境、P1/P3：跑三段门禁
4. 如影响 live / metadata / stoploss / informative：看 dry-run
5. 如形成新结论：回写台账

## 6. 结果汇报模板
结果至少包含：
- 基线 vs 新结果
- 变化来自哪类信号 / 哪段行情
- 有无副作用
- 是否需要开关
- 属于当前主线 / 已归档结论 / 未来优化

建议格式：
```text
本次任务属于 [P1/P3/P4/P5]。
改动文件：[...]
原因：[...]
验证：[全量 / 三段 / dry-run / 脚本]
结果：基线 A -> 新结果 B
主要改善：[...]
主要风险：[...]
建议：保持默认 / 加开关 / 暂不固化 / 进入下一轮实验
```
