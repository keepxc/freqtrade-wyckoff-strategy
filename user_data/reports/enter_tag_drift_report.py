#!/usr/bin/env python3
"""V1B-4: Enter Tag Drift Report — 比较最近窗口与基线窗口的 enter_tag 统计漂移。

用法：
  python enter_tag_drift_report.py --db p5.db \\
      --baseline-start 2023-01-01 --baseline-end 2024-01-01 \\
      --recent-start 2025-01-01 --recent-end 2026-01-01 \\
      [--output report.md]
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# 优先覆盖的 enter_tag 列表
PRIORITY_TAGS = [
    "wyckoff_axis_support",
    "wyckoff_spring",
    "wyckoff_sow",
    "wyckoff_divergence",
]


def connect_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def fetch_tag_stats(
    conn: sqlite3.Connection,
    start: str,
    end: str,
) -> Dict[str, dict]:
    """按 enter_tag 汇总已平仓交易的统计指标。"""
    rows = conn.execute(
        """
        SELECT
            enter_tag,
            COUNT(*) AS n,
            AVG(profit_pct) AS avg_pnl,
            SUM(CASE WHEN profit_pct > 0 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS win_rate,
            SUM(CASE WHEN profit_pct > 0 THEN profit_pct ELSE 0 END) /
                NULLIF(SUM(CASE WHEN profit_pct <= 0 THEN ABS(profit_pct) ELSE 0 END), 0) AS profit_factor
        FROM trades
        WHERE exit_time IS NOT NULL
          AND exit_time >= ? AND exit_time < ?
        GROUP BY enter_tag
        ORDER BY n DESC
        """,
        (start, end),
    ).fetchall()
    return {row["enter_tag"]: dict(row) for row in rows if row["enter_tag"]}


def parse_period(date_str: str) -> str:
    """将日期字符串规范化为 ISO datetime 字符串。"""
    dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    return dt.replace(tzinfo=dt.tzinfo or timezone.utc).isoformat()


def build_report(
    baseline_stats: Dict[str, dict],
    recent_stats: Dict[str, dict],
    baseline_range: Tuple[str, str],
    recent_range: Tuple[str, str],
) -> str:
    all_tags = sorted(set(list(baseline_stats.keys()) + list(recent_stats.keys())))
    # 优先排序：PRIORITY_TAGS 靠前
    priority_set = set(PRIORITY_TAGS)
    all_tags = [t for t in PRIORITY_TAGS if t in set(all_tags)] + [
        t for t in all_tags if t not in priority_set
    ]

    lines: List[str] = []
    lines.append("# P5 Enter Tag Drift Report\n")
    lines.append(f"- 基线窗口：{baseline_range[0]} ~ {baseline_range[1]}")
    lines.append(f"- 最近窗口：{recent_range[0]} ~ {recent_range[1]}")
    lines.append("")
    lines.append(
        "| enter_tag | 基线N | 近期N | 基线胜率 | 近期胜率 | 胜率Δ | 基线avgPnL | 近期avgPnL | PnLΔ | 基线PF | 近期PF |"
    )
    lines.append(
        "|-----------|-------|-------|----------|----------|-------|------------|------------|------|--------|--------|"
    )

    risk_tags: List[str] = []

    for tag in all_tags:
        b = baseline_stats.get(tag)
        r = recent_stats.get(tag)

        b_n = b["n"] if b else 0
        r_n = r["n"] if r else 0
        b_wr = b["win_rate"] if b and b["win_rate"] is not None else float("nan")
        r_wr = r["win_rate"] if r and r["win_rate"] is not None else float("nan")
        b_pnl = b["avg_pnl"] if b and b["avg_pnl"] is not None else float("nan")
        r_pnl = r["avg_pnl"] if r and r["avg_pnl"] is not None else float("nan")
        b_pf = b["profit_factor"] if b and b["profit_factor"] is not None else float("nan")
        r_pf = r["profit_factor"] if r and r["profit_factor"] is not None else float("nan")

        wr_delta = r_wr - b_wr if b and r and b["win_rate"] is not None and r["win_rate"] is not None else float("nan")
        pnl_delta = r_pnl - b_pnl if b and r and b["avg_pnl"] is not None and r["avg_pnl"] is not None else float("nan")

        def fmt(v, fmt_str=".3f") -> str:
            return format(v, fmt_str) if v == v else "N/A"  # NaN check

        lines.append(
            f"| {tag} | {b_n} | {r_n} | {fmt(b_wr, '.2%')} | {fmt(r_wr, '.2%')} | "
            f"{fmt(wr_delta, '+.2%')} | {fmt(b_pnl, '.4f')} | {fmt(r_pnl, '.4f')} | "
            f"{fmt(pnl_delta, '+.4f')} | {fmt(b_pf)} | {fmt(r_pf)} |"
        )

        # 风险提示条件
        if r_n > 0 and wr_delta == wr_delta and wr_delta < -0.10:
            risk_tags.append(f"- **{tag}**：近期胜率下降 {wr_delta:.2%}（基线 {b_wr:.2%} → 近期 {r_wr:.2%}）")
        if r_n > 0 and pnl_delta == pnl_delta and pnl_delta < -0.005:
            risk_tags.append(f"- **{tag}**：近期平均收益下降 {pnl_delta:.4f}")
        if r_n < 5 and tag in priority_set:
            risk_tags.append(f"- **{tag}**：近期样本不足 5 笔，结论不可靠")

    lines.append("")
    lines.append("## 风险提示")
    if risk_tags:
        # 去重
        seen = set()
        for rt in risk_tags:
            if rt not in seen:
                lines.append(rt)
                seen.add(rt)
    else:
        lines.append("- 暂无显著 drift 预警")
    lines.append("")

    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Enter Tag Drift Report")
    p.add_argument("--db", required=True, help="P5 SQLite 数据库路径")
    p.add_argument("--baseline-start", required=True, help="基线窗口开始 (YYYY-MM-DD)")
    p.add_argument("--baseline-end", required=True, help="基线窗口结束 (YYYY-MM-DD)")
    p.add_argument("--recent-start", required=True, help="最近窗口开始 (YYYY-MM-DD)")
    p.add_argument("--recent-end", required=True, help="最近窗口结束 (YYYY-MM-DD)")
    p.add_argument("--output", help="输出 markdown 文件路径（不指定则打印到 stdout）")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    db = Path(args.db)
    conn = connect_db(db)
    try:
        b_start = parse_period(args.baseline_start)
        b_end = parse_period(args.baseline_end)
        r_start = parse_period(args.recent_start)
        r_end = parse_period(args.recent_end)

        baseline_stats = fetch_tag_stats(conn, b_start, b_end)
        recent_stats = fetch_tag_stats(conn, r_start, r_end)

        report = build_report(
            baseline_stats, recent_stats,
            (b_start, b_end), (r_start, r_end),
        )

        if args.output:
            Path(args.output).write_text(report, encoding="utf-8")
            print(f"[OK] wrote enter_tag drift report to {args.output}")
        else:
            print(report)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
