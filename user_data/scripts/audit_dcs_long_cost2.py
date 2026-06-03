"""多头机会成本审计 v2"""
import json, zipfile, sys, glob
sys.path.insert(0, '/freqtrade')
import pandas as pd
import numpy as np
from wyckoff.daily_event_detector import DailyEventDetector

# 找最新 2022 回测 zip
zips = sorted(glob.glob('user_data/backtest_results/backtest-result-2026-06-03_08-03-43.zip'))
if not zips:
    zips = sorted(glob.glob('user_data/backtest_results/*.zip'))
with zipfile.ZipFile(zips[-1]) as z:
    fname = [n for n in z.namelist() if n.endswith('.json') and 'config' not in n][0]
    with z.open(fname) as f:
        data = json.load(f)

sk = list(data['strategy'].keys())[0]
trades = pd.DataFrame(data['strategy'][sk]['trades'])
trades['open_date'] = pd.to_datetime(trades['open_date'])
trades['close_date'] = pd.to_datetime(trades['close_date'])
trades['duration_h'] = (trades['close_date'] - trades['open_date']).dt.total_seconds()/3600

# DCS mapping
df1d = pd.read_feather('user_data/data/binance/futures/BTC_USDT_USDT-1d-futures.feather')
df1d['date'] = pd.to_datetime(df1d['date'])
df1d = df1d.sort_values('date').reset_index(drop=True)
df1d = df1d[df1d['date'] >= '2021-01-01'].reset_index(drop=True)
det = DailyEventDetector()
df1d = det.detect(df1d.copy())
df1d['dcs_avail'] = df1d['dist_cause_sufficient'].shift(1).fillna(False)

trades['open_date_d'] = trades['open_date'].dt.date.astype(str)
df1d['date_str'] = df1d['date'].dt.date.astype(str)
dcs_map = df1d.set_index('date_str')['dcs_avail'].to_dict()
trades['dcs_at_entry'] = trades['open_date_d'].map(dcs_map).fillna(False)

longs  = trades[~trades['is_short']]
shorts = trades[trades['is_short']]
ld = longs[longs['dcs_at_entry']]   # DCS=True 多头
ln = longs[~longs['dcs_at_entry']]  # DCS=False 多头

print("=" * 65)
print("2022年 多头机会成本审计")
print("=" * 65)
print(f"\n总交易: {len(trades)} 笔 (多={len(longs)}, 空={len(shorts)})")
print()

def show(df, label):
    if len(df) == 0:
        print(f"{label}: 0笔\n"); return
    wr = (df['profit_ratio'] > 0).mean() * 100
    avg_p = df['profit_ratio'].mean() * 100
    tot_p = df['profit_ratio'].sum() * 100
    avg_d = df['duration_h'].mean()
    tot_d = df['duration_h'].sum()
    wins  = (df['profit_ratio'] > 0).sum()
    losses = (df['profit_ratio'] <= 0).sum()
    print(f"{label}")
    print(f"  笔数={len(df)}  胜/负={wins}/{losses}  胜率={wr:.1f}%")
    print(f"  均收益={avg_p:+.2f}%  累计收益={tot_p:+.2f}%")
    print(f"  均持仓={avg_d/24:.1f}天  总占位={tot_d/24:.1f}天 ({tot_d/87.6:.1f}% of全年)")
    print()

show(ld, "【多头 DCS=True (熊市背景)】")
show(ln, "【多头 DCS=False (牛市背景)】")
show(shorts, "【空头 (全部)】")

print("-" * 65)
print("逐笔 DCS=True 多头：")
h1, h2, h3, h4, h5 = "开仓", "关仓", "收益%", "持仓天", "exit_reason"
print(f"  {h1:19s} {h2:19s} {h3:>7} {h4:>7}  {h5}")
for _, t in ld.sort_values('open_date').iterrows():
    print(f"  {str(t['open_date'])[:19]:19s} {str(t['close_date'])[:19]:19s} "
          f"{t['profit_ratio']*100:+6.2f}% {t['duration_h']/24:7.1f}  {t.get('exit_reason','')}")

print()
print("逐笔 空头：")
h6 = "enter_tag"
print(f"  {h1:19s} {h2:19s} {h3:>7} {h4:>7}  {h6:16s} {h5}")
for _, t in shorts.sort_values('open_date').iterrows():
    print(f"  {str(t['open_date'])[:19]:19s} {str(t['close_date'])[:19]:19s} "
          f"{t['profit_ratio']*100:+6.2f}% {t['duration_h']/24:7.1f}  "
          f"{t.get('enter_tag',''):16s} {t.get('exit_reason','')}")

print()
print("=" * 65)
print("方案A 取舍测算：")
freed   = ld['duration_h'].sum()
lost_p  = ld['profit_ratio'].sum() * 100
avg_sp  = shorts['profit_ratio'].mean() if len(shorts) > 0 else 0
avg_sd  = shorts['duration_h'].mean() if len(shorts) > 0 else 48
extra_n = freed / avg_sd
extra_p = extra_n * avg_sp * 100
print(f"  放弃 DCS=True 多头利润 : {lost_p:+.2f}%")
print(f"  释放槽位时间           : {freed/24:.1f}天")
print(f"  空头均持仓             : {avg_sd/24:.1f}天  均收益={avg_sp*100:.2f}%/笔")
print(f"  预计增加空头数(估算)   : {extra_n:.1f}笔")
print(f"  预计增加空头利润(估算) : {extra_p:+.2f}%")
print(f"  净效果(粗估)           : {extra_p - lost_p:+.2f}%")
print("=" * 65)
