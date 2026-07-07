"""
TASK 73（预研）：大结构跟随定型——纯多头变体 / Donchian 诚实基线 / 回撤画像

预注册判据（跑数前写定）：
  C1 定型：纯多头（up持多/down空仓）vs 双向翻转，净期望与逐日盯市 MaxDD 双维对比择优
  C2 伪影防线：经典 Donchian（20/10 与 55/20，零调参）若累计收益 ≥ 翻转跟随器
     且 DD 不更差（双数据集）→ 采用简单实现，"结构检测器"判无增量
  C3 存活门槛：定型变体须维持 TASK 72 判据（扣费腿期望>0、半段双正、双数据集）
     且 1x 名义逐日盯市 MaxDD < 60%
成本：0.11%/边（taker 0.06% + 滑点 0.05%），仓位变动 |Δpos| 计费。
全部决策在收盘，指标只用截至前一日数据（shift(1)），因果。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import numpy as np
import pandas as pd

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DATA = [
    ("spot", os.path.join(BASE, "data", "binance", "BTC_USDT-1d.feather")),
    ("futures", os.path.join(BASE, "data", "binance", "futures", "BTC_USDT_USDT-1d-futures.feather")),
]
COST_SIDE = 0.0011
X = 0.10  # TASK 72 通过档


def flipflop_positions(closes, x, long_only=False):
    n = len(closes)
    pos = np.zeros(n)
    state, extreme = None, closes[0]
    for i in range(1, n):
        c = closes[i]
        if state is None:
            if c >= closes[0] * (1 + x):
                state, extreme = 'up', c
            elif c <= closes[0] * (1 - x):
                state, extreme = 'down', c
        elif state == 'up':
            if c > extreme:
                extreme = c
            elif c <= extreme * (1 - x):
                state, extreme = 'down', c
        else:
            if c < extreme:
                extreme = c
            elif c >= extreme * (1 + x):
                state, extreme = 'up', c
        pos[i] = 1.0 if state == 'up' else (0.0 if (long_only or state is None) else -1.0)
    return pos


def donchian_positions(df, n_entry, n_exit, long_only=False):
    hi = df['high'].rolling(n_entry).max().shift(1)
    lo_exit = df['low'].rolling(n_exit).min().shift(1)
    lo = df['low'].rolling(n_entry).min().shift(1)
    hi_exit = df['high'].rolling(n_exit).max().shift(1)
    c = df['close']
    n = len(df)
    pos = np.zeros(n)
    state = 0
    for i in range(n):
        if state == 1 and c.iloc[i] < lo_exit.iloc[i]:
            state = 0
        elif state == -1 and c.iloc[i] > hi_exit.iloc[i]:
            state = 0
        if state == 0:
            if not np.isnan(hi.iloc[i]) and c.iloc[i] > hi.iloc[i]:
                state = 1
            elif not long_only and not np.isnan(lo.iloc[i]) and c.iloc[i] < lo.iloc[i]:
                state = -1
        pos[i] = state
    return pos


def evaluate(df, pos, label, mid_i):
    closes = df['close'].values
    ret = np.zeros(len(df))
    ret[1:] = closes[1:] / closes[:-1] - 1
    # 当日收盘决策 → 次日起持仓生效
    pos_eff = np.roll(pos, 1)
    pos_eff[0] = 0
    dpos = np.abs(np.diff(np.concatenate([[0], pos])))
    strat_ret = pos_eff * ret - dpos * COST_SIDE
    eq = np.cumprod(1 + strat_ret)
    dd = 1 - eq / np.maximum.accumulate(eq)
    h1 = np.prod(1 + strat_ret[:mid_i]) - 1
    h2 = np.prod(1 + strat_ret[mid_i:]) - 1
    n_flips = int((dpos > 0).sum())
    tim = float((pos_eff != 0).mean())
    print(f"    {label:<26s} 终值 {eq[-1]-1:+8.1%} | H1 {h1:+7.1%} / H2 {h2:+7.1%} | "
          f"MaxDD {dd.max():5.1%} | 变动 {n_flips:3d} 次 | 在场 {tim:.0%}")
    return eq[-1] - 1, h1, h2, dd.max()


def main():
    for tag, path in DATA:
        df = pd.read_feather(path).sort_values('date').reset_index(drop=True)
        closes = df['close'].values.astype(float)
        mid_i = len(df) // 2
        bh = closes[-1] / closes[0] - 1
        print(f"\n========== {tag}: {pd.to_datetime(df['date'].iloc[0]).date()} ~ "
              f"{pd.to_datetime(df['date'].iloc[-1]).date()}  Buy&Hold {bh:+.1%} ==========")
        evaluate(df, flipflop_positions(closes, X), "翻转跟随 X=10% 双向", mid_i)
        evaluate(df, flipflop_positions(closes, X, long_only=True), "翻转跟随 X=10% 纯多头", mid_i)
        evaluate(df, donchian_positions(df, 20, 10), "Donchian 20/10 双向", mid_i)
        evaluate(df, donchian_positions(df, 20, 10, long_only=True), "Donchian 20/10 纯多头", mid_i)
        evaluate(df, donchian_positions(df, 55, 20), "Donchian 55/20 双向", mid_i)
        evaluate(df, donchian_positions(df, 55, 20, long_only=True), "Donchian 55/20 纯多头", mid_i)


if __name__ == "__main__":
    main()
