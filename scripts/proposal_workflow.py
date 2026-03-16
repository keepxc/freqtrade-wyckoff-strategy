#!/usr/bin/env python3
"""
P5 V2-5: proposal → experiment → review 状态流转脚本

Proposal 状态机：
  pending → in_review → approved → in_experiment → validated
                      ↘ rejected                 ↘ rejected

支持操作：
  list              列出 proposals（可按状态筛选）
  show              查看指定 proposal 详情
  submit            从 JSON 文件提交新 proposal
  create            CLI 快速创建 proposal（基础字段）
  review            将 proposal 设为 in_review
  approve           批准 proposal（进入 approved，有质量门禁检查）
  reject            拒绝 proposal
  create-experiment 从 approved proposal 创建 experiment_run
  validate          将 in_experiment proposal 标记为 validated
  close-reject      将 in_experiment proposal 标记为 rejected（实验失败）

用法示例：
  python scripts/proposal_workflow.py list --status pending
  python scripts/proposal_workflow.py show --proposal-id prop_20260315_001
  python scripts/proposal_workflow.py submit --file proposal_draft.json
  python scripts/proposal_workflow.py review --proposal-id prop_20260315_001
  python scripts/proposal_workflow.py approve --proposal-id prop_20260315_001
  python scripts/proposal_workflow.py reject --proposal-id prop_20260315_001 --reason "样本不足"
  python scripts/proposal_workflow.py create-experiment --proposal-id prop_20260315_001 --timerange 20260101-20260315
  python scripts/proposal_workflow.py validate --proposal-id prop_20260315_001
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


DEFAULT_DB = "user_data/market.db"

VALID_TRANSITIONS = {
    "pending": ["in_review", "rejected"],
    "in_review": ["approved", "rejected"],
    "approved": ["in_experiment", "rejected"],
    "in_experiment": ["validated", "rejected"],
}

QUALITY_GATE_FIELDS = ["trade_count", "baseline_win_rate", "recent_win_rate"]
MIN_TRADE_COUNT = 30


def connect_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def short_id() -> str:
    dt = datetime.now(timezone.utc)
    suffix = str(uuid.uuid4())[:3].upper()
    return f"prop_{dt.strftime('%Y%m%d')}_{suffix}"


def _load_proposal(conn: sqlite3.Connection, proposal_id: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()


# ── list ───────────────────────────────────────────────────────────────────────

def cmd_list(conn: sqlite3.Connection, status: Optional[str]) -> None:
    sql = "SELECT proposal_id, proposal_type, risk_level, sample_sufficiency, status, created_at FROM proposals WHERE 1=1"
    params: list = []
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY created_at DESC"

    rows = conn.execute(sql, params).fetchall()
    if not rows:
        print("（无符合条件的 proposal 记录）")
        return

    print(f"{'proposal_id':<28}  {'type':<20}  {'risk':<5}  {'status':<16}  created_at")
    print("-" * 100)
    for r in rows:
        print(
            f"{r['proposal_id']:<28}  {(r['proposal_type'] or ''):<20}  "
            f"{(r['risk_level'] or ''):<5}  {(r['status'] or ''):<16}  {r['created_at'] or ''}"
        )


# ── show ───────────────────────────────────────────────────────────────────────

def cmd_show(conn: sqlite3.Connection, proposal_id: str) -> None:
    prop = _load_proposal(conn, proposal_id)
    if not prop:
        print(f"[ERROR] 未找到 proposal_id：{proposal_id}")
        return

    print(f"\n=== Proposal: {proposal_id} ===")
    for key in prop.keys():
        val = prop[key]
        if key in ("evidence_json", "experiment_matrix_json") and val:
            try:
                val = json.dumps(json.loads(val), ensure_ascii=False, indent=2)
            except (json.JSONDecodeError, TypeError):
                pass
        print(f"  {key}: {val}")

    # 关联的 experiment_runs
    exp_rows = conn.execute(
        "SELECT run_id, run_type, status, created_at FROM experiment_runs WHERE notes LIKE ?",
        (f"%{proposal_id}%",),
    ).fetchall()
    if exp_rows:
        print(f"\n关联实验（{len(exp_rows)} 条）：")
        for e in exp_rows:
            print(f"  [{e['run_type']}] {e['run_id']}  status={e['status']}  created={e['created_at']}")


# ── submit / create ────────────────────────────────────────────────────────────

def cmd_submit(conn: sqlite3.Connection, file_path: str) -> None:
    path = Path(file_path)
    if not path.exists():
        print(f"[ERROR] 文件不存在：{file_path}")
        return

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"[ERROR] JSON 解析失败：{e}")
        return

    proposal_id = data.get("proposal_id") or short_id()
    _insert_proposal(conn, proposal_id, data)
    print(f"[OK] Proposal 已提交：{proposal_id}")


def cmd_create(conn: sqlite3.Connection, proposal_type: str, problem: str, risk: str, action: str) -> None:
    proposal_id = short_id()
    data = {
        "proposal_type": proposal_type,
        "problem_statement": problem,
        "risk_level": risk,
        "recommended_action": action,
        "status": "pending",
    }
    _insert_proposal(conn, proposal_id, data)
    print(f"[OK] Proposal 已创建：{proposal_id}")


def _insert_proposal(conn: sqlite3.Connection, proposal_id: str, data: dict) -> None:
    # 检查是否已存在
    existing = conn.execute("SELECT proposal_id FROM proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()
    if existing:
        print(f"[ERROR] proposal_id 已存在：{proposal_id}")
        return

    evidence = data.get("evidence_json")
    experiment_matrix = data.get("experiment_matrix_json")

    with conn:
        conn.execute(
            """
            INSERT INTO proposals
              (proposal_id, created_at, proposal_type, problem_statement,
               evidence_json, risk_level, sample_sufficiency, recommended_action,
               experiment_matrix_json, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                proposal_id,
                now_iso(),
                data.get("proposal_type", "parameter_change"),
                data.get("problem_statement", ""),
                json.dumps(evidence, ensure_ascii=False) if isinstance(evidence, dict) else evidence,
                data.get("risk_level", "L3"),
                data.get("sample_sufficiency", ""),
                data.get("recommended_action", ""),
                json.dumps(experiment_matrix, ensure_ascii=False) if isinstance(experiment_matrix, dict) else experiment_matrix,
                data.get("status", "pending"),
            ),
        )


# ── state transitions ──────────────────────────────────────────────────────────

def _transition(conn: sqlite3.Connection, proposal_id: str, target_status: str, extra_check=None) -> bool:
    prop = _load_proposal(conn, proposal_id)
    if not prop:
        print(f"[ERROR] 未找到 proposal_id：{proposal_id}")
        return False

    current = prop["status"] or "pending"
    allowed = VALID_TRANSITIONS.get(current, [])
    if target_status not in allowed:
        print(f"[ERROR] 无效状态流转：{current} → {target_status}（允许：{allowed}）")
        return False

    if extra_check and not extra_check(prop):
        return False

    with conn:
        conn.execute("UPDATE proposals SET status = ? WHERE proposal_id = ?", (target_status, proposal_id))
    return True


def cmd_review(conn: sqlite3.Connection, proposal_id: str) -> None:
    if _transition(conn, proposal_id, "in_review"):
        print(f"[OK] {proposal_id}：pending → in_review")


def cmd_approve(conn: sqlite3.Connection, proposal_id: str) -> None:
    def quality_gate(prop) -> bool:
        # 质量门禁检查
        risk = prop["risk_level"]
        if risk in ("L1", "L2"):
            print(f"[GATE FAIL] 风险等级 {risk} 不允许进入 approved（必须 L3/L4）")
            return False

        evidence_raw = prop["evidence_json"]
        if not evidence_raw:
            print("[GATE FAIL] 缺少 evidence_json")
            return False

        try:
            evidence = json.loads(evidence_raw)
        except (json.JSONDecodeError, TypeError):
            print("[GATE FAIL] evidence_json 格式不合法")
            return False

        trade_count = evidence.get("trade_count", 0) or 0
        if trade_count < MIN_TRADE_COUNT:
            print(f"[GATE FAIL] 样本量不足：{trade_count} < {MIN_TRADE_COUNT}")
            return False

        for field in QUALITY_GATE_FIELDS:
            if field not in evidence:
                print(f"[GATE FAIL] evidence_json 缺少字段：{field}")
                return False

        exp_matrix = prop["experiment_matrix_json"]
        if not exp_matrix:
            print("[GATE FAIL] 缺少 experiment_matrix_json（至少需要 1 组候选参数）")
            return False

        try:
            matrix = json.loads(exp_matrix)
            if not matrix.get("candidates"):
                print("[GATE FAIL] experiment_matrix_json.candidates 为空")
                return False
        except (json.JSONDecodeError, TypeError):
            print("[GATE FAIL] experiment_matrix_json 格式不合法")
            return False

        sample_suf = prop["sample_sufficiency"] or ""
        if sample_suf.startswith("insufficient"):
            print(f"[GATE FAIL] sample_sufficiency={sample_suf}，不允许 approved")
            return False

        return True

    if _transition(conn, proposal_id, "approved", quality_gate):
        print(f"[OK] {proposal_id}：in_review → approved（质量门禁通过）")


def cmd_reject(conn: sqlite3.Connection, proposal_id: str, reason: str) -> None:
    prop = _load_proposal(conn, proposal_id)
    if not prop:
        print(f"[ERROR] 未找到 proposal_id：{proposal_id}")
        return

    current = prop["status"] or "pending"
    allowed = VALID_TRANSITIONS.get(current, [])
    if "rejected" not in allowed:
        print(f"[ERROR] 当前状态 {current} 不允许拒绝")
        return

    with conn:
        new_notes = json.dumps({"rejected_reason": reason, "rejected_at": now_iso()}, ensure_ascii=False)
        conn.execute(
            "UPDATE proposals SET status = 'rejected', recommended_action = COALESCE(recommended_action, '') || ' [REJECTED: ' || ? || ']' WHERE proposal_id = ?",
            (reason, proposal_id),
        )
    print(f"[OK] {proposal_id} 已拒绝。原因：{reason}")


# ── create-experiment ──────────────────────────────────────────────────────────

def cmd_create_experiment(conn: sqlite3.Connection, proposal_id: str, timerange: str, dry_run: bool) -> None:
    prop = _load_proposal(conn, proposal_id)
    if not prop:
        print(f"[ERROR] 未找到 proposal_id：{proposal_id}")
        return

    if prop["status"] != "approved":
        print(f"[ERROR] Proposal 状态必须为 approved，当前为 '{prop['status']}'")
        return

    # 获取当前 baseline run_id
    baseline = conn.execute(
        "SELECT run_id FROM experiment_runs WHERE run_type = 'baseline' AND status != 'archived' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    parent_baseline = baseline["run_id"] if baseline else None

    run_id = f"run_candidate_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    notes = f"created_from_proposal:{proposal_id}"

    if dry_run:
        print(f"[DRY-RUN] 将创建 experiment_run：")
        print(f"  run_id: {run_id}")
        print(f"  run_type: candidate")
        print(f"  timerange: {timerange}")
        print(f"  parent_baseline_run_id: {parent_baseline}")
        print(f"  notes: {notes}")
        return

    with conn:
        conn.execute(
            """
            INSERT INTO experiment_runs
              (run_id, run_type, mode, strategy_name, strategy_version, parameter_set_name,
               parent_baseline_run_id, timerange, status, notes, created_at)
            VALUES (?, 'candidate', 'backtest', NULL, NULL, NULL, ?, ?, 'running', ?, ?)
            """,
            (run_id, parent_baseline, timerange, notes, now_iso()),
        )
        conn.execute(
            "UPDATE proposals SET status = 'in_experiment' WHERE proposal_id = ?",
            (proposal_id,),
        )

    print(f"[OK] 已创建实验 run：{run_id}")
    print(f"[OK] Proposal {proposal_id}：approved → in_experiment")


# ── validate / close-reject ────────────────────────────────────────────────────

def cmd_validate(conn: sqlite3.Connection, proposal_id: str) -> None:
    if _transition(conn, proposal_id, "validated"):
        print(f"[OK] {proposal_id}：in_experiment → validated")
        print("提示：请使用 manage_experiment_status.py promote 晋升对应的 candidate run 为 baseline")


def cmd_close_reject(conn: sqlite3.Connection, proposal_id: str, reason: str) -> None:
    prop = _load_proposal(conn, proposal_id)
    if not prop or prop["status"] != "in_experiment":
        print(f"[ERROR] Proposal 不存在或状态不为 in_experiment")
        return

    with conn:
        conn.execute("UPDATE proposals SET status = 'rejected' WHERE proposal_id = ?", (proposal_id,))
    print(f"[OK] {proposal_id}：in_experiment → rejected（实验失败）。原因：{reason}")


# ── main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="P5 V2-5: proposal → experiment → review 状态流转")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--dry-run", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_list = subparsers.add_parser("list")
    p_list.add_argument("--status", help="按状态筛选")

    p_show = subparsers.add_parser("show")
    p_show.add_argument("--proposal-id", required=True)

    p_submit = subparsers.add_parser("submit")
    p_submit.add_argument("--file", required=True)

    p_create = subparsers.add_parser("create")
    p_create.add_argument("--type", default="parameter_change", dest="proposal_type")
    p_create.add_argument("--problem", required=True)
    p_create.add_argument("--risk", choices=["L1", "L2", "L3", "L4"], default="L3")
    p_create.add_argument("--action", default="")

    p_review = subparsers.add_parser("review")
    p_review.add_argument("--proposal-id", required=True)

    p_approve = subparsers.add_parser("approve")
    p_approve.add_argument("--proposal-id", required=True)

    p_reject = subparsers.add_parser("reject")
    p_reject.add_argument("--proposal-id", required=True)
    p_reject.add_argument("--reason", default="未提供原因")

    p_exp = subparsers.add_parser("create-experiment")
    p_exp.add_argument("--proposal-id", required=True)
    p_exp.add_argument("--timerange", required=True)

    p_validate = subparsers.add_parser("validate")
    p_validate.add_argument("--proposal-id", required=True)

    p_close = subparsers.add_parser("close-reject")
    p_close.add_argument("--proposal-id", required=True)
    p_close.add_argument("--reason", default="实验结果不达预期")

    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"[ERROR] 数据库文件不存在：{db_path}")
        raise SystemExit(1)

    conn = connect_db(db_path)
    try:
        if args.command == "list":
            cmd_list(conn, getattr(args, "status", None))
        elif args.command == "show":
            cmd_show(conn, args.proposal_id)
        elif args.command == "submit":
            cmd_submit(conn, args.file)
        elif args.command == "create":
            cmd_create(conn, args.proposal_type, args.problem, args.risk, args.action)
        elif args.command == "review":
            cmd_review(conn, args.proposal_id)
        elif args.command == "approve":
            cmd_approve(conn, args.proposal_id)
        elif args.command == "reject":
            cmd_reject(conn, args.proposal_id, args.reason)
        elif args.command == "create-experiment":
            cmd_create_experiment(conn, args.proposal_id, args.timerange, args.dry_run)
        elif args.command == "validate":
            cmd_validate(conn, args.proposal_id)
        elif args.command == "close-reject":
            cmd_close_reject(conn, args.proposal_id, args.reason)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
