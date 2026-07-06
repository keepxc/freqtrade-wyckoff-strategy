"""
TASK 66（预研）：结构限定 E——用量价结构变量替代日历 E 龄（TASK 64 证伪后的正确重开）

预注册判据（写在跑数之前，不许事后挪门柱）：
  一个结构变量合格，当且仅当：
  1. futures 与 spot 两套数据上，"健康"分层 fwd20d 均值 > "不健康"分层（排序一致）
  2. 逐年分解（样本 ≥15 的年份）中健康-不健康差值无符号反转（TASK 64/funding 教训）
  3. 健康分层样本 ≥30（可交易性）
候选变量（全部为日线收盘可知，因果）：
  A. E内回撤：close / cummax(close|本轮E) - 1（趋势健康度的结构表达）
  B. 突破溢价：close / v8_tr_high - 1（距离结构锚的位置）
  C. EMA20 健康：close > EMA20 且 EMA20 上行（5日斜率为正）
  D. （对照）E 龄 ≤10 天——已知 regime 依赖，作为反面基准
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import numpy as np
import pandas as pd

from user_data.strategies.v8_modules.sc_detector import add_v8_sc_detection
from user_data.strategies.v8_modules.phase_detector import add_v8_phase_detection

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DATA = [
    ("futures", os.path.join(BASE, "data", "binance", "futures", "BTC_USDT_USDT-1d-futures.feather")),
    ("spot", os.path.join(BASE, "data", "binance", "BTC_USDT-1d.feather")),
]


def prep(path):
    df = pd.read_feather(path).sort_values("date").reset_index(drop=True)
    df = add_v8_sc_detection(df)
    df = add_v8_phase_detection(df)
    df["fwd20"] = df["close"].shift(-20) / df["close"] - 1
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()

    ph = df["v8_phase"].fillna("")
    is_e = (ph == "E").values
    run_id = (~pd.Series(is_e)).cumsum().values
    days_in_e = np.zeros(len(df), dtype=int)
    e_cummax = np.full(len(df), np.nan)
    cnt, cmx = 0, np.nan
    for i in range(len(df)):
        if is_e[i]:
            if i > 0 and is_e[i - 1]:
                cnt += 1
                cmx = max(cmx, df["close"].iloc[i])
            else:
                cnt, cmx = 1, df["close"].iloc[i]
            days_in_e[i] = cnt
            e_cummax[i] = cmx
        else:
            cnt, cmx = 0, np.nan

    df["days_in_e"] = days_in_e
    df["e_dd"] = df["close"] / e_cummax - 1          # ≤0，越接近0越健康
    df["bo_premium"] = df["close"] / df["v8_tr_high"] - 1
    df["ema_ok"] = (df["close"] > df["ema20"]) & (df["ema20"] > df["ema20"].shift(5))
    warm = np.asarray(df.index >= 200)
    df["in_e"] = is_e & warm
    return df


def strat_report(df, label, healthy_mask, unhealthy_mask):
    e = df[df["in_e"]]
    h = e.loc[healthy_mask[df["in_e"]].values, "fwd20"].dropna()
    u = e.loc[unhealthy_mask[df["in_e"]].values, "fwd20"].dropna()
    d = h.mean() - u.mean() if len(h) and len(u) else np.nan
    print(f"    {label:<34s} 健康 n={len(h):<5d} {h.mean():+.2%} | 不健康 n={len(u):<5d} "
          f"{u.mean():+.2%} | 差 {d:+.2%}")
    return h, u


def yearly_reversal(df, healthy_mask, unhealthy_mask, label):
    e_mask = df["in_e"].values
    years = pd.to_datetime(df["date"]).dt.year
    rows = []
    for y in sorted(years.unique()):
        m = e_mask & (years == y).values
        h = df.loc[m & healthy_mask.values, "fwd20"].dropna()
        u = df.loc[m & unhealthy_mask.values, "fwd20"].dropna()
        if len(h) >= 15 and len(u) >= 15:
            rows.append((y, len(h), len(u), h.mean() - u.mean()))
    flips = sum(1 for _, _, _, d in rows if d < 0)
    detail = " ".join(f"{y}:{d:+.1%}" for y, _, _, d in rows)
    print(f"    {label:<34s} 年度差值 [{detail}] 反转年数={flips}/{len(rows)}")
    return flips, len(rows)


def main():
    for tag, path in DATA:
        df = prep(path)
        n_e = int(df["in_e"].sum())
        print(f"\n========== {tag}: E 日样本 n={n_e} ==========")
        base = df.loc[df["in_e"], "fwd20"].dropna().mean()
        print(f"  E 内无条件基准 fwd20d: {base:+.2%}")

        print("  -- 分层（E 日内部对比）--")
        specs = {
            "A: E内回撤 ≥-5% vs <-15%": (df["e_dd"] >= -0.05, df["e_dd"] < -0.15),
            "A2: E内回撤 ≥-10% vs <-10%": (df["e_dd"] >= -0.10, df["e_dd"] < -0.10),
            "B: 突破溢价 <25% vs ≥60%": (df["bo_premium"] < 0.25, df["bo_premium"] >= 0.60),
            "C: EMA20健康 vs 不健康": (df["ema_ok"], ~df["ema_ok"]),
            "D(对照): E龄≤10 vs >60": (pd.Series(df["days_in_e"] <= 10, index=df.index) & (df["days_in_e"] > 0),
                                       pd.Series(df["days_in_e"] > 60, index=df.index)),
        }
        for label, (hm, um) in specs.items():
            strat_report(df, label, hm.fillna(False), um.fillna(False))

        print("  -- 逐年反转检验（样本≥15年份）--")
        for label, (hm, um) in specs.items():
            yearly_reversal(df, hm.fillna(False), um.fillna(False), label)


if __name__ == "__main__":
    main()
