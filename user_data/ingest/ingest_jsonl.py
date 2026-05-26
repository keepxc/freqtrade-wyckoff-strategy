#!/usr/bin/env python3
"""Ingest structured JSONL events into the P5 SQLite database.

Usage:
  python ingest_jsonl.py --db p5.db --schema ../db/schema.sql --views ../db/views.sql \
      --events ../events/sample/sample_events.jsonl
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


TABLE_BY_EVENT = {
    "run_event": "experiment_runs",
    "signal_event": "signals",
    "trade_event": "trades",
    "detector_event": "detector_events",
    "regime_snapshot": "regime_snapshots",
    "exit_decision_event": "exit_decisions",
}


def connect_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def execute_sql_file(conn: sqlite3.Connection, path: Optional[Path]) -> None:
    if not path:
        return
    text = path.read_text(encoding="utf-8")
    if text.strip():
        conn.executescript(text)


def normalize_bool(value: Any) -> Any:
    if isinstance(value, bool):
        return 1 if value else 0
    return value


def normalize_common(record: Dict[str, Any]) -> Dict[str, Any]:
    normalized = {}
    for k, v in record.items():
        if isinstance(v, dict):
            normalized[k] = json.dumps(v, ensure_ascii=False, sort_keys=True)
        elif isinstance(v, list):
            normalized[k] = json.dumps(v, ensure_ascii=False)
        else:
            normalized[k] = normalize_bool(v)
    return normalized


def upsert_experiment_run(conn: sqlite3.Connection, record: Dict[str, Any]) -> None:
    subtype = record.get("event_subtype")
    run_id = record["run_id"]
    if subtype == "run_start":
        payload = {
            "run_id": run_id,
            "run_type": record.get("run_type", "analysis"),
            "mode": record.get("mode", "backtest"),
            "strategy_name": record.get("strategy_name", "unknown"),
            "strategy_version": record.get("strategy_version"),
            "git_commit": record.get("git_commit"),
            "config_hash": record.get("config_hash"),
            "parameter_set_name": record.get("parameter_set_name"),
            "parent_baseline_run_id": record.get("parent_baseline_run_id"),
            "timerange": record.get("timerange"),
            "pair_scope": record.get("pair_scope"),
            "created_at": record.get("ts"),
            "ended_at": None,
            "status": record.get("status", "running"),
            "notes": record.get("notes"),
        }
        cols = ", ".join(payload.keys())
        placeholders = ", ".join([":" + k for k in payload.keys()])
        updates = ", ".join([f"{k}=excluded.{k}" for k in payload.keys() if k != "run_id"])
        conn.execute(
            f"INSERT INTO experiment_runs ({cols}) VALUES ({placeholders}) "
            f"ON CONFLICT(run_id) DO UPDATE SET {updates}",
            payload,
        )
    elif subtype in {"run_end", "run_error"}:
        conn.execute(
            "UPDATE experiment_runs SET ended_at=?, status=?, notes=COALESCE(?, notes) WHERE run_id=?",
            (record.get("ts"), record.get("status", "completed" if subtype == "run_end" else "failed"), record.get("notes"), run_id),
        )
    else:
        raise ValueError(f"Unsupported run_event subtype: {subtype}")


TRADE_OPEN_MAP = {
    "trade_id": "trade_id",
    "run_id": "run_id",
    "pair": "pair",
    "side": "side",
    "ts": "enter_time",
    "enter_tag": "enter_tag",
    "entry_price": "entry_price",
    "stake_amount": "stake_amount",
    "leverage": "leverage",
    "signal_quality_enter": "signal_quality_enter",
    "market_env_enter": "market_env_enter",
    "htf_market_env_enter": "htf_market_env_enter",
    "macro_phase_enter": "macro_phase_enter",
    "bear_market_mode_enter": "bear_market_mode_enter",
    "phase_context_tag_enter": "phase_context_tag_enter",
    "composite_intent_enter": "composite_intent_enter",
    "daily_gate_score_enter": "daily_gate_score_enter",
    "daily_tr_exists_enter": "daily_tr_exists_enter",
    "rr_long_standard_enter": "rr_long_standard_enter",
    "rr_short_standard_enter": "rr_short_standard_enter",
    "p3_swing_down_er_exhaustion_enter": "p3_swing_down_er_exhaustion_enter",
    "p3_swing_up_er_weak_enter": "p3_swing_up_er_weak_enter",
}

TRADE_CLOSE_MAP = {
    "ts": "exit_time",
    "exit_reason": "exit_reason",
    "exit_tag": "exit_tag",
    "exit_price": "exit_price",
    "profit_pct": "profit_pct",
    "profit_abs": "profit_abs",
    "duration_minutes": "duration_minutes",
    "mfe_pct": "mfe_pct",
    "mae_pct": "mae_pct",
    "funding_proxy_hours": "funding_proxy_hours",
    "signal_bar_close": "signal_bar_close",
    "t1_price": "t1_price",
    "t1_triggered": "t1_triggered",
}


# Module-level cache for trade_close matching across calls
_close_match_cache: Dict[str, str] = {}  # pair → matched trade_id


def upsert_trade(conn: sqlite3.Connection, record: Dict[str, Any]) -> None:
    subtype = record.get("event_subtype")
    if subtype == "trade_open":
        payload = {dst: normalize_bool(record.get(src)) for src, dst in TRADE_OPEN_MAP.items()}
        cols = ", ".join(payload.keys())
        placeholders = ", ".join([":" + k for k in payload.keys()])
        updates = ", ".join([f"{k}=excluded.{k}" for k in payload.keys() if k != "trade_id"])
        conn.execute(
            f"INSERT INTO trades ({cols}) VALUES ({placeholders}) "
            f"ON CONFLICT(trade_id) DO UPDATE SET {updates}",
            payload,
        )
    elif subtype == "trade_close":
        internal_trade_id = record["trade_id"]
        pair = record.get("pair", "")
        close_ts = record.get("ts", "")
        sets = ", ".join([f"{dst}=?" for dst in TRADE_CLOSE_MAP.values()])
        values = [normalize_bool(record.get(src)) for src in TRADE_CLOSE_MAP.keys()]

        # Layer 1: exact trade_id match
        matched = conn.execute(
            "SELECT trade_id, exit_time FROM trades WHERE trade_id=?",
            (internal_trade_id,)).fetchone()

        # Layer 2: same pair, OPEN (exit_time IS NULL), non-backtest, enter_time <= close_ts
        if not matched:
            matched = conn.execute(
                "SELECT trade_id, exit_time FROM trades "
                "WHERE pair=? AND exit_time IS NULL "
                "AND run_id NOT LIKE '%backtest%' AND run_id NOT LIKE '%demo%' "
                "AND run_id NOT LIKE '%test%' "
                "AND enter_time IS NOT NULL AND enter_time <= ? "
                "ORDER BY enter_time DESC LIMIT 1",
                (pair, close_ts)).fetchone()
            if matched:
                _close_match_cache[pair] = matched["trade_id"]

        # Layer 3: only accept if Layer 2 already matched this pair in this run
        if not matched:
            recent_tid = _close_match_cache.get(pair)
            if recent_tid:
                matched = conn.execute(
                    "SELECT trade_id, exit_time FROM trades WHERE trade_id=?",
                    (recent_tid,)).fetchone()
                # Don't overwrite newer close data
                if matched and matched["exit_time"] and close_ts <= matched["exit_time"]:
                    matched = None

        if matched:
            actual_trade_id = matched["trade_id"]
            existing_exit = matched["exit_time"]
            if existing_exit and close_ts <= existing_exit:
                return  # Already have newer close data
            conn.execute(
                f"UPDATE trades SET {sets} WHERE trade_id=?",
                values + [actual_trade_id])
        else:
            # Fallback: create placeholder (historical data with no matching open)
            existing = conn.execute(
                "SELECT 1 FROM trades WHERE trade_id=?",
                (internal_trade_id,)).fetchone()
            if not existing:
                placeholder = {
                    "trade_id": internal_trade_id,
                    "run_id": record.get("run_id", ""),
                    "pair": pair,
                    "side": record.get("side"),
                    "enter_time": None,
                }
                cols = ", ".join(placeholder.keys())
                ph = ", ".join([":" + k for k in placeholder.keys()])
                conn.execute(f"INSERT INTO trades ({cols}) VALUES ({ph})", placeholder)
            conn.execute(f"UPDATE trades SET {sets} WHERE trade_id=?",
                         values + [internal_trade_id])
    else:
        raise ValueError(f"Unsupported trade_event subtype: {subtype}")


INSERT_DIRECT_TABLES = {
    "signals": [
        "signal_id", "run_id", "ts", "pair", "timeframe", "side", "signal_name",
        "candidate_enter_tag", "was_executed", "rejection_reason", "signal_quality_before",
        "signal_quality_after", "rr_estimate", "market_env", "htf_market_env", "macro_phase",
        "bear_market_mode", "phase_context_tag", "daily_gate_score", "daily_tr_exists",
        "composite_intent", "p3_swing_down_er_exhaustion", "p3_swing_up_er_weak",
        "wyckoff_warning", "max_open_trades_blocked", "risk_guard_blocked", "regime_blocked",
    ],
    "detector_events": [
        "event_id", "run_id", "ts", "pair", "timeframe", "event_family", "event_name",
        "event_value", "event_confidence", "metadata_json",
    ],
    "regime_snapshots": [
        "snapshot_id", "run_id", "ts", "pair", "timeframe", "market_env", "htf_market_env",
        "macro_phase", "bear_market_mode", "phase_context_tag", "daily_choch_confirmed",
        "dist_cause_sufficient", "daily_tr_exists", "daily_gate_score",
    ],
    "exit_decisions": [
        "decision_id", "run_id", "trade_id", "ts", "pair", "timeframe",
        "phase_context_tag", "exit_logic_module", "candidate_reason",
        "triggered", "blocked_reason", "expected_effect", "metadata_json",
    ],
}


def insert_direct(conn: sqlite3.Connection, table: str, record: Dict[str, Any]) -> None:
    cols_list = INSERT_DIRECT_TABLES[table]
    payload = {col: normalize_bool(record.get(col)) for col in cols_list}
    if table == "detector_events" and "metadata_json" not in payload:
        payload["metadata_json"] = None
    cols = ", ".join(payload.keys())
    placeholders = ", ".join([":" + k for k in payload.keys()])
    pkey = cols_list[0]
    updates = ", ".join([f"{k}=excluded.{k}" for k in payload.keys() if k != pkey])
    conn.execute(
        f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) ON CONFLICT({pkey}) DO UPDATE SET {updates}",
        payload,
    )


def ingest_record(conn: sqlite3.Connection, record: Dict[str, Any]) -> None:
    event_type = record.get("event_type")
    if event_type == "run_event":
        upsert_experiment_run(conn, record)
    elif event_type == "trade_event":
        upsert_trade(conn, record)
    elif event_type in {"signal_event", "detector_event", "regime_snapshot", "exit_decision_event"}:
        table = TABLE_BY_EVENT[event_type]
        normalized = normalize_common(record)
        # metadata 字段处理
        if "metadata_json" not in normalized and "metadata" in record:
            normalized["metadata_json"] = json.dumps(record["metadata"], ensure_ascii=False, sort_keys=True)
        # exit_decision_event: event_id → decision_id 映射
        if event_type == "exit_decision_event":
            normalized["decision_id"] = record.get("event_id", "")
            if "metadata" in record and isinstance(record["metadata"], dict):
                normalized["metadata_json"] = json.dumps(record["metadata"], ensure_ascii=False, sort_keys=True)
        insert_direct(conn, table, normalized)
    else:
        raise ValueError(f"Unsupported event_type: {event_type}")


def iter_jsonl(paths: Iterable[Path]) -> Iterable[Dict[str, Any]]:
    for path in paths:
        with path.open("r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid JSON in {path} line {lineno}: {exc}") from exc


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True, help="Path to SQLite database file")
    p.add_argument("--schema", help="Path to schema.sql")
    p.add_argument("--views", help="Path to views.sql")
    p.add_argument("--events", nargs="+", required=True, help="One or more JSONL event files")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect_db(db_path)
    try:
        execute_sql_file(conn, Path(args.schema) if args.schema else None)
        execute_sql_file(conn, Path(args.views) if args.views else None)
        count = 0
        # 确保 run.jsonl（含 run_start 事件）优先处理，避免外键约束失败
        all_paths = [Path(p) for p in args.events]
        sorted_paths = sorted(all_paths, key=lambda p: (0 if p.name == "run.jsonl" else 1, p.name))
        for record in iter_jsonl(sorted_paths):
            ingest_record(conn, record)
            count += 1
        conn.commit()
        print(f"[OK] ingested {count} event records into {db_path}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
