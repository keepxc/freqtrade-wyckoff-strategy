#!/usr/bin/env python3
"""Build a minimal weekly markdown report from the P5 SQLite database."""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import List, Tuple


def connect_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def parse_dt(text: str) -> datetime:
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def get_week_bounds(explicit_start: str | None, explicit_end: str | None) -> Tuple[datetime, datetime]:
    if explicit_start and explicit_end:
        return parse_dt(explicit_start), parse_dt(explicit_end)
    now = datetime.now(timezone.utc)
    end = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=7)
    return start, end


def fetch_one(conn: sqlite3.Connection, sql: str, params=()):
    row = conn.execute(sql, params).fetchone()
    return row if row else None


def fetch_all(conn: sqlite3.Connection, sql: str, params=()):
    return conn.execute(sql, params).fetchall()


def get_pnf_section(conn: sqlite3.Connection, start: datetime, end: datetime) -> List[str]:
    """V8 PNF 状态：从 regime_snapshots.daily_tr_exists 统计 TR Low 覆盖率。

    V8 无独立 PNF 锁定日志；TR Low 覆盖率 = SC 已确认 + Phase B+ 已建立。
    daily_tr_exists=1 意味着当前 bar 有有效 TR Low，PNF 目标位可用。
    """
    start_s, end_s = start.isoformat(), end.isoformat()
    row = conn.execute(
        """
        SELECT SUM(CASE WHEN daily_tr_exists = 1 THEN 1 ELSE 0 END) AS tr_bars,
               COUNT(*) AS total
        FROM regime_snapshots WHERE ts >= ? AND ts < ?
        """,
        (start_s, end_s),
    ).fetchone()

    lines = ["## V8 PNF / TR Low 覆盖率"]
    if row and row["total"]:
        tr_bars = row["tr_bars"] or 0
        total = row["total"]
        pct = tr_bars / total
        lines.append(f"- TR Low 有效 bars：{tr_bars} / {total} ({pct:.1%})")
        if pct < 0.3:
            lines.append("- ⚠️ TR Low 覆盖率偏低（< 30%），SC 锚点尚未建立或已失效，PNF 目标位不可用")
        elif pct < 0.6:
            lines.append("- 🔶 TR Low 覆盖率中等（30–60%），部分时段无 PNF 目标位")
        else:
            lines.append("- ✅ TR Low 覆盖率正常（≥ 60%），PNF 结构有效")
    else:
        lines.append("- 本周无 regime 快照，无法统计 TR Low 覆盖率")
    lines.append("")
    return lines


def build_report(conn: sqlite3.Connection, start: datetime, end: datetime) -> Tuple[str, List[str], float]:
    start_s, end_s = start.isoformat(), end.isoformat()

    # V8 PNF / TR Low 覆盖率（从 regime_snapshots）
    pnf_lines = get_pnf_section(conn, start, end)

    trades = fetch_one(
        conn,
        """
        SELECT COUNT(*) AS n,
               AVG(profit_pct) AS avg_profit_pct,
               SUM(CASE WHEN profit_pct > 0 THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(*),0) AS win_rate,
               AVG(duration_minutes) AS avg_duration_minutes
        FROM trades
        WHERE exit_time IS NOT NULL AND exit_time >= ? AND exit_time < ?
        """,
        (start_s, end_s),
    )

    signals = fetch_one(
        conn,
        """
        SELECT COUNT(*) AS total_signals,
               SUM(CASE WHEN was_executed = 1 THEN 1 ELSE 0 END) AS executed_signals,
               SUM(CASE WHEN regime_blocked = 1 THEN 1 ELSE 0 END) AS regime_blocked_n
        FROM signals
        WHERE ts >= ? AND ts < ?
        """,
        (start_s, end_s),
    )

    # V1B-7: detector 激活率摘要
    detector_rows = fetch_all(
        conn,
        """
        SELECT event_name, event_family, COUNT(*) AS event_count
        FROM detector_events
        WHERE ts >= ? AND ts < ?
        GROUP BY event_name
        ORDER BY event_count DESC, event_name
        LIMIT 8
        """,
        (start_s, end_s),
    )

    total_bars_row = fetch_one(
        conn,
        "SELECT COUNT(*) AS n FROM regime_snapshots WHERE ts >= ? AND ts < ?",
        (start_s, end_s),
    )
    total_bars = total_bars_row["n"] if total_bars_row else 0

    # V1B-7: regime 分布摘要（全 4 个字段）
    regime_rows = fetch_all(
        conn,
        """
        SELECT COALESCE(market_env, 'null') AS market_env, COUNT(*) AS bars
        FROM regime_snapshots
        WHERE ts >= ? AND ts < ?
        GROUP BY COALESCE(market_env, 'null')
        ORDER BY bars DESC
        """,
        (start_s, end_s),
    )

    regime_htf_rows = fetch_all(
        conn,
        """
        SELECT COALESCE(htf_market_env, 'null') AS val, COUNT(*) AS bars
        FROM regime_snapshots WHERE ts >= ? AND ts < ?
        GROUP BY COALESCE(htf_market_env, 'null') ORDER BY bars DESC LIMIT 5
        """,
        (start_s, end_s),
    )

    regime_bear_row = fetch_one(
        conn,
        """
        SELECT
            SUM(CASE WHEN bear_market_mode = 1 THEN 1 ELSE 0 END) AS bear_bars,
            COUNT(*) AS total
        FROM regime_snapshots WHERE ts >= ? AND ts < ?
        """,
        (start_s, end_s),
    )

    # V8: enter_tag 汇总（v8_wave_start 为唯一入场标签）
    tag_rows = fetch_all(
        conn,
        """
        SELECT COALESCE(enter_tag, 'unknown') AS enter_tag,
               COUNT(*) AS n,
               AVG(profit_pct) AS avg_pnl,
               SUM(CASE WHEN profit_pct > 0 THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(*),0) AS win_rate
        FROM trades
        WHERE exit_time IS NOT NULL AND exit_time >= ? AND exit_time < ?
        GROUP BY enter_tag ORDER BY n DESC
        """,
        (start_s, end_s),
    )

    # V8: Phase 入场分布（D/E/unknown 各自的胜率和收益）
    phase_entry_rows = fetch_all(
        conn,
        """
        SELECT COALESCE(phase_context_tag_enter, 'unknown') AS phase,
               COUNT(*) AS n,
               AVG(profit_pct) AS avg_pnl,
               SUM(CASE WHEN profit_pct > 0 THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(*),0) AS win_rate
        FROM trades
        WHERE exit_time IS NOT NULL AND exit_time >= ? AND exit_time < ?
        GROUP BY phase ORDER BY n DESC
        """,
        (start_s, end_s),
    )

    # V8: 退出原因分布（v8_down_wave / v8_structure_fail / v8_pnf_t2 / stop_loss 等）
    exit_reason_rows = fetch_all(
        conn,
        """
        SELECT COALESCE(exit_reason, 'unknown') AS exit_reason,
               COUNT(*) AS n,
               AVG(profit_pct) AS avg_pnl,
               SUM(CASE WHEN profit_pct > 0 THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(*),0) AS win_rate
        FROM trades
        WHERE exit_time IS NOT NULL AND exit_time >= ? AND exit_time < ?
        GROUP BY exit_reason ORDER BY n DESC
        """,
        (start_s, end_s),
    )

    warnings: List[str] = []
    health_score = 1.0

    trade_n = trades["n"] if trades and trades["n"] is not None else 0
    executed = signals["executed_signals"] if signals and signals["executed_signals"] is not None else 0
    total_signals = signals["total_signals"] if signals and signals["total_signals"] is not None else 0
    regime_blocked_n = signals["regime_blocked_n"] if signals and signals["regime_blocked_n"] is not None else 0
    execution_rate = (executed / total_signals) if total_signals else 0.0
    win_rate = trades["win_rate"] if trades and trades["win_rate"] is not None else 0.0
    avg_profit_pct = trades["avg_profit_pct"] if trades and trades["avg_profit_pct"] is not None else 0.0

    if total_signals < 10:
        warnings.append("样本偏少：本周候选信号少于 10 条，仅适合观察。")
        health_score -= 0.15
    if trade_n > 0 and win_rate < 0.45:
        warnings.append("交易胜率偏弱：本周已平仓交易胜率低于 45%。")
        health_score -= 0.25
    if avg_profit_pct < -0.01:
        warnings.append("平均单笔收益偏弱：本周已平仓交易平均收益低于 -1%。")
        health_score -= 0.2
    if execution_rate < 0.1 and total_signals >= 10:
        warnings.append("执行率偏低：大量候选信号未进入成交集合。")
        health_score -= 0.15

    # V1B-7: bear_market_mode 预警
    bear_bars = regime_bear_row["bear_bars"] if regime_bear_row and regime_bear_row["bear_bars"] else 0
    bear_total = regime_bear_row["total"] if regime_bear_row and regime_bear_row["total"] else 0
    bear_pct = bear_bars / bear_total if bear_total > 0 else 0.0
    if bear_pct > 0.5:
        warnings.append(f"P2 Regime Mismatch：本周 bear_market_mode 占比 {bear_pct:.1%}，超过 50%，建议检查 P2 触发条件。")
        health_score -= 0.1

    health_score = max(0.0, min(1.0, health_score))

    lines: List[str] = []
    lines.append(f"# P5 Weekly Report\n")
    lines.append(f"- 周期：{start_s} ~ {end_s}")
    lines.append(f"- 健康分：{health_score:.2f}")
    lines.append("")
    lines.append("## 交易摘要")
    lines.append(f"- 已平仓交易数：{trade_n}")
    lines.append(f"- 平均单笔收益：{avg_profit_pct:.4f}")
    lines.append(f"- 胜率：{win_rate:.2%}")
    avg_duration = trades["avg_duration_minutes"] if trades and trades["avg_duration_minutes"] is not None else 0.0
    lines.append(f"- 平均持仓时长（分钟）：{avg_duration:.1f}")
    lines.append("")
    lines.append("## 信号摘要")
    lines.append(f"- 候选信号数：{total_signals}")
    lines.append(f"- 成交信号数：{executed}")
    lines.append(f"- 执行率：{execution_rate:.2%}")
    lines.append("")

    # V1B-7: Detector 激活率摘要
    lines.append("## Detector 激活率摘要")
    if detector_rows:
        for row in detector_rows:
            rate = row["event_count"] / total_bars if total_bars > 0 else 0.0
            lines.append(f"- [{row['event_family']}] {row['event_name']}: {row['event_count']} 次 (per-bar率 {rate:.4f})")
    else:
        lines.append("- 本周无 detector 事件")
    lines.append(f"- Regime blocked 信号数：{regime_blocked_n}")
    lines.append("")

    # V1B-7: Regime 分布摘要
    lines.append("## Regime 分布摘要")
    total_bars_all = sum(row["bars"] for row in regime_rows) if regime_rows else 0
    if regime_rows and total_bars_all:
        lines.append("**market_env:**")
        for row in regime_rows:
            ratio = row["bars"] / total_bars_all
            lines.append(f"- {row['market_env']}: {row['bars']} ({ratio:.2%})")
    else:
        lines.append("- 本周无 regime 快照")
    if regime_htf_rows:
        htf_total = sum(r["bars"] for r in regime_htf_rows)
        lines.append("**htf_market_env:**")
        for row in regime_htf_rows:
            lines.append(f"- {row['val']}: {row['bars']} ({row['bars'] / htf_total:.2%})")
    if bear_total > 0:
        lines.append(f"**bear_market_mode:** {bear_bars}/{bear_total} bars ({bear_pct:.2%})")
    lines.append("")

    # V8: Enter Tag 摘要
    lines.append("## Enter Tag 周报摘要")
    if tag_rows:
        for row in tag_rows:
            wr = row["win_rate"] or 0.0
            pnl = row["avg_pnl"] or 0.0
            lines.append(f"- {row['enter_tag']}: n={row['n']}, 胜率={wr:.2%}, avg_pnl={pnl:.4f}")
    else:
        lines.append("- 本周无已平仓交易")
    lines.append("")

    # V8: Phase 入场分布
    lines.append("## V8 Phase 入场分布")
    if phase_entry_rows:
        lines.append("| Phase | N | 胜率 | avg_pnl |")
        lines.append("|-------|---|------|---------|")
        for row in phase_entry_rows:
            wr = row["win_rate"] or 0.0
            pnl = row["avg_pnl"] or 0.0
            lines.append(f"| {row['phase']} | {row['n']} | {wr:.2%} | {pnl:.4f} |")
    else:
        lines.append("- 本周无已平仓交易")
    lines.append("")

    # V8: 退出原因分布
    lines.append("## V8 退出原因分布")
    if exit_reason_rows:
        lines.append("| 退出原因 | N | 胜率 | avg_pnl |")
        lines.append("|----------|---|------|---------|")
        for row in exit_reason_rows:
            wr = row["win_rate"] or 0.0
            pnl = row["avg_pnl"] or 0.0
            lines.append(f"| {row['exit_reason']} | {row['n']} | {wr:.2%} | {pnl:.4f} |")
    else:
        lines.append("- 本周无已平仓交易")
    lines.append("")

    # V8 PNF / TR Low 覆盖率
    if pnf_lines:
        lines.extend(pnf_lines)

    # 预警 & 样本不足提醒
    lines.append("## 预警")
    if warnings:
        for w in warnings:
            lines.append(f"- {w}")
    else:
        lines.append("- 暂无显著预警")
    lines.append("")

    if trade_n < 5:
        lines.append("> [WARN] **样本不足提醒**：本周已平仓交易小于 5 笔，所有统计结论仅供参考，不建议基于此做决策。")
        lines.append("")

    lines.append("")

    return "\n".join(lines), warnings, health_score


def maybe_store_report(conn: sqlite3.Connection, week_start: datetime, week_end: datetime, markdown: str, warnings: List[str], health_score: float) -> None:
    report_id = f"weekly::{week_start.date()}::{week_end.date()}"
    conn.execute(
        """
        INSERT INTO weekly_reports(report_id, week_start, week_end, run_scope, report_type, summary_markdown, health_score, warnings_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(report_id) DO UPDATE SET
            summary_markdown=excluded.summary_markdown,
            health_score=excluded.health_score,
            warnings_json=excluded.warnings_json,
            created_at=excluded.created_at
        """,
        (
            report_id,
            week_start.date().isoformat(),
            week_end.date().isoformat(),
            "all_runs",
            "dry_run",
            markdown,
            health_score,
            json.dumps(warnings, ensure_ascii=False),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True)
    p.add_argument("--week-start")
    p.add_argument("--week-end")
    p.add_argument("--output")
    p.add_argument("--store", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    db = Path(args.db)
    conn = connect_db(db)
    try:
        start, end = get_week_bounds(args.week_start, args.week_end)
        markdown, warnings, health_score = build_report(conn, start, end)
        if args.output:
            Path(args.output).write_text(markdown, encoding="utf-8")
            print(f"[OK] wrote weekly report to {args.output}")
        else:
            print(markdown)
        if args.store:
            maybe_store_report(conn, start, end, markdown, warnings, health_score)
            print("[OK] stored report in weekly_reports")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
