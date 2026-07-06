"""
TASK 53 第一步：再积累（Re-accumulation）观测性审计（无参数改动，纯统计）

目的：验证"无 SC 前提"下，趋势中横盘结构（无恐慌抛售、无年度新低）是否存在
      可辨识的候选 TR，并统计其 forward return 分布。
门禁：候选 ≥30 个才继续（CLAUDE.md 第一原则 + TASK 53 研究步骤第2步）。

方法论对齐 TASK 22 REDIST 检测器（WyckoffV8.py L482-539）的量化口径，
方向镜像翻转（横盘+高点递降+量萎缩+跌破EMA200 → 横盘+收敛+量萎缩+站上EMA200）：
  1. range_atr7   : 窗口价格区间宽度 / ATR14（横盘紧凑度，同 REDIST）
  2. converge7    : 窗口后半区间宽度 < 前半区间宽度（高低点收敛）
  3. no_supply7   : 下跌日平均成交量 < vol_ma20 * 0.85（供应匮乏，镜像 no_demand7）
  4. above_ema200 : 窗口内全部 bar close > EMA200（维持上升趋势背景）
  5. mom7_up      : close > close.shift(7)（7日净趋势仍上行，非破位反弹）

不涉及策略代码改动，仅审计。
"""
import numpy as np
import pandas as pd

import os

DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "binance", "BTC_USDT-1d.feather")

WIN = 7
MID = WIN // 2
FWD_HORIZONS = [10, 20, 40]


def compute_candidates(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy().reset_index(drop=True)
    ema200 = df["close"].ewm(span=200, adjust=False).mean()
    atr14 = (df["high"] - df["low"]).rolling(14).mean()
    vol_ma20 = df["volume"].rolling(20, min_periods=10).mean()

    n = len(df)
    range_atr = np.full(n, np.nan)
    converge = np.zeros(n, dtype=bool)
    no_supply = np.zeros(n, dtype=bool)
    above_ema = np.zeros(n, dtype=bool)

    for i in range(WIN, n):
        w = df.iloc[i - WIN:i]
        atr = atr14.iloc[i - 1]
        vma = vol_ma20.iloc[i - 1]
        if atr <= 0 or np.isnan(atr):
            continue

        rng = w["high"].max() - w["low"].min()
        range_atr[i] = rng / atr

        h1, l1 = w.iloc[:MID]["high"].max(), w.iloc[:MID]["low"].min()
        h2, l2 = w.iloc[MID:]["high"].max(), w.iloc[MID:]["low"].min()
        converge[i] = (h2 - l2) < (h1 - l1)

        down = w[w["close"] < w["open"]]
        if len(down) >= 2 and vma > 0 and not np.isnan(vma):
            no_supply[i] = down["volume"].mean() < vma * 0.85
        else:
            no_supply[i] = True  # 下跌日极少 = 供应本身不存在

        above_ema[i] = bool((w["close"] > ema200.iloc[i - WIN:i]).all())

    mom7_up = (df["close"] > df["close"].shift(7)).fillna(False).values

    candidate = (
        (range_atr <= 2.5)
        & converge
        & no_supply
        & above_ema
        & mom7_up
    )

    out = df[["date", "close"]].copy()
    out["range_atr7"] = range_atr
    out["converge7"] = converge
    out["no_supply7"] = no_supply
    out["above_ema200"] = above_ema
    out["candidate"] = candidate
    for h in FWD_HORIZONS:
        out[f"fwd{h}d"] = df["close"].shift(-h) / df["close"] - 1
    return out


def dedupe_events(out: pd.DataFrame) -> pd.DataFrame:
    """非重叠事件：candidate 从 False->True 的转折点才计一次，
    且距上一事件末尾（+WIN天冷却）之前不重复计数。"""
    events = []
    cooldown_until = -1
    cand = out["candidate"].values
    for i in range(len(out)):
        if cand[i] and i > cooldown_until:
            events.append(i)
            cooldown_until = i + WIN
    return out.iloc[events]


def main():
    df = pd.read_feather(DATA_PATH)
    df["date"] = pd.to_datetime(df["date"])
    out_full = compute_candidates(df)

    events_full = dedupe_events(out_full)
    print(f"[全历史 2018-01-01~2026-05-05] 候选事件总数（去重后）：{len(events_full)}")

    mask_2021 = (events_full["date"] >= "2021-01-01") & (events_full["date"] < "2022-01-01")
    events_2021 = events_full[mask_2021]
    print(f"[2021 全年] 候选事件数（去重后）：{len(events_2021)}")
    print()

    if len(events_2021) > 0:
        print("=== 2021 年候选事件明细 ===")
        cols = ["date", "close"] + [f"fwd{h}d" for h in FWD_HORIZONS]
        print(events_2021[cols].to_string(index=False))
        print()

    print("=== 2021 年 forward return 分布 ===")
    for h in FWD_HORIZONS:
        vals = events_2021[f"fwd{h}d"].dropna()
        if len(vals) == 0:
            print(f"fwd{h}d: 无有效样本")
            continue
        print(
            f"fwd{h}d: n={len(vals)}, mean={vals.mean():+.2%}, median={vals.median():+.2%}, "
            f"positive_ratio={(vals > 0).mean():.1%}, min={vals.min():+.2%}, max={vals.max():+.2%}"
        )

    print()
    print("=== 全历史（2018-2026）候选事件按年分布，供横向对比 ===")
    events_full = events_full.copy()
    events_full["year"] = events_full["date"].dt.year
    print(events_full.groupby("year").size().to_string())

    print()
    n_2021 = len(events_2021)
    if n_2021 >= 30:
        print(f"[判定] 2021 年候选数 {n_2021} >= 30，通过样本量门禁，可进入第 3 步（区分器设计）")
    else:
        print(f"[判定] 2021 年候选数 {n_2021} < 30，未通过样本量门禁，该 regime 在此窗口内样本过于稀疏，不立项（TASK 53 步骤2）")


if __name__ == "__main__":
    main()
