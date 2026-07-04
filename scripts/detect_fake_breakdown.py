#!/usr/bin/env python3
"""P5 假跌破（fake breakdown）检测器 — wave_breakdown 路径异常簇软告警。

设计定稿：TASK_NEW/wyckoff_v8_redesign.md「P5 假跌破检测器设计定稿（2026-06-08）」
三条件 AND（12h 滑窗）：
  1. wave_breakdown（enter_tag=v8_short_wsdown）触发数 >= 3
  2. 止损类退出占比（stop_loss + v8_short_structure_fail）> 40%
  3. 持仓时长中位数 < 8h
命中 → 写 proposals 表（诊断类软建议，不改策略代码）+ Telegram 通知。

数据源（--source）：
  backtest  — 读取 freqtrade 原生回测结果 zip（当前唯一已验证可用路径，
              因 dry run 不在本机、且 market.db 摄入管线对 backtest run_id 有
              意愿性排除，对 dry_run/live run_id 未验证，见脚本内 NOTE）
  market_db — 生产路径：查询 market.db 的 trades 表（依赖 ingest 管线把
              dry_run/live 事件摄入 market.db；本机无法验证）

Bug 记录（本次开发中发现，未修复，超出本 TASK 范围）：
  scripts/proposal_workflow.py 的 cmd_create 与 proposals 表 CHECK 约束不一致
  （status='pending' 不在 draft/reviewed/accepted/rejected/executed 内；
   risk_level 默认 'L3' 不在 low/medium/high 内）——该工具从未成功写入过
  一行数据（market.db.proposals 表 0 行即为证据）。本脚本绕开该工具，直接
  按表的真实 CHECK 约束写入。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

WAVE_BREAKDOWN_ENTER_TAG = "v8_short_wsdown"
STRUCTURE_FAIL_EXIT_REASONS = {"stop_loss", "v8_short_structure_fail"}

WINDOW_HOURS = 12
STEP_HOURS = 6
MIN_TRIGGER_COUNT = 3
MIN_STOP_LOSS_RATIO = 0.4
MAX_MEDIAN_HOLD_HOURS = 8.0

PATH_NAME = "wave_breakdown"


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ──────────────────────────────────────────────
# 数据加载
# ──────────────────────────────────────────────
def load_trades_from_backtest_zip(zip_path: Path) -> list[dict[str, Any]]:
    """读取 freqtrade 原生回测结果 zip，过滤 wave_breakdown 路径交易。"""
    with zipfile.ZipFile(zip_path) as z:
        # 主结果文件命名固定为 <stem>.json
        target = f"{zip_path.stem}.json"
        if target not in z.namelist():
            raise FileNotFoundError(f"{target} 不在 {zip_path} 内，实际内容：{z.namelist()}")
        data = json.loads(z.read(target))
    strategy_name = next(iter(data["strategy"].keys()))
    raw_trades = data["strategy"][strategy_name]["trades"]

    out = []
    for t in raw_trades:
        if t.get("enter_tag") != WAVE_BREAKDOWN_ENTER_TAG:
            continue
        out.append({
            "pair": t["pair"],
            "open_date": datetime.fromisoformat(t["open_date"]),
            "close_date": datetime.fromisoformat(t["close_date"]) if t.get("close_date") else None,
            "exit_reason": t.get("exit_reason"),
            "duration_hours": (t["trade_duration"] / 60.0) if t.get("trade_duration") is not None else None,
        })
    return out


def load_trades_from_market_db(db_path: Path, since: datetime | None) -> list[dict[str, Any]]:
    """生产路径：查询 market.db trades 表（未在本机验证，依赖 ingest 管线）。

    since=None 时不做时间过滤（scan-history 模式，取全部历史）。
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    if since is not None:
        rows = conn.execute(
            """
            SELECT pair, enter_time, exit_time, exit_reason, duration_minutes
            FROM trades
            WHERE enter_tag = ? AND enter_time >= ?
            ORDER BY enter_time
            """,
            (WAVE_BREAKDOWN_ENTER_TAG, since.isoformat()),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT pair, enter_time, exit_time, exit_reason, duration_minutes
            FROM trades
            WHERE enter_tag = ?
            ORDER BY enter_time
            """,
            (WAVE_BREAKDOWN_ENTER_TAG,),
        ).fetchall()
    conn.close()

    out = []
    for r in rows:
        out.append({
            "pair": r["pair"],
            "open_date": datetime.fromisoformat(r["enter_time"]),
            "close_date": datetime.fromisoformat(r["exit_time"]) if r["exit_time"] else None,
            "exit_reason": r["exit_reason"],
            "duration_hours": (r["duration_minutes"] / 60.0) if r["duration_minutes"] is not None else None,
        })
    return out


# ──────────────────────────────────────────────
# 窗口评估
# ──────────────────────────────────────────────
def evaluate_window(trades: list[dict[str, Any]], window_start: datetime, window_end: datetime) -> dict[str, Any]:
    entries_in_window = [t for t in trades if window_start <= t["open_date"] < window_end]
    closed_in_window = [t for t in entries_in_window if t["close_date"] is not None]

    trigger_count = len(entries_in_window)
    n_closed = len(closed_in_window)
    n_stop_loss = sum(1 for t in closed_in_window if t["exit_reason"] in STRUCTURE_FAIL_EXIT_REASONS)
    stop_loss_ratio = (n_stop_loss / n_closed) if n_closed else 0.0
    durations = [t["duration_hours"] for t in closed_in_window if t["duration_hours"] is not None]
    median_hold = statistics.median(durations) if durations else None

    fired = (
        trigger_count >= MIN_TRIGGER_COUNT
        and stop_loss_ratio > MIN_STOP_LOSS_RATIO
        and median_hold is not None
        and median_hold < MAX_MEDIAN_HOLD_HOURS
    )

    return {
        "window_start": window_start,
        "window_end": window_end,
        "trigger_count": trigger_count,
        "n_closed": n_closed,
        "n_stop_loss": n_stop_loss,
        "stop_loss_ratio": round(stop_loss_ratio, 4),
        "median_hold_hours": round(median_hold, 2) if median_hold is not None else None,
        "fired": fired,
    }


def scan_history(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 6h 步长扫描整段历史（回测验证用），返回所有命中窗口。"""
    if not trades:
        return []
    t_min = min(t["open_date"] for t in trades)
    t_max = max((t["close_date"] or t["open_date"]) for t in trades)

    fired_windows = []
    cursor = t_min
    while cursor <= t_max:
        window_end = cursor + timedelta(hours=WINDOW_HOURS)
        result = evaluate_window(trades, cursor, window_end)
        if result["fired"]:
            fired_windows.append(result)
        cursor += timedelta(hours=STEP_HOURS)
    return fired_windows


# ──────────────────────────────────────────────
# proposals 表写入（绕开 proposal_workflow.py 的 broken cmd_create，
# 直接按表的真实 CHECK 约束构造）
# ──────────────────────────────────────────────
def write_proposal(db_path: Path, window_result: dict[str, Any]) -> str:
    proposal_id = f"fake_breakdown_{PATH_NAME}_{window_result['window_start'].strftime('%Y%m%dT%H%M%S')}"
    risk_level = "high" if window_result["stop_loss_ratio"] > 0.6 else "medium"
    problem_statement = (
        f"{PATH_NAME} 路径 12h 滑窗内检测到疑似假跌破簇："
        f"触发 {window_result['trigger_count']} 笔，"
        f"止损类退出占比 {window_result['stop_loss_ratio']:.0%}，"
        f"持仓中位数 {window_result['median_hold_hours']}h "
        f"（窗口 {window_result['window_start'].isoformat()} ~ {window_result['window_end'].isoformat()}）"
    )
    evidence = {
        "path_name": PATH_NAME,
        "detection_type": "fake_breakdown",
        "window_start": window_result["window_start"].isoformat(),
        "window_end": window_result["window_end"].isoformat(),
        "trigger_count": window_result["trigger_count"],
        "n_closed": window_result["n_closed"],
        "n_stop_loss": window_result["n_stop_loss"],
        "stop_loss_ratio": window_result["stop_loss_ratio"],
        "median_hold_hours": window_result["median_hold_hours"],
        "thresholds": {
            "min_trigger_count": MIN_TRIGGER_COUNT,
            "min_stop_loss_ratio": MIN_STOP_LOSS_RATIO,
            "max_median_hold_hours": MAX_MEDIAN_HOLD_HOURS,
        },
    }
    recommended_action = (
        f"suspend_path {PATH_NAME} 24h" if risk_level == "high" else f"reduce_size {PATH_NAME} 50%"
    )

    conn = sqlite3.connect(str(db_path))
    with conn:
        existing = conn.execute(
            "SELECT proposal_id FROM proposals WHERE proposal_id = ?", (proposal_id,)
        ).fetchone()
        if existing:
            log(f"  跳过（proposal 已存在）：{proposal_id}")
            return proposal_id
        conn.execute(
            """
            INSERT INTO proposals
              (proposal_id, created_at, proposal_type, problem_statement,
               evidence_json, risk_level, sample_sufficiency, recommended_action,
               experiment_matrix_json, status)
            VALUES (?, ?, 'diagnostic', ?, ?, ?, 'minimum', ?, NULL, 'draft')
            """,
            (
                proposal_id,
                datetime.now(timezone.utc).isoformat(),
                problem_statement,
                json.dumps(evidence, ensure_ascii=False),
                risk_level,
                recommended_action,
            ),
        )
    conn.close()
    log(f"  ✓ 写入 proposal：{proposal_id}（risk={risk_level}）")
    return proposal_id


# ──────────────────────────────────────────────
# Telegram 软建议通知
# ──────────────────────────────────────────────
def send_telegram(config_path: Path, window_result: dict[str, Any]) -> None:
    import requests

    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    tg = cfg.get("telegram", {})
    if not tg.get("enabled") or not tg.get("token") or not tg.get("chat_id"):
        log("  ⚠ Telegram 未配置，跳过通知")
        return

    text = (
        "🟡 V形假跌破模式检测\n"
        f"路径：{PATH_NAME}\n"
        f"窗口：{window_result['window_start'].strftime('%m-%d %H:%M')} ~ "
        f"{window_result['window_end'].strftime('%m-%d %H:%M')}\n"
        f"触发数：{window_result['trigger_count']}｜"
        f"止损占比：{window_result['stop_loss_ratio']:.0%}｜"
        f"持仓中位数：{window_result['median_hold_hours']}h\n"
        "建议：人工复核，暂不自动执行（软建议）"
    )
    url = f"https://api.telegram.org/bot{tg['token']}/sendMessage"
    resp = requests.post(url, json={"chat_id": tg["chat_id"], "text": text}, timeout=10)
    if resp.status_code == 200:
        log("  ✓ Telegram 通知已发送")
    else:
        log(f"  ✗ Telegram 发送失败：{resp.status_code} {resp.text[:200]}")


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────
def main() -> None:
    p = argparse.ArgumentParser(description="P5 假跌破检测器")
    p.add_argument("--source", choices=["backtest", "market_db"], required=True)
    p.add_argument("--zip", type=Path, help="backtest 模式：freqtrade 回测结果 zip 路径")
    p.add_argument("--db", type=Path, default=ROOT / "user_data" / "market.db", help="market_db 模式：SQLite 路径")
    p.add_argument("--mode", choices=["scan-history", "live"], default="scan-history",
                   help="scan-history: 扫描整段历史找命中窗口（验证用）；live: 只看最近 12h（生产用）")
    p.add_argument("--notify", action="store_true", help="命中时发送 Telegram（默认只落库不通知，避免历史回放误发）")
    p.add_argument("--telegram-config", type=Path, default=ROOT / "user_data" / "config_dryrun.json")
    p.add_argument("--dry-run-write", action="store_true", help="只打印命中窗口，不写 proposals 表")
    args = p.parse_args()

    if args.source == "backtest":
        if not args.zip:
            print("错误：--source backtest 必须提供 --zip", file=sys.stderr)
            sys.exit(1)
        trades = load_trades_from_backtest_zip(args.zip)
        log(f"从 {args.zip.name} 加载 {PATH_NAME} 路径交易 {len(trades)} 笔")
    else:
        since = (datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)) if args.mode == "live" else None
        trades = load_trades_from_market_db(args.db, since)
        log(f"从 market.db 加载 {PATH_NAME} 路径交易 {len(trades)} 笔（NOTE：生产 ingest 路径本机未验证）")

    if args.mode == "scan-history":
        fired = scan_history(trades)
        log(f"扫描完成：共 {len(fired)} 个窗口命中三条件 AND")
        for w in fired:
            log(f"  命中窗口 {w['window_start']} ~ {w['window_end']}："
                f"触发{w['trigger_count']} / 止损占比{w['stop_loss_ratio']:.0%} / "
                f"中位持仓{w['median_hold_hours']}h")
            if not args.dry_run_write:
                write_proposal(args.db, w)
            if args.notify:
                send_telegram(args.telegram_config, w)
    else:
        now = datetime.now(timezone.utc)
        w = evaluate_window(trades, now - timedelta(hours=WINDOW_HOURS), now)
        log(f"最近 12h 窗口：触发{w['trigger_count']} / 止损占比{w['stop_loss_ratio']:.0%} / "
            f"中位持仓{w['median_hold_hours']}h / fired={w['fired']}")
        if w["fired"]:
            if not args.dry_run_write:
                write_proposal(args.db, w)
            if args.notify:
                send_telegram(args.telegram_config, w)


if __name__ == "__main__":
    main()
