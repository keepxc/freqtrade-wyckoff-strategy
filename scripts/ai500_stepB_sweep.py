#!/usr/bin/env python3
"""
Step B 参数扫描（IS 段）+ 邻域稳健选参 + 单次 OOS
==================================================
判据见 AI500_STEPB_PREREG.md（已冻结）。本脚本严格按其执行：

  1. 只在 IS 段（2026-08-18 → 08-30，含 2h embargo）评估全部 60 组
  2. 用「邻域稳健」选出**唯一一个**候选（不取 IS 最高值）
  3. 只对该候选跑 **一次** OOS（08-31 → 09-02）

OOS 段在第 2 步完成前不参与任何计算，也不打印任何 OOS 数字。
跑完若不通过，不得回头换参数重跑——那样 OOS 就变成第 14 个训练日。
"""
import itertools
import os
import sys
import time

import numpy as np
import pandas as pd

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
DATA_DIR = os.path.join(_REPO_ROOT, "user_data", "ai500_local")
PANEL = os.path.join(DATA_DIR, "stepB_panel.npz")

GRID_MIN, FWD_MIN, COOLDOWN_MIN = 5, 120, 120
IS_END = pd.Timestamp("2026-08-31", tz="UTC")
BOOT_N, RNG_SEED = 2000, 20260903

SHORT_WINS = [5, 15, 30]
LONG_WINS = [30, 60]
K_SHORTS = [3, 5, 10]
K_LONGS = [3, 5]
CONFIRMS = [1, 2]


def dedup_cooldown(ti, si, cd):
    """同标的冷却：距上次入场不足 cd 个网格步的信号丢弃。"""
    keep = np.zeros(len(ti), dtype=bool)
    last = {}
    for k in np.argsort(ti, kind="stable"):
        s = si[k]
        if ti[k] - last.get(s, -10**9) >= cd:
            keep[k] = True
            last[s] = ti[k]
    return ti[keep], si[keep]


def main() -> int:
    t0 = time.time()
    z = np.load(PANEL, allow_pickle=True)
    ts = pd.DatetimeIndex(z["ts"].view("datetime64[ns]")).tz_localize("UTC")
    fwd = z["fwd"]
    ranks = {w: z[f"rank{w}"] for w in [5, 15, 30, 60]}
    cd = COOLDOWN_MIN // GRID_MIN
    day = ts.date
    # embargo：前瞻窗口跨越 IS/OOS 边界的事件从 IS 剔除
    is_mask = (ts + pd.Timedelta(minutes=FWD_MIN)) <= IS_END
    oos_mask = ts >= IS_END
    print(f"[info] 网格 {len(ts):,} | IS 时点 {is_mask.sum():,} "
          f"| OOS 时点 {oos_mask.sum():,}", flush=True)

    def events(sw, lw, ks, kl, cf):
        cond = (ranks[lw] <= kl) & (ranks[sw] <= ks)
        if cf == 2:
            prev = np.zeros_like(cond)
            prev[1:] = cond[:-1]
            cond = cond & prev
        ti, si = np.nonzero(cond)
        ok = np.isfinite(fwd[ti, si])
        ti, si = dedup_cooldown(ti[ok], si[ok], cd)
        return ti, si

    def stats(ti, si, mask):
        m = mask[ti]
        t2, s2 = ti[m], si[m]
        return t2, s2, fwd[t2, s2]

    # ---------- 基线 ----------
    bti, bsi = events(60, 60, 3, 3, 1)   # 退化为 1h rank<=3
    bt, bs, bret = stats(bti, bsi, is_mask)
    bday = day[bt]
    print(f"[info] 基线 IS 事件 {len(bret):,} 均值 {bret.mean()*100:+.3f}%",
          flush=True)

    combos = [c for c in itertools.product(SHORT_WINS, LONG_WINS, K_SHORTS,
                                           K_LONGS, CONFIRMS) if c[0] != c[1]]
    print(f"[info] 有效组合 {len(combos)}", flush=True)

    rng = np.random.default_rng(RNG_SEED)
    days = np.unique(bday)
    b_by_day = [bret[bday == d] for d in days]
    picks = rng.integers(0, len(days), (BOOT_N, len(days)))
    b_boot = np.array([np.concatenate([b_by_day[i] for i in p]).mean()
                       for p in picks])

    rows = []
    for sw, lw, ks, kl, cf in combos:
        ti, si = events(sw, lw, ks, kl, cf)
        t2, s2, ret = stats(ti, si, is_mask)
        if len(ret) < 20:
            rows.append(dict(sw=sw, lw=lw, ks=ks, kl=kl, cf=cf, n=len(ret),
                             mean=np.nan, win=np.nan, delta=np.nan,
                             d_lo=np.nan, d_hi=np.nan))
            continue
        d2 = day[t2]
        v_by_day = {d: ret[d2 == d] for d in np.unique(d2)}
        # 与基线共用同一次日抽样 → 配对，抵消共同市场波动
        v_boot = np.empty(BOOT_N)
        for b, p in enumerate(picks):
            parts = [v_by_day[days[i]] for i in p if days[i] in v_by_day]
            v_boot[b] = np.concatenate(parts).mean() if parts else np.nan
        diff = v_boot - b_boot
        lo, hi = np.nanpercentile(diff, [5, 95])
        rows.append(dict(sw=sw, lw=lw, ks=ks, kl=kl, cf=cf, n=len(ret),
                         mean=ret.mean(), win=(ret > 0).mean(),
                         delta=ret.mean() - bret.mean(), d_lo=lo, d_hi=hi))

    r = pd.DataFrame(rows)
    r.to_csv(os.path.join(DATA_DIR, "stepB_sweep_is.csv"), index=False)

    print("\n" + "=" * 92)
    print("IS 段扫描结果（按 IS 均值排序，前 15）")
    print("=" * 92)
    show = r.dropna(subset=["mean"]).sort_values("mean", ascending=False)
    disp = show.head(15).copy()
    for c in ["mean", "delta", "d_lo", "d_hi"]:
        disp[c] = (disp[c] * 100).round(3)
    disp["win"] = (disp["win"] * 100).round(1)
    print(disp.to_string(index=False))

    # ---------- 邻域稳健选参 ----------
    key = {(x.sw, x.lw, x.ks, x.kl, x.cf): x.mean for x in r.itertuples()}

    def neighbors(c):
        sw, lw, ks, kl, cf = c
        out = []
        for lst, val, pos in [(SHORT_WINS, sw, 0), (LONG_WINS, lw, 1),
                              (K_SHORTS, ks, 2), (K_LONGS, kl, 3),
                              (CONFIRMS, cf, 4)]:
            i = lst.index(val)
            for j in (i - 1, i + 1):
                if 0 <= j < len(lst):
                    n = list(c)
                    n[pos] = lst[j]
                    if n[0] != n[1]:
                        out.append(tuple(n))
        return out

    rob = []
    for c in key:
        vals = [key[c]] + [key[n] for n in neighbors(c) if n in key]
        vals = [v for v in vals if not np.isnan(v)]
        rob.append((c, min(vals) if vals else np.nan, len(vals)))
    rob = pd.DataFrame(rob, columns=["combo", "robust", "n_nb"]).dropna()
    rob = rob.sort_values("robust", ascending=False)

    print("\n" + "=" * 92)
    print("邻域稳健分（= 自身与全部邻居 IS 均值的最小值），前 10")
    print("=" * 92)
    tb = rob.head(10).copy()
    tb["robust%"] = (tb.robust * 100).round(3)
    tb["self%"] = [round(key[c] * 100, 3) for c in tb.combo]
    print(tb[["combo", "robust%", "self%", "n_nb"]].to_string(index=False))

    cand = rob.iloc[0].combo
    print(f"\n>>> 唯一候选（邻域稳健分最高）: 短{cand[0]}m 长{cand[1]}m "
          f"K_short<={cand[2]} K_long<={cand[3]} 确认{cand[4]}次")

    # ---------- OOS：只跑这一次 ----------
    print("\n" + "=" * 92)
    print("OOS 单次复核（2026-08-31 → 09-02，此前未被任何计算触碰）")
    print("=" * 92)
    ti, si = events(*cand)
    _, _, vo = stats(ti, si, oos_mask)
    _, _, bo = stats(bti, bsi, oos_mask)
    ci = r[(r.sw == cand[0]) & (r.lw == cand[1]) & (r.ks == cand[2]) &
           (r.kl == cand[3]) & (r.cf == cand[4])].iloc[0]
    print(f"  IS  候选 n={ci.n:.0f} 均值 {ci['mean']*100:+.3f}% "
          f"胜率 {ci.win*100:.1f}% | delta {ci.delta*100:+.3f}% "
          f"90%CI [{ci.d_lo*100:+.3f}%, {ci.d_hi*100:+.3f}%]")
    print(f"  IS  基线 n={len(bret):,} 均值 {bret.mean()*100:+.3f}% "
          f"胜率 {(bret>0).mean()*100:.1f}%")
    if len(vo) < 10:
        print(f"  OOS 候选事件仅 {len(vo)} 个，样本不足，不作判定")
    else:
        print(f"  OOS 候选 n={len(vo)} 均值 {vo.mean()*100:+.3f}% "
              f"胜率 {(vo>0).mean()*100:.1f}%")
        print(f"  OOS 基线 n={len(bo)} 均值 {bo.mean()*100:+.3f}% "
              f"胜率 {(bo>0).mean()*100:.1f}%")
        print(f"  OOS delta {(vo.mean()-bo.mean())*100:+.3f}%")

    print(f"\n[info] 耗时 {(time.time()-t0)/60:.1f} min")
    return 0


if __name__ == "__main__":
    sys.exit(main())
