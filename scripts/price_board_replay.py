#!/usr/bin/env python3
"""涨幅榜（vergex trending-crypto?tab=price）离线复现 + 前向收益回放

⚠️ 探索性——非结论，禁止引用为证据。判据未预注册。

为什么这个榜可以离线复现
------------------------
2026-09-05 实测：接口的 price_delta 就是**真实涨幅**，且吃 duration 参数。
  duration=24h: BULLA 板 +100.78% vs 币安 +101.14%；DASH +42.47 vs +44.90；ZEN +22.65 vs +23.12
即「涨幅前 N」= 横截面按窗口涨幅排序，用币安 K 线可完全重建任意历史时点。
（score 榜不可复现：分数是对方算的，且历史只剩活着的信号 = 幸存者样本。）

Lookahead 清单逐项
------------------
1. 无转折点/极值回溯标记——排名只用 T 时刻及之前已收盘的 close。
2. 跨标的合并：面板 reindex 到**完整**1h UTC 网格，shift(n) 才等价于「n 小时前」；
   若按原始行 shift，有数据空洞的标的会静默错位。T 时点只读 index<=T 的 bar。
3. 无 df.iloc[i+n] 进入信号；前向收益是**标签**不是特征，严格取 T 之后的 bar。
4. 胜率>90% 或 DD<1% 需怀疑未来函数——摘要输出胜率供核对。
入场价 = T 根的 close（同源铁律，全项目一致口径）。
止损与出场同 bar 时**假定止损先触发**（保守）。
"""
import json, os
import numpy as np, pandas as pd

OUT = "user_data/ai500_local/kl1y"
FEE = 0.001          # 往返 0.1%
STOP = 0.05          # 5% 止损
MAX_HOLD_H = 48      # 与 AI500_EXIT_4H_PREREG §2.2 同口径（数据可得性兜底，非调参）

def load_panel():
    files = sorted(f for f in os.listdir(OUT) if f.endswith(".feather"))
    cl, op, hi, lo, qv = {}, {}, {}, {}, {}
    for f in files:
        s, d = f[:-8], pd.read_feather(f"{OUT}/{f}")
        if len(d) < 48:
            continue
        ix = pd.to_datetime(d.t, unit="ms", utc=True)
        cl[s] = pd.Series(d.close.values, index=ix); op[s] = pd.Series(d.open.values, index=ix)
        hi[s] = pd.Series(d.high.values, index=ix);  lo[s] = pd.Series(d.low.values, index=ix)
        qv[s] = pd.Series(d.qv.values, index=ix)
    C = pd.DataFrame(cl).sort_index()
    # 关键：补成完整小时网格，否则 shift(n) ≠ n 小时前（见文件头 Lookahead 第 2 条）
    full = pd.date_range(C.index[0], C.index[-1], freq="1h", tz="UTC")
    C = C.reindex(full)
    g = lambda d: pd.DataFrame(d).reindex(full)
    return C, g(op), g(hi), g(lo), g(qv)

def replay(C, O, H, L, QV, dur_h, topn=3, grid_h=4, min_qv=0.0):
    idx = C.index
    ret = C / C.shift(dur_h) - 1.0                 # 只用已收盘 bar
    liq = QV.rolling(24, min_periods=24).median()  # 过去 24h 每小时成交额中位
    # 上市天数：用币安 onboardDate 真实值。早先用 C.notna().cumsum() 是错的——
    # 那把「面板刚开始」当成「刚上市」，全部标的开头都会被记成 age=1。
    ob = json.load(open(f"{OUT}/symbols.json"))
    onboard = np.array([ob.get(s_, 0) / 1000.0 for s_ in C.columns])
    tsec = idx.astype("int64").values / 1e9
    Cv, Ov, Hv, Lv = C.values, O.values, H.values, L.values
    is4 = (idx.hour % 4 == 3)                      # 该 1h bar 收盘 == 一根 4h K 收盘
    pos = {s: i for i, s in enumerate(C.columns)}
    rows, start = [], max(dur_h, 24) + 1
    for ti in range(start, len(idx) - 1):
        if grid_h != 1 and (idx[ti].hour % grid_h) != (grid_h - 1):
            continue
        r = ret.iloc[ti]
        r = r[r.notna() & (liq.iloc[ti] >= min_qv) & C.iloc[ti].notna()]
        if len(r) < topn:
            continue
        for rank, (sym, chg) in enumerate(r.nlargest(topn).items(), 1):
            j = pos[sym]; entry = Cv[ti, j]
            if not np.isfinite(entry) or entry <= 0:
                continue
            stop_px = entry * (1 - STOP)
            rec, last = {}, min(ti + MAX_HOLD_H, len(idx) - 1)
            # 出场 A：ai500.txt —— 首根「收盘晚于入场」的收阴 4h K
            ex_px = None; tag = "maxhold"
            for k in range(ti + 1, last + 1):
                if np.isfinite(Lv[k, j]) and Lv[k, j] <= stop_px:
                    ex_px, hold, tag = stop_px, k - ti, "stop"; break
                if is4[k] and k >= 3 and np.isfinite(Cv[k, j]) and np.isfinite(Ov[k - 3, j]) \
                        and Cv[k, j] < Ov[k - 3, j]:
                    ex_px, hold, tag = Cv[k, j], k - ti, "bear4h"; break
            if ex_px is None:
                ex_px, hold = Cv[last, j], last - ti
            rec.update(A_ret=(ex_px / entry - 1) * 100 - FEE * 100, A_hold=hold, A_tag=tag)
            # 出场 B/C：固定持有 4h / 24h（同样带 5% 止损）
            for lbl, hh in (("B", 4), ("C", 24)):
                px, t2 = None, "time"
                end = min(ti + hh, len(idx) - 1)
                for k in range(ti + 1, end + 1):
                    if np.isfinite(Lv[k, j]) and Lv[k, j] <= stop_px:
                        px, t2 = stop_px, "stop"; break
                if px is None:
                    px = Cv[end, j]
                rec[f"{lbl}_ret"] = (px / entry - 1) * 100 - FEE * 100
                rec[f"{lbl}_tag"] = t2
            rows.append(dict(t=idx[ti], day=idx[ti].date(), sym=sym, rank=rank,
                             chg=chg * 100, entry=entry,
                             age_d=int((tsec[ti] - onboard[j]) // 86400), **rec))
    return pd.DataFrame(rows)

def boot_ci(df, col, n=2000, seed=0):
    """按日区块自举（CLAUDE.md：禁止 n=总事件数 → sd/√n）。"""
    g = df.groupby("day")[col].agg(["sum", "count"])
    S, N = g["sum"].values, g["count"].values
    if len(S) < 5:
        return np.nan, np.nan, np.nan
    rng = np.random.default_rng(seed); out = np.empty(n)
    for i in range(n):
        p = rng.integers(0, len(S), len(S))
        out[i] = S[p].sum() / max(N[p].sum(), 1)
    return np.percentile(out, 5), np.percentile(out, 95), out.std()

if __name__ == "__main__":
    C, O, H, L, QV = load_panel()
    print("面板: %d 根 1h bar × %d 标的  %s -> %s" % (
        len(C), C.shape[1], C.index[0], C.index[-1]), flush=True)
    recs = []
    for dur_h, dlab in ((1, "1h"), (4, "4h"), (24, "24h")):
        for min_qv, qlab in ((0.0, "无"), (5e5, "50万U/h")):
            d = replay(C, O, H, L, QV, dur_h, 3, 4, min_qv)
            if d.empty:
                continue
            d.to_csv(f"{OUT}/replay_{dlab}_{'liq' if min_qv else 'all'}.csv", index=False)
            for leg, name in (("A_ret", "ai500首根阴线"), ("B_ret", "固定4h"), ("C_ret", "固定24h")):
                lo_, hi_, sd = boot_ci(d, leg)
                recs.append(dict(duration=dlab, liq=qlab, exit=name, n=len(d),
                                 mean=d[leg].mean(), med=d[leg].median(),
                                 win=(d[leg] > 0).mean() * 100, sd=d[leg].std(),
                                 ci_lo=lo_, ci_hi=hi_, mde=2.8016 * sd,
                                 newlist=(d.age_d < 30).mean() * 100))
            print("done %s/%s n=%d" % (dlab, qlab, len(d)), flush=True)
    r = pd.DataFrame(recs); r.to_csv(f"{OUT}/summary.csv", index=False)
    pd.set_option("display.width", 250)
    print("\n=== 探索性——非结论 ===")
    print(r.to_string(index=False, float_format=lambda x: "%.3f" % x))
