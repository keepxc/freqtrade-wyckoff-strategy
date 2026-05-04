#!/usr/bin/env python3
"""
P5 market.db 垃圾回收脚本。
清理 backtest 污染数据、孤立记录、过期周报。

运行: python scripts/gc_market_db.py [--db PATH] [--dry-run]
"""

import sqlite3
import sys
from pathlib import Path
from datetime import datetime, timedelta

DEFAULT_DB = Path(__file__).parent.parent / "user_data" / "market.db"


def gc_backtest_data(conn: sqlite3.Connection, dry_run: bool) -> int:
    """删除所有 mode='backtest' 的 experiment_runs 及关联数据。"""
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM experiment_runs WHERE mode = 'backtest'")
    count = cur.fetchone()[0]
    if count == 0:
        return 0

    if not dry_run:
        # 先删子表（FK 级联不一定开着）
        for table in ["signals", "regime_snapshots", "trades", "detector_events",
                       "exit_decision_events", "parameter_versions"]:
            cur.execute(f"""
                DELETE FROM {table} WHERE run_id IN (
                    SELECT run_id FROM experiment_runs WHERE mode = 'backtest'
                )
            """)
        cur.execute("DELETE FROM experiment_runs WHERE mode = 'backtest'")
        conn.commit()

    return count


def gc_orphan_records(conn: sqlite3.Connection, dry_run: bool) -> dict:
    """删除 run_id 不存在于 experiment_runs 的孤立记录。"""
    cur = conn.cursor()
    stats = {}

    for table in ["signals", "regime_snapshots", "trades", "detector_events",
                   "exit_decision_events", "parameter_versions"]:
        cur.execute(f"""
            SELECT COUNT(*) FROM {table} t
            WHERE NOT EXISTS (
                SELECT 1 FROM experiment_runs e WHERE e.run_id = t.run_id
            )
        """)
        count = cur.fetchone()[0]
        stats[table] = count

        if count > 0 and not dry_run:
            cur.execute(f"""
                DELETE FROM {table} WHERE run_id NOT IN (
                    SELECT run_id FROM experiment_runs
                )
            """)
            conn.commit()

    return stats


def gc_old_weekly_reports(conn: sqlite3.Connection, days: int, dry_run: bool) -> int:
    """删除超过 N 天的周报。"""
    cur = conn.cursor()
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    cur.execute("SELECT COUNT(*) FROM weekly_reports WHERE week_end < ?", (cutoff,))
    count = cur.fetchone()[0]

    if count > 0 and not dry_run:
        cur.execute("DELETE FROM weekly_reports WHERE week_end < ?", (cutoff,))
        conn.commit()

    return count


def report_table_sizes(conn: sqlite3.Connection):
    """输出各表行数。"""
    cur = conn.cursor()
    tables = [
        "experiment_runs", "signals", "regime_snapshots", "trades",
        "detector_events", "exit_decision_events", "parameter_versions",
        "proposals", "weekly_reports",
    ]
    print("\n表大小:")
    for t in tables:
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        print(f"  {t:30s} {cur.fetchone()[0]:>10,}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="P5 market.db garbage collection")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--dry-run", action="store_true", help="只报告，不删除")
    parser.add_argument("--keep-weekly-days", type=int, default=90, help="保留最近 N 天的周报")
    args = parser.parse_args()

    if not args.db.exists():
        print(f"数据库不存在: {args.db}")
        sys.exit(1)

    conn = sqlite3.connect(str(args.db))
    mode = "DRY RUN" if args.dry_run else "LIVE"

    print(f"P5 Garbage Collection [{mode}]")
    print(f"数据库: {args.db}")
    print("=" * 40)

    # 1. Backtest 数据
    bt = gc_backtest_data(conn, args.dry_run)
    print(f"\n[1] Backtest 清理: {bt} experiment_runs" + (" (dry run, skipped)" if args.dry_run and bt else ""))

    # 2. 孤立记录
    orphans = gc_orphan_records(conn, args.dry_run)
    print(f"\n[2] 孤立记录清理:")
    total_orphans = sum(orphans.values())
    for table, count in orphans.items():
        if count > 0:
            label = " (dry run)" if args.dry_run else ""
            print(f"  {table}: {count}{label}")
    if total_orphans == 0:
        print("  无孤立记录 ✓")

    # 3. 过期周报
    old_reports = gc_old_weekly_reports(conn, args.keep_weekly_days, args.dry_run)
    print(f"\n[3] 过期周报清理 (> {args.keep_weekly_days} 天): {old_reports}")

    # 4. 报告
    report_table_sizes(conn)

    conn.close()

    total_cleaned = bt + total_orphans + old_reports
    print(f"\n{'=' * 40}")
    if total_cleaned == 0:
        print("数据库干净，无需清理 ✓")
    elif args.dry_run:
        print(f"将清理 {total_cleaned} 条记录。去掉 --dry-run 执行。")
    else:
        print(f"已清理 {total_cleaned} 条记录。")


if __name__ == "__main__":
    main()
