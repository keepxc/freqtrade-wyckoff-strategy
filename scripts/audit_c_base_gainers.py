#!/usr/bin/env python3
"""裁决 C 底座前置法医核查 —— 2026-09-11

**探索性——非结论，禁止引用为证据。** 仅供「裁决 C 要不要立项」判断，不入证伪表。
对标 RESEARCH_FRAMEWORK §4 的 audit_task114/116/117 模式。

背景
----
`TASK_NEW/DRYRUN_STATUS_20260911.md` 裁决清单 C：
    「💎黄金一击后 24h/48h 正期望 → 爆发线回踩触发」，数据源 b300_breakout × macd_m8。

`scripts/b300_collector.py` 的 `fetch_binance_gainers()` 说明 b300_breakout 的**底座**
就是「币安 24h 涨幅榜 top20，qv ≥ 500 万 USDT」。该底座可用币安 K 线完全离线重建，
不必等两周积累——这正是 AI500_PRICEBOARD_PREREG §0 的同一条性质。

判据（跑数前冻结）
------------------
- 宇宙：币安 USDT 永续 528 个，2025-09-11 ~ 2026-09-11 UTC
- 入场网格：4h（对齐 b300 cron）
- 排名：按 24h 涨幅降序，取 top20，要求 24h 成交额 ≥ 500 万 USDT
- 入场价：close(T)（同源铁律）
- 出场：固定持有 24h / 48h，各报「无止损」与「带 5% 止损」两档
- 费用：往返 0.1%
- 统计：按日区块自举 2000 次（CLAUDE.md 禁止 n=总事件数 → sd/√n）

结论（2026-09-11 实测）
-----------------------
n=43460/腿，522 标的，363 天。四格全负，四格 90% CI 上界全部 < 0：
  持有 24h 无止损 mean=-0.511% CI[-0.831,-0.184] | 带止损 -0.472% CI[-0.652,-0.283]
  持有 48h 无止损 mean=-0.610% CI[-1.119,-0.093] | 带止损 -0.663% CI[-0.882,-0.438]
注意这是 top20（比涨幅榜 top3 的网宽得多）仍为负。

配合事件数：已积累的 b300_breakout.jsonl 110 行里 candidate 级 0 个、
candidate_minor 3 个且全是 ZEC → C 在可见未来都凑不出可用样本。**建议放弃立项。**
"""
import glob
import os

import numpy as np
import pandas as pd

PANEL = "user_data/ai500_local/kl1y"
FEE = 0.001
STOP = 0.05
MIN_QV_24H = 5e6
TOP_N = 20


def load_panel():
    cl, lo, qv = {}, {}, {}
    for f in glob.glob(f"{PANEL}/*.feather"):
        sym = os.path.basename(f)[:-8]
        d = pd.read_feather(f)
        d["t"] = pd.to_datetime(d.t, unit="ms", utc=True)
        d = d.set_index("t")
        cl[sym], lo[sym], qv[sym] = d.close, d.low, d.qv
    C = pd.DataFrame(cl).sort_index()
    # 补成完整小时网格，否则 shift(n) ≠ n 小时前
    full = pd.date_range(C.index[0], C.index[-1], freq="1h", tz="UTC")
    return C.reindex(full), pd.DataFrame(lo).reindex(full), pd.DataFrame(qv).reindex(full)


def boot_ci(df, col, n=2000, seed=0):
    """按日区块自举（CLAUDE.md：禁止 n=总事件数 → sd/√n）。"""
    g = df.groupby("day")[col].agg(["sum", "count"])
    S, N = g["sum"].values, g["count"].values
    rs = np.random.default_rng(seed)
    m = len(S)
    b = np.array([S[i].sum() / N[i].sum()
                  for i in (rs.integers(0, m, m) for _ in range(n))])
    return np.percentile(b, 5), np.percentile(b, 95), b.std()


def main():
    C, L, QV = load_panel()
    ret24 = C / C.shift(24) - 1.0
    qv24 = QV.rolling(24, min_periods=24).sum()
    idx = C.index
    Cv, Lv, r24v, qvv = C.values, L.values, ret24.values, qv24.values

    rows = []
    for i in (k for k in range(24, len(idx) - 48) if idx[k].hour % 4 == 0):
        r, q = r24v[i].copy(), qvv[i]
        ok = np.isfinite(r) & np.isfinite(q) & (q >= MIN_QV_24H) & np.isfinite(Cv[i])
        if ok.sum() < TOP_N:
            continue
        cand = np.where(ok)[0]
        for j in cand[np.argsort(-r[cand])[:TOP_N]]:
            entry = Cv[i, j]
            for H in (24, 48):
                k = i + H
                if not np.isfinite(Cv[k, j]):
                    continue
                seg, stop_px = Lv[i + 1:k + 1, j], entry * (1 - STOP)
                hit = np.isfinite(seg) & (seg <= stop_px)
                px = stop_px if hit.any() else Cv[k, j]
                rows.append(dict(t=idx[i], sym=C.columns[j], H=H, 涨幅24h=r[j] * 100,
                                 无止损=(Cv[k, j] / entry - 1 - FEE) * 100,
                                 带止损=(px / entry - 1 - FEE) * 100))

    M = pd.DataFrame(rows)
    M["day"] = M.t.dt.date
    M.to_csv(f"{PANEL}/audit_c_base_gainers_top20.csv", index=False)

    print("=== 探索性——非结论 | 裁决 C 底座：币安 24h 涨幅榜 top20 ===")
    print(f"网格 4h，一年，n/腿={len(M) // 2}，标的={M.sym.nunique()}，日数={M.day.nunique()}")
    for H in (24, 48):
        for col in ("无止损", "带止损"):
            g = M[M.H == H]
            lo, hi, sd = boot_ci(g, col)
            print(f"  持有{H}h {col}: mean={g[col].mean():+.3f}%  中位={g[col].median():+.3f}%  "
                  f"胜率={(g[col] > 0).mean() * 100:.1f}%  90%CI=[{lo:+.3f},{hi:+.3f}]  "
                  f"MDE={2.8016 * sd:.3f}")


if __name__ == "__main__":
    main()
