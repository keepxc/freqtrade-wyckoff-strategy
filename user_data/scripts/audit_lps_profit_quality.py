"""
深度分析：有LPS前导 vs 无LPS前导 的交易质量差异
指标：利润因子、持仓时长、盈亏分布、利润分位数
不动任何策略代码，纯数据审计
"""
import sys, json, zipfile
sys.path.insert(0, '/freqtrade')
import pandas as pd, numpy as np
from pathlib import Path

ZIP = Path('/freqtrade/user_data/backtest_results/backtest-result-2026-06-12_04-04-59.zip')
with zipfile.ZipFile(ZIP) as z:
    fname = [f for f in z.namelist() if f.endswith('.json') and 'meta' not in f][0]
    bt = json.loads(z.read(fname))
strat = list(bt['strategy'].keys())[0]
trades = pd.DataFrame(bt['strategy'][strat]['trades'])
trades['open_date']  = pd.to_datetime(trades['open_date']).dt.tz_localize(None)
trades['close_date'] = pd.to_datetime(trades['close_date']).dt.tz_localize(None)
trades['profit_ratio'] = pd.to_numeric(trades.get('profit_ratio', trades.get('profit_factor', 0)), errors='coerce').fillna(0)
trades['profit_pct'] = trades['profit_ratio'] * 100
trades['duration_h'] = (trades['close_date'] - trades['open_date']).dt.total_seconds() / 3600
trades['is_win'] = trades['profit_ratio'] > 0

long_trades = trades[trades['is_short'] == False].copy()

# ── 重建 LPS 信号（方向A：SOS低点×0.99）────────────────────────────────────
df = pd.read_feather('/freqtrade/user_data/data/binance/futures/BTC_USDT_USDT-1h-futures.feather')
df['date'] = pd.to_datetime(df['date']).dt.tz_localize(None)
df = df.sort_values('date').reset_index(drop=True)
high, low, close, open_ = df['high'], df['low'], df['close'], df['open']
tr = pd.concat([high-low,(high-close.shift(1)).abs(),(low-close.shift(1)).abs()],axis=1).max(axis=1)
df['atr14']    = tr.ewm(span=14, adjust=False).mean()
df['vol_ma10'] = df['volume'].rolling(10, min_periods=3).mean()
df['spread']   = df['high'] - df['low']
df['move_pct'] = (df['close'] - df['open']) / df['open'] * 100
df['is_sos']   = ((df['close']>df['open']) & (df['spread']>df['atr14']*0.6) &
                  (df['volume']>df['vol_ma10']*1.2) & (df['move_pct']>0.5))

lps_events = []
for i in range(len(df)):
    if not df.iloc[i]['is_sos']: continue
    sos_low = df.iloc[i]['low']; sos_spread = df.iloc[i]['spread']
    anchor  = sos_low * 0.99
    for j in range(i+1, min(i+7, len(df))):
        r = df.iloc[j]
        if (r['close']<r['open'] and r['volume']<r['vol_ma10']*0.7 and
            r['spread']<sos_spread*0.7 and r['close']>=anchor):
            lps_events.append({'date': r['date']})
            break
df_lps = pd.DataFrame(lps_events).drop_duplicates('date')

# 标记每笔多头是否有 LPS 前导
has_lps_list = []
lps_lead_h   = []
for _, trade in long_trades.iterrows():
    entry_dt = trade['open_date']
    hits = df_lps[(df_lps['date'] >= entry_dt-pd.Timedelta(hours=24)) &
                  (df_lps['date'] <  entry_dt)]
    if len(hits) > 0:
        has_lps_list.append(True)
        lps_lead_h.append((entry_dt - hits.iloc[-1]['date']).total_seconds()/3600)
    else:
        has_lps_list.append(False)
        lps_lead_h.append(None)

long_trades = long_trades.copy()
long_trades['has_lps']  = has_lps_list
long_trades['lps_lead'] = lps_lead_h

A = long_trades[long_trades['has_lps']]   # 有LPS
B = long_trades[~long_trades['has_lps']]  # 无LPS

def pf(df):
    """利润因子 = 总盈利 / 总亏损绝对值"""
    wins = df[df['profit_pct']>0]['profit_pct'].sum()
    loss = df[df['profit_pct']<0]['profit_pct'].abs().sum()
    return wins / loss if loss > 0 else float('inf')

def summary(label, df):
    wr   = df['is_win'].mean()*100
    avg_w = df[df['is_win']]['profit_pct'].mean()
    avg_l = df[~df['is_win']]['profit_pct'].mean()
    med_dur = df['duration_h'].median()
    pf_val  = pf(df)
    p25, p50, p75 = df['profit_pct'].quantile([.25,.5,.75]).values
    return dict(label=label, n=len(df), wr=wr, avg_win=avg_w, avg_loss=avg_l,
                pf=pf_val, med_dur=med_dur, p25=p25, p50=p50, p75=p75)

sa = summary('有LPS前导', A)
sb = summary('无LPS前导', B)

print('='*65)
print('=== LPS 置信度标签 — 交易质量深度对比 ===\n')
print(f'{"指标":<20} {"有LPS (231)":>14} {"无LPS (238)":>14} {"差值":>10}')
print('-'*60)
rows = [
    ('笔数',          f'{sa["n"]}',           f'{sb["n"]}',           ''),
    ('胜率',          f'{sa["wr"]:.1f}%',      f'{sb["wr"]:.1f}%',     f'{sa["wr"]-sb["wr"]:+.1f}pp'),
    ('平均盈利',      f'+{sa["avg_win"]:.2f}%',f'+{sb["avg_win"]:.2f}%',f'{sa["avg_win"]-sb["avg_win"]:+.2f}pp'),
    ('平均亏损',      f'{sa["avg_loss"]:.2f}%',f'{sb["avg_loss"]:.2f}%',f'{sa["avg_loss"]-sb["avg_loss"]:+.2f}pp'),
    ('利润因子',      f'{sa["pf"]:.2f}',       f'{sb["pf"]:.2f}',      f'{sa["pf"]-sb["pf"]:+.2f}'),
    ('持仓时长中位',  f'{sa["med_dur"]:.1f}h', f'{sb["med_dur"]:.1f}h',f'{sa["med_dur"]-sb["med_dur"]:+.1f}h'),
    ('利润P25',       f'{sa["p25"]:.2f}%',     f'{sb["p25"]:.2f}%',    f'{sa["p25"]-sb["p25"]:+.2f}pp'),
    ('利润P50',       f'{sa["p50"]:.2f}%',     f'{sb["p50"]:.2f}%',    f'{sa["p50"]-sb["p50"]:+.2f}pp'),
    ('利润P75',       f'{sa["p75"]:.2f}%',     f'{sb["p75"]:.2f}%',    f'{sa["p75"]-sb["p75"]:+.2f}pp'),
]
for r in rows:
    print(f'{r[0]:<20} {r[1]:>14} {r[2]:>14} {r[3]:>10}')

# LPS 提前量 vs 利润相关性
print(f'\n=== LPS 提前量分组分析 ===')
A2 = A.copy()
A2['lead_group'] = pd.cut(A2['lps_lead'], bins=[0,6,12,18,24], labels=['0-6h','6-12h','12-18h','18-24h'])
grp = A2.groupby('lead_group', observed=True).agg(
    笔数=('profit_pct','count'),
    胜率=('is_win', lambda x: f'{x.mean()*100:.1f}%'),
    平均利润=('profit_pct', lambda x: f'{x.mean():+.2f}%'),
    中位利润=('profit_pct', lambda x: f'{x.median():+.2f}%'),
    持仓时长=('duration_h', lambda x: f'{x.median():.1f}h'),
)
print(grp.to_string())

# 亏损笔分析
print(f'\n=== 亏损笔对比 ===')
A_loss = A[~A['is_win']]; B_loss = B[~B['is_win']]
print(f'有LPS亏损笔: {len(A_loss)}  最大单笔亏损: {A_loss["profit_pct"].min():.2f}%  均值: {A_loss["profit_pct"].mean():.2f}%')
print(f'无LPS亏损笔: {len(B_loss)}  最大单笔亏损: {B_loss["profit_pct"].min():.2f}%  均值: {B_loss["profit_pct"].mean():.2f}%')

print(f'\n=== 标注层立项判断 ===')
pf_delta = sa['pf'] - sb['pf']
dur_delta = sa['med_dur'] - sb['med_dur']
p50_delta = sa['p50'] - sb['p50']
print(f'利润因子差: {pf_delta:+.2f}  {"✅ 有提升" if pf_delta>0 else "❌ 无提升"}')
print(f'持仓时长差: {dur_delta:+.1f}h  {"✅ 更短（效率更高）" if dur_delta<0 else "❌ 更长"}')
print(f'P50利润差:  {p50_delta:+.2f}pp  {"✅ 中位利润更高" if p50_delta>0 else "❌ 中位利润更低"}')
if pf_delta > 0 and p50_delta > 0:
    print('\n→ ✅ LPS 标注与更高利润质量正相关，值得注入标注层做持续追踪')
elif pf_delta > 0 or p50_delta > 0:
    print('\n→ ⚠️ 部分指标改善，但不显著，标注层可选')
else:
    print('\n→ ❌ 无正相关，LPS 标注无实用价值')
