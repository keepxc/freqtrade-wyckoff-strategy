# 黄金规则 (Golden Rules)

> 不可违反的机械规则。每条关联一个 lint 脚本强制检查。

## G001: down_wave 参数不可变
- **规则**: WY_DW_EFF_THRESHOLD 必须 = 0.001, WY_DW_DISPLACEMENT 必须 = 0.008
- **强制**: lint_golden_rules.py → G001
- **原因**: D001 锁定结论，任何豁免机制代价 > 收益
- **决策**: [D001](DECISIONS.md#d001-down_wave-退出锁定-v71)

## G002: P5 只用 dry_run 数据
- **规则**: p5.db 中 experiment_runs.mode 不得有 'backtest' 值
- **强制**: lint_golden_rules.py → G002
- **原因**: D004，backtest 数据污染诊断
- **决策**: [D004](DECISIONS.md#d004-p5-数据只来自-dry_run)

## G003: 禁止硬编码绝对路径
- **规则**: 代码中不得出现 `/freqtrade/` 作为路径前缀
- **强制**: lint_golden_rules.py → G003
- **原因**: 环境依赖 — 用户路径为 `/home/gyan/freqtrade/`
- **决策**: (工程惯例)

## G004: AI 不直接突变生产代码
- **规则**: Hermes Agent 不得直接 write 策略文件或策略参数
- **强制**: (运行时约束, Hermes 层)
- **原因**: D005，输出必须经 TASK / DECISIONS.md 审查
- **决策**: [D005](DECISIONS.md#d005-ai-不直接修改生产逻辑)

## G005: 回测必须对标 V7.1 baseline
- **规则**: 任何优化实验的回测结果必须与 V7.1 baseline (8997363, 8,374%) 对比
- **强制**: lint_golden_rules.py → G005 (检查回测结果注释中是否提及 baseline 对比)
- **原因**: D003，不对标的实验结论无效
- **决策**: [D003](DECISIONS.md#d003-v71-baseline-锁定)
