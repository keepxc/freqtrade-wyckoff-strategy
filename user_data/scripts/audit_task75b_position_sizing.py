"""
TASK 75b（预研）：Donchian 20/10 纯多头波动率目标仓位——Calmar 判据重注册

背景：TASK 75 的 E2 判据（终值/MaxDD 比 ≥ 基线）存在先验可推导的度量偏差——
终值是几何复利（对仓位系数超线性），MaxDD 是单次事件（近似线性），任何 f<1
的降杠杆方案该比值必然劣于 1x 基线，与方案质量无关。TASK 75 按原判据记 ❌
不改写；本 TASK 换用标准 Calmar 比（年化收益/MaxDD）重新预注册。

预注册判据（跑数前写定）：
  F1 DD 改善：MaxDD < 40%（双数据集）
  F2 风险调整收益：Calmar = 年化收益/MaxDD ≥ 1x 基线的 Calmar（同数据集）
  F3 稳健性：≥2 组 (vol_lookback, target_vol) 同时在双数据集通过 F1+F2，
     防孤立参数点
机制与 TASK 75 完全一致：信号层不动（Donchian 20/10 纯多头），信号=多头
期间按 target_vol/realized_vol 反比缩放，封顶 1.0x（只降不加杠杆），每 7
自然日再平衡一次杠杆，成本 0.11%/边按 |Δsized_pos| 计费，shift(1) 因果。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import numpy as np
import pandas as pd

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DATA = [
    ("BTC-spot", os.path.join(BASE, "data", "binance", "BTC_USDT-1d.feather")),
    ("BTC-futures", os.path.join(BASE, "data", "binance", "futures", "BTC_USDT_USDT-1d-futures.feather")),
]
COST_SIDE = 0.0011
N_ENTRY, N_EXIT = 20, 10
REBAL_DAYS = 7
MAX_LEV = 1.0
VOL_LOOKBACKS = [10, 20, 40]
TARGET_VOLS = [0.15, 0.20, 0.25, 0.30]


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
    mid_i = len(df) // 2
    h1 = np.prod(1 + strat_ret[:mid_i]) - 1
    h2 = np.prod(1 + strat_ret[mid_i:]) - 1
    return eq[-1] - 1, ann, h1, h2, dd.max()


def main():
    pass_by_combo = {}
    for tag, path in DATA:
        df = pd.read_feather(path).sort_values('date').reset_index(drop=True)
        base_pos = donchian_positions(df, N_ENTRY, N_EXIT)
        term0, ann0, h1_0, h2_0, dd0 = run_equity(df, base_pos, COST_SIDE)
        calmar0 = ann0 / dd0
        print(f"\n========== {tag} ==========")
        print(f"  1x base: term {term0:+8.1%} | ann {ann0:+6.1%} | MaxDD {dd0:5.1%} | Calmar {calmar0:5.2f}")
        for vl in VOL_LOOKBACKS:
            for tv in TARGET_VOLS:
                sized = sized_positions(df, base_pos, vl, tv, REBAL_DAYS, MAX_LEV)
                term, ann, h1, h2, dd = run_equity(df, sized, COST_SIDE)
                calmar = ann / dd if dd > 0 else float('nan')
                f1 = dd < 0.40
                f2 = calmar >= calmar0
                passed = f1 and f2
                if passed:
                    pass_by_combo.setdefault((vl, tv), []).append(tag)
                print(f"    lb={vl:2d}d tv={tv:.0%}: term {term:+8.1%} | ann {ann:+6.1%} | "
                      f"H1 {h1:+7.1%}/H2 {h2:+7.1%} | MaxDD {dd:5.1%} | Calmar {calmar:5.2f} | "
                      f"{'PASS' if passed else ('F2fail' if f1 else 'F1fail')}")
    print("\n" + "=" * 70)
    robust = {k: v for k, v in pass_by_combo.items() if len(v) == len(DATA)}
    print(f"Both-dataset PASS combos: {sorted(robust.keys())}")
    print(f"F3: {'PASS' if len(robust) >= 2 else 'FAIL'} (need >=2, got {len(robust)})")


if __name__ == "__main__":
    main()
