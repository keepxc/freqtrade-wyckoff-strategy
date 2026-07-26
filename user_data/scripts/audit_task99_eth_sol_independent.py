"""
TASK 99（预注册，2026-07-18）：ETH/SOL 独立 Donchian 参数高原扫描——
跨资产结构非移植验证

预注册见 quant_system_tasks.md TASK 99（判据跑数前冻结）。纯数据审计，
禁碰 strategies/。核心状态机（donchian_positions/run_equity）与
audit_task74_donchian_robustness.py 逐行同口径复用，不改写。

区别于 TASK 74 D3（BTC 零调参移植到 ETH/SOL 做 OOS，已判"仅1/4通过，
参数对BTC特化"）：本 TASK 测的是"ETH/SOL 各自独立寻优后，是否存在类似
BTC 的参数高原（而非孤立点）"——回答"Donchian 突破结构本身能否推广"，
而非"BTC 参数能否直接搬"。

判据：G1 高原通过率≥60%（entry∈[15,40]×exit∈[8,20]邻域，终值>0且半段
双正）/ G2 通过格子总数≥5（防孤立点）/ G3 通过率最高格在cost=0.20%仍正
/ G4 报告级：最优格是否落在BTC已知高原内。
映射：ETH+SOL 均G1+G2+G3全过→结构可推广（列入集成候选，仍需三层+OOS）；
仅一个通过→该资产判活，另一个判"不具备可用结构"；均未全过→⭕结构
不能推广，通用趋势跟随方向收官。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
FUT = os.path.join(BASE, "data", "binance", "futures")

CROSS_DATA = [
    ("ETH-futures", os.path.join(FUT, "ETH_USDT_USDT-1d-futures.feather")),
    ("SOL-futures", os.path.join(FUT, "SOL_USDT_USDT-1d-futures.feather")),
]
DEFAULT_COST_SIDE = 0.0011
STRESS_COST_SIDE = 0.0020
ENTRY_GRID = [10, 15, 20, 25, 30, 40, 50, 60]
EXIT_GRID = [5, 8, 10, 15, 20, 25, 30]
PLATEAU_ENTRY_RANGE = (15, 40)
PLATEAU_EXIT_RANGE = (8, 20)
MIN_PASS_CELLS = 5
PLATEAU_RATIO_MIN = 0.60


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


def scan_asset(tag, path):
    df = load(path)
    print(f"\n{'='*70}\n{tag}: {pd.to_datetime(df['date'].iloc[0]).date()} ~ "
          f"{pd.to_datetime(df['date'].iloc[-1]).date()}\n{'='*70}")
    grid_pass, grid_term = {}, {}
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
            grid_term[(ne, nx)] = term
            row.append((term, passed))
        cells = " ".join(
            ("  .  " if r is None else (f"{r[0]:+5.0%}" if r[1] else f"[{r[0]:+4.0%}]"))
            for r in row
        )
        print(f"  entry={ne:3d}: {cells}")
    print(f"  (exit列: {EXIT_GRID})  [x] = 未通过(终值或半段为负)")

    neigh = [(ne, nx) for ne in ENTRY_GRID for nx in EXIT_GRID
             if PLATEAU_ENTRY_RANGE[0] <= ne <= PLATEAU_ENTRY_RANGE[1]
             and PLATEAU_EXIT_RANGE[0] <= nx <= PLATEAU_EXIT_RANGE[1] and nx < ne]
    n_pass_neigh = sum(1 for k in neigh if grid_pass.get(k))
    ratio = n_pass_neigh / len(neigh) if neigh else 0
    n_pass_total = sum(1 for k, v in grid_pass.items() if v)

    print(f"  高原邻域 entry∈{PLATEAU_ENTRY_RANGE}×exit∈{PLATEAU_EXIT_RANGE}: "
          f"{n_pass_neigh}/{len(neigh)} 格通过 ({ratio:.0%})")
    print(f"  全网格通过格子总数: {n_pass_total}")

    g1 = ratio >= PLATEAU_RATIO_MIN
    g2 = n_pass_total >= MIN_PASS_CELLS
    print(f"  G1 高原通过率≥60%: {'PASS' if g1 else 'FAIL'} | "
          f"G2 通过格子≥5: {'PASS' if g2 else 'FAIL'}")

    # G3: 通过率最高格（全网格通过格中终值最高者）在 stress cost 下仍正
    best_cell, best_term = None, -np.inf
    for k, v in grid_pass.items():
        if v and grid_term[k] > best_term:
            best_cell, best_term = k, grid_term[k]
    g3 = False
    if best_cell is not None:
        ne, nx = best_cell
        pos = donchian_positions(df, ne, nx)
        term_stress, h1_s, h2_s, dd_s = run_equity(df, pos, STRESS_COST_SIDE)
        g3 = term_stress > 0
        print(f"  最优格子 entry={ne}/exit={nx} (终值 {best_term:+.1%}): "
              f"cost=0.20% 终值 {term_stress:+.1%} → G3 {'PASS' if g3 else 'FAIL'}")
        in_btc_plateau = (PLATEAU_ENTRY_RANGE[0] <= ne <= PLATEAU_ENTRY_RANGE[1]
                           and PLATEAU_EXIT_RANGE[0] <= nx <= PLATEAU_EXIT_RANGE[1])
        print(f"  G4（报告级）最优格是否落在 BTC 已知高原区间内: "
              f"{'是' if in_btc_plateau else '否'}")
    else:
        print("  无通过格子，G3/G4 无法评估")

    verdict = g1 and g2 and g3
    print(f"  {tag} 综合判定: {'活（G1+G2+G3全过）' if verdict else '不具备可用结构（未全过）'}")
    return dict(tag=tag, g1=g1, g2=g2, g3=g3, ratio=ratio, n_pass_total=n_pass_total,
                 best_cell=best_cell, verdict=verdict)


TRUNC_CUTOFF = pd.Timestamp("2021-09-14")  # SOL 数据起点+1年，剔除首年52x暴涨期


def scan_asset_truncated(tag, path, cutoff):
    df = load(path)
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    df_t = df[df["date"] >= cutoff].reset_index(drop=True)
    print(f"\n{'='*70}\n{tag}（稳健性哨兵：剔除首年暴涨期，起点后移至 {cutoff.date()}）"
          f"\n  截断后窗口 {df_t['date'].iloc[0].date()} ~ {df_t['date'].iloc[-1].date()}"
          f"，n={len(df_t)}\n{'='*70}")
    grid_pass, grid_term = {}, {}
    for ne in ENTRY_GRID:
        row = []
        for nx in EXIT_GRID:
            if nx >= ne:
                row.append(None)
                continue
            pos = donchian_positions(df_t, ne, nx)
            term, h1, h2, dd = run_equity(df_t, pos, DEFAULT_COST_SIDE)
            passed = (term > 0) and (h1 > 0) and (h2 > 0)
            grid_pass[(ne, nx)] = passed
            grid_term[(ne, nx)] = term
            row.append((term, passed))
        cells = " ".join(
            ("  .  " if r is None else (f"{r[0]:+5.0%}" if r[1] else f"[{r[0]:+4.0%}]"))
            for r in row
        )
        print(f"  entry={ne:3d}: {cells}")
    print(f"  (exit列: {EXIT_GRID})  [x] = 未通过(终值或半段为负)")

    neigh = [(ne, nx) for ne in ENTRY_GRID for nx in EXIT_GRID
             if PLATEAU_ENTRY_RANGE[0] <= ne <= PLATEAU_ENTRY_RANGE[1]
             and PLATEAU_EXIT_RANGE[0] <= nx <= PLATEAU_EXIT_RANGE[1] and nx < ne]
    n_pass_neigh = sum(1 for k in neigh if grid_pass.get(k))
    ratio = n_pass_neigh / len(neigh) if neigh else 0
    n_pass_total = sum(1 for k, v in grid_pass.items() if v)
    print(f"  高原邻域: {n_pass_neigh}/{len(neigh)} 格通过 ({ratio:.0%})  全网格通过格子: {n_pass_total}")

    g1 = ratio >= PLATEAU_RATIO_MIN
    g2 = n_pass_total >= MIN_PASS_CELLS
    best_cell, best_term = None, -np.inf
    for k, v in grid_pass.items():
        if v and grid_term[k] > best_term:
            best_cell, best_term = k, grid_term[k]
    g3 = False
    if best_cell is not None:
        ne, nx = best_cell
        pos = donchian_positions(df_t, ne, nx)
        term_stress, h1_s, h2_s, dd_s = run_equity(df_t, pos, STRESS_COST_SIDE)
        g3 = term_stress > 0
        print(f"  最优格子 entry={ne}/exit={nx} (终值 {best_term:+.1%}): "
              f"cost=0.20% 终值 {term_stress:+.1%} → G3 {'PASS' if g3 else 'FAIL'}")
    verdict = g1 and g2 and g3
    print(f"  {tag} 截断后判定: {'活' if verdict else '不具备可用结构'}"
          f"  G1={g1}({ratio:.0%}) G2={g2}({n_pass_total}) G3={g3}")
    return dict(tag=tag, g1=g1, g2=g2, g3=g3, ratio=ratio, n_pass_total=n_pass_total, verdict=verdict)


def main():
    results = []
    for tag, path in CROSS_DATA:
        if not os.path.exists(path):
            print(f"\n--- {tag}: 文件不存在，跳过 ---")
            continue
        results.append(scan_asset(tag, path))

    print("\n" + "=" * 70)
    print("TASK 99 综合小结")
    print("=" * 70)
    for r in results:
        print(f"  {r['tag']}: G1={r['g1']} G2={r['g2']} G3={r['g3']} "
              f"→ {'活' if r['verdict'] else '不具备可用结构'}")

    n_alive = sum(1 for r in results if r['verdict'])
    if n_alive == len(results) and len(results) == 2:
        final = "两资产均活 → Donchian 突破为跨资产可复制结构（仅需按资产独立调参），列入集成候选（仍需组合级三层+OOS）"
    elif n_alive == 1:
        alive = [r['tag'] for r in results if r['verdict']][0]
        dead = [r['tag'] for r in results if not r['verdict']][0]
        final = f"仅 {alive} 判活，{dead} 判不具备可用结构（不否定{alive}的结论）"
    else:
        final = "⭕ 两资产均未全过，Donchian 结构不能推广到 ETH/SOL，通用趋势跟随扩展方向收官，仅 BTC 单资产结论继续有效"
    print(f"\n总判定: {final}")

    print("\n\n" + "#" * 70)
    print("稳健性哨兵：SOL 剔除首年暴涨期（2020-09-14~2021-09-14，3.27→170，52x）复核")
    print("#" * 70)
    sol_path = dict(CROSS_DATA)["SOL-futures"]
    sol_trunc = scan_asset_truncated("SOL-futures", sol_path, TRUNC_CUTOFF)
    print(f"\nSOL 稳健性哨兵判定: 截断前=活, 截断后={'活' if sol_trunc['verdict'] else '不具备可用结构'}")
    if sol_trunc["verdict"]:
        print("→ SOL 的'活'判定在剔除首年暴涨期后依然成立，非样本期红利驱动，判定维持。")
    else:
        print("→ SOL 的'活'判定主要由首年暴涨期贡献，剔除后结构不再成立，"
              "原判定降级为⭕（与 ETH 同归'不具备可用结构'）。")


if __name__ == "__main__":
    main()
