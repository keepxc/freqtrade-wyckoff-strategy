#!/usr/bin/env python3
"""V1B-6: Regime Distribution Report — 统计 market_env / htf_market_env / macro_phase / bear_market_mode 分布变化。

用法：
  python regime_distribution_report.py --db p5.db \\
      --window 30 \\
      [--baseline-start 2023-01-01 --baseline-end 2024-01-01] \\
      [--output report.md]
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REGIME_FIELDS = ["market_env", "htf_market_env", "macro_phase", "bear_market_mode"]


def connect_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _now_bounds(window_days: int) -> Tuple[str, str]:
    now = datetime.now(timezone.utc)
    end = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=window_days)
    return start.isoformat(), end.isoformat()


def fetch_field_distribution(
    conn: sqlite3.Connection,
    field: str,
    start: str,
    end: str,
) -> Dict[str, int]:
    """统计某个字段的值分布（值 -> 行数）。"""
    rows = conn.execute(
        f"""
        SELECT COALESCE(CAST({field} AS TEXT), 'null') AS val, COUNT(*) AS n
        FROM regime_snapshots
        WHERE ts >= ? AND ts < ?
        GROUP BY COALESCE(CAST({field} AS TEXT), 'null')
        ORDER BY n DESC
        """,
        (start, end),
    ).fetchall()
    return {row["val"]: row["n"] for row in rows}


def _fmt_dist(dist: Dict[str, int], total: int) -> str:
    if not dist:
        return "（无数据）"
    parts = []
    for val, n in sorted(dist.items(), key=lambda x: -x[1]):
        pct = n / total if total > 0 else 0.0
        parts.append(f"{val}: {n} ({pct:.1%})")
    return " | ".join(parts)


def build_report(
    recent_dists: Dict[str, Dict[str, int]],
    recent_range: Tuple[str, str],
    baseline_dists: Optional[Dict[str, Dict[str, int]]] = None,
    baseline_range: Optional[Tuple[str, str]] = None,
) -> str:
    lines: List[str] = []
    lines.append("# P5 Regime Distribution Report\n")
    lines.append(f"- 统计窗口：{recent_range[0]} ~ {recent_range[1]}")
    if baseline_range:
        lines.append(f"- 基线窗口：{baseline_range[0]} ~ {baseline_range[1]}")
    lines.append("")

    for field in REGIME_FIELDS:
        r_dist = recent_dists.get(field, {})
        r_total = sum(r_dist.values())

        lines.append(f"## {field}")
        lines.append(f"- 近期总 bars：{r_total}")
        if r_dist:
            lines.append("| 值 | 近期 bars | 近期占比 | 基线占比 |")
            lines.append("|---|---------|---------|---------|")
            b_dist = (baseline_dists or {}).get(field, {})
            b_total = sum(b_dist.values())
            for val, n in sorted(r_dist.items(), key=lambda x: -x[1]):
                r_pct = n / r_total if r_total > 0 else 0.0
                b_n = b_dist.get(val, 0)
                b_pct = b_n / b_total if b_total > 0 else 0.0
                delta_str = f"{r_pct - b_pct:+.1%}" if b_total > 0 else "N/A"
                lines.append(f"| {val} | {n} | {r_pct:.1%} | {b_pct:.1%} ({delta_str}) |")

            # 检测基线中有但近期消失的值
            if b_dist:
                for val in b_dist:
                    if val not in r_dist:
                        b_n = b_dist[val]
                        b_pct = b_n / b_total if b_total > 0 else 0.0
                        lines.append(f"| {val} | 0 | 0.0% | {b_pct:.1%} (**消失**) |")
        else:
            lines.append("- 本窗口无数据")
        lines.append("")

    # P2 mismatch 预警：near-term bear_market_mode 占比偏高
    r_bear = recent_dists.get("bear_market_mode", {})
    r_bear_total = sum(r_bear.values())
    r_bear_on = r_bear.get("1", r_bear.get("True", 0))
    if r_bear_total > 0 and r_bear_on / r_bear_total > 0.5:
        lines.append(
            f"> ⚠️ **P2 Regime Mismatch 预警**：近期 bear_market_mode=1 占比 "
            f"{r_bear_on / r_bear_total:.1%}，超过 50%，建议检查 P2 触发条件。"
        )
        lines.append("")

    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Regime Distribution Report")
    p.add_argument("--db", required=True, help="P5 SQLite 数据库路径")
    p.add_argument("--window", type=int, default=30, help="最近窗口天数（默认 30）")
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

        recent_dists = {f: fetch_field_distribution(conn, f, r_start, r_end) for f in REGIME_FIELDS}

        baseline_dists = None
        baseline_range = None
        if args.baseline_start and args.baseline_end:
            b_start = datetime.fromisoformat(args.baseline_start).replace(tzinfo=timezone.utc).isoformat()
            b_end = datetime.fromisoformat(args.baseline_end).replace(tzinfo=timezone.utc).isoformat()
            baseline_dists = {f: fetch_field_distribution(conn, f, b_start, b_end) for f in REGIME_FIELDS}
            baseline_range = (b_start, b_end)

        report = build_report(recent_dists, (r_start, r_end), baseline_dists, baseline_range)

        if args.output:
            Path(args.output).write_text(report, encoding="utf-8")
            print(f"[OK] wrote regime distribution report to {args.output}")
        else:
            print(report)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
