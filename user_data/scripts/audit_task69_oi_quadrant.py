"""
TASK 69（预研）：OI 四象限前瞻信号审计——V9 空头触发器可行性裁决

数据：data.binance.vision 归档聚合的 1H OI（TASK 57 基建，2020-09~2026-07），
      重采样到日线（每 UTC 日最后一条读数 = 日收盘时点 OI 状态，因果）。

四象限（威科夫 Effort 的 OI 表达，成交量因 wash trading 被 TASK 29 证死）：
  Q1 价涨+OI涨 = 新多进场（趋势延续看涨）   Q2 价涨+OI跌 = 空头回补（虚涨）
  Q3 价跌+OI涨 = 新空进场（趋势延续看跌）   Q4 价跌+OI跌 = 多头投降（衰竭）

预注册判据（跑数前写定；OI 无 spot 对照 → 单数据集，判据从严）：
  1. 关键象限 fwd20d 与全样本基线差异的 bootstrap 95%CI 不含零
  2. 逐年（n≥15）无符号反转——TASK 64/66 的 regime 教训
  3. V9 适用性：在 EMA200 熊市 regime（连续≥5日收于其下）内部，象限仍提供
     超出 regime 基线的增量信号（CI 不含零）；不满足则只能作全局 regime 参考，
     不能作空头触发器
  主检验对象：Q2（熊市反弹=空头回补，V9 做空入场候选）与 Q3（新空驱动下跌，
  延续确认候选）。Q1/Q4 记录供多头侧参考，不设判据。
辅助（不设门槛）：强动量变体——|Δprice| 与 |ΔOI| 均超各自中位数的子集。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import numpy as np
import pandas as pd

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
OI_PATH = os.path.join(BASE, "data", "oi_metrics", "BTC_USDT_USDT-1h-oi_metrics.feather")
PX_PATH = os.path.join(BASE, "data", "binance", "futures", "BTC_USDT_USDT-1d-futures.feather")
HORIZONS = [5, 10, 20]


def bootstrap_ci(a, b, n_boot=5000, seed=42):
    rng = np.random.default_rng(seed)
    diffs = np.array([rng.choice(a, len(a)).mean() - rng.choice(b, len(b)).mean()
                      for _ in range(n_boot)])
    return np.percentile(diffs, 2.5), np.percentile(diffs, 97.5)


def main():
    oi = pd.read_feather(OI_PATH)
    oi["day"] = oi["date"].dt.floor("D")
    oi_d = oi.groupby("day")["sum_open_interest"].last().reset_index()
    oi_d.columns = ["date", "oi"]

    px = pd.read_feather(PX_PATH).sort_values("date").reset_index(drop=True)
    px["date"] = px["date"].dt.floor("D")
    df = px.merge(oi_d, on="date", how="inner").sort_values("date").reset_index(drop=True)

    df["d_px"] = df["close"].pct_change()
    df["d_oi"] = df["oi"].pct_change()
    for h in HORIZONS:
        df[f"fwd{h}"] = df["close"].shift(-h) / df["close"] - 1

    ema200 = df["close"].ewm(span=200, adjust=False).mean()
    below = df["close"] < ema200
    df["bear"] = below.rolling(5).sum() >= 5
    df = df.dropna(subset=["d_px", "d_oi"]).reset_index(drop=True)
    print(f"样本：{df['date'].min().date()} ~ {df['date'].max().date()}，n={len(df)} 天，"
          f"熊市 regime 天数={int(df['bear'].sum())}")

    q = np.select(
        [(df["d_px"] > 0) & (df["d_oi"] > 0), (df["d_px"] > 0) & (df["d_oi"] <= 0),
         (df["d_px"] <= 0) & (df["d_oi"] > 0), (df["d_px"] <= 0) & (df["d_oi"] <= 0)],
        ["Q1_涨+OI涨", "Q2_涨+OI跌", "Q3_跌+OI涨", "Q4_跌+OI跌"], default="NA")
    df["quad"] = q

    def table(sub, label):
        base = {h: sub[f"fwd{h}"].dropna() for h in HORIZONS}
        print(f"\n-- {label}（基线 fwd20d {base[20].mean():+.2%}, n={len(sub)}）--")
        for name, g in sub.groupby("quad"):
            line = f"  {name:<12s} n={len(g):<5d}"
            for h in HORIZONS:
                v = g[f"fwd{h}"].dropna()
                line += f" | fwd{h}d {v.mean():+.2%}"
            print(line)
        for name in ["Q2_涨+OI跌", "Q3_跌+OI涨"]:
            g = sub[sub["quad"] == name][f"fwd20"].dropna()
            rest = sub[sub["quad"] != name][f"fwd20"].dropna()
            if len(g) >= 20:
                lo, hi = bootstrap_ci(g.values, rest.values)
                sig = "显著" if (lo > 0 or hi < 0) else "CI含零"
                print(f"  {name} vs 其余 fwd20d 差 95%CI [{lo:+.2%}, {hi:+.2%}] ({sig})")

    table(df, "全样本")
    table(df[df["bear"]], "EMA200 熊市 regime 内（判据3主战场）")

    print("\n-- 判据2：逐年 fwd20d（象限均值 - 当年基线），样本≥15 --")
    df["year"] = df["date"].dt.year
    for name in ["Q2_涨+OI跌", "Q3_跌+OI涨"]:
        rows = []
        for y, g in df.groupby("year"):
            v = g[g["quad"] == name]["fwd20"].dropna()
            if len(v) >= 15:
                rows.append((y, v.mean() - g["fwd20"].dropna().mean()))
        detail = " ".join(f"{y}:{d:+.1%}" for y, d in rows)
        neg = sum(1 for _, d in rows if d > 0) if name.startswith("Q2") else sum(1 for _, d in rows if d < 0)
        # Q2 预期为负（虚涨后回落），Q3 预期为负（新空驱动延续跌）——反转=符号为正
        flips = sum(1 for _, d in rows if d > 0)
        print(f"  {name}: [{detail}] 预期为负，符号为正年数={flips}/{len(rows)}")

    print("\n-- 辅助：强动量变体（|Δpx|、|ΔOI| 均>各自中位）--")
    strong = df[(df["d_px"].abs() > df["d_px"].abs().median()) &
                (df["d_oi"].abs() > df["d_oi"].abs().median())]
    table(strong, "强动量子集")


if __name__ == "__main__":
    main()
