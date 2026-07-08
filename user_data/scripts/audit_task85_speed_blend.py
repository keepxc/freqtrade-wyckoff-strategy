"""
TASK 85（预研）：速度分散——Donchian 20/10 + 55/20 双速混合 vs 单速基线

论题：Carver 框架核心主张之一：同一趋势规则的多个速度档位半相关，
混合可提升风险调整收益（文献先验 +0.1~0.2 SR 量级 [COMMON]）。
两档参数均为 TASK 74 D1 平台上的已验证点（20/10 经典档、55/20 海龟慢档，
后者亦是 D3 中 ETH 唯一过检的档）——组合两个已验证速度，非参数搜索。

设计（跑数前固定，零调参）：
  资产：BTC futures 全样本（唯一已验证部署资产）
  基线：Donchian 20/10 + 入场定杠杆 vol-target lb20/tv20 封顶1x（现役 DonchianV1）
  变体：50% 资本跑 20/10 + 50% 资本跑 55/20（各自独立同款 vol-target），
        日收益 = 两 sleeve 简单平均（等权日频隐性再平衡，声明同 TASK 84）
  成本：0.11%/边，per sleeve
  口径：几何复合

预注册判据：
  I1 混合 Calmar ≥ 基线 Calmar
  I2 混合 MaxDD ≤ 基线 MaxDD + 2pp
  I3 对半切：两半中混合 Calmar 均 ≥ 基线同半段 Calmar（优势同号）
  全过 → 采纳候选（更新 DonchianV1 为双速）；I1 败 → 速度分散关闭
附带诊断（不参与判定）：两速 sleeve 日收益相关性、55/20 单速 standalone 统计。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import numpy as np
import pandas as pd

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
FUT = os.path.join(BASE, "data", "binance", "futures")
VOL_LB, TARGET_VOL, MAX_LEV = 20, 0.20, 1.0
COST_SIDE = 0.0011
SPEEDS = [(20, 10), (55, 20)]


def sleeve_returns(df, n_entry, n_exit):
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
    return pd.Series(pos_eff * np.nan_to_num(ret) - dpos_eff * COST_SIDE,
                     index=df['date'].values)


def stats(r):
    eq = (1 + r).cumprod()
    dd = (1 - eq / eq.cummax()).max()
    yrs = len(r) / 365.0
    total = eq.iloc[-1] - 1
    ann = (1 + total) ** (1 / yrs) - 1
    return total, ann, dd, (ann / dd if dd > 0 else np.nan)


def main():
    df = pd.read_feather(os.path.join(FUT, "BTC_USDT_USDT-1d-futures.feather"))
    df = df.sort_values('date').reset_index(drop=True)
    df['date'] = pd.to_datetime(df['date']).dt.tz_localize(None)

    fast = sleeve_returns(df, *SPEEDS[0])
    slow = sleeve_returns(df, *SPEEDS[1])
    blend = (fast + slow) / 2.0
    corr = fast.corr(slow)

    mid = len(fast) // 2
    res = {}
    print(f"窗口: {fast.index[0].date()} ~ {fast.index[-1].date()}  {len(fast)} 日")
    for name, r in [("20/10基线", fast), ("55/20慢速", slow), ("双速混合", blend)]:
        t, a, d, c_ = stats(r)
        _, a1, d1, c1 = stats(r.iloc[:mid])
        _, a2, d2, c2 = stats(r.iloc[mid:])
        res[name] = (t, a, d, c_, c1, c2)
        print(f"{name:<9s} 累计 {t:+9.1%} | 年化 {a:+6.1%} | MaxDD {d:5.1%} | "
              f"Calmar {c_:.2f} | 半段 {c1:.2f}/{c2:.2f}")
    print(f"两速 sleeve 相关: {corr:.2f}")

    b, m = res["20/10基线"], res["双速混合"]
    i1 = m[3] >= b[3]
    i2 = m[2] <= b[2] + 0.02
    i3 = m[4] >= b[4] and m[5] >= b[5]
    print("\n" + "=" * 60)
    print(f"I1 混合Calmar≥基线: {'PASS' if i1 else 'FAIL'} ({m[3]:.2f} vs {b[3]:.2f})")
    print(f"I2 MaxDD不劣于+2pp: {'PASS' if i2 else 'FAIL'} ({m[2]:.1%} vs {b[2]:.1%})")
    print(f"I3 对半切优势同号: {'PASS' if i3 else 'FAIL'}")
    print(f"总判定: {'采纳候选(双速)' if (i1 and i2 and i3) else '速度分散关闭' if not i1 else '部分通过，按分级记录'}")


if __name__ == "__main__":
    main()
