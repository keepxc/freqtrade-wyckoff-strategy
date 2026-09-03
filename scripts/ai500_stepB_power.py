#!/usr/bin/env python3
"""
Step B 第 0 步：构建多窗口代理排名面板 + 功效分析（MDE）
=========================================================
判据见 AI500_STEPB_PREREG.md（已冻结，本脚本不得偏离）。

本脚本**不做参数扫描**。只回答一个前置问题：
  以现有 16 天样本和基线规则的事件频率，能检测出多大的效应？
MDE 若远大于合理预期效应，该实验从设计上不可能给出结论，应改设计。
（CLAUDE.md「先算功效，再跑回测」）

关键：MDE 必须用**按日期区块的自举**估计。加密资产共同 beta 极高，
用 n=总事件数 直接算 sd/sqrt(n) 会把功效高估约一倍（W1-b 实测 2.0~2.4 倍方差膨胀）。

输出
----
user_data/ai500_local/stepB_panel.npz   排名面板，供扫描脚本复用
控制台：基线事件数、fwd2h 收益分布、日区块自举 MDE
"""
import glob
import os
import sys
import time

import numpy as np
import pandas as pd

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
DATA_DIR = os.path.join(_REPO_ROOT, "user_data", "ai500_local")
KL_DIR = os.path.join(DATA_DIR, "klines_hist")
PANEL = os.path.join(DATA_DIR, "stepB_panel.npz")

# ---- 冻结参数（改动必须同步改预注册文档）----
WINDOWS = [5, 15, 30, 60]
GRID_MIN = 5
FWD_MIN = 120
COOLDOWN_MIN = 120
IS_END = pd.Timestamp("2026-08-31", tz="UTC")     # OOS 起点
BOOT_N = 2000
RNG_SEED = 20260903


def load_matrices():
    files = sorted(glob.glob(os.path.join(KL_DIR, "*-1m.feather")))
    nf, cl = {}, {}
    for f in files:
        sym = os.path.basename(f).replace("-1m.feather", "")
        d = pd.read_feather(f, columns=["t", "close", "net_flow"]).set_index("t")
        nf[sym] = d["net_flow"]
        cl[sym] = d["close"]
    nf = pd.DataFrame(nf)
    cl = pd.DataFrame(cl)
    full = pd.date_range(nf.index.min(), nf.index.max(), freq="min", tz="UTC")
    # 净流缺失分钟 = 无成交，记 0；价格缺失用前值（停牌/无成交时价格不变）
    nf = nf.reindex(full).fillna(0.0)
    cl = cl.reindex(full).ffill()
    return nf, cl


def main() -> int:
    t0 = time.time()
    print("[info] 载入 K 线 ...", flush=True)
    nf, cl = load_matrices()
    syms = list(nf.columns)
    idx = nf.index
    print(f"[info] {len(idx):,} 分钟 x {len(syms)} 币 | "
          f"{idx[0]} -> {idx[-1]} | {time.time()-t0:.0f}s", flush=True)

    # 每个标的的首个真实 bar（上市过滤用）：close 首次非 NaN 的位置
    first_valid = cl.apply(lambda s: s.first_valid_index())
    fv = np.array([idx.get_loc(first_valid[s]) for s in syms])

    cum = nf.cumsum().shift(1).fillna(0.0).to_numpy(dtype=np.float64)
    px = cl.shift(1).to_numpy(dtype=np.float64)      # px[i] = 时刻 idx[i] 可观测价格

    # 信号网格：留足最长窗口(60m)与前瞻(120m)
    g0 = int(np.ceil(max(WINDOWS) / GRID_MIN) * GRID_MIN)
    g1 = len(idx) - FWD_MIN - 1
    gpos = np.arange(g0, g1, GRID_MIN)
    print(f"[info] 信号网格 {len(gpos):,} 个时点 (每 {GRID_MIN} 分钟)", flush=True)

    # ---- 排名面板：ranks[w][t, s] = 该时点该币在窗口 w 上的名次(1=最大流入) ----
    ranks = {}
    for w in WINDOWS:
        flow = cum[gpos] - cum[gpos - w]
        # 上市不足 w 分钟的标的置为 -inf，永远排不进榜
        valid = (gpos[:, None] - w) >= fv[None, :]
        flow = np.where(valid, flow, -np.inf)
        order = np.argsort(-flow, axis=1, kind="stable")
        r = np.empty_like(order)
        np.put_along_axis(r, order, np.arange(1, len(syms) + 1)[None, :], axis=1)
        ranks[w] = r.astype(np.int16)
        print(f"[info]   窗口 {w}m 排名完成", flush=True)

    fwd = px[gpos + FWD_MIN] / px[gpos] - 1.0
    ts = idx[gpos]

    np.savez_compressed(
        PANEL, gpos=gpos, ts=ts.view("int64"), fwd=fwd,
        syms=np.array(syms), **{f"rank{w}": ranks[w] for w in WINDOWS})
    print(f"[info] 面板已存 {PANEL} "
          f"({os.path.getsize(PANEL)/1e6:.0f} MB)", flush=True)

    # ---- 基线规则事件：1h 排名 <= 3，同标的 2h 冷却 ----
    hit = ranks[60] <= 3
    ti, si = np.nonzero(hit)
    ok = np.isfinite(fwd[ti, si])
    ti, si = ti[ok], si[ok]

    keep = np.zeros(len(ti), dtype=bool)
    last = {}
    cd = COOLDOWN_MIN // GRID_MIN
    for k in np.argsort(ti, kind="stable"):
        s = si[k]
        if ti[k] - last.get(s, -10**9) >= cd:
            keep[k] = True
            last[s] = ti[k]
    ti, si = ti[keep], si[keep]

    ev = pd.DataFrame({"ts": ts[ti], "sym": np.array(syms)[si],
                       "ret": fwd[ti, si]})
    ev["day"] = ev.ts.dt.date
    ev_is = ev[ev.ts + pd.Timedelta(minutes=FWD_MIN) <= IS_END]

    print("\n" + "=" * 74)
    print("基线（现行策略规则：1h 排名 <= 3）")
    print("=" * 74)
    print(f"  全样本事件  {len(ev):,}  | IS 段(含 embargo) {len(ev_is):,}")
    print(f"  独立日区块  全 {ev.day.nunique()}  | IS {ev_is.day.nunique()}")
    print(f"  每日事件数  中位 {ev.groupby('day').size().median():.0f}")
    print(f"\n  fwd{FWD_MIN}m 原始收益:")
    print(f"    均值 {ev.ret.mean()*100:+.3f}%  中位 {ev.ret.median()*100:+.3f}%"
          f"  标准差 {ev.ret.std()*100:.3f}%")
    print(f"    胜率 {(ev.ret>0).mean()*100:.1f}%")

    # ---- 日区块自举：重采样整天，日内事件同进同出 ----
    rng = np.random.default_rng(RNG_SEED)
    for tag, e in [("全样本", ev), ("IS 段", ev_is)]:
        days = e.day.unique()
        by_day = [e.ret.values[e.day.values == d] for d in days]
        means = np.empty(BOOT_N)
        for b in range(BOOT_N):
            pick = rng.integers(0, len(days), len(days))
            means[b] = np.concatenate([by_day[i] for i in pick]).mean()
        sd = means.std(ddof=1)
        mde = (1.96 + 0.8416) * sd
        lo, hi = np.percentile(means, [5, 95])
        naive = e.ret.std() / np.sqrt(len(e))
        print(f"\n  --- {tag} 日区块自举 ({len(days)} 块, {BOOT_N} 次) ---")
        print(f"    实际均值      {e.ret.mean()*100:+.3f}%")
        print(f"    自举 sd       {sd*100:.3f}%   "
              f"(朴素 sd/sqrt(n) = {naive*100:.3f}%, "
              f"低估 {sd/naive:.1f}x)")
        print(f"    90% CI        [{lo*100:+.3f}%, {hi*100:+.3f}%]")
        print(f"    MDE (80%功效) {mde*100:.3f}%   "
              f"← 只能检测出 >= 此量级的效应")

    print(f"\n[info] 总耗时 {(time.time()-t0)/60:.1f} min")
    return 0


if __name__ == "__main__":
    sys.exit(main())
