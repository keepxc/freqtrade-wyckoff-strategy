"""
审计：Phase D/E 多头入场前 48H，TR Low 附近是否出现 No Supply 指纹
目的：验证 VSA 前向信号的可行性，为"是否立项"提供数据依据
"""
import sys, json, zipfile
sys.path.insert(0, '/freqtrade')

import pandas as pd
import numpy as np
from pathlib import Path

# ── 1. 加载回测交易记录（多头入场）──────────────────────────────────────────
ZIP = Path('/freqtrade/user_data/backtest_results/backtest-result-2026-06-12_04-04-59.zip')
with zipfile.ZipFile(ZIP) as z:
    fname = [f for f in z.namelist() if f.endswith('.json') and 'meta' not in f][0]
    bt = json.loads(z.read(fname))

strat = list(bt['strategy'].keys())[0]
trades = pd.DataFrame(bt['strategy'][strat]['trades'])
trades['open_date'] = pd.to_datetime(trades['open_date']).dt.tz_localize(None)
trades['close_date'] = pd.to_datetime(trades['close_date'])

# 只取多头入场
long_trades = trades[trades['is_short'] == False].copy()
long_tags = long_trades['enter_tag'].value_counts()
print(f'多头入场标签分布：')
print(long_tags.to_string())
print(f'\n多头总笔数: {len(long_trades)}')

# 取 Phase D/E 相关标签（非 wsdown）
phase_de_tags = [t for t in long_trades['enter_tag'].unique() if 'ws' in t or 'sow' in t or 'lpsy' not in t]
long_de = long_trades[long_trades['enter_tag'].isin(
    [t for t in long_trades['enter_tag'].unique() if 'short' not in str(t)]
)].copy()
print(f'\nPhase D/E 多头笔数: {len(long_de)}')

# ── 2. 加载 1H 数据（含 ATR、volume）────────────────────────────────────────
df1h = pd.read_feather('/freqtrade/user_data/data/binance/futures/BTC_USDT_USDT-1h-futures.feather')
df1h['date'] = pd.to_datetime(df1h['date']).dt.tz_localize(None)
df1h = df1h.sort_values('date').reset_index(drop=True)

# ATR14
high, low, close = df1h['high'], df1h['low'], df1h['close']
tr = pd.concat([high - low,
                (high - close.shift(1)).abs(),
                (low  - close.shift(1)).abs()], axis=1).max(axis=1)
df1h['atr14'] = tr.ewm(span=14, adjust=False).mean()
df1h['vol_ma10'] = df1h['volume'].rolling(10, min_periods=3).mean()
df1h['spread'] = df1h['high'] - df1h['low']
df1h['close_pos'] = (df1h['close'] - df1h['low']) / (df1h['spread'] + 1e-9)  # 0=低收 1=高收

# ── 3. 加载日线 Phase 状态（TR_Low）────────────────────────────────────────
df1d = pd.read_feather('/freqtrade/user_data/data/binance/futures/BTC_USDT_USDT-1d-futures.feather')
df1d['date'] = pd.to_datetime(df1d['date']).dt.tz_localize(None)

# 简化重建 TR Low：每个 SC 事件的低点
# 用日线滚动 100 日新低作为 SC 候选
df1d['roll_low_100'] = df1d['low'].rolling(100, min_periods=20).min()
df1d['is_sc'] = (df1d['low'] == df1d['roll_low_100']) & (df1d['volume'] > df1d['volume'].rolling(20).mean() * 1.5)

# 构建 TR Low 映射（SC 后的 TR Low = SC 当日低点，持续到下一个 SC）
tr_low_series = pd.Series(np.nan, index=df1d.index)
current_tr_low = np.nan
for i, row in df1d.iterrows():
    if row['is_sc']:
        current_tr_low = row['low']
    tr_low_series[i] = current_tr_low
df1d['tr_low'] = tr_low_series
df1d_map = df1d.set_index('date')[['tr_low']].copy()

# merge TR Low 到 1H
df1h['date_day'] = df1h['date'].dt.normalize()
df1h = df1h.merge(df1d_map.rename(columns={'tr_low': 'tr_low'}),
                  left_on='date_day', right_index=True, how='left')
df1h['tr_low'] = df1h['tr_low'].ffill()

# ── 4. No Supply 指纹定义 ─────────────────────────────────────────────────
# 条件：
#   A. 收盘 > (high+low)/2（偏高收盘，买方吸收）
#   B. spread < ATR14 * 0.5（窄价差）
#   C. volume < vol_ma10 * 0.6（极低量）
#   D. close 在 TR Low * 1.05 以内（靠近支撑区）

df1h['no_supply'] = (
    (df1h['close_pos'] > 0.5) &
    (df1h['spread'] < df1h['atr14'] * 0.5) &
    (df1h['volume'] < df1h['vol_ma10'] * 0.6) &
    (df1h['close'] <= df1h['tr_low'] * 1.05) &
    (df1h['tr_low'] > 0)
)

total_no_supply = df1h['no_supply'].sum()
print(f'\n全历史 No Supply 信号总数: {total_no_supply} 根（共 {len(df1h)} 根 1H K线）')
print(f'信号频率: {total_no_supply/len(df1h)*100:.2f}%')

# ── 5. 核心分析：多头入场前 48H 内是否有 No Supply ─────────────────────────
print(f'\n=== Phase D/E 多头入场前 48H No Supply 分析 ===\n')
print(f'{"入场时间":<22} {"入场标签":<20} {"入场价":>9} {"前48H内No Supply":>16} {"最近NS距入场":>14} {"提前小时":>10}')
print('-'*100)

lead_times = []
has_signal = 0

for _, trade in long_de.iterrows():
    entry_dt = trade['open_date']
    window_start = entry_dt - pd.Timedelta(hours=48)

    # 入场前 48H 的 1H 数据
    mask = (df1h['date'] >= window_start) & (df1h['date'] < entry_dt)
    window = df1h[mask]

    ns_in_window = window[window['no_supply']]
    count = len(ns_in_window)

    if count > 0:
        has_signal += 1
        last_ns = ns_in_window.iloc[-1]['date']
        hours_before = (entry_dt - last_ns).total_seconds() / 3600
        lead_times.append(hours_before)
        lead_str = f'{hours_before:.1f}h 前'
    else:
        lead_str = '—'
        hours_before = None

    tag = str(trade['enter_tag'])[:18]
    print(f'{str(entry_dt)[:19]:<22} {tag:<20} {trade["open_rate"]:>9,.0f} '
          f'{"✅ "+str(count)+"根" if count>0 else "❌ 无":>16} {lead_str:>14}')

# ── 6. 统计汇总 ─────────────────────────────────────────────────────────────
print(f'\n=== 汇总 ===')
total = len(long_de)
print(f'多头入场总笔数: {total}')
print(f'前48H内有 No Supply 信号: {has_signal} 笔 ({has_signal/total*100:.1f}%)')
print(f'前48H内无 No Supply 信号: {total-has_signal} 笔 ({(total-has_signal)/total*100:.1f}%)')

if lead_times:
    arr = np.array(lead_times)
    print(f'\nNo Supply → 入场 提前时间:')
    print(f'  中位数: {np.median(arr):.1f}h')
    print(f'  均值:   {np.mean(arr):.1f}h')
    print(f'  最小:   {np.min(arr):.1f}h')
    print(f'  最大:   {np.max(arr):.1f}h')
    print(f'  提前 >12h: {(arr>12).sum()} 笔 ({(arr>12).mean()*100:.1f}%)')
    print(f'  提前 >24h: {(arr>24).sum()} 笔 ({(arr>24).mean()*100:.1f}%)')

print(f'\n=== 结论 ===')
if has_signal / total > 0.5 and lead_times and np.median(lead_times) > 12:
    print('✅ No Supply 指纹在多数多头入场前存在，且提前量 >12H → 值得立项')
elif has_signal / total > 0.3:
    print('⚠️ No Supply 指纹存在但覆盖率不足 → 需调整参数或叠加条件')
else:
    print('❌ No Supply 指纹覆盖率低 → 当前定义不适用，需重新设计')
