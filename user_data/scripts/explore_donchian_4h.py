"""
探索性——非结论，禁止引用为证据（2026-07-18）

问题：DonchianV1（TASK 72-76 存活策略）目前仅在 1D 跑通并封板。用户提出"切到 4H
找更多机会"——第一原则明确该动机（"更能捕捉机会"）不能单独作为确认性结论的依据，
但可以作为探索性问题："Donchian 突破结构在 4H 粒度上是否存在类似 1D 的参数高原？
换手/成本敏感度是否可承受？" 本脚本只回答这个问题，不写入 TASK 表，不构成采纳依据。

方法：复用 audit_task74_donchian_robustness.py 的因果状态机（donchian_positions/
run_equity 逐行同口径），仅将 n_years 年化改为按 bars_per_year 参数化（1D=365，
4H=365*6=2190），避免对4H数据错误年化。

数据：
- BTC-futures 4H：user_data/data/binance/futures/BTC_USDT_USDT-4h-futures.feather（原生）
- BTC-spot 4H：无原生文件，由 1H spot feather resample 得到（OHLC 标准聚合，无未来函数——
  每根4H bar只用其所属的4根1H bar，resample 本身是因果操作）
- 对照：同数据源 1D 基线（BASELINE=20/10）用于换手/Calmar 直接对比

网格：entry∈[10,20,30,40,60,80,120]（4H bar数），exit∈[5,10,15,20,30,40,60]，
比 1D 网格更宽是因为 4H bar 更密，需要覆盖同等"日历天数"跨度。
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

COST_SIDE = 0.0011
ENTRY_GRID_4H = [10, 20, 30, 40, 60, 80, 120]
EXIT_GRID_4H = [5, 10, 15, 20, 30, 40, 60]
PLATEAU_ENTRY_RANGE_4H = (30, 80)   # 对应 1D 高原 15-40 天 * 6 根/天 附近
PLATEAU_EXIT_RANGE_4H = (15, 40)
MIN_PASS_CELLS = 5
PLATEAU_RATIO_MIN = 0.60

BASELINE_1D = (20, 10)


def load(path):
    return pd.read_feather(path).sort_values('date').reset_index(drop=True)


def resample_to_4h(df_1h):
    d = df_1h.copy()
    d['date'] = pd.to_datetime(d['date'])
    d = d.set_index('date')
    agg = {
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last',
    }
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
    return eq[-1] - 1, ann, h1, h2, dd.max(), n_trades


def scan_grid(tag, df, entry_grid, exit_grid, plateau_entry_range, plateau_exit_range,
              cost_side, bars_per_year):
    print(f"\n{'='*70}\n{tag}: {pd.to_datetime(df['date'].iloc[0])} ~ "
          f"{pd.to_datetime(df['date'].iloc[-1])}  n_bars={len(df)}\n{'='*70}")
    grid_pass, grid_term, grid_trades = {}, {}, {}
    for ne in entry_grid:
        row = []
        for nx in exit_grid:
            if nx >= ne:
                row.append(None)
                continue
            pos = donchian_positions(df, ne, nx)
            term, ann, h1, h2, dd, nt = run_equity(df, pos, cost_side, bars_per_year)
            passed = (term > 0) and (h1 > 0) and (h2 > 0)
            grid_pass[(ne, nx)] = passed
            grid_term[(ne, nx)] = term
            grid_trades[(ne, nx)] = nt
            row.append((term, passed))
        cells = " ".join(
            ("   .   " if r is None else (f"{r[0]:+6.0%}" if r[1] else f"[{r[0]:+5.0%}]"))
            for r in row
        )
        print(f"  entry={ne:4d}: {cells}")
    print(f"  (exit列: {exit_grid})  [x] = 未通过(终值或半段为负)")

    neigh = [(ne, nx) for ne in entry_grid for nx in exit_grid
             if plateau_entry_range[0] <= ne <= plateau_entry_range[1]
             and plateau_exit_range[0] <= nx <= plateau_exit_range[1] and nx < ne]
    n_pass_neigh = sum(1 for k in neigh if grid_pass.get(k))
    ratio = n_pass_neigh / len(neigh) if neigh else 0
    n_pass_total = sum(1 for k, v in grid_pass.items() if v)

    print(f"  高原邻域 entry∈{plateau_entry_range}×exit∈{plateau_exit_range}: "
          f"{n_pass_neigh}/{len(neigh)} 格通过 ({ratio:.0%})")
    print(f"  全网格通过格子总数: {n_pass_total}")

    best_cell, best_term = None, -np.inf
    for k, v in grid_pass.items():
        if v and grid_term[k] > best_term:
            best_cell, best_term = k, grid_term[k]
    if best_cell is not None:
        print(f"  最优格子 entry={best_cell[0]}/exit={best_cell[1]}: "
              f"终值{best_term:+.1%}，换手{grid_trades[best_cell]}笔")

    g1 = ratio >= PLATEAU_RATIO_MIN
    g2 = n_pass_total >= MIN_PASS_CELLS
    print(f"  高原通过率≥60%: {'PASS' if g1 else 'FAIL'} | 通过格子≥5: {'PASS' if g2 else 'FAIL'}")
    return dict(tag=tag, ratio=ratio, n_pass_total=n_pass_total, best_cell=best_cell,
                best_term=best_term, g1=g1, g2=g2,
                best_trades=grid_trades.get(best_cell) if best_cell else None)


def baseline_1d(tag, path):
    df = load(path)
    pos = donchian_positions(df, *BASELINE_1D)
    term, ann, h1, h2, dd, nt = run_equity(df, pos, COST_SIDE, 365)
    calmar = ann / dd if dd > 0 else np.nan
    print(f"  [1D基线对照 {tag} 20/10] 终值{term:+.1%} 年化{ann:+.1%} MaxDD{dd:.1%} "
          f"Calmar{calmar:.2f} 换手{nt}笔  (n_bars={len(df)})")
    return dict(term=term, calmar=calmar, dd=dd, n_trades=nt)


def main():
    print("#" * 70)
    print("探索性脚本 —— 结果不构成结论，不进证伪/采纳表，仅回答:")
    print("Donchian 突破结构在 4H 粒度上是否存在类似 1D 的参数高原？")
    print("#" * 70)

    # --- BTC-futures 4H（原生数据）---
    fut_4h_path = os.path.join(FUT, "BTC_USDT_USDT-4h-futures.feather")
    df_fut_4h = load(fut_4h_path)
    print("\n\n[1D基线对照]")
    baseline_1d("BTC-futures", os.path.join(FUT, "BTC_USDT_USDT-1d-futures.feather"))
    r_fut = scan_grid("BTC-futures-4H", df_fut_4h, ENTRY_GRID_4H, EXIT_GRID_4H,
                       PLATEAU_ENTRY_RANGE_4H, PLATEAU_EXIT_RANGE_4H, COST_SIDE, 365 * 6)

    # --- BTC-spot 4H（由 1H resample，无原生文件）---
    spot_1h_path = os.path.join(SPOT, "BTC_USDT-1h.feather")
    df_spot_1h = load(spot_1h_path)
    df_spot_4h = resample_to_4h(df_spot_1h)
    print("\n\n[1D基线对照]")
    baseline_1d("BTC-spot", os.path.join(SPOT, "BTC_USDT-1d.feather"))
    r_spot = scan_grid("BTC-spot-4H(resampled)", df_spot_4h, ENTRY_GRID_4H, EXIT_GRID_4H,
                        PLATEAU_ENTRY_RANGE_4H, PLATEAU_EXIT_RANGE_4H, COST_SIDE, 365 * 6)

    print("\n" + "=" * 70)
    print("探索性小结（非结论，不构成立项/采纳依据）")
    print("=" * 70)
    for r in (r_fut, r_spot):
        note = "看起来存在高原" if (r['g1'] and r['g2']) else "未见类似1D的稳健高原"
        trades_note = f"，最优格换手{r['best_trades']}笔" if r['best_trades'] else ""
        print(f"  {r['tag']}: 高原比{r['ratio']:.0%} 通过格{r['n_pass_total']} → {note}{trades_note}")
    print("\n若两数据集均显示高原且换手/成本可承受 → 值得转入确认阶段（预注册+三层校验+OOS）。")
    print("若换手暴涨或高原消失 → 与探索前的技术顾虑一致，4H方向大概率不值得投入，无需立项。")


if __name__ == "__main__":
    main()
