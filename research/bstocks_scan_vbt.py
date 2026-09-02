"""
BStocks 16 票 Donchian 参数扫描 —— vectorbt 学习 demo 2
用 Binance bStock 1d 真实数据扫 (N_ENTRY, N_EXIT) 组合
对照：当前实盘 BStocksHybridV1 用 (20, 10)

注意：本扫描只测 Donchian 信号层（N_ENTRY×N_EXIT），不含 TARGET_VOL 仓位管理
和 tactical_exit 半仓逻辑——那是 BStocksHybridV1 特有的，简化会低估/高估。
"""
import warnings
warnings.filterwarnings('ignore')
import numpy as np
import pandas as pd
import vectorbt as vbt
import glob, os

# 1. 加载全部 16 只票（共享 freqtrade 数据管道）
files = sorted(glob.glob('/home/gyan/freqtrade/user_data/data/binance/*B_USDT-1d.feather'))
symbols = [os.path.basename(f).split('_')[0] for f in files]

# 只取共同时间区间（对齐 index）
series_list = []
for f in files:
    df = pd.read_feather(f)
    df['date'] = pd.to_datetime(df['date'], utc=True)
    s = df.set_index('date')['close']
    series_list.append(s)

# 对齐：取所有票的共同 index
common_idx = series_list[0].index
for s in series_list[1:]:
    common_idx = common_idx.intersection(s.index)

print(f"数据: {len(symbols)} 只票, 共同区间 {len(common_idx)} 根 1d K 线")
print(f"区间: {common_idx[0]:%Y-%m-%d} ~ {common_idx[-1]:%Y-%m-%d}")

# 合并成 DataFrame（symbol 为列）
price_df = pd.DataFrame({sym: s.reindex(common_idx) for sym, s in zip(symbols, series_list)})

# 2. 参数网格：N_ENTRY 10~40, N_EXIT 5~30（31×26=806 组合）
entry_windows = np.arange(10, 41)
exit_windows = np.arange(5, 31)
print(f"参数网格: {len(entry_windows)}×{len(exit_windows)} = {len(entry_windows)*len(exit_windows)} 组合 × {len(symbols)} 票 = {len(entry_windows)*len(exit_windows)*len(symbols)} 实例")

# 3. 构造信号矩阵（bars × (combo, symbol)）
cols = pd.MultiIndex.from_product([entry_windows, exit_windows, symbols], names=['N_ENTRY', 'N_EXIT', 'symbol'])
entries = pd.DataFrame(index=price_df.index, columns=cols, dtype=bool)
exits = pd.DataFrame(index=price_df.index, columns=cols, dtype=bool)

# 每个 (entry, exit) 组合对全部票同时算（用矩阵广播）
for e in entry_windows:
    don_up = price_df.rolling(e).max().shift(1)
    for x in exit_windows:
        don_ex = price_df.rolling(x).min().shift(1)
        entries[(e, x)] = (price_df > don_up).values  # 广播所有票
        exits[(e, x)] = (price_df < don_ex).values

print(f"信号矩阵: {entries.shape[0]} bars × {entries.shape[1]} 组合")

# 4. 一次性回测全部（16 票共享 10k 初始资金，每票独立）
#    price 需要和 entries 一样的 MultiIndex 列（每个 combo 重复 price 列）
price_broadcast = pd.DataFrame(
    np.tile(price_df.values, (1, len(entry_windows) * len(exit_windows))),
    index=price_df.index, columns=cols)
pf = vbt.Portfolio.from_signals(
    price_broadcast, entries, exits,
    freq='1D',
    init_cash=10000,
    fees=0.001,       # 0.1% 手续费（Binance 现货水平）
    slippage=0.001,   # 0.1% 滑点
    # 不分组：每个 (combo, symbol) 独立 portfolio，便于跨票平均
)

# 5. 结果分析：total_return() 返回 MultiIndex Series (N_ENTRY, N_EXIT, symbol)
total_return = pf.total_return()
print(f"total_return 形状: {total_return.shape}")
# 跨票平均收益
mean_ret = total_return.groupby(['N_ENTRY', 'N_EXIT']).mean()

print(f"\n=== 扫描结果（跨 16 票平均）===")
print(f"全部组合中位数: {mean_ret.median():.2%}")
best = mean_ret.idxmax()
print(f"最好组合: {best} → {mean_ret.max():.2%}")
worst = mean_ret.idxmin()
print(f"最差组合: {worst} → {mean_ret.min():.2%}")

# 当前实盘参数 (20,10) 的排名
cur = mean_ret.get((20, 10), None)
if cur is not None:
    rank = (mean_ret > cur).sum() + 1
    print(f"\n当前实盘 (20,10): {cur:.2%}, 排名 {rank}/{len(mean_ret)}")

# 6. 按 N_ENTRY 看趋势
by_entry = mean_ret.groupby('N_ENTRY').mean()
print(f"\n=== 按 N_ENTRY 平均收益 ===")
for e, v in by_entry.items():
    print(f"  N_ENTRY={e}: {v:.2%}")

# 7. 按票看当前参数表现（16 票各自 (20,10) 收益）
print(f"\n=== 各票 (20,10) 收益 ===")
per_symbol = total_return.xs((20, 10), level=('N_ENTRY', 'N_EXIT'))
for sym in symbols:
    print(f"  {sym:10s}: {per_symbol.get(sym, float('nan')):.2%}")

# 8. 保存
mean_ret.unstack(level='N_EXIT').to_csv('/home/gyan/bstocks_scan.csv')
print(f"\n热力图已存: /home/gyan/bstocks_scan.csv")

# 9. 稳定性：切 4 段看 (20,10) 平均表现
print(f"\n=== 稳定性检查（切 4 段, 20/10 跨票平均）===")
try:
    seg_returns = []
    for i in range(4):
        seg_start = len(common_idx) * i // 4
        seg_end = len(common_idx) * (i + 1) // 4
        seg_idx = common_idx[seg_start:seg_end]
        seg_prices = price_df.loc[seg_idx]
        don_up = seg_prices.rolling(20).max().shift(1)
        don_ex = seg_prices.rolling(10).min().shift(1)
        p = vbt.Portfolio.from_signals(
            seg_prices, seg_prices > don_up, seg_prices < don_ex,
            freq='1D', init_cash=10000, fees=0.001)
        seg_returns.append(float(p.total_return().mean()))
    print(f"  4 段跨票平均: {[f'{x:.1%}' for x in seg_returns]}")
except Exception as e:
    print(f"  切段失败: {e}")
