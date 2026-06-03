"""
ATH→现在 下跌期间 空头执行审计
"""
import sys, json, zipfile, glob
sys.path.insert(0, '/freqtrade')
import pandas as pd
import numpy as np
from wyckoff.daily_event_detector import DailyEventDetector

zips = sorted(glob.glob('user_data/backtest_results/*.zip'))
with zipfile.ZipFile(zips[-1]) as z:
    fname = [n for n in z.namelist() if n.endswith('.json') and 'config' not in n][0]
    with z.open(fname) as f:
        data = json.load(f)
sk = list(data['strategy'].keys())[0]
trades = pd.DataFrame(data['strategy'][sk]['trades'])
trades['open_date']  = pd.to_datetime(trades['open_date'], utc=True)
trades['close_date'] = pd.to_datetime(trades['close_date'], utc=True)
shorts = trades[trades['is_short']].sort_values('open_date')
longs  = trades[~trades['is_short']].sort_values('open_date')

df1d = pd.read_feather('user_data/data/binance/futures/BTC_USDT_USDT-1d-futures.feather')
df1d['date'] = pd.to_datetime(df1d['date'], utc=True)
df1d = df1d[df1d['date'] >= '2025-01-01'].sort_values('date').reset_index(drop=True)
det = DailyEventDetector()
df1d = det.detect(df1d.copy())
df1d['ema200'] = df1d['close'].ewm(span=200, adjust=False).mean()

ath_idx  = df1d['close'].idxmax()
ath_row  = df1d.loc[ath_idx]
ath_date = ath_row['date']
ath_px   = ath_row['close']
cur_px   = df1d['close'].iloc[-1]
cur_date = df1d['date'].iloc[-1]

print("=" * 65)
print("BTC ATH → 现在  空头执行全审计")
print("=" * 65)
print(f"ATH: {str(ath_date.date())}  价格={ath_px:,.0f}")
print(f"现在: {str(cur_date.date())}  价格={cur_px:,.0f}  跌幅={((cur_px/ath_px)-1)*100:.1f}%")
print(f"共 {(cur_date - ath_date).days} 天")

df_dec = df1d[df1d['date'] >= ath_date].copy().reset_index(drop=True)

# 关键节点
below_ema200 = df_dec[df_dec['close'] < df_dec['ema200']]
first_below  = below_ema200.iloc[0] if len(below_ema200) else None

first_persist = None
for i in range(7, len(df_dec)):
    if (df_dec['close'].iloc[i-7:i] < df_dec['ema200'].iloc[i-7:i]).all():
        first_persist = df_dec.iloc[i]
        break

be_rows  = df_dec[df_dec['buying_exhaustion']]
sow_rows = df_dec[df_dec['daily_dist_sow_detected']]

print()
print("─── 熊市门控激活时间线 ───")
print(f"  ATH                  : {str(ath_date.date())}  {ath_px:>10,.0f}")
if first_below is not None:
    d = (first_below['date'] - ath_date).days
    print(f"  首次破EMA200         : {str(first_below['date'].date())}  {first_below['close']:>10,.0f}  (ATH后{d:3d}天)")
else:
    print("  首次破EMA200         : 从未触发")
if first_persist is not None:
    d = (first_persist['date'] - ath_date).days
    print(f"  ema200_persist激活   : {str(first_persist['date'].date())}  {first_persist['close']:>10,.0f}  (ATH后{d:3d}天)")
else:
    print("  ema200_persist激活   : 从未触发")
if len(be_rows):
    d = (be_rows.iloc[0]['date'] - ath_date).days
    print(f"  BE首次触发           : {str(be_rows.iloc[0]['date'].date())}  {be_rows.iloc[0]['close']:>10,.0f}  (ATH后{d:3d}天)")
else:
    print("  BE首次触发           : 从未触发")
if len(sow_rows):
    d = (sow_rows.iloc[0]['date'] - ath_date).days
    print(f"  SOW首次触发          : {str(sow_rows.iloc[0]['date'].date())}  {sow_rows.iloc[0]['close']:>10,.0f}  (ATH后{d:3d}天)")
else:
    print("  SOW首次触发          : 从未触发")

print()
print("─── 各阶段跌幅与空头情况 ───")

# 阶段划分
stages = []
t0 = ath_date
labels_dates = []
if first_below is not None:
    labels_dates.append(("阶段1: ATH→破EMA200（熊市门关闭）", t0, first_below['date']))
    t1 = first_below['date']
else:
    t1 = t0
if first_persist is not None:
    labels_dates.append(("阶段2: 破EMA200→ema200_persist（门半开）", t1, first_persist['date']))
    t2 = first_persist['date']
else:
    t2 = t1
if len(be_rows):
    labels_dates.append(("阶段3: ema200_persist→BE触发（门开但无BE）", t2, be_rows.iloc[0]['date']))
    t3 = be_rows.iloc[0]['date']
else:
    t3 = t2
labels_dates.append(("阶段4: BE触发→现在（可开空）", t3, cur_date))

for label, s, e in labels_dates:
    seg = df_dec[(df_dec['date'] >= s) & (df_dec['date'] <= e)]
    if len(seg) < 2:
        continue
    p0 = seg['close'].iloc[0]
    p1 = seg['close'].iloc[-1]
    days = (e - s).days
    chg = (p1/p0 - 1)*100
    sh_in = shorts[(shorts['open_date'] >= s) & (shorts['open_date'] <= e)]
    lo_in = longs[(longs['open_date'] >= s) & (longs['open_date'] <= e)]
    print(f"\n  {label}")
    print(f"    {str(s.date())} → {str(e.date())}  ({days}天)")
    print(f"    价格: {p0:,.0f} → {p1:,.0f}  ({chg:+.1f}%)")
    print(f"    空头: {len(sh_in)}笔  |  多头: {len(lo_in)}笔")

print()
print("─── ATH后实际空头逐笔 ───")
sh_after = shorts[shorts['open_date'] >= ath_date]
if len(sh_after) == 0:
    print("  无空头交易")
else:
    print(f"  共 {len(sh_after)} 笔")
    for _, t in sh_after.iterrows():
        d = (t['open_date'] - ath_date).days
        print(f"  {str(t['open_date'])[:10]}  ATH后{d:3d}天  "
              f"收益={t['profit_ratio']*100:+.2f}%  "
              f"标签={t.get('enter_tag','')}")

print()
print("─── ATH后实际多头逐笔（参考） ───")
lo_after = longs[longs['open_date'] >= ath_date]
print(f"  共 {len(lo_after)} 笔")
for _, t in lo_after.iterrows():
    d = (t['open_date'] - ath_date).days
    print(f"  {str(t['open_date'])[:10]}  ATH后{d:3d}天  "
          f"收益={t['profit_ratio']*100:+.2f}%  "
          f"标签={t.get('enter_tag','')}")

print()
print("=" * 65)
print("结论：")

total_decline = (cur_px/ath_px - 1)*100
sh_profit = sh_after['profit_ratio'].sum()*100 if len(sh_after) else 0
lo_profit = lo_after['profit_ratio'].sum()*100 if len(lo_after) else 0

# 物理闭合阶段
closed_days = (first_below['date'] - ath_date).days if first_below is not None else (cur_date-ath_date).days
closed_chg  = (first_below['close']/ath_px - 1)*100 if first_below is not None else total_decline

print(f"  总跌幅 {total_decline:.1f}%  历时 {(cur_date-ath_date).days} 天")
print(f"  物理不可开空阶段：前 {closed_days} 天（-{abs(closed_chg):.1f}%），EMA200上方无熊市门")
if first_persist is not None and len(be_rows):
    blind_days  = (be_rows.iloc[0]['date'] - first_persist['date']).days
    blind_start = first_persist['close']
    blind_end   = be_rows.iloc[0]['close']
    blind_chg   = (blind_end/blind_start - 1)*100
    print(f"  BE信号缺失阶段：{blind_days} 天（{blind_chg:+.1f}%），ema200_persist已激活但BE从未触发")
print(f"  实际空头执行：{len(sh_after)} 笔  累计贡献={sh_profit:+.2f}%")
print(f"  期间多头执行：{len(lo_after)} 笔  累计贡献={lo_profit:+.2f}%")
print("=" * 65)
