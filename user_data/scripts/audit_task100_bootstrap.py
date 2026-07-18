"""
TASK 100 补充复核（2026-07-18）：低频策略分布检验替代笔数门槛

背景：CLAUDE.md 第二层校验的"n≥30笔"门槛是按 WyckoffV8（1H高频）写的，对
DonchianV1/V2Ensemble 这类日线低频策略（年化仅4-5笔）结构性不可达——主区间
(24笔)/futures OOS(5-4笔)/spot OOS(15笔)全部够不着30笔，此前 TASK 100 用笔数
框架判"证据不足"是规则和策略特性不匹配，非真实结论。本脚本按 CLAUDE.md 修订
后的规则，改用成对区块自举（paired block bootstrap）检验同一价格路径下
DonchianV2Ensemble 相对 DonchianV1 的逐日收益差是否统计显著为正。

方法：diff[t] = strat_ret_ensemble[t] - strat_ret_baseline[t]（同价格路径配对，
抵消共同市场波动，只隔离信号差异）；区块长度20个交易日（对齐参数网格量级）；
区块自举重采样2000次；判据 P(总delta>0)≥0.90 视为统计显著。

三个窗口与 Freqtrade 回测口径完全对应：主区间 futures 20220101-20260704，
OOS futures 20210101-20220101，OOS spot 20180101-20220101。
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
VARIANTS = [(15, 8), (20, 10), (30, 15)]
VOTE_MIN = 2
BLOCK_LEN = 20
N_BOOT = 2000
SEED = 20260718

WINDOWS = [
    ("主区间-futures", FUT_PATH, "2022-01-01", "2026-07-04"),
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


def ensemble_positions(df, variants, vote_min):
    votes = np.zeros(len(df))
    for ne, nx in variants:
        votes += donchian_positions(df, ne, nx)
    return (votes >= vote_min).astype(float)


def strat_returns(df, pos, cost_side):
    closes = df['close'].values
    ret = np.zeros(len(df))
    ret[1:] = closes[1:] / closes[:-1] - 1
    pos_eff = np.roll(pos, 1)
    pos_eff[0] = 0
    dpos = np.abs(np.diff(np.concatenate([[0], pos])))
    return pos_eff * ret - dpos * cost_side


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
    p_positive = float((totals > 0).mean())
    return totals, p_positive


def main():
    print("=" * 70)
    print("TASK 100 补充复核：paired block bootstrap（低频策略分布检验）")
    print(f"区块长度={BLOCK_LEN}交易日, 重采样={N_BOOT}次, seed={SEED}")
    print("=" * 70)

    results = []
    for tag, path, start, end in WINDOWS:
        df_full = load(path)
        # 状态机在全历史上跑（与 Freqtrade startup_candle_count 全历史热身口径一致），
        # 窗口切片只在算完 position/return 之后做，避免窗口开头 rolling 缺历史热身
        base_pos_full = donchian_positions(df_full, *BASELINE)
        ens_pos_full = ensemble_positions(df_full, VARIANTS, VOTE_MIN)
        base_ret_full = strat_returns(df_full, base_pos_full, COST_SIDE)
        ens_ret_full = strat_returns(df_full, ens_pos_full, COST_SIDE)

        mask = (df_full['date'] >= start) & (df_full['date'] < end)
        df = df_full[mask].reset_index(drop=True)
        base_ret = base_ret_full[mask.values]
        ens_ret = ens_ret_full[mask.values]
        diff = ens_ret - base_ret

        term_base = np.prod(1 + base_ret) - 1
        term_ens = np.prod(1 + ens_ret) - 1

        totals, p_pos = block_bootstrap_test(diff, BLOCK_LEN, N_BOOT, SEED)
        ci_low, ci_high = np.percentile(totals, [5, 95])

        zero_variance = np.allclose(diff, 0.0)
        if zero_variance:
            verdict = "零方差(窗口内逐日仓位完全无分歧，P统计量不适用)"
        elif p_pos >= 0.90:
            verdict = "PASS(P≥0.90)"
        else:
            verdict = "不显著(P<0.90)"
        print(f"\n--- {tag} [{start}~{end}] n_bars={len(df)} ---")
        print(f"  基线终值{term_base:+.2%}  集成终值{term_ens:+.2%}  实际delta{term_ens-term_base:+.2%}")
        print(f"  自举总delta分布: 均值{totals.mean():+.2%}  90%CI[{ci_low:+.2%}, {ci_high:+.2%}]")
        print(f"  P(总delta>0) = {p_pos:.3f} → {verdict}")
        results.append(dict(tag=tag, p_pos=p_pos, term_base=term_base, term_ens=term_ens,
                             mean_delta=totals.mean(), ci=(ci_low, ci_high), zero_variance=zero_variance))

    print("\n" + "=" * 70)
    print("汇总")
    print("=" * 70)
    for r in results:
        print(f"  {r['tag']}: P(delta>0)={r['p_pos']:.3f}  实际delta={r['term_ens']-r['term_base']:+.2%}")
    informative = [r for r in results if not r.get('zero_variance')]
    all_pass = all(r['p_pos'] >= 0.90 for r in results)
    any_strong_negative = any((not r.get('zero_variance')) and r['p_pos'] <= 0.10 for r in results)
    if all_pass:
        verdict = "三窗口 P≥0.90 全过 → 集成投票相对基线的优势统计显著，满足第二层统计有效性"
    elif any_strong_negative:
        verdict = "存在非零方差窗口 P≤0.10（统计显著为负）→ 有方向性反向证据"
    else:
        verdict = "未全部达到 P≥0.90，且无非零方差窗口显著为负 → 不构成'证据不足'级⭕，是'统计上不显著优于基线'"
    print(f"\n总判定: {verdict}")
    zv = [r['tag'] for r in results if r.get('zero_variance')]
    if zv:
        print(f"（提示: {', '.join(zv)} 窗口 diff 恒为0，逐日仓位与基线完全一致，P值不代表反向证据，已从判负逻辑中剔除）")


if __name__ == "__main__":
    main()
