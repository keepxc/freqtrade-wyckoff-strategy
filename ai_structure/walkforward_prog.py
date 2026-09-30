# -*- coding: utf-8 -*-
"""程序腿回溯扫描（walk-forward，决策侧无前视）。

背景：用户 2026-09-29 提议——与其等 30 周，不如回溯验证「这套判据能不能识别大涨/大跌」。
本脚本只做**程序腿**（纯数学，可无泄漏回溯）；AI 识图腿的回溯必须另做（图上有日期与
价位，模型可能是在"回忆"历史，必须做遮蔽对照），见 RETRO_PROG_LEG_20260929.md。

方法，对历史每个采样点 T：
  1) 只用 ts + 1h <= T 的 K 线（即在 T 时刻已收盘的数据；先快照 CSV，不碰运行中的文件）
  2) 重跑 swing_watch.analyse() —— 条件组统计也只来自 <= T 的历史（当时真能看到的东西）
  3) 组装与 watch_latest.json 同构的 dict，调用 forward_log.prog_direction(7/30)
  4) 与真实结果对比：兑现价 = T + h 天那根 bar 的收盘 → 涨=多 / 否则=空

性质（写进报告，别误读）：
- 决策侧无前视，但整体仍是**样本内**诊断——条件组本身就是用这段历史设计的。
  所以它只能**证伪**（连样本内都区分不了涨跌 = 信号没信息），不能**验证**。
- 不参与前向判据：不写 forward_log / dense_log / score，产物仅 out/prog_walkforward.csv。

用法：
    .venv/bin/python walkforward_prog.py                 # 全史，每 7 天（约 3.7 分钟）
    WF_START=2026-01-01 .venv/bin/python walkforward_prog.py   # 缩短区间（验证用）
环境变量：WF_START（默认 2017-09-01）、WF_STEP_DAYS（默认 7）、WF_OUT（输出路径）
"""
import bisect
import csv
import datetime
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import config as C          # noqa: E402
import swing_watch as S     # noqa: E402
import forward_log as FL    # noqa: E402

H1, H2 = C.FWD_PRIMARY_H, C.FWD_SECONDARY_H
DAY = 86_400_000
HOUR = 3_600_000
STEP_DAYS = int(os.environ.get("WF_STEP_DAYS", "7"))
START = os.environ.get("WF_START", "2017-09-01")
OUT = os.environ.get("WF_OUT", os.path.join(C.OUT_DIR, "prog_walkforward.csv"))


def main():
    snap_dir = tempfile.mkdtemp(prefix="wf_")
    snap = os.path.join(snap_dir, "BTCUSDT_1h.csv")
    shutil.copy(C.CSV, snap)          # 快照：避免与运行中的 cron 抢同一个文件
    print(f"数据快照 {snap}（{os.path.getsize(snap):,} 字节）")

    ts_all, hi_all, lo_all, cl_all = S.load_data(snap)
    ts_list = [int(x) for x in ts_all]
    print(f"全量 {len(ts_all):,} 根  最后 {datetime.datetime.fromtimestamp(ts_all[-1] / 1000, datetime.UTC)}")

    y, m, d = (int(x) for x in START.split("-"))
    grid, t = [], int(datetime.datetime(y, m, d, 0, 30, tzinfo=datetime.UTC).timestamp() * 1000)
    end = int(ts_all[-1]) - (H1 + 1) * DAY
    while t <= end:
        grid.append(t)
        t += STEP_DAYS * DAY
    print(f"采样点 {len(grid)} 个（每 {STEP_DAYS} 天，{START} 起）\n")

    rows, t0 = [], time.time()
    for i, T in enumerate(grid):
        # 只含「在 T 时刻已收盘」的 bar
        k = bisect.bisect_right(ts_list, T - HOUR)
        if k < 5000:                     # 预热不足，跳过
            continue
        ts_, hi_, lo_, cl_ = ts_all[:k], hi_all[:k], lo_all[:k], cl_all[:k]
        try:
            A = S.analyse(ts_, hi_, lo_, cl_)
        except ValueError:
            continue
        cur = A["cur"]
        w = {"as_of": datetime.datetime.fromtimestamp(ts_[k - 1] / 1000, datetime.UTC)
             .strftime("%Y-%m-%d %H:%M"),
             "last_close": float(cl_[k - 1]),
             "position": {"phase": cur["phase"], "move": float(cur["move"]),
                          "days": float(cur["days"]), "breakout": bool(cur["breakout"])},
             "base": {str(h): A["base"][h] for h in S.HORIZONS},
             "conditional": {str(h): A["cond"][h] for h in S.HORIZONS}}
        d7, audit7 = FL.prog_direction(w, H1)
        d30, _ = FL.prog_direction(w, H2)

        def realized(h):
            """真实结果（用全量数据回填，仅用于打分）。"""
            base_ts = int(ts_[k - 1])
            for jj in range(k - 1, min(len(ts_all), k - 1 + 24 * (h + 3))):
                if int(ts_all[jj]) >= base_ts + h * DAY:
                    return "多" if cl_all[jj] > cl_[k - 1] else "空"
            return ""

        rows.append({"T": w["as_of"], "ts": int(ts_[k - 1]), "close": round(float(cl_[k - 1]), 2),
                     "phase": cur["phase"], "move": round(float(cur["move"]), 3),
                     "days": round(float(cur["days"]), 1), "breakout": int(bool(cur["breakout"])),
                     "prog7": d7, "actual7": realized(H1),
                     "prog30": d30, "actual30": realized(H2), "audit7": audit7})
        if (i + 1) % 50 == 0:
            print(f"  {i + 1}/{len(grid)}  已用 {time.time() - t0:.0f}s  "
                  f"当前 {w['as_of']}  {cur['phase']}  prog7={d7}")

    if not rows:
        print("[中止] 没有采样点（区间太短或数据不足）")
        return 1
    with open(OUT, "w", encoding="utf-8-sig", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        wr.writeheader()
        wr.writerows(rows)
    print(f"\n写出 {OUT}  行数 {len(rows)}  用时 {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
