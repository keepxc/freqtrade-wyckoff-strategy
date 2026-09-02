"""
BStocks 币池筛选分析 —— 每票 (20,10) 独立统计
输出：胜率 / 盈亏比 / 期望 / 最大回撤 / 笔数 → 保留/剔除清单
"""
import warnings
warnings.filterwarnings('ignore')
import numpy as np
import pandas as pd
import vectorbt as vbt
import glob, os

files = sorted(glob.glob('/home/gyan/freqtrade/user_data/data/binance/*B_USDT-1d.feather'))
symbols = [os.path.basename(f).split('_')[0] for f in files]

series_list = []
for f in files:
    df = pd.read_feather(f)
    df['date'] = pd.to_datetime(df['date'], utc=True)
    series_list.append(df.set_index('date')['close'])

common_idx = series_list[0].index
for s in series_list[1:]:
    common_idx = common_idx.intersection(s.index)

price_df = pd.DataFrame({sym: s.reindex(common_idx) for sym, s in zip(symbols, series_list)})
print(f"数据: {len(symbols)} 票, {len(common_idx)} 根, {common_idx[0]:%Y-%m-%d} ~ {common_idx[-1]:%Y-%m-%d}\n")

# (20,10) 信号
E, X = 20, 10
don_up = price_df.rolling(E).max().shift(1)
don_ex = price_df.rolling(X).min().shift(1)
entries = price_df > don_up
exits = price_df < don_ex

# 每票独立 portfolio（列 = 票）
pf = vbt.Portfolio.from_signals(price_df, entries, exits, freq='1D',
                                init_cash=10000, fees=0.001, slippage=0.001)

rec = pf.trades.records  # DataFrame: id, col, size, entry_idx, entry_price, exit_idx, exit_price, pnl, return, ...
per_symbol_ret = pf.total_return()  # Series indexed by symbol

# 按 col 分组统计（col = 票的列索引）
rows = []
for ci, sym in enumerate(symbols):
    t = rec[rec['col'] == ci]
    n = len(t)
    if n == 0:
        rows.append({'symbol': sym, 'trades': 0, 'win_rate': float('nan'),
                     'profit_factor': float('nan'), 'expectancy': float('nan'),
                     'max_dd': float('nan'), 'total_ret': float('nan'),
                     'avg_win': float('nan'), 'avg_loss': float('nan')})
        continue
    returns = t['return'].values
    wins = returns[returns > 0]
    losses = returns[returns < 0]
    win_rate = len(wins) / n
    avg_win = wins.mean() if len(wins) else 0
    avg_loss = losses.mean() if len(losses) else 0
    profit_factor = (wins.sum() / abs(losses.sum())) if len(losses) and losses.sum() != 0 else float('inf')
    rows.append({
        'symbol': sym, 'trades': n, 'win_rate': win_rate,
        'profit_factor': profit_factor, 'expectancy': returns.mean(),
        'max_dd': float(pf.max_drawdown()[sym]),
        'total_ret': float(per_symbol_ret[sym]),
        'avg_win': avg_win, 'avg_loss': avg_loss,
    })

df = pd.DataFrame(rows).set_index('symbol')
df = df.sort_values('expectancy', ascending=False)

pd.set_option('display.width', 160)
print("=== 每票 (20,10) 统计（按期望排序）===")
print(df.to_string(float_format=lambda x: f'{x:.1%}' if abs(x) < 5 else f'{x:.2f}'))

# 分档
print(f"\n=== 分档 ===")
pos = df[df['expectancy'] > 0]
neg = df[df['expectancy'] <= 0]
print(f"正期望（保留）: {len(pos)} 票")
print(f"负期望（剔除候选）: {len(neg)} 票: {list(neg.index)}")
print(f"\n负期望详情:")
for sym in neg.index:
    r = neg.loc[sym]
    pf_str = '∞' if r['profit_factor'] == float('inf') else f"{r['profit_factor']:.2f}"
    print(f"  {sym}: 笔数{r['trades']}, 胜率{r['win_rate']:.1%}, PF={pf_str}, 期望={r['expectancy']:.2%}, 回撤={r['max_dd']:.1%}")

# 与实盘 8 仓对照
print(f"\n=== 实盘 8 仓在清单中的位置 ===")
live = ['PYPLB', 'MUB', 'SNDKB', 'ASMLB', 'CRCLB', 'COINB', 'NFLXB', 'HOODB']
for sym in live:
    if sym in df.index:
        r = df.loc[sym]
        verdict = "✅保留" if r['expectancy'] > 0 else "❌剔除候选"
        print(f"  {sym}: 期望{r['expectancy']:.2%} 胜率{r['win_rate']:.1%} PF={r['profit_factor']:.2f} {verdict}")
