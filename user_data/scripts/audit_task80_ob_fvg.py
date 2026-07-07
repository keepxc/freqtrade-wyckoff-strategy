"""
TASK 80（预研）：SMC 订单块（Order Block）/ 公允价值缺口（FVG）电池

背景：用户提出 Smart Money Concepts [LuxAlgo]。其大部分组件与已判死方向同构
（BOS/CHoCH=确认税波段，溢价折价区=TR mid，HH/HL=结构门控），仅 OB 与 FVG
是未测过的原语。LuxAlgo 自认"没有任何支持数据"，本电池给出数据。

定义（跑数前固定，LuxAlgo/ICT 标准形态，零调参）：
  多头 FVG：low[i] > high[i-2]，缺口区 = [high[i-2], low[i]]，i 收盘成立
  空头 FVG：high[i] < low[i-2]，缺口区 = [high[i], low[i-2]]
  多头 OB：close 突破前 20 根bar最高价（Donchian BOS 代理，零新参数）时，
           突破前最近一根阴线（close<open），区间 = 该bar [low, high]
  空头 OB：镜像（突破 20 根bar最低价前的最近阳线）
  回踩事件：形成后 50 根bar内首次触区（多头：low ≤ 区顶且 close ≥ 区底，
           跌穿区底=失效不计），入场记回踩bar收盘价
测试形态：
  T1 形成动量：FVG 形成后 5/20 bar 前瞻收益（对比无条件基线）
  T2 回踩反弹：OB/FVG 回踩触区后 5/20 bar 前瞻收益（对比无条件基线）
时间框架：1h（SMC 原生）+ 1d；资产：BTC/ETH futures。

预注册判据（全部满足才算候选）：
  E1 edge（条件均值−无条件基线）bootstrap 95% CI 不含 0
  E2 |edge| ≥ 0.3%（可操作性下限，往返成本 0.22%）
  E3 时间对半切同号
  E4 BTC 与 ETH 同时通过 E1-E3（复制内建）
  样本门槛：事件数 ≥ 100/资产/框架，不足记"样本不足"不判方向
多重检验申明：6 形态×2框架×2视界=24 检验/资产，E4 双资产联合通过使
噪声假阳性率 ≈ (0.05)²量级/检验；空头形态 edge 期望为负号。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import numpy as np
import pandas as pd

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
FUT = os.path.join(BASE, "data", "binance", "futures")
ASSETS = ["BTC", "ETH"]
TIMEFRAMES = ["1h", "1d"]
HORIZONS = [5, 20]
MAX_WAIT = 50
EFFECT_FLOOR = 0.003
N_BOOT = 2000
SEED = 20260708


def load(asset, tf):
    p = os.path.join(FUT, f"{asset}_USDT_USDT-{tf}-futures.feather")
    df = pd.read_feather(p).sort_values('date').reset_index(drop=True)
    return df


def fvg_events(df, bull=True):
    h, l = df['high'].values, df['low'].values
    ev = []
    for i in range(2, len(df)):
        if bull and l[i] > h[i - 2]:
            ev.append((i, h[i - 2], l[i]))
        elif not bull and h[i] < l[i - 2]:
            ev.append((i, h[i], l[i - 2]))
    return ev


def ob_events(df, bull=True):
    h, l, c, o = df['high'].values, df['low'].values, df['close'].values, df['open'].values
    n = len(df)
    roll_hi = pd.Series(h).rolling(20).max().shift(1).values
    roll_lo = pd.Series(l).rolling(20).min().shift(1).values
    ev = []
    prev_break = False
    for i in range(21, n):
        brk = (c[i] > roll_hi[i]) if bull else (c[i] < roll_lo[i])
        if brk and not prev_break:
            # 突破前最近一根反向K线
            for k in range(i - 1, max(i - 20, 0), -1):
                if (bull and c[k] < o[k]) or (not bull and c[k] > o[k]):
                    ev.append((i, l[k], h[k]))
                    break
        prev_break = brk
    return ev


def revisit_entries(df, events, bull=True):
    """形成后 50 bar 内首次触区（未失效），返回入场 bar 索引列表。"""
    h, l, c = df['high'].values, df['low'].values, df['close'].values
    entries = []
    for (i, zlo, zhi) in events:
        for j in range(i + 1, min(i + 1 + MAX_WAIT, len(df))):
            if bull:
                if c[j] < zlo:
                    break  # 跌穿区底，失效
                if l[j] <= zhi:
                    entries.append(j)
                    break
            else:
                if c[j] > zhi:
                    break
                if h[j] >= zlo:
                    entries.append(j)
                    break
    return entries


def edge_test(df, bars, h, uncond_mean, rng):
    c = df['close'].values
    rets = np.array([c[j + h] / c[j] - 1 for j in bars if j + h < len(c)])
    if len(rets) == 0:
        return None
    edge = rets.mean() - uncond_mean
    boot = np.array([rng.choice(rets, len(rets)).mean() for _ in range(N_BOOT)]) - uncond_mean
    lo, hi = np.percentile(boot, 2.5), np.percentile(boot, 97.5)
    return edge, lo, hi, len(rets), rets


def main():
    rng = np.random.default_rng(SEED)
    results = {}
    for asset in ASSETS:
        for tf in TIMEFRAMES:
            df = load(asset, tf)
            c = df['close'].values
            n = len(df)
            mid_date_i = n // 2
            print(f"\n========== {asset} {tf}: n={n} ==========")
            forms = {
                'fvg_bull': fvg_events(df, True),
                'fvg_bear': fvg_events(df, False),
                'ob_bull': ob_events(df, True),
                'ob_bear': ob_events(df, False),
            }
            setups = {}
            setups['fvg_bull_form'] = [i for (i, _, _) in forms['fvg_bull']]
            setups['fvg_bear_form'] = [i for (i, _, _) in forms['fvg_bear']]
            setups['fvg_bull_revisit'] = revisit_entries(df, forms['fvg_bull'], True)
            setups['fvg_bear_revisit'] = revisit_entries(df, forms['fvg_bear'], False)
            setups['ob_bull_revisit'] = revisit_entries(df, forms['ob_bull'], True)
            setups['ob_bear_revisit'] = revisit_entries(df, forms['ob_bear'], False)
            for name, bars in setups.items():
                want_neg = 'bear' in name
                for h in HORIZONS:
                    all_f = c[h:] / c[:-h] - 1
                    uncond = all_f.mean()
                    r = edge_test(df, bars, h, uncond, rng)
                    if r is None or r[3] < 100:
                        print(f"  {name:<18s} fwd{h:>2d}: 样本不足 (n={0 if r is None else r[3]})")
                        results[(asset, tf, name, h)] = None
                        continue
                    edge, lo, hi, nev, rets = r
                    e1 = (lo > 0) or (hi < 0)
                    e2 = abs(edge) >= EFFECT_FLOOR
                    b1 = [j for j in bars if j < mid_date_i]
                    b2 = [j for j in bars if j >= mid_date_i]
                    r1 = edge_test(df, b1, h, uncond, rng)
                    r2 = edge_test(df, b2, h, uncond, rng)
                    e3 = (r1 and r2 and np.sign(r1[0]) == np.sign(r2[0]) == np.sign(edge))
                    sign_ok = (edge < 0) if want_neg else (edge > 0)
                    ok = e1 and e2 and e3 and sign_ok
                    results[(asset, tf, name, h)] = (edge, ok)
                    print(f"  {name:<18s} fwd{h:>2d}: n={nev:5d} edge {edge:+.3%} "
                          f"CI[{lo:+.3%},{hi:+.3%}] "
                          f"E1{'√' if e1 else '×'} E2{'√' if e2 else '×'} "
                          f"E3{'√' if e3 else '×'} 号{'√' if sign_ok else '×'} "
                          f"{'** 单资产过 **' if ok else ''}")
    print("\n" + "=" * 70)
    print("E4 双资产联合通过（同形态同框架同视界，BTC+ETH 均单资产过）:")
    n_joint = 0
    for tf in TIMEFRAMES:
        for name in ['fvg_bull_form', 'fvg_bear_form', 'fvg_bull_revisit',
                     'fvg_bear_revisit', 'ob_bull_revisit', 'ob_bear_revisit']:
            for h in HORIZONS:
                a = results.get(("BTC", tf, name, h))
                b = results.get(("ETH", tf, name, h))
                if a and b and a[1] and b[1]:
                    n_joint += 1
                    print(f"  ✅ {tf} {name} fwd{h}: BTC {a[0]:+.3%} / ETH {b[0]:+.3%}")
    if n_joint == 0:
        print("  （无）")
    print(f"\n总判定: {n_joint} 个形态×框架×视界联合通过 → "
          f"{'进入策略层评估' if n_joint > 0 else 'OB/FVG 原语关闭，SMC 全组件审判完毕'}")


if __name__ == "__main__":
    main()
