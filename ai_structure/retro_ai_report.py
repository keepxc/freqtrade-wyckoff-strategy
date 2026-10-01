# -*- coding: utf-8 -*-
"""retro_ai.py 产物的汇总。判定规则见 RETRO_AI_LEG_PREREG_20261001.md §3（已冻结）。

用法： .venv/bin/python retro_ai_report.py
"""
import csv
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)

import config as C          # noqa: E402
import swing_watch as S     # noqa: E402
import retro_ai as R        # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

GATE = 0.55                 # 预注册 §3：CI 上界门槛
MIN_DIR = 30                # 预注册 §3：给方向点下限
SEGS = (("大涨 2020-10 → 2021-11", "2020-10-01", "2021-11-30"),
        ("下跌 2025-10 → 2026-07", "2025-10-01", "2026-07-31"))


def load(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def hit(rs):
    d = [r for r in rs if r["ai_dir_7"] in ("多", "空") and r["actual7"]]
    k = sum(1 for r in d if r["ai_dir_7"] == r["actual7"])
    return k, len(d)


def pct(k, n):
    return f"{k}/{n} = {k / n * 100:.1f}%" if n else "—"


def main():
    masked = [r for r in load(R.out_csv("masked")) if r["status"] == "ok"]
    raw = [r for r in load(R.out_csv("raw")) if r["status"] == "ok"]
    allm = load(R.out_csv("masked"))
    print(f"遮蔽版 成功 {len(masked)}/{len(allm)} 行   原样版 成功 {len(raw)} 行")
    if not masked:
        return 1

    print("\n================ ① 主判据（遮蔽版，预注册 §3）================")
    k, n = hit(masked)
    if n < MIN_DIR:
        verdict = f"不可判定（给方向点 {n} < {MIN_DIR}）"
        print(f"  命中 {pct(k, n)}")
    else:
        lo, hi = S.wilson(k, n)
        print(f"  命中 {pct(k, n)}   Wilson95 CI [{lo * 100:.1f}%, {hi * 100:.1f}%]   门槛：上界 < {GATE:.0%}")
        verdict = ("证伪：AI 腿不携带方向信息" if hi < GATE
                   else "未证伪（≠ 有效；见预注册 §3 的两条理由）")
    if len(masked) < len(allm) or len(allm) < len(R.grid_points(R.load_rows(C.CSV))):
        verdict += "   ⚠ 尚未跑完全部准入点，此为中间读数"
    print(f"  判定：{verdict}")

    print("\n================ ② 描述项（不参与判定）================")
    c = Counter(r["ai_dir_7"] for r in masked)
    print(f"  7d 方向分布：多 {c['多']}  空 {c['空']}  中性 {c['中性']}   "
          f"给方向比例 {(c['多'] + c['空']) / len(masked) * 100:.0f}%")
    for d in ("多", "空"):
        rs = [r for r in masked if r["ai_dir_7"] == d and r["actual7"]]
        kk = sum(1 for r in rs if r["actual7"] == d)
        print(f"  说「{d}」时命中 {pct(kk, len(rs))}")
    up = lambda rs: (sum(1 for r in rs if r["actual7"] == "多"), len(rs))  # noqa: E731
    u_l, n_l = up([r for r in masked if r["ai_dir_7"] == "多"])
    u_s, n_s = up([r for r in masked if r["ai_dir_7"] == "空"])
    if n_l and n_s:
        print(f"  区分度 P(涨|多) − P(涨|空) = {u_l / n_l * 100:.1f}% − {u_s / n_s * 100:.1f}%"
              f" = {(u_l / n_l - u_s / n_s) * 100:+.1f} 个百分点")
    ua, na = up([r for r in masked if r["actual7"]])
    dirs = [r for r in masked if r["ai_dir_7"] in ("多", "空") and r["actual7"]]
    ud, nd = up(dirs)
    print(f"  「全部说多」基准：全部点 {pct(ua, na)}；AI 给方向的那些点 {pct(ud, nd)}")
    vals = [float(r["ai_p_up_7d"]) for r in masked if r["ai_p_up_7d"] not in ("", None)]
    if vals:
        print(f"  p_up_7d 均值 {sum(vals) / len(vals):.1f}（n={len(vals)}）")

    print("\n================ ③ 泄漏量（40 点配对：原样 − 遮蔽，只看量级）================")
    mb = {r["ts"]: r for r in masked}
    pairs = [(r, mb[r["ts"]]) for r in raw if r["ts"] in mb]
    if pairs:
        kr, nr = hit([p[0] for p in pairs])
        km, nm = hit([p[1] for p in pairs])
        same = sum(1 for a, b in pairs if a["ai_dir_7"] == b["ai_dir_7"])
        print(f"  配对点 {len(pairs)}   原样版命中 {pct(kr, nr)}   遮蔽版命中 {pct(km, nm)}")
        if nr and nm:
            print(f"  泄漏量 ≈ {(kr / nr - km / nm) * 100:+.1f} 个百分点   两版 7d 方向相同 {same}/{len(pairs)}")
    else:
        print("  （原样版尚未跑）")

    print("\n================ ④ 与程序腿同点对照 ================")
    prog = {r["ts"]: r for r in load(os.path.join(C.OUT_DIR, "prog_walkforward.csv"))}
    both = [(m, prog[m["ts"]]) for m in masked if m["ts"] in prog]
    if both:
        agree = [(m, p) for m, p in both if m["ai_dir_7"] in ("多", "空") and m["ai_dir_7"] == p["prog7"]]
        ka = sum(1 for m, _ in agree if m["actual7"] == m["ai_dir_7"])
        print(f"  同点 {len(both)}   AI 与程序同向（非中性）{len(agree)} 次，命中 {pct(ka, len(agree))}")
    else:
        print("  （无 out/prog_walkforward.csv，先跑 walkforward_prog.py）")

    for name, a, b in SEGS:
        seg = [r for r in masked if a <= r["T"][:10] <= b]
        if not seg:
            continue
        ks, ns = hit(seg)
        cs = Counter(r["ai_dir_7"] for r in seg)
        print(f"\n================ ⑤ {name}（{len(seg)} 点）================")
        print(f"  AI 7d：多 {cs['多']} 空 {cs['空']} 中性 {cs['中性']}   命中 {pct(ks, ns)}   "
              f"实际涨 {sum(1 for r in seg if r['actual7'] == '多')} / 跌 {sum(1 for r in seg if r['actual7'] == '空')}")
        print("  日期        收盘       AI7d  p_up  实际7d  模型")
        for r in seg:
            print(f"  {r['T'][:10]}  {float(r['close']):>9,.0f}  {r['ai_dir_7']:4s}  "
                  f"{r['ai_p_up_7d']:>4}  {r['actual7']:4s}   {r['model'].split('/')[-1]}")

    print("\n================ ⑥ 调用质量 ================")
    print(f"  首个真实作答合规率：{pct(sum(1 for r in masked if r['first_resp_ok'] == '1'), sum(1 for r in masked if r['first_resp_ok'] in ('0', '1')))}")
    print(f"  交付模型：{dict(Counter(r['model'] for r in masked))}")
    if os.path.exists(R.ATTEMPTS):
        notes = Counter()
        for line in open(R.ATTEMPTS, encoding="utf-8"):
            a = json.loads(line)
            notes[(a.get("model", "").split("/")[-1], a.get("note"))] += 1
        for (m, note), cnt in sorted(notes.items()):
            print(f"  {m:40s} {note:7s} {cnt}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
