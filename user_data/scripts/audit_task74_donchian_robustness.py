"""
TASK 74（预研）：Donchian 纯多头定型后的三项稳健性检验——
参数高原扫描 / 成本敏感度 / 跨资产 OOS（ETH/SOL）

背景：TASK 73 判定经典 Donchian（20/10、55/20，零调参，纯多头）
在 BTC spot+futures 上双双跑赢自定义翻转跟随器且 DD 不更差，触发 C2
"结构检测器判无增量"→采用最简实现。但只测了两组参数、一组成本假设、
单一资产（BTC），存在"孤立幸运点"未证伪的风险。本 TASK 是该方向
判死或判活前的最后把关。

预注册判据（跑数前写定）：
  D1 参数高原：entry∈{10,15,20,25,30,40,50,60} × exit∈{5,8,10,15,20,25,30}，
     BTC spot+futures 双数据集。若"终值为正 且 半段双正"的格子在
     entry∈[15,40]×exit∈[8,20]邻域内占比 ≥ 60%（非孤立在 20/10 一个点），
     判"高原"成立；否则判"孤立峰"，20/10 的采用判定作废。
  D2 成本敏感度：cost_side ∈ {0.03%, 0.06%, 0.11%, 0.20%, 0.30%}，20/10 与
     55/20 在 BTC 双数据集。若 0.20% 档仍终值为正，判"对成本不敏感"；
     若 0.11%→0.20% 翻负，判"成本敏感，需在实盘滑点评估前保留怀疑"。
  D3 跨资产 OOS：同一组零调参参数（20/10、55/20）应用到 ETH、SOL futures
     日线（参数从未在这两个资产上调过）。判据：终值为正 且 半段双正
     视为"通过"；否则记录为"该参数对 BTC 特化，非普适结构"，不构成
     对 BTC 结论的否定，但限制"通用趋势跟随"的推广范围。
成本：默认 0.11%/边（taker 0.06% + 滑点 0.05%），仓位变动 |Δpos| 计费。
全部决策在收盘，指标只用截至前一日数据（shift(1)），因果，纯多头
（做多/空仓两态，不做空）。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import numpy as np
import pandas as pd

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
FUT = os.path.join(BASE, "data", "binance", "futures")

BTC_DATA = [
    ("BTC-spot", os.path.join(BASE, "data", "binance", "BTC_USDT-1d.feather")),
    ("BTC-futures", os.path.join(FUT, "BTC_USDT_USDT-1d-futures.feather")),
]
CROSS_DATA = [
    ("ETH-futures", os.path.join(FUT, "ETH_USDT_USDT-1d-futures.feather")),
    ("SOL-futures", os.path.join(FUT, "SOL_USDT_USDT-1d-futures.feather")),
]
DEFAULT_COST_SIDE = 0.0011
ENTRY_GRID = [10, 15, 20, 25, 30, 40, 50, 60]
EXIT_GRID = [5, 8, 10, 15, 20, 25, 30]
PLATEAU_ENTRY_RANGE = (15, 40)
PLATEAU_EXIT_RANGE = (8, 20)


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
    mid_i = len(df) // 2
    h1 = np.prod(1 + strat_ret[:mid_i]) - 1
    h2 = np.prod(1 + strat_ret[mid_i:]) - 1
    return eq[-1] - 1, h1, h2, dd.max()


def d1_plateau_scan():
    print("\n" + "=" * 70)
    print("D1: 参数高原扫描（entry x exit 网格，BTC spot+futures）")
    print("=" * 70)
    plateau_hits = {}
    for tag, path in BTC_DATA:
        df = load(path)
        print(f"\n--- {tag} ---")
        grid_pass = {}
        for ne in ENTRY_GRID:
            row = []
            for nx in EXIT_GRID:
                if nx >= ne:
                    row.append(None)
                    continue
                pos = donchian_positions(df, ne, nx)
                term, h1, h2, dd = run_equity(df, pos, DEFAULT_COST_SIDE)
                passed = (term > 0) and (h1 > 0) and (h2 > 0)
                grid_pass[(ne, nx)] = passed
                row.append((term, passed))
            cells = " ".join(
                ("  .  " if r is None else (f"{r[0]:+5.0%}" if r[1] else f"[{r[0]:+4.0%}]"))
                for r in row
            )
            print(f"  entry={ne:3d}: {cells}")
        print(f"  (exit列: {EXIT_GRID})  [x] = 未通过(终值或半段为负)")
        # 高原邻域统计
        neigh = [(ne, nx) for ne in ENTRY_GRID for nx in EXIT_GRID
                 if PLATEAU_ENTRY_RANGE[0] <= ne <= PLATEAU_ENTRY_RANGE[1]
                 and PLATEAU_EXIT_RANGE[0] <= nx <= PLATEAU_EXIT_RANGE[1] and nx < ne]
        n_pass = sum(1 for k in neigh if grid_pass.get(k))
        ratio = n_pass / len(neigh) if neigh else 0
        print(f"  高原邻域 entry∈{PLATEAU_ENTRY_RANGE}×exit∈{PLATEAU_EXIT_RANGE}: "
              f"{n_pass}/{len(neigh)} 格通过 ({ratio:.0%})")
        plateau_hits[tag] = ratio
    verdict = all(r >= 0.60 for r in plateau_hits.values())
    print(f"\n  D1 判定: {'高原成立 (PASS)' if verdict else '孤立峰，20/10 采用判定作废 (FAIL)'}"
          f"  {plateau_hits}")
    return verdict


def d2_cost_sensitivity():
    print("\n" + "=" * 70)
    print("D2: 成本敏感度（20/10 与 55/20，BTC spot+futures）")
    print("=" * 70)
    costs = [0.0003, 0.0006, 0.0011, 0.0020, 0.0030]
    all_positive_at_020 = True
    for tag, path in BTC_DATA:
        df = load(path)
        print(f"\n--- {tag} ---")
        for ne, nx in [(20, 10), (55, 20)]:
            pos = donchian_positions(df, ne, nx)
            line = []
            for cs in costs:
                term, h1, h2, dd = run_equity(df, pos, cs)
                line.append(f"cost{cs:.2%}:{term:+.0%}")
                if cs == 0.0020 and term <= 0:
                    all_positive_at_020 = False
            print(f"  Donchian {ne}/{nx}: " + " | ".join(line))
    verdict = all_positive_at_020
    print(f"\n  D2 判定: {'对成本不敏感 (PASS, 0.20%档仍为正)' if verdict else '成本敏感 (FAIL, 0.20%档翻负)'}")
    return verdict


def d3_cross_asset():
    print("\n" + "=" * 70)
    print("D3: 跨资产 OOS（ETH/SOL futures，零调参 20/10 与 55/20）")
    print("=" * 70)
    results = {}
    for tag, path in CROSS_DATA:
        if not os.path.exists(path):
            print(f"\n--- {tag}: 文件不存在，跳过 ---")
            continue
        df = load(path)
        print(f"\n--- {tag}: {pd.to_datetime(df['date'].iloc[0]).date()} ~ "
              f"{pd.to_datetime(df['date'].iloc[-1]).date()} "
              f"Buy&Hold {df['close'].iloc[-1]/df['close'].iloc[0]-1:+.1%} ---")
        for ne, nx in [(20, 10), (55, 20)]:
            pos = donchian_positions(df, ne, nx)
            term, h1, h2, dd = run_equity(df, pos, DEFAULT_COST_SIDE)
            passed = (term > 0) and (h1 > 0) and (h2 > 0)
            results[(tag, ne, nx)] = passed
            print(f"  Donchian {ne:2d}/{nx:2d}: 终值 {term:+8.1%} | H1 {h1:+7.1%} / H2 {h2:+7.1%} | "
                  f"MaxDD {dd:5.1%} | {'PASS' if passed else 'FAIL'}")
    if results:
        n_pass = sum(results.values())
        print(f"\n  D3 判定: {n_pass}/{len(results)} 组合通过。"
              f"{'普适性初步成立' if n_pass == len(results) else '部分/全部未通过——参数对 BTC 特化，不否定BTC结论，但限制推广范围'}")
    return results


def main():
    d1 = d1_plateau_scan()
    d2 = d2_cost_sensitivity()
    d3 = d3_cross_asset()
    print("\n" + "=" * 70)
    print("TASK 74 综合小结")
    print("=" * 70)
    print(f"D1 参数高原: {'PASS' if d1 else 'FAIL'}")
    print(f"D2 成本不敏感: {'PASS' if d2 else 'FAIL'}")
    print(f"D3 跨资产: {sum(d3.values())}/{len(d3)} 通过" if d3 else "D3: 无数据")


if __name__ == "__main__":
    main()
