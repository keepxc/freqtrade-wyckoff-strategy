"""
TASK 21 前置诊断：早期窗口量价指纹
目标：在 persist_days < 30 的窗口内，检验 2025-03 假信号 vs 2025-11 真信号
      是否可以用量价证据区分

三项指标：
1. SOW 强度：close 偏离 EMA200 的幅度（以 ATR 倍数表示）
2. 放量属性：成交量 vs 60日均量（是否 >= 2x）
3. 努力/结果失衡：放量但价差极窄（供应被吸收）
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
df1d['ema200']     = df1d['close'].ewm(span=200, adjust=False).mean()
df1d['ema200_lag'] = df1d['ema200'].shift(1)
df1d['atr14']      = (df1d['high'] - df1d['low']).rolling(14).mean()
df1d['atr14_lag']  = df1d['atr14'].shift(1)
df1d['vol_ma60']   = df1d['volume'].rolling(60, min_periods=30).mean()
df1d['vol_ma60_lag'] = df1d['vol_ma60'].shift(1)

# persist 连续天数
df1d['below']       = df1d['close'] < df1d['ema200']
df1d['persist_raw'] = df1d['below'].rolling(7, min_periods=7).min().astype(bool)
df1d['persist_lag'] = df1d['persist_raw'].shift(1)

days_cnt = []
cnt = 0
for v in df1d['persist_raw']:
    cnt = cnt + 1 if v else 0
    days_cnt.append(cnt)
df1d['persist_days']     = days_cnt
df1d['persist_days_lag'] = df1d['persist_days'].shift(1).fillna(0)

# merge 到 1H
df1h = df1h.sort_values('date').reset_index(drop=True)
df1h = pd.merge_asof(df1h,
    df1d[['date','ema200_lag','persist_lag','persist_days_lag',
          'atr14_lag','vol_ma60_lag']].rename(columns={
        'ema200_lag':'ema200', 'persist_lag':'ema200_persist',
        'persist_days_lag':'persist_days',
        'atr14_lag':'atr14', 'vol_ma60_lag':'vol_ma60'}),
    on='date', direction='backward')
df1h['ema200_persist'] = df1h['ema200_persist'].fillna(False).astype(bool)
df1h['persist_days']   = df1h['persist_days'].fillna(0)

# ── 研究窗口：2024-01 至今，覆盖假/真信号全集 ──────────────────────────────
study = df1h[df1h['date'] >= '2024-01-01'].copy().reset_index(drop=True)

# ── 早期窗口触碰事件：persist_days IN [1, 29] + 冰线触碰失败 ──────────────
TOUCH_RATIO = 0.98
COOLDOWN_H  = 48

study['early_touch'] = (
    study['ema200_persist'] &
    (study['persist_days'] >= 1) &
    (study['persist_days'] < 30) &          # 早期窗口
    (study['high']  >= study['ema200'] * TOUCH_RATIO) &
    (study['close'] <  study['ema200']) &
    (study['close'] <  study['open'])        # 阴线
)

events = []
last_idx = -COOLDOWN_H * 2
for i, row in study.iterrows():
    if row['early_touch'] and (i - last_idx) >= COOLDOWN_H:
        events.append(i)
        last_idx = i

print('=' * 90)
print('  TASK 21 前置诊断 — 早期窗口（persist_days 1~29）量价指纹')
print('  目标：2025-03 假信号 vs 2025-11 真信号 是否可用量价区分')
print('=' * 90)
print(f'\n早期窗口触碰事件总数: {len(events)} 次\n')

# ── 逐事件量价指纹 ────────────────────────────────────────────────────────────
print(f'{"日期":12} {"价格":>8} {"EMA200":>8} {"偏离ATR":>8} {"量/60MA":>8} '
      f'{"价差/ATR":>9} {"持续天":>7} {"7D空头%":>9}  判定')
print('-' * 90)

records = []
for idx in events:
    row    = study.loc[idx]
    price  = row['close']
    ema200 = row['ema200']
    atr    = row['atr14'] if row['atr14'] > 0 else np.nan
    vol_ma = row['vol_ma60'] if row['vol_ma60'] > 0 else np.nan
    p_days = int(row['persist_days'])

    # 指标1：SOW强度 — close 偏离 EMA200（以ATR倍数）
    sow_atr = (ema200 - price) / atr if not np.isnan(atr) else np.nan

    # 指标2：放量属性 — 当前1H量 vs 60日均量（用1H近24根近似日量）
    window_start = max(0, idx - 24)
    daily_vol_approx = study.loc[window_start:idx, 'volume'].sum()
    vol_ratio = daily_vol_approx / vol_ma if not np.isnan(vol_ma) else np.nan

    # 指标3：努力/结果失衡 — K线价差（high-low）/ ATR（窄价差=供应被吸收）
    candle_range = row['high'] - row['low']
    range_atr = candle_range / atr if not np.isnan(atr) else np.nan

    # 7日后续（做空方向）
    fi = idx + 7 * 24
    short7 = (price / study.loc[fi, 'close'] - 1) * 100 if fi < len(study) else np.nan

    # 判定
    sow_strong  = (not np.isnan(sow_atr))  and sow_atr  >= 1.0   # 偏离>1ATR
    vol_panic   = (not np.isnan(vol_ratio)) and vol_ratio >= 2.0   # 放量>2x
    absorbed    = (not np.isnan(range_atr)) and range_atr <= 0.5   # 价差窄（被吸收）

    flags = []
    if sow_strong: flags.append('SOW强')
    if vol_panic:  flags.append('恐慌量')
    if absorbed:   flags.append('量价背离')
    verdict = '+'.join(flags) if flags else '—'

    records.append({
        'date': str(row['date'])[:10],
        'price': price, 'ema200': ema200,
        'sow_atr': sow_atr, 'vol_ratio': vol_ratio, 'range_atr': range_atr,
        'p_days': p_days, 'short7': short7,
        'sow_strong': sow_strong, 'vol_panic': vol_panic, 'absorbed': absorbed,
        'verdict': verdict,
    })

    s7s  = f'{short7:+.1f}%' if not np.isnan(short7) else 'N/A'
    sa   = f'{sow_atr:.2f}x'  if not np.isnan(sow_atr)  else 'N/A'
    vr   = f'{vol_ratio:.2f}x' if not np.isnan(vol_ratio) else 'N/A'
    rar  = f'{range_atr:.2f}x' if not np.isnan(range_atr) else 'N/A'
    print(f'{str(row["date"])[:10]:12} {price:>8,.0f} {ema200:>8,.0f} '
          f'{sa:>8} {vr:>8} {rar:>9} {p_days:>7} {s7s:>9}  {verdict}')

df_ev = pd.DataFrame(records)

# ── 关键日期对比 ──────────────────────────────────────────────────────────────
print()
print('=' * 90)
print('  核心对比：2025-03/04 假信号 vs 2025-11 真信号')
print('=' * 90)

key = {
    '2025-03': '假（牛市深调）',
    '2025-04': '假（牛市深调）',
    '2025-11': '真（ATH崩盘）',
}
for _, r in df_ev.iterrows():
    mo = r['date'][:7]
    if mo in key:
        s7s = f'{r["short7"]:+.1f}%' if not np.isnan(r['short7']) else 'N/A'
        sa  = f'{r["sow_atr"]:.2f}x'  if not np.isnan(r['sow_atr'])  else 'N/A'
        vr  = f'{r["vol_ratio"]:.2f}x' if not np.isnan(r['vol_ratio']) else 'N/A'
        rar = f'{r["range_atr"]:.2f}x' if not np.isnan(r['range_atr']) else 'N/A'
        print(f'  {r["date"]}  [{key[mo]}]')
        print(f'    偏离ATR={sa}  量/60MA={vr}  价差/ATR={rar}  '
              f'持续={r["p_days"]}天  7D空头={s7s}  指纹={r["verdict"]}')

# ── 区分度统计 ────────────────────────────────────────────────────────────────
print()
print('=' * 90)
print('  量价门槛 vs 过滤效果（核心决策表）')
print('=' * 90)
print()

# 标记假/真信号
df_ev['is_false'] = df_ev['date'].str[:7].isin(['2025-03','2025-04'])
df_ev['is_true']  = df_ev['date'].str[:7].isin(['2025-11'])
false_ev = df_ev[df_ev['is_false']]
true_ev  = df_ev[df_ev['is_true']]

print(f'假信号({len(false_ev)}笔) vs 真信号({len(true_ev)}笔)\n')

for label, cond_fn in [
    ('SOW偏离 >= 1.0 ATR',  lambda r: r['sow_atr']  >= 1.0 if not np.isnan(r['sow_atr'])  else False),
    ('SOW偏离 >= 0.5 ATR',  lambda r: r['sow_atr']  >= 0.5 if not np.isnan(r['sow_atr'])  else False),
    ('量/60MA >= 2.0x',     lambda r: r['vol_ratio'] >= 2.0 if not np.isnan(r['vol_ratio']) else False),
    ('量/60MA >= 1.5x',     lambda r: r['vol_ratio'] >= 1.5 if not np.isnan(r['vol_ratio']) else False),
    ('价差/ATR <= 0.5x',    lambda r: r['range_atr'] <= 0.5 if not np.isnan(r['range_atr']) else False),
    ('SOW>=0.5 AND 量>=1.5',lambda r: (r['sow_atr']>=0.5 and r['vol_ratio']>=1.5)
                                        if not (np.isnan(r['sow_atr']) or np.isnan(r['vol_ratio'])) else False),
]:
    false_pass = false_ev.apply(cond_fn, axis=1).sum()
    true_pass  = true_ev.apply(cond_fn,  axis=1).sum()
    print(f'  [{label}]  假信号通过: {false_pass}/{len(false_ev)}  真信号通过: {true_pass}/{len(true_ev)}  '
          f'{"✅ 可区分" if false_pass == 0 and true_pass > 0 else "❌ 无法区分" if false_pass > 0 and true_pass > 0 else "⚠️  两者均0" if false_pass == 0 and true_pass == 0 else "❌ 误杀真信号"}')

print()
print('=' * 90)
if len(false_ev) == 0:
    print('  ⚠️  早期窗口内无假信号触发 — 量价条件本身已有选择性，TASK 21 值得深入')
elif false_ev['sow_strong'].any() or false_ev['vol_panic'].any():
    print('  结论：2025-03 假信号同样触发强量价指纹 → 30天硬防御不可逾越，TASK 21 撤项')
else:
    print('  结论：2025-03 假信号量价指纹偏弱 → 存在区分空间，TASK 21 可进一步设计')
print('=' * 90)
