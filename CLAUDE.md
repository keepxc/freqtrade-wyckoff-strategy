# CLAUDE.md

## 项目身份

这是一个基于 **Freqtrade** 的 **威科夫交易系统**，包含两大子系统：

| 子系统 | 定位 | 状态 |
|--------|------|:---:|
| **Wyckoff 策略** | 交易执行引擎（V7.1 生产 / V8 wave_start 频率优化中） | 🟡 V8 小范围优化 |
| **P5 智能层** | 统计、诊断、实验、治理底座（SQLite + JSONL） | 🟢 V1 已运行 |

> AI 不改生产逻辑，只读数据和出分析报告。

---

## 开始前必读

1. `@AGENTS.md` — 项目整体说明、P5 架构、V1 范围
2. `TASK_NEW/TASKS_with_checklist.md` — 当前任务状态、常用命令、已完成实验索引
3. `TASK_NEW/TASKS.md` — 详细任务记录和实验数据
4. `TASK_NEW/wyckoff_v8_redesign.md` — V8 重构设计文档（最重要，当前主线）
5. `docs/P5-Data-Design.md` / `docs/P5-Event-Spec.md` — P5 数据底座设计

---

## 当前基线

| 版本 | 收益率 | 交易数 | 胜率 | DD | 说明 |
|------|--------|:------:|------|-----|------|
| **V7.1** | 8,374% | — | — | — | 已被取代，移除 EMA200 独裁者 |
| **V7.1 + 实验A** | 9,749% | 554 | 81.4% | 6.28% | wave_start 豁免 distributing，已固化 |
| **V7.1 + A+B（当前）** | **11,313%** | 561 | 81.5% | 6.28% | +B：wave_start 豁免 bear+accumulating，已固化 |

回测范围：`20220101-20260502`

策略文件：`user_data/strategies/WyckoffV7_TrueMethod.py`

---

## V8 当前状态（2026-05-10）

**真 V8 架构确立**：新策略文件 `WyckoffV8.py` 从零实现，不引用 V7 代码。

> 核心：V7 退出端约束（Freqtrade 向量化路径）来自在 V7 框架内修改，新文件不用 `exit_signal`，全走 `custom_exit`，约束消失。

### 架构
```
日线层：sc_detector → phase_detector → pnf_calculator（TASK 1-3，已完成）
1H 层：wave_start ChoCh + Phase D/E 上下文（TASK 4）
退出层：custom_exit + PNF T1/T2/T3 目标位驱动（TASK 5）
集成：WyckoffV8.py（TASK 6）
```

### TASK 状态
| TASK | 内容 | 状态 |
|------|------|:---:|
| TASK 1 | `sc_detector.py` SC/BC 识别 | ✅ |
| TASK 2 | `phase_detector.py` Phase A-E | ✅ |
| TASK 3 | `pnf_calculator.py` PNF 目标位 | ✅ |
| TASK 4 | 1H 执行层设计 | 🔵 下一步 |
| TASK 5 | 退出层设计（custom_exit + PNF） | 🔵 待开始 |
| TASK 6 | `WyckoffV8.py` 集成 | 🔵 待开始 |
| TASK 7 | 回测验证 vs V7.1+A+B（11,313%） | 🔵 待开始 |

V8 模块位置：`user_data/strategies/v8_modules/`
V8 设计文档：`TASK_NEW/wyckoff_v8_redesign.md`

---

## 关键约束 & 已证伪的实验

| 约束/结论 | 详情 |
|-----------|------|
| EMA200 移除 = +138% 利润 | V7→V7.1 唯一变量 |
| wave_start 豁免 distributing = +1375% | 实验 A，已固化，8374%→9749% |
| composite_intent ctx/harmony/sot/quality 对 wave_start 拦截率为 0 | 可删，不带来任何增益 |
| 1H 退出端已达物理极限 | 7 轮实验无一超越 V7.1 |
| 和谐保险必须分信号 | 统一和谐 -21%，分信号 -69% |
| Phase E 分层抑制无效 | Freqtrade 向量化 exit_signal 优先级冲突 |
| RP 参数路由已永久关闭 | `WY_REGIME_ROUTING=0`，信号层已覆盖市场自适应 |
| P3 波段 E/R 默认关闭 | `WY_P3_SWING_ER=off` |
| 仓位管理默认关闭 | `WY_POSITION_MODE=off` |

---

## 常用命令

```bash
# 回测（全量，--cache none 保证不走缓存）
docker compose run --rm freqtrade backtesting --config user_data/config_backtest.json --strategy WyckoffV7_TrueMethod --timerange 20220101-20260502 --cache none

# P5 全流程一键：回测 + 摄入 + 报表
python scripts/run_backtest_pipeline.py --timerange 20260101-20260114

# Dry Run 日志
docker compose logs -f --tail=100 freqtrade

# 快速查数据库行数
python -c "import sqlite3; conn=sqlite3.connect('user_data/market.db'); [print(f'{t}: {conn.execute(f\"SELECT COUNT(*) FROM {t}\").fetchone()[0]}') for t in ['experiment_runs','signals','trades','detector_events','regime_snapshots','exit_decision_events','weekly_reports']]"
```

---

## 修改策略代码的流程

1. 先读 `TASK_NEW/wyckoff_v8_redesign.md` 确认改动是否与 V8 设计一致
2. 备份当前 `WyckoffV7_TrueMethod.py`
3. 改代码 → Docker 回测验证 → 对比基线
4. 记录实验结果到修订记录中
5. 如果结果劣化，回退并记录证伪结论
