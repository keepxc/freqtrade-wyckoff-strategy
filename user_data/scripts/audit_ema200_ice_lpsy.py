"""
EMA200 冰线 LPSY 诊断脚本
统计 $124k→$70k 阴跌期间，价格触碰 EMA200 后失败的次数与质量
"""
import sys
sys.path.insert(0, '/freqtrade')

import pandas as pd
import numpy as np

# ── 读取数据 ──────────────────────────────────────────────────────────────────
df1h = pd.read_feather('user_data/data/binance/futures/BTC_USDT_USDT-1h-futures.feather')
df1d = pd.read_feather('user_data/data/binance/futures/BTC_USDT_USDT-1d-futures.feather')
df1h['date'] = pd.to_datetime(df1h['date'], utc=True)
df1d['date'] = pd.to_datetime(df1d['date'], utc=True)

# 日线 EMA200
df1d = df1d.sort_values('date').reset_index(drop=True)
df1d['ema200'] = df1d['close'].ewm(span=200, adjust=False).mean()
# shift(1)：策略当天只能看到昨日 EMA200
df1d['ema200_lag'] = df1d['ema200'].shift(1)

# 计算 ema200_persist（日线7日持续低于EMA200）
df1d['below_ema200'] = df1d['close'] < df1d['ema200']
df1d['persist_raw'] = df1d['below_ema200'].rolling(7, min_periods=7).min().astype(bool)
df1d['persist_lag'] = df1d['persist_raw'].shift(1)

# merge 日线 EMA200 + persist 到 1H
df1h = df1h.sort_values('date').reset_index(drop=True)
df1h = pd.merge_asof(df1h,
                     df1d[['date', 'ema200_lag', 'persist_lag']].rename(
                         columns={'ema200_lag': 'ema200', 'persist_lag': 'ema200_persist'}),
                     on='date', direction='backward')
df1h['ema200_persist'] = df1h['ema200_persist'].fillna(False).infer_objects(copy=False).astype(bool)

# ── 定义研究窗口：ATH → 当前 ────────────────────────────────────────────────
study = df1h[df1h['date'] >= '2025-01-01'].copy().reset_index(drop=True)

# ── 找 ATH ────────────────────────────────────────────────────────────────────
ath_idx = study['high'].idxmax()
ath_date = study.loc[ath_idx, 'date']
ath_price = study.loc[ath_idx, 'high']
print('=' * 70)
print(f'ATH: {str(ath_date)[:10]}  价格={ath_price:,.0f}')
print(f'研究窗口: {str(study["date"].min())[:10]} → {str(study["date"].max())[:10]}')
print('=' * 70)

# ── 核心算法：检测"触碰冰线后失败"事件 ─────────────────────────────────────
# 条件：
#   A. ema200_persist = True
#   B. 当前 1H bar 的 high >= ema200 * 0.98（触碰警戒区）
#   C. 当前 close < ema200（未能站上冰线）
#   D. 当前 close < open（阴线，失败确认）
# 额外过滤：
#   E. 前 48H 内没有触发过同一事件（去重，避免同一反弹被多次计数）

TOUCH_RATIO = 0.98   # 触碰冰线的警戒区下限
COOLDOWN_H  = 48     # 冷却期（小时）

study['touch_ice'] = (
    study['ema200_persist'] &
    (study['high'] >= study['ema200'] * TOUCH_RATIO) &
    (study['close'] < study['ema200']) &
    (study['close'] < study['open'])
)

# 去重：冷却期内只保留第一次触发
events = []
last_event_idx = -COOLDOWN_H * 2
for i, row in study.iterrows():
    if row['touch_ice'] and (i - last_event_idx) >= COOLDOWN_H:
        events.append(i)
        last_event_idx = i

print(f'\n【触碰冰线事件总数】（ema200_persist=True，触碰后失败，{COOLDOWN_H}H冷却）: {len(events)} 次')
print()

# ── 逐事件分析 ────────────────────────────────────────────────────────────────
print(f'{"日期":12s} {"价格":>9} {"EMA200":>9} {"触碰比":>7} '
      f'{"3D后":>8} {"7D后":>8} {"14D后":>8} {"备注":>15}')
print('-' * 80)

results = []
for idx in events:
    row = study.loc[idx]
    date_str = str(row['date'])[:10]
    price = row['close']
    ema200 = row['ema200']
    touch_ratio = row['high'] / ema200

    # 后续收益
    def fwd_chg(bars):
        future_idx = idx + bars
        if future_idx >= len(study):
            return np.nan
        return (study.loc[future_idx, 'close'] / price - 1) * 100

    chg3d  = fwd_chg(3 * 24)
    chg7d  = fwd_chg(7 * 24)
    chg14d = fwd_chg(14 * 24)

    # 特殊日期标注
    note = ''
    if '2026-04-13' in date_str:
        note = '← TASK19陷阱'
    elif touch_ratio >= 1.0:
        note = '← 站上冰线'

    results.append({
        'date': date_str, 'price': price, 'ema200': ema200,
        'touch_ratio': touch_ratio, 'chg3d': chg3d, 'chg7d': chg7d,
        'chg14d': chg14d, 'note': note
    })

    chg3d_s  = f'{chg3d:+.1f}%'  if not np.isnan(chg3d)  else 'N/A'
    chg7d_s  = f'{chg7d:+.1f}%'  if not np.isnan(chg7d)  else 'N/A'
    chg14d_s = f'{chg14d:+.1f}%' if not np.isnan(chg14d) else 'N/A'

    print(f'{date_str:12s} {price:>9,.0f} {ema200:>9,.0f} {touch_ratio:>7.3f} '
          f'{chg3d_s:>8} {chg7d_s:>8} {chg14d_s:>8} {note:>15}')

# ── 统计汇总 ──────────────────────────────────────────────────────────────────
print()
print('=' * 70)
df_res = pd.DataFrame(results).dropna(subset=['chg7d'])

if len(df_res) > 0:
    # 做空方向：价格下跌 = 正收益（short profit = -price_change）
    df_res['short_profit_7d'] = -df_res['chg7d']
    win7 = (df_res['short_profit_7d'] > 0).sum()
    avg7 = df_res['short_profit_7d'].mean()

    print(f'有效事件（含7日后续数据）: {len(df_res)} 次')
    print(f'7日做空胜率: {win7}/{len(df_res)} = {win7/len(df_res)*100:.0f}%')
    print(f'7日平均空头收益: {avg7:+.2f}%')
    print()

    # ATH之后、ema200_persist激活之前的"空窗期"
    persist_start = study[study['ema200_persist']]['date'].min()
    print(f'ema200_persist 首次激活: {str(persist_start)[:10]}')
    print(f'ATH → ema200_persist激活 空窗期: {(persist_start - ath_date).days} 天')
    print()

    # 2026-04-13 过滤验证
    task19_events = [r for r in results if '2026-04-13' in r['date']]
    if task19_events:
        print(f'⚠️  2026-04-13（TASK19陷阱）被捕获: {len(task19_events)} 次')
        for e in task19_events:
            print(f'   价格={e["price"]:,.0f}  EMA200={e["ema200"]:,.0f}  触碰比={e["touch_ratio"]:.3f}')
    else:
        print(f'✅ 2026-04-13（TASK19陷阱）被自动过滤，未出现在事件列表中')

print('=' * 70)
