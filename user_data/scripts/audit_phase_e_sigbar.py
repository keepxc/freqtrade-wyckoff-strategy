"""
audit_phase_e_sigbar.py
路径 A 审计：Phase E 显著意图棒直接触发 vs ZigZag 等待确认

Phase E 代理：
  - 日线 close > EMA200（牛市门控）
  - 当前 1H close > 过去 N 根最高价（微观阻力突破）

显著意图棒（Significant Bar）：
  - 强阳线（close > open）
  - 价差 > ATR × 0.6
  - 成交量 > vol_ma × 1.2
  - 涨幅 > 0.5%
  - close > rolling_high[N]（阻力突破，N=10/20/30 分组测试）

对照基线：现有 ZigZag ws_up wave_start 胜率 91.6%，avg win +3.3%
"""
import pandas as pd
import numpy as np
from pathlib import Path

DATA_DIR = Path("user_data/data/binance")
H1_FILE  = DATA_DIR / "futures/BTC_USDT_USDT-1h-futures.feather"
D1_FILE  = DATA_DIR / "futures/BTC_USDT_USDT-1d-futures.feather"

df1h = pd.read_feather(H1_FILE)
df1d = pd.read_feather(D1_FILE)
df1h['date'] = pd.to_datetime(df1h['date']).dt.tz_localize(None)
df1d['date'] = pd.to_datetime(df1d['date']).dt.tz_localize(None)
df1h = df1h.sort_values('date').reset_index(drop=True)
df1d = df1d.sort_values('date').reset_index(drop=True)

# daily EMA200 (+1d shift)
df1d['ema200']      = df1d['close'].ewm(span=200, adjust=False).mean()
df1d['bull_market'] = df1d['close'] > df1d['ema200']
df1d_s = df1d[['date','ema200','bull_market']].copy()
df1d_s['date'] += pd.Timedelta(days=1)
df1d_s.columns = ['merge_date','ema200','bull_market']

df1h['merge_date'] = df1h['date'].dt.normalize()
df1h = df1h.merge(df1d_s, on='merge_date', how='left')
df1h['ema200']      = df1h['ema200'].ffill()
df1h['bull_market'] = df1h['bull_market'].ffill().fillna(False)

# 1H indicators
df1h['atr14']    = (df1h['high'] - df1h['low']).rolling(14, min_periods=3).mean()
df1h['vol_ma10'] = df1h['volume'].rolling(10, min_periods=3).mean()
df1h['spread']   = df1h['high'] - df1h['low']
df1h['move_pct'] = (df1h['close'] - df1h['open']) / df1h['open'].replace(0, np.nan) * 100

# 显著意图棒基础条件（不含阻力突破）
sos_base = (
    (df1h['close'] > df1h['open']) &
    (df1h['spread'] > df1h['atr14'] * 0.6) &
    (df1h['volume'] > df1h['vol_ma10'] * 1.2) &
    (df1h['move_pct'] > 0.5) &
    df1h['bull_market']
)

print(f"Data: {df1h['date'].min()} -> {df1h['date'].max()}")
print(f"bull_market bars: {df1h['bull_market'].sum()}")
print(f"SOS base (no breakout filter): {sos_base.sum()}")
print()

def audit_sigbar(n_lookback, label):
    """显著意图棒 + 突破过去 n_lookback 根 K 线最高价"""
    rolling_high = df1h['high'].shift(1).rolling(n_lookback, min_periods=n_lookback).max()
    breakout = df1h['close'] > rolling_high
    mask = sos_base & breakout

    signals = df1h[mask].copy()
    close_arr = df1h['close'].values
    n = len(df1h)

    rows = []
    for idx in signals.index:
        entry = close_arr[idx]
        row = {'date': df1h.loc[idx, 'date'], 'close': entry}
        for h in [6, 12, 24, 48, 72]:
            ti = idx + h
            row[f'ret_{h}h'] = (close_arr[ti] - entry) / entry * 100 if ti < n else np.nan
        rows.append(row)

    df = pd.DataFrame(rows)

    print(f"{'='*60}")
    print(f"{label}  signals={len(df)}  (~{len(df)/5.5:.0f}/yr)")
    print(f"{'='*60}")
    if len(df) == 0:
        print("  No signals")
        return

    for h in [6, 12, 24, 48, 72]:
        col = f'ret_{h}h'
        v = df[col].dropna()
        wr  = (v > 0).sum() / len(v) * 100
        avg = v.mean()
        med = v.median()
        p1  = (v > 1).sum() / len(v) * 100
        p2  = (v > 2).sum() / len(v) * 100
        p3  = (v > 3).sum() / len(v) * 100
        # 最大回撤（min of forward returns as proxy for intra-trade drawdown)
        dd  = (v < -2).sum() / len(v) * 100
        print(f"  {h:>3}H  WR={wr:.1f}%  avg={avg:+.2f}%  med={med:+.2f}%  "
              f">1%={p1:.0f}%  >2%={p2:.0f}%  >3%={p3:.0f}%  <-2%={dd:.0f}%")

    # 年度
    df['year'] = df['date'].dt.year
    print(f"  --- by year (24H WR | avg) ---")
    for yr, g in df.groupby('year'):
        v = g['ret_24h'].dropna()
        if len(v) == 0: continue
        print(f"  {yr}: n={len(g):>3}  WR={( v>0).sum()/len(v)*100:.0f}%  avg={v.mean():+.2f}%")

    # 聚集性
    df_s = df.sort_values('date')
    gaps = df_s['date'].diff().dt.total_seconds().dropna() / 3600
    clusters = (gaps > 48).sum() + 1
    print(f"  clusters(48H): {clusters}  avg/cluster: {len(df)/clusters:.1f}")
    print()

# 测试不同回看窗口
audit_sigbar(10,  "Phase-E SigBar: breakout above 10-bar high")
audit_sigbar(20,  "Phase-E SigBar: breakout above 20-bar high")
audit_sigbar(30,  "Phase-E SigBar: breakout above 30-bar high")
audit_sigbar(48,  "Phase-E SigBar: breakout above 48-bar high (2-day)")
audit_sigbar(72,  "Phase-E SigBar: breakout above 72-bar high (3-day)")
audit_sigbar(168, "Phase-E SigBar: breakout above 168-bar high (1-week)")

print("Baseline (ZigZag ws_up): WR=91.6%  avg win=+3.3%  avg loss=-0.5%")
print("Audit done")
