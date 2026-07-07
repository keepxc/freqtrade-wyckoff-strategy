"""
TASK 82（预研）：组合元层——Donchian 趋势腿 + 空仓期 carry 腿的账户级装配

设计（只用已验证组件，零新信号）：
  趋势腿：Donchian 20/10 纯多头 + 入场定杠杆 vol-target（lb20/tv20，封顶1x）
          —— TASK 73/74/75b 全链验证
  carry 腿：Donchian 空仓期间，若 7 日均值 funding 年化 ≥5% 则部署
          现货-永续套保收租（TASK 79 验证），<0% 退出；趋势信号出现时
          无条件退出 carry 转趋势腿（趋势腿优先，edge 量级高一个数量级）
  账户会计：趋势腿收益 = lev × 日收益 − 换仓成本（同 TASK 75b）；
          carry 腿收益 = 当日 funding 结算和 / 2（保守双倍资本口径，TASK 79）
          − 切换成本 0.11%/次（进出各一次）
预注册判据：
  G1 组合 Calmar ≥ 纯趋势腿 Calmar（BTC 与 ETH 双资产）
  G2 组合 MaxDD ≤ 纯趋势腿 MaxDD + 2pp（carry 不得引入显著新回撤）
  G3 组合终值 > 纯趋势腿终值（租金净贡献为正，切换成本扣完还有剩）
  全过 → 组合方案定型为部署蓝图；失败 → 维持纯趋势腿，carry 独立运行再评估
因果性：全部决策在收盘（趋势信号/funding 均值都只用已知数据），次日生效。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import numpy as np
import pandas as pd

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
FUT = os.path.join(BASE, "data", "binance", "futures")
ASSETS = ["BTC", "ETH"]
COST_SIDE = 0.0011
CARRY_SWITCH_COST = 0.0011
N_ENTRY, N_EXIT = 20, 10
VOL_LB, TARGET_VOL, MAX_LEV = 20, 0.20, 1.0
FUND_WIN_EVENTS = 21   # 7日×3次结算
ENTER_ANN, EXIT_ANN = 0.05, 0.0
EVENTS_PER_YEAR = 3 * 365


def donchian_positions(df):
    hi = df['high'].rolling(N_ENTRY).max().shift(1)
    lo_exit = df['low'].rolling(N_EXIT).min().shift(1)
    c = df['close']
    pos = np.zeros(len(df))
    state = 0
    for i in range(len(df)):
        if state == 1 and c.iloc[i] < lo_exit.iloc[i]:
            state = 0
        if state == 0 and not np.isnan(hi.iloc[i]) and c.iloc[i] > hi.iloc[i]:
            state = 1
        pos[i] = state
    return pos


def entry_leverage(df, base_pos):
    ret = df['close'].pct_change()
    rv = ret.rolling(VOL_LB).std().shift(1) * np.sqrt(365)
    n = len(df)
    lev = np.zeros(n)
    cur = 0.0
    for i in range(n):
        if base_pos[i] == 0:
            cur = 0.0
        elif cur == 0.0:
            r = rv.iloc[i]
            cur = 1.0 if (np.isnan(r) or r <= 0) else min(MAX_LEV, TARGET_VOL / r)
        lev[i] = cur
    return lev


def daily_carry(funding_path, dates):
    """日级 carry 状态与收益：funding 8h事件 → 日和 + 7日均值门控（shift到次日生效）。"""
    f = pd.read_feather(funding_path)
    f['date'] = pd.to_datetime(f['date']).dt.tz_localize(None)
    f['day'] = f['date'].dt.floor('D')
    ev = f.set_index('date')['open'].astype(float)
    trail_ann = ev.rolling(FUND_WIN_EVENTS).mean() * EVENTS_PER_YEAR
    day_sum = ev.groupby(ev.index.floor('D')).sum()
    day_gate_raw = trail_ann.groupby(trail_ann.index.floor('D')).last()
    # 门控用截至前一日收盘的信息（shift1），当日收益用当日结算和
    out = pd.DataFrame({'carry_pnl': day_sum / 2.0, 'gate_ann': day_gate_raw.shift(1)})
    out = out.reindex(pd.DatetimeIndex(dates)).fillna({'carry_pnl': 0.0})
    return out


def run(asset):
    df = pd.read_feather(os.path.join(FUT, f"{asset}_USDT_USDT-1d-futures.feather"))
    df = df.sort_values('date').reset_index(drop=True)
    df['date'] = pd.to_datetime(df['date']).dt.tz_localize(None)
    c = df['close'].values
    n = len(df)
    ret = np.zeros(n)
    ret[1:] = c[1:] / c[:-1] - 1

    base = donchian_positions(df)
    lev = entry_leverage(df, base)
    sized = base * lev
    pos_eff = np.roll(sized, 1)
    pos_eff[0] = 0
    dpos = np.abs(np.diff(np.concatenate([[0], sized])))
    trend_ret = pos_eff * ret - dpos * COST_SIDE

    carry = daily_carry(os.path.join(FUT, f"{asset}_USDT_USDT-1h-funding_rate.feather"),
                        df['date'].values)
    gate = carry['gate_ann'].values
    carry_pnl = carry['carry_pnl'].values

    # 组合状态机：趋势在场 → 趋势腿；空仓 → carry 门控状态机
    comb_ret = trend_ret.copy()
    in_carry = False
    carry_state = False  # carry 门控（进≥5%年化 / 出<0%）
    for i in range(n):
        trend_active = pos_eff[i] != 0 or dpos[i] > 0
        g = gate[i]
        if not np.isnan(g):
            if not carry_state and g >= ENTER_ANN:
                carry_state = True
            elif carry_state and g < EXIT_ANN:
                carry_state = False
        want_carry = (not trend_active) and carry_state
        if want_carry and not in_carry:
            comb_ret[i] -= CARRY_SWITCH_COST
            in_carry = True
        elif in_carry and not want_carry:
            comb_ret[i] -= CARRY_SWITCH_COST
            in_carry = False
        if in_carry:
            comb_ret[i] += carry_pnl[i]

    def stats(r):
        eq = np.cumprod(1 + r)
        dd = 1 - eq / np.maximum.accumulate(eq)
        yrs = n / 365.0
        ann = eq[-1] ** (1 / yrs) - 1
        return eq[-1] - 1, ann, dd.max(), ann / dd.max()

    t = stats(trend_ret)
    m = stats(comb_ret)
    carry_days = int(sum(1 for i in range(n) if comb_ret[i] != trend_ret[i]))
    print(f"\n===== {asset}: n={n} =====")
    print(f"  纯趋势腿:  终值 {t[0]:+8.1%} | 年化 {t[1]:+6.1%} | MaxDD {t[2]:5.1%} | Calmar {t[3]:.2f}")
    print(f"  组合装配:  终值 {m[0]:+8.1%} | 年化 {m[1]:+6.1%} | MaxDD {m[2]:5.1%} | Calmar {m[3]:.2f}")
    print(f"  carry 部署天数 {carry_days} ({carry_days/n:.0%})  租金净贡献 {m[0]-t[0]:+.1%}")
    return t, m


def main():
    g1 = g2 = g3 = True
    for asset in ASSETS:
        t, m = run(asset)
        if m[3] < t[3]:
            g1 = False
        if m[2] > t[2] + 0.02:
            g2 = False
        if m[0] <= t[0]:
            g3 = False
    print("\n" + "=" * 60)
    print(f"G1 组合Calmar≥纯趋势（双资产）: {'PASS' if g1 else 'FAIL'}")
    print(f"G2 MaxDD不劣于+2pp: {'PASS' if g2 else 'FAIL'}")
    print(f"G3 租金净贡献为正: {'PASS' if g3 else 'FAIL'}")
    print(f"总判定: {'组合方案定型为部署蓝图' if (g1 and g2 and g3) else '维持纯趋势腿'}")


if __name__ == "__main__":
    main()
