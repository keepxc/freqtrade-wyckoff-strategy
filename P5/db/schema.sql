-- ============================================================
-- P5 Strategy Intelligence Layer — Database Schema V1
-- 对应 docs/P5-Data-Design.md §4
-- ============================================================

-- 4.1 实验/运行记录
CREATE TABLE IF NOT EXISTS experiment_runs (
    run_id              TEXT PRIMARY KEY,
    run_type            TEXT DEFAULT 'baseline',   -- baseline|candidate|dry_run|live|analysis
    mode                TEXT NOT NULL,             -- backtest|dry_run|live
    strategy_name       TEXT,
    strategy_version    TEXT,
    git_commit          TEXT,
    config_hash         TEXT,
    parameter_set_name  TEXT,
    parent_baseline_run_id TEXT,
    timerange           TEXT,
    pair_scope          TEXT,
    created_at          TIMESTAMP DEFAULT (datetime('now')),
    ended_at            TIMESTAMP,
    status              TEXT DEFAULT 'running',    -- running|completed|failed|approved|rejected|rolled_back
    notes               TEXT
);

-- 4.2 已成交交易
CREATE TABLE IF NOT EXISTS trades (
    trade_id            TEXT PRIMARY KEY,
    run_id              TEXT NOT NULL,
    pair                TEXT,
    side                TEXT,                      -- long|short
    enter_time          TIMESTAMP,
    exit_time           TIMESTAMP,
    enter_tag           TEXT,
    exit_reason         TEXT,
    exit_tag            TEXT,
    entry_price         REAL,
    exit_price          REAL,
    profit_pct          REAL,
    profit_abs          REAL,
    duration_minutes    INTEGER,
    stake_amount        REAL,
    leverage            REAL DEFAULT 1.0,

    -- 入场快照
    signal_quality_enter    REAL,
    market_env_enter        TEXT,
    htf_market_env_enter    TEXT,
    macro_phase_enter       TEXT,
    bear_market_mode_enter  INTEGER,
    phase_context_tag_enter TEXT,
    composite_intent_enter  TEXT,
    daily_gate_score_enter  REAL,
    daily_tr_exists_enter   INTEGER,
    rr_long_standard_enter  REAL,
    rr_short_standard_enter REAL,
    p3_swing_down_er_exhaustion_enter INTEGER,
    p3_swing_up_er_weak_enter       INTEGER,

    -- 退出快照
    mfe_pct             REAL,
    mae_pct             REAL,
    funding_proxy_hours REAL,

    -- 滑点审计 + T1 保本止损审计 (commit 57cfd24)
    signal_bar_close    REAL,
    t1_price            REAL,
    t1_triggered        REAL,

    -- Phase-aware exit 预埋 (P5 V1.1)
    phase_context_tag_exit   TEXT,
    phase_exit_candidate     INTEGER DEFAULT 0,
    phase_exit_triggered     INTEGER DEFAULT 0,
    phase_exit_reason        TEXT,
    phase_exit_path          TEXT,
    exit_logic_module        TEXT,
    phase_exit_blocked_reason TEXT,

    FOREIGN KEY (run_id) REFERENCES experiment_runs(run_id)
);

-- 4.3 候选信号（含未成交）
CREATE TABLE IF NOT EXISTS signals (
    signal_id           TEXT PRIMARY KEY,
    run_id              TEXT NOT NULL,
    ts                  TIMESTAMP,
    pair                TEXT,
    timeframe           TEXT,
    side                TEXT,
    signal_name         TEXT,
    candidate_enter_tag TEXT,
    was_executed        INTEGER,                  -- 0=未成交, 1=已成交
    rejection_reason    TEXT,

    signal_quality_before REAL,
    signal_quality_after  REAL,
    rr_estimate         REAL,
    market_env          TEXT,
    htf_market_env      TEXT,
    macro_phase         TEXT,
    bear_market_mode    INTEGER,
    phase_context_tag   TEXT,
    daily_gate_score    REAL,
    daily_tr_exists     INTEGER,
    composite_intent    TEXT,
    p3_swing_down_er_exhaustion INTEGER,
    p3_swing_up_er_weak         INTEGER,
    wyckoff_warning     INTEGER,
    max_open_trades_blocked INTEGER,
    risk_guard_blocked  INTEGER,
    regime_blocked      INTEGER,

    -- Future exit hook 预埋
    phase_exit_eligible         INTEGER DEFAULT 0,
    phase_exit_candidate_reason TEXT,

    FOREIGN KEY (run_id) REFERENCES experiment_runs(run_id)
);

-- 4.4 Detector / 状态机事件
CREATE TABLE IF NOT EXISTS detector_events (
    event_id            TEXT PRIMARY KEY,
    run_id              TEXT NOT NULL,
    ts                  TIMESTAMP,
    pair                TEXT,
    timeframe           TEXT,
    event_family        TEXT,
    event_name          TEXT,
    event_value         TEXT,
    event_confidence    REAL,
    metadata_json       TEXT,

    FOREIGN KEY (run_id) REFERENCES experiment_runs(run_id)
);

-- 4.5 环境状态快照
CREATE TABLE IF NOT EXISTS regime_snapshots (
    snapshot_id         TEXT PRIMARY KEY,
    run_id              TEXT NOT NULL,
    ts                  TIMESTAMP,
    pair                TEXT,
    timeframe           TEXT,
    market_env          TEXT,
    htf_market_env      TEXT,
    macro_phase         TEXT,
    bear_market_mode    INTEGER,
    phase_context_tag   TEXT,
    daily_choch_confirmed INTEGER,
    dist_cause_sufficient INTEGER,
    daily_tr_exists     INTEGER,
    daily_gate_score    REAL,
    daily_sm_v2         INTEGER DEFAULT 0,

    FOREIGN KEY (run_id) REFERENCES experiment_runs(run_id)
);

-- 4.6 参数版本变更
CREATE TABLE IF NOT EXISTS parameter_versions (
    param_version_id    TEXT PRIMARY KEY,
    run_id              TEXT NOT NULL,
    parameter_name      TEXT,
    old_value           TEXT,
    new_value           TEXT,
    parameter_class     TEXT,                     -- structure|arbitration|injection|deployment|exit
    reason              TEXT,
    risk_level          TEXT DEFAULT 'medium',    -- low|medium|high
    proposal_id         TEXT,

    FOREIGN KEY (run_id) REFERENCES experiment_runs(run_id)
);

-- 4.7 AI 周报归档
CREATE TABLE IF NOT EXISTS weekly_reports (
    report_id           TEXT PRIMARY KEY,
    week_start          DATE NOT NULL,
    week_end            DATE NOT NULL,
    run_scope           TEXT,
    report_type         TEXT,                     -- dry_run|live|backtest_summary
    summary_markdown    TEXT,
    health_score        REAL,
    warnings_json       TEXT,
    created_at          TIMESTAMP DEFAULT (datetime('now'))
);

-- 4.8 AI 诊断与提案归档
CREATE TABLE IF NOT EXISTS proposals (
    proposal_id         TEXT PRIMARY KEY,
    created_at          TIMESTAMP DEFAULT (datetime('now')),
    proposal_type       TEXT,                     -- diagnostic|experiment|rollback_suggestion
    problem_statement   TEXT,
    evidence_json       TEXT,
    risk_level          TEXT,
    sample_sufficiency  TEXT,                     -- insufficient|minimal|sufficient
    recommended_action  TEXT,
    experiment_matrix_json TEXT,
    status              TEXT DEFAULT 'draft'      -- draft|reviewed|accepted|rejected|executed
);

-- 4.9 Phase-aware 退出决策记录
CREATE TABLE IF NOT EXISTS exit_decisions (
    decision_id         TEXT PRIMARY KEY,
    run_id              TEXT NOT NULL,
    trade_id            TEXT,
    ts                  TIMESTAMP,
    pair                TEXT,
    timeframe           TEXT,
    phase_context_tag   TEXT,
    exit_logic_module   TEXT,
    candidate_reason    TEXT,
    triggered           INTEGER,
    blocked_reason      TEXT,
    expected_effect     TEXT,
    metadata_json       TEXT,

    FOREIGN KEY (run_id) REFERENCES experiment_runs(run_id)
);

-- ============================================================
-- 索引
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_trades_run           ON trades(run_id);
CREATE INDEX IF NOT EXISTS idx_trades_enter_tag     ON trades(enter_tag);
CREATE INDEX IF NOT EXISTS idx_trades_exit_reason   ON trades(exit_reason);
CREATE INDEX IF NOT EXISTS idx_trades_enter_time    ON trades(enter_time);

CREATE INDEX IF NOT EXISTS idx_signals_run          ON signals(run_id);
CREATE INDEX IF NOT EXISTS idx_signals_ts           ON signals(ts);
CREATE INDEX IF NOT EXISTS idx_signals_tag          ON signals(candidate_enter_tag);

CREATE INDEX IF NOT EXISTS idx_detector_run         ON detector_events(run_id);
CREATE INDEX IF NOT EXISTS idx_detector_family      ON detector_events(event_family);

CREATE INDEX IF NOT EXISTS idx_regime_run           ON regime_snapshots(run_id);
CREATE INDEX IF NOT EXISTS idx_regime_env           ON regime_snapshots(market_env);

CREATE INDEX IF NOT EXISTS idx_param_versions_run   ON parameter_versions(run_id);

CREATE INDEX IF NOT EXISTS idx_exit_decisions_run   ON exit_decisions(run_id);
CREATE INDEX IF NOT EXISTS idx_exit_decisions_trade ON exit_decisions(trade_id);
