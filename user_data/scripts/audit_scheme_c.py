"""
方案C前置诊断 — 空头信号与多头持仓的时间重叠度

核心问题：
  1. DCS=True 期间，多头持仓期间有多少1H bar同时存在空头信号？
  2. 空头信号触发后平均持续多少根1H bar（信号半衰期）？

运行：
  docker compose run --rm --entrypoint "" freqtrade \
    sh -c "python user_data/scripts/audit_scheme_c.py"
"""

import sys, json, zipfile, glob
sys.path.insert(0, '/freqtrade')

import pandas as pd
import numpy as np
from wyckoff.daily_event_detector import DailyEventDetector

# ─── 1. 加载回测交易数据 ──────────────────────────────────────────────────────
zips = sorted(glob.glob('user_data/backtest_results/*.zip'))
with zipfile.ZipFile(zips[-1]) as z:
    fname = [n for n in z.namelist() if n.endswith('.json') and 'config' not in n][0]
    with z.open(fname) as f:
        data = json.load(f)

sk = list(data['strategy'].keys())[0]
trades = pd.DataFrame(data['strategy'][sk]['trades'])
trades['open_date']  = pd.to_datetime(trades['open_date'], utc=True)
trades['close_date'] = pd.to_datetime(trades['close_date'], utc=True)
longs = trades[~trades['is_short']].copy()

# ─── 2. 日线数据 + DCS + 空头信号检测 ────────────────────────────────────────
df1d = pd.read_feather('user_data/data/binance/futures/BTC_USDT_USDT-1d-futures.feather')
df1d['date'] = pd.to_datetime(df1d['date'], utc=True)
df1d = df1d[df1d['date'] >= '2021-01-01'].sort_values('date').reset_index(drop=True)

det = DailyEventDetector()
df1d = det.detect(df1d.copy())

# shift(1)：防未来函数，策略当天只能看到昨日信号
df1d['dcs']  = df1d['dist_cause_sufficient'].shift(1).fillna(False)
df1d['be']   = df1d['buying_exhaustion'].shift(1).fillna(False)
df1d['sow']  = df1d['daily_dist_sow_detected'].shift(1).fillna(False)
df1d['utad'] = df1d['daily_utad_detected'].shift(1).fillna(False) if 'daily_utad_detected' in df1d.columns else False

# 综合空头信号（日线层）
df1d['any_short_sig'] = df1d['be'] | df1d['sow'] | df1d['utad']

# ─── 3. 1H数据 — merge日线信号 ────────────────────────────────────────────────
df1h = pd.read_feather('user_data/data/binance/futures/BTC_USDT_USDT-1h-futures.feather')
df1h['date'] = pd.to_datetime(df1h['date'], utc=True)
df1h = df1h[df1h['date'] >= '2022-01-01'].sort_values('date').reset_index(drop=True)

# merge日线信号到1H（向前填充=当天的24根bar都继承该信号）
df1d_slim = df1d[['date', 'dcs', 'be', 'sow', 'utad', 'any_short_sig']].copy()
df1d_slim['date_1d'] = df1d_slim['date'] + pd.Timedelta(days=1)
df1d_slim = df1d_slim.drop(columns=['date']).sort_values('date_1d')

df1h = pd.merge_asof(
    df1h.sort_values('date'),
    df1d_slim,
    left_on='date', right_on='date_1d',
    direction='backward'
)
df1h[['dcs','be','sow','utad','any_short_sig']] = (
    df1h[['dcs','be','sow','utad','any_short_sig']].fillna(False)
)

# ─── 4. 标记每根1H bar是否在多头持仓窗口内 ───────────────────────────────────
df1h['long_open'] = False

# 只看 DCS=True 期间的多头
longs_dcs = longs[longs['open_date'].dt.date.astype(str).map(
    df1d.set_index(df1d['date'].dt.date.astype(str))['dcs'].to_dict()
).fillna(False)].copy()

for _, t in longs.iterrows():
    mask = (df1h['date'] >= t['open_date']) & (df1h['date'] < t['close_date'])
    df1h.loc[mask, 'long_open'] = True

# DCS=True 子集
df_dcs = df1h[df1h['dcs']].copy()

# ─── 5. 核心统计 ─────────────────────────────────────────────────────────────
print("=" * 65)
print("方案C前置诊断 — 空头信号与多头持仓重叠度")
print("=" * 65)

total_dcs_bars = len(df_dcs)
long_open_bars  = df_dcs['long_open'].sum()
short_sig_bars  = df_dcs['any_short_sig'].sum()
overlap_bars    = (df_dcs['long_open'] & df_dcs['any_short_sig']).sum()

print(f"\nDCS=True 期间 1H bars 总计  : {total_dcs_bars:,}")
print(f"  其中 多头持仓中            : {long_open_bars:,}  ({long_open_bars/total_dcs_bars*100:.1f}%)")
print(f"  其中 有空头信号            : {short_sig_bars:,}  ({short_sig_bars/total_dcs_bars*100:.1f}%)")
print(f"  【重叠】多头持仓 & 空头信号 : {overlap_bars:,}  ({overlap_bars/total_dcs_bars*100:.1f}% of DCS期间)")

if long_open_bars > 0:
    print(f"  重叠占多头持仓比例         : {overlap_bars/long_open_bars*100:.1f}%")
if short_sig_bars > 0:
    print(f"  重叠占空头信号比例         : {overlap_bars/short_sig_bars*100:.1f}%")

# 按信号类型拆分
print(f"\n按信号类型拆分（重叠1H bars）：")
for sig in ['be', 'sow', 'utad']:
    ov = (df_dcs['long_open'] & df_dcs[sig]).sum()
    total = df_dcs[sig].sum()
    print(f"  {sig.upper():6s}: 总信号={total:4d}bars  重叠={ov:4d}bars  ({ov/max(total,1)*100:.1f}%被多头占据)")

# ─── 6. 信号半衰期分析 ────────────────────────────────────────────────────────
print(f"\n{'─'*65}")
print("空头信号半衰期（触发后持续多少1H bar）：")

df1h_full = df1h.copy()

for sig in ['be', 'sow']:
    # 找每次信号的起始点（上升沿）
    col = df1h_full[sig].astype(int)
    rising = (col == 1) & (col.shift(1).fillna(0) == 0)
    starts = df1h_full[rising].index.tolist()

    durations = []
    for idx in starts:
        # 从起始点向后数连续True的bar数
        run = 0
        for i in range(idx, min(idx + 200, len(df1h_full))):
            if df1h_full.loc[i, sig]:
                run += 1
            else:
                break
        durations.append(run)

    if durations:
        arr = np.array(durations)
        print(f"  {sig.upper():6s}: 触发次数={len(arr):3d}  "
              f"中位={np.median(arr):.0f}h  均值={arr.mean():.1f}h  "
              f"最短={arr.min()}h  最长={arr.max()}h  "
              f"(≥24h占比={( arr>=24).mean()*100:.0f}%)")
    else:
        print(f"  {sig.upper():6s}: 无触发")

# ─── 7. 逐笔：多头持仓中出现空头信号的具体时间窗口 ───────────────────────────
print(f"\n{'─'*65}")
print("逐笔多头持仓：持仓期间是否出现空头信号")
print(f"  {'开仓':19s} {'关仓':19s} {'DCS':>4} {'BE信号h':>8} {'SOW信号h':>9} {'有重叠':>6}")

dcs_map = df1d.set_index(df1d['date'].dt.date.astype(str))['dcs'].to_dict()

for _, t in longs.sort_values('open_date').iterrows():
    open_d  = str(t['open_date'].date())
    dcs_at  = bool(dcs_map.get(open_d, False))
    mask    = (df1h['date'] >= t['open_date']) & (df1h['date'] < t['close_date'])
    window  = df1h[mask]
    be_h    = int(window['be'].sum())
    sow_h   = int(window['sow'].sum())
    overlap = (be_h + sow_h) > 0
    print(f"  {str(t['open_date'])[:19]:19s} {str(t['close_date'])[:19]:19s} "
          f"{'✓' if dcs_at else '':>4} {be_h:>8} {sow_h:>9} {'★重叠' if overlap else '':>6}")

# ─── 8. 逐年分解 ──────────────────────────────────────────────────────────────
print(f"\n{'─'*65}")
print("逐年分解：DCS天数 / 空头信号 / 多头持仓 / 重叠")
print(f"  {'年份':6} {'DCS天':>7} {'空头信号h':>10} {'多头持仓h':>10} {'重叠h':>7} {'重叠/DCS':>9} {'重叠事件数':>10}")

for yr in range(2022, 2027):
    mask_yr = df1h['date'].dt.year == yr
    sub = df1h[mask_yr]
    sub_dcs = sub[sub['dcs']]
    dcs_bars_yr   = len(sub_dcs)
    sig_bars_yr   = int(sub_dcs['any_short_sig'].sum())
    long_bars_yr  = int(sub_dcs['long_open'].sum())
    ov_bars_yr    = int((sub_dcs['long_open'] & sub_dcs['any_short_sig']).sum())
    # 重叠事件：连续重叠块计数
    ov_series = (sub_dcs['long_open'] & sub_dcs['any_short_sig']).reset_index(drop=True)
    events = int(((ov_series == True) & (ov_series.shift(1).fillna(False) == False)).sum())
    pct = f"{ov_bars_yr/dcs_bars_yr*100:.1f}%" if dcs_bars_yr > 0 else "N/A"
    print(f"  {yr}   {dcs_bars_yr//24:>7} {sig_bars_yr:>10} {long_bars_yr:>10} {ov_bars_yr:>7} {pct:>9} {events:>10}")

# ─── 9. 结论 ─────────────────────────────────────────────────────────────────
print(f"\n{'='*65}")
print("方案C可行性判断：")
if overlap_bars >= 20 * 24:
    print(f"  ★ 重叠度充分（{overlap_bars}bars ≈ {overlap_bars//24}天）")
    print(f"  → 方案C有足够的'抢占窗口'，建议立项")
elif overlap_bars >= 5 * 24:
    print(f"  △ 重叠度中等（{overlap_bars}bars ≈ {overlap_bars//24}天）")
    print(f"  → 方案C有一定潜力，但收益空间有限，需谨慎评估")
else:
    print(f"  ✗ 重叠度不足（{overlap_bars}bars ≈ {overlap_bars//24}天）")
    print(f"  → 方案C前提假设不成立，强行实施将损失复利基石")
print("=" * 65)
