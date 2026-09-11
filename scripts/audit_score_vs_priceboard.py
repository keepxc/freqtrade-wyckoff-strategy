#!/usr/bin/env python3
"""score 榜 × 涨幅榜 重合度 + 信号年龄核查 —— 2026-09-11

**探索性——非结论，禁止引用为证据。** 仅供立项判断，不入证伪表。

要回答的两个问题
----------------
Q1  `AI500_PRICEBOARD_PREREG` 证伪了「横截面涨幅 top3 + 短持有」。V3 跑的是 score 榜，
    两者重合多少？重合高 → 证伪传导到 V3；重合低 → 不传导。
Q2  score 榜信号存活时长（`start_time` 到入场时刻）有没有预测力？值不值得加第四层门。

数据
----
- score 榜快照：V2 `ai500_score_history.csv` + V3 `ai500_score_history_v3.csv`
  （两者字段同构；V2 中途从 +00:00 换成 +08:00，但 offset 内嵌且正确，
   **必须按 offset 解析、不得手动 -8h**，否则双重修正）
- 涨幅榜：用 `user_data/ai500_local/kl1y/` 的 528 标的 × 一年 1h K 线自行重建横截面排名

结论（2026-09-11 实测）
-----------------------
Q1 重合度低。score 榜标的在全市场涨幅横截面上的中位名次：
   dur=1h 193/528（落 top3 仅 5.6%）| 4h 138/528（7.0%）| 24h 70/526（12.7%）
   → **V3 不是换皮的涨幅榜，证伪不传导。**
   但另一面：接口口径累计涨幅中位 +55.1%（>100% 占 31.1%），而同时刻 trailing-24h
   实际涨幅中位仅 +3.9%、29.1% 在跌 → V3 买的是「已经走完、正在衰减」的行情。
   IOST 那次 +91.58% 只排 65 分位，**不是异常值**。

Q2 不加。41 个独立 episode / 32 标的 / 11 天，全样本 MDE=5.01%，子集 7.17~10.63%。
   方向还与提案相反（<24h 的 fwd24 均值 -7.88%，>7d 为 +3.76%），且 >7d 那格被
   幸存者偏差污染（信号能活过 7 天本身就是「没崩」的条件）。样本撑不起任何门槛。
"""
import glob
import os
import sys

import numpy as np
import pandas as pd

PANEL = "user_data/ai500_local/kl1y"
FEE = 0.001


def load_panel():
    cl = {}
    for f in glob.glob(f"{PANEL}/*.feather"):
        sym = os.path.basename(f)[:-8]
        d = pd.read_feather(f)
        d["t"] = pd.to_datetime(d.t, unit="ms", utc=True)
        cl[sym] = d.set_index("t")["close"]
    C = pd.DataFrame(cl).sort_index()
    return C.reindex(pd.date_range(C.index[0], C.index[-1], freq="1h", tz="UTC"))


def load_scores(src_dir):
    def one(fn, src):
        d = pd.read_csv(f"{src_dir}/{fn}")
        # offset 内嵌且正确（V2 中途换过时区）→ utc=True 归一，禁止手动 -8h
        d["t"] = pd.to_datetime(d.fetched_at_utc, utc=True).dt.floor("1h")
        d["sym"] = d.pair.str.split("/").str[0] + "USDT"
        d["src"] = src
        return d
    sc = pd.concat([one("ai500_score_history.csv", "V2"),
                    one("ai500_score_history_v3.csv", "V3")])
    # 同一小时同一标的取 score 最高的一条
    return sc.sort_values("score", ascending=False).groupby(["t", "sym"], as_index=False).first()


def boot_ci(d, col, n=2000, seed=0):
    d = d.copy()
    d["day"] = d.t.dt.date
    g = d.groupby("day")[col].agg(["sum", "count"])
    S, N = g["sum"].values, g["count"].values
    rs = np.random.default_rng(seed)
    m = len(S)
    b = np.array([S[i].sum() / N[i].sum()
                  for i in (rs.integers(0, m, m) for _ in range(n))])
    return np.percentile(b, 5), np.percentile(b, 95), b.std()


def main(src_dir):
    C = load_panel()
    sc = load_scores(src_dir)
    sc = sc[sc.t.isin(C.index) & sc.sym.isin(C.columns)].copy()
    print("=== 探索性——非结论 ===")
    print(f"score 快照小时数 {sc.t.nunique()} | 标的-小时 {len(sc)} | 覆盖日 {sc.t.dt.date.nunique()}")

    # ---- Q1: 在全市场涨幅横截面上的名次
    rows = []
    for dur in (1, 4, 24):
        ret = C / C.shift(dur) - 1.0
        rk = ret.rank(axis=1, ascending=False, method="min")
        nv = ret.notna().sum(axis=1)
        for t, g in sc.groupby("t"):
            for _, r in g.iterrows():
                pr = rk.at[t, r.sym]
                if np.isfinite(pr):
                    rows.append(dict(t=t, sym=r.sym, src=r.src, score=r.score, dur=f"{dur}h",
                                     涨幅榜名次=int(pr), 宇宙=int(nv[t]),
                                     窗口涨幅=ret.at[t, r.sym] * 100,
                                     接口涨幅=r.change_pct_value))
    M = pd.DataFrame(rows)
    M.to_csv(f"{PANEL}/score_vs_priceboard.csv", index=False)
    print("\n--- Q1 score 榜标的在全市场涨幅榜上的名次 ---")
    for dur, g in M.groupby("dur"):
        line = (f"  dur={dur:>3} n={len(g):5d} 中位名次={g.涨幅榜名次.median():5.0f}/{g.宇宙.median():.0f}")
        for k in (3, 10, 20, 50):
            line += f"  top{k}={(g.涨幅榜名次 <= k).mean() * 100:5.1f}%"
        print(line)
    g24 = M[M.dur == "24h"]
    print(f"  接口累计涨幅: 中位 {g24.接口涨幅.median():.1f}%  >50% 占 {(g24.接口涨幅 > 50).mean() * 100:.1f}%"
          f"  >100% 占 {(g24.接口涨幅 > 100).mean() * 100:.1f}%")
    print(f"  同时刻实际 24h 涨幅: 中位 {g24.窗口涨幅.median():.1f}%  在跌占 {(g24.窗口涨幅 < 0).mean() * 100:.1f}%")

    # ---- Q2: 信号年龄 vs 前向收益
    sc["年龄_h"] = (sc.t.astype("int64") / 1e9 - sc.start_time) / 3600
    sc["episode"] = sc.sym + "_" + sc.start_time.astype(str)
    for H in (4, 24):
        fwd = C.shift(-H) / C - 1.0
        sc[f"fwd{H}"] = [fwd.at[t, s] * 100 - FEE * 100 if t in fwd.index else np.nan
                         for t, s in zip(sc.t, sc.sym)]
    sc = sc.dropna(subset=["fwd24"])
    sc["年龄档"] = pd.cut(sc.年龄_h, [-1, 6, 24, 72, 168, 1e9],
                          labels=["<6h", "6-24h", "1-3d", "3-7d", ">7d"])
    print(f"\n--- Q2 信号年龄 (独立 episode {sc.episode.nunique()} | 标的 {sc.sym.nunique()}"
          f" | 日 {sc.t.dt.date.nunique()}) ---")
    print(sc.groupby("年龄档", observed=True).apply(lambda d: pd.Series({
        "n": len(d), "episode": d.episode.nunique(),
        "fwd4h均值": round(d.fwd4.mean(), 3), "fwd24h均值": round(d.fwd24.mean(), 3),
        "fwd24h胜率": round((d.fwd24 > 0).mean() * 100, 1)}), include_groups=False).to_string())
    for lab, sub in (("全样本", sc), ("年龄<24h", sc[sc.年龄_h < 24]), ("年龄>7d", sc[sc.年龄_h > 168])):
        if len(sub) < 10:
            continue
        lo, hi, sd = boot_ci(sub, "fwd24")
        print(f"  {lab:<9} n={len(sub):4d} ep={sub.episode.nunique():3d} "
              f"fwd24 mean={sub.fwd24.mean():+.3f}% 90%CI=[{lo:+.3f},{hi:+.3f}] MDE={2.8016 * sd:.3f}%")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
