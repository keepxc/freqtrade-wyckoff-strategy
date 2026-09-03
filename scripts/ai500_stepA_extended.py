#!/usr/bin/env python3
"""
Step A 扩展校准 —— K 线净流代理 vs VergeX 1h 净流榜（16 天成对样本）
====================================================================
探索性——非结论，禁止引用为证据。

要回答的问题（与本机 405 快照那版完全相同，只换样本）
------------------------------------------------------
用交易所 1m K 线算出的 net_flow = 2*taker_buy_quote_volume - quote_volume，
按滚动窗口聚合后排名，能不能复现 VergeX 的 net_flow 榜？
能复现 → 才有资格用 K 线倒推 8 月以来的多窗口历史排名（Step B 的输入）。

为什么必须重做一遍
------------------
本机快照只有 405 个、覆盖约 19 小时单一行情，60 秒间距高度自相关，
名义样本远大于有效样本（CLAUDE.md「名义样本数 ≠ 有效样本数」）。
本次样本跨 16 天、两个月份、多段行情，才有资格谈稳定性。

本次的口径限制（必须如实标注）
------------------------------
远程 bot 调 VergeX 时没传 duration，历史全部是默认的 **1h 窗口**。
所以扩展样本只能校准 1h；5m/15m/30m 的证据仍只有本机那 405 个快照，
强度要单独打折说明，不能用本次结论覆盖。

Lookahead
---------
窗口右端取 t_end.floor("min")，配合 cumsum().shift(1)，
只累加到「信号时刻之前最后一根已收盘的 1m bar」，不含当前未收盘 bar。

输出
----
user_data/ai500_local/stepA_ext_<mode>.csv   每快照一行的指标
控制台打印总体 + 按日分组，用于看跨行情稳定性。
"""
import argparse
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
HIST = os.path.join(DATA_DIR, "ai500_netflow_hist_compact.csv.gz")

WIN_MIN = 60          # bot 历史唯一可用的窗口


def load_matrix():
    """把 526 个币的 1m net_flow 拼成 (minute × symbol) 矩阵。"""
    files = sorted(glob.glob(os.path.join(KL_DIR, "*-1m.feather")))
    if not files:
        sys.exit(f"[fatal] {KL_DIR} 为空，先跑 ai500_klines_bulk.py")
    cols = {}
    for f in files:
        sym = os.path.basename(f).replace("-1m.feather", "")
        d = pd.read_feather(f, columns=["t", "net_flow"])
        cols[sym] = d.set_index("t")["net_flow"]
    mat = pd.DataFrame(cols)
    full = pd.date_range(mat.index.min(), mat.index.max(), freq="min", tz="UTC")
    # 缺失的分钟 = 该分钟无成交（新上市币在上市前也是这种情况），净流记 0
    mat = mat.reindex(full).fillna(0.0)
    return mat


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["trailing", "aligned_partial", "both"],
                    default="both")
    ap.add_argument("--max-snapshots", type=int, default=0,
                    help="调试用，0=全部")
    args = ap.parse_args()

    t0 = time.time()
    print("[info] 载入 K 线矩阵 ...", flush=True)
    mat = load_matrix()
    print(f"[info] 矩阵 {mat.shape[0]:,} 分钟 x {mat.shape[1]} 币 | "
          f"{mat.index[0]} -> {mat.index[-1]} | {time.time()-t0:.0f}s",
          flush=True)

    cum = mat.cumsum().shift(1).fillna(0.0)
    idx = mat.index
    lo_bound, hi_bound = idx[0], idx[-1]

    print("[info] 载入 VergeX 历史 ...", flush=True)
    h = pd.read_csv(HIST)
    h = h[h.netflow_rank.notna()].copy()
    h["ts"] = pd.to_datetime(h.ts_utc, utc=True)
    h["rank"] = h.netflow_rank.astype(int)
    snaps = sorted(h.ts.unique())
    print(f"[info] 快照 {len(snaps):,} | 标的 {h.symbol.nunique()}", flush=True)

    kl_syms = set(mat.columns)
    modes = ["trailing", "aligned_partial"] if args.mode == "both" \
        else [args.mode]

    for mode in modes:
        rows = []
        n_skip_range = 0
        groups = h.groupby("ts")
        todo = snaps[:args.max_snapshots] if args.max_snapshots else snaps
        t1 = time.time()
        for n, ts in enumerate(todo, 1):
            ts = pd.Timestamp(ts)
            hi = ts.floor("min")
            lo = (hi - pd.Timedelta(minutes=WIN_MIN)) if mode == "trailing" \
                else ts.floor(f"{WIN_MIN}min")
            if lo >= hi or lo < lo_bound or hi > hi_bound:
                n_skip_range += 1
                continue

            g = groups.get_group(ts)
            v = g.set_index("symbol")["netflow_amount"].astype(float)
            vrank = g.set_index("symbol")["rank"]

            proxy = (cum.loc[hi] - cum.loc[lo]).sort_values(ascending=False)

            # VergeX 榜里有、K 线目录里没有的币（非币安永续 / 已下架）
            missing = [s for s in v.index if s not in kl_syms]
            common = [s for s in v.index if s in kl_syms]
            if len(common) < 10:
                continue

            pv = proxy.reindex(common)
            vv = v.reindex(common)
            prank_all = proxy.rank(ascending=False)

            v_top3 = set(vrank[vrank <= 3].index)
            v_top20 = set(vrank[vrank <= 20].index)

            rows.append({
                "ts": ts,
                "n_common": len(common),
                "n_missing": len(missing),
                "pearson": pv.corr(vv),
                "spearman_val": pv.corr(vv, method="spearman"),
                # VergeX 名次 vs 代理在全宇宙内的名次
                "spearman_rank": vrank.reindex(common).corr(
                    prank_all.reindex(common), method="spearman"),
                "overlap20": len(v_top20 & set(proxy.index[:20])),
                "overlap3": len(v_top3 & set(proxy.index[:3])),
                "top3_in_proxy10": len(v_top3 & set(proxy.index[:10])),
                # VergeX 金额 / 代理金额；若二者只差固定单位，该比值应稳定
                "scale_ratio": float(pv.sum() / vv.sum()) if vv.sum() else np.nan,
            })
            if n % 500 == 0:
                el = time.time() - t1
                print(f"[info]   {mode} {n}/{len(todo)} | {el:.0f}s "
                      f"ETA {el/n*(len(todo)-n):.0f}s", flush=True)

        r = pd.DataFrame(rows)
        out = os.path.join(DATA_DIR, f"stepA_ext_{mode}.csv")
        r.to_csv(out, index=False)

        print("\n" + "=" * 78)
        print(f"模式 {mode} | 窗口 {WIN_MIN}m | 有效快照 {len(r):,} "
              f"(越界跳过 {n_skip_range})")
        print("=" * 78)
        if not len(r):
            continue
        cols = ["pearson", "spearman_val", "spearman_rank",
                "overlap20", "overlap3", "top3_in_proxy10", "scale_ratio"]
        print("\n--- 总体 ---")
        print(r[cols].describe().loc[
            ["mean", "std", "25%", "50%", "75%"]].round(3).to_string())
        tot = r.n_missing.sum() + r.n_common.sum()
        print(f"\nVergeX 榜中缺 K 线的比例: {r.n_missing.sum()/tot:.1%}")

        print("\n--- 按日（看跨行情稳定性）---")
        r["day"] = r.ts.dt.date
        daily = r.groupby("day")[cols].mean().round(3)
        daily["n"] = r.groupby("day").size()
        print(daily.to_string())

    print(f"\n[info] 总耗时 {(time.time()-t0)/60:.1f} min")
    return 0


if __name__ == "__main__":
    sys.exit(main())
