"""
分析当前 SC→AR 周期的 OI 变化
2026-06-07 SC → 2026-06-14 AR 完成
"""
import sys
sys.path.insert(0, '/freqtrade')

import pandas as pd
import numpy as np
from pathlib import Path

DATA_PATH = Path('/freqtrade/user_data/data/binance/futures')

# 价格数据
df_price = pd.read_feather(DATA_PATH / 'BTC_USDT_USDT-1d-futures.feather')
df_price = df_price[df_price['date'] >= '2026-05-15'].copy().reset_index(drop=True)

# OI 数据
df_oi = pd.read_feather(DATA_PATH / 'BTC_USDT_USDT-1d-open_interest.feather')

# 统一时区后合并
df_price['date'] = df_price['date'].dt.tz_localize(None)
df_oi['date']    = pd.to_datetime(df_oi['date']).dt.tz_localize(None)
df = df_price.merge(df_oi, on='date', how='left')

# 计算 OI 变化
df['oi_chg_pct'] = df['open_interest'].pct_change() * 100
df['oi_value_chg_pct'] = df['open_interest_value'].pct_change() * 100

# 标记关键事件
def mark_event(date_str):
    return '★ SC' if date_str == '2026-06-07' else \
           '◆ AR' if date_str == '2026-06-14' else ''

print('=== 当前周期 OI + 价格 日线数据 ===\n')
print(f'{"日期":<12} {"close":>9} {"涨跌%":>7} {"OI(BTC)":>12} {"OI变化%":>9} {"OI价值(亿)":>11} {"事件"}')
print('-'*80)

for _, row in df.iterrows():
    date_str = str(row['date'].date())
    event = mark_event(date_str)
    oi = row['open_interest']
    oi_val = row['open_interest_value'] / 1e8  # 亿 USDT
    oi_chg = row['oi_chg_pct']
    price_chg = (row['close'] - row['open']) / row['open'] * 100
    oi_str = f'{oi:>12,.0f}' if not np.isnan(oi) else f'{"N/A":>12}'
    oi_chg_str = f'{oi_chg:>+8.2f}%' if not np.isnan(oi_chg) else f'{"N/A":>9}'
    oi_val_str = f'{oi_val:>10.2f}亿' if not np.isnan(oi_val) else f'{"N/A":>11}'
    print(f'{date_str:<12} {row["close"]:>9,.0f} {price_chg:>+6.1f}%  {oi_str} {oi_chg_str} {oi_val_str}  {event}')

# ── 关键节点分析 ──
print('\n=== 关键节点 OI 分析 ===\n')

sc_row = df[df['date'].dt.date.astype(str) == '2026-06-07']
ar_row = df[df['date'].dt.date.astype(str) == '2026-06-14']
pre_sc = df[df['date'].dt.date.astype(str) == '2026-06-06']
today  = df.iloc[-1]

if not sc_row.empty and not ar_row.empty:
    sc_oi    = sc_row['open_interest'].values[0]
    ar_oi    = ar_row['open_interest'].values[0]
    pre_oi   = pre_sc['open_interest'].values[0] if not pre_sc.empty else np.nan
    today_oi = today['open_interest']

    sc_price  = sc_row['close'].values[0]
    ar_price  = ar_row['close'].values[0]
    today_price = today['close']

    print(f'SC 前一日（6/6）  OI: {pre_oi:>10,.0f} BTC   价格: {pre_sc["close"].values[0]:>9,.0f}')
    print(f'SC 当日  （6/7）  OI: {sc_oi:>10,.0f} BTC   价格: {sc_price:>9,.0f}  '
          f'OI变化: {(sc_oi-pre_oi)/pre_oi*100:+.2f}%')
    print(f'AR 完成  （6/14） OI: {ar_oi:>10,.0f} BTC   价格: {ar_price:>9,.0f}  '
          f'OI变化(vs SC): {(ar_oi-sc_oi)/sc_oi*100:+.2f}%')
    print(f'今日     （6/15） OI: {today_oi:>10,.0f} BTC   价格: {today_price:>9,.0f}  '
          f'OI变化(vs SC): {(today_oi-sc_oi)/sc_oi*100:+.2f}%')

    print()
    print('=== Wyckoff OI 解读 ===\n')

    # SC 时 OI 变化
    sc_oi_chg = (sc_oi - pre_oi) / pre_oi * 100
    if sc_oi_chg < -3:
        print(f'✅ SC 当天 OI 下降 {sc_oi_chg:.1f}%：多头爆仓+空头平仓，真实恐慌性清洗，SC 可信度高')
    elif sc_oi_chg > 3:
        print(f'⚠️ SC 当天 OI 上升 {sc_oi_chg:.1f}%：新空头入场，非恐慌底，可能继续下跌')
    else:
        print(f'➖ SC 当天 OI 变化 {sc_oi_chg:.1f}%：中性，信号不明确')

    # SC→AR OI 变化
    ar_oi_chg = (ar_oi - sc_oi) / sc_oi * 100
    if ar_oi_chg > 5:
        print(f'⚠️ SC→AR OI 上升 {ar_oi_chg:.1f}%：可能是逼空行情，新空头入场被动回补，反弹可持续性存疑')
    elif ar_oi_chg < -3:
        print(f'✅ SC→AR OI 下降 {ar_oi_chg:.1f}%：空头继续平仓，需求真实，积累结构有支撑')
    else:
        print(f'➖ SC→AR OI 变化 {ar_oi_chg:.1f}%：中性，空头未明显离场也未加仓')

    # AR→今日 OI 变化
    today_oi_chg = (today_oi - ar_oi) / ar_oi * 100
    if today_oi_chg > 5:
        print(f'📈 AR→今日 OI 上升 {today_oi_chg:.1f}%：Phase B 期间新多头建仓，积累结构强化')
    elif today_oi_chg < -3:
        print(f'📉 AR→今日 OI 下降 {today_oi_chg:.1f}%：Phase B 期间去杠杆，多头谨慎')
    else:
        print(f'➖ AR→今日 OI 变化 {today_oi_chg:.1f}%：Phase B 震荡，OI 平稳')

    # 综合判断
    print()
    print('=== 综合判断 ===')
    sc_valid = sc_oi_chg <= 0  # SC 时 OI 不涨
    ar_healthy = ar_oi_chg < 5  # AR 时 OI 不大幅拉升
    today_building = today_oi_chg > 0  # Phase B OI 温和上升

    signals = [sc_valid, ar_healthy, today_building]
    score = sum(signals)
    labels = ['SC时OI不涨(真恐慌)', 'AR时OI不暴涨(非逼空)', 'Phase B OI回升(需求建立)']
    for s, l in zip(signals, labels):
        print(f'  {"✅" if s else "❌"} {l}')
    print(f'\n  积累结构 OI 强度评分: {score}/3')
    if score == 3:
        print('  → 三项全满：OI 结构支持真实积累，Phase D/E 入场胜率高')
    elif score == 2:
        print('  → 基本成立：积累结构有一定支撑，但需关注 Phase B 期间 OI 变化')
    else:
        print('  → 信号偏弱：OI 结构不支持强积累，Phase B 可能延长或失败')
