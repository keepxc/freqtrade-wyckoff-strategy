"""
审计：Phase B 锁定期间（day 7~20）的错过机会
统计历史上有多少次价格在 Phase B 等待期内出现显著上涨，但因 phase_b_min_days=20 无法开仓。
"""
import sys, json
sys.path.insert(0, '/freqtrade')

import pandas as pd
import numpy as np
from pathlib import Path
from user_data.strategies.v8_modules.sc_detector import add_v8_sc_detection
from user_data.strategies.v8_modules.phase_detector import add_v8_phase_detection, DEFAULT_PHASE_PARAMS

# ── 加载日线数据 ──
DATA_PATH = Path('/freqtrade/user_data/data/binance/futures')
pair_file = DATA_PATH / 'BTC_USDT_USDT-1d-futures.feather'
df = pd.read_feather(pair_file)
df = df[df['date'] >= '2022-01-01'].copy().reset_index(drop=True)
print(f'日线数据: {len(df)} 根 ({df["date"].iloc[0].date()} ~ {df["date"].iloc[-1].date()})')

# ── 运行 SC + Phase 检测（基线参数 phase_b_min_days=20）──
df = add_v8_sc_detection(df)
df_b20 = add_v8_phase_detection(df.copy(), {'phase_b_min_days': 20})

# ── 同时运行 7 天版本，用于对比 ──
df_b7  = add_v8_phase_detection(df.copy(), {'phase_b_min_days': 7})

# ── 找出所有 Phase B 段落 ──
phases_b20 = df_b20['v8_phase'].values
phases_b7  = df_b7['v8_phase'].values
dates = df_b20['date'].values
closes = df_b20['close'].values
highs  = df_b20['high'].values

print('\n=== Phase B 锁定窗口（day 7~20）内的价格运动 ===\n')

missed_windows = []
i = 0
n = len(df_b20)

while i < n:
    # 找 Phase B 开始
    if phases_b20[i] == 'B':
        b_start = i
        # 找 Phase B 结束（转为其他 Phase）
        j = i + 1
        while j < n and phases_b20[j] == 'B':
            j += 1
        b_end = j  # 不含
        b_len = b_end - b_start

        if b_len >= 7:
            # 窗口：day 7~20（索引 b_start+7 ~ b_start+20）
            win_start = b_start + 7
            win_end   = min(b_start + 20, b_end, n)

            if win_start < win_end:
                # 这段期间的价格变动
                win_closes = closes[win_start:win_end]
                win_highs  = highs[win_start:win_end]
                entry_price = closes[win_start - 1]  # 第7天收盘（解锁前最后一根）

                max_high = win_highs.max()
                max_close = win_closes.max()
                move_pct = (max_high - entry_price) / entry_price * 100

                # b7 版本在这个窗口期间是否已经进入 D/E
                b7_phases_in_win = phases_b7[win_start:win_end]
                b7_entered_de = any(p in ('D', 'E') for p in b7_phases_in_win)

                missed_windows.append({
                    'b_start_date': pd.Timestamp(dates[b_start]).date(),
                    'b_end_date': pd.Timestamp(dates[b_end-1]).date() if b_end > b_start else None,
                    'b_len': b_len,
                    'win_start_date': pd.Timestamp(dates[win_start]).date(),
                    'entry_price': entry_price,
                    'max_high': max_high,
                    'move_pct': move_pct,
                    'b7_entered_de': b7_entered_de,
                })
        i = b_end
    else:
        i += 1

# ── 汇总输出 ──
total = len(missed_windows)
sig5  = [w for w in missed_windows if w['move_pct'] >= 5]
sig10 = [w for w in missed_windows if w['move_pct'] >= 10]
b7_would_catch = [w for w in missed_windows if w['b7_entered_de'] and w['move_pct'] >= 5]

print(f'历史 Phase B 段落总数（≥7天）: {total}')
print(f'其中锁定期内价格上涨 ≥5%:  {len(sig5)} 次')
print(f'其中锁定期内价格上涨 ≥10%: {len(sig10)} 次')
print(f'其中 b7=7天版本能捕获且涨≥5%: {len(b7_would_catch)} 次')
print()

print(f'{"Phase B开始":<14} {"B结束":<12} {"B天数":>6} {"窗口开始":>12} {"入场价":>10} {"最高涨幅":>9} {"b7能进D/E"}')
print('-'*80)
for w in sorted(missed_windows, key=lambda x: -x['move_pct']):
    flag = '✓' if w['b7_entered_de'] else ' '
    sig = '***' if w['move_pct'] >= 10 else ('**' if w['move_pct'] >= 5 else '')
    print(f'{str(w["b_start_date"]):<14} {str(w["b_end_date"]):<12} {w["b_len"]:>6} '
          f'{str(w["win_start_date"]):>12} {w["entry_price"]:>10,.0f} '
          f'{w["move_pct"]:>+8.1f}% {flag}  {sig}')

print()
print(f'结论：历史上 {len(sig5)} 次 Phase B 锁定期内价格上涨≥5%，'
      f'其中 {len(b7_would_catch)} 次用 7天版本可以捕获。')
print(f'但 TASK 34 回测显示，7天版本总收益 -101,042pp，因为：')
print(f'  提前进 D/E 阻断了 Phase B 期间的空头，空头贡献损失 > 多头新增')
