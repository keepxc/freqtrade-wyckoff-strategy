# -*- coding: utf-8 -*-
"""H1/H2 分析 —— 严格按 PREREG_H1_H2.md（0e47cc4）执行，不得事后改口径。"""
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
D = "research/ai500_h1/data"
RNG = np.random.default_rng(20260925)
HORIZONS = {"1d": 24, "4d": 96, "7d": 168}
N_BOOT = 5000

k = pd.read_parquet(f"{D}/klines_1h.parquet")
px = k.pivot_table(index="open_time", columns="symbol", values="open")   # 1h 开盘价矩阵
kmax = px.index.max()

sh = pd.read_csv("user_data/ai500_score_history_v3.csv")
sh["t"] = pd.to_datetime(sh["fetched_at_utc"]).dt.tz_convert("UTC")
sh = sh[sh["start_time"].notna()].copy()
sh["sig"] = sh["symbol"] + "@" + sh["start_time"].astype("Int64").astype(str)
t_first_collect = sh["t"].min()


def fwd(sym, t_entry, h):
    """返回 (信号收益, 基准中位收益)；数据不全返回 None。"""
    t_exit = t_entry + pd.Timedelta(hours=h)
    if t_exit > kmax or t_entry not in px.index or t_exit not in px.index:
        return None
    a, b = px.loc[t_entry], px.loc[t_exit]
    if sym not in px.columns or pd.isna(a.get(sym)) or pd.isna(b.get(sym)):
        return None
    r_all = (b / a - 1).dropna()
    return b[sym] / a[sym] - 1, r_all.median()


def events_from(first_times):
    """first_times: DataFrame[sig, symbol, t0] → 每个窗口的事件超额表。"""
    rows = []
    for _, e in first_times.iterrows():
        t_entry = e["t0"].ceil("1h")
        for hn, h in HORIZONS.items():
            r = fwd(e["symbol"], t_entry, h)
            if r is None:
                continue
            rows.append({**e.to_dict(), "t_entry": t_entry, "h": hn,
                         "ret": r[0], "bench": r[1], "excess": r[0] - r[1]})
    return pd.DataFrame(rows)


def day_boot_median(df, n=N_BOOT, rng=RNG):
    """按入场日区块自举中位超额。"""
    df = df.assign(day=df["t_entry"].dt.floor("1D"))
    groups = [g["excess"].values for _, g in df.groupby("day")]
    m = len(groups)
    out = np.empty(n)
    for i in range(n):
        idx = rng.integers(0, m, m)
        out[i] = np.median(np.concatenate([groups[j] for j in idx]))
    return out


# ================================================================ 事件（H1）
first = sh.sort_values("t").groupby("sig").first().reset_index()
first = first[first["t"] > t_first_collect + pd.Timedelta("1h")]
first = first.rename(columns={"t": "t0"})[["sig", "symbol", "t0", "score", "change_pct_value"]]
print(f"H1 信号数（剔除采集开始前已在榜）: {len(first)}")

# ================================================================ 功效（先于事件收益）
print("\n=== 功效：零效应伪事件（随机 symbol × 同时间窗随机入场） ===")
syms = px.columns.to_list()
entry_hours = pd.date_range(first["t0"].min().ceil("1h"), first["t0"].max().ceil("1h"), freq="1h")
mde = {}
for hn, h in HORIZONS.items():
    n_real = sum(1 for _, e in first.iterrows() if e["t0"].ceil("1h") + pd.Timedelta(hours=h) <= kmax)
    meds = []
    for rep in range(200):
        pe = []
        while len(pe) < n_real:
            s = syms[RNG.integers(len(syms))]
            t = entry_hours[RNG.integers(len(entry_hours))]
            r = fwd(s, t, h)
            if r is not None:
                pe.append({"t_entry": t, "excess": r[0] - r[1]})
        pe = pd.DataFrame(pe)
        meds.append(np.std(day_boot_median(pe, n=200, rng=RNG)))
    sd = float(np.mean(meds))
    mde[hn] = 2.49 * sd
    print(f"  {hn}: n={n_real}  自举 sd(中位超额)={sd*100:.2f}%  MDE≈{mde[hn]*100:.2f}%")

# ================================================================ H1 结果
ev = events_from(first)
print("\n=== H1：首次上榜后前向超额（信号收益 − 全市场中位） ===")
h1 = []
for hn in HORIZONS:
    e = ev[ev["h"] == hn]
    bt = day_boot_median(e)
    h1.append({"h": hn, "n": len(e), "days": e["t_entry"].dt.floor("1D").nunique(),
               "median_excess_%": np.median(e["excess"]) * 100, "mean_excess_%": e["excess"].mean() * 100,
               "ci5_%": np.percentile(bt, 5) * 100, "ci95_%": np.percentile(bt, 95) * 100,
               "hit_rate_%": (e["excess"] > 0).mean() * 100, "MDE_%": mde[hn] * 100})
h1 = pd.DataFrame(h1).round(2)
print(h1.to_string(index=False))
ev.to_csv("research/ai500_h1/h1_events.csv", index=False)

# ================================================================ H2
print("\n=== H2：score 分档首次落入后的 4d 超额（描述性） ===")
bins = [-np.inf, 60, 70, 80, np.inf]
labs = ["<60", "60-70", "70-80", ">=80"]
sh["bucket"] = pd.cut(sh["score"], bins=bins, labels=labs, right=False)
fb = (sh.sort_values("t").groupby(["sig", "bucket"], observed=True).first().reset_index()
      .rename(columns={"t": "t0"}))
fb = fb[fb["t0"] > t_first_collect + pd.Timedelta("1h")]
ev2 = events_from(fb[["sig", "symbol", "t0", "bucket", "score", "change_pct_value"]])
e4 = ev2[ev2["h"] == "4d"]
h2 = e4.groupby("bucket", observed=True).agg(
    n=("excess", "size"), median_excess=("excess", "median"), mean_excess=("excess", "mean"),
    hit=("excess", lambda s: (s > 0).mean()), prior_gain_med=("change_pct_value", "median"))
h2[["median_excess", "mean_excess", "hit"]] *= 100
print(h2.round(2).to_string())
hi, lo = e4[e4["bucket"] == ">=80"], e4[e4["bucket"] != ">=80"]
if len(hi):
    d_obs = np.median(hi["excess"]) - np.median(lo["excess"])
    ds = []
    for _ in range(N_BOOT):
        a = hi["excess"].values[RNG.integers(0, len(hi), len(hi))]
        b = day_boot_median(lo, n=1)[0]
        ds.append(np.median(a) - b)
    print(f"  ≥80 − <80 中位差 = {d_obs*100:+.2f}%  90%CI [{np.percentile(ds,5)*100:+.2f}, {np.percentile(ds,95)*100:+.2f}]  (≥80 n={len(hi)})")
ev2.to_csv("research/ai500_h1/h2_events.csv", index=False)

# ================================================================ H1b（配对描述）
print("\n=== H1b：达到 ≥80 的信号 —— 首次上榜入场 vs 首次 ≥80 入场（4d 超额，描述） ===")
a = ev[(ev["h"] == "4d")].set_index("sig")
b = e4[e4["bucket"] == ">=80"].set_index("sig")
sig80 = sh[sh["score"] >= 80]["sig"].unique()
rows = []
for s in sig80:
    rows.append({"sig": s,
                 "first_seen_excess_%": a["excess"].get(s, np.nan) * 100,
                 "first_seen_prior_gain_%": a["change_pct_value"].get(s, np.nan),
                 "first80_excess_%": b["excess"].get(s, np.nan) * 100,
                 "first80_prior_gain_%": b["change_pct_value"].get(s, np.nan)})
print(pd.DataFrame(rows).round(2).to_string(index=False))
print("（NaN = 采集开始前已在榜，或 4d 窗口超出 K 线范围）")
