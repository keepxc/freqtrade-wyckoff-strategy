#!/usr/bin/env python3
"""V1B-5: Detector Activation Report — detector 激活率 / 冲突率 / fallback 率统计。

用法：
  python detector_activation_report.py --db p5.db \\
      --window 7   # 最近 7 天
      [--baseline-start 2023-01-01 --baseline-end 2024-01-01]
      [--output report.md]
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def connect_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _now_bounds(window_days: int) -> Tuple[str, str]:
    now = datetime.now(timezone.utc)
    end = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=window_days)
    return start.isoformat(), end.isoformat()


def fetch_detector_stats(
    conn: sqlite3.Connection,
    start: str,
    end: str,
) -> List[dict]:
    """按 event_name + event_family 统计激活次数。"""
    rows = conn.execute(
        """
        SELECT
            event_family,
            event_name,
            COUNT(*) AS activation_count,
            COUNT(DISTINCT pair) AS pair_count,
            MIN(ts) AS first_ts,
            MAX(ts) AS last_ts
        FROM detector_events
        WHERE ts >= ? AND ts < ?
        GROUP BY event_family, event_name
        ORDER BY activation_count DESC
        """,
        (start, end),
    ).fetchall()
    return [dict(r) for r in rows]


def fetch_total_bars(conn: sqlite3.Connection, start: str, end: str) -> int:
    """用 regime_snapshots 数量近似总 bar 数，用作激活率分母。"""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM regime_snapshots WHERE ts >= ? AND ts < ?",
        (start, end),
    ).fetchone()
    return row["n"] if row else 0


def fetch_conflict_count(conn: sqlite3.Connection, start: str, end: str) -> int:
    """信号中同时有 p3_swing_up_er_weak 且 p3_swing_down_er_exhaustion 的 bar（粗略冲突代理）。"""
    row = conn.execute(
        """
        SELECT COUNT(*) AS n FROM signals
        WHERE ts >= ? AND ts < ?
          AND p3_swing_up_er_weak = 1
          AND p3_swing_down_er_exhaustion = 1
        """,
        (start, end),
    ).fetchone()
    return row["n"] if row else 0


def fetch_fallback_count(conn: sqlite3.Connection, start: str, end: str) -> int:
    """regime_blocked 信号数（作为 fallback 代理指标）。"""
    row = conn.execute(
        """
        SELECT COUNT(*) AS n FROM signals
        WHERE ts >= ? AND ts < ?
          AND regime_blocked = 1
        """,
        (start, end),
    ).fetchone()
    return row["n"] if row else 0


def build_report(
    recent_stats: List[dict],
    recent_range: Tuple[str, str],
    total_bars: int,
    conflict_count: int,
    fallback_count: int,
    baseline_stats: Optional[List[dict]] = None,
    baseline_range: Optional[Tuple[str, str]] = None,
) -> str:
    lines: List[str] = []
    lines.append("# P5 Detector Activation Report\n")
    lines.append(f"- 统计窗口：{recent_range[0]} ~ {recent_range[1]}")
    if baseline_range:
        lines.append(f"- 基线窗口：{baseline_range[0]} ~ {baseline_range[1]}")
    lines.append(f"- 总 regime 快照数（proxy for bars）：{total_bars}")
    lines.append("")

    # 激活率主表
    lines.append("## Detector 激活明细")
    lines.append("| event_family | event_name | 激活次数 | 激活率(per bar) | 覆盖交易对数 |")
    lines.append("|--------------|------------|----------|----------------|-------------|")

    baseline_map: Dict[str, int] = {}
    if baseline_stats:
        for row in baseline_stats:
            baseline_map[f"{row['event_family']}.{row['event_name']}"] = row["activation_count"]

    for row in recent_stats:
        key = f"{row['event_family']}.{row['event_name']}"
        rate = row["activation_count"] / total_bars if total_bars > 0 else 0.0
        baseline_n = baseline_map.get(key)
        baseline_note = f" (基线: {baseline_n})" if baseline_n is not None else ""
        lines.append(
            f"| {row['event_family']} | {row['event_name']} | "
            f"{row['activation_count']}{baseline_note} | {rate:.4f} | {row['pair_count']} |"
        )

    lines.append("")
    lines.append("## 冲突率 & Fallback 率")
    total_signals_row = None
    _ts_row = None
    try:
        _ts_row = None
    except Exception:
        pass

    lines.append(f"- 信号冲突数（up_er_weak ∩ down_er_exhaustion）：{conflict_count}")
    lines.append(f"- Regime blocked 信号数（fallback 代理）：{fallback_count}")
    lines.append("")

    if not recent_stats:
        lines.append("> [WARN] 本窗口内无 detector 事件记录，请检查事件流是否正常写入。")

    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Detector Activation Report")
    p.add_argument("--db", required=True, help="P5 SQLite 数据库路径")
    p.add_argument("--window", type=int, default=7, help="最近窗口天数（默认 7）")
    p.add_argument("--baseline-start", help="基线窗口开始 (YYYY-MM-DD)")
    p.add_argument("--baseline-end", help="基线窗口结束 (YYYY-MM-DD)")
    p.add_argument("--output", help="输出 markdown 文件路径（不指定则打印到 stdout）")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    db = Path(args.db)
    conn = connect_db(db)
    try:
        r_start, r_end = _now_bounds(args.window)

        recent_stats = fetch_detector_stats(conn, r_start, r_end)
        total_bars = fetch_total_bars(conn, r_start, r_end)
        conflict_count = fetch_conflict_count(conn, r_start, r_end)
        fallback_count = fetch_fallback_count(conn, r_start, r_end)

        baseline_stats = None
        baseline_range = None
        if args.baseline_start and args.baseline_end:
            b_start = datetime.fromisoformat(args.baseline_start).replace(tzinfo=timezone.utc).isoformat()
            b_end = datetime.fromisoformat(args.baseline_end).replace(tzinfo=timezone.utc).isoformat()
            baseline_stats = fetch_detector_stats(conn, b_start, b_end)
            baseline_range = (b_start, b_end)

        report = build_report(
            recent_stats, (r_start, r_end),
            total_bars, conflict_count, fallback_count,
            baseline_stats, baseline_range,
        )

        if args.output:
            Path(args.output).write_text(report, encoding="utf-8")
            print(f"[OK] wrote detector activation report to {args.output}")
        else:
            print(report)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
