-- P5 V1-B 迁移：添加 exit_decision_events 表
-- 用于记录 V1B-3 的退出决策事件
-- 应用方式：sqlite3 <db_path> < v1b_001_exit_decision_events.sql

CREATE TABLE IF NOT EXISTS exit_decision_events (
    event_id            TEXT    PRIMARY KEY,
    run_id              TEXT    NOT NULL REFERENCES experiment_runs(run_id),
    ts                  TEXT    NOT NULL,
    trade_id            TEXT,
    pair                TEXT    NOT NULL,
    phase_context_tag   TEXT,
    exit_logic_module   TEXT    NOT NULL,  -- e.g. 'phase_aware_exit', 'p3_swing_up_er_weak'
    candidate_reason    TEXT    NOT NULL,
    triggered           INTEGER NOT NULL DEFAULT 0,   -- 1=triggered, 0=evaluated but not triggered
    blocked_reason      TEXT,
    confidence          REAL,
    metadata_json       TEXT,
    created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_exit_decision_events_run_id
    ON exit_decision_events(run_id);

CREATE INDEX IF NOT EXISTS idx_exit_decision_events_trade_id
    ON exit_decision_events(trade_id);

CREATE INDEX IF NOT EXISTS idx_exit_decision_events_ts
    ON exit_decision_events(ts);
