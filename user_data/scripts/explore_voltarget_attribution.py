"""
探索性——非结论，禁止引用为证据。

问题：TASK 75b 采纳的 vol-target 仓位（lb=20d/tv=20%）把 Donchian 20/10 的
Calmar 从 X 提到 Y。文献（Kim/Tse/Wald 2016；Georgopoulou & Wang 2016）对
TSMOM 的批判指出：报告的 alpha 主要来自波动率缩放（risk parity）本身，去掉
vol-scaling 后动量信号和买入持有几乎无统计差异。

这里直接在本仓库数据上测这个批判：把同一套 vol-target 仓位公式（TASK75b
sized_positions，逐行复用，未改动）分别套在
  (A) Donchian 20/10 信号（TASK75b 原臂，仅多头期间参与）
  (B) 买入持有（全程参与，无择时信号）
上，对比两者的 Calmar 改善幅度。如果 (B) 的改善和 (A) 接近，说明本仓库的
vol-target 收益主要是风险平价效应，不是 Donchian 突破择时的功劳——这会直接
影响"该不该继续在信号层做文章"的资源分配判断。

不是 TASK，不写入 wyckoff_v8_redesign.md 判定表，不进采纳/证伪表。
沿用 TASK75b 的 shift(1) 因果口径，无未来函数。
"""
import os
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DATA = [
    ("BTC-spot", os.path.join(BASE, "data", "binance", "BTC_USDT-1d.feather")),
    ("BTC-futures", os.path.join(BASE, "data", "binance", "futures", "BTC_USDT_USDT-1d-futures.feather")),
]
COST_SIDE = 0.0011
N_ENTRY, N_EXIT = 20, 10
REBAL_DAYS = 7
MAX_LEV = 1.0
VOL_LOOKBACK, TARGET_VOL = 20, 0.20  # TASK75b 采纳档


def donchian_positions(df, n_entry, n_exit):
    hi = df['high'].rolling(n_entry).max().shift(1)
    lo_exit = df['low'].rolling(n_exit).min().shift(1)
    c = df['close']
    n = len(df)
    pos = np.zeros(n)
    state = 0
    for i in range(n):
        if state == 1 and c.iloc[i] < lo_exit.iloc[i]:
            state = 0
        if state == 0 and not np.isnan(hi.iloc[i]) and c.iloc[i] > hi.iloc[i]:
            state = 1
        pos[i] = state
    return pos


def buy_hold_positions(df):
    return np.ones(len(df))


def sized_positions(df, base_pos, vol_lookback, target_vol, rebal_days, max_lev):
    ret = df['close'].pct_change()
    realized_vol = ret.rolling(vol_lookback).std().shift(1) * np.sqrt(365)
    n = len(df)
    sized = np.zeros(n)
    last_lev = 0.0
    last_rebal = -10**9
    for i in range(n):
        if base_pos[i] == 0:
            sized[i] = 0.0
            last_lev = 0.0
            last_rebal = -10**9
            continue
        if i - last_rebal >= rebal_days or last_lev == 0.0:
            rv = realized_vol.iloc[i]
            lev = 1.0 if (np.isnan(rv) or rv <= 0) else min(max_lev, target_vol / rv)
            last_lev = lev
            last_rebal = i
        sized[i] = last_lev
    return sized


def run_equity(df, pos_notional, cost_side):
    closes = df['close'].values
    ret = np.zeros(len(df))
    ret[1:] = closes[1:] / closes[:-1] - 1
    pos_eff = np.roll(pos_notional, 1)
    pos_eff[0] = 0
    dpos = np.abs(np.diff(np.concatenate([[0], pos_notional])))
    strat_ret = pos_eff * ret - dpos * cost_side
    eq = np.cumprod(1 + strat_ret)
    dd = 1 - eq / np.maximum.accumulate(eq)
    n_years = len(df) / 365.0
    ann = (eq[-1]) ** (1 / n_years) - 1
    return eq[-1] - 1, ann, dd.max()


def main():
    print("探索性——非结论，禁止引用为证据\n")
    for tag, path in DATA:
        df = pd.read_feather(path).sort_values('date').reset_index(drop=True)

        don_pos = donchian_positions(df, N_ENTRY, N_EXIT)
        bh_pos = buy_hold_positions(df)

        term_d0, ann_d0, dd_d0 = run_equity(df, don_pos, COST_SIDE)
        calmar_d0 = ann_d0 / dd_d0
        term_b0, ann_b0, dd_b0 = run_equity(df, bh_pos, COST_SIDE)
        calmar_b0 = ann_b0 / dd_b0

        don_sized = sized_positions(df, don_pos, VOL_LOOKBACK, TARGET_VOL, REBAL_DAYS, MAX_LEV)
        term_d1, ann_d1, dd_d1 = run_equity(df, don_sized, COST_SIDE)
        calmar_d1 = ann_d1 / dd_d1

        bh_sized = sized_positions(df, bh_pos, VOL_LOOKBACK, TARGET_VOL, REBAL_DAYS, MAX_LEV)
        term_b1, ann_b1, dd_b1 = run_equity(df, bh_sized, COST_SIDE)
        calmar_b1 = ann_b1 / dd_b1

        d_gain = calmar_d1 - calmar_d0
        b_gain = calmar_b1 - calmar_b0
        share = b_gain / d_gain if d_gain != 0 else float('nan')

        print(f"========== {tag} ==========")
        print(f"  Donchian  1x: ann {ann_d0:+6.1%} MaxDD {dd_d0:5.1%} Calmar {calmar_d0:5.2f}")
        print(f"  Donchian voltgt: ann {ann_d1:+6.1%} MaxDD {dd_d1:5.1%} Calmar {calmar_d1:5.2f}  (Δ={d_gain:+.2f})")
        print(f"  买入持有  1x: ann {ann_b0:+6.1%} MaxDD {dd_b0:5.1%} Calmar {calmar_b0:5.2f}")
        print(f"  买入持有 voltgt: ann {ann_b1:+6.1%} MaxDD {dd_b1:5.1%} Calmar {calmar_b1:5.2f}  (Δ={b_gain:+.2f})")
        print(f"  vol-scaling 单独贡献占 Donchian 总改善的比例: {share:+.1%}\n")


if __name__ == "__main__":
    main()
