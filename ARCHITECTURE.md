# Freqtrade / P5 架构

> ⚠️ **归档（2026-10-04）**：本文描述的 `WyckoffV7 → P5 → AI` 架构**已不反映现状**——
> 全部 freqtrade bot 已停，本仓库不再承载在跑系统。保留供追溯。
>
> **当前在跑的系统**（币安跟单 / ETF 定投 / 币安美股手动）见 `~/.hermes/scripts/README.md`。
> 判断"策略是否在跑"须查 trades DB 的 `MAX(open_date)`，不能凭文件存在下结论。

> 一句话：Wyckoff 策略信号层作为 Freqtrade 执行引擎的上游，P5 作为数据底座做诊断和复盘。

```
  ┌─────────────────────────────────────────────────────┐
  │                    Freqtrade                        │
  │  WyckoffV7_TrueMethod.py                           │
  │    ├── wyckoff/* detector modules  ──→ 入场/退出信号 │
  │    ├── exit_logic.py               ──→ down_wave等  │
  │    └── strategy_event_bridge.py    ──→ JSONL事件输出  │
  └───────────────┬─────────────────────────────────────┘
                  │ JSONL (events/*.jsonl)
                  ▼
  ┌─────────────────────────────────────────────────────┐
  │                  P5 数据底座                          │
  │  ingest/ingest_jsonl.py  ──→ SQLite (p5.db)         │
  │  db/schema.sql            ──→ 核心表结构              │
  │  db/views.sql             ──→ 聚合视图                │
  │  reports/build_weekly_report.py → 周报               │
  └───────────────┬─────────────────────────────────────┘
                  │ SQL queries
                  ▼
  ┌─────────────────────────────────────────────────────┐
  │              AI 诊断层 (Hermes Agent)                │
  │  读取事实表 + 聚合视图 → 周报/诊断/提案               │
  │  不直接修改生产逻辑                                  │
  └─────────────────────────────────────────────────────┘
```

## 组件职责

| 层 | 组件 | 路径 | 职责 |
|----|------|------|------|
| 执行 | Freqtrade 引擎 | docker-compose.yml | 订单执行、仓位管理、风险控制 |
| 信号 | Wyckoff 检测器 | wyckoff/*detector*.py | Spring/SOS/SOW/Climax 等 Wyckoff 事件检测 |
| 信号 | 退出逻辑 | wyckoff/exit_logic.py | down_wave, climax exit 等结构化退出 |
| 信号 | 状态机 | wyckoff/state_machine.py | 事件序列驱动的阶段转换 |
| 信号 | 策略入口 | user_data/strategies/WyckoffV7_TrueMethod.py | populate_entry/exit 指标拼接 |
| 数据 | 事件桥接 | strategy_event_bridge.py | 策略运行时 → JSONL |
| 数据 | 摄取 | ingest/ingest_jsonl.py | JSONL → SQLite |
| 数据 | 数据库 | P5/p5.db | 事实表 + 聚合视图 |
| 诊断 | 周报 | reports/build_weekly_report.py | 每周自动诊断 |
| 诊断 | 分析脚本 | scripts/*.py | 专项分析（退出、冲突、机会成本等） |
| 诊断 | Hermes Agent | (外部) | AI 读取事实，不写入生产逻辑 |

## 数据流方向

**只允许单向：** Freqtrade → JSONL → P5 SQLite → AI 读取。禁止反向（AI 直接改策略代码或参数）。

## 关键路径

| 场景 | 经过组件 |
|------|---------|
| 入场信号 | detector → state_machine → populate_entry_trend → freqtrade order |
| 退出信号 | exit_logic → custom_exit → freqtrade order → strategy_event_bridge |
| 诊断分析 | p5.db views → weekly_report / scripts / AI |
| 实验对比 | backtest → compare_baseline.py → DECISIONS.md |

## 禁止事项

- ❌ AI 直接修改策略代码或参数（只能通过 DECISIONS.md 记录结论）
- ❌ down_wave 参数改动（见 DECISIONS.md D001）
- ❌ P5 数据库混入 backtest 模式数据
- ❌ 硬编码 /freqtrade/ 路径（必须用 /home/gyan/freqtrade/）
