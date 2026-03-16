# P5-Event-Spec.md

## 0. 目标

本规范定义 P5 / P5-WS 的 JSONL 事件格式。  
原则：

- 执行仍在 Freqtrade 内
- 事件先写 JSONL，再由外部 ingest 入库
- 事件必须满足“可重放、可审计、可扩展”
- 本版额外补入 `Phase-aware exit` 预埋规范，确保未来 `ExitLogic` 接线时无需重写事件体系

---

## 1. 通用规范

### 1.1 文件格式
- UTF-8 编码
- 每行一个 JSON object（JSONL）
- 文件按 run 维度分目录组织
- 单事件不跨行

### 1.2 通用字段
所有事件都必须包含以下字段：

```json
{
  "event_type": "string",
  "run_id": "uuid",
  "ts": "ISO8601 UTC timestamp",
  "pair": "BTC/USDT:USDT"
}
```

可选公共字段：
- `timeframe`
- `strategy_name`
- `strategy_version`
- `mode`
- `metadata`

### 1.3 事件命名
`event_type` 允许以下取值：
- `run_event`
- `signal_event`
- `trade_event`
- `detector_event`
- `regime_snapshot`
- `exit_decision_event`  ← 新增

---

## 2. `run_event`

### 2.1 用途
表示一次 run 的开始、结束、错误、摘要。

### 2.2 `event_subtype`
- `run_start`
- `run_end`
- `run_error`

### 2.3 示例
```json
{
  "event_type": "run_event",
  "event_subtype": "run_start",
  "run_id": "run_20260314_bt_baseline_001",
  "ts": "2026-03-14T08:00:00Z",
  "mode": "backtest",
  "strategy_name": "WyckoffV7_TrueMethod",
  "strategy_version": "v7.3.0",
  "config_hash": "sha256:abc123",
  "timerange": "20230101-20260228",
  "pair_scope": "BTC/USDT:USDT"
}
```

---

## 3. `signal_event`

### 3.1 用途
记录候选信号，不论是否最终成交。

### 3.2 必填字段
- `event_type`
- `run_id`
- `ts`
- `pair`
- `timeframe`
- `side`
- `signal_name`
- `candidate_enter_tag`
- `was_executed`
- `signal_quality_before`
- `signal_quality_after`

### 3.3 推荐字段
- `rejection_reason`
- `rr_estimate`
- `market_env`
- `htf_market_env`
- `macro_phase`
- `bear_market_mode`
- `phase_context_tag`
- `daily_gate_score`
- `daily_tr_exists`
- `composite_intent`
- `p3_swing_down_er_exhaustion`
- `p3_swing_up_er_weak`
- `wyckoff_warning`
- `max_open_trades_blocked`
- `risk_guard_blocked`
- `regime_blocked`

### 3.4 为 future exit hook 预留的字段
- `phase_exit_eligible`
- `phase_exit_candidate_reason`

### 3.5 示例
```json
{
  "event_type": "signal_event",
  "run_id": "run_20260314_bt_baseline_001",
  "ts": "2026-03-14T11:00:00Z",
  "pair": "BTC/USDT:USDT",
  "timeframe": "1h",
  "side": "long",
  "signal_name": "wyckoff_spring",
  "candidate_enter_tag": "wyckoff_spring",
  "was_executed": true,
  "rejection_reason": null,
  "signal_quality_before": 1.0,
  "signal_quality_after": 1.15,
  "rr_estimate": 2.3,
  "market_env": "trading_range",
  "htf_market_env": "transition",
  "macro_phase": "transition",
  "bear_market_mode": false,
  "phase_context_tag": "phase_c",
  "daily_gate_score": 1.0,
  "daily_tr_exists": true,
  "composite_intent": "bullish",
  "p3_swing_down_er_exhaustion": true,
  "phase_exit_eligible": false,
  "phase_exit_candidate_reason": null
}
```

### 3.6 `rejection_reason` 枚举
建议标准化：
- `gate_blocked`
- `regime_blocked`
- `rr_blocked`
- `risk_guard_blocked`
- `max_open_trades_blocked`
- `quality_below_threshold`
- `other`

---

## 4. `trade_event`

### 4.1 用途
记录真实成交行为。  
通过 `event_subtype` 区分：
- `trade_open`
- `trade_close`

---

### 4.2 `trade_open` 示例
```json
{
  "event_type": "trade_event",
  "event_subtype": "trade_open",
  "run_id": "run_20260314_bt_baseline_001",
  "trade_id": "trade_0001",
  "ts": "2026-03-14T12:00:00Z",
  "pair": "BTC/USDT:USDT",
  "side": "long",
  "enter_tag": "wyckoff_axis_support",
  "entry_price": 84500.0,
  "stake_amount": 1000.0,
  "leverage": 2.0,
  "signal_quality_enter": 1.15,
  "market_env_enter": "markup",
  "htf_market_env_enter": "markup",
  "macro_phase_enter": "markup",
  "bear_market_mode_enter": false,
  "phase_context_tag_enter": "phase_d",
  "composite_intent_enter": "bullish",
  "daily_gate_score_enter": 1.0,
  "daily_tr_exists_enter": true,
  "rr_long_standard_enter": 2.3,
  "p3_swing_down_er_exhaustion_enter": true,
  "p3_swing_up_er_weak_enter": false
}
```

---

### 4.3 `trade_close` 示例
```json
{
  "event_type": "trade_event",
  "event_subtype": "trade_close",
  "run_id": "run_20260314_bt_baseline_001",
  "trade_id": "trade_0001",
  "ts": "2026-03-15T02:00:00Z",
  "pair": "BTC/USDT:USDT",
  "side": "long",
  "exit_reason": "roi",
  "exit_tag": "roi",
  "exit_price": 86000.0,
  "profit_pct": 0.021,
  "profit_abs": 21.0,
  "duration_minutes": 840,
  "mfe_pct": 0.045,
  "mae_pct": -0.012,
  "funding_proxy_hours": 14.0,

  "phase_context_tag_exit": "phase_e",
  "phase_exit_candidate": false,
  "phase_exit_triggered": false,
  "phase_exit_reason": null,
  "phase_exit_path": null,
  "exit_logic_module": null,
  "phase_exit_blocked_reason": null
}
```

### 4.4 与 Phase-aware exit 相关字段说明
- `phase_context_tag_exit`：平仓时所在 phase
- `phase_exit_candidate`：exit 层曾考虑过 phase-aware exit
- `phase_exit_triggered`：最终是否由 phase-aware exit 触发
- `phase_exit_reason`：该次 phase exit 的结构原因
- `phase_exit_path`：与 ROI / custom_exit / stop 的关系
- `exit_logic_module`：例如 `ExitLogic`
- `phase_exit_blocked_reason`：候选存在但未触发的原因

---

## 5. `detector_event`

### 5.1 用途
记录关键 detector / regime / state machine 的状态变化或关键触发。

### 5.2 示例
```json
{
  "event_type": "detector_event",
  "run_id": "run_20260314_bt_baseline_001",
  "ts": "2026-03-14T11:00:00Z",
  "pair": "BTC/USDT:USDT",
  "timeframe": "1h",
  "event_family": "wave_er",
  "event_name": "swing_up_er_weak",
  "event_value": true,
  "event_confidence": 1.0,
  "metadata": {
    "effort_ratio": 1.18,
    "result_ratio": 0.82
  }
}
```

### 5.3 为 future exit hook 预留的事件名
- `phase_exit_candidate`
- `phase_exit_confirmed`
- `phase_exit_blocked`
- `phase_exit_window_open`
- `phase_exit_window_expired`

这些事件即使在策略未正式接线前，也可用于 shadow mode 观察。

---

## 6. `regime_snapshot`

### 6.1 用途
固定周期记录环境状态。  
建议每根主执行周期 bar 一条。

### 6.2 示例
```json
{
  "event_type": "regime_snapshot",
  "run_id": "run_20260314_bt_baseline_001",
  "ts": "2026-03-14T11:00:00Z",
  "pair": "BTC/USDT:USDT",
  "timeframe": "1h",
  "market_env": "markup",
  "htf_market_env": "markup",
  "macro_phase": "markup",
  "bear_market_mode": false,
  "phase_context_tag": "phase_d",
  "daily_choch_confirmed": true,
  "dist_cause_sufficient": true,
  "daily_tr_exists": true,
  "daily_gate_score": 1.0
}
```

---

## 7. `exit_decision_event`（新增）

### 7.1 用途
这是给 future exit hook 尤其是 Phase-aware exit 的核心预埋事件。  
它记录：

- 退出层是否认为“应该考虑退出”
- 最终有没有触发
- 若没触发，被谁挡住了
- 预期是为了保护利润、减少过早离场，还是替代 ROI

### 7.2 触发时机
未来建议在以下时机发：
- `ExitLogic` 命中候选退出条件时
- phase 退出确认时
- phase 退出被 ROI / 其他条件阻断时
- phase exit 观察窗口开启 / 过期时

### 7.3 必填字段
- `event_type`
- `run_id`
- `ts`
- `pair`
- `trade_id`
- `phase_context_tag`
- `exit_logic_module`
- `candidate_reason`
- `triggered`

### 7.4 推荐字段
- `blocked_reason`
- `expected_effect`
- `window_opened`
- `window_expired`
- `metadata`

### 7.5 示例：候选但被 ROI 抢先
```json
{
  "event_type": "exit_decision_event",
  "run_id": "run_20260314_bt_baseline_001",
  "trade_id": "trade_0001",
  "ts": "2026-03-15T01:30:00Z",
  "pair": "BTC/USDT:USDT",
  "timeframe": "1h",
  "phase_context_tag": "phase_e",
  "exit_logic_module": "ExitLogic",
  "candidate_reason": "phase_e_distribution",
  "triggered": false,
  "blocked_reason": "roi_first",
  "expected_effect": "protect_profit",
  "metadata": {
    "mfe_pct": 0.041,
    "current_profit_pct": 0.019
  }
}
```

### 7.6 示例：成功触发
```json
{
  "event_type": "exit_decision_event",
  "run_id": "run_20260314_bt_baseline_001",
  "trade_id": "trade_0002",
  "ts": "2026-03-15T05:00:00Z",
  "pair": "BTC/USDT:USDT",
  "timeframe": "1h",
  "phase_context_tag": "phase_d",
  "exit_logic_module": "ExitLogic",
  "candidate_reason": "phase_d_exhaustion",
  "triggered": true,
  "blocked_reason": null,
  "expected_effect": "reduce_overstay",
  "metadata": {
    "phase_score": 0.82
  }
}
```

### 7.7 blocked_reason 枚举建议
- `roi_first`
- `not_confirmed`
- `risk_guard`
- `window_expired`
- `duplicate_exit`
- `disabled`
- `other`

---

## 8. Shadow Mode 约定（针对未接线预埋点）

对于当前“已实现 ExitLogic，但策略未接线”的情形，允许采用 shadow mode：

- 可以输出 `exit_decision_event`
- 可以输出 `phase_exit_candidate` / `phase_exit_blocked`
- 但不真正改变交易行为
- 其目的是先积累样本和诊断 ROI 抢先/持仓寿命问题

这对当前 Phase-aware 退出尤其重要。

---

## 9. 事件文件组织建议

```text
events/
  backtest/
    run_20260314_bt_baseline_001/
      run.jsonl
      signals.jsonl
      trades.jsonl
      detectors.jsonl
      regimes.jsonl
      exits.jsonl
  dry_run/
    run_20260320_dry_001/
      ...
```

说明：
- `exits.jsonl` 用于承载 `exit_decision_event`
- 即使 V1 暂未启用，也应预留路径

---

## 10. 当前结论

本版事件规范已经将 Phase-aware 退出正式纳入：
- 事件类型
- 关键字段
- 枚举值
- shadow mode 观察模式
- 后续接线兼容路径

因此，未来若启动 `Phase-aware exit wiring`，不需要重做事件体系，只需：
1. 打开相应事件输出
2. 打开策略开关
3. 跑最小验证矩阵
4. 让 P5 / P5-WS 读取并审计