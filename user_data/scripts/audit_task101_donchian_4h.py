"""
TASK 101（预注册，2026-07-18）：Donchian 4H 粒度参数高原确认——1D→4H 结构迁移检验

预注册见 wyckoff_v8_redesign.md TASK 101（判据跑数前冻结）。纯数据审计，
禁碰 strategies/。状态机/权益计算与 audit_task74_donchian_robustness.py 逐行
同口径复用（仅将年化 bars_per_year 参数化，避免4H数据错误年化）。

判据：E1 高原邻域(entry∈(30,80)×exit∈(15,40))通过率≥60%，双数据集均需通过
（与TASK74 D1同标准）/ E2 双数据集最优格cost=0.30%/边仍正 / E3 双数据集最优
格entry/exit差异≤10根 / E4（报告级）换手倍数对比1D基线20/10。
映射：E1+E2+E3全过→4H列入集成候选（仍需三层+OOS）；任一FAIL→⭕4H无稳健结构，
维持1D为唯一时间框架，方向收官。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
FUT = os.path.join(BASE, "data", "binance", "futures")
SPOT = os.path.join(BASE, "data", "binance")

DEFAULT_COST_SIDE = 0.0011
STRESS_COST_SIDE = 0.0030
ENTRY_GRID = [10, 20, 30, 40, 60, 80, 120]
EXIT_GRID = [5, 10, 15, 20, 30, 40, 60]
PLATEAU_ENTRY_RANGE = (30, 80)
PLATEAU_EXIT_RANGE = (15, 40)
PLATEAU_RATIO_MIN = 0.60
BASELINE_1D = (20, 10)
MAX_BEST_CELL_DIFF = 10


def load(path):
    return pd.read_feather(path).sort_values('date').reset_index(drop=True)


def resample_to_4h(df_1h):
    d = df_1h.copy()
    d['date'] = pd.to_datetime(d['date'])
    d = d.set_index('date')
    agg = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}
    if 'volume' in d.columns:
        agg['volume'] = 'sum'
    out = d.resample('4h').agg(agg).dropna(subset=['open', 'high', 'low', 'close'])
    return out.reset_index()


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


def run_equity(df, pos, cost_side, bars_per_year):
    closes = df['close'].values
    ret = np.zeros(len(df))
    ret[1:] = closes[1:] / closes[:-1] - 1
    pos_eff = np.roll(pos, 1)
    pos_eff[0] = 0
    dpos = np.abs(np.diff(np.concatenate([[0], pos])))
    strat_ret = pos_eff * ret - dpos * cost_side
    eq = np.cumprod(1 + strat_ret)
    dd = 1 - eq / np.maximum.accumulate(eq)
    n_years = len(df) / bars_per_year
    ann = (eq[-1]) ** (1 / n_years) - 1 if eq[-1] > 0 else -1.0
    mid_i = len(df) // 2
    h1 = np.prod(1 + strat_ret[:mid_i]) - 1
    h2 = np.prod(1 + strat_ret[mid_i:]) - 1
    n_trades = int((dpos > 0).sum() / 2)
    dd_max = dd.max()
    calmar = ann / dd_max if dd_max > 0 else np.nan
    return eq[-1] - 1, ann, h1, h2, dd_max, n_trades, calmar


def scan_asset(tag, df, bars_per_year):
    print(f"\n{'='*70}\n{tag}: n_bars={len(df)}\n{'='*70}")
    grid_pass, grid_term, grid_calmar, grid_trades = {}, {}, {}, {}
    for ne in ENTRY_GRID:
        row = []
        for nx in EXIT_GRID:
            if nx >= ne:
                row.append(None)
                continue
            pos = donchian_positions(df, ne, nx)
            term, ann, h1, h2, dd, nt, calmar = run_equity(df, pos, DEFAULT_COST_SIDE, bars_per_year)
            passed = (term > 0) and (h1 > 0) and (h2 > 0)
            grid_pass[(ne, nx)] = passed
            grid_term[(ne, nx)] = term
            grid_calmar[(ne, nx)] = calmar
            grid_trades[(ne, nx)] = nt
            row.append((term, passed))
        cells = " ".join(
            ("   .   " if r is None else (f"{r[0]:+6.0%}" if r[1] else f"[{r[0]:+5.0%}]"))
            for r in row
        )
        print(f"  entry={ne:4d}: {cells}")
    print(f"  (exit列: {EXIT_GRID})  [x] = 未通过(终值或半段为负)")

    neigh = [(ne, nx) for ne in ENTRY_GRID for nx in EXIT_GRID
             if PLATEAU_ENTRY_RANGE[0] <= ne <= PLATEAU_ENTRY_RANGE[1]
             and PLATEAU_EXIT_RANGE[0] <= nx <= PLATEAU_EXIT_RANGE[1] and nx < ne]
    n_pass_neigh = sum(1 for k in neigh if grid_pass.get(k))
    ratio = n_pass_neigh / len(neigh) if neigh else 0
    print(f"  高原邻域 entry∈{PLATEAU_ENTRY_RANGE}×exit∈{PLATEAU_EXIT_RANGE}: "
          f"{n_pass_neigh}/{len(neigh)} 格通过 ({ratio:.0%})")

    # 邻域内最优格（按 Calmar，仅在通过格中选）
    neigh_pass = [k for k in neigh if grid_pass.get(k)]
    best_cell = max(neigh_pass, key=lambda k: grid_calmar[k]) if neigh_pass else None
    if best_cell:
        ne, nx = best_cell
        print(f"  邻域内最优格(Calmar) entry={ne}/exit={nx}: 终值{grid_term[best_cell]:+.1%} "
              f"Calmar{grid_calmar[best_cell]:.2f} 换手{grid_trades[best_cell]}笔")

    e1 = ratio >= PLATEAU_RATIO_MIN
    print(f"  E1 高原通过率≥60%: {'PASS' if e1 else 'FAIL'}")
    return dict(tag=tag, ratio=ratio, e1=e1, best_cell=best_cell,
                best_trades=grid_trades.get(best_cell) if best_cell else None,
                grid_pass=grid_pass)


def stress_test(tag, df, best_cell, bars_per_year):
    if best_cell is None:
        print(f"  {tag}: 无最优格，E2 无法评估 → FAIL")
        return False
    ne, nx = best_cell
    pos = donchian_positions(df, ne, nx)
    term, ann, h1, h2, dd, nt, calmar = run_equity(df, pos, STRESS_COST_SIDE, bars_per_year)
    e2 = term > 0
    print(f"  {tag} 最优格 entry={ne}/exit={nx} @ cost=0.30%/边: 终值{term:+.1%} → "
          f"E2 {'PASS' if e2 else 'FAIL'}")
    return e2


def baseline_1d(tag, path):
    df = load(path)
    pos = donchian_positions(df, *BASELINE_1D)
    term, ann, h1, h2, dd, nt, calmar = run_equity(df, pos, DEFAULT_COST_SIDE, 365)
    n_years = len(df) / 365.0
    print(f"  [1D基线 {tag} 20/10] 终值{term:+.1%} Calmar{calmar:.2f} 换手{nt}笔 "
          f"（{n_years:.1f}年，年化换手{nt/n_years:.1f}笔/年）")
    return dict(n_trades=nt, n_years=n_years)


def main():
    print("=" * 70)
    print("TASK 101: Donchian 4H 参数高原确认（预注册，判据冻结）")
    print("=" * 70)

    fut_4h = load(os.path.join(FUT, "BTC_USDT_USDT-4h-futures.feather"))
    spot_1h = load(os.path.join(SPOT, "BTC_USDT-1h.feather"))
    spot_4h = resample_to_4h(spot_1h)
    bars_per_year = 365 * 6

    print("\n[1D基线对照]")
    base_fut = baseline_1d("BTC-futures", os.path.join(FUT, "BTC_USDT_USDT-1d-futures.feather"))
    base_spot = baseline_1d("BTC-spot", os.path.join(SPOT, "BTC_USDT-1d.feather"))

    r_fut = scan_asset("BTC-futures-4H", fut_4h, bars_per_year)
    r_spot = scan_asset("BTC-spot-4H(resampled)", spot_4h, bars_per_year)

    print("\n" + "-" * 70)
    print("E2 成本敏感度压力测试（cost=0.30%/边，各自邻域最优格）")
    print("-" * 70)
    e2_fut = stress_test("BTC-futures-4H", fut_4h, r_fut['best_cell'], bars_per_year)
    e2_spot = stress_test("BTC-spot-4H", spot_4h, r_spot['best_cell'], bars_per_year)

    print("\n" + "-" * 70)
    print("E3 跨数据集最优格收敛度")
    print("-" * 70)
    e3 = False
    if r_fut['best_cell'] and r_spot['best_cell']:
        d_entry = abs(r_fut['best_cell'][0] - r_spot['best_cell'][0])
        d_exit = abs(r_fut['best_cell'][1] - r_spot['best_cell'][1])
        e3 = (d_entry <= MAX_BEST_CELL_DIFF) and (d_exit <= MAX_BEST_CELL_DIFF)
        print(f"  futures最优格{r_fut['best_cell']} vs spot最优格{r_spot['best_cell']}: "
              f"Δentry={d_entry} Δexit={d_exit} (阈值≤{MAX_BEST_CELL_DIFF}) → "
              f"E3 {'PASS' if e3 else 'FAIL'}")
    else:
        print("  某一数据集无最优格 → E3 FAIL")

    print("\n" + "-" * 70)
    print("E4（报告级）换手倍数对比 1D 基线20/10")
    print("-" * 70)
    for r, base, bpy in [(r_fut, base_fut, bars_per_year), (r_spot, base_spot, bars_per_year)]:
        if r['best_trades']:
            n_years_4h = None
        print(f"  {r['tag']}: 最优格换手{r['best_trades']}笔 vs 1D基线{base['n_trades']}笔"
              f"（{base['n_years']:.1f}年）")

    print("\n" + "=" * 70)
    e1_all = r_fut['e1'] and r_spot['e1']
    e2_all = e2_fut and e2_spot
    all_pass = e1_all and e2_all and e3
    print(f"E1(双数据集高原≥60%): futures={r_fut['e1']} spot={r_spot['e1']} → {'PASS' if e1_all else 'FAIL'}")
    print(f"E2(双数据集0.30%成本仍正): futures={e2_fut} spot={e2_spot} → {'PASS' if e2_all else 'FAIL'}")
    print(f"E3(最优格收敛): {'PASS' if e3 else 'FAIL'}")
    if all_pass:
        verdict = "E1+E2+E3 全过 → 4H 结构确认存在稳健高原，列入 Freqtrade 集成候选（仍需三层+OOS）"
    else:
        verdict = "⭕ 4H 粒度未见稳健结构，维持 1D 为核心引擎唯一时间框架，方向收官"
    print(f"\n总判定: {verdict}")


if __name__ == "__main__":
    main()
