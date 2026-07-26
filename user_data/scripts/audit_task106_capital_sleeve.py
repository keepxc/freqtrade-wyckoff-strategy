"""
TASK 106（预注册，2026-07-25）：独立资金池组合——Donchian% + Carry% 静态分仓
DD/Calmar 检验。预注册见 quant_system_tasks.md TASK 106（判据跑数前冻结）。
纯数据审计，禁碰 strategies/。

子序列函数与既有审计脚本逐行同源：
  - donchian_positions/strat_returns：与 audit_task100/101/103 同源（0.11%/边）
  - carry_sleeve_daily：与 audit_task79_funding_carry.py 的 carry_sleeve()
    逐行同源（win=7d 主规则，进5%/出0%年化，0.11%/边×2腿），按8h事件加总到日频

判据：P1 DD改善方向性(联合区块自举CI下界>0) / P2 Calmar改善方向性(同批自举CI下界>0)
/ P3(报告级)绝对收益代价 / P4(一票否决)combined终值不得转负。
futures主+spot主+futures OOS三窗口(carry 100%覆盖)同时P1+P2+P4通过才可能推进；
OOS-spot(carry覆盖58%)仅报告，不计入判定门槛。w_c∈{0.3,0.5,0.7}三档独立判定。
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
WC_GRID = [0.3, 0.5, 0.7]
BLOCK_LEN = 20
N_BOOT = 1000
SEED = 20260725

EVENTS_PER_DAY = 3
EVENTS_PER_YEAR = EVENTS_PER_DAY * 365
COST_ENTRY_CAP = 0.0011
COST_EXIT_CAP = 0.0011
ENTER_ANN = 0.05
EXIT_ANN = 0.0
WIN_D = 7  # TASK79 主规则

WINDOWS = [
    ("主区间-futures", FUT_PATH, "2022-01-01", "2026-07-04", True),
    ("主区间-spot", SPOT_PATH, "2022-01-01", "2026-07-04", True),
    ("OOS-futures", FUT_PATH, "2021-01-01", "2022-01-01", True),
    ("OOS-spot", SPOT_PATH, "2018-01-01", "2022-01-01", False),  # carry覆盖58%，报告级
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


def block_indices(n, block_len, n_boot, seed):
    n_blocks = int(np.ceil(n / block_len))
    rng = np.random.RandomState(seed)
    max_start = n - block_len
    idx_list = []
    for _ in range(n_boot):
        idx = []
        for _ in range(n_blocks):
            start = rng.randint(0, max_start + 1) if max_start > 0 else 0
            idx.extend(range(start, start + block_len))
        idx_list.append(np.array(idx[:n]))
    return idx_list


def calmar(ret, n_bars):
    term = np.prod(1 + ret) - 1
    ann = (1 + term) ** (365 / n_bars) - 1
    dd = max_dd(ret)
    return term, ann, dd, (ann / dd if dd > 0 else np.nan)


def main():
    print("=" * 96)
    print("TASK 106: 独立资金池组合 Donchian% + Carry% 静态分仓 —— DD/Calmar 联合区块自举检验")
    print(f"w_c_grid={WC_GRID}, 区块长度={BLOCK_LEN}交易日, 重采样={N_BOOT}次, seed={SEED}")
    print("=" * 96)

    carry_daily = carry_sleeve_daily()
    print(f"\ncarry 日频数据范围: {carry_daily.index[0].date()} ~ {carry_daily.index[-1].date()}")

    verdicts = {wc: {} for wc in WC_GRID}

    for tag, path, start, end, required in WINDOWS:
        df_full = load(path)
        pos_full = donchian_positions(df_full, *BASELINE)
        don_ret_full = strat_returns(df_full, pos_full, COST_SIDE)
        don_s = pd.Series(don_ret_full, index=df_full['date'].values)

        mask = (df_full['date'] >= start) & (df_full['date'] < end)
        dates = df_full['date'][mask.values].values
        don_ret = don_s.reindex(dates).fillna(0.0).values
        carry_ret = carry_daily.reindex(pd.to_datetime(dates)).fillna(0.0).values
        n = len(dates)
        coverage = (pd.to_datetime(dates) >= carry_daily.index[0]).mean()

        tag_label = tag + ("" if required else "（报告级，不计入判定门槛）")
        print(f"\n--- {tag_label} [{start}~{end}] n_bars={n} carry覆盖率{coverage:.0%} ---")

        term_b, ann_b, dd_b, cal_b = calmar(don_ret, n)
        print(f"  基线100%Donchian: 终值{term_b:+.2%} 年化{ann_b:+.2%} MaxDD{dd_b:.2%} Calmar{cal_b:.2f}")

        idx_list = block_indices(n, BLOCK_LEN, N_BOOT, SEED)

        for wc in WC_GRID:
            wd = 1 - wc
            combined = wd * don_ret + wc * carry_ret
            term_c, ann_c, dd_c, cal_c = calmar(combined, n)

            diff_dd = np.zeros(N_BOOT)
            diff_cal = np.zeros(N_BOOT)
            for b, idx in enumerate(idx_list):
                don_r = don_ret[idx]
                car_r = carry_ret[idx]
                comb_r = wd * don_r + wc * car_r
                _, ann_br, dd_br, cal_br = calmar(don_r, n)
                _, ann_cr, dd_cr, cal_cr = calmar(comb_r, n)
                diff_dd[b] = dd_br - dd_cr
                diff_cal[b] = (cal_cr - cal_br) if (not np.isnan(cal_cr) and not np.isnan(cal_br)) else np.nan

            dd_ci = np.percentile(diff_dd, [5, 95])
            valid_cal = diff_cal[~np.isnan(diff_cal)]
            cal_ci = np.percentile(valid_cal, [5, 95]) if len(valid_cal) > 0 else (np.nan, np.nan)

            p1 = dd_ci[0] > 0
            p2 = (not np.isnan(cal_ci[0])) and (cal_ci[0] > 0)
            p4 = term_c >= 0

            print(f"  w_c={wc:.1f}: 终值{term_c:+.2%}(代价{term_c-term_b:+.2%}) 年化{ann_c:+.2%} "
                  f"MaxDD{dd_c:.2%} Calmar{cal_c:.2f}")
            print(f"       DD改善90%CI[{dd_ci[0]:+.2%},{dd_ci[1]:+.2%}] P1={'PASS' if p1 else 'FAIL'}  "
                  f"Calmar改善90%CI[{cal_ci[0]:+.2f},{cal_ci[1]:+.2f}] P2={'PASS' if p2 else 'FAIL'}  "
                  f"P4(终值不转负)={'PASS' if p4 else 'FAIL'}")

            if required:
                verdicts[wc][tag] = dict(p1=p1, p2=p2, p4=p4)

    print("\n" + "=" * 96)
    print("判定映射核对：futures主+spot主+futures OOS 三窗口(carry100%覆盖)需同时 P1+P2+P4 全过")
    print("=" * 96)
    for wc in WC_GRID:
        rows = verdicts[wc]
        all_pass = all(v['p1'] and v['p2'] and v['p4'] for v in rows.values())
        detail = "  ".join(f"{t}(P1={v['p1']},P2={v['p2']},P4={v['p4']})" for t, v in rows.items())
        verdict = "列入独立资金池候选(仍需三层校验+再平衡摩擦敏感性检验)" if all_pass else "⭕ 不采纳"
        print(f"  w_c={wc:.1f}: {detail}  → {verdict}")


if __name__ == "__main__":
    main()
