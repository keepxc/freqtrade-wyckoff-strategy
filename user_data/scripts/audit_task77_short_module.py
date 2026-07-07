"""
TASK 77（预研）：Donchian 空头模块——EMA200 熊市 regime 门控

背景：对称双向 Donchian 已被 TASK 73 证伪（加空头收益砍八成）；翻转跟随器
空头腿全样本负期望（TASK 72）。未测变体：只在 close < EMA200（TASK 61 幸存
的 regime edge）时允许空头。若此变体也失败，空头方向彻底关闭。

依据（第一原则）：TASK 61 EMA200 persist regime 是因果口径下双显著的幸存
结论；2022（市场 -65%）与 2026（市场 -29%）两段熊市纯多头策略只能空仓等待，
理论上存在可捕获的下行位移。

预注册判据（跑数前写定）：
  G1 组合 Calmar ≥ 纯多头基线 Calmar（双数据集，1x 口径）
  G2 空头腿独立核算：扣费后净期望 > 0 且空头笔数 ≥ 15（样本门槛）
  G3 组合 MaxDD ≤ 纯多头基线 MaxDD + 5pp
  三条全过才进入 vol-target 层与实现；任一失败 → 空头方向关闭，
  纯多头 DonchianV1 维持终版。
规格（零调参，禁止网格搜索空头参数）：
  空头入场：close < 20日低点最小值(shift1) 且 close < EMA200
  空头退出：close > 10日高点最大值(shift1) 或 close ≥ EMA200（regime 失效强平）
  多头逻辑与 DonchianV1 完全一致，多头优先（同bar冲突不可能，数学互斥）。
成本 0.11%/边；决策在收盘、次日生效（shift 口径与 TASK 73/74/75 一致）。
对照组：无门控对称双向（复现 TASK 73 结论，验证分解）。
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


def positions(df, allow_short=False, ema_gate=False):
    hi = df['high'].rolling(N_ENTRY).max().shift(1)
    lo_exit = df['low'].rolling(N_EXIT).min().shift(1)
    lo = df['low'].rolling(N_ENTRY).min().shift(1)
    hi_exit = df['high'].rolling(N_EXIT).max().shift(1)
    ema200 = df['close'].ewm(span=200, adjust=False).mean()
    bear = (df['close'] < ema200).values
    c = df['close'].values
    n = len(df)
    pos = np.zeros(n)
    state = 0
    for i in range(n):
        if state == 1 and c[i] < lo_exit.iloc[i]:
            state = 0
        elif state == -1:
            if c[i] > hi_exit.iloc[i] or (ema_gate and not bear[i]):
                state = 0
        if state == 0:
            if not np.isnan(hi.iloc[i]) and c[i] > hi.iloc[i]:
                state = 1
            elif allow_short and not np.isnan(lo.iloc[i]) and c[i] < lo.iloc[i]:
                if (not ema_gate) or bear[i]:
                    state = -1
        pos[i] = state
    return pos


def evaluate(df, pos, label):
    closes = df['close'].values
    ret = np.zeros(len(df))
    ret[1:] = closes[1:] / closes[:-1] - 1
    pos_eff = np.roll(pos, 1)
    pos_eff[0] = 0
    dpos = np.abs(np.diff(np.concatenate([[0], pos])))
    strat_ret = pos_eff * ret - dpos * COST_SIDE
    eq = np.cumprod(1 + strat_ret)
    dd = 1 - eq / np.maximum.accumulate(eq)
    n_years = len(df) / 365.0
    ann = eq[-1] ** (1 / n_years) - 1
    calmar = ann / dd.max() if dd.max() > 0 else float('nan')
    # 空头腿独立核算
    legs = []
    entry_i = None
    for i in range(len(pos)):
        if pos[i] == -1 and (i == 0 or pos[i-1] != -1):
            entry_i = i
        if entry_i is not None and pos[i] != -1 and (i > 0 and pos[i-1] == -1):
            legs.append(closes[entry_i] / closes[i] - 1 - 2 * COST_SIDE)
            entry_i = None
    if entry_i is not None:
        legs.append(closes[entry_i] / closes[-1] - 1 - 2 * COST_SIDE)
    print(f"    {label:<28s} 终值 {eq[-1]-1:+8.1%} | 年化 {ann:+6.1%} | MaxDD {dd.max():5.1%} | "
          f"Calmar {calmar:5.2f}" + (f" | 空头腿 n={len(legs)} 期望 {np.mean(legs):+.2%} 累计 {np.sum(legs):+.1%}"
                                     if legs else " | 空头腿 n=0"))
    return ann, dd.max(), calmar, legs


def main():
    g1 = g3 = True
    all_short_legs = []
    for tag, path in DATA:
        df = pd.read_feather(path).sort_values('date').reset_index(drop=True)
        print(f"\n========== {tag} ==========")
        ann0, dd0, cal0, _ = evaluate(df, positions(df), "纯多头基线")
        evaluate(df, positions(df, allow_short=True), "对称双向（TASK73对照）")
        ann1, dd1, cal1, legs = evaluate(df, positions(df, allow_short=True, ema_gate=True),
                                         "EMA200门控双向")
        if cal1 < cal0:
            g1 = False
        if dd1 > dd0 + 0.05:
            g3 = False
        all_short_legs += legs
    n_legs = len(all_short_legs)
    exp = np.mean(all_short_legs) if all_short_legs else float('nan')
    g2 = n_legs >= 15 * len(DATA) and exp > 0
    print("\n" + "=" * 70)
    print(f"G1 组合Calmar≥纯多头(双数据集): {'PASS' if g1 else 'FAIL'}")
    print(f"G2 空头腿净期望>0且n≥15/数据集: {'PASS' if g2 else 'FAIL'} "
          f"(合计 n={n_legs}, 期望 {exp:+.2%})")
    print(f"G3 MaxDD不劣于基线+5pp: {'PASS' if g3 else 'FAIL'}")
    print(f"总判定: {'空头模块进入下一层' if (g1 and g2 and g3) else '空头方向关闭，纯多头维持终版'}")


if __name__ == "__main__":
    main()
