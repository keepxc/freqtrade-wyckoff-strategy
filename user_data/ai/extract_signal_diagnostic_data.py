#!/usr/bin/env python3
"""提取信号生态诊断所需的数据摘要，输出为 JSON 供 AI 分析。

用法：
  python extract_signal_diagnostic_data.py --db user_data/market.db \\
      --recent-days 30 \\
      --baseline-start 2023-01-01 --baseline-end 2024-01-01 \\
      --output signal_diagnostic_data.json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def connect_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def parse_dt(text: str) -> datetime:
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def get_recent_bounds(days: int) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    end = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=days)
    return start.isoformat(), end.isoformat()


def fetch_signal_quality_distribution(conn: sqlite3.Connection, start: str, end: str) -> Dict[str, Any]:
    """信号质量分布统计"""
    rows = conn.execute(
        """
        SELECT
            CASE
                WHEN signal_quality_after < 0.5 THEN '0.0-0.5'
                WHEN signal_quality_after < 0.8 THEN '0.5-0.8'
                WHEN signal_quality_after < 1.0 THEN '0.8-1.0'
                WHEN signal_quality_after < 1.2 THEN '1.0-1.2'
                ELSE '>1.2'
            END AS quality_bucket,
            COUNT(*) AS signal_count,
            SUM(CASE WHEN was_executed = 1 THEN 1 ELSE 0 END) AS executed_count,
            AVG(CASE WHEN was_executed = 1 THEN signal_quality_after ELSE NULL END) AS avg_quality_executed
        FROM signals
        WHERE ts >= ? AND ts < ?
        GROUP BY quality_bucket
        ORDER BY quality_bucket
        """,
        (start, end),
    ).fetchall()

    # 获取已执行信号对应的交易表现
    trade_perf = conn.execute(
        """
        SELECT
            CASE
                WHEN t.signal_quality_enter < 0.5 THEN '0.0-0.5'
                WHEN t.signal_quality_enter < 0.8 THEN '0.5-0.8'
                WHEN t.signal_quality_enter < 1.0 THEN '0.8-1.0'
                WHEN t.signal_quality_enter < 1.2 THEN '1.0-1.2'
                ELSE '>1.2'
            END AS quality_bucket,
            COUNT(*) AS trade_count,
            AVG(t.profit_pct) AS avg_pnl,
            SUM(CASE WHEN t.profit_pct > 0 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS win_rate
        FROM trades t
        WHERE t.exit_time IS NOT NULL
          AND t.exit_time >= ? AND t.exit_time < ?
        GROUP BY quality_bucket
        """,
        (start, end),
    ).fetchall()

    quality_dist = [dict(r) for r in rows]
    trade_perf_map = {r["quality_bucket"]: dict(r) for r in trade_perf}

    # 合并
    for item in quality_dist:
        bucket = item["quality_bucket"]
        if bucket in trade_perf_map:
            item["trade_count"] = trade_perf_map[bucket]["trade_count"]
            item["avg_pnl"] = trade_perf_map[bucket]["avg_pnl"]
            item["win_rate"] = trade_perf_map[bucket]["win_rate"]
        else:
            item["trade_count"] = 0
            item["avg_pnl"] = None
            item["win_rate"] = None

    return {
        "quality_distribution": quality_dist,
        "total_signals": sum(r["signal_count"] for r in quality_dist),
        "total_executed": sum(r["executed_count"] for r in quality_dist),
    }


def fetch_rejection_reasons(conn: sqlite3.Connection, start: str, end: str) -> List[Dict[str, Any]]:
    """拒绝原因统计"""
    rows = conn.execute(
        """
        SELECT
            COALESCE(rejection_reason, 'unknown') AS rejection_reason,
            COUNT(*) AS count,
            AVG(signal_quality_after) AS avg_quality,
            MIN(signal_quality_after) AS min_quality,
            MAX(signal_quality_after) AS max_quality
        FROM signals
        WHERE ts >= ? AND ts < ?
          AND was_executed = 0
        GROUP BY rejection_reason
        ORDER BY count DESC
        """,
        (start, end),
    ).fetchall()
    return [dict(r) for r in rows]


def fetch_phase_efficiency(conn: sqlite3.Connection, start: str, end: str) -> List[Dict[str, Any]]:
    """相位效率对比"""
    # 信号层面
    signal_rows = conn.execute(
        """
        SELECT
            COALESCE(phase_context_tag, 'unknown') AS phase,
            COUNT(*) AS signal_count,
            SUM(CASE WHEN was_executed = 1 THEN 1 ELSE 0 END) AS executed_count,
            AVG(signal_quality_after) AS avg_quality
        FROM signals
        WHERE ts >= ? AND ts < ?
        GROUP BY phase
        """,
        (start, end),
    ).fetchall()

    # 交易层面
    trade_rows = conn.execute(
        """
        SELECT
            COALESCE(phase_context_tag_enter, 'unknown') AS phase,
            COUNT(*) AS trade_count,
            AVG(profit_pct) AS avg_pnl,
            SUM(CASE WHEN profit_pct > 0 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS win_rate
        FROM trades
        WHERE exit_time IS NOT NULL
          AND exit_time >= ? AND exit_time < ?
        GROUP BY phase
        """,
        (start, end),
    ).fetchall()

    signal_map = {r["phase"]: dict(r) for r in signal_rows}
    trade_map = {r["phase"]: dict(r) for r in trade_rows}

    all_phases = set(signal_map.keys()) | set(trade_map.keys())
    result = []
    for phase in sorted(all_phases):
        item = {"phase": phase}
        if phase in signal_map:
            item.update(signal_map[phase])
        else:
            item.update({"signal_count": 0, "executed_count": 0, "avg_quality": None})
        if phase in trade_map:
            item["trade_count"] = trade_map[phase]["trade_count"]
            item["avg_pnl"] = trade_map[phase]["avg_pnl"]
            item["win_rate"] = trade_map[phase]["win_rate"]
        else:
            item["trade_count"] = 0
            item["avg_pnl"] = None
            item["win_rate"] = None
        result.append(item)

    return result


def fetch_enter_tag_regime_matrix(conn: sqlite3.Connection, start: str, end: str) -> List[Dict[str, Any]]:
    """Enter Tag × Regime 交叉分析"""
    rows = conn.execute(
        """
        SELECT
            COALESCE(enter_tag, 'unknown') AS enter_tag,
            COALESCE(macro_phase_enter, 'unknown') AS macro_phase,
            COALESCE(market_env_enter, 'unknown') AS market_env,
            COUNT(*) AS trade_count,
            AVG(profit_pct) AS avg_pnl,
            SUM(CASE WHEN profit_pct > 0 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS win_rate
        FROM trades
        WHERE exit_time IS NOT NULL
          AND exit_time >= ? AND exit_time < ?
        GROUP BY enter_tag, macro_phase, market_env
        HAVING COUNT(*) >= 3
        ORDER BY trade_count DESC
        LIMIT 50
        """,
        (start, end),
    ).fetchall()
    return [dict(r) for r in rows]


def fetch_drift_comparison(
    conn: sqlite3.Connection,
    recent_start: str,
    recent_end: str,
    baseline_start: str,
    baseline_end: str,
) -> Dict[str, Any]:
    """漂移对比：最近窗口 vs 基线窗口"""

    def get_window_stats(start: str, end: str) -> Dict[str, Any]:
        # 信号统计
        signal_row = conn.execute(
            """
            SELECT
                COUNT(*) AS total_signals,
                AVG(signal_quality_after) AS avg_quality,
                SUM(CASE WHEN was_executed = 1 THEN 1 ELSE 0 END) AS executed_signals
            FROM signals
            WHERE ts >= ? AND ts < ?
            """,
            (start, end),
        ).fetchone()

        # 交易统计
        trade_row = conn.execute(
            """
            SELECT
                COUNT(*) AS trade_count,
                AVG(profit_pct) AS avg_pnl,
                SUM(CASE WHEN profit_pct > 0 THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(*), 0) AS win_rate
            FROM trades
            WHERE exit_time IS NOT NULL
              AND exit_time >= ? AND exit_time < ?
            """,
            (start, end),
        ).fetchone()

        return {
            "total_signals": signal_row["total_signals"] if signal_row else 0,
            "avg_quality": signal_row["avg_quality"] if signal_row else None,
            "executed_signals": signal_row["executed_signals"] if signal_row else 0,
            "trade_count": trade_row["trade_count"] if trade_row else 0,
            "avg_pnl": trade_row["avg_pnl"] if trade_row else None,
            "win_rate": trade_row["win_rate"] if trade_row else None,
        }

    recent = get_window_stats(recent_start, recent_end)
    baseline = get_window_stats(baseline_start, baseline_end)

    # 计算 delta
    delta = {}
    for key in ["avg_quality", "avg_pnl", "win_rate"]:
        if recent.get(key) is not None and baseline.get(key) is not None:
            delta[f"{key}_delta"] = recent[key] - baseline[key]
        else:
            delta[f"{key}_delta"] = None

    return {
        "recent_window": {"start": recent_start, "end": recent_end, "stats": recent},
        "baseline_window": {"start": baseline_start, "end": baseline_end, "stats": baseline},
        "delta": delta,
    }


def fetch_detector_activation_summary(conn: sqlite3.Connection, start: str, end: str) -> List[Dict[str, Any]]:
    """Detector 激活率摘要"""
    rows = conn.execute(
        """
        SELECT
            event_family,
            event_name,
            COUNT(*) AS activation_count
        FROM detector_events
        WHERE ts >= ? AND ts < ?
        GROUP BY event_family, event_name
        ORDER BY activation_count DESC
        LIMIT 20
        """,
        (start, end),
    ).fetchall()
    return [dict(r) for r in rows]


def fetch_regime_distribution(conn: sqlite3.Connection, start: str, end: str) -> Dict[str, Any]:
    """Regime 分布统计"""
    market_env_rows = conn.execute(
        """
        SELECT
            COALESCE(market_env, 'null') AS market_env,
            COUNT(*) AS bar_count
        FROM regime_snapshots
        WHERE ts >= ? AND ts < ?
        GROUP BY market_env
        ORDER BY bar_count DESC
        """,
        (start, end),
    ).fetchall()

    macro_phase_rows = conn.execute(
        """
        SELECT
            COALESCE(macro_phase, 'null') AS macro_phase,
            COUNT(*) AS bar_count
        FROM regime_snapshots
        WHERE ts >= ? AND ts < ?
        GROUP BY macro_phase
        ORDER BY bar_count DESC
        """,
        (start, end),
    ).fetchall()

    bear_row = conn.execute(
        """
        SELECT
            SUM(CASE WHEN bear_market_mode = 1 THEN 1 ELSE 0 END) AS bear_bars,
            COUNT(*) AS total_bars
        FROM regime_snapshots
        WHERE ts >= ? AND ts < ?
        """,
        (start, end),
    ).fetchone()

    return {
        "market_env_distribution": [dict(r) for r in market_env_rows],
        "macro_phase_distribution": [dict(r) for r in macro_phase_rows],
        "bear_market_mode": {
            "bear_bars": bear_row["bear_bars"] if bear_row else 0,
            "total_bars": bear_row["total_bars"] if bear_row else 0,
            "bear_ratio": (
                bear_row["bear_bars"] / bear_row["total_bars"]
                if bear_row and bear_row["total_bars"] > 0
                else 0.0
            ),
        },
    }


def build_diagnostic_data(
    conn: sqlite3.Connection,
    recent_start: str,
    recent_end: str,
    baseline_start: Optional[str] = None,
    baseline_end: Optional[str] = None,
) -> Dict[str, Any]:
    """构建完整的诊断数据包"""
    data = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "recent_window": {"start": recent_start, "end": recent_end},
            "baseline_window": (
                {"start": baseline_start, "end": baseline_end}
                if baseline_start and baseline_end
                else None
            ),
        },
        "signal_quality": fetch_signal_quality_distribution(conn, recent_start, recent_end),
        "rejection_reasons": fetch_rejection_reasons(conn, recent_start, recent_end),
        "phase_efficiency": fetch_phase_efficiency(conn, recent_start, recent_end),
        "enter_tag_regime_matrix": fetch_enter_tag_regime_matrix(conn, recent_start, recent_end),
        "detector_activation": fetch_detector_activation_summary(conn, recent_start, recent_end),
        "regime_distribution": fetch_regime_distribution(conn, recent_start, recent_end),
    }

    if baseline_start and baseline_end:
        data["drift_comparison"] = fetch_drift_comparison(
            conn, recent_start, recent_end, baseline_start, baseline_end
        )

    return data


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="提取信号生态诊断数据")
    p.add_argument("--db", required=True, help="P5 SQLite 数据库路径")
    p.add_argument("--recent-days", type=int, default=30, help="最近窗口天数（默认 30）")
    p.add_argument("--baseline-start", help="基线窗口开始 (YYYY-MM-DD)")
    p.add_argument("--baseline-end", help="基线窗口结束 (YYYY-MM-DD)")
    p.add_argument("--output", required=True, help="输出 JSON 文件路径")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    db = Path(args.db)
    conn = connect_db(db)

    try:
        recent_start, recent_end = get_recent_bounds(args.recent_days)

        baseline_start = None
        baseline_end = None
        if args.baseline_start and args.baseline_end:
            baseline_start = datetime.fromisoformat(args.baseline_start).replace(tzinfo=timezone.utc).isoformat()
            baseline_end = datetime.fromisoformat(args.baseline_end).replace(tzinfo=timezone.utc).isoformat()

        data = build_diagnostic_data(conn, recent_start, recent_end, baseline_start, baseline_end)

        output_path = Path(args.output)
        output_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[OK] 诊断数据已写入：{output_path}")
        print(f"[INFO] 最近窗口：{recent_start} ~ {recent_end}")
        if baseline_start:
            print(f"[INFO] 基线窗口：{baseline_start} ~ {baseline_end}")
        print(f"[INFO] 总信号数：{data['signal_quality']['total_signals']}")
        print(f"[INFO] 已执行信号数：{data['signal_quality']['total_executed']}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
