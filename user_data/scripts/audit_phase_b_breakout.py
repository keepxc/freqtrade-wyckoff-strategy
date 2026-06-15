"""
审计：Phase B 期间（day 0~20）直接突破 TR High 的历史情况
统计：
1. 多少次在 Phase B 未满 20 天时出现 close > TR High
2. 突破后是否持续上涨（真突破 vs UTAD 假突破）
3. 当前 V8 设计因此错过了多少利润
"""
import sys
sys.path.insert(0, '/freqtrade')

import pandas as pd
import numpy as np
from pathlib import Path
from user_data.strategies.v8_modules.sc_detector import add_v8_sc_detection
from user_data.strategies.v8_modules.phase_detector import add_v8_phase_detection

DATA_PATH = Path('/freqtrade/user_data/data/binance/futures')
pair_file = DATA_PATH / 'BTC_USDT_USDT-1d-futures.feather'
df = pd.read_feather(pair_file)
df = df[df['date'] >= '2022-01-01'].copy().reset_index(drop=True)
print(f'日线数据: {len(df)} 根 ({df["date"].iloc[0].date()} ~ {df["date"].iloc[-1].date()})')

df = add_v8_sc_detection(df)
df = add_v8_phase_detection(df)

n = len(df)
phases = df['v8_phase'].values
closes = df['close'].values
highs  = df['high'].values
lows   = df['low'].values
dates  = df['date'].values
tr_highs = df['v8_tr_high'].values

print('\n=== Phase B 期间突破 TR High 的情况（day 0~19）===\n')

breakouts = []
i = 0
while i < n:
    if phases[i] == 'B':
        b_start = i
        # 找这段 Phase B 的开始
        while b_start > 0 and phases[b_start - 1] == 'B':
            b_start -= 1

        days_in_b = i - b_start
        tr_h = tr_highs[i]

        if days_in_b < 20 and not np.isnan(tr_h) and closes[i] > tr_h:
            # Phase B 未满20天就突破了 TR High
            entry_price = closes[i]
            breakout_day = days_in_b

            # 统计突破后的走势
            future_highs = []
            future_lows = []
            for fw in [5, 10, 20, 30]:
                end = min(i + fw, n - 1)
                future_highs.append(highs[i:end+1].max())
                future_lows.append(lows[i:end+1].min())

            # 判断是真突破还是 UTAD
            # 标准：突破后 10 天内最低价是否跌回 TR High 以下
            low_10d = lows[i:min(i+10, n)].min()
            utad = low_10d < tr_h  # 跌回 TR High 以下 = UTAD

            # 突破后 20 天最大涨幅
            high_20d = highs[i:min(i+20, n)].max()
            max_gain_20d = (high_20d - entry_price) / entry_price * 100

            # 突破后 20 天最大回撤
            low_20d = lows[i:min(i+20, n)].min()
            max_dd_20d = (entry_price - low_20d) / entry_price * 100

            breakouts.append({
                'date': pd.Timestamp(dates[i]).date(),
                'b_start_date': pd.Timestamp(dates[b_start]).date(),
                'b_start_idx': b_start,
                'breakout_day': breakout_day,
                'tr_high': tr_h,
                'entry_price': entry_price,
                'utad': utad,
                'low_10d': low_10d,
                'max_gain_20d': max_gain_20d,
                'max_dd_20d': max_dd_20d,
                'future_high_5d': future_highs[0],
                'future_high_10d': future_highs[1],
                'future_high_20d': future_highs[2],
            })
            # 跳过这段 Phase B（避免同一段重复统计）
            while i < n and phases[i] == 'B' and (i - b_start) < 20:
                i += 1
            continue
    i += 1

# ── 输出 ──
total = len(breakouts)
true_breaks = [b for b in breakouts if not b['utad']]
utads = [b for b in breakouts if b['utad']]

print(f'Phase B 未满20天突破 TR High 总次数: {total}')
print(f'  真突破（10天内未跌回）: {len(true_breaks)}')
print(f'  UTAD 假突破（10天内跌回 TR High）: {len(utads)}')
print()

print(f'{"日期":<12} {"B开始":<12} {"B第N天":>7} {"TR_High":>9} {"突破价":>9} {"20天最高涨":>11} {"20天最大跌":>11} {"判断":>8}')
print('-'*90)
for b in sorted(breakouts, key=lambda x: x['date']):
    verdict = 'UTAD ✗' if b['utad'] else '真突破 ✓'
    print(f'{str(b["date"]):<12} {str(b["b_start_date"]):<12} {b["breakout_day"]:>7} '
          f'{b["tr_high"]:>9,.0f} {b["entry_price"]:>9,.0f} '
          f'{b["max_gain_20d"]:>+10.1f}% {b["max_dd_20d"]:>+10.1f}% {verdict:>8}')

print()
if true_breaks:
    avg_gain = sum(b['max_gain_20d'] for b in true_breaks) / len(true_breaks)
    print(f'真突破平均20天最高涨幅: +{avg_gain:.1f}%')
if utads:
    avg_gain = sum(b['max_gain_20d'] for b in utads) / len(utads)
    avg_dd = sum(b['max_dd_20d'] for b in utads) / len(utads)
    print(f'UTAD 平均20天最高涨幅: +{avg_gain:.1f}%  平均最大回撤: -{avg_dd:.1f}%')

print()
print('=== 结论 ===')
print(f'4年历史中，Phase B 未满20天突破 TR High 共 {total} 次')
print(f'其中 {len(true_breaks)} 次是真突破，{len(utads)} 次是 UTAD 陷阱')
if total > 0:
    print(f'真突破率: {len(true_breaks)/total*100:.0f}%')
    missed_gain = sum(b['max_gain_20d'] for b in true_breaks)
    print(f'真突破合计20天最高涨幅（被 V8 当前设计错过）: {missed_gain:.1f}%（单笔之和，非复利）')
