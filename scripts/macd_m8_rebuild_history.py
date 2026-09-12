#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""历史底表重建 —— MACD M8（合约口径全量，2026-09-06 ~ 09-12 停采前）

为什么需要：
  9/6-9/11 的在线采集是**现货**口径，且 8 个标的（USELESS/BULLA/1000BONK 等）
  在币安现货无对应交易对 → 当时静默记 error，动量盲区。K 线永久可查，
  改用**合约（fapi）**口径重建该段历史 → 零缺口，且首帧时钟错位自动消失
  （重建器锚定 T，不受脚本耗时影响）。

用途：裁决 A（V3 阴线出场时 4h zone）的输入底表。

实现要点：
  - 直接 import 重建器的 state_at / pool_at（不重写计算逻辑，避免第二份真相）
  - 监控池 = MAJORS(9) + score 榜历史 CSV 还原的当刻 top3（score 榜不可重建，靠 CSV）
  - 断点续跑：已在输出文件中的 (ts, symbol) 跳过
  - 输出 = 与在线采集同构的 jsonl，但 src 恒为 futures

用法：
  B300_PROXY=http://127.0.0.1:7890 python3 scripts/macd_m8_rebuild_history.py
  B300_PROXY=... python3 scripts/macd_m8_rebuild_history.py --frames 2026-09-11   # 只跑某天
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from macd_m8_offline import state_at, pool_at  # noqa: E402
from macd_m8 import MAJORS  # noqa: E402

CST = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parent.parent
POOL_CSV = ROOT / "user_data" / "ai500_score_history_v3.csv"
OUT = ROOT / "user_data" / "macd_m8_rebuilt.jsonl"
SOURCE = "futures"


def build_frames() -> list[datetime]:
    """部署首帧 + 9/7~9/11 的 cron 帧（*:05 六档）+ 9/12 停采前的三帧。"""
    frames = [datetime(2026, 9, 6, 18, 29, 45, tzinfo=CST)]
    for day in range(7, 12):                      # 9/7 ~ 9/11
        for h in (0, 4, 8, 12, 16, 20):
            frames.append(datetime(2026, 9, day, h, 5, 0, tzinfo=CST))
    for h in (0, 4, 8):                           # 9/12 停采前（最后采集帧 08:05）
        frames.append(datetime(2026, 9, 12, h, 5, 0, tzinfo=CST))
    return frames


def load_done() -> set[tuple[str, str]]:
    done = set()
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            done.add((r.get("ts", ""), r.get("symbol", "")))
    return done


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=str, default="", help="只跑指定日期，如 2026-09-11")
    ap.add_argument("--limit-frames", type=int, default=0, help="只跑前 N 帧（试跑用）")
    args = ap.parse_args()

    frames = build_frames()
    if args.frames:
        frames = [f for f in frames if f.date().isoformat() == args.frames]
    if args.limit_frames:
        frames = frames[: args.limit_frames]

    done = load_done()
    print(f"[rebuild] 目标帧 {len(frames)} 个 | 已完成记录 {len(done)} 条 | 输出 {OUT.name}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    n_rec = n_err = 0
    with OUT.open("a", encoding="utf-8") as fh:
        for i, when in enumerate(frames, 1):
            ts = when.isoformat(timespec="seconds")
            try:
                pool = pool_at(str(POOL_CSV), when)
            except Exception as e:
                print(f"  [{i}/{len(frames)}] {ts} 池还原失败: {str(e)[:60]} → 仅用 MAJORS")
                pool = []
            symbols = list(dict.fromkeys(list(MAJORS) + list(pool)))
            if all((ts, s) in done for s in symbols):
                print(f"  [{i}/{len(frames)}] {ts} 已存在，跳过")
                continue
            ok = 0
            for sym in symbols:
                if (ts, sym) in done:
                    ok += 1
                    continue
                rec = state_at(sym, when, SOURCE)
                rec["ts"] = ts
                rec.setdefault("src", SOURCE)
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()
                if "state" in rec:
                    ok += 1
                    n_rec += 1
                else:
                    n_err += 1
            print(f"  [{i}/{len(frames)}] {ts} 池 {len(symbols)} 币 → 成功 {ok} | 累计记录 {n_rec} 错误 {n_err}")
    print(f"[rebuild] 完成：写入 {n_rec} 条，错误 {n_err} 条 → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
