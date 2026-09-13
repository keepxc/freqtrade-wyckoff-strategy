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
  - **帧来源 = 采集留存 `macd_m8.jsonl` 里实际出现过的 ts**，不按调度表推算
  - **监控池 = 三者并集**：MAJORS(9) ∪ 该帧采集器实际扫过的标的 ∪ CSV 还原的 top3
  - 每条记录带 `frame_kind`（cron / manual），下游据此分开统计
  - 池还原失败 → **整体中止（exit 2）**，不降级为仅 MAJORS（理由见 main() 内注释）
  - 断点续跑：已在输出文件中的 (ts, symbol) 跳过
  - 输出 = 与在线采集同构的 jsonl，但 src 恒为 futures

为什么帧必须从 jsonl 取，而不是按「应该跑过哪些帧」推算（2026-09-13 实测）：
  原实现硬编码「9/7~9/11 每天 *:05 六档 + 9/12 三帧」共 34 帧，与留存对照后发现
    ① 漏了 3 个真实帧：09-06 18:24:56、09-06 23:54:51、09-07 16:06:16（手动/补跑）
    ② 秒级 ts 对不上：采集是 :02/:03/:07/:17/:21 不等，硬编码全是 :00，
       38 帧里只有 1 帧秒级相同 → 重建结果**无法按 ts join 回采集数据**
  重建对给定 T 是确定性的，用采集器的真实 ts 严格更优：不漏帧、不臆造帧、可 join。
  推论：本脚本只覆盖**采集期内**的帧；停采之后的时点不在 jsonl 里，需要另行指定。

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
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
POOL_CSV = ROOT / "user_data" / "ai500_score_history_v3.csv"
COLLECTED = HERE / "macd_m8.jsonl"       # 采集留存：帧与池的真相源
OUT = ROOT / "user_data" / "macd_m8_rebuilt.jsonl"
SOURCE = "futures"

CRON_HOURS = (0, 4, 8, 12, 16, 20)       # 系统 crontab 的六档（分钟固定 05）


def load_collected(path: Path) -> dict[str, set[str]]:
    """读采集留存 → {ts: 该帧采集器实际扫过的标的集合}。

    含 error 行：采集时报错的行同样记录了 symbol，说明当时池里有它
    （HYPE 38/38 全 error 就是这么暴露的），重建时必须补上。
    """
    if not path.exists():
        raise SystemExit(
            f"找不到采集留存 {path}——本脚本的帧与池都来自它，不能按调度表推算。\n"
            f"它同时是校验重建结果的唯一对照物，不要删。")
    frames: dict[str, set[str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts, sym = r.get("ts"), r.get("symbol")
        if ts and sym:
            frames.setdefault(ts, set()).add(sym)
    if not frames:
        raise SystemExit(f"{path} 里没有可用记录")
    return frames


def classify_frames(ts_list) -> dict[str, str]:
    """标注 cron / manual —— §八-5「手动帧与 cron 帧不同质，统计前必须分开」。

    ⚠️ 这是**启发式**，不是真相：采集器没有落盘触发来源。规则为
      同一 (日期, 小时) 桶内，首个落在 cron 档（小时 ∈ CRON_HOURS 且分钟 == 05）
      的 ts 记 cron，其余一律 manual。
    因此 09-07 12:05:03 记 cron、同分钟稍后的 12:05:55 记 manual（判定为补跑）。
    若下游对某帧的归类有异议，以 crontab 日志为准，不要反过来改本规则去迁就结果。
    """
    kinds, seen = {}, set()
    for ts in sorted(ts_list):
        d = datetime.fromisoformat(ts)
        bucket = (d.date(), d.hour)
        if d.hour in CRON_HOURS and d.minute == 5 and bucket not in seen:
            kinds[ts] = "cron"
            seen.add(bucket)
        else:
            kinds[ts] = "manual"
    return kinds


def load_done() -> set[tuple[str, str]]:
    """已完成的 (ts, symbol)，用于断点续跑。

    ⚠️ 拒绝与旧版输出混写：2026-09-13 之前的版本按硬编码网格跑，ts 一律 `*:05:00`，
    与现在从采集留存取的真实 ts（:02/:03/:17/…）**永不相等** → 续跑不会跳过任何记录，
    只会把两套 ts 口径的行叠进同一个文件，而且没有字段能区分它们。
    旧版行没有 `frame_kind`，据此识别并拒绝，要求先把旧文件挪走。
    """
    done = set()
    if not OUT.exists():
        return done
    legacy = 0
    for line in OUT.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "frame_kind" not in r:
            legacy += 1
        done.add((r.get("ts", ""), r.get("symbol", "")))
    if legacy:
        raise SystemExit(
            f"{OUT} 含 {legacy} 条旧版记录（无 frame_kind，ts 为硬编码的 *:05:00）。\n"
            f"旧版帧口径与本版不兼容，续写会产生两套 ts 混在一起且无法区分的底表。\n"
            f"请先挪走再重跑：  mv {OUT} {OUT}.legacy")
    return done


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=str, default="", help="只跑指定日期，如 2026-09-11")
    ap.add_argument("--limit-frames", type=int, default=0, help="只跑前 N 帧（试跑用）")
    args = ap.parse_args()

    collected = load_collected(COLLECTED)
    kinds = classify_frames(collected.keys())
    frames = sorted(collected)          # ISO + 同一 offset，字典序即时间序
    if args.frames:
        frames = [t for t in frames if t[:10] == args.frames]
    if args.limit_frames:
        frames = frames[: args.limit_frames]

    done = load_done()
    n_cron = sum(1 for t in frames if kinds[t] == "cron")
    print(f"[rebuild] 帧来源 {COLLECTED.name} | 目标帧 {len(frames)} 个"
          f"（cron {n_cron} / manual {len(frames) - n_cron}）"
          f" | 已完成记录 {len(done)} 条 | 输出 {OUT.name}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    n_rec = n_err = 0
    with OUT.open("a", encoding="utf-8") as fh:
        for i, ts in enumerate(frames, 1):
            when = datetime.fromisoformat(ts)
            # 池还原失败必须**中止**，不得降级为「仅用 MAJORS」：本底表的用途是裁决 A
            # （V3 阴线出场时的 4h zone），而 V3 只交易 AI500 top3，MAJORS 里一个都没有。
            # 降级会静默写出一份"看起来跑完了"但恰好缺了唯一有用那部分的底表。
            # 断点续跑按 (ts, symbol) 去重，修好 CSV 后重跑不会重复计算，中止的代价≈0。
            # ⚠️ pool_at 抛的是 SystemExit（BaseException 子类），单写 except Exception 接不住。
            try:
                pool = pool_at(str(POOL_CSV), when)
            except (Exception, SystemExit) as e:
                print(f"\n[abort] 第 {i}/{len(frames)} 帧 {ts} 监控池还原失败: {str(e)[:120]}")
                print(f"[abort] score 榜历史 CSV: {POOL_CSV}"
                      f"（存在={POOL_CSV.exists()}）")
                print("[abort] 已写入的记录保留，修复后重跑本脚本会自动续跑。")
                return 2
            # 池取三者并集：漏掉任一来源都会在底表上留洞——
            #   MAJORS            固定主流池
            #   collected[ts]     采集器当帧实际扫过的（含当时报 error 的，如 HYPE）
            #   pool              CSV 还原的当刻 top3（采集器实时拉榜，与 CSV 快照时刻
            #                     不同，实测 34 帧里 3 帧两边不一致）
            symbols = list(dict.fromkeys(
                list(MAJORS) + sorted(collected.get(ts, set())) + list(pool)))
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
                rec["frame_kind"] = kinds[ts]
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()
                if "state" in rec:
                    ok += 1
                    n_rec += 1
                else:
                    n_err += 1
            print(f"  [{i}/{len(frames)}] {ts} [{kinds[ts]}] 池 {len(symbols)} 币"
                  f" → 成功 {ok} | 累计记录 {n_rec} 错误 {n_err}")
    print(f"[rebuild] 完成：写入 {n_rec} 条，错误 {n_err} 条 → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
