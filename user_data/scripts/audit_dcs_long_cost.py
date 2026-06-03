"""
多头机会成本审计 — DCS=True 期间的多头 vs 空头取舍

量化问题：
  1. DCS=True 期间，多头表现（平均收益、胜率、持仓时长、槽位占用）
  2. 同期被挤占的空头信号数量
  3. 若实施方案A（DCS=True时阻断多头），预计空出多少窗口

运行：
  docker compose run --rm --entrypoint "" freqtrade \
    sh -c "python user_data/scripts/audit_dcs_long_cost.py"
"""

import sys
sys.path.insert(0, '/freqtrade')

import pandas as pd
import numpy as np
from wyckoff.daily_event_detector import DailyEventDetector

# ─── 日线数据 + DCS 检测 ─────────────────────────────────────────────────────
df_1d = pd.read_feather('user_data/data/binance/futures/BTC_USDT_USDT-1d-futures.feather')
df_1d['date'] = pd.to_datetime(df_1d['date'])
df_1d = df_1d.sort_values('date').reset_index(drop=True)
df_1d = df_1d[df_1d['date'] >= '2021-01-01'].reset_index(drop=True)

det = DailyEventDetector()
df_1d = det.detect(df_1d.copy())

# +1天 shift（防未来函数）
df_1d['dcs_shifted'] = df_1d['dist_cause_sufficient'].shift(1).fillna(False)

# ─── 1H数据 ──────────────────────────────────────────────────────────────────
df_1h = pd.read_feather('user_data/data/binance/futures/BTC_USDT_USDT-1h-futures.feather')
df_1h['date'] = pd.to_datetime(df_1h['date'])
df_1h = df_1h.sort_values('date').reset_index(drop=True)
df_1h = df_1h[(df_1h['date'] >= '2022-01-01') & (df_1h['date'] <= '2022-12-31')].reset_index(drop=True)

# merge DCS 到 1H
df_1d_slim = df_1d[['date', 'dcs_shifted', 'dist_cause_sufficient', 'v8_phase' if 'v8_phase' in df_1d.columns else 'dist_cause_sufficient']].copy()
# 实际上 v8_phase 在 daily_event_detector 里不输出，用 dist_sequence_progress 代替
df_1d_slim = df_1d[['date', 'dcs_shifted', 'dist_cause_sufficient', 'dist_sequence_progress']].copy()
df_1d_slim['date_shifted'] = pd.to_datetime(df_1d_slim['date'], utc=True) + pd.Timedelta(days=1)

df_1h['date_utc'] = pd.to_datetime(df_1h['date'], utc=True)
df_1h = pd.merge_asof(
    df_1h.sort_values('date_utc'),
    df_1d_slim.rename(columns={'date_shifted': 'date_1d'}).sort_values('date_1d'),
    left_on='date_utc', right_on='date_1d',
    direction='backward'
)

# ─── 读取回测交易数据 ────────────────────────────────────────────────────────
import json, glob, os

# 找最新回测结果
result_files = sorted(glob.glob('/freqtrade/user_data/backtest_results/**/*.json', recursive=True))
# 过滤掉 config 文件
result_files = [f for f in result_files if '_config' not in f and '_WyckoffV' not in f and 'market_change' not in f]
if not result_files:
    print("未找到回测结果文件，请先运行 2022 年回测")
    sys.exit(1)

# 先运行 2022 专项回测获取交易数据
print("正在运行 2022 年专项回测（导出交易数据）...")
import subprocess
result = subprocess.run(
    ['freqtrade', 'backtesting',
     '--config', 'user_data/config_backtest.json',
     '--config', 'user_data/config_v8_backtest.json',
     '--strategy', 'WyckoffV8',
     '--timerange', '20220101-20221231',
     '--cache', 'none',
     '--export', 'trades',
     '--export-filename', '/freqtrade/user_data/backtest_results/audit_2022.json'],
    capture_output=True, text=True, cwd='/freqtrade'
)

# 找 2022 结果
result_files2 = sorted(glob.glob('/freqtrade/user_data/backtest_results/audit_2022*.json'))
if not result_files2:
    print("回测导出失败，stdout:", result.stdout[-2000:])
    sys.exit(1)

with open(result_files2[-1]) as f:
    bt_data = json.load(f)

# 提取交易列表
strategy_key = list(bt_data['strategy'].keys())[0]
trades_raw = bt_data['strategy'][strategy_key].get('trades', [])
if not trades_raw:
    # 尝试新格式
    trades_raw = bt_data.get('strategy', {}).get('WyckoffV8', {}).get('trades', [])

print(f"提取到 {len(trades_raw)} 笔交易\n")

trades = pd.DataFrame(trades_raw)
trades['open_date']  = pd.to_datetime(trades['open_date'])
trades['close_date'] = pd.to_datetime(trades['close_date'])
trades['duration_h'] = (trades['close_date'] - trades['open_date']).dt.total_seconds() / 3600

# 关联 DCS 状态（用开仓时间查 1H 的 dcs_shifted）
df_1h_dcs = df_1h[['date', 'dcs_shifted']].copy()
df_1h_dcs['date'] = pd.to_datetime(df_1h_dcs['date'], utc=True)

trades['open_date_utc'] = pd.to_datetime(trades['open_date'], utc=True)
trades = pd.merge_asof(
    trades.sort_values('open_date_utc'),
    df_1h_dcs.rename(columns={'date': 'date_1h'}).sort_values('date_1h'),
    left_on='open_date_utc', right_on='date_1h',
    direction='backward'
)

# ─── 分析 ─────────────────────────────────────────────────────────────────────

print("=" * 70)
print("【2022年 多头机会成本审计】")
print("=" * 70)

longs  = trades[trades['is_short'] == False].copy()
shorts = trades[trades['is_short'] == True].copy()

longs_dcs_true  = longs[longs['dcs_shifted'] == True]
longs_dcs_false = longs[longs['dcs_shifted'] == False]

print(f"\n总交易: {len(trades)} 笔  (多头={len(longs)}, 空头={len(shorts)})")

print(f"\n─── 多头分组（按DCS状态）───")
def stats(df, label):
    if len(df) == 0:
        print(f"  {label}: 0笔")
        return
    wr = (df['profit_ratio'] > 0).mean() * 100
    avg_p = df['profit_ratio'].mean() * 100
    avg_d = df['duration_h'].mean()
    total_p = df['profit_ratio'].sum() * 100
    slot_h = df['duration_h'].sum()
    print(f"  {label}: {len(df):3d}笔 | 胜率={wr:.1f}% | 均收益={avg_p:+.2f}% | "
          f"均持仓={avg_d:.1f}h ({avg_d/24:.1f}天) | 槽位总占={slot_h:.0f}h ({slot_h/24:.1f}天)")

stats(longs_dcs_true,  "多头 DCS=True  (熊市背景)")
stats(longs_dcs_false, "多头 DCS=False (牛市背景)")
stats(shorts,          "空头            (全部)    ")

print(f"\n─── 槽位竞争分析（2022年总交易时间=8760h）───")
total_hours = 8760
long_dcs_slot  = longs_dcs_true['duration_h'].sum()
long_ndcs_slot = longs_dcs_false['duration_h'].sum()
short_slot     = shorts['duration_h'].sum()
idle_dcs_true  = df_1h[df_1h['dcs_shifted'] == True].shape[0]  # DCS=True的1H bar数

print(f"  DCS=True  期间总1H bars : {idle_dcs_true} 根 ({idle_dcs_true/24:.0f}天)")
print(f"  DCS=True 期间多头占位   : {long_dcs_slot:.0f}h ({long_dcs_slot/24:.1f}天, "
      f"{long_dcs_slot/idle_dcs_true*100:.1f}% of DCS窗口)")
print(f"  DCS=False期间多头占位   : {long_ndcs_slot:.0f}h ({long_ndcs_slot/24:.1f}天)")
print(f"  空头占位                : {short_slot:.0f}h ({short_slot/24:.1f}天)")

print(f"\n─── DCS=True期间 空头信号被挤分析 ───")
# 空头信号在DCS=True期间多少根bar有信号
dcs_true_bars = df_1h[df_1h['dcs_shifted'] == True]
print(f"  DCS=True 1H bars 总数  : {len(dcs_true_bars)}")
# 无法直接看1H enter_short信号（需要全策略计算），估算方式：
# 从回测日志已知 2022年 sow=173, be=120, lpsy=160
# 这些信号分布在DCS=True期间的比例
print(f"  (回测日志) 2022年全年空头信号 sow=173, be=120, lpsy=160 → 共453个")
dcs_ratio = idle_dcs_true / 8760
print(f"  DCS=True 占全年比例    : {dcs_ratio*100:.1f}%")
print(f"  估算DCS=True期间空头信号: ~{int(453*dcs_ratio)} 个（比例估算）")

print(f"\n─── 逐笔明细（DCS=True期间多头）───")
print(f"  {'开仓':20s} {'关仓':20s} {'收益%':>7} {'持仓h':>7} {'exit_reason'}")
for _, t in longs_dcs_true.sort_values('open_date').iterrows():
    print(f"  {str(t['open_date'])[:19]:20s} {str(t['close_date'])[:19]:20s} "
          f"{t['profit_ratio']*100:+6.2f}% {t['duration_h']:7.1f}h  {t.get('exit_reason','')}")

print(f"\n─── 逐笔明细（空头）───")
print(f"  {'开仓':20s} {'关仓':20s} {'收益%':>7} {'持仓h':>7} {'enter_tag':15s} {'exit_reason'}")
for _, t in shorts.sort_values('open_date').iterrows():
    print(f"  {str(t['open_date'])[:19]:20s} {str(t['close_date'])[:19]:20s} "
          f"{t['profit_ratio']*100:+6.2f}% {t['duration_h']:7.1f}h  "
          f"{t.get('enter_tag',''):15s} {t.get('exit_reason','')}")

print(f"\n─── 方案A取舍测算 ───")
long_dcs_profit_pct = longs_dcs_true['profit_ratio'].sum() * 100
print(f"  放弃 DCS=True 期间多头利润 : {long_dcs_profit_pct:+.2f}% 累计")
print(f"  换取 空头信号窗口增加约     : {long_dcs_slot:.0f}h ({long_dcs_slot/24:.1f}天)")
print(f"  当前空头平均单笔收益        : {shorts['profit_ratio'].mean()*100:.2f}%")
extra_shorts_est = long_dcs_slot / (shorts['duration_h'].mean() if len(shorts) > 0 else 48)
extra_profit_est = extra_shorts_est * (shorts['profit_ratio'].mean() if len(shorts) > 0 else 0) * 100
print(f"  预计增加空头笔数（估算）    : {extra_shorts_est:.1f} 笔")
print(f"  预计增加空头利润（估算）    : {extra_profit_est:+.2f}%")
print(f"\n  净效果（粗估）             : {extra_profit_est - long_dcs_profit_pct:+.2f}%")

print("\n" + "=" * 70)
