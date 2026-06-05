"""
EMA200 冰线 LPSY — 时间窗口敏感度诊断
测试 ema200_persist 持续天数门槛（N=15/30/45）对10次触碰事件的过滤效果
"""
import sys
sys.path.insert(0, '/freqtrade')
import pandas as pd
import numpy as np

# ── 数据准备 ─────────────────────────────────────────────────────────────────
df1h = pd.read_feather('user_data/data/binance/futures/BTC_USDT_USDT-1h-futures.feather')
df1d = pd.read_feather('user_data/data/binance/futures/BTC_USDT_USDT-1d-futures.feather')
df1h['date'] = pd.to_datetime(df1h['date'], utc=True)
df1d['date'] = pd.to_datetime(df1d['date'], utc=True)

df1d = df1d.sort_values('date').reset_index(drop=True)
df1d['ema200'] = df1d['close'].ewm(span=200, adjust=False).mean()
df1d['ema200_lag'] = df1d['ema200'].shift(1)
df1d['below'] = df1d['close'] < df1d['ema200']
df1d['persist_raw'] = df1d['below'].rolling(7, min_periods=7).min().astype(bool)
df1d['persist_lag'] = df1d['persist_raw'].shift(1)

# 计算 ema200_persist 连续激活天数（以日线为单位，shift=1）
days_count = []
cnt = 0
for v in df1d['persist_raw']:
    if v:
        cnt += 1
    else:
        cnt = 0
    days_count.append(cnt)
df1d['persist_days'] = days_count
df1d['persist_days_lag'] = df1d['persist_days'].shift(1).fillna(0)

# 方案B锚点：ema200_persist 激活前 60 日滚动最高价（shift=1）
df1d['rolling_60d_high'] = df1d['high'].rolling(60, min_periods=30).max().shift(1)

df1h = df1h.sort_values('date').reset_index(drop=True)
df1h = pd.merge_asof(df1h,
    df1d[['date','ema200_lag','persist_lag','persist_days_lag','rolling_60d_high']].rename(
        columns={'ema200_lag':'ema200','persist_lag':'ema200_persist',
                 'persist_days_lag':'persist_days','rolling_60d_high':'ref_high'}),
    on='date', direction='backward')
df1h['ema200_persist'] = df1h['ema200_persist'].fillna(False).astype(bool)
df1h['persist_days'] = df1h['persist_days'].fillna(0)

study = df1h[df1h['date'] >= '2025-01-01'].copy().reset_index(drop=True)

# ── 基础触碰事件（同前，无时间门槛） ─────────────────────────────────────────
TOUCH_RATIO = 0.98
COOLDOWN_H  = 48

study['touch_ice_base'] = (
    study['ema200_persist'] &
    (study['high'] >= study['ema200'] * TOUCH_RATIO) &
    (study['close'] < study['ema200']) &
    (study['close'] < study['open'])
)

base_events = []
last_idx = -COOLDOWN_H * 2
for i, row in study.iterrows():
    if row['touch_ice_base'] and (i - last_idx) >= COOLDOWN_H:
        base_events.append(i)
        last_idx = i

# ── 逐事件构建完整信息 ────────────────────────────────────────────────────────
records = []
for idx in base_events:
    row = study.loc[idx]
    date_str = str(row['date'])[:10]
    price    = row['close']
    ema200   = row['ema200']
    p_days   = int(row['persist_days'])
    ref_high = row['ref_high']
    drawdown = (price / ref_high - 1) * 100 if ref_high > 0 else np.nan

    def fwd_chg(bars):
        fi = idx + bars
        return (study.loc[fi, 'close'] / price - 1) * 100 if fi < len(study) else np.nan

    chg7d  = fwd_chg(7 * 24)
    short7 = -chg7d if not np.isnan(chg7d) else np.nan

    # 是否为 TASK19 陷阱日期附近
    note = 'TASK19陷阱' if '2026-04-13' in date_str else ''
    # 标记高质量信号
    if short7 is not np.nan and not np.isnan(short7) and short7 >= 5:
        note = 'HIGH-QUALITY'

    records.append({
        'date': date_str,
        'price': price,
        'ema200': ema200,
        'persist_days': p_days,
        'ref_high': ref_high,
        'drawdown_pct': drawdown,
        'short7d': short7,
        'note': note,
    })

df_ev = pd.DataFrame(records)

# ── 输出主表 ──────────────────────────────────────────────────────────────────
print('=' * 78)
print('  EMA200 冰线 LPSY 诊断 — 时间窗口敏感度')
print('=' * 78)
print()
print(f'{"日期":12s} {"价格":>9} {"EMA200":>9} {"持续天":>7} {"锚点高":>9} '
      f'{"回撤%":>7} {"空头7D":>8} {"备注":>14}')
print('-' * 78)
for _, r in df_ev.iterrows():
    dd  = f'{r["drawdown_pct"]:.1f}%' if not np.isnan(r['drawdown_pct']) else 'N/A'
    s7  = f'{r["short7d"]:+.1f}%'     if not np.isnan(r['short7d'])     else 'N/A'
    print(f'{r["date"]:12s} {r["price"]:>9,.0f} {r["ema200"]:>9,.0f} '
          f'{r["persist_days"]:>7.0f} {r["ref_high"]:>9,.0f} '
          f'{dd:>7} {s7:>8} {r["note"]:>14}')

# ── 时间门槛对照表 ────────────────────────────────────────────────────────────
print()
print('=' * 78)
print('  时间门槛 vs 捕捉率 对照表')
print('  （保留=通过门槛的事件，过滤=被门槛拦截）')
print('=' * 78)
print()
print(f'{"门槛":>8} {"保留数":>6} {"过滤数":>6} {"胜率7D":>8} '
      f'{"均收益":>9} {"保留日期列表"}')
print('-' * 78)

thresholds = [0, 15, 30, 45]
for N in thresholds:
    kept = df_ev[df_ev['persist_days'] >= N].copy()
    filtered = df_ev[df_ev['persist_days'] < N]
    valid = kept.dropna(subset=['short7d'])
    wr  = (valid['short7d'] > 0).sum() / len(valid) * 100 if len(valid) > 0 else 0
    avg = valid['short7d'].mean() if len(valid) > 0 else 0
    dates = ' | '.join(kept['date'].tolist())
    label = f'N>={N}天' if N > 0 else '无门槛'
    print(f'{label:>8} {len(kept):>6} {len(filtered):>6} {wr:>7.0f}% '
          f'{avg:>+8.2f}% {dates}')

# ── 关键信号分析 ──────────────────────────────────────────────────────────────
print()
print('=' * 78)
print('  关键信号逐项分析')
print('=' * 78)

for N in [30, 45]:
    kept = df_ev[df_ev['persist_days'] >= N]
    filtered = df_ev[df_ev['persist_days'] < N]
    print(f'\n[N>={N}天 门槛]')
    print(f'  保留 {len(kept)} 笔: {", ".join(kept["date"].tolist())}')
    print(f'  过滤 {len(filtered)} 笔: {", ".join(filtered["date"].tolist())}')
    valid = kept.dropna(subset=['short7d'])
    if len(valid) > 0:
        print(f'  7日空头胜率: {(valid["short7d"]>0).sum()}/{len(valid)} = '
              f'{(valid["short7d"]>0).mean()*100:.0f}%  均收益: {valid["short7d"].mean():+.2f}%')
    # 高质量信号是否保留
    hq = kept[kept['note'] == 'HIGH-QUALITY']
    print(f'  高质量信号(7D>5%)保留: {len(hq)} 笔 — {", ".join(hq["date"].tolist()) if len(hq) else "无"}')

print()
print('=' * 78)
print('  2025-03/04 假信号 vs 2025-11/2026-01 真信号 — 持续天数对比')
print('=' * 78)
for _, r in df_ev.iterrows():
    tag = ''
    if r['date'] in ['2025-03-16','2025-03-18','2025-04-04','2025-04-09','2025-04-11']:
        tag = '<-- 疑似假信号（牛市深调）'
    if r['date'] in ['2025-11-10','2026-01-14']:
        tag = '<-- 高质量目标信号'
    if tag:
        print(f'  {r["date"]}  persist_days={r["persist_days"]:.0f}天  空头7D={r["short7d"]:+.1f}%  {tag}')
print()
