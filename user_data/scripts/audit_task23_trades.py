"""
TASK 23 交易审计：重放 FTI 逻辑，还原两笔 v8_short_fti 开仓的确切时间与结构
"""
import sys
sys.path.insert(0, '/freqtrade')
import pandas as pd
import numpy as np

# ── 数据加载 ──────────────────────────────────────────────────────────────────
df1d = pd.read_feather('user_data/data/binance/futures/BTC_USDT_USDT-1d-futures.feather')
df1d['date'] = pd.to_datetime(df1d['date'], utc=True)
df1d = df1d.sort_values('date').reset_index(drop=True)

df1h = pd.read_feather('user_data/data/binance/futures/BTC_USDT_USDT-1h-futures.feather')
df1h['date'] = pd.to_datetime(df1h['date'], utc=True)
df1h = df1h.sort_values('date').reset_index(drop=True)

# ── 日线指标 ──────────────────────────────────────────────────────────────────
df1d['ema200']    = df1d['close'].ewm(span=200, adjust=False).mean()
df1d['atr14']     = (df1d['high'] - df1d['low']).rolling(14, min_periods=7).mean()
df1d['vol_ma20']  = df1d['volume'].rolling(20, min_periods=10).mean()

# ── FTI 参数（与 WyckoffV8.py 一致）──────────────────────────────────────────
CONSOL_WINDOW    = 14
CONSOL_PCT_MAX   = 0.15
MSOW_SPREAD_MULT = 1.5
MSOW_VOL_MULT    = 1.5
PROPAGATE_DAYS   = 7

# ── 日线 FTI 门控计算 ─────────────────────────────────────────────────────────
fti_range14 = (df1d['high'].rolling(CONSOL_WINDOW, min_periods=8).max()
             - df1d['low'].rolling(CONSOL_WINDOW, min_periods=8).min())
fti_consol  = (fti_range14 / df1d['close'].replace(0, float('nan'))) <= CONSOL_PCT_MAX
fti_ice_raw = df1d['low'].rolling(CONSOL_WINDOW, min_periods=8).min().shift(1)

ema200_shifted = df1d['ema200'].shift(1).fillna(0)
consol_shifted = fti_consol.shift(1).infer_objects(copy=False).fillna(False)

fti_msow_raw = (
    consol_shifted &
    (df1d['close'] < ema200_shifted) &
    (df1d['close'] < fti_ice_raw) &
    ((df1d['high'] - df1d['low']) >= df1d['atr14'] * MSOW_SPREAD_MULT) &
    (df1d['volume'] >= df1d['vol_ma20'] * MSOW_VOL_MULT)
).fillna(False)

# 7-day propagation
active_arr = np.zeros(len(df1d), dtype=bool)
ice_arr    = np.full(len(df1d), np.nan)
active_cnt = 0
ice_locked = np.nan

for i in range(len(df1d)):
    if fti_msow_raw.iloc[i]:
        active_cnt = PROPAGATE_DAYS
        ice_locked = fti_ice_raw.iloc[i]
    if active_cnt > 0 and not np.isnan(ice_locked):
        active_arr[i] = True
        ice_arr[i]    = ice_locked
        active_cnt   -= 1
    else:
        active_arr[i] = False

df1d['fti_active'] = active_arr
df1d['fti_ice']    = ice_arr

# 找出所有 MSOW 触发日
msow_days = df1d[fti_msow_raw].copy()
print('=' * 90)
print('  FTI MSOW 日线触发事件（2022-01-01 后）')
print('=' * 90)
msow_days_study = msow_days[msow_days['date'] >= '2022-01-01']
print(f'{"日期":12} {"收盘价":>9} {"EMA200":>9} {"冰线":>9} {"spread/ATR":>11} {"vol/MA":>8}')
print('-' * 70)
for _, r in msow_days_study.iterrows():
    ice = fti_ice_raw.loc[r.name]
    spread_ratio = (r['high'] - r['low']) / r['atr14'] if r['atr14'] > 0 else 0
    vol_ratio    = r['volume'] / r['vol_ma20'] if r['vol_ma20'] > 0 else 0
    print(f'{str(r["date"])[:10]:12} {r["close"]:>9,.0f} {r["ema200"]:>9,.0f} '
          f'{ice:>9,.0f} {spread_ratio:>10.2f}x {vol_ratio:>7.2f}x')

# ── 将日线信号 forward-fill 到 1H ────────────────────────────────────────────
# merge by date（日线 date 表示当天 00:00 UTC，1H 数据每小时一根）
df1d_merge = df1d[['date', 'fti_active', 'fti_ice']].copy()
df1d_merge['date_day'] = df1d_merge['date'].dt.normalize()

df1h['date_day'] = df1h['date'].dt.normalize()
df1h = df1h.merge(df1d_merge[['date_day', 'fti_active', 'fti_ice']],
                  on='date_day', how='left')
df1h['fti_active'] = df1h['fti_active'].fillna(False)
df1h['fti_ice']    = df1h['fti_ice'].fillna(0)

# ── 1H 执行确认 ───────────────────────────────────────────────────────────────
FTI_CLOSE_LOC = 0.40
FTI_VOL_MULT  = 1.0   # volume <= vol_ma (no demand)

df1h['vol_ma20_1h'] = df1h['volume'].rolling(20, min_periods=5).mean()
bar_range = (df1h['high'] - df1h['low']).replace(0, np.nan)
df1h['close_loc'] = (df1h['close'] - df1h['low']) / bar_range

df1h['fti_1h_confirm'] = (
    (df1h['fti_ice'] > 0) &
    (df1h['high'] >= df1h['fti_ice'] * 0.985) &   # high touches ice
    (df1h['close'] < df1h['fti_ice']) &              # closes below ice
    (df1h['close_loc'] <= FTI_CLOSE_LOC) &           # closed in low zone
    (df1h['volume'] <= df1h['vol_ma20_1h'] * FTI_VOL_MULT)  # low volume
)

fti_entries = df1h[df1h['fti_active'] & df1h['fti_1h_confirm']].copy()

print('\n')
print('=' * 90)
print('  FTI 1H 入场信号（全量）')
print('=' * 90)
print(f'{"时间":20} {"入场价(close)":>13} {"冰线":>9} {"H/ice%":>8} '
      f'{"收盘位置":>9} {"vol/MA":>8}')
print('-' * 80)

for _, r in fti_entries[fti_entries['date'] >= '2022-01-01'].iterrows():
    h_vs_ice = (r['high'] / r['fti_ice'] - 1) * 100
    vol_ratio = r['volume'] / r['vol_ma20_1h'] if r['vol_ma20_1h'] > 0 else 0
    print(f'{str(r["date"])[:19]:20} {r["close"]:>13,.0f} {r["fti_ice"]:>9,.0f} '
          f'{h_vs_ice:>7.2f}% {r["close_loc"]:>8.2f}x {vol_ratio:>8.2f}x')

print(f'\n总计 FTI 入场信号：{len(fti_entries[fti_entries["date"] >= "2022-01-01"])} 笔')

# ── 每笔详细上下文 ────────────────────────────────────────────────────────────
print('\n')
print('=' * 90)
print('  FTI 入场逐笔上下文（±3日背景）')
print('=' * 90)

entries_study = fti_entries[fti_entries['date'] >= '2022-01-01'].reset_index()

for idx, row in entries_study.iterrows():
    ts = row['date']
    ice = row['fti_ice']
    close_price = row['close']

    print(f'\n【第{idx+1}笔】{str(ts)[:19]}  入场价={close_price:,.0f}  冰线={ice:,.0f}  '
          f'收盘位置={row["close_loc"]:.2f}')

    # 找对应 MSOW 日
    msow_before = msow_days_study[msow_days_study['date'] < ts]
    if not msow_before.empty:
        msow_row = msow_before.iloc[-1]
        days_gap = (ts - msow_row['date']).days
        print(f'  MSOW 触发：{str(msow_row["date"])[:10]}（入场前 {days_gap} 天）'
              f'  收盘={msow_row["close"]:,.0f}  EMA200={msow_row["ema200"]:,.0f}')

    # 后续 7 日 1H 价格走势
    future_1h = df1h[(df1h['date'] > ts) & (df1h['date'] <= ts + pd.Timedelta(days=7))].head(168)
    if not future_1h.empty:
        fut_low  = future_1h['low'].min()
        fut_high = future_1h['high'].max()
        fut_last = future_1h.iloc[-1]['close']
        ret_pct  = (close_price / fut_last - 1) * 100
        print(f'  后7日：最低={fut_low:,.0f}  最高={fut_high:,.0f}  '
              f'末价={fut_last:,.0f}  空头收益≈{ret_pct:+.1f}%')

    # 冰线对应的 14 日横盘区间
    day_of_entry = ts.normalize()
    d1_row = df1d[df1d['date'].dt.normalize() == day_of_entry]
    if not d1_row.empty:
        consol_range = fti_range14.loc[d1_row.index[0]]
        consol_pct   = consol_range / d1_row.iloc[0]['close'] * 100
        print(f'  横盘区间幅度：{consol_range:,.0f} USDT（{consol_pct:.1f}%）  冰线={ice:,.0f}')

    # Lookahead 检查：冰线是否用了未来数据
    # fti_ice_raw = min(14d low).shift(1)，即用昨日收盘前14日低点作冰线
    # 验证：冰线值是否等于入场时间前14个日线bar的最低值
    d1_before = df1d[df1d['date'].dt.normalize() < day_of_entry].tail(15)
    if len(d1_before) >= 14:
        actual_14d_low = d1_before.tail(14)['low'].min()
        lookahead_ok = abs(actual_14d_low - ice) < 1  # 1 USDT 误差
        print(f'  Lookahead 检查：14日前低={actual_14d_low:,.0f}  冰线={ice:,.0f}  '
              f'{"✅ 无未来函数" if lookahead_ok else "❌ 冰线与历史低点不匹配！检查shift"}')
