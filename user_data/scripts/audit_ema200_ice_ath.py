"""
EMA200 冰线 LPSY — ATH 接近度诊断
核心问题：ref_high >= cycle_max * 0.90 能否精准区分真假信号？
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

# persist 连续天数
days_cnt = []
cnt = 0
for v in df1d['persist_raw']:
    cnt = cnt + 1 if v else 0
    days_cnt.append(cnt)
df1d['persist_days'] = days_cnt
df1d['persist_days_lag'] = df1d['persist_days'].shift(1).fillna(0)

# 方案B锚点：过去60日滚动最高价（shift=1，仅用过去数据）
df1d['ref_high_60d'] = df1d['high'].rolling(60, min_periods=30).max().shift(1)

# 全局 cycle_max：截止当前 bar 的历史最高价（expanding max，shift=1）
df1d['cycle_max'] = df1d['high'].expanding().max().shift(1)

df1h = df1h.sort_values('date').reset_index(drop=True)
df1h = pd.merge_asof(df1h,
    df1d[['date','ema200_lag','persist_lag','persist_days_lag',
          'ref_high_60d','cycle_max']].rename(
        columns={'ema200_lag':'ema200','persist_lag':'ema200_persist',
                 'persist_days_lag':'persist_days'}),
    on='date', direction='backward')
df1h['ema200_persist'] = df1h['ema200_persist'].fillna(False).astype(bool)
df1h['persist_days']   = df1h['persist_days'].fillna(0)

study = df1h[df1h['date'] >= '2025-01-01'].copy().reset_index(drop=True)

# ── 基础触碰事件检测 ─────────────────────────────────────────────────────────
TOUCH_RATIO = 0.98
COOLDOWN_H  = 48

study['touch_base'] = (
    study['ema200_persist'] &
    (study['high'] >= study['ema200'] * TOUCH_RATIO) &
    (study['close'] < study['ema200']) &
    (study['close'] < study['open'])
)

base_events = []
last_idx = -COOLDOWN_H * 2
for i, row in study.iterrows():
    if row['touch_base'] and (i - last_idx) >= COOLDOWN_H:
        base_events.append(i)
        last_idx = i

# ── 构建事件记录 ─────────────────────────────────────────────────────────────
records = []
for idx in base_events:
    row = study.loc[idx]
    price    = row['close']
    ema200   = row['ema200']
    ref_high = row['ref_high_60d']
    cyc_max  = row['cycle_max']
    ath_ratio = ref_high / cyc_max if cyc_max > 0 else np.nan
    p_days   = int(row['persist_days'])

    def fwd(bars):
        fi = idx + bars
        return (study.loc[fi,'close'] / price - 1) * 100 if fi < len(study) else np.nan

    chg3  = fwd(3*24)
    chg7  = fwd(7*24)
    chg14 = fwd(14*24)
    short7 = -chg7 if not np.isnan(chg7) else np.nan

    records.append({
        'date':      str(row['date'])[:10],
        'price':     price,
        'ema200':    ema200,
        'ref_high':  ref_high,
        'cycle_max': cyc_max,
        'ath_ratio': ath_ratio,
        'p_days':    p_days,
        'chg3':      chg3,
        'short7':    short7,
        'chg14':     chg14,
    })

df_ev = pd.DataFrame(records)

# ── 输出完整事件表 ────────────────────────────────────────────────────────────
print('=' * 82)
print('  EMA200 冰线 LPSY — ATH 接近度诊断')
print('  核心指标：ref_high / cycle_max（锚点位势比）')
print('=' * 82)
print()
print(f'{"日期":12s} {"价格":>8} {"EMA200":>8} {"锚点":>8} {"ATH":>8} '
      f'{"位势比":>7} {"持续天":>7} {"空头7D":>8}')
print('-' * 82)

for _, r in df_ev.iterrows():
    s7  = f'{r["short7"]:+.1f}%' if not np.isnan(r['short7']) else 'N/A'
    ar  = f'{r["ath_ratio"]:.3f}' if not np.isnan(r['ath_ratio']) else 'N/A'
    flag = ''
    if not np.isnan(r['ath_ratio']) and r['ath_ratio'] >= 0.90:
        flag = '<-- ATH区'
    elif r['date'] in ['2025-11-10']:
        flag = '<-- !!!'
    print(f'{r["date"]:12s} {r["price"]:>8,.0f} {r["ema200"]:>8,.0f} '
          f'{r["ref_high"]:>8,.0f} {r["cycle_max"]:>8,.0f} '
          f'{ar:>7} {r["p_days"]:>7} {s7:>8}  {flag}')

# ── ATH 接近度门槛对照表 ──────────────────────────────────────────────────────
print()
print('=' * 82)
print('  ATH 位势比门槛 vs 捕捉率对照（核心对比）')
print('=' * 82)
print()
print(f'{"门槛":>12} {"保留":>6} {"过滤":>6} {"胜率7D":>8} {"均收益":>9}  保留日期')
print('-' * 82)

for thresh in [0.0, 0.80, 0.85, 0.90, 0.92, 0.95]:
    kept    = df_ev[df_ev['ath_ratio'] >= thresh].dropna(subset=['short7'])
    filt    = df_ev[df_ev['ath_ratio'] <  thresh]
    wr      = (kept['short7'] > 0).mean() * 100 if len(kept) > 0 else 0
    avg     = kept['short7'].mean() if len(kept) > 0 else 0
    dates   = ' | '.join(kept['date'].tolist())
    label   = f'>={thresh:.2f}' if thresh > 0 else '无门槛'
    print(f'{label:>12} {len(kept):>6} {len(filt):>6} {wr:>7.0f}% {avg:>+8.2f}%  {dates}')

# ── 2025-03 vs 2025-11 关键对比 ───────────────────────────────────────────────
print()
print('=' * 82)
print('  2025-03/04 假信号 vs 2025-11/2026-01 真信号 — 位势比对比')
print('=' * 82)
key_dates = {
    '2025-03-16': '疑似假（牛市深调）',
    '2025-03-18': '疑似假（牛市深调）',
    '2025-04-04': '疑似假（牛市深调）',
    '2025-04-09': '疑似假（牛市深调）',
    '2025-04-11': '疑似假（牛市深调）',
    '2025-11-10': '目标真信号（ATH崩盘）',
    '2026-01-14': '目标真信号（持续下跌）',
}
for _, r in df_ev.iterrows():
    if r['date'] in key_dates:
        ar = f'{r["ath_ratio"]:.3f}' if not np.isnan(r['ath_ratio']) else 'N/A'
        s7 = f'{r["short7"]:+.1f}%' if not np.isnan(r['short7']) else 'N/A'
        verdict = 'PASS' if (not np.isnan(r['ath_ratio']) and r['ath_ratio'] >= 0.90) else 'FILTER'
        print(f'  {r["date"]}  位势={ar}  持续={r["p_days"]}天  空头7D={s7}'
              f'  [{verdict}]  {key_dates[r["date"]]}')

# ── 结论 ─────────────────────────────────────────────────────────────────────
print()
print('=' * 82)
ath_90 = df_ev[df_ev['ath_ratio'] >= 0.90].dropna(subset=['short7'])
fake_passed = df_ev[(df_ev['ath_ratio'] >= 0.90) &
                    (df_ev['date'].isin(['2025-03-16','2025-03-18',
                                         '2025-04-04','2025-04-09','2025-04-11']))]
print(f'  位势>=0.90 保留 {len(ath_90)} 笔，其中假信号混入 {len(fake_passed)} 笔')
print(f'  2025-11-10 是否保留: {"YES" if any(df_ev[df_ev["ath_ratio"]>=0.90]["date"]=="2025-11-10") else "NO -- 被过滤"}')
print(f'  2026-01-14 是否保留: {"YES" if any(df_ev[df_ev["ath_ratio"]>=0.90]["date"]=="2026-01-14") else "NO -- 被过滤"}')
print('=' * 82)
