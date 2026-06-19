"""
方向A验证：放宽 LPS 条件（SOS低点×0.99 替代 SOS收盘价）
同时检查假信号率：LPS 出现后 24H 内是否有多头入场 + 入场胜率
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
long_trades = trades[trades['is_short'] == False].copy()

df = pd.read_feather('/freqtrade/user_data/data/binance/futures/BTC_USDT_USDT-1h-futures.feather')
df['date'] = pd.to_datetime(df['date']).dt.tz_localize(None)
df = df.sort_values('date').reset_index(drop=True)

high, low, close, open_ = df['high'], df['low'], df['close'], df['open']
tr = pd.concat([high-low,(high-close.shift(1)).abs(),(low-close.shift(1)).abs()],axis=1).max(axis=1)
df['atr14']    = tr.ewm(span=14, adjust=False).mean()
df['vol_ma10'] = df['volume'].rolling(10, min_periods=3).mean()
df['spread']   = df['high'] - df['low']
df['move_pct'] = (df['close'] - df['open']) / df['open'] * 100

df['is_sos'] = (
    (df['close'] > df['open']) &
    (df['spread'] > df['atr14'] * 0.6) &
    (df['volume'] > df['vol_ma10'] * 1.2) &
    (df['move_pct'] > 0.5)
)

# ── 标记全历史 LPS（方向A：不破 SOS低点×0.99）────────────────────────────
lps_events = []  # (date, lead_sos_low)
for i in range(len(df)):
    if not df.iloc[i]['is_sos']:
        continue
    sos_low    = df.iloc[i]['low']
    sos_spread = df.iloc[i]['spread']
    anchor     = sos_low * 0.99          # ← 方向A 放宽门槛
    for j in range(i+1, min(i+7, len(df))):
        r = df.iloc[j]
        if (r['close'] < r['open'] and
            r['volume'] < r['vol_ma10'] * 0.7 and
            r['spread'] < sos_spread * 0.7 and
            r['close'] >= anchor):
            lps_events.append({'date': r['date'], 'anchor': anchor})
            break   # 每个SOS只取第一个LPS

df_lps = pd.DataFrame(lps_events).drop_duplicates('date')
print(f'全历史 LPS 信号（方向A）: {len(df_lps)} 个')

# ── 第一层：覆盖率（多头入场前24H内有无LPS）────────────────────────────────
results_a = []
for _, trade in long_trades.iterrows():
    entry_dt = trade['open_date']
    w_start  = entry_dt - pd.Timedelta(hours=24)
    hits = df_lps[(df_lps['date'] >= w_start) & (df_lps['date'] < entry_dt)]
    if len(hits) > 0:
        lead = (entry_dt - hits.iloc[-1]['date']).total_seconds() / 3600
        results_a.append({'hit': True, 'lead_h': lead,
                          'profit_pct': trade['profit_factor'] if 'profit_factor' in trade else trade.get('profit_ratio',0)*100})
    else:
        results_a.append({'hit': False, 'lead_h': None, 'profit_pct': None})

df_ra = pd.DataFrame(results_a)
n = len(df_ra); n_hit = df_ra['hit'].sum()
leads = df_ra[df_ra['lead_h'].notna()]['lead_h'].values

print(f'\n=== 方向A：覆盖率对比 ===')
print(f'原始（SOS收盘锚）: 132/469 = 28.1%')
print(f'方向A（SOS低点×0.99）: {n_hit}/{n} = {n_hit/n*100:.1f}%  (+{n_hit/n*100-28.1:.1f}pp)')
if len(leads):
    print(f'提前量 中位:{np.median(leads):.1f}h  均值:{np.mean(leads):.1f}h  >12h:{(leads>12).mean()*100:.1f}%')

# ── 第二层：假信号率（LPS后24H内是否有多头入场）────────────────────────────
print(f'\n=== 假信号率：LPS信号后24H内是否触发多头入场 ===')
entry_dates = set(long_trades['open_date'].dt.floor('H').tolist())

followed = 0; not_followed = 0
false_pos_profits = []

for _, lps in df_lps.iterrows():
    lps_dt  = lps['date']
    w_end   = lps_dt + pd.Timedelta(hours=24)
    after   = long_trades[(long_trades['open_date'] > lps_dt) &
                           (long_trades['open_date'] <= w_end)]
    if len(after) > 0:
        followed += 1
    else:
        not_followed += 1

total_lps = len(df_lps)
fp_rate = not_followed / total_lps * 100
print(f'LPS总信号: {total_lps}')
print(f'  → 24H内有入场（真信号）: {followed} ({followed/total_lps*100:.1f}%)')
print(f'  → 24H内无入场（假信号）: {not_followed} ({fp_rate:.1f}%)')

# ── 第三层：命中笔的胜率分析 ────────────────────────────────────────────────
print(f'\n=== 有LPS前导 vs 无LPS前导 入场胜率对比 ===')
long_trades2 = long_trades.copy()
long_trades2['profit_ratio'] = pd.to_numeric(long_trades2.get('profit_ratio', long_trades2.get('profit_factor', 0)), errors='coerce').fillna(0)
long_trades2['is_win'] = long_trades2['profit_ratio'] > 0

hit_indices = []
for idx, trade in long_trades2.iterrows():
    entry_dt = trade['open_date']
    w_start  = entry_dt - pd.Timedelta(hours=24)
    hits = df_lps[(df_lps['date'] >= w_start) & (df_lps['date'] < entry_dt)]
    hit_indices.append(len(hits) > 0)

long_trades2['has_lps'] = hit_indices
with_lps    = long_trades2[long_trades2['has_lps']]
without_lps = long_trades2[~long_trades2['has_lps']]

wr_with    = with_lps['is_win'].mean() * 100
wr_without = without_lps['is_win'].mean() * 100
print(f'有LPS前导 ({len(with_lps)}笔): 胜率 {wr_with:.1f}%')
print(f'无LPS前导 ({len(without_lps)}笔): 胜率 {wr_without:.1f}%')
print(f'胜率差: {wr_with - wr_without:+.1f}pp')

print(f'\n=== 综合结论 ===')
cov = n_hit/n*100
print(f'覆盖率: {cov:.1f}%  (目标≥35%) → {"✅" if cov>=35 else "⚠️"}')
print(f'假信号率: {fp_rate:.1f}%  (目标<70%) → {"✅" if fp_rate<70 else "⚠️"}')
print(f'胜率影响: {wr_with - wr_without:+.1f}pp  (目标≥0) → {"✅" if wr_with>=wr_without else "⚠️"}')
if cov>=35 and fp_rate<70 and wr_with>=wr_without:
    print('→ ✅ 方向A 通过三项校验，可以立项')
else:
    print('→ ⚠️ 部分指标未达标，见具体数字决策')
