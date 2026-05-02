-- ============================================================
-- P5 Strategy Intelligence Layer — Database Views V1
-- 对应 docs/P5-Data-Design.md §5（指标口径）
-- ============================================================

-- -----------------------------------------------------------
-- VW_01: 每周交易摘要（enter_tag × regime 分桶）
-- -----------------------------------------------------------
CREATE VIEW IF NOT EXISTS vw_weekly_trade_summary AS
SELECT
    date(enter_time, 'weekday 0', '-6 days') AS week_start,
    date(exit_time)                           AS week_end,
    enter_tag,
    market_env_enter,
    macro_phase_enter,
    COUNT(*)                                  AS trade_count,
    AVG(profit_pct)                           AS avg_profit_pct,
    SUM(CASE WHEN profit_pct > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) AS win_rate,
    AVG(duration_minutes)                     AS avg_duration_min,
    AVG(mfe_pct)                              AS avg_mfe_pct,
    MAX(mfe_pct)                              AS max_mfe_pct
FROM trades
WHERE profit_pct IS NOT NULL
GROUP BY week_start, enter_tag, market_env_enter, macro_phase_enter;

-- -----------------------------------------------------------
-- VW_02: Enter tag 漂移（最近窗口 vs 全历史基线）
-- -----------------------------------------------------------
CREATE VIEW IF NOT EXISTS vw_enter_tag_drift AS
SELECT
    enter_tag,
    -- 全历史基线
    COUNT(*)                                          AS total_count,
    AVG(profit_pct)                                   AS baseline_avg_profit,
    SUM(CASE WHEN profit_pct > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) AS baseline_win_rate,
    -- 最近 30 天窗口
    SUM(CASE WHEN exit_time >= date('now', '-30 days') THEN 1 ELSE 0 END) AS recent_count,
    AVG(CASE WHEN exit_time >= date('now', '-30 days') THEN profit_pct END) AS recent_avg_profit,
    SUM(CASE WHEN exit_time >= date('now', '-30 days') AND profit_pct > 0 THEN 1 ELSE 0 END) * 100.0
        / NULLIF(SUM(CASE WHEN exit_time >= date('now', '-30 days') THEN 1 ELSE 0 END), 0) AS recent_win_rate
FROM trades
WHERE profit_pct IS NOT NULL
GROUP BY enter_tag;

-- -----------------------------------------------------------
-- VW_03: Exit reason 分布
-- -----------------------------------------------------------
CREATE VIEW IF NOT EXISTS vw_exit_reason_dist AS
SELECT
    exit_reason,
    enter_tag,
    COUNT(*)                                          AS count,
    AVG(profit_pct)                                   AS avg_profit_pct,
    SUM(CASE WHEN profit_pct > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) AS win_rate,
    AVG(duration_minutes)                             AS avg_duration_min,
    AVG(mfe_pct)                                      AS avg_mfe_pct
FROM trades
WHERE profit_pct IS NOT NULL
GROUP BY exit_reason, enter_tag;

-- -----------------------------------------------------------
-- VW_04: Signal execution rate（信号 → 成交 转化率）
-- -----------------------------------------------------------
CREATE VIEW IF NOT EXISTS vw_signal_exec_rate AS
SELECT
    candidate_enter_tag,
    COUNT(*)                                          AS total_signals,
    SUM(was_executed)                                 AS executed_signals,
    ROUND(SUM(was_executed) * 100.0 / COUNT(*), 1)   AS execution_rate_pct,
    AVG(signal_quality_before)                        AS avg_quality_before,
    AVG(signal_quality_after)                         AS avg_quality_after,
    AVG(rr_estimate)                                  AS avg_rr_estimate
FROM signals
GROUP BY candidate_enter_tag;

-- -----------------------------------------------------------
-- VW_05: Signal rejection reasons
-- -----------------------------------------------------------
CREATE VIEW IF NOT EXISTS vw_signal_rejections AS
SELECT
    candidate_enter_tag,
    rejection_reason,
    COUNT(*) AS count
FROM signals
WHERE was_executed = 0 AND rejection_reason IS NOT NULL
GROUP BY candidate_enter_tag, rejection_reason;

-- -----------------------------------------------------------
-- VW_06: Detector 激活率
-- -----------------------------------------------------------
CREATE VIEW IF NOT EXISTS vw_detector_activation AS
SELECT
    event_name,
    event_family,
    COUNT(*)                                          AS activation_count,
    COUNT(DISTINCT pair)                              AS pairs_affected
FROM detector_events
GROUP BY event_name, event_family
ORDER BY activation_count DESC;

-- -----------------------------------------------------------
-- VW_07: Regime 分布快照
-- -----------------------------------------------------------
CREATE VIEW IF NOT EXISTS vw_regime_distribution AS
SELECT
    date(ts)        AS snapshot_date,
    market_env,
    COUNT(*)        AS bar_count,
    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (PARTITION BY date(ts)), 1) AS pct
FROM regime_snapshots
GROUP BY snapshot_date, market_env;

-- -----------------------------------------------------------
-- VW_08: 最新 run 交易明细（含 phase-aware 预埋字段）
-- -----------------------------------------------------------
CREATE VIEW IF NOT EXISTS vw_latest_trades AS
SELECT
    t.*,
    (t.mfe_pct - t.profit_pct) AS profit_giveback,
    CASE WHEN t.mfe_pct >= 0.06
         AND (t.mfe_pct - t.profit_pct) >= 0.025
         AND t.profit_pct >= 0.03
         AND t.enter_tag = 'wyckoff_axis_support'
    THEN 1 ELSE 0 END AS shadow_protect_triggered
FROM trades t
WHERE t.run_id = (
    SELECT run_id FROM experiment_runs
    WHERE status IN ('running', 'completed')
    ORDER BY created_at DESC LIMIT 1
);

-- -----------------------------------------------------------
-- VW_09: Shadow forensic 追踪（axis_support 的回撤保护）
-- -----------------------------------------------------------
CREATE VIEW IF NOT EXISTS vw_shadow_forensic AS
SELECT
    trade_id,
    pair,
    enter_tag,
    enter_time,
    exit_time,
    profit_pct,
    mfe_pct,
    ROUND((mfe_pct - profit_pct) * 100, 2) AS giveback_pct,
    exit_reason,
    CASE WHEN mfe_pct >= 0.06
         AND (mfe_pct - profit_pct) >= 0.025
         AND profit_pct >= 0.03
         AND enter_tag = 'wyckoff_axis_support'
    THEN 1 ELSE 0 END AS shadow_protect_triggered,
    market_env_enter,
    macro_phase_enter,
    phase_context_tag_enter
FROM trades
WHERE enter_tag = 'wyckoff_axis_support'
  AND profit_pct IS NOT NULL
ORDER BY exit_time DESC;

-- -----------------------------------------------------------
-- VW_10: Phase-aware exit 诊断预埋（future wiring）
-- -----------------------------------------------------------
CREATE VIEW IF NOT EXISTS vw_phase_exit_diagnostics AS
SELECT
    trade_id,
    phase_context_tag_exit,
    phase_exit_candidate,
    phase_exit_triggered,
    phase_exit_reason,
    exit_logic_module,
    phase_exit_blocked_reason,
    exit_reason AS actual_exit_reason,
    profit_pct,
    duration_minutes
FROM trades
WHERE phase_exit_candidate = 1
   OR phase_exit_triggered = 1;

-- -----------------------------------------------------------
-- VW_11: Exit decision 事件分析（shadow mode 观察）
-- -----------------------------------------------------------
CREATE VIEW IF NOT EXISTS vw_exit_decision_analysis AS
SELECT
    exit_logic_module,
    candidate_reason,
    SUM(CASE WHEN triggered = 1 THEN 1 ELSE 0 END) AS triggered_count,
    SUM(CASE WHEN triggered = 0 THEN 1 ELSE 0 END) AS blocked_count,
    COUNT(*)                                        AS total_count,
    blocked_reason
FROM exit_decisions
GROUP BY exit_logic_module, candidate_reason, blocked_reason;

-- -----------------------------------------------------------
-- VW_12: 每周健康评分计算
-- -----------------------------------------------------------
CREATE VIEW IF NOT EXISTS vw_weekly_health AS
SELECT
    date(exit_time, 'weekday 0', '-6 days') AS week_start,
    COUNT(*)                                 AS trades_count,
    AVG(profit_pct)                          AS avg_profit,
    SUM(CASE WHEN profit_pct > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) AS win_rate,
    -- 健康分简化计算：胜率分 + 样本分
    ROUND(
        (SUM(CASE WHEN profit_pct > 0 THEN 1 ELSE 0 END) * 0.6 / COUNT(*))
        + MIN(COUNT(*) * 0.4 / 10.0, 0.4),
        2
    ) AS health_score
FROM trades
WHERE profit_pct IS NOT NULL
  AND exit_time IS NOT NULL
GROUP BY week_start;
