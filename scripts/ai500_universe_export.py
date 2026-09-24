#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI500 可交易宇宙导出器 —— 为 RemotePairList 产出 pairlist JSON。

背景（2026-09-25，见 AI500_V3_GATE3_PREREG.md §6.16）
------------------------------------------------------
V3 原用 VolumePairList(number_assets=1000) → 实际池子 **685 对**，但策略只可能交易
AI500 top3。实测代价：每次 4h 边界 685 对一起刷新 → 「新 K 线未发布」竞态 →
**28,017 次白跑调用，全部落在 64 个边界小时**（16 天 × 4），峰值单小时 4,162 次、
持续 19.4 分钟；这些重试与 bstocks 的盘口定价共用同一出口 IP 权重，09-17 两台 bot
一起把币安额度打爆。本脚本把池子换成「可能被交易的标的」。

池子规则
--------
    pool = { 历史曾达 score >= buffer_score }     # 预热余量：入场门槛 80 > 缓冲 70
         ∪ { 最近 window_days 天内榜单出现过的 }

**覆盖率论证**：入场门槛是 score ≥ 80，缓冲门槛 70 更低 —— 任何想升到 80 的标的必须先
经过 70，届时已进池（本脚本 15 分钟刷新一次），等它真到 80 时已在池内。
实测（16 天）：曾达 ≥80 的仅 5 个 symbol，曾达 ≥70 的 21 个 → 池子约 25~35 对，
对比原 685 对减少约 20 倍。

安全条款
--------
* **原子写**：先写 .tmp 再 rename —— bot 绝不会读到半个 JSON
* **空池不覆盖**：池子为空（历史缺失/损坏）时**保持原文件不动**并退出码 2
  （空池 = 完全停止交易，绝不允许静默发生）
* 覆盖告警在策略侧：AI500 top3 若不在池内，策略会 WARNING + 落盘
  `ai500_v3_coverage_warn.csv`（唯一漏单风险，必须可检测）

用法
----
    .venv/bin/python scripts/ai500_universe_export.py            # 写 JSON
    .venv/bin/python scripts/ai500_universe_export.py --dry-run  # 只看，不写
"""
import argparse
import csv
import json
import os
import sys
from datetime import datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UD = os.environ.get("AI500_DATA_DIR", os.path.join(REPO, "user_data"))
HISTORY = os.environ.get("AI500_SCORE_HISTORY", os.path.join(UD, "ai500_score_history_v3.csv"))
OUT = os.path.join(UD, "ai500_universe.json")
# 在途仓并集：持仓中的 pair 若轮出池子，出场逻辑（dp.get_analyzed_dataframe）会拿不到数据
TRADES_DB = os.environ.get("AI500_TRADES_DB",
                           os.path.join(UD, "tradesv3.ai500_v3_dryrun.sqlite"))
MANIFEST = os.path.join(UD, "ai500_universe_manifest.json")

BUFFER_SCORE = float(os.environ.get("AI500_BUFFER_SCORE", 70))
WINDOW_DAYS = float(os.environ.get("AI500_WINDOW_DAYS", 7))
REFRESH_PERIOD = 900          # 与 15 分钟定时器一致
PAIR_SUFFIX = "/USDT:USDT"


def build_universe(history_path: str = HISTORY, buffer_score: float = BUFFER_SCORE,
                   window_days: float = WINDOW_DAYS) -> tuple[list[str], dict]:
    """返回 (pairs, stats)。纯函数，无副作用（可被夹具直接调用）。"""
    stats = {"history": os.path.basename(history_path), "rows": 0, "peak": 0,
             "window": 0, "universe": 0, "err": "", "newest": ""}
    if not os.path.exists(history_path):
        stats["err"] = "history_missing"
        return [], stats
    peak: set[str] = set()
    seen: list[tuple[datetime, str]] = []
    try:
        with open(history_path, errors="replace") as f:
            for r in csv.DictReader(f):
                sym = (r.get("symbol") or "").strip()
                if not sym:
                    continue
                try:
                    sc = float(r.get("score") or "")
                except (TypeError, ValueError):
                    sc = None
                if sc is not None and sc >= buffer_score:
                    peak.add(sym)
                ts = (r.get("fetched_at_utc") or "").strip()
                try:
                    t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    if t.tzinfo is None:
                        t = t.replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                seen.append((t, sym))
    except Exception as e:                      # 文件损坏 → 不产出（保持原文件）
        stats["err"] = f"{type(e).__name__}: {e}"
        return [], stats
    stats["rows"] = len(seen)
    stats["peak"] = len(peak)
    if not seen:
        stats["err"] = "no_rows"
        return [], stats
    newest = max(t for t, _ in seen)
    stats["newest"] = newest.isoformat()
    cutoff = newest - timedelta(days=window_days)
    window = {sym for t, sym in seen if t >= cutoff}
    stats["window"] = len(window)
    universe = sorted(peak | window)
    stats["universe"] = len(universe)
    return [s + PAIR_SUFFIX for s in universe], stats


def open_position_pairs(db_path: str = TRADES_DB) -> tuple[list[str], str]:
    """读 sqlite 取在途仓（只读，失败不影响主流程）。返回 (pairs, note)。"""
    import sqlite3
    if not os.path.exists(db_path):
        return [], "db_missing"
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        try:
            cur = con.execute("SELECT DISTINCT pair FROM trades WHERE is_open=1")
            pairs = [r[0] for r in cur.fetchall() if r[0]]
        finally:
            con.close()
        return pairs, f"ok({len(pairs)})"
    except Exception as e:                     # 库被锁/表结构变化 → 跳过，不让导出失败
        return [], f"{type(e).__name__}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--buffer-score", type=float, default=BUFFER_SCORE)
    ap.add_argument("--window-days", type=float, default=WINDOW_DAYS)
    a = ap.parse_args()

    pairs, stats = build_universe(buffer_score=a.buffer_score, window_days=a.window_days)
    held, note = open_position_pairs()
    stats["open_positions"] = note
    held = [p if p.endswith(PAIR_SUFFIX) or ":" in p else p + PAIR_SUFFIX for p in held]
    added = [p for p in held if p not in pairs]
    pairs = pairs + added                      # 在途仓必须留在池内（出场路径依赖框架数据）
    stats["open_positions_added"] = len(added)
    print(f"[universe] {stats}")
    if not pairs:
        print(f"[universe] ⛔ 池子为空（err={stats['err']}）→ **不覆盖** {OUT}，退出码 2",
              file=sys.stderr)
        return 2
    payload = {"pairs": pairs, "refresh_period": REFRESH_PERIOD}
    print(f"[universe] {len(pairs)} 对: {[p.split('/')[0] for p in pairs]}")
    if a.dry_run:
        print("[universe] --dry-run：未写文件")
        return 0
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)                       # 原子替换
    stats_out = dict(stats, written_at_utc=datetime.now(timezone.utc).isoformat(),
                     pairs=len(pairs), out=os.path.basename(OUT),
                     buffer_score=a.buffer_score, window_days=a.window_days)
    tmpm = MANIFEST + ".tmp"
    with open(tmpm, "w", encoding="utf-8") as f:
        json.dump(stats_out, f, ensure_ascii=False, indent=1)
    os.replace(tmpm, MANIFEST)
    print(f"[universe] 已写 {OUT}（{len(pairs)} 对）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
