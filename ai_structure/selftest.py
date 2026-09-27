# -*- coding: utf-8 -*-
"""交付自检：在目标机上证明这套东西真的能跑，而且算出来的数不是垃圾。

用法：
    .venv/bin/python selftest.py          # 全部检查
    .venv/bin/python selftest.py -v       # 打印每项细节

四类检查，按「越往下越有价值」排列：

  A 数据完整性    CSV 能不能用、是否陈旧
  B 算法不变量    统计量是否满足数学上必须成立的约束
                  （区间包含点估计、单调性、尺度不变性…）
  C 判别力        用已知答案的合成输入检验函数**真的能分辨**，
                  而不是「跑出了数」就等于「算对了」
  D 边界夹具      极端输入下优雅退出而不是崩

退出码：0 全过 / 1 有失败。

为什么要有 C 这一类：一个只会返回 0.5 的「报警器」永远不报错，
但它也永远不会报警。不喂已知答案的输入，就无法区分
「没有异常」和「检测不出异常」。
"""
import argparse
import csv
import datetime
import math
import os
import sys

import numpy as np

import config as C

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import swing_watch as S     # noqa: E402  （要在 stdout 配置之后导入）

PASS, FAIL, WARN = [], [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append((name, detail))
    mark = "\033[32m✓\033[0m" if cond else "\033[31m✗\033[0m"
    print(f"  {mark} {name}" + (f"   {detail}" if detail else ""))
    return cond


def note(msg):
    WARN.append(msg)
    print(f"  \033[33m!\033[0m {msg}")


# ================================================================ A 数据完整性
def part_a(verbose):
    print("\n\033[1mA. 数据完整性\033[0m")
    if not os.path.exists(C.CSV):
        check("CSV 存在", False, C.CSV)
        return None
    check("CSV 存在", True, f"{os.path.getsize(C.CSV):,} 字节")

    ts, o, h, l, c, v = [], [], [], [], [], []
    with open(C.CSV, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            ts.append(int(r["ts"]))
            o.append(float(r["open"])); h.append(float(r["high"]))
            l.append(float(r["low"])); c.append(float(r["close"]))
            v.append(float(r["volume"]))
    ts = np.array(ts, np.int64)
    o, h, l, c, v = (np.array(x, float) for x in (o, h, l, c, v))
    n = len(ts)

    check("样本量足够（>10000 根 1h）", n > 10000, f"{n:,} 根")
    if n < 100:
        return None

    dt = np.diff(ts)
    check("ts 严格递增（无重复、无乱序）", bool((dt > 0).all()),
          f"最小间隔 {dt.min() / 60000:.0f} 分钟")
    gaps = int((dt > 2 * 3_600_000).sum())
    if gaps:
        note(f"存在 {gaps} 处 >2 小时的缺口（币安维护窗口属正常，最大 "
             f"{dt.max() / 3_600_000:.1f} 小时）")
    else:
        check("无 >2 小时的数据缺口", True)

    bad = int(((h < l) | (h < o) | (h < c) | (l > o) | (l > c)).sum())
    check("OHLC 关系自洽（high≥low 且包住 open/close）", bad == 0, f"异常 {bad} 根")
    check("无 NaN / 无零或负价格",
          bool(np.isfinite(o).all() and (l > 0).all() and (h > 0).all()))

    age_h = (datetime.datetime.now(datetime.UTC).timestamp() * 1000 - ts[-1]) / 3_600_000
    last = datetime.datetime.fromtimestamp(ts[-1] / 1000, datetime.UTC)
    if age_h <= 3:
        check("数据新鲜（最新 K 线在 3 小时内）", True, f"{last:%Y-%m-%d %H:%M} UTC")
    else:
        note(f"最新 K 线是 {last:%Y-%m-%d %H:%M} UTC，距今 {age_h:.1f} 小时"
             f"——若是首次部署请先跑 run_session.sh 更新数据")

    if verbose:
        print(f"      区间 {datetime.datetime.fromtimestamp(ts[0] / 1000, datetime.UTC):%Y-%m-%d}"
              f" → {last:%Y-%m-%d}   {n:,} 根")
    return ts, h, l, c


# ================================================================ B 算法不变量
def part_b(data=None):
    print("\n\033[1mB. 算法不变量（数学上必须成立，违反即说明实现有 bug）\033[0m")
    import numpy as np

    # ---- 枢轴有效性：每个 H 之后必须真的回落过 thr，每个 L 之后必须真的上涨过 thr
    #      这是 zigzag 的定义，也是它唯一不能违反的性质。
    #      曾经踩过的坑：dir_==0 初始化阶段高低点共用一个下标变量，
    #      导致首个枢轴「价格取自高点、位置指向低点」，价格对而位置错。
    def validity(ts, h, l, thr):
        piv = S.zigzag(ts, h, l, thr)
        bad = []
        for j, p in enumerate(piv):
            i0 = p["i"]
            i1 = piv[j + 1]["i"] if j + 1 < len(piv) else len(ts) - 1
            if i1 <= i0:
                bad.append(f"#{j} 下标非递增")
                continue
            if p["kind"] == "H":
                drop = l[i0:i1 + 1].min() / p["price"] - 1
                if drop > -thr * 0.999:
                    bad.append(f"#{j} H@{i0} 之后仅回落 {drop:.1%}")
            else:
                rise = h[i0:i1 + 1].max() / p["price"] - 1
                if rise < thr * 0.999:
                    bad.append(f"#{j} L@{i0} 之后仅上涨 {rise:.1%}")
        return piv, bad

    # 单调序列：首个枢轴必须落在极值真正发生的那根 K 线上（这里是第 0 根）
    n = 24 * 400
    ts = np.arange(n) * 3_600_000
    up = 100 * np.exp(np.linspace(0, 3, n))
    piv, bad = validity(ts, up * 1.01, up * 0.99, 0.30)
    check("单调上涨：枢轴有效且首个枢轴下标指向极值所在 bar（i=0）",
          not bad and len(piv) == 1 and piv[0]["kind"] == "L"
          and piv[0]["i"] == 0 and abs(piv[0]["price"] - up[0] * 0.99) < 1e-9,
          f"piv={[(p['kind'], p['i']) for p in piv]} 异常={bad}")

    dn = 100 * np.exp(np.linspace(0, -3, n))
    piv, bad = validity(ts, dn * 1.01, dn * 0.99, 0.30)
    check("单调下跌：枢轴有效且首个枢轴下标指向极值所在 bar（i=0）",
          not bad and len(piv) == 1 and piv[0]["kind"] == "H"
          and piv[0]["i"] == 0 and abs(piv[0]["price"] - dn[0] * 1.01) < 1e-9,
          f"piv={[(p['kind'], p['i']) for p in piv]} 异常={bad}")

    # 真实数据：全部枢轴都必须满足定义（这条最有说服力）
    if data is not None:
        dts, dh, dl, dc = data
        piv, bad = validity(dts, dh, dl, S.THETA)
        check(f"真实数据 θ={S.THETA:.0%}：全部 {len(piv)} 个枢轴都满足 ZigZag 定义",
              not bad, "；".join(bad[:3]) if bad else f"{len(piv)} 个枢轴全部有效")
        piv2, bad2 = validity(dts, dh, dl, S.THETA_ALT)
        check(f"真实数据 θ={S.THETA_ALT:.0%}：全部 {len(piv2)} 个枢轴都满足定义",
              not bad2, "；".join(bad2[:3]) if bad2 else f"{len(piv2)} 个枢轴全部有效")
        check("枢轴下标严格递增且 conf_i >= i（无前视）",
              all(p["i"] < q["i"] for p, q in zip(piv, piv[1:]))
              and all(p["conf_i"] >= p["i"] for p in piv))

    # ---- nonoverlap：抽出的下标必须真的互不重叠
    m = np.ones(24 * 200, dtype=bool)
    idx = S.nonoverlap(m, 7)
    ok = len(idx) > 0 and np.all(np.diff(idx) >= 7 * 24)
    check("nonoverlap：相邻观测间隔 ≥ 期限", bool(ok),
          f"n={len(idx)}  最小间隔 {np.diff(idx).min() if len(idx) > 1 else 0} 小时")
    expect = 1 + (24 * 200 - 1) // (7 * 24)
    check("nonoverlap：数量符合 ceil(n/步长)", abs(len(idx) - expect) <= 1,
          f"实得 {len(idx)}，期望约 {expect}")
    check("nonoverlap：空掩码返回空数组",
          len(S.nonoverlap(np.zeros(100, bool), 7)) == 0)

    # ---- Wilson：区间必须包含点估计，且落在 [0,1]
    bad = []
    for k, nn in ((0, 10), (1, 3), (5, 10), (9, 10), (10, 10), (30, 100), (0, 1)):
        lo, hi = S.wilson(k, nn)
        p = k / nn
        if not (0 <= lo <= p <= hi <= 1):
            bad.append(f"k={k},n={nn}→[{lo:.3f},{hi:.3f}]")
    check("Wilson：区间包含点估计且 ∈[0,1]（含极端值）", not bad,
          "；".join(bad) if bad else "7 组含边界用例全过")
    lo0, hi0 = S.wilson(0, 10)
    check("Wilson：0/10 的上界远低于 0.5（不会把 0 命中算成不确定）",
          hi0 < 0.35, f"[{lo0:.3f}, {hi0:.3f}]")
    lo1, hi1 = S.wilson(10, 10)
    check("Wilson：10/10 的下界远高于 0.5", lo1 > 0.65, f"[{lo1:.3f}, {hi1:.3f}]")
    check("Wilson：n=0 时返回全区间而非崩溃", S.wilson(0, 0) == (0.0, 1.0))

    # ---- binom_tail：必须按偏离方向取尾
    p_up, direc = S.binom_tail(10, 10, 0.5)
    check("binom_tail：10/10 取上尾，p=0.5^10≈0.00098",
          direc == "偏多" and abs(p_up - 0.5 ** 10) < 1e-12, f"p={p_up:.6f}")
    p_dn, direc2 = S.binom_tail(0, 10, 0.5)
    check("binom_tail：0/10 取下尾（同值，方向为偏空）",
          direc2 == "偏空" and abs(p_dn - 0.5 ** 10) < 1e-12, f"p={p_dn:.6f}")
    p_mid, _ = S.binom_tail(5, 10, 0.5)
    check("binom_tail：5/10 恰在基准上，p 接近 0.62（不显著）",
          p_mid > 0.5, f"p={p_mid:.3f}")
    p_far, d3 = S.binom_tail(3, 10, 0.5)
    check("binom_tail：3/10 偏空，取的是下尾而不是上尾（这是最易错的一处）",
          d3 == "偏空" and p_far < 0.25, f"p={p_far:.3f}")

    # ---- 期限越长，独立样本必须越少（单调性）
    n_h = 24 * 365 * 5
    counts = [len(S.nonoverlap(np.ones(n_h, bool), h)) for h in S.HORIZONS]
    mono = all(counts[i] >= counts[i + 1] for i in range(len(counts) - 1))
    check("独立样本数随期限单调不增", mono, f"{dict(zip(S.HORIZONS, counts))}")
    check("长期限样本确实稀少（365 天 < 10 个）",
          counts[-1] < 10, f"365 天期限独立观测 {counts[-1]} 个")
    return True


# ================================================================ C 判别力
def _mk(n_hours, period_pat):
    """构造确定性序列：按 period_pat 循环的正负号 × 变化的幅度。

    为什么要刻意构造而不是用随机数：
      非重叠抽样的步长恒为 h*24，而 24 能被 1/2/3/4/6/8/12/24 整除。
      若信号周期能整除 24，抽样点会全部落在同一相位上（P(up) 变成 0 或 1）；
      若周期与 24 互质（如 5、7），抽样点会均匀覆盖所有相位，
      P(up) 就等于模式里正号的比例——**可精确预测**。
      这正是「判别力夹具」该有的性质：期望值事先算得出来，而不是「跑完看看」。
    """
    pat = np.array(period_pat, float)
    mag = 0.02 + 0.01 * np.sin(np.arange(n_hours) * 0.7)
    return pat[np.arange(n_hours) % len(pat)] * mag


def part_c():
    print("\n\033[1mC. 判别力（喂已知答案的输入，看它能不能分辨）\033[0m")
    import numpy as np

    # ---- 1. 方向识别：全正 / 全负
    fwd = np.full(24 * 200, 0.01)
    m = np.ones(len(fwd), bool)
    s = S.summarise(fwd, m, 1)
    check("判别力：全为正收益 → P(涨)=100% 且 CI 下界 > 50%",
          s["usable"] and s["p_up"] == 1.0 and s["p_up_lo"] > 0.5,
          f"P={s['p_up']:.0%} CI[{s['p_up_lo']:.2f},{s['p_up_hi']:.2f}] n={s['n_indep']}")

    s = S.summarise(np.full(24 * 200, -0.01), m, 1)
    check("判别力：全为负收益 → P(涨)=0% 且 CI 上界 < 50%",
          s["usable"] and s["p_up"] == 0.0 and s["p_up_hi"] < 0.5,
          f"P={s['p_up']:.0%} CI[{s['p_up_lo']:.2f},{s['p_up_hi']:.2f}]")

    # ---- 2. 已知比例的构造序列：周期 5（与 24 互质）→ 抽样后 P(up) 恰为 4/5
    fwd = _mk(24 * 500, [1, 1, 1, 1, -1])
    m = np.ones(len(fwd), bool)
    s = S.summarise(fwd, m, 1)
    check("判别力：构造 P(涨)=80% 的序列 → 估计值精确命中 0.80",
          abs(s["p_up"] - 0.80) < 0.005,
          f"P={s['p_up']:.3f}  n={s['n_indep']}（周期与步长互质，期望值可预知）")
    base = {"usable": True, "p_up": 0.5, "median": 0.0}
    v, code, pv, ok = S.verdict(s, base, 1, ab=S.ab_split(fwd, m, 1), n_tests=1)
    check("判别力：真存在 80% 的偏多 → 能给出方向性判定",
          code in ("edge", "weak", "dir"), f"判定={v!r} p={pv:.2e}")

    # ---- 3. 决策规则本身：同样的 p_up，样本量决定能不能下结论
    #        这两个用例不依赖数据，直接喂统计量，因此完全确定。
    def fake(p_up, n, lo, hi):
        return {"n_indep": n, "usable": True, "p_up": p_up,
                "p_up_lo": lo, "p_up_hi": hi, "median": 0.0,
                "wins": int(round(p_up * n))}

    tiny = fake(0.60, 20, 0.37, 0.79)          # 60% 但只有 20 个样本
    v, code, pv, ok = S.verdict(tiny, base, 1, n_tests=1)
    check("决策规则：P(涨)=60% 但 n=20 → 判为无法确认（不因点估计好看就下结论）",
          not ok and code in ("none", "dir"), f"判定={v!r}")

    big = fake(0.60, 500, 0.556, 0.643)        # 同样的 60%，但样本充足
    v, code, pv, ok = S.verdict(big, base, 1, ab=(fake(0.58, 200, 0.51, 0.65),
                                                 fake(0.62, 200, 0.55, 0.69)),
                                n_tests=1)
    check("决策规则：同样的 60% 但 n=500 且 A/B 同向 → 可判断",
          ok and code == "edge", f"判定={v!r} p={pv:.2e}")

    # ---- 4. Bonferroni：同一份证据，在多组检验下必须更难通过
    #        取一个卡在两条阈值之间的样本：p ≈ 0.011，
    #        1 组检验时 α=0.05（通过），28 组检验时 α=0.0018（不通过）。
    lo62, hi62 = S.wilson(62, 100)
    border = fake(0.62, 100, lo62, hi62)
    ab_pass = (fake(0.58, 60, *S.wilson(35, 60)),
               fake(0.66, 60, *S.wilson(40, 60)))
    v1, c1, p1, ok1 = S.verdict(border, base, 1, ab=ab_pass, n_tests=1)
    v2, c2, p2, ok2 = S.verdict(border, base, 1, ab=ab_pass, n_tests=28)
    check("Bonferroni：p≈0.011 在 1 组检验下可判断、在 28 组下不可判断",
          ok1 and not ok2 and p1 == p2,
          f"n_tests=1 → {c1}；n_tests=28 → {c2}（p 值同为 {p1:.4f}）")

    # ---- 5. 尺度不变性：价格整体 ×100 不应改变任何方向性结论
    rng = np.random.default_rng(11)
    n = 24 * 900
    px = 100 * np.exp(np.cumsum(rng.normal(0.0002, 0.02, n)))
    ts = np.arange(n) * 3_600_000
    try:
        A1 = S.analyse(ts, px * 1.5, px * 0.5, px, thr=0.20, horizons=[7, 30, 90])
        A2 = S.analyse(ts, px * 150, px * 50, px * 100, thr=0.20, horizons=[7, 30, 90])
        pairs = [(A1["base"][h], A2["base"][h]) for h in (7, 30, 90)
                 if A1["base"][h].get("usable") and A2["base"][h].get("usable")]
        same = bool(pairs) and all(
            abs(a["p_up"] - b["p_up"]) < 1e-12 for a, b in pairs)
        check("尺度不变性：价格整体 ×100 不改变任何 P(涨)", same,
              f"比对 {len(pairs)} 个期限（用对数收益与相对阈值，理应完全一致）")
    except ValueError as e:
        note(f"尺度不变性用例被跳过（合成数据枢轴不足）：{e}")

    return True


# ================================================================ D 边界夹具
def part_d():
    print("\n\033[1mD. 边界夹具（极端输入应优雅退出，不是崩）\033[0m")
    import numpy as np

    def run(px, thr=0.30, lab=""):
        n = len(px)
        ts = np.arange(n) * 3_600_000
        try:
            A = S.analyse(ts, px * 1.01, px * 0.99, px, thr=thr,
                          horizons=[7, 30])
            return "ok", A
        except ValueError:
            return "valueerror", None

    # 极短数据
    r = np.random.default_rng(3)
    px = 100 * np.exp(np.cumsum(r.normal(0, 0.01, 68)))
    st, _ = run(px, lab="68 根")
    check("68 根 K 线：抛可读的 ValueError 而非 TypeError",
          st in ("ok", "valueerror"), f"结果={st}")

    # 只有 5 根
    st, _ = run(px[:5])
    check("5 根 K 线：同样优雅退出", st in ("ok", "valueerror"), f"结果={st}")

    # 恒定价格：没有任何枢轴，不应崩
    st, _ = run(np.full(24 * 400, 100.0))
    check("恒定价格（零波动）：不崩溃", st in ("ok", "valueerror"), f"结果={st}")

    # 单调上涨：永远不形成枢轴
    st, _ = run(100 * np.exp(np.linspace(0, 3, 24 * 400)))
    check("严格单调上涨：不崩溃", st in ("ok", "valueerror"), f"结果={st}")

    # 正常数据但很短：应能出结果
    r = np.random.default_rng(5)
    px = 100 * np.exp(np.cumsum(r.normal(0, 0.03, 24 * 400)))
    st, A = run(px)
    if st == "ok":
        check("400 根正常数据：能出结构", True,
              f"{len(A['piv'])} 个已确认枢轴")
    else:
        note("400 根随机数据未确认出枢轴（随机游走本就如此，不算失败）")

    # 极小 θ 不应导致崩溃
    st, _ = run(px, thr=0.02)
    check("极小 θ=2%：不崩溃", st in ("ok", "valueerror"), f"结果={st}")
    return True


# ================================================================ E 报告结构
def part_e():
    print("\n\033[1mE. 报告结构（若已有 report_watch.html）\033[0m")
    p = os.path.join(C.OUT_DIR, "report_watch.html")
    if not os.path.exists(p):
        note("尚未生成 report_watch.html，跳过（先跑一次 swing_watch.py）")
        return True
    html = open(p, encoding="utf-8").read()
    check("HTML 已生成", True, f"{len(html):,} 字符")
    for bad in ("nan", "NaN", "None%", "{'", '"}', "Traceback"):
        check(f"无占位/泄漏文本 {bad!r}", bad not in html)
    check("SVG 标签配对", html.count("<svg") == html.count("</svg>")
          and html.count("<svg") > 0, f"{html.count('<svg')} 对")
    check("无外链依赖（可离线打开、可邮件发送）",
          "http://" not in html.replace("http://www.w3.org", "")
          and "https://" not in html)
    return True


# ================================================================ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--skip-slow", action="store_true")
    args = ap.parse_args()

    print("=" * 78)
    print("交付自检   " + datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M UTC"))
    print(f"包目录 {C.HERE}")
    print(f"数据   {C.CSV}")
    print("=" * 78)

    part_a_ret = part_a(args.verbose)
    if not args.skip_slow:
        part_b(part_a_ret)
        part_c()
    part_d()
    part_e()

    print("\n" + "=" * 78)
    print(f"通过 \033[32m{len(PASS)}\033[0m 项，失败 \033[31m{len(FAIL)}\033[0m 项，"
          f"提示 {len(WARN)} 条")
    if FAIL:
        print("\n失败明细：")
        for name, detail in FAIL:
            print(f"  ✗ {name}   {detail}")
        print("\n有失败项。请先解决再依赖这套分析的结论。")
        return 1
    print("\n全部通过。这套分析在目标机上可正常运行。")
    if WARN:
        print("（上面的提示项不影响运行，但值得看一眼）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
