"""
TASK 100（预注册，2026-07-18）：Donchian 多窗口集成投票——BTC 核心引擎信号平滑

预注册见 wyckoff_v8_redesign.md TASK 100（判据跑数前冻结）。纯数据审计，
禁碰 strategies/。单窗口状态机/权益计算与 audit_task74_donchian_robustness.py
逐行同口径复用，不改写；仅新增"三变体多数投票"集成层。

判据：H1 Calmar集成≥基线（双数据集）/ H2 MaxDD集成≤基线（双数据集）/
H3 集成对半切双正（双数据集）/ H4（报告级）换手对比。
映射：H1+H2+H3双数据集全过→集成投票列入集成候选（仍需三层+OOS）；
任一FAIL→⭕集成投票无增量，维持单窗口20/10为最终形态。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
BTC_DATA = [
    ("BTC-spot", os.path.join(BASE, "data", "binance", "BTC_USDT-1d.feather")),
    ("BTC-futures", os.path.join(BASE, "data", "binance", "futures", "BTC_USDT_USDT-1d-futures.feather")),
]
COST_SIDE = 0.0011
BASELINE = (20, 10)
VARIANTS = [(15, 8), (20, 10), (30, 15)]
VOTE_MIN = 2  # ≥2/3 一致


def load(path):
    return pd.read_feather(path).sort_values('date').reset_index(drop=True)


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


def run_equity(df, pos, cost_side):
    closes = df['close'].values
    ret = np.zeros(len(df))
    ret[1:] = closes[1:] / closes[:-1] - 1
    pos_eff = np.roll(pos, 1)
    pos_eff[0] = 0
    dpos = np.abs(np.diff(np.concatenate([[0], pos])))
    strat_ret = pos_eff * ret - dpos * cost_side
    eq = np.cumprod(1 + strat_ret)
    dd = 1 - eq / np.maximum.accumulate(eq)
    n_years = len(df) / 365.0
    ann = (eq[-1]) ** (1 / n_years) - 1
    mid_i = len(df) // 2
    h1 = np.prod(1 + strat_ret[:mid_i]) - 1
    h2 = np.prod(1 + strat_ret[mid_i:]) - 1
    n_trades = int((dpos > 0).sum() / 2)  # 开+平各计一次 Δpos，粗略换手估计
    return eq[-1] - 1, ann, h1, h2, dd.max(), n_trades


def ensemble_positions(df, variants, vote_min):
    votes = np.zeros(len(df))
    for ne, nx in variants:
        votes += donchian_positions(df, ne, nx)
    return (votes >= vote_min).astype(float)


def main():
    print("=" * 70)
    print(f"TASK 100: Donchian 集成投票（变体 {VARIANTS}，≥{VOTE_MIN}/3 一致）"
          f" vs 单窗口基线 {BASELINE}")
    print("=" * 70)

    results = {}
    for tag, path in BTC_DATA:
        df = load(path)
        base_pos = donchian_positions(df, *BASELINE)
        term_b, ann_b, h1_b, h2_b, dd_b, nt_b = run_equity(df, base_pos, COST_SIDE)
        calmar_b = ann_b / dd_b

        ens_pos = ensemble_positions(df, VARIANTS, VOTE_MIN)
        term_e, ann_e, h1_e, h2_e, dd_e, nt_e = run_equity(df, ens_pos, COST_SIDE)
        calmar_e = ann_e / dd_e

        print(f"\n--- {tag} ---")
        print(f"  基线20/10:   终值{term_b:+8.1%} 年化{ann_b:+6.1%} MaxDD{dd_b:5.1%} "
              f"Calmar{calmar_b:5.2f} H1{h1_b:+7.1%}/H2{h2_b:+7.1%} 换手{nt_b}")
        print(f"  集成投票:    终值{term_e:+8.1%} 年化{ann_e:+6.1%} MaxDD{dd_e:5.1%} "
              f"Calmar{calmar_e:5.2f} H1{h1_e:+7.1%}/H2{h2_e:+7.1%} 换手{nt_e}")

        h1 = calmar_e >= calmar_b
        h2 = dd_e <= dd_b + 1e-9  # 允许持平；容差防浮点噪声误判（预注册H2本即"允许持平"）
        h3 = h1_e > 0 and h2_e > 0
        print(f"  H1 Calmar集成≥基线: {'PASS' if h1 else 'FAIL'} | "
              f"H2 MaxDD集成≤基线: {'PASS' if h2 else 'FAIL'} | "
              f"H3 集成对半切双正: {'PASS' if h3 else 'FAIL'}")
        print(f"  H4(报告级) 换手变化: {nt_e - nt_b:+d} 笔 "
              f"({'集成换手更多' if nt_e > nt_b else '集成换手更少或持平'})")
        results[tag] = dict(h1=h1, h2=h2, h3=h3)

    print("\n" + "=" * 70)
    all_pass = all(r["h1"] and r["h2"] and r["h3"] for r in results.values())
    if all_pass:
        verdict = "H1+H2+H3 双数据集全过 → 集成投票列入策略集成候选（仍需组合级三层+OOS，本审计不构成采纳）"
    else:
        verdict = "⭕ 集成投票无增量，维持单窗口 20/10 为核心引擎最终形态"
    print(f"总判定: {verdict}")
    for tag, r in results.items():
        print(f"  {tag}: H1={r['h1']} H2={r['h2']} H3={r['h3']}")


if __name__ == "__main__":
    main()
