#!/usr/bin/env python3
"""
P5 数据完整性 lint 检查。
运行: python scripts/lint_p5.py [--db PATH] [--jsonl PATH]
退出码: 0=通过, 1=有错误
"""

import json
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ─── 配置 ────────────────────────────────────────────────────────
DEFAULT_DB = Path(__file__).parent.parent / "user_data" / "market.db"
DEFAULT_JSONL_DIR = Path(__file__).parent.parent / "user_data" / "events"

VALID_EVENT_TYPES = {
    "signal", "trade_open", "trade_close", "regime_snapshot",
    "detector_event", "daily_analysis", "weekly_summary",
    "experiment_start", "experiment_end",
}

VALID_SIDES = {"long", "short"}

VALID_MODES = {"backtest", "dry_run", "live"}

# ─── 结果收集 ─────────────────────────────────────────────────────
@dataclass
class LintResult:
    passed: bool = True
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def error(self, rule: str, msg: str, fix: str = ""):
        self.passed = False
        entry = f"[ERROR] {rule}: {msg}"
        if fix:
            entry += f"\n  Fix: {fix}"
        self.errors.append(entry)

    def warn(self, rule: str, msg: str):
        entry = f"[WARN]  {rule}: {msg}"
        self.warnings.append(entry)

    def print_all(self):
        for w in self.warnings:
            print(w)
        for e in self.errors:
            print(e)


# ─── 规则 1: 数据库中不能有 backtest 数据 ──────────────────────────
def lint_no_backtest(conn: sqlite3.Connection, result: LintResult):
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM experiment_runs WHERE mode = 'backtest'")
    count = cur.fetchone()[0]
    if count > 0:
        result.error(
            "no-backtest-data",
            f"Found {count} experiment_runs with mode='backtest'",
            "DELETE FROM experiment_runs WHERE mode = 'backtest'; "
            "(signals/regime_snapshots/trades/detector_events 会因 FK 级联删除)",
        )


# ─── 规则 2: signals 必须有 run_id 且对应 experiment_runs 存在 ─────
def lint_orphan_signals(conn: sqlite3.Connection, result: LintResult):
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*) FROM signals s
        WHERE NOT EXISTS (
            SELECT 1 FROM experiment_runs e WHERE e.run_id = s.run_id
        )
    """)
    count = cur.fetchone()[0]
    if count > 0:
        result.error(
            "orphan-signals",
            f"Found {count} signals with no matching experiment_runs",
            "DELETE FROM signals WHERE run_id NOT IN (SELECT run_id FROM experiment_runs);",
        )


# ─── 规则 3: regime_snapshots 必须有有效 run_id ──────────────────
def lint_orphan_snapshots(conn: sqlite3.Connection, result: LintResult):
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*) FROM regime_snapshots r
        WHERE NOT EXISTS (
            SELECT 1 FROM experiment_runs e WHERE e.run_id = r.run_id
        )
    """)
    count = cur.fetchone()[0]
    if count > 0:
        result.error(
            "orphan-snapshots",
            f"Found {count} regime_snapshots with no matching experiment_runs",
            "DELETE FROM regime_snapshots WHERE run_id NOT IN (SELECT run_id FROM experiment_runs);",
        )


# ─── 规则 4: signals 的 side 必须是 long/short ───────────────────
def lint_signal_sides(conn: sqlite3.Connection, result: LintResult):
    cur = conn.cursor()
    cur.execute(f"""
        SELECT DISTINCT side FROM signals
        WHERE side NOT IN ({",".join("?" for _ in VALID_SIDES)})
    """, list(VALID_SIDES))
    bad = [r[0] for r in cur.fetchall()]
    if bad:
        result.error(
            "signal-side-invalid",
            f"signals.side contains invalid values: {bad}",
            "UPDATE signals SET side = LOWER(side) WHERE side NOT IN ('long', 'short');",
        )


# ─── 规则 5: weekly_reports 健康分合理性 ──────────────────────────
def lint_weekly_reports(conn: sqlite3.Connection, result: LintResult):
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM weekly_reports WHERE health_score < 0 OR health_score > 100")
    count = cur.fetchone()[0]
    if count > 0:
        result.warn("weekly-report-score", f"{count} weekly_reports with health_score outside 0-100 range")


# ─── 规则 6: JSONL 事件文件格式检查 ──────────────────────────────
def lint_jsonl_files(jsonl_dir: Path, result: LintResult):
    if not jsonl_dir.exists():
        result.warn("jsonl-dir-missing", f"JSONL directory not found: {jsonl_dir}")
        return

    for f in sorted(jsonl_dir.glob("*.jsonl")):
        if f.name.startswith("_") or f.name.startswith("."):
            continue
        for line_num, line in enumerate(f.read_text().splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                result.error(
                    "jsonl-parse",
                    f"{f.name}:{line_num}: invalid JSON",
                    f"Fix the JSON syntax at line {line_num}",
                )
                continue

            if "event_type" not in obj:
                result.error(
                    "jsonl-event-type",
                    f"{f.name}:{line_num}: missing event_type field",
                    "Add event_type field (valid: " + ", ".join(sorted(VALID_EVENT_TYPES)) + ")",
                )
            elif obj["event_type"] not in VALID_EVENT_TYPES:
                result.error(
                    "jsonl-event-type",
                    f"{f.name}:{line_num}: invalid event_type '{obj['event_type']}'",
                    "Valid types: " + ", ".join(sorted(VALID_EVENT_TYPES)),
                )

            if obj.get("event_type") == "trade_close" and "trade_id" not in obj:
                result.error(
                    "jsonl-trade-close",
                    f"{f.name}:{line_num}: trade_close event missing trade_id",
                    "Add trade_id field to trade_close events",
                )


# ─── 主入口 ──────────────────────────────────────────────────────
def main():
    import argparse
    parser = argparse.ArgumentParser(description="P5 data integrity lint")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="Path to market.db")
    parser.add_argument("--jsonl", type=Path, default=DEFAULT_JSONL_DIR, help="Path to events directory")
    args = parser.parse_args()

    result = LintResult()

    # DB checks
    if not args.db.exists():
        result.warn("db-missing", f"Database not found: {args.db}")
    else:
        conn = sqlite3.connect(str(args.db))
        lint_no_backtest(conn, result)
        lint_orphan_signals(conn, result)
        lint_orphan_snapshots(conn, result)
        lint_signal_sides(conn, result)
        lint_weekly_reports(conn, result)
        conn.close()

    # JSONL checks
    lint_jsonl_files(args.jsonl, result)

    # Output
    result.print_all()

    total_checks = 7
    passed_checks = total_checks - len(result.errors)
    print(f"\n{'=' * 40}")
    print(f"P5 Lint: {passed_checks}/{total_checks} checks passed")
    if result.warnings:
        print(f"Warnings: {len(result.warnings)}")
    if result.errors:
        print(f"Errors: {len(result.errors)}")
    print(f"{'=' * 40}")

    sys.exit(0 if result.passed else 1)


if __name__ == "__main__":
    main()
