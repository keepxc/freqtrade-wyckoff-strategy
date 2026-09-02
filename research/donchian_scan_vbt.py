"""
Donchian 参数扫描 demo —— vectorbt 学习示例
用 BTC 1d 真实数据扫 (N_ENTRY, N_EXIT) 组合，找最优参数区
对照：当前实盘用的是 (20, 10)
"""
import warnings
warnings.filterwarnings('ignore')
import numpy as np
import pandas as pd
import vectorbt as vbt

# 1. 加载 freqtrade 数据（共享数据管道）
df = pd.read_feather('/home/gyan/freqtrade/user_data/data/okx/BTC_USDT-1d.feather')
df['date'] = pd.to_datetime(df['date'], utc=True)
price = df.set_index('date')['close']
print(f"数据: {len(df)} 根 1d K 线, {df['date'].iloc[0]:%Y-%m-%d} ~ {df['date'].iloc[-1]:%Y-%m-%d}")

# 2. 参数网格：N_ENTRY 5~40, N_EXIT 5~30（交叉 36×26=936 组合）
entry_windows = np.arange(5, 41)
exit_windows = np.arange(5, 31)
print(f"参数网格: {len(entry_windows)}×{len(exit_windows)} = {len(entry_windows)*len(exit_windows)} 组合")

# 3. 向量化生成 Donchian 信号（一次算全部组合）
#    直接构造 (bars × combos) 矩阵——vectorbt 的 Portfolio.from_signals 接受多列信号
cols = pd.MultiIndex.from_product([entry_windows, exit_windows], names=['N_ENTRY', 'N_EXIT'])
entries = pd.DataFrame(index=price.index, columns=cols, dtype=bool)
exits = pd.DataFrame(index=price.index, columns=cols, dtype=bool)
for e in entry_windows:
    don_up = price.rolling(e).max().shift(1)
    for x in exit_windows:
        don_ex = price.rolling(x).min().shift(1)
        entries[(e, x)] = price > don_up
        exits[(e, x)] = price < don_ex

print(f"信号矩阵: {entries.shape[0]} bars × {entries.shape[1]} 组合")

# 4. 一次性回测全部组合（vectorbt 核心：矩阵化）
pf = vbt.Portfolio.from_signals(
    price, entries, exits,
    freq='1D',  # 日频
    init_cash=10000,
    fees=0.001,  # 0.1% 手续费（OKX 现货实际水平）
    slippage=0.001,  # 0.1% 滑点
)

# 5. 结果分析
total_return = pf.total_return()
print(f"\n=== 扫描结果 ===")
print(f"全部组合中位数收益: {total_return.median():.2%}")
print(f"最好组合: {total_return.idxmax()} → {total_return.max():.2%}")
print(f"最差组合: {total_return.idxmin()} → {total_return.min():.2%}")

# 当前实盘参数 (20,10) 的排名
cur = total_return.get((20, 10), None) if isinstance(total_return, pd.Series) else None
if cur is not None:
    rank = (total_return > cur).sum() + 1
    print(f"\n当前实盘 (20,10): {cur:.2%}, 排名 {rank}/{len(total_return)}")

# 6. 按 N_ENTRY 分组看均值（趋势发现）
by_entry = total_return.groupby('N_ENTRY').mean()
print(f"\n=== 按 N_ENTRY 平均收益（找趋势）===")
for e, v in by_entry.items():
    print(f"  N_ENTRY={e}: {v:.2%}")

# 7. 热力图数据（保存 CSV 供查看）
result = total_return.unstack(level='N_EXIT')  # rows=ENTRY, cols=EXIT
result.to_csv('/home/gyan/donchian_scan.csv')
print(f"\n热力图已存: /home/gyan/donchian_scan.csv ({result.shape[0]}×{result.shape[1]})")

# 8. 最优 5 个组合
print(f"\n=== Top 5 组合 ===")
top5 = total_return.nlargest(5)
for idx, v in top5.items():
    print(f"  {idx}: {v:.2%}")

# 9. 稳定性检查：range_split（按年切，看 20/10 是否每年都好）
print(f"\n=== 稳定性检查（按半年切）===")
for n in [2, 4]:
    try:
        splits = price.vbt.range_split(n=n)
        # range_split 返回 (DataFrame, split_index)，DataFrame 每列是一个分段
        splits_df = splits[0] if isinstance(splits, tuple) else splits
        seg_returns = []
        for col in splits_df.columns:
            seg = splits_df[col].dropna()
            don_up = seg.rolling(20).max().shift(1)
            don_ex = seg.rolling(10).min().shift(1)
            p = vbt.Portfolio.from_signals(seg, seg > don_up, seg < don_ex, freq='1D', init_cash=10000, fees=0.001)
            seg_returns.append(float(p.total_return().iloc[0]) if hasattr(p.total_return(), 'iloc') else float(p.total_return()))
        print(f"  切 {n} 段: {[f'{x:.1%}' for x in seg_returns]}")
    except Exception as e:
        print(f"  切 {n} 段失败: {e}")
