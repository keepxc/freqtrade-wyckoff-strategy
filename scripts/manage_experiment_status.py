#!/usr/bin/env python3
"""
P5 V2-4: baseline / candidate / rollback 状态管理脚本

支持操作：
  list        列出所有实验运行（按状态筛选）
  promote     将 candidate 晋升为新 baseline（旧 baseline 状态改为 archived）
  rollback    将当前 baseline 回滚到指定的历史 baseline
  archive     将指定 run_id 标记为 archived（不再活跃）
  show        查看指定 run_id 的完整信息

用法：
  python scripts/manage_experiment_status.py list
  python scripts/manage_experiment_status.py list --run-type candidate
  python scripts/manage_experiment_status.py promote --run-id <candidate_run_id>
  python scripts/manage_experiment_status.py rollback --to <target_baseline_run_id>
  python scripts/manage_experiment_status.py archive --run-id <run_id>
  python scripts/manage_experiment_status.py show --run-id <run_id>
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


DEFAULT_DB = "user_data/market.db"


def connect_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── list ──────────────────────────────────────────────────────────────────────

def cmd_list(conn: sqlite3.Connection, run_type: Optional[str], status: Optional[str]) -> None:
    sql = "SELECT run_id, run_type, mode, status, strategy_version, parameter_set_name, timerange, created_at FROM experiment_runs WHERE 1=1"
    params: list = []
    if run_type:
        sql += " AND run_type = ?"
        params.append(run_type)
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY created_at DESC"

    rows = conn.execute(sql, params).fetchall()
    if not rows:
        print("（无符合条件的实验记录）")
        return

    print(f"{'run_id':<36}  {'run_type':<12}  {'status':<12}  {'strategy_ver':<16}  {'timerange':<26}  created_at")
    print("-" * 120)
    for r in rows:
        print(
            f"{r['run_id']:<36}  {(r['run_type'] or ''):<12}  {(r['status'] or ''):<12}  "
            f"{(r['strategy_version'] or ''):<16}  {(r['timerange'] or ''):<26}  {r['created_at'] or ''}"
        )


# ── show ───────────────────────────────────────────────────────────────────────

def cmd_show(conn: sqlite3.Connection, run_id: str) -> None:
    run = conn.execute("SELECT * FROM experiment_runs WHERE run_id = ?", (run_id,)).fetchone()
    if not run:
        print(f"[ERROR] 未找到 run_id：{run_id}")
        return

    print(f"\n=== ExperimentRun: {run_id} ===")
    for key in run.keys():
        print(f"  {key}: {run[key]}")

    pv_rows = conn.execute(
        "SELECT * FROM parameter_versions WHERE run_id = ?", (run_id,)
    ).fetchall()
    if pv_rows:
        print(f"\n参数变更记录（{len(pv_rows)} 条）：")
        for pv in pv_rows:
            print(f"  [{pv['risk_level'] or 'N/A'}] {pv['parameter_name']}: {pv['old_value']} → {pv['new_value']}")
            if pv['proposal_id']:
                print(f"    ← proposal: {pv['proposal_id']}")
    else:
        print("\n参数变更记录：（无）")

    trade_row = conn.execute(
        """SELECT COUNT(*) AS n,
                  AVG(profit_pct) AS avg_pnl,
                  SUM(CASE WHEN profit_pct > 0 THEN 1.0 ELSE 0 END) / NULLIF(COUNT(*),0) AS win_rate
           FROM trades WHERE run_id = ? AND exit_time IS NOT NULL""",
        (run_id,),
    ).fetchone()
    if trade_row and trade_row["n"]:
        print(f"\n交易摘要：{trade_row['n']} 笔，胜率={trade_row['win_rate']:.2%}，avg_pnl={trade_row['avg_pnl']:.4f}")
    else:
        print("\n交易摘要：（无数据）")


# ── promote ────────────────────────────────────────────────────────────────────

def cmd_promote(conn: sqlite3.Connection, run_id: str, dry_run: bool) -> None:
    # 验证目标 run
    run = conn.execute(
        "SELECT run_id, run_type, status FROM experiment_runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if not run:
        print(f"[ERROR] 未找到 run_id：{run_id}")
        return
    if run["run_type"] != "candidate":
        print(f"[ERROR] 只能晋升 run_type='candidate' 的运行，当前为 '{run['run_type']}'")
        return

    # 找当前活跃 baseline
    current_baseline = conn.execute(
        "SELECT run_id FROM experiment_runs WHERE run_type = 'baseline' AND status != 'archived' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()

    if dry_run:
        print("[DRY-RUN] promote 操作预览：")
        if current_baseline:
            print(f"  1. 将 baseline {current_baseline['run_id']} 标记为 archived")
        print(f"  2. 将 candidate {run_id} 的 run_type 改为 baseline，status 改为 completed")
        return

    # 确认操作
    confirm = input(f"确认将 {run_id} 晋升为 baseline？(yes/no): ").strip().lower()
    if confirm != "yes":
        print("操作已取消")
        return

    with conn:
        if current_baseline:
            conn.execute(
                "UPDATE experiment_runs SET status = 'archived', ended_at = ? WHERE run_id = ?",
                (now_iso(), current_baseline["run_id"]),
            )
            print(f"[OK] 旧 baseline {current_baseline['run_id']} 已归档")

        conn.execute(
            "UPDATE experiment_runs SET run_type = 'baseline', status = 'completed', ended_at = ? WHERE run_id = ?",
            (now_iso(), run_id),
        )
        print(f"[OK] {run_id} 已晋升为 baseline")


# ── rollback ───────────────────────────────────────────────────────────────────

def cmd_rollback(conn: sqlite3.Connection, target_run_id: str, dry_run: bool) -> None:
    target = conn.execute(
        "SELECT run_id, run_type, status FROM experiment_runs WHERE run_id = ?", (target_run_id,)
    ).fetchone()
    if not target:
        print(f"[ERROR] 未找到目标 run_id：{target_run_id}")
        return
    if target["run_type"] not in ("baseline", "archived"):
        print(f"[ERROR] 回滚目标必须是 baseline 或 archived，当前为 '{target['run_type']}'")
        return

    current_baseline = conn.execute(
        "SELECT run_id FROM experiment_runs WHERE run_type = 'baseline' AND status != 'archived' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()

    if dry_run:
        print("[DRY-RUN] rollback 操作预览：")
        if current_baseline:
            print(f"  1. 将当前 baseline {current_baseline['run_id']} 标记为 archived（回滚标记）")
        print(f"  2. 将 {target_run_id} 重新设为 baseline，status = completed")
        return

    confirm = input(
        f"确认回滚至 {target_run_id}？当前 baseline{'（' + current_baseline['run_id'] + '）' if current_baseline else ''} 将被归档。(yes/no): "
    ).strip().lower()
    if confirm != "yes":
        print("操作已取消")
        return

    with conn:
        if current_baseline:
            conn.execute(
                "UPDATE experiment_runs SET status = 'archived', notes = COALESCE(notes, '') || '[rollback_archived]', ended_at = ? WHERE run_id = ?",
                (now_iso(), current_baseline["run_id"]),
            )
            print(f"[OK] 当前 baseline {current_baseline['run_id']} 已归档（回滚标记）")

        conn.execute(
            "UPDATE experiment_runs SET run_type = 'baseline', status = 'completed', ended_at = ? WHERE run_id = ?",
            (now_iso(), target_run_id),
        )
        print(f"[OK] 已回滚至 {target_run_id}")


# ── archive ────────────────────────────────────────────────────────────────────

def cmd_archive(conn: sqlite3.Connection, run_id: str, dry_run: bool) -> None:
    run = conn.execute(
        "SELECT run_id, run_type, status FROM experiment_runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if not run:
        print(f"[ERROR] 未找到 run_id：{run_id}")
        return

    if dry_run:
        print(f"[DRY-RUN] archive: 将 {run_id} (run_type={run['run_type']}) 标记为 archived")
        return

    with conn:
        conn.execute(
            "UPDATE experiment_runs SET status = 'archived', ended_at = ? WHERE run_id = ?",
            (now_iso(), run_id),
        )
    print(f"[OK] {run_id} 已标记为 archived")


# ── main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="P5 V2-4: baseline/candidate/rollback 状态管理")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--dry-run", action="store_true", help="预览操作，不实际修改数据库")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # list
    p_list = subparsers.add_parser("list", help="列出实验运行")
    p_list.add_argument("--run-type", choices=["baseline", "candidate", "dry_run", "live", "analysis"])
    p_list.add_argument("--status", help="按 status 筛选")

    # show
    p_show = subparsers.add_parser("show", help="查看指定 run_id 详情")
    p_show.add_argument("--run-id", required=True)

    # promote
    p_promote = subparsers.add_parser("promote", help="将 candidate 晋升为 baseline")
    p_promote.add_argument("--run-id", required=True, help="candidate run_id")

    # rollback
    p_rollback = subparsers.add_parser("rollback", help="回滚到指定历史 baseline")
    p_rollback.add_argument("--to", required=True, dest="target_run_id", help="目标 baseline run_id")

    # archive
    p_archive = subparsers.add_parser("archive", help="归档指定 run_id")
    p_archive.add_argument("--run-id", required=True)

    args = parser.parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"[ERROR] 数据库文件不存在：{db_path}")
        raise SystemExit(1)

    conn = connect_db(db_path)
    try:
        if args.command == "list":
            cmd_list(conn, getattr(args, "run_type", None), getattr(args, "status", None))
        elif args.command == "show":
            cmd_show(conn, args.run_id)
        elif args.command == "promote":
            cmd_promote(conn, args.run_id, args.dry_run)
        elif args.command == "rollback":
            cmd_rollback(conn, args.target_run_id, args.dry_run)
        elif args.command == "archive":
            cmd_archive(conn, args.run_id, args.dry_run)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
