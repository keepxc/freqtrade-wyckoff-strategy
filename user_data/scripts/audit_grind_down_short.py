"""
审计：阴跌（无新 wave_start）该不该跟做空？

背景：实盘发现持续阴跌时 is_wave_start.rolling(8) 过期，空头不触发。
问题：V8 系统性跳过的「阴跌延续」，如果做空，盈亏如何？vs 现有 wsdown 空头？

方法：用真实 WaveAnalyzer（mock strategy）复刻实盘 is_wave_start，
      对比两类下降setup的前向收益：
        A) V8实际会做空（rolling8 有 wave_start）
        B) V8跳过的阴跌（rolling8=0，纯延续）

纯审计，不动策略代码。
运行：python user_data/scripts/audit_grind_down_short.py
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'strategies'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'wyckoff'))

import pandas as pd
import numpy as np
import talib.abstract as ta
from wave_analyzer import WaveAnalyzer

FEATHER = os.path.join(os.path.dirname(__file__), '..', 'data', 'binance', 'futures',
                       'BTC_USDT_USDT-1h-futures.feather')
START, END = '2022-01-01', '2026-06-13'

# ── 实盘参数（与 WyckoffV8.py 一致）──
WAVE_MIN_PERCENT = 0.008
WAVE_ATR_MULT    = 1.5
WAVE_MIN_BARS    = 3

HORIZONS = [12, 24, 48]   # 前向收益观察小时数


@property
def _disp(self): return abs(self.end_price - self.start_price)
@property
def _eff(self): return self.displacement / (self.total_volume + 1e-9)
@property
def _isup(self): return self.direction == 'up'

from dataclasses import dataclass
@dataclass
class WyckoffWave:
    start_idx: int; end_idx: int; start_price: float; end_price: float
    high_price: float; low_price: float; total_volume: float
    bar_count: int; direction: str
    @property
    def displacement(self): return abs(self.end_price - self.start_price)
    @property
    def effort(self): return self.total_volume
    @property
    def efficiency(self): return self.displacement / (self.effort + 1e-9)
    @property
    def is_up_wave(self): return self.direction == 'up'
    @property
    def duration_hours(self): return self.bar_count


class _P:
    def __init__(self, v): self.value = v

class MockStrategy:
    wave_min_percent = _P(WAVE_MIN_PERCENT)
    wave_atr_multiplier = _P(WAVE_ATR_MULT)
    wave_min_bars = _P(WAVE_MIN_BARS)


def main():
    print("加载 1H 数据 + 计算波...")
    df = pd.read_feather(FEATHER)
    df['date'] = pd.to_datetime(df['date'], utc=True)
    df = df[(df['date'] >= START) & (df['date'] < END)].reset_index(drop=True)
    df['atr_20'] = ta.ATR(df, timeperiod=20)
    print(f"{len(df)} 根 1H K线，{df['date'].min().date()} ~ {df['date'].max().date()}")

    wa = WaveAnalyzer(MockStrategy(), WyckoffWave)
    df = wa._segment_weis_waves(df)

    # shift(3) 消除 lookahead，与实盘一致
    for c in ['is_wave_start', 'is_wave_end']:
        if c in df.columns:
            df[c] = df[c].shift(WAVE_MIN_BARS).fillna(False).astype(bool)

    for c in ['is_wave_start', 'wave_direction', 'wave_efficiency', 'wave_displacement']:
        if c not in df.columns:
            print(f"[ERR] 缺列 {c}，WaveAnalyzer 输出列: {[x for x in df.columns]}")
            return

    # 下降延续基础条件（去掉 daily_ema200_persist 这个日线门控，单看 1H 结构）
    base_down = (
        (df['wave_direction'] == 'down') &
        (df['wave_efficiency'] > 0.001) &
        (df['wave_displacement'] > df['close'] * 0.008)
    )
    ws_roll8 = df['is_wave_start'].rolling(8, min_periods=1).max().fillna(0).astype(bool)

    setup_A = base_down & ws_roll8           # V8 实际会做空
    setup_B = base_down & (~ws_roll8)        # V8 跳过的阴跌延续

    print(f"\nsetup A（V8会做空, rolling8有wave_start）: {int(setup_A.sum())} bar")
    print(f"setup B（V8跳过的阴跌, rolling8=0）:        {int(setup_B.sum())} bar")

    # ── 去重成"片段"：连续 setup bar 只取第一根 ──
    def to_episodes(mask):
        idxs = np.where(mask.values)[0]
        eps = []
        prev = -10
        for i in idxs:
            if i - prev > 3:   # 间隔>3根视为新片段
                eps.append(i)
            prev = i
        return eps

    epA = to_episodes(setup_A)
    epB = to_episodes(setup_B)
    print(f"去重后片段数 — A: {len(epA)}  B: {len(epB)}")

    close = df['close'].values
    n = len(df)

    def fwd_short_stats(episodes, H):
        """做空前向收益：(entry - close[t+H]) / entry，正=盈利"""
        rets = []
        for i in episodes:
            if i + H >= n: continue
            r = (close[i] - close[i + H]) / close[i] * 100
            rets.append(r)
        rets = np.array(rets)
        if len(rets) == 0:
            return None
        return {
            'n': len(rets),
            'win_rate': (rets > 0).mean() * 100,
            'avg_ret': rets.mean(),
            'median_ret': np.median(rets),
        }

    print("\n" + "=" * 72)
    print("做空前向收益对比（正收益=做空赚钱）")
    print("=" * 72)
    print(f"{'horizon':>8} | {'A:V8做空 (n/胜率/均利)':>32} | {'B:阴跌延续 (n/胜率/均利)':>32}")
    print("-" * 72)
    for H in HORIZONS:
        a = fwd_short_stats(epA, H)
        b = fwd_short_stats(epB, H)
        astr = f"{a['n']}/{a['win_rate']:.1f}%/{a['avg_ret']:+.2f}%" if a else "—"
        bstr = f"{b['n']}/{b['win_rate']:.1f}%/{b['avg_ret']:+.2f}%" if b else "—"
        print(f"{H:>6}h | {astr:>32} | {bstr:>32}")

    # ── 量价特征对比（验证威科夫"低量阴跌 vs 放量阴跌"）──
    df['_vol_ma20'] = df['volume'].rolling(20).mean()
    df['_vol_ratio'] = df['volume'] / df['_vol_ma20']
    volA = df.loc[setup_A, '_vol_ratio'].dropna()
    volB = df.loc[setup_B, '_vol_ratio'].dropna()
    print("\n" + "=" * 72)
    print("量价特征（量比中位数，相对20均量）")
    print("=" * 72)
    print(f"  A (V8做空):   {volA.median():.2f}")
    print(f"  B (阴跌延续): {volB.median():.2f}")
    print(f"  → {'阴跌量更低（no demand受控下行）' if volB.median()<volA.median() else '阴跌量不低'}")

    # ── 结论 ──
    print("\n" + "=" * 72)
    print("结论")
    print("=" * 72)
    a24 = fwd_short_stats(epA, 24); b24 = fwd_short_stats(epB, 24)
    if a24 and b24:
        gap = a24['win_rate'] - b24['win_rate']
        print(f"  24h: A胜率 {a24['win_rate']:.1f}%  vs  B胜率 {b24['win_rate']:.1f}%  (差 {gap:+.1f}pp)")
        if b24['win_rate'] >= a24['win_rate'] - 5:
            print(f"  → 阴跌延续做空胜率接近 V8 现有空头，存在被跳过的真实机会，值得研究")
        else:
            print(f"  → 阴跌延续做空胜率明显更低，当前跳过是对的（无结构=噪声，TASK36同源）")


if __name__ == '__main__':
    main()
