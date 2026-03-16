#!/usr/bin/env python3
"""
P5 V2-3: experiment_runs 与 parameter_versions 联动报告

展示每个实验运行关联的参数变更历史、与 proposal 的关联关系，
以及 baseline/candidate 状态对比。

用法：
  python user_data/reports/experiment_status_report.py --db user_data/market.db
  python user_data/reports/experiment_status_report.py --db user_data/market.db --run-type candidate
  python user_data/reports/experiment_status_report.py --db user_data/market.db --run-id <run_id>
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


def connect_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def fetch_experiment_runs(conn: sqlite3.Connection, run_type: Optional[str], run_id: Optional[str]) -> List[sqlite3.Row]:
    sql = """
        SELECT
            run_id,
            run_type,
            mode,
            strategy_name,
            strategy_version,
            parameter_set_name,
            parent_baseline_run_id,
            timerange,
            status,
            created_at,
            ended_at,
            notes
        FROM experiment_runs
        WHERE 1=1
    """
    params: list = []
    if run_type:
        sql += " AND run_type = ?"
        params.append(run_type)
    if run_id:
        sql += " AND run_id = ?"
        params.append(run_id)
    sql += " ORDER BY created_at DESC"
    return conn.execute(sql, params).fetchall()


def fetch_parameter_versions(conn: sqlite3.Connection, run_id: str) -> List[sqlite3.Row]:
    return conn.execute(
        """
        SELECT param_version_id, parameter_name, old_value, new_value,
               parameter_class, reason, risk_level, proposal_id
        FROM parameter_versions
        WHERE run_id = ?
        ORDER BY param_version_id
        """,
        (run_id,),
    ).fetchall()


def fetch_proposal(conn: sqlite3.Connection, proposal_id: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT proposal_id, proposal_type, problem_statement, risk_level, status FROM proposals WHERE proposal_id = ?",
        (proposal_id,),
    ).fetchone()


def fetch_run_trades_summary(conn: sqlite3.Connection, run_id: str):
    return conn.execute(
        """
        SELECT
            COUNT(*) AS n,
            AVG(profit_pct) AS avg_pnl,
            SUM(CASE WHEN profit_pct > 0 THEN 1.0 ELSE 0 END) / NULLIF(COUNT(*), 0) AS win_rate
        FROM trades
        WHERE run_id = ? AND exit_time IS NOT NULL
        """,
        (run_id,),
    ).fetchone()


def fetch_baseline_run(conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    return conn.execute(
        """
        SELECT run_id, strategy_version, parameter_set_name, timerange, status, created_at
        FROM experiment_runs
        WHERE run_type = 'baseline'
        ORDER BY created_at DESC
        LIMIT 1
        """,
    ).fetchone()


def format_run_block(conn: sqlite3.Connection, run: sqlite3.Row, verbose: bool = False) -> str:
    lines = []
    lines.append(f"### [{run['run_type'].upper()}] {run['run_id']}")
    lines.append(f"- 状态：{run['status'] or 'N/A'}")
    lines.append(f"- 模式：{run['mode'] or 'N/A'}")
    lines.append(f"- 策略版本：{run['strategy_version'] or 'N/A'}")
    lines.append(f"- 参数集：{run['parameter_set_name'] or 'N/A'}")
    lines.append(f"- 时间范围：{run['timerange'] or 'N/A'}")
    lines.append(f"- 创建时间：{run['created_at'] or 'N/A'}")
    if run['parent_baseline_run_id']:
        lines.append(f"- 父基线 run_id：{run['parent_baseline_run_id']}")
    if run['notes']:
        lines.append(f"- 备注：{run['notes']}")

    # 交易摘要
    trade_summary = fetch_run_trades_summary(conn, run['run_id'])
    if trade_summary and trade_summary['n']:
        n = trade_summary['n']
        win_rate = trade_summary['win_rate'] or 0.0
        avg_pnl = trade_summary['avg_pnl'] or 0.0
        lines.append(f"- 已平仓交易：{n} 笔，胜率={win_rate:.2%}，avg_pnl={avg_pnl:.4f}")
    else:
        lines.append("- 已平仓交易：（无数据）")

    # 参数版本
    pv_rows = fetch_parameter_versions(conn, run['run_id'])
    if pv_rows:
        lines.append(f"- 参数变更记录（{len(pv_rows)} 条）：")
        for pv in pv_rows:
            proposal_ref = f" ← proposal:{pv['proposal_id']}" if pv['proposal_id'] else ""
            lines.append(
                f"  - {pv['parameter_name']}: {pv['old_value']} → {pv['new_value']}"
                f"  [{pv['risk_level'] or 'N/A'}]{proposal_ref}"
            )
            if verbose and pv['reason']:
                lines.append(f"    理由：{pv['reason']}")
            # 关联 proposal 详情
            if verbose and pv['proposal_id']:
                prop = fetch_proposal(conn, pv['proposal_id'])
                if prop:
                    lines.append(
                        f"    提案 [{prop['status']}] ({prop['proposal_type']}): {prop['problem_statement'][:80]}..."
                    )
    else:
        lines.append("- 参数变更记录：（无）")

    return "\n".join(lines)


def build_report(conn: sqlite3.Connection, run_type: Optional[str], run_id: Optional[str]) -> str:
    lines = []
    lines.append("# P5 实验运行状态报告")
    lines.append(f"- 生成时间：{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")
    lines.append("")

    # 当前 baseline
    baseline = fetch_baseline_run(conn)
    if baseline:
        lines.append("## 当前活跃 Baseline")
        lines.append(f"- run_id：{baseline['run_id']}")
        lines.append(f"- 策略版本：{baseline['strategy_version'] or 'N/A'}")
        lines.append(f"- 参数集：{baseline['parameter_set_name'] or 'N/A'}")
        lines.append(f"- 时间范围：{baseline['timerange'] or 'N/A'}")
        lines.append(f"- 创建时间：{baseline['created_at'] or 'N/A'}")
    else:
        lines.append("## 当前活跃 Baseline")
        lines.append("- （未找到 baseline 记录）")
    lines.append("")

    runs = fetch_experiment_runs(conn, run_type, run_id)
    if not runs:
        lines.append("## 实验列表")
        lines.append("- 无符合条件的实验记录")
        return "\n".join(lines)

    # 按 run_type 分组
    groups: dict[str, list] = {}
    for run in runs:
        rt = run['run_type'] or 'unknown'
        groups.setdefault(rt, []).append(run)

    run_type_order = ['baseline', 'candidate', 'dry_run', 'live', 'analysis', 'unknown']
    for rt in run_type_order:
        if rt not in groups:
            continue
        lines.append(f"## {rt.upper()} 实验（{len(groups[rt])} 条）")
        for run in groups[rt]:
            lines.append("")
            lines.append(format_run_block(conn, run, verbose=(run_id is not None)))
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="P5 实验运行状态报告（V2-3）")
    parser.add_argument("--db", default="user_data/market.db", help="SQLite 数据库路径")
    parser.add_argument("--run-type", choices=["baseline", "candidate", "dry_run", "live", "analysis"],
                        help="筛选指定 run_type")
    parser.add_argument("--run-id", help="查看指定 run_id 的详情")
    parser.add_argument("--out", help="输出 Markdown 文件路径（不指定则打印到 stdout）")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"[ERROR] 数据库文件不存在：{db_path}")
        raise SystemExit(1)

    conn = connect_db(db_path)
    try:
        report = build_report(conn, args.run_type, args.run_id)
    finally:
        conn.close()

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report, encoding="utf-8")
        print(f"[OK] 报告已写入：{out_path}")
    else:
        print(report)


if __name__ == "__main__":
    main()
