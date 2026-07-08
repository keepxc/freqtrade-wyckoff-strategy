"""
TASK 84（预研）：广度分散——DonchianV1 规则多资产组合 vs BTC-only

论题：趋势跟随文献的最大收益杠杆是广度（MOP 2012 Sharpe 1.28 来自 58 品种
分散，非单品种信号强度）。同一套已验证规则（Donchian 20/10 纯多 +
入场定杠杆 vol-target lb20/tv20 封顶1x）独立跑 15 币、等资本分仓，
组合级风险调整收益是否优于 BTC 单资产。

与已关闭方向的区分：
  - 非 C族（TASK 83 ❌）：无截面排名，每资产独立 TSM 信号
  - 非 TASK 74 D3 翻案：D3 判的是"单资产 standalone 过不过判据"，
    本题是"组合分散收益能否覆盖弱资产负贡献"——未测过的新假设
死亡先验 [COMMON]：crypto 币间相关 0.8+，广度分散收益远低于跨资产类期货；
本电池就是要量化这一点。诊断指标：sleeve 日收益平均两两相关。

设计（跑数前固定，零调参）：
  宇宙：TASK 83 同 15 币（幸存者偏差申明同 TASK 83——alt sleeve 收益被
        结构性抬高，若组合仅靠 alt 牛市段取胜则结论不可部署）
  窗口：2020-10-01（15 币全部上市后）~ 数据尾；BTC-only 基线同窗口重算
  规则：每 sleeve 独立 Donchian 20/10 + 入场定杠杆 vol-target（DonchianV1 全套）
  组合：等资本 1/15 固定分仓，无跨 sleeve 再平衡（各 sleeve 独立复利，
        组合收益 = sleeve 简单平均——即定期隐性再平衡到等权，日频，声明）
  成本：0.11%/边 × 仓位变动，per sleeve
  口径：几何复合

预注册判据：
  H1 组合 Calmar ≥ BTC-only Calmar（同窗口）
  H2 组合 MaxDD ≤ BTC-only MaxDD + 2pp
  H3 对半切：两半中组合 (半段年化/半段MaxDD) 均 ≥ BTC-only 同指标（优势同号）
  全过 → 采纳候选（部署前另需影子运行，幸存者偏差不可回测消除）；
  H1 败 → 广度方向关闭（机制：相关性过高）
附带诊断（全报告，不参与判定）：各 sleeve 独立统计、平均两两相关、
  剔除 BTC 的 14 币组合（分散收益来自 alt 还是 BTC beta）。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import numpy as np
import pandas as pd

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
FUT = os.path.join(BASE, "data", "binance", "futures")
UNIVERSE = ["BTC", "ETH", "BNB", "XRP", "ADA", "SOL", "DOGE", "DOT",
            "LTC", "LINK", "AVAX", "TRX", "BCH", "ETC", "ATOM"]
START = "2020-10-01"
N_ENTRY, N_EXIT = 20, 10
VOL_LB, TARGET_VOL, MAX_LEV = 20, 0.20, 1.0
COST_SIDE = 0.0011


def sleeve_returns(df):
    """单资产 DonchianV1 规则日收益（与 TASK 82 趋势腿同实现）。"""
    hi = df['high'].rolling(N_ENTRY).max().shift(1)
    lo_exit = df['low'].rolling(N_EXIT).min().shift(1)
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
    r = pos_eff * np.nan_to_num(ret) - dpos_eff * COST_SIDE
    return pd.Series(r, index=df['date'].values)


def stats(r):
    eq = (1 + r).cumprod()
    dd = (1 - eq / eq.cummax()).max()
    yrs = len(r) / 365.0
    total = eq.iloc[-1] - 1
    ann = (1 + total) ** (1 / yrs) - 1
    return total, ann, dd, (ann / dd if dd > 0 else np.nan)


def main():
    sleeves = {}
    for sym in UNIVERSE:
        df = pd.read_feather(os.path.join(FUT, f"{sym}_USDT_USDT-1d-futures.feather"))
        df = df.sort_values('date').reset_index(drop=True)
        df['date'] = pd.to_datetime(df['date']).dt.tz_localize(None)
        # 预热数据保留（rolling 需要 START 前历史），收益序列截 START 后
        r = sleeve_returns(df)
        sleeves[sym] = r[r.index >= pd.Timestamp(START)]
    mat = pd.DataFrame(sleeves).dropna()
    print(f"窗口: {mat.index[0].date()} ~ {mat.index[-1].date()}  {len(mat)} 日  {len(mat.columns)} sleeve")

    port = mat.mean(axis=1)
    btc = mat['BTC']
    port_ex = mat.drop(columns='BTC').mean(axis=1)

    # 诊断：sleeve 相关性（仅双方都在场的日子才有意义，用原始收益近似，声明）
    corr = mat.corr().values
    avg_corr = corr[np.triu_indices_from(corr, k=1)].mean()

    print(f"\n{'sleeve':<6} {'累计':>10} {'年化':>8} {'MaxDD':>7} {'Calmar':>7}")
    for sym in UNIVERSE:
        t, a, d, c_ = stats(mat[sym])
        print(f"{sym:<6} {t:>+10.1%} {a:>+8.1%} {d:>7.1%} {c_:>7.2f}")

    rows = [("BTC-only", btc), ("组合15币", port), ("组合exBTC", port_ex)]
    print()
    res = {}
    for name, r in rows:
        t, a, d, c_ = stats(r)
        res[name] = (t, a, d, c_)
        print(f"{name:<10} 累计 {t:+9.1%} | 年化 {a:+6.1%} | MaxDD {d:5.1%} | Calmar {c_:.2f}")
    print(f"\nsleeve 平均两两相关: {avg_corr:.2f}")

    mid = len(mat) // 2
    halves = {}
    for name, r in [("BTC-only", btc), ("组合15币", port)]:
        h = []
        for seg in (r.iloc[:mid], r.iloc[mid:]):
            _, a, d, c_ = stats(seg)
            h.append((a, d, c_))
        halves[name] = h
        print(f"{name:<10} H1 年化 {h[0][0]:+6.1%}/DD {h[0][1]:5.1%}/Calmar {h[0][2]:5.2f} | "
              f"H2 年化 {h[1][0]:+6.1%}/DD {h[1][1]:5.1%}/Calmar {h[1][2]:5.2f}")

    h1 = res["组合15币"][3] >= res["BTC-only"][3]
    h2 = res["组合15币"][2] <= res["BTC-only"][2] + 0.02
    h3 = all(halves["组合15币"][i][2] >= halves["BTC-only"][i][2] for i in (0, 1))
    print("\n" + "=" * 60)
    print(f"H1 组合Calmar≥BTC-only: {'PASS' if h1 else 'FAIL'} "
          f"({res['组合15币'][3]:.2f} vs {res['BTC-only'][3]:.2f})")
    print(f"H2 MaxDD不劣于+2pp: {'PASS' if h2 else 'FAIL'} "
          f"({res['组合15币'][2]:.1%} vs {res['BTC-only'][2]:.1%})")
    print(f"H3 对半切优势同号: {'PASS' if h3 else 'FAIL'}")
    print(f"总判定: {'广度采纳候选（需影子运行）' if (h1 and h2 and h3) else '广度关闭' if not h1 else '部分通过，按分级记录'}")


if __name__ == "__main__":
    main()
