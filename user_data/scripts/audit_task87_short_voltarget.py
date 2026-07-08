"""
TASK 87（预研）：vol-target 空头模块——修复 TASK 77 两个已知缺陷后的重测

背景（用户挑战成立的部分）：TASK 77 的空头电池有两个流程缺口：
  (1) 空头未上 vol-targeting（多头方差拖累正是靠它压住的，TASK 75b），
      仅做了上限估算（~+1%/年）未跑正式电池；
  (2) 未建模资金费率——永续空头在正费率时收取 funding，是空头独有的
      结构性补贴。
120k→60k 的下跌客观存在，多头系统空仓躲过 -50% 但"空仓是否已是全部"
未在修复上述缺陷后测过。本电池补上。

设计（跑数前固定，零调参——全部参数镜像已验证多头，禁止空头侧搜索）：
  空头入场：close < 过去20日最低（shift1）且 close < EMA200（regime 门控，
            TASK 61 幸存 regime edge 的镜像应用）
  空头退出：close > 过去10日最高（shift1）或 close ≥ EMA200（regime 失效强平）
  仓位：与多头完全同款 vol-target（lb20/tv20 封顶1x，入场锁定）
  funding：空头在场日 pnl += lev × 当日 funding 结算和（正费率空头收取）
  成本：0.11%/边；多空状态机结构互斥（20日低破位前多头必已在10日低离场）
  组合：长-only 现役 sleeve + 空头 sleeve 同账户叠加
  资产：BTC 主判定；ETH 同规则复制（报告，不参与判定）

预注册判据（含效应量下限——TASK 86 教训）：
  K1 组合 Calmar ≥ 长-only Calmar + 0.05（绝对效应量下限）
  K2 空头 sleeve 独立净累计 > 0（含 funding 与成本）且 round-trips ≥ 30
  K3 组合 MaxDD ≤ 长-only MaxDD + 2pp
  K4 对半切：两半组合 Calmar 均 ≥ 长-only 同半段
  全过 → 空头模块采纳候选（进 OOS/影子流程）；K1 或 K2 败 → 空头方向
  维持关闭（本次已含两项修复，再开需真正的新机制）
附带诊断（不判定）：空头 sleeve 逐年累计（检验 120k→60k 段贡献）、
  funding 补贴占空头 pnl 比例、ETH 复制。
跑数前预测申明：不确定方向——tv20 下方差拖累仅 ~2%/年，funding 补贴为正，
  空头有真实机会；死点可能在震荡段 whipsaw 与熊市负费率。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd

from audit_task85_speed_blend import sleeve_returns, stats

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
FUT = os.path.join(BASE, "data", "binance", "futures")
VOL_LB, TARGET_VOL, MAX_LEV = 20, 0.20, 1.0
COST_SIDE = 0.0011
N_ENTRY_S, N_EXIT_S = 20, 10


def daily_funding(sym, dates):
    f = pd.read_feather(os.path.join(FUT, f"{sym}_USDT_USDT-1h-funding_rate.feather"))
    f['date'] = pd.to_datetime(f['date']).dt.tz_localize(None)
    ev = f.set_index('date')['open'].astype(float)
    day_sum = ev.groupby(ev.index.floor('D')).sum()
    return day_sum.reindex(pd.DatetimeIndex(dates)).fillna(0.0).values


def short_sleeve(df, f_day):
    lo_ent = df['low'].rolling(N_ENTRY_S).min().shift(1)
    hi_exit = df['high'].rolling(N_EXIT_S).max().shift(1)
    c = df['close']
    ema200 = c.ewm(span=200, adjust=False).mean()
    n = len(df)
    pos = np.zeros(n)
    state = 0
    for i in range(n):
        if state == 1 and (c.iloc[i] > hi_exit.iloc[i] or c.iloc[i] >= ema200.iloc[i]):
            state = 0
        if (state == 0 and not np.isnan(lo_ent.iloc[i])
                and c.iloc[i] < lo_ent.iloc[i] and c.iloc[i] < ema200.iloc[i]):
            state = 1
        pos[i] = state
    ret = c.pct_change().values
    rv = pd.Series(ret, index=df.index).rolling(VOL_LB).std().shift(1) * np.sqrt(365)
    lev = np.zeros(n)
    cur = 0.0
    for i in range(n):
        if pos[i] == 0:
            cur = 0.0
        elif cur == 0.0:
            r = rv.iloc[i]
            cur = 1.0 if (np.isnan(r) or r <= 0) else min(MAX_LEV, TARGET_VOL / r)
        lev[i] = cur
    sized = pos * lev
    pos_eff = np.roll(sized, 1)
    pos_eff[0] = 0
    dpos = np.abs(np.diff(np.concatenate([[0], sized])))
    dpos_eff = np.roll(dpos, 1)
    dpos_eff[0] = 0
    price_pnl = -pos_eff * np.nan_to_num(ret)
    fund_pnl = pos_eff * f_day
    r = price_pnl + fund_pnl - dpos_eff * COST_SIDE
    entries = int((np.diff(np.concatenate([[0], pos])) > 0).sum())
    return (pd.Series(r, index=df['date'].values), entries,
            fund_pnl.sum(), price_pnl.sum() - (dpos_eff * COST_SIDE).sum())


def run(sym):
    df = pd.read_feather(os.path.join(FUT, f"{sym}_USDT_USDT-1d-futures.feather"))
    df = df.sort_values('date').reset_index(drop=True)
    df['date'] = pd.to_datetime(df['date']).dt.tz_localize(None)
    f_day = daily_funding(sym, df['date'].values)
    long_r = sleeve_returns(df, 20, 10)
    short_r, n_rt, fund_total, price_total = short_sleeve(df, f_day)
    comb = long_r + short_r
    print(f"\n===== {sym}: {long_r.index[0].date()} ~ {long_r.index[-1].date()} =====")
    res = {}
    for name, r in [("长-only", long_r), ("空头sleeve", short_r), ("组合", comb)]:
        t, a, d, c_ = stats(r)
        res[name] = (t, a, d, c_)
        print(f"  {name:<9s} 累计 {t:+8.1%} | 年化 {a:+6.1%} | MaxDD {d:5.1%} | Calmar {c_:.2f}")
    print(f"  空头 round-trips {n_rt} | 价格腿净 {price_total:+.1%} | funding 补贴 {fund_total:+.1%}")
    yr = short_r.groupby(short_r.index.year).apply(lambda s: (1 + s).prod() - 1)
    print("  空头逐年: " + "  ".join(f"{y}:{v:+.1%}" for y, v in yr.items() if abs(v) > 1e-9 or True))
    mid = len(long_r) // 2
    halves = []
    for seg in ((slice(None, mid)), (slice(mid, None))):
        _, _, _, cl = stats(long_r.iloc[seg])
        _, _, _, cc = stats(comb.iloc[seg])
        halves.append((cl, cc))
    print(f"  半段 Calmar 长/组合: H1 {halves[0][0]:.2f}/{halves[0][1]:.2f}  "
          f"H2 {halves[1][0]:.2f}/{halves[1][1]:.2f}")
    return res, n_rt, halves


def main():
    res_btc, n_rt, halves = run("BTC")
    run("ETH")
    long_, short_, comb_ = res_btc["长-only"], res_btc["空头sleeve"], res_btc["组合"]
    k1 = comb_[3] >= long_[3] + 0.05
    k2 = short_[0] > 0 and n_rt >= 30
    k3 = comb_[2] <= long_[2] + 0.02
    k4 = all(cc >= cl for cl, cc in halves)
    print("\n" + "=" * 60)
    print(f"K1 组合Calmar≥长-only+0.05: {'PASS' if k1 else 'FAIL'} ({comb_[3]:.2f} vs {long_[3]:.2f}+0.05)")
    print(f"K2 空头独立净>0且rt≥30: {'PASS' if k2 else 'FAIL'} (净 {short_[0]:+.1%}, rt {n_rt})")
    print(f"K3 MaxDD不劣于+2pp: {'PASS' if k3 else 'FAIL'} ({comb_[2]:.1%} vs {long_[2]:.1%})")
    print(f"K4 对半切组合≥长-only: {'PASS' if k4 else 'FAIL'}")
    verdict = "空头模块采纳候选" if (k1 and k2 and k3 and k4) else "空头方向维持关闭"
    print(f"总判定: {verdict}")


if __name__ == "__main__":
    main()
