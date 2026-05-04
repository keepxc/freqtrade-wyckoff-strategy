# 架构地图

> 给 agent 看的顶层地图。不是文档，是指路牌。

## 分层

```
┌─────────────────────────┐
│     P5 分析层            │  views + 周报 + 诊断
├─────────────────────────┤
│     JSONL 桥接           │  event_emitter → ingest_jsonl → SQLite
├─────────────────────────┤
│     Freqtrade 执行引擎   │  策略 + 风控 + dry-run
└─────────────────────────┘
```

依赖方向: Freqtrade → JSONL → P5（单向，不回调）

## 关键目录

| 目录 | 用途 |
|------|------|
| `user_data/strategies/` | 策略代码（WyckoffV7_TrueMethod） |
| `wyckoff/` | 威科夫检测器库（独立于策略） |
| `user_data/ingest/` | JSONL → SQLite 导入 |
| `user_data/reports/` | 周报 + 诊断报告生成 |
| `user_data/events/` | JSONL 事件文件 |
| `scripts/` | 分析脚本 + lint + 垃圾回收 |
| `docs/` | P5 设计文档 + 事件规范 |
| `tests/` | 检测器和集成测试 |

## 关键文件速查

| 文件 | 作用 |
|------|------|
| `user_data/market.db` | 核心 SQLite 数据库 |
| `user_data/event_emitter.py` | 策略侧事件写入 |
| `user_data/config_dryrun.json` | Dry-run 配置 |
| `scripts/lint_p5.py` | 数据完整性检查 |
| `scripts/gc_market_db.py` | 垃圾回收 |

## 数据流

```
策略运行
  → event_emitter.py 写入 JSONL (user_data/events/)
    → ingest_jsonl.py 导入 market.db
      → build_weekly_report.py 生成周报
        → AI 读取分析
```

## 禁止事项

1. **P5 层不回调 Freqtrade** — 单向流出，不读取 Freqtrade API
2. **策略代码不读 market.db** — 策略是生产者，不是消费者
3. **不混入 backtest 数据** — lint 强制执行
4. **JSONL 事件必须符合 Event Spec** — lint 强制执行
