"""
TASK 88（预研）：1H 双向趋势——"1D 震荡内部由 1H 可交易趋势组成"假设检验

用户命题：六年 1D 震荡期"不可能没有机会"，向上做多向下做空，1H 粒度
应能抓住 1D 系统漏掉的段内趋势。可证伪核心：1H Donchian 双向系统在
震荡年（2021/2023/2024/2025，declared）净收益是否为正。

跑数前预测申明：毛收益可能为正，净收益被成本吃死——1H 频率下
0.11%/边的税率是 1D 的十几倍。为区分"信号死"vs"成本死"，
同时报告 0.03%/边（maker 乐观档）与零成本（毛）诊断。

设计（跑数前固定，零调参）：
  数据：BTC 1H futures 2021-01~2026-07（5.5年，含4个震荡年+2个崩盘年）
  系统：对称双向 Donchian（bar 单位）：
    多头：破 N bar 高入场，破 M bar 低离场
    空头：破 N bar 低入场，破 M bar 高离场
  速度档（申明两档全报告，数字沿用已验证 1D 系统，仅单位换 bar）：
    fast (20,10)≈0.8天通道 · slow (55,20)≈2.3天通道
  仓位：固定 1x（sizing 精化仅在信号存活后才有意义，声明）
  funding：多头付/空头收，按 8h 结算事件逐小时入账
  成本：主档 0.11%/边

预注册判据（对每个速度档独立判，任一档全过 → 深入；全败 → 关闭）：
  L1 全样本净累计 > 0
  L2 震荡年（2021+2023+2024+2025 合并）净累计 > 0 ——用户命题的直接检验
  L3 净年化 ≥ +5%（效应量下限：低于此不值得与 1D 系统并行部署）
  L4 时间对半切同号
多重检验申明：2 速度档 × 4 判据联合；诊断档（成本敏感性）不参与判定。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import numpy as np
import pandas as pd

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
FUT = os.path.join(BASE, "data", "binance", "futures")
SPEEDS = [(20, 10), (55, 20)]
COST_MAIN = 0.0011
COST_DIAG = [0.0003, 0.0]
CHOP_YEARS = {2021, 2023, 2024, 2025}


def build_positions(df, n_entry, n_exit):
    """对称双向状态机，返回净仓位（+1/0/-1，决策 bar 收盘，次 bar 生效前移位）。"""
    hi_ent = df['high'].rolling(n_entry).max().shift(1).values
    lo_ent = df['low'].rolling(n_entry).min().shift(1).values
    hi_ex = df['high'].rolling(n_exit).max().shift(1).values
    lo_ex = df['low'].rolling(n_exit).min().shift(1).values
    c = df['close'].values
    n = len(df)
    pos = np.zeros(n)
    state = 0
    for i in range(n):
        if state == 1 and c[i] < lo_ex[i]:
            state = 0
        elif state == -1 and c[i] > hi_ex[i]:
            state = 0
        if state == 0 and not np.isnan(hi_ent[i]):
            if c[i] > hi_ent[i]:
                state = 1
            elif c[i] < lo_ent[i]:
                state = -1
        pos[i] = state
    return pos


def run_speed(df, f_hour, ne, nx):
    pos = build_positions(df, ne, nx)
    ret = df['close'].pct_change().fillna(0).values
    pos_eff = np.roll(pos, 1)
    pos_eff[0] = 0
    dpos = np.abs(np.diff(np.concatenate([[0], pos])))
    dpos_eff = np.roll(dpos, 1)
    dpos_eff[0] = 0
    price_pnl = pos_eff * ret
    fund_pnl = -pos_eff * f_hour  # 多头付正费率，空头收
    n_rt = int((dpos > 0).sum())
    out = {}
    for cost in [COST_MAIN] + COST_DIAG:
        r = pd.Series(price_pnl + fund_pnl - dpos_eff * cost, index=df['date'].values)
        out[cost] = r
    return out, n_rt


def stats(r, bars_per_year=24 * 365):
    eq = (1 + r).cumprod()
    dd = (1 - eq / eq.cummax()).max()
    yrs = len(r) / bars_per_year
    total = eq.iloc[-1] - 1
    ann = (1 + total) ** (1 / yrs) - 1
    return total, ann, dd


def main():
    df = pd.read_feather(os.path.join(FUT, "BTC_USDT_USDT-1h-futures.feather"))
    df = df.sort_values('date').reset_index(drop=True)
    df['date'] = pd.to_datetime(df['date']).dt.tz_localize(None)
    f = pd.read_feather(os.path.join(FUT, "BTC_USDT_USDT-1h-funding_rate.feather"))
    f['date'] = pd.to_datetime(f['date']).dt.tz_localize(None)
    f_hour = f.set_index('date')['open'].astype(float).reindex(
        pd.DatetimeIndex(df['date'])).fillna(0.0).values
    print(f"BTC 1H: {df['date'].min()} ~ {df['date'].max()}  {len(df)} bars")

    verdicts = {}
    for ne, nx in SPEEDS:
        out, n_rt = run_speed(df, f_hour, ne, nx)
        r_main = out[COST_MAIN]
        t, a, d = stats(r_main)
        years = r_main.groupby(r_main.index.year).apply(lambda s: (1 + s).prod() - 1)
        chop = r_main[r_main.index.year.isin(CHOP_YEARS)]
        t_chop = (1 + chop).prod() - 1
        mid = len(r_main) // 2
        h1 = (1 + r_main.iloc[:mid]).prod() - 1
        h2 = (1 + r_main.iloc[mid:]).prod() - 1
        l1, l2 = t > 0, t_chop > 0
        l3 = a >= 0.05
        l4 = np.sign(h1) == np.sign(h2)
        verdicts[(ne, nx)] = l1 and l2 and l3 and l4
        print(f"\n===== ({ne},{nx}) bars | round-trips {n_rt} ({n_rt/5.5:.0f}/年) =====")
        print(f"  净(0.11%): 累计 {t:+8.1%} | 年化 {a:+6.1%} | MaxDD {d:5.1%}")
        for cost in COST_DIAG:
            tc, ac, dc = stats(out[cost])
            tag = "毛(零成本)" if cost == 0 else f"净({cost:.2%}/边)"
            print(f"  {tag}: 累计 {tc:+8.1%} | 年化 {ac:+6.1%} | MaxDD {dc:5.1%}")
        print("  逐年(0.11%): " + "  ".join(f"{y}:{v:+.1%}" for y, v in years.items()))
        print(f"  L1 全样本净>0: {'PASS' if l1 else 'FAIL'} | "
              f"L2 震荡年净>0: {'PASS' if l2 else 'FAIL'} ({t_chop:+.1%}) | "
              f"L3 年化≥5%: {'PASS' if l3 else 'FAIL'} | "
              f"L4 对半切同号: {'PASS' if l4 else 'FAIL'}")

    print("\n" + "=" * 60)
    any_pass = any(verdicts.values())
    print(f"总判定: {'存在通过档，进入深入评估' if any_pass else '1H 双向趋势关闭（震荡段无 1H 级可交易趋势结构）'}")


if __name__ == "__main__":
    main()
