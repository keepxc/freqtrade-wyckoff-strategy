# -*- coding: utf-8 -*-
"""walkforward_prog.py 产物的分析：方向分布、命中率、"实际下跌时程序说了什么"。

用法： .venv/bin/python walkforward_report.py
环境变量：WF_OUT（默认 out/prog_walkforward.csv）、WF_SEG_START/WF_SEG_END（⑤ 段明细窗口）
"""
import csv
import datetime
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)

import config as C          # noqa: E402
import swing_watch as S     # noqa: E402

SRC = os.environ.get("WF_OUT", os.path.join(C.OUT_DIR, "prog_walkforward.csv"))
SEG_START = os.environ.get("WF_SEG_START", "2025-04-01")
SEG_END = os.environ.get("WF_SEG_END", "2026-06-30")


def dist(rs, key="prog7"):
    c = Counter(r[key] for r in rs if r[key])
    n = sum(c.values()) or 1
    return "  ".join(f"{k}={c.get(k, 0)}({c.get(k, 0) / n * 100:.0f}%)" for k in ("多", "中性", "空")) \
        + f"  共{sum(c.values())}"


def main():
    rows = list(csv.DictReader(open(SRC, encoding="utf-8-sig")))
    print(f"扫描点 {len(rows)}  覆盖 {rows[0]['T']} → {rows[-1]['T']}   （{SRC}）")

    ts_all, hi_all, lo_all, cl_all = S.load_data()
    imax = int(max(range(len(cl_all)), key=lambda i: cl_all[i]))
    hi_date = datetime.datetime.fromtimestamp(ts_all[imax] / 1000, datetime.UTC).strftime("%Y-%m-%d")
    imin = int(min(range(imax, len(cl_all)), key=lambda i: cl_all[i]))
    lo_date = datetime.datetime.fromtimestamp(ts_all[imin] / 1000, datetime.UTC).strftime("%Y-%m-%d")
    print(f"历史最高收盘 {cl_all[imax]:,.0f} @ {hi_date}   其后最低收盘 {cl_all[imin]:,.0f} @ {lo_date}")

    print("\n================ ① 程序 7 天方向的分布 ================")
    print("  全史      :", dist(rows))
    for y in sorted({r["T"][:4] for r in rows}):
        print(f"  {y}      :", dist([r for r in rows if r["T"][:4] == y]))

    print("\n================ ② 按「上行腿 / 下行腿」拆 ================")
    for ph in ("up", "down"):
        rs = [r for r in rows if r["phase"] == ph]
        if rs:
            print(f"  phase={ph:4s} ({len(rs):3d} 点):", dist(rs))

    print("\n================ ③ 关键问：实际下跌时程序说了什么 ================")
    for a in ("空", "多"):
        rs = [r for r in rows if r["actual7"] == a]
        if rs:
            print(f"  实际 7 天后{'下跌' if a == '空' else '上涨'}的 {len(rs):3d} 个点 →", dist(rs))

    print("\n================ ④ 命中率（只在程序给出方向时）================")
    for lbl, rs in (("全部", rows), ("phase=up", [r for r in rows if r["phase"] == "up"]),
                    ("phase=down", [r for r in rows if r["phase"] == "down"])):
        d = [r for r in rs if r["prog7"] in ("多", "空") and r["actual7"]]
        if not d:
            continue
        k = sum(1 for r in d if r["prog7"] == r["actual7"])
        lo, hi = S.wilson(k, len(d))
        print(f"  {lbl:10s} 给方向 {len(d):3d}/{len(rs):3d} 点（{len(d) / len(rs) * 100:.0f}%）"
              f"  命中 {k}/{len(d)} = {k / len(d) * 100:.1f}%"
              f"  Wilson95 CI [{lo * 100:.0f}%, {hi * 100:.0f}%]  基准 {C.FWD_BASE_RATE:.0%}")

    print(f"\n================ ⑤ 段明细 {SEG_START} → {SEG_END} ================")
    seg = [r for r in rows if SEG_START <= r["T"] <= SEG_END]
    print("  日期        收盘      phase  程序7d  实际7d")
    for r in seg:
        print(f"  {r['T'][:10]}  {r['close']:>9}  {r['phase']:5s}  {r['prog7']:4s}  {r['actual7']:4s}")
    if seg:
        print(f"\n  该窗口程序给方向 {sum(1 for r in seg if r['prog7'] in ('多', '空'))}/{len(seg)} 次"
              f"  实际上涨 {sum(1 for r in seg if r['actual7'] == '多')}"
              f"  实际下跌 {sum(1 for r in seg if r['actual7'] == '空')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
