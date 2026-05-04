# 黄金规则

> 这些规则不是建议，是约束。lint 脚本机械执行，违反 = CI 阻塞。
> 人类的品味一旦被编码为规则，就会持续应用于每一行代码。

---

## 数据完整性

### G001: P5 只用 dry_run 数据
- **规则:** market.db 中不允许存在 mode='backtest' 的 experiment_runs 及其关联数据
- **强制:** `scripts/lint_p5.py` → no-backtest-data
- **原因:** backtest 数据会污染信号质量评估和 regime 分布分析
- **决策:** D001

### G002: 孤立记录必须清理
- **规则:** signals/regime_snapshots/trades/detector_events 的 run_id 必须对应 experiment_runs 中存在的记录
- **强制:** `scripts/lint_p5.py` → orphan-signals, orphan-snapshots
- **原因:** 孤立记录会导致统计偏差

### G003: JSONL 事件必须符合规范
- **规则:** 每个 JSONL 事件必须有 event_type 字段，且值在 Event Spec 定义的枚举中
- **强制:** `scripts/lint_p5.py` → jsonl-event-type
- **原因:** 不合规事件导致 ingest 失败或数据污染
- **参考:** `docs/P5-Event-Spec.md`

## 架构约束

### G010: 单向依赖
- **规则:** P5 分析层不回调 Freqtrade 执行引擎。数据流是 Freqtrade → JSONL → P5
- **强制:** 人工审查
- **原因:** 保持 Freqtrade 可独立升级，P5 是叠加不是修改
- **决策:** D003

### G011: 策略不读 market.db
- **规则:** 策略代码（user_data/strategies/）不导入或查询 market.db
- **强制:** 人工审查
- **原因:** 策略是事件生产者，不是消费者。双向耦合会导致循环依赖

### G012: 检测器独立于策略
- **规则:** wyckoff/ 目录的检测器通过信号接口与策略连接，不直接引用策略特定逻辑
- **强制:** 人工审查
- **原因:** 检测器需要在不同策略间复用
- **决策:** D005

## 开发节奏

### G020: 设计先行
- **规则:** 先想清楚要什么，再让 AI 实现。不要 vibe-coding
- **强制:** 人工纪律
- **原因:** 没有设计脉络时不可能和 agent 有效沟通

### G021: 重构纪律
- **规则:** 每批代码生成后审查，保持代码库可理解
- **强制:** 人工纪律
- **原因:** AI 擅长生成也擅长腐烂，不重构 = 快速腐烂
