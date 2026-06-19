"""
审计：469笔多头入场前24H内，是否出现 1H级别 SOS + LPS缩量回踩 组合
目的：验证 LPS 前向指纹的覆盖率与提前量，判断是否值得立项

SOS 定义（1H强势上升波雏形）：
  - close > open（阳线）
  - 价差 > ATR14 * 0.6（宽价差，有力）
  - volume > vol_ma10 * 1.2（放量，需求主导）
  - 涨幅 > 0.5%

LPS 回踩定义（SOS后的缩量回调）：
  - 出现在 SOS 之后（1~6根K线内）
  - close < open（阴线，价格回踩）
  - volume < vol_ma10 * 0.7（缩量，无供应）
  - 价差 < SOS价差 * 0.7（价差收窄）
  - 收盘不破 SOS收盘价（支撑有效）
"""
import sys, json, zipfile
sys.path.insert(0, '/freqtrade')

import pandas as pd
import numpy as np
from pathlib import Path

# ── 加载回测交易 ──
ZIP = Path('/freqtrade/user_data/backtest_results/backtest-result-2026-06-12_04-04-59.zip')
with zipfile.ZipFile(ZIP) as z:
    fname = [f for f in z.namelist() if f.endswith('.json') and 'meta' not in f][0]
    bt = json.loads(z.read(fname))
strat = list(bt['strategy'].keys())[0]
trades = pd.DataFrame(bt['strategy'][strat]['trades'])
trades['open_date'] = pd.to_datetime(trades['open_date']).dt.tz_localize(None)
long_trades = trades[trades['is_short'] == False].copy()

# ── 加载并预处理 1H 数据 ──
df = pd.read_feather('/freqtrade/user_data/data/binance/futures/BTC_USDT_USDT-1h-futures.feather')
df['date'] = pd.to_datetime(df['date']).dt.tz_localize(None)
df = df.sort_values('date').reset_index(drop=True)

# 指标
high, low, close, open_ = df['high'], df['low'], df['close'], df['open']
tr = pd.concat([high - low,
                (high - close.shift(1)).abs(),
                (low  - close.shift(1)).abs()], axis=1).max(axis=1)
df['atr14']    = tr.ewm(span=14, adjust=False).mean()
df['vol_ma10'] = df['volume'].rolling(10, min_periods=3).mean()
df['spread']   = df['high'] - df['low']
df['move_pct'] = (df['close'] - df['open']) / df['open'] * 100

# SOS 标记
df['is_sos'] = (
    (df['close'] > df['open']) &
    (df['spread'] > df['atr14'] * 0.6) &
    (df['volume'] > df['vol_ma10'] * 1.2) &
    (df['move_pct'] > 0.5)
)

# ── 核心审计：逐笔检查 ──
print(f'=== LPS 前向指纹审计（469笔多头入场前24H）===\n')
print(f'{"入场时间":<22} {"标签":<10} {"价格":>8} {"SOS":>5} {"LPS":>5} {"SOS→LPS→入场":>16} {"提前h":>8}')
print('-'*85)

results = []

for _, trade in long_trades.iterrows():
    entry_dt  = trade['open_date']
    entry_px  = trade['open_rate']
    tag       = str(trade['enter_tag'])[:8]
    window_start = entry_dt - pd.Timedelta(hours=24)

    win = df[(df['date'] >= window_start) & (df['date'] < entry_dt)].copy()
    if len(win) < 4:
        results.append({'has_sos': False, 'has_lps': False, 'lead_h': None})
        continue

    # 在窗口内找 SOS
    sos_bars = win[win['is_sos']]
    has_sos = len(sos_bars) > 0

    has_lps = False
    lead_h  = None
    lps_dt  = None

    if has_sos:
        # 对每个 SOS，找其后 1~6 根内的 LPS
        for _, sos_row in sos_bars.iterrows():
            sos_idx   = win.index.get_loc(sos_row.name)
            sos_close = sos_row['close']
            sos_spread = sos_row['spread']

            post_sos = win.iloc[sos_idx+1 : sos_idx+7]
            for _, lps_row in post_sos.iterrows():
                is_lps = (
                    (lps_row['close'] < lps_row['open']) &      # 阴线
                    (lps_row['volume'] < lps_row['vol_ma10'] * 0.7) &  # 缩量
                    (lps_row['spread'] < sos_spread * 0.7) &    # 价差收窄
                    (lps_row['close'] >= sos_close * 0.995)     # 不破SOS收盘
                )
                if is_lps:
                    has_lps = True
                    lps_dt  = lps_row['date']
                    lead_h  = (entry_dt - lps_dt).total_seconds() / 3600
                    break
            if has_lps:
                break

    results.append({'has_sos': has_sos, 'has_lps': has_lps, 'lead_h': lead_h})

    # 打印（只打印有信号的，或前20笔）
    sos_str = '✅' if has_sos else '❌'
    lps_str = '✅' if has_lps else ('—' if not has_sos else '❌')
    combo   = f'{lead_h:.0f}h 前' if lead_h else '—'
    if has_lps or (not has_sos and trade.name < long_trades.index[20]):
        print(f'{str(entry_dt)[:19]:<22} {tag:<10} {entry_px:>8,.0f} {sos_str:>5} {lps_str:>5} {"✅ 命中" if has_lps else "":>16} {combo:>8}')

# ── 汇总 ──
df_res = pd.DataFrame(results)
n = len(df_res)
n_sos = df_res['has_sos'].sum()
n_lps = df_res['has_lps'].sum()
leads = df_res[df_res['lead_h'].notna()]['lead_h'].values

print(f'\n{"="*70}')
print(f'=== 汇总（共 {n} 笔多头入场）===\n')
print(f'前24H 内有 SOS 信号         : {n_sos:3d} 笔 ({n_sos/n*100:.1f}%)')
print(f'前24H 内有 SOS + LPS 组合   : {n_lps:3d} 笔 ({n_lps/n*100:.1f}%)')
print(f'有SOS但无LPS                : {n_sos-n_lps:3d} 笔')
print(f'无SOS                       : {n-n_sos:3d} 笔 ({(n-n_sos)/n*100:.1f}%)')

if len(leads) > 0:
    print(f'\nLPS → 入场 提前时间统计:')
    print(f'  中位数 : {np.median(leads):.1f}h')
    print(f'  均值   : {np.mean(leads):.1f}h')
    print(f'  最小   : {np.min(leads):.1f}h')
    print(f'  最大   : {np.max(leads):.1f}h')
    print(f'  提前 >6h  : {(leads>6).sum()} 笔 ({(leads>6).mean()*100:.1f}%)')
    print(f'  提前 >12h : {(leads>12).sum()} 笔 ({(leads>12).mean()*100:.1f}%)')

print(f'\n=== 立项判断 ===')
coverage = n_lps / n
if coverage > 0.4 and len(leads) > 0 and np.median(leads) > 6:
    print(f'✅ 覆盖率 {coverage*100:.1f}%，提前量中位 {np.median(leads):.0f}h → 值得立项')
elif coverage > 0.2:
    print(f'⚠️ 覆盖率 {coverage*100:.1f}%，信号存在但覆盖不足 → 调整参数后再评估')
else:
    print(f'❌ 覆盖率 {coverage*100:.1f}% → 当前定义不适用，需重新设计')
