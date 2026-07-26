"""
探索性——非结论，禁止引用为证据。不算 TASK，不进证伪表/采纳表。

目的：粗测"独立资金池组合"方向（TASK 82 明确留开的方向：Donchian% + Carry%
静态比例分仓，无信号交互）量级是否值得投入 TASK 105（真实双腿套保执行工程）
和正式 TASK 106 预注册。数据全部复用已存在的审计产物：
  - Donchian 收益序列：donchian_positions/strat_returns（与 TASK100/101/103 同口径）
  - Carry 收益序列：carry_sleeve（与 TASK79 audit_task79_funding_carry.py 逐行同源，
    win_d=7 主规则，ENTER_ANN=5%/EXIT_ANN=0%）

组合方法（简化，如实声明）：假设每日再平衡维持固定权重 w_d/w_c，
组合日收益 = w_d*donchian_ret[t] + w_c*carry_ret[t]（carry pnl 已按8h事件
先加总到日频）。这是"两个子仓位各自独立跑、每天调回目标权重"的近似，
不模拟再平衡摩擦成本，如实标注为简化。

已知口径缺口：carry funding 数据从 2019-09 开始，早于此的窗口（如
OOS-spot 2018-01~2019-09）carry 腿按 0 处理（视为"尚无carry可收"），
会低估该窗口内 carry 的潜在贡献，不是carry本身表现差。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
FUT_PATH = os.path.join(BASE, "data", "binance", "futures", "BTC_USDT_USDT-1d-futures.feather")
SPOT_PATH = os.path.join(BASE, "data", "binance", "BTC_USDT-1d.feather")
FUNDING_PATH = os.path.join(BASE, "data", "binance", "futures", "BTC_USDT_USDT-1h-funding_rate.feather")

COST_SIDE = 0.0011
BASELINE = (20, 10)
SPLITS = [(1.0, 0.0), (0.7, 0.3), (0.5, 0.5), (0.3, 0.7)]

EVENTS_PER_DAY = 3
EVENTS_PER_YEAR = EVENTS_PER_DAY * 365
COST_ENTRY_CAP = 0.0011
COST_EXIT_CAP = 0.0011
ENTER_ANN = 0.05
EXIT_ANN = 0.0
WIN_D = 7  # TASK79 主规则

WINDOWS = [
    ("主区间-futures", FUT_PATH, "2022-01-01", "2026-07-04"),
    ("主区间-spot", SPOT_PATH, "2022-01-01", "2026-07-04"),
    ("OOS-futures", FUT_PATH, "2021-01-01", "2022-01-01"),
    ("OOS-spot", SPOT_PATH, "2018-01-01", "2022-01-01"),
]


def load(path):
    df = pd.read_feather(path).sort_values('date').reset_index(drop=True)
    df['date'] = pd.to_datetime(df['date']).dt.tz_localize(None)
    return df


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


def strat_returns(df, pos, cost_side):
    closes = df['close'].values
    ret = np.zeros(len(df))
    ret[1:] = closes[1:] / closes[:-1] - 1
    pos_eff = np.roll(pos, 1)
    pos_eff[0] = 0
    dpos = np.abs(np.diff(np.concatenate([[0], pos])))
    return pos_eff * ret - dpos * cost_side


def max_dd(strat_ret):
    eq = np.cumprod(1 + strat_ret)
    dd = 1 - eq / np.maximum.accumulate(eq)
    return dd.max()


def carry_sleeve_daily():
    """复刻 audit_task79_funding_carry.py 的 carry_sleeve()，win_d=7 主规则，
    返回按日加总的 pnl Series（index=日期，无 tz）。"""
    df = pd.read_feather(FUNDING_PATH)
    df['date'] = pd.to_datetime(df['date'])
    f = df.set_index('date')['open'].astype(float)

    win = WIN_D * EVENTS_PER_DAY
    trail_ann = f.rolling(win).mean().shift(1) * EVENTS_PER_YEAR
    n = len(f)
    in_pos = np.zeros(n, dtype=bool)
    state = False
    for i in range(n):
        t = trail_ann.iloc[i]
        if np.isnan(t):
            in_pos[i] = state
            continue
        if not state and t >= ENTER_ANN:
            state = True
        elif state and t < EXIT_ANN:
            state = False
        in_pos[i] = state
    pnl = np.where(in_pos, f.values / 2.0, 0.0)
    entries = ((~np.roll(in_pos, 1)) & in_pos)
    entries[0] = in_pos[0]
    exits = (np.roll(in_pos, 1) & (~in_pos))
    exits[0] = False
    pnl = pnl - entries * COST_ENTRY_CAP - exits * COST_EXIT_CAP

    pnl_s = pd.Series(pnl, index=f.index.tz_localize(None))
    daily = pnl_s.groupby(pnl_s.index.normalize()).sum()
    return daily


def main():
    print("=" * 90)
    print("探索性——非结论，禁止引用为证据。独立资金池组合(Donchian% + Carry%)静态比例粗测")
    print(f"权重档: {SPLITS}  carry规则: win={WIN_D}d/进{ENTER_ANN:.0%}/出{EXIT_ANN:.0%}(TASK79主规则)")
    print("=" * 90)

    carry_daily = carry_sleeve_daily()
    print(f"\ncarry 日频数据范围: {carry_daily.index[0].date()} ~ {carry_daily.index[-1].date()}  "
          f"(早于此范围的窗口，carry 腿按0处理，如实声明为口径缺口)")

    for tag, path, start, end in WINDOWS:
        df_full = load(path)
        pos_full = donchian_positions(df_full, *BASELINE)
        don_ret_full = strat_returns(df_full, pos_full, COST_SIDE)
        don_s = pd.Series(don_ret_full, index=df_full['date'].values)

        mask = (df_full['date'] >= start) & (df_full['date'] < end)
        dates = df_full['date'][mask.values].values
        don_ret = don_s.reindex(dates).fillna(0.0).values

        carry_ret = carry_daily.reindex(pd.to_datetime(dates)).fillna(0.0).values
        carry_coverage = (pd.to_datetime(dates) >= carry_daily.index[0]).mean()

        print(f"\n--- {tag} [{start}~{end}] n_bars={len(dates)}  carry数据覆盖率{carry_coverage:.0%} ---")
        don_only_term = np.prod(1 + don_ret) - 1
        don_only_ann = (1 + don_only_term) ** (365 / len(dates)) - 1
        don_only_dd = max_dd(don_ret)
        print(f"  参照 100%Donchian: 终值{don_only_term:+.2%} 年化{don_only_ann:+.2%} "
              f"MaxDD{don_only_dd:.2%} Calmar{(don_only_ann/don_only_dd if don_only_dd>0 else float('nan')):.2f}")

        for w_d, w_c in SPLITS:
            combined = w_d * don_ret + w_c * carry_ret
            term = np.prod(1 + combined) - 1
            ann = (1 + term) ** (365 / len(dates)) - 1
            dd = max_dd(combined)
            calmar = ann / dd if dd > 0 else float('nan')
            print(f"  w_d={w_d:.1f}/w_c={w_c:.1f}: 终值{term:+.2%} 年化{ann:+.2%} "
                  f"MaxDD{dd:.2%} Calmar{calmar:.2f}")

    print("\n" + "=" * 90)
    print("提醒：以上为探索性粗测（简化再平衡假设，未做自举/预注册），仅用于判断是否值得")
    print("往下投入 TASK 105（双腿执行工程）与正式 TASK 106 预注册，不构成任何采纳/证伪结论。")
    print("=" * 90)


if __name__ == "__main__":
    main()
