"""
TASK 103（预注册，2026-07-25）：Donchian 趋势年龄入场过滤——days_above_ma200 阈值检验

预注册见 quant_system_tasks.md TASK 103（判据跑数前冻结）。纯数据审计，
禁碰 strategies/。状态机/权益/自举函数与 audit_task100_bootstrap.py 逐行同源，
仅新增"days_above_ma200 <= X 才允许 don_break 触发"的过滤层。

判据：N1 成对区块自举 CI 下界>0（方向性证据）/ N2 MaxDD不劣化 / N3 半段双正 /
N4（报告级）过滤触发比例。X∈{30,35,40}，四窗口（futures主/spot主/futures OOS/spot OOS）。
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

COST_SIDE = 0.0011
BASELINE = (20, 10)
X_GRID = [30, 35, 40]
MA_WINDOW = 200
BLOCK_LEN = 20
N_BOOT = 2000
SEED = 20260725

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


def days_above_ma200(df, ma_window):
    c = df['close']
    ma = c.rolling(ma_window).mean()
    above = (c > ma).astype(int)
    grp = (above != above.shift(1)).cumsum()
    days = above.groupby(grp).cumcount() + 1
    days = days.where(above == 1, 0)
    return days


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


def donchian_positions_filtered(df, n_entry, n_exit, max_days, days_above):
    hi = df['high'].rolling(n_entry).max().shift(1)
    lo_exit = df['low'].rolling(n_exit).min().shift(1)
    c = df['close']
    n = len(df)
    pos = np.zeros(n)
    state = 0
    n_raw_breaks = 0
    n_filtered_out = 0
    for i in range(n):
        if state == 1 and c.iloc[i] < lo_exit.iloc[i]:
            state = 0
        if state == 0 and not np.isnan(hi.iloc[i]) and c.iloc[i] > hi.iloc[i]:
            n_raw_breaks += 1
            da = days_above.iloc[i]
            if pd.isna(da) or da <= max_days:
                state = 1
            else:
                n_filtered_out += 1
        pos[i] = state
    return pos, n_raw_breaks, n_filtered_out


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


def block_bootstrap_test(diff, block_len, n_boot, seed):
    n = len(diff)
    n_blocks = int(np.ceil(n / block_len))
    rng = np.random.RandomState(seed)
    totals = np.zeros(n_boot)
    max_start = n - block_len
    for b in range(n_boot):
        idx = []
        for _ in range(n_blocks):
            start = rng.randint(0, max_start + 1) if max_start > 0 else 0
            idx.extend(range(start, start + block_len))
        idx = np.array(idx[:n])
        totals[b] = diff[idx].sum()
    return totals


def main():
    print("=" * 78)
    print("TASK 103: Donchian 趋势年龄入场过滤 (days_above_ma200 <= X) vs 基线20/10")
    print(f"X_GRID={X_GRID}, 区块长度={BLOCK_LEN}交易日, 重采样={N_BOOT}次, seed={SEED}")
    print("=" * 78)

    summary = []

    for tag, path, start, end in WINDOWS:
        df_full = load(path)
        days_above_full = days_above_ma200(df_full, MA_WINDOW)
        base_pos_full = donchian_positions(df_full, *BASELINE)
        base_ret_full = strat_returns(df_full, base_pos_full, COST_SIDE)

        mask = (df_full['date'] >= start) & (df_full['date'] < end)
        base_ret = base_ret_full[mask.values]
        term_base = np.prod(1 + base_ret) - 1
        dd_base = max_dd(base_ret)
        mid_i = len(base_ret) // 2
        h1_base = np.prod(1 + base_ret[:mid_i]) - 1
        h2_base = np.prod(1 + base_ret[mid_i:]) - 1

        print(f"\n--- {tag} [{start}~{end}] n_bars={mask.sum()} ---")
        print(f"  基线20/10: 终值{term_base:+.2%} MaxDD{dd_base:.2%} "
              f"半段({h1_base:+.2%}/{h2_base:+.2%})")

        for X in X_GRID:
            filt_pos_full, n_raw, n_filt = donchian_positions_filtered(
                df_full, *BASELINE, X, days_above_full)
            filt_ret_full = strat_returns(df_full, filt_pos_full, COST_SIDE)

            filt_ret = filt_ret_full[mask.values]
            diff = filt_ret - base_ret

            term_filt = np.prod(1 + filt_ret) - 1
            dd_filt = max_dd(filt_ret)
            h1_filt = np.prod(1 + filt_ret[:mid_i]) - 1
            h2_filt = np.prod(1 + filt_ret[mid_i:]) - 1

            zero_variance = np.allclose(diff, 0.0)
            actual_delta = term_filt - term_base

            if zero_variance:
                ci_low, ci_high = 0.0, 0.0
                note = "零方差(窗口内过滤从未触发，与基线逐日仓位完全相同)"
            else:
                totals = block_bootstrap_test(diff, BLOCK_LEN, N_BOOT, SEED)
                ci_low, ci_high = np.percentile(totals, [5, 95])
                note = f"CI下界{'>' if ci_low > 0 else '<='}0"

            n1 = (not zero_variance) and (ci_low > 0)
            n2 = dd_filt <= dd_base + 1e-9
            n3 = (h1_filt > 0) and (h2_filt > 0)

            # 注意：本窗口内的 n_raw/n_filt 是在全历史状态机上统计的触发次数，
            # 报告级用途，按窗口切片近似（全历史口径下的整体过滤强度参考）
            filt_ratio_note = ""
            if n_raw > 0 and (n_filt / n_raw) > 0.5:
                filt_ratio_note = "  [高强度过滤，全历史口径>50%触发被过滤，样本代表性存疑]"

            print(f"  X={X}: 终值{term_filt:+.2%} MaxDD{dd_filt:.2%} "
                  f"半段({h1_filt:+.2%}/{h2_filt:+.2%})  实际delta{actual_delta:+.2%}  "
                  f"90%CI[{ci_low:+.2%}, {ci_high:+.2%}] {note}")
            print(f"       N1(CI下界>0)={'PASS' if n1 else 'FAIL'}  "
                  f"N2(DD不劣化)={'PASS' if n2 else 'FAIL'}  "
                  f"N3(半段双正)={'PASS' if n3 else 'FAIL'}"
                  f"{filt_ratio_note}")

            summary.append(dict(window=tag, X=X, n1=n1, n2=n2, n3=n3,
                                 delta=actual_delta, ci=(ci_low, ci_high),
                                 zero_variance=zero_variance,
                                 n_raw=n_raw, n_filt=n_filt))

    print("\n" + "=" * 78)
    print("判定映射核对：需 futures主区间 + spot主区间 同时 N1+N2+N3 全过 才可能推进")
    print("=" * 78)
    for X in X_GRID:
        rows = [r for r in summary if r['X'] == X]
        main_fut = next(r for r in rows if r['window'] == "主区间-futures")
        main_spot = next(r for r in rows if r['window'] == "主区间-spot")
        both_main_pass = all([main_fut['n1'], main_fut['n2'], main_fut['n3'],
                               main_spot['n1'], main_spot['n2'], main_spot['n3']])
        verdict = "列入Freqtrade集成候选(仍需三层+两个OOS)" if both_main_pass else "⭕ 不采纳"
        print(f"  X={X}: futures主(N1={main_fut['n1']},N2={main_fut['n2']},N3={main_fut['n3']},"
              f"delta={main_fut['delta']:+.2%})  "
              f"spot主(N1={main_spot['n1']},N2={main_spot['n2']},N3={main_spot['n3']},"
              f"delta={main_spot['delta']:+.2%})  → {verdict}")


if __name__ == "__main__":
    main()
