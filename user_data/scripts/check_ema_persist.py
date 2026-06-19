"""查 daily_ema200_persist 当前状态"""
import sys
sys.path.insert(0, '/freqtrade')
import ccxt
import pandas as pd
import numpy as np

# 拉最新日线（历史 + 实时接续）
hist = pd.read_feather('/freqtrade/user_data/data/binance/futures/BTC_USDT_USDT-1d-futures.feather')
hist['date'] = pd.to_datetime(hist['date']).dt.tz_localize(None)

ex = ccxt.binance({'options': {'defaultType': 'future'}})
ohlcv = ex.fetch_ohlcv('BTC/USDT', '1d', limit=10)
df_new = pd.DataFrame(ohlcv, columns=['ts','open','high','low','close','volume'])
df_new['date'] = pd.to_datetime(df_new['ts'], unit='ms', utc=True).dt.tz_localize(None)

df = pd.concat([hist[['date','close']], df_new[['date','close']]], ignore_index=True)
df = df.drop_duplicates('date').sort_values('date').reset_index(drop=True)

# EMA200
df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()

# ema200_persist 逻辑
raw = (df['close'] < df['ema200']).astype(int)
activate = raw.rolling(1, min_periods=1).min().fillna(0).astype(bool)
reset = df['close'].gt(df['ema200']).astype(int).rolling(5, min_periods=5).min().fillna(0).astype(bool).values
act = activate.values
arr = np.zeros(len(df), dtype=bool)
on = False
for i in range(len(df)):
    if act[i]: on = True
    if reset[i]: on = False
    arr[i] = on
df['ema_persist'] = arr

# 统计
recent = df.tail(30).copy()
print('=== 近 30 日 daily_ema200_persist 状态 ===')
print(f'{"日期":<12} {"close":>9} {"EMA200":>9} {"<EMA?":>6} {"persist":>8} {"重置?":>6}')
print('-'*55)
for i, r in recent.iterrows():
    lt = '✓' if r['close'] < r['ema200'] else ' '
    p = '✅ON' if r['ema_persist'] else '❌OFF'
    rst = '←RESET' if (i < len(df)-1 and reset[i]) else ''
    print(f'{str(r["date"].date()):<12} {r["close"]:>9,.0f} {r["ema200"]:>9,.0f} {lt:>6} {p:>8} {rst}')

last = df.iloc[-1]
print(f'\n当前 ema200_persist = {"✅ 激活（wsdown 可触发）" if arr[-1] else "❌ 关闭（wsdown 不触发）"}')
print(f'当前 close={last["close"]:,.0f}  EMA200={last["ema200"]:,.0f}')
