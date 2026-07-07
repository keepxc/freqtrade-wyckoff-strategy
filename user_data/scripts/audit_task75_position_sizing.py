"""
TASK 75（预研）：Donchian 20/10 纯多头仓位方案——波动率目标降 DD

背景：TASK 73/74 判定经典 Donchian 20/10 纯多头是全战役唯一存活策略，
但 1x 名义仓位 MaxDD 54.1%(spot)/54.2%(futures)。本 TASK 只做仓位管理
（信号层完全不动，entry/exit 沿用 TASK74 的 donchian_positions），
用波动率目标（vol targeting）把仓位在信号=多头期间按已实现波动率
反比缩放，只降杠杆不加杠杆（cap at 1.0x，不引入保证金/爆仓风险）。

理论依据 [COMMON]：时序动量文献（Moskowitz/Ooi/Pedersen 2012 等）标准
做法——用滚动已实现波动率反比定仓位，使组合波动率贴近目标，能在不改变
信号胜率结构的前提下压低回撤（因为最大回撤往往发生在高波动期）。

预注册判据（跑数前写定）：
  E1 DD 改善：目标波动率方案的 MaxDD 应显著低于 1x 基线（<40%，
     双数据集）
  E2 收益保留：终值不应对基线造成过度损耗——收益/回撤比（终值/MaxDD）
     应优于或持平 1x 基线（不能"降DD但把edge也降没了"）
  E3 稳健性：至少 2 组 (vol_lookback, target_vol) 组合同时满足 E1+E2，
     防止孤立参数点
成本：0.11%/边，仓位变动用 |Δ(sized_pos)| 计费（含信号进出与杠杆再平衡）。
杠杆再平衡频率：每周（7 个自然日）一次，降低换手成本；杠杆封顶 1.0x
（只降险不加杠杆）。全部用 shift(1) 因果已实现波动率，无未来函数。
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
            if np.isnan(rv) or rv <= 0:
                lev = 1.0
            else:
                lev = min(max_lev, target_vol / rv)
            last_lev = lev
            last_rebal = i
        sized[i] = last_lev
    return sized


def run_equity(df, pos_signal_notional, cost_side):
    closes = df['close'].values
    ret = np.zeros(len(df))
    ret[1:] = closes[1:] / closes[:-1] - 1
    pos_eff = np.roll(pos_signal_notional, 1)
    pos_eff[0] = 0
    dpos = np.abs(np.diff(np.concatenate([[0], pos_signal_notional])))
    strat_ret = pos_eff * ret - dpos * cost_side
    eq = np.cumprod(1 + strat_ret)
    dd = 1 - eq / np.maximum.accumulate(eq)
    mid_i = len(df) // 2
    h1 = np.prod(1 + strat_ret[:mid_i]) - 1
    h2 = np.prod(1 + strat_ret[mid_i:]) - 1
    return eq[-1] - 1, h1, h2, dd.max()


def main():
    survivors = []
    for tag, path in DATA:
        df = pd.read_feather(path).sort_values('date').reset_index(drop=True)
        base_pos = donchian_positions(df, N_ENTRY, N_EXIT)
        term0, h1_0, h2_0, dd0 = run_equity(df, base_pos, COST_SIDE)
        print(f"\n========== {tag} ==========")
        print(f"  1x 基线: 终值 {term0:+8.1%} | H1 {h1_0:+7.1%} / H2 {h2_0:+7.1%} | MaxDD {dd0:5.1%} | "
              f"收益/DD比 {term0/dd0:5.2f}")
        for vl in VOL_LOOKBACKS:
            for tv in TARGET_VOLS:
                sized = sized_positions(df, base_pos, vl, tv, REBAL_DAYS, MAX_LEV)
                term, h1, h2, dd = run_equity(df, sized, COST_SIDE)
                e1 = dd < 0.40
                e2 = (term / dd if dd > 0 else -999) >= (term0 / dd0)
                passed = e1 and e2
                if passed:
                    survivors.append((tag, vl, tv, term, dd))
                flag = "PASS" if passed else ("dd_ok" if e1 else ("ratio_ok" if e2 else ""))
                print(f"    lookback={vl:2d}d target_vol={tv:.0%}: 终值 {term:+8.1%} | "
                      f"H1 {h1:+7.1%}/H2 {h2:+7.1%} | MaxDD {dd:5.1%} | 收益/DD比 {(term/dd if dd>0 else float('nan')):5.2f} | {flag}")
    print("\n" + "=" * 70)
    print(f"E1+E2 双通过组合数: {len(survivors)} / {len(DATA) * len(VOL_LOOKBACKS) * len(TARGET_VOLS)}")
    by_combo = {}
    for tag, vl, tv, term, dd in survivors:
        by_combo.setdefault((vl, tv), []).append(tag)
    robust = {k: v for k, v in by_combo.items() if len(v) == len(DATA)}
    print(f"双数据集同时通过的 (lookback, target_vol) 组合: {list(robust.keys())}")
    e3 = len(robust) >= 2
    print(f"E3 稳健性判定: {'PASS' if e3 else 'FAIL'} (需要≥2组双数据集通过)")


if __name__ == "__main__":
    main()
