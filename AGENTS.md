# AGENTS.md

给 Codex / coding agent 的最简生产指令。默认目标：**最小改动、可验证、可回滚、先当前主线后未来优化**。

## 1. 当前阶段顺序
按下面顺序理解任务，除非用户明确改优先级：

1. **P1 — daily gate 参数系统工程**
   - 只做最小可测矩阵。
   - 研究阶段围绕 `soft`。
   - 部署默认尊重当前基线。
2. **P3 — 波段级 Effort/Result**
   - 仅在 P1 完成后推进。
   - 目标：改善持仓管理、减轻过早离场。
   - 与 bar 级 E/R 增量融合，不覆盖。
3. **P4 — 已归档结论**
   - `MarketEnvironmentDetector` 当前是 **future regime-aware optimization 预埋点**。
   - 默认不接线，除非用户明确要求且有 regime 失配证据。
4. **P5 / P5-WS — 平台化升级**
   - 目标：可统计、可诊断、可提案、可受控进化。
   - AI 可以分析和提案，**不能直接改生产策略**。

## 2. 开工前先读
至少先读：
- `TASK_NEW/TASKS_with_checklist.md`
- `user_data/strategies/WyckoffV7_TrueMethod.py`
- 本次直接涉及的 `wyckoff/*.py`
- 如涉及 dry-run：`DRY_RUN_GUIDE.md`

## 3. 默认基线与约束
默认尊重以下项目级事实：
- 主策略：`WyckoffV7_TrueMethod`
- 配置：`user_data/config_backtest.json`
- 主周期：`1h`
- 主要标的：`BTC/USDT:USDT`
- 模式：`futures` + `isolated`
- 手续费：`0.0006`
- 默认回测带 `--cache none`
- 当前阶段目标：**系统存活率优先，不是短期最大化收益**
- `daily_gate_mode` 默认 `off`

## 4. 工作规则
- 先总结现状、风险、改动点，再动代码。
- 默认做**最小可验证改动**。
- 不把 bug 修复、重构、调参、文档整理混成一次提交。
- 高风险逻辑优先加开关或保留旧路径。
- 没有充分验证前，不改默认值。
- 不引入前视偏差、未来函数、错误对齐。
- 每完成一个任务后，主动判断当前上下文是否需要压缩；不要等系统强制触发，执行上从“被动撑爆”改为“主动清理”。

## 5. 阶段化默认动作
### P1
- 冻结所有非 P1 变量。
- 一次只改一个关键参数。
- 先看 gate 分布，再看交易行为，最后看绩效。
- 不做组合爆炸，采用淘汰制。

### P3
- 用已有 `WaveAnalyzer`。
- 波段级 E/R 只做增量层。
- 优先影响减仓 / 退出 / Spring 增强。
- 不直接做新的硬入场门槛。

### P4
- 先做定位与 ROI 判断，不先接线。
- 无明确 regime 失配证据时，倾向归档。

### P5 / P5-WS
- 先补 ledger / 周报 / 漂移诊断 / 参数版本治理。
- 不把“自动提案”写成“自动上线”。
- 保留人工最终批准。

## 6. 高风险禁区
- 不要用 `shift(-n)` 或任何未来数据。
- informative / HTF 合并必须有对齐和缺失保护。
- `custom_stoploss` 不要重新引入错误固定止损语义。
- 不要在小样本信号上做强结论。
- 不要在当前阶段直接启用大规模动态仓位。
- 不要把 future backlog 提前写成当前主线。
- 不要让 AI / agent 直接改生产参数。

## 7. 默认验证顺序
1. 语义检查：路径、字段、默认行为是否正确。
2. 局部验证：相关脚本 / 最小复现场景。
3. 全量回测。
4. 如影响筛选、防御、环境、P1/P3：补三段回测。
5. 如影响 live / metadata / stoploss / informative：检查 dry-run 字段或日志。
6. 如形成新结论：回写任务台账。

推荐回测命令：
```bash
docker compose run --rm freqtrade backtesting --config user_data/config_backtest.json --strategy WyckoffV7_TrueMethod --timerange 20230101-20260228 --cache none
```

三段门禁：
```bash
docker compose run --rm freqtrade backtesting --config user_data/config_backtest.json --strategy WyckoffV7_TrueMethod --timerange 20230101-20240630 --cache none
docker compose run --rm freqtrade backtesting --config user_data/config_backtest.json --strategy WyckoffV7_TrueMethod --timerange 20240701-20250630 --cache none
docker compose run --rm freqtrade backtesting --config user_data/config_backtest.json --strategy WyckoffV7_TrueMethod --timerange 20210109-20221231 --cache none
```

Dry-run 检查：
```bash
python check_dryrun_fields.py
```

## 8. 汇报格式
至少说明：
- 改了什么文件、为什么改
- 属于哪个阶段（P1 / P3 / P4 / P5）
- 做了哪些验证
- 基线 vs 新结果
- 主要收益 / 风险 / 副作用
- 是否需要开关、是否建议改默认值
