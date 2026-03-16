# P5-Data-Design.md

## 0. 设计目标

P5 不是新信号模块，而是策略研究与进化的数据底座。  
本版在原有 P5 设计上，进一步补入 **Phase-aware 退出（P0 预埋点）**，确保未来 `wyckoff/exit_logic.py` 中的 `ExitLogic` 一旦接线，不需要重做数据库、事件流和治理层。

P5 数据层必须支持五件事：

1. **事实留痕**  
   每笔交易、每个候选信号、每次 detector/regime 状态变化都可追溯。

2. **统计分析**  
   能算：
   - enter_tag 胜率漂移
   - detector 激活率
   - 冲突率 / fallback 率
   - regime 分布变化
   - baseline vs candidate 差异
   - exit reason 分布变化
   - ROI 提前截断 vs 结构化退出的替代关系

3. **AI 读取**  
   AI 不直接扫原始策略文件，而是读事实表和聚合视图做周报、诊断、提案。

4. **版本治理**  
   每次实验、参数修改、基线晋升、回滚都可审计。

5. **未来预埋点兼容**  
   对 Phase-aware exit、P&F 分层退出、regime-aware RR multiplier、持仓寿命授权等未来功能预留字段和事件位。

---

## 1. 系统边界

### 1.1 Freqtrade 内部保留
以下仍留在 Freqtrade / 策略代码中：
- 策略逻辑
- detector / state machine
- entry / exit / custom_exit / custom_stoploss
- signal_quality / tags / regime 计算
- Phase-aware 退出决策逻辑本体（未来接线）
- backtest / dry run / live 执行

### 1.2 P5 外部承担
以下由外部系统承担：
- 事件日志落盘
- 事件 ingest
- SQLite / PostgreSQL 数据库存储
- ledger 聚合
- 周报 / 漂移诊断
- proposal 生成
- experiment registry
- baseline / candidate / rollback 治理
- 退出层诊断与 Phase-aware exit 触发统计

### 1.3 硬边界
- 策略执行不能依赖数据库在线可用
- 数据库故障不能阻断交易
- AI 不能直接写生产参数
- AI 不能绕过回测 / 回归 / Dry Run 闸门

---

## 2. 数据流设计

推荐数据流：

**Freqtrade 运行**  
→ 输出结构化 JSONL 事件流  
→ 外部 ingest 脚本导入数据库  
→ 生成聚合视图 / 周报  
→ AI 读取数据库和摘要进行诊断与提案

### 2.1 为什么先 JSONL 再入库
不建议策略直接写 DB，原因：
- 会增加执行耦合
- DB 故障可能影响运行
- 调试困难
- 不利于重放

JSONL 的好处：
- 简单
- 可重放
- 易于排错
- 易于追加字段
- 后续 SQLite / PostgreSQL 都能 ingest

---

## 3. 事件模型（Event Spec）

V1 至少定义 5 类事件，V1.1 为 Phase-aware 退出额外补 1 类事件。

### 3.1 `run_event`
表示一次 backtest / dry run / live run 的开始、结束和摘要。

### 3.2 `signal_event`
候选信号事件。  
不论最终成交与否都必须记录。

### 3.3 `trade_event`
已成交交易事件，分为 `trade_open` 和 `trade_close`。

### 3.4 `detector_event`
关键 detector / 状态机 / regime 变化时输出。

### 3.5 `regime_snapshot`
按固定粒度输出环境状态快照。

### 3.6 `exit_decision_event`（新增，给 Phase-aware 退出等未来退出层预埋点用）
表示“退出层曾经考虑过某个退出动作，但最终是否触发、为什么没触发”。

用途：
- 解决“ExitLogic 已实现，但策略未接线”时无法观察逻辑命中率的问题
- 区分“没有信号”和“有退出候选但被阻断”
- 为未来 ROI vs 结构化退出替代分析提供基础

---

## 4. 数据库设计（P5-Data-Design）

V1 建议用 SQLite，但 schema 设计要兼容 PostgreSQL。

### 4.1 表：`experiment_runs`
每次 backtest / dry run / live / candidate 都是一条 run。

建议字段：
- `run_id` TEXT PRIMARY KEY
- `run_type` TEXT (`baseline|candidate|dry_run|live|analysis`)
- `mode` TEXT (`backtest|dry_run|live`)
- `strategy_name` TEXT
- `strategy_version` TEXT
- `git_commit` TEXT
- `config_hash` TEXT
- `parameter_set_name` TEXT
- `parent_baseline_run_id` TEXT NULL
- `timerange` TEXT
- `pair_scope` TEXT
- `created_at` TIMESTAMP
- `ended_at` TIMESTAMP NULL
- `status` TEXT (`running|completed|failed|approved|rejected|rolled_back`)
- `notes` TEXT NULL

### 4.2 表：`trades`
每笔成交交易一行。

核心字段：
- `trade_id` TEXT PRIMARY KEY
- `run_id` TEXT NOT NULL
- `pair` TEXT
- `side` TEXT
- `enter_time` TIMESTAMP
- `exit_time` TIMESTAMP NULL
- `enter_tag` TEXT
- `exit_reason` TEXT NULL
- `exit_tag` TEXT NULL
- `entry_price` REAL
- `exit_price` REAL NULL
- `profit_pct` REAL NULL
- `profit_abs` REAL NULL
- `duration_minutes` INTEGER NULL
- `stake_amount` REAL
- `leverage` REAL

入场快照字段：
- `signal_quality_enter` REAL
- `market_env_enter` TEXT
- `htf_market_env_enter` TEXT
- `macro_phase_enter` TEXT
- `bear_market_mode_enter` INTEGER
- `phase_context_tag_enter` TEXT
- `composite_intent_enter` TEXT
- `daily_gate_score_enter` REAL
- `daily_tr_exists_enter` INTEGER
- `rr_long_standard_enter` REAL NULL
- `rr_short_standard_enter` REAL NULL
- `p3_swing_down_er_exhaustion_enter` INTEGER
- `p3_swing_up_er_weak_enter` INTEGER

退出补充字段：
- `mfe_pct` REAL NULL
- `mae_pct` REAL NULL
- `funding_proxy_hours` REAL NULL

**Phase-aware 退出预埋字段（新增）**
- `phase_context_tag_exit` TEXT NULL
- `phase_exit_candidate` INTEGER DEFAULT 0
- `phase_exit_triggered` INTEGER DEFAULT 0
- `phase_exit_reason` TEXT NULL
- `phase_exit_path` TEXT NULL
- `exit_logic_module` TEXT NULL
- `phase_exit_blocked_reason` TEXT NULL

说明：
- `phase_exit_candidate=1`：退出层曾命中某个候选条件
- `phase_exit_triggered=1`：最终确实触发并进入实际退出路径
- `phase_exit_reason`：例如 `phase_d_exhaustion` / `phase_e_distribution` / `phase_transition_fail`
- `phase_exit_path`：例如 `roi_replaced` / `custom_exit_only` / `coexist_with_roi`
- `phase_exit_blocked_reason`：例如 `roi_first` / `not_confirmed` / `risk_guard` / `window_expired`

### 4.3 表：`signals`
候选信号，包括未成交。

字段：
- `signal_id` TEXT PRIMARY KEY
- `run_id` TEXT NOT NULL
- `ts` TIMESTAMP
- `pair` TEXT
- `timeframe` TEXT
- `side` TEXT
- `signal_name` TEXT
- `candidate_enter_tag` TEXT
- `was_executed` INTEGER
- `rejection_reason` TEXT NULL
- `signal_quality_before` REAL
- `signal_quality_after` REAL
- `rr_estimate` REAL NULL
- `market_env` TEXT NULL
- `htf_market_env` TEXT NULL
- `macro_phase` TEXT NULL
- `bear_market_mode` INTEGER
- `phase_context_tag` TEXT NULL
- `daily_gate_score` REAL NULL
- `daily_tr_exists` INTEGER
- `composite_intent` TEXT NULL
- `p3_swing_down_er_exhaustion` INTEGER
- `p3_swing_up_er_weak` INTEGER
- `wyckoff_warning` INTEGER
- `max_open_trades_blocked` INTEGER
- `risk_guard_blocked` INTEGER
- `regime_blocked` INTEGER

**为 future exit hook 预埋的字段**
- `phase_exit_eligible` INTEGER DEFAULT 0
- `phase_exit_candidate_reason` TEXT NULL

说明：
- 这些字段对当前 entry 不产生行为影响，仅用于后续对“哪些 setup 在未来更适合 phase-aware exit 管理”的统计观察。

### 4.4 表：`detector_events`
关键 detector 事件。

字段：
- `event_id` TEXT PRIMARY KEY
- `run_id` TEXT NOT NULL
- `ts` TIMESTAMP
- `pair` TEXT
- `timeframe` TEXT
- `event_family` TEXT
- `event_name` TEXT
- `event_value` TEXT
- `event_confidence` REAL NULL
- `metadata_json` TEXT NULL

**为 phase-aware exit 预埋的 detector / state 事件名**
- `phase_exit_candidate`
- `phase_exit_confirmed`
- `phase_exit_blocked`
- `phase_exit_window_open`
- `phase_exit_window_expired`

### 4.5 表：`regime_snapshots`
固定周期快照。

字段：
- `snapshot_id` TEXT PRIMARY KEY
- `run_id` TEXT NOT NULL
- `ts` TIMESTAMP
- `pair` TEXT
- `timeframe` TEXT
- `market_env` TEXT NULL
- `htf_market_env` TEXT NULL
- `macro_phase` TEXT NULL
- `bear_market_mode` INTEGER
- `phase_context_tag` TEXT NULL
- `daily_choch_confirmed` INTEGER
- `dist_cause_sufficient` INTEGER
- `daily_tr_exists` INTEGER
- `daily_gate_score` REAL NULL

### 4.6 表：`parameter_versions`
参数版本变更表。

字段：
- `param_version_id` TEXT PRIMARY KEY
- `run_id` TEXT NOT NULL
- `parameter_name` TEXT
- `old_value` TEXT
- `new_value` TEXT
- `parameter_class` TEXT (`structure|arbitration|injection|deployment|exit`)
- `reason` TEXT
- `risk_level` TEXT (`low|medium|high`)
- `proposal_id` TEXT NULL

说明：
- Phase-aware 退出未来的阈值/窗口参数统一归类到 `parameter_class='exit'`

### 4.7 表：`weekly_reports`
AI 周报归档。

字段：
- `report_id` TEXT PRIMARY KEY
- `week_start` DATE
- `week_end` DATE
- `run_scope` TEXT
- `report_type` TEXT (`dry_run|live|backtest_summary`)
- `summary_markdown` TEXT
- `health_score` REAL NULL
- `warnings_json` TEXT NULL
- `created_at` TIMESTAMP

**新增约定**
周报未来必须支持一节：`Exit Layer Diagnostics`
用于观察：
- phase exit candidate 次数
- 实际 phase exit trigger 次数
- ROI 先触发覆盖率
- 平均持仓时长变化
- exit reason 分布变化

### 4.8 表：`proposals`
AI 诊断与提案归档。

字段：
- `proposal_id` TEXT PRIMARY KEY
- `created_at` TIMESTAMP
- `proposal_type` TEXT (`diagnostic|experiment|rollback_suggestion`)
- `problem_statement` TEXT
- `evidence_json` TEXT
- `risk_level` TEXT
- `sample_sufficiency` TEXT (`insufficient|minimal|sufficient`)
- `recommended_action` TEXT
- `experiment_matrix_json` TEXT NULL
- `status` TEXT (`draft|reviewed|accepted|rejected|executed`)

**建议扩展的 proposal 标签**
- `entry_enhancement`
- `exit_enhancement`
- `regime_authorization`
- `risk_control`
- `rollback_suggestion`

Phase-aware 退出归到：`exit_enhancement`

### 4.9 表：`exit_decisions`（新增）
V1 可以先留表、V1.1 再真正用。

字段：
- `decision_id` TEXT PRIMARY KEY
- `run_id` TEXT NOT NULL
- `trade_id` TEXT NULL
- `ts` TIMESTAMP
- `pair` TEXT
- `timeframe` TEXT
- `phase_context_tag` TEXT NULL
- `exit_logic_module` TEXT
- `candidate_reason` TEXT
- `triggered` INTEGER
- `blocked_reason` TEXT NULL
- `expected_effect` TEXT NULL
- `metadata_json` TEXT NULL

用途：
- 单独承载 future exit hook 的“候选退出但未执行”的中间态
- 避免这些信息混进 `trade_close` 才能看到

---

## 5. 指标口径设计

### 5.1 detector 激活率
某 detector 为“有效真值”的 bar 数 ÷ 可观察总 bar 数

### 5.2 detector 冲突率
主判定与辅判定不一致样本 ÷ 主判定有效样本

### 5.3 fallback 率
因主 detector 缺失 / 冲突 / 无法判定而退回次级规则的次数 ÷ 总判定次数

### 5.4 signal execution rate
已成交信号数 ÷ 候选信号总数

### 5.5 enter_tag drift
最近窗口表现 vs 基线窗口表现

至少输出：
- 胜率差
- 平均单笔收益差
- PF 差
- 样本数

### 5.6 regime mismatch
满足以下至少一类：
- 某 regime 下平均单笔收益持续低于对应回测基线 30% 以上
- 某核心信号仅在某 regime 内失真
- 某 regime 标签分布与实际结构明显脱节
- `bear_market_mode` 异常常开超过 30 天

### 5.7 Phase-aware exit diagnostics（新增）
未来用于判断 `ExitLogic` 是否值得接线的核心指标：

1. `phase_exit_candidate_rate`
   - `phase_exit_candidate = 1` 的交易数 ÷ 总交易数

2. `phase_exit_trigger_rate`
   - `phase_exit_triggered = 1` 的交易数 ÷ `phase_exit_candidate = 1` 的交易数

3. `phase_exit_blocked_by_roi_rate`
   - `phase_exit_blocked_reason = 'roi_first'` 的次数 ÷ `phase_exit_candidate = 1` 的次数

4. `phase_exit_duration_delta`
   - Phase exit candidate 交易的平均持仓时长 vs 非 candidate 交易的平均持仓时长

5. `phase_exit_reason_distribution`
   - 不同 `phase_exit_reason` 的频率分布与表现

这几项将成为未来是否启动 P0 Phase-aware exit 接线实验的证据基础。

---

## 6. V1 / V1.1 范围建议

### V1 必做
- JSONL 事件输出
- SQLite
- `experiment_runs`
- `trades`
- `signals`
- `detector_events`
- `regime_snapshots`
- `parameter_versions`
- ingest 脚本
- 每周 summary 脚本
- 1 个 AI 周报入口

### V1.1 预留并补齐
- `exit_decisions`
- `exit_decision_event`
- trades 中的 phase exit 预埋字段
- 周报中的 Exit Layer Diagnostics 小节

### V1 不做
- PostgreSQL 生产迁移
- 自动晋升 baseline
- 自动改参数
- dashboard 前端
- 多用户并发
- 全自动实验队列
- 直接接线 Phase-aware exit 到生产策略

---

## 7. 与当前预埋点的衔接

### 当前状态
- `wyckoff/exit_logic.py` 中 ExitLogic 已实现
- 策略未接线
- 归档为未来预埋点

### 当前动作
P5 数据设计层现在就应完成：
- 字段预留
- 事件预留
- 周报预留
- proposal 类型预留

### 后续进入条件
只有在满足以下条件后，才建议正式启动 `Phase-aware exit wiring`：
- P5 V1 已落地
- Dry Run / Ledger 已稳定
- Exit Layer Diagnostics 显示：
  - Phase exit candidate 并非极低频
  - 存在明显 ROI 抢先 / 持仓寿命不匹配 / “该拿没拿住”的证据
- 退出层问题已明确不是更适合由 P3 或普通 ROI/stop 调整解决

---

## 8. 目录结构建议（V1）

```text
p5_ws/
  config/
    db.yaml
    metrics.yaml
  events/
    dry_run/
    backtest/
    live/
  ingest/
    ingest_jsonl.py
    normalize_events.py
  db/
    schema.sql
    views.sql
    migrations/
  reports/
    build_weekly_report.py
    build_drift_report.py
  analysis/
    diagnose_regime_mismatch.py
    diagnose_signal_drift.py
    diagnose_exit_layer.py
  ai/
    prompt_templates/
    generate_weekly_summary.py
    generate_proposal.py
  docs/
    P5-Data-Design.md
    P5-Event-Spec.md
    P5-V1-Implementation-Plan.md
```

---

## 9. 结论

P5 数据层不是为了今天就把 Phase-aware exit 接进策略，  
而是为了确保：

> 当未来决定接线时，系统已经能记录、分析、诊断、回滚，而不是再重做一次底层架构。

因此，本版数据设计已将 `Phase-aware 退出` 纳入：
- 表结构
- 事件模型
- 指标口径
- proposal 分类
- 周报接口
- 后续接线前置条件