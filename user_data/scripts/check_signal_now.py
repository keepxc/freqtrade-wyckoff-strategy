"""查最新 1H 数据判断 wsdown 入场条件"""
import sys
sys.path.insert(0, '/freqtrade')

import ccxt
import pandas as pd

# 从 Binance 拉最新 72 根 1H
ex = ccxt.binance({'options': {'defaultType': 'future'}})
ohlcv = ex.fetch_ohlcv('BTC/USDT', '1h', limit=72)
df_new = pd.DataFrame(ohlcv, columns=['ts','open','high','low','close','volume'])
df_new['date'] = pd.to_datetime(df_new['ts'], unit='ms', utc=True).dt.tz_localize(None)

# 历史数据接 EMA200
hist = pd.read_feather('/freqtrade/user_data/data/binance/futures/BTC_USDT_USDT-1h-futures.feather')
hist['date'] = pd.to_datetime(hist['date']).dt.tz_localize(None)
combined = pd.concat([hist[['date','close']], df_new[['date','close']]], ignore_index=True)
combined = combined.drop_duplicates('date').sort_values('date').reset_index(drop=True)
combined['ema200'] = combined['close'].ewm(span=200, adjust=False).mean()

ema_map = combined.set_index('date')['ema200'].to_dict()
df_new['ema200'] = df_new['date'].map(ema_map)
df_new['below_ema'] = df_new['close'] < df_new['ema200']
df_new['chg'] = df_new['close'].pct_change() * 100

print('=== 最近 48 根 1H K线（实时数据）===')
print(f'{"时间(UTC)":<22} {"close":>9} {"EMA200":>9} {"位置":>6} {"涨跌%":>7}')
print('-'*60)
for _, r in df_new.tail(48).iterrows():
    pos = '↓下' if r['below_ema'] else '↑上'
    print(f'{str(r["date"]):<22} {r["close"]:>9,.1f} {r["ema200"]:>9,.1f} {pos:>6} {r["chg"]:>+6.2f}%')

last = df_new.iloc[-1]
recent = df_new.tail(24)
high24 = recent['high'].max()
low24 = recent['low'].min()

print(f'\n=== 当前状态 ===')
print(f'时间    : {last["date"]} UTC')
print(f'close   : {last["close"]:,.1f}')
print(f'EMA200  : {last["ema200"]:,.1f}')
print(f'位置    : EMA200 {"下方 ✅" if last["below_ema"] else "上方 ❌"}')
print(f'距离    : {(last["close"]-last["ema200"])/last["ema200"]*100:+.2f}%')
print(f'近24h高 : {high24:,.1f}')
print(f'近24h低 : {low24:,.1f}')
print(f'距高点  : {(last["close"]-high24)/high24*100:.2f}%')

# 简单判断下行波（连续下跌 > 0.8%）
df_new['cumdown'] = 0.0
for i in range(1, len(df_new)):
    prev_high = df_new.iloc[max(0,i-6):i]['high'].max()
    chg = (df_new.iloc[i]['close'] - prev_high) / prev_high * 100
    df_new.at[df_new.index[i], 'cumdown'] = chg

last_down = df_new.tail(6)['cumdown'].min()
print(f'\n近6根K最大跌幅: {last_down:.2f}%  (wsdown 需下行波位移 > 0.8%)')
print(f'wsdown 触发可能性: {"⚠️ 可能有信号" if last["below_ema"] and last_down < -0.8 else "❌ 条件未满足"}')
