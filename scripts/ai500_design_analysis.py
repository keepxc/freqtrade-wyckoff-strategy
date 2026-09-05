#!/usr/bin/env python3
"""
【探索性——非结论，禁止引用为证据】
AI500 去掉 OI 闸门后的策略设计分析
==================================
前置：AI500_GATE_CALIBRATION_FINDINGS.md 已确定
  - OI 闸门无法自证有益 -> 用户决定移除
  - 「瞬时>3 但中位<=3」那批 -0.798%/4h（CI 排除零）-> 需加瞬时 rank 条件

本脚本回答**设计**问题，不是参数标定：
  Q1 持有多久？        30m ~ 12h 逐档看收益/中位/胜率如何演化
  Q2 该不该追高？      按信号发生前 1h/4h 已有涨幅分桶（AKE/HEMI 都是大涨之后
                       才上榜 —— 这是「继续」还是「见顶」？）
  Q3 该不该止损/止盈？ 1m 收盘逐分钟走路径。已知分布右偏（均值正、中位负），
                       止损砍左尾、移动止损砍右尾，哪个占优要用数据定
  Q4 容量够不够？      去掉 OI 后每天多少信号？超 max_open_trades 时按什么排序

口径与 ai500_gate_calibration.py 一致（同源铁律、按日区块自举、0.1% 往返费）。
"""
import os
import sys

import numpy as np
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
from ai500_gate_calibration import day_block_boot  # noqa: E402

LOCAL = os.path.join(_ROOT, "user_data", "ai500_local")
KL = os.path.join(LOCAL, "klines_hist")
FEE = 0.001
D0, D1 = "2026-08-26", "2026-09-02"

_c = {}


def bars(sym):
    if sym not in _c:
        p = os.path.join(KL, sym + "-1m.feather")
        if not os.path.exists(p):
            _c[sym] = None
        else:
            d = pd.read_feather(p)
            _c[sym] = ((d.t.astype("int64") // 10 ** 9).to_numpy(),
                       d.close.to_numpy(float))
    return _c[sym]


def px(sym, ts):
    v = bars(sym)
    if v is None:
        return np.nan
    t, c = v
    i = np.searchsorted(t, ts - 60, side="right") - 1
    return c[i] if (i >= 0 and ts - 60 - t[i] <= 600) else np.nan


def path(sym, ts, horizon_s):
    """entry 后 (ts, ts+horizon] 的 1m 收盘序列。"""
    v = bars(sym)
    if v is None:
        return None
    t, c = v
    i0 = np.searchsorted(t, ts - 60, side="right")
    i1 = np.searchsorted(t, ts + horizon_s - 60, side="right")
    return c[i0:i1] if i1 > i0 else None


def rep(lab, days, vals, per_day=None, boot=2000):
    v = np.asarray(vals, float)
    m = np.isfinite(v)
    if m.sum() < 20:
        print("%-26s %6d  [样本不足]" % (lab, int(m.sum())))
        return
    bs = day_block_boot(np.asarray(days)[m], v[m], boot)
    lo, hi = np.percentile(bs, [5, 95])
    mde = (1.96 + 0.8416) * bs.std() * 100
    flag = "  <<<" if (lo > 0 or hi < 0) else ""
    pdc = per_day if per_day is not None else m.sum() / 8
    print("%-26s %6d %6.1f %9.4f %9.4f %7.1f  [%+8.4f,%+8.4f] %7.4f%s" % (
        lab, int(m.sum()), pdc, v[m].mean() * 100, np.median(v[m]) * 100,
        (v[m] > 0).mean() * 100, lo * 100, hi * 100, mde, flag))


HDR = "%-26s %6s %6s %9s %9s %7s %20s %7s" % (
    "", "n", "/天", "均值%", "中位%", "胜率%", "90%CI", "MDE")


def main():
    b = pd.read_csv(os.path.join(LOCAL, "ai500_netflow_hist_v2.csv.gz"),
                    low_memory=False)
    b["ts"] = pd.to_datetime(b.ts_utc, utc=True)
    b["sec"] = b.ts.astype("int64") // 10 ** 9
    b["day"] = b.ts.dt.strftime("%Y-%m-%d")
    b = b[(b.day >= D0) & (b.day <= D1)].copy()
    b["slot"] = (b.sec // 1800 + 1) * 1800
    b = b.sort_values("sec")
    st = b.groupby(["symbol", "slot"], as_index=False).last()
    st = st[st.slot - st.sec <= 900].copy()
    st["day"] = pd.to_datetime(st.slot, unit="s", utc=True).dt.strftime("%Y-%m-%d")
    st["inst"] = st.netflow_rank.fillna(99.0)
    st = st.dropna(subset=["median_rank"])

    HS = [0.5, 1, 2, 3, 4, 6, 8, 12]
    p0 = np.array([px(s, int(x)) for s, x in zip(st.symbol, st.slot)])
    st["p0"] = p0
    for h in HS:
        p1 = np.array([px(s, int(x) + int(h * 3600))
                       for s, x in zip(st.symbol, st.slot)])
        st["h%g" % h] = np.where((p0 > 0) & np.isfinite(p1),
                                 p1 / p0 - 1 - FEE, np.nan)
    for w, lab in ((3600, "pre1h"), (14400, "pre4h")):
        pm = np.array([px(s, int(x) - w) for s, x in zip(st.symbol, st.slot)])
        st[lab] = np.where((pm > 0) & (p0 > 0), p0 / pm - 1, np.nan)
    st = st[st["h4"].notna()].copy()
    for h in HS:
        st["x%g" % h] = st["h%g" % h] - st.groupby("slot")["h%g" % h].transform("mean")

    base = (st.inst <= 3) & (st.median_rank <= 3)
    g = st[base].copy()
    print("全评估点 n=%d  |  新闸门集(瞬时<=3 & 中位<=3, 无OI) n=%d = %.1f/天  %d 标的"
          % (len(st), len(g), len(g) / 8, g.symbol.nunique()))

    print("\n" + "=" * 100 + "\nQ1 持有时长（新闸门集）\n" + "=" * 100 + "\n" + HDR)
    for h in HS:
        rep("  中性 持有 %gh" % h, g.day, g["x%g" % h])
    print()
    for h in HS:
        rep("  原始 持有 %gh" % h, g.day, g["h%g" % h])

    print("\n" + "=" * 100 + "\nQ2 入场前已有涨幅 vs 之后 4h 收益（市场中性）\n"
          + "=" * 100)
    for col, lab in (("pre1h", "前1h"), ("pre4h", "前4h")):
        print("\n  按 %s 涨幅分桶：\n%s" % (lab, HDR))
        for lo, hi in [(-99, 0), (0, .02), (.02, .05), (.05, .10),
                       (.10, .20), (.20, 99)]:
            s = g[(g[col] > lo) & (g[col] <= hi)]
            rep("  %s (%g,%g]" % (lab, lo, hi), s.day, s.x4)
        v = g[[col, "x4"]].dropna()
        if len(v) > 30:
            from scipy.stats import spearmanr
            r, p = spearmanr(v[col], v.x4)
            print("    Spearman rho = %+.4f  (p=%.3f, n=%d)" % (r, p, len(v)))

    print("\n" + "=" * 100 + "\nQ3 路径模拟：止损/止盈/移动止损（4h 上限，1m 收盘逐分钟）\n"
          + "=" * 100)
    print("  注：klines_hist 只有 1m 收盘、无 high/low —— 止损按收盘判定，"
          "实盘触发更早，故本表对止损方案偏乐观。")
    H4 = 4 * 3600
    paths = [path(s, int(t), H4) for s, t in zip(g.symbol, g.slot)]
    ent = g.p0.to_numpy()
    days = g.day.to_numpy()

    def sim(sl=None, tp=None, trail=None):
        out = np.full(len(paths), np.nan)
        for i, (pa, e) in enumerate(zip(paths, ent)):
            if pa is None or len(pa) == 0 or not e > 0:
                continue
            r = pa / e - 1.0
            peak, hit = -9e9, None
            for x in r:
                peak = max(peak, x)
                if sl is not None and x <= -sl:
                    hit = -sl
                    break
                if trail is not None and peak > 0 and x <= peak - trail:
                    hit = peak - trail
                    break
                if tp is not None and x >= tp:
                    hit = tp
                    break
            out[i] = (hit if hit is not None else r[-1]) - FEE
        return out

    print("\n" + HDR)
    rep("  纯持有 4h", days, sim())
    for sl in (0.02, 0.03, 0.05, 0.08):
        rep("  止损 %.0f%%" % (sl * 100), days, sim(sl=sl))
    for tp in (0.02, 0.05, 0.10):
        rep("  止盈 %.0f%%" % (tp * 100), days, sim(tp=tp))
    for tr in (0.02, 0.03, 0.05):
        rep("  移动止损 %.0f%%" % (tr * 100), days, sim(trail=tr))
    rep("  止损3%+止盈10%", days, sim(sl=0.03, tp=0.10))

    r4 = g["h4"].dropna().to_numpy()
    ps = [1, 5, 10, 25, 50, 75, 90, 95, 99]
    q = np.percentile(r4, ps) * 100
    print("\n  4h 原始收益分位(%): " + "  ".join(
        "p%d=%+.2f" % (p, v) for p, v in zip(ps, q)))
    srt = np.sort(r4)[::-1]
    print("  合计 %+.1f%%  |  前1%%贡献 %+.1f%%  |  前5%%贡献 %+.1f%%" % (
        r4.sum() * 100, srt[:max(1, len(srt) // 100)].sum() * 100,
        srt[:max(1, len(srt) // 20)].sum() * 100))

    print("\n" + "=" * 100 + "\nQ4 容量：每天 %.0f 个信号，排序取前 N\n" % (len(g) / 8)
          + "=" * 100)
    per = g.groupby("slot").size()
    print("  每个 30m 边界并发信号数: 中位 %.0f  p90 %.0f  最大 %d" % (
        per.median(), per.quantile(.9), per.max()))
    print("\n" + HDR)
    for col, asc, lab in (("inst", True, "瞬时rank最小"),
                          ("netflow_amount", False, "净流入最大"),
                          ("pre1h", True, "前1h涨幅最小"),
                          ("pre1h", False, "前1h涨幅最大")):
        if col not in g:
            continue
        for topn in (1, 3):
            s = (g.dropna(subset=[col]).sort_values(col, ascending=asc)
                 .groupby("slot").head(topn))
            rep("  %s top%d" % (lab, topn), s.day, s.x4)

    out = os.path.join(LOCAL, "design_analysis.csv")
    g.to_csv(out, index=False)
    print("\n明细 -> " + out)
    print("\n[!] 样本内探索，8 天单窗口。任何采纳需 09-03 之后的 OOS。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
