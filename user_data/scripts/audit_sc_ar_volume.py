"""
审计：SC 确认后 AR 阶段的量价特征
分析历史上每次 SC 事件的 AR 反弹质量：
1. AR 反弹成交量 vs SC 成交量比值
2. AR 反弹速度（天数）
3. AR 之后是否成功进入 Phase D/E（真积累 vs 失败）
4. 量价特征能否区分两者
"""
import sys
sys.path.insert(0, '/freqtrade')

import pandas as pd
import numpy as np
from pathlib import Path
from user_data.strategies.v8_modules.sc_detector import add_v8_sc_detection
from user_data.strategies.v8_modules.phase_detector import add_v8_phase_detection

DATA_PATH = Path('/freqtrade/user_data/data/binance/futures')
df = pd.read_feather(DATA_PATH / 'BTC_USDT_USDT-1d-futures.feather')
df = df[df['date'] >= '2018-01-01'].copy().reset_index(drop=True)
print(f'日线数据: {len(df)} 根 ({df["date"].iloc[0].date()} ~ {df["date"].iloc[-1].date()})')

df = add_v8_sc_detection(df)
df = add_v8_phase_detection(df)

n = len(df)
sc_final = df['v8_sc_final'].values
phases   = df['v8_phase'].values
closes   = df['close'].values
highs    = df['high'].values
lows     = df['low'].values
vols     = df['volume'].values
dates    = df['date'].values
tr_highs = df['v8_tr_high'].values
tr_lows  = df['v8_tr_low'].values

# ── 找每次 SC 事件，统计后续 AR 和结构 ──
results = []

for i in range(n):
    if not sc_final[i]:
        continue

    sc_date  = pd.Timestamp(dates[i]).date()
    sc_close = closes[i]
    sc_vol   = vols[i]
    sc_low   = lows[i]

    # SC 前 5 根成交量均值（背景量）
    bg_vol = vols[max(0, i-20):i].mean() if i >= 5 else np.nan

    # 向后找 AR 完成（Phase B 开始）
    ar_complete_idx  = -1
    ar_high_val      = np.nan
    ar_vol_sum       = 0.0
    ar_vol_max       = 0.0
    ar_days          = 0

    for j in range(i+1, min(i+16, n)):
        if phases[j] == 'B':
            ar_complete_idx = j
            # AR 区间：i+1 到 j
            ar_slice_high = highs[i+1:j+1]
            ar_slice_vol  = vols[i+1:j+1]
            ar_high_val   = ar_slice_high.max() if len(ar_slice_high) > 0 else np.nan
            ar_vol_sum    = ar_slice_vol.sum()
            ar_vol_max    = ar_slice_vol.max()
            ar_days       = j - i
            break

    if ar_complete_idx < 0:
        # AR 未完成（SC 超时或数据不足）
        results.append({
            'sc_date': sc_date, 'sc_close': sc_close, 'sc_vol': sc_vol,
            'bg_vol': bg_vol, 'sc_vol_ratio': sc_vol / bg_vol if bg_vol > 0 else np.nan,
            'ar_complete': False, 'ar_days': None, 'ar_gain_pct': None,
            'ar_vol_ratio': None, 'ar_vol_max_ratio': None,
            'outcome': 'AR_TIMEOUT',
        })
        continue

    ar_gain_pct = (ar_high_val - sc_close) / sc_close * 100 if not np.isnan(ar_high_val) else np.nan
    ar_vol_ratio = ar_vol_max / sc_vol if sc_vol > 0 else np.nan  # AR 最大单日量 / SC 量
    ar_avg_vol_ratio = (ar_vol_sum / ar_days) / sc_vol if ar_days > 0 and sc_vol > 0 else np.nan

    # 向后找结构结果：Phase B 之后进入 D/E 还是失败
    outcome = 'PENDING'
    phase_b_start = ar_complete_idx
    max_look = min(ar_complete_idx + 120, n)

    reached_de   = False
    de_first_idx = -1
    db_retreat   = False  # D→B 回退

    for k in range(ar_complete_idx, max_look):
        p = phases[k]
        if p in ('D', 'E') and not reached_de:
            reached_de = True
            de_first_idx = k
        if reached_de and p == 'B':
            db_retreat = True

    if reached_de and not db_retreat:
        outcome = 'SUCCESS_DE'      # 进入 D/E 且未回退
    elif reached_de and db_retreat:
        outcome = 'PARTIAL_DE'      # 进 D/E 但又退回 B
    elif not reached_de:
        outcome = 'STUCK_B'         # 一直卡在 B，未进 D/E

    # 特殊情况：Phase B 后直接出现新 SC（结构废弃）
    for k in range(ar_complete_idx, max_look):
        if sc_final[k] and k > ar_complete_idx + 5:
            outcome = 'NEW_SC'
            break

    results.append({
        'sc_date': sc_date,
        'sc_close': sc_close,
        'sc_vol': sc_vol,
        'bg_vol': bg_vol,
        'sc_vol_ratio': sc_vol / bg_vol if bg_vol > 0 else np.nan,
        'ar_complete': True,
        'ar_days': ar_days,
        'ar_gain_pct': ar_gain_pct,
        'ar_vol_ratio': ar_vol_ratio,           # AR 峰值量 / SC 量
        'ar_avg_vol_ratio': ar_avg_vol_ratio,   # AR 均量 / SC 量
        'outcome': outcome,
    })

# ── 输出 ──
df_r = pd.DataFrame(results)

print(f'\n=== SC 事件统计（{len(df_r)} 次）===')
print(df_r.groupby('outcome').size().to_string())

print('\n=== 各 SC 事件明细 ===')
print(f'{"SC日期":<12} {"SC价格":>9} {"SC量倍":>7} {"AR天数":>7} {"AR涨幅":>9} '
      f'{"AR峰量/SC量":>11} {"结果":<14}')
print('-'*80)
for _, r in df_r.iterrows():
    if not r['ar_complete']:
        print(f'{str(r["sc_date"]):<12} {r["sc_close"]:>9,.0f} '
              f'{r["sc_vol_ratio"]:>7.1f}x  {"N/A":>6}  {"N/A":>8}  {"N/A":>10}  {r["outcome"]}')
    else:
        print(f'{str(r["sc_date"]):<12} {r["sc_close"]:>9,.0f} '
              f'{r["sc_vol_ratio"]:>7.1f}x  {r["ar_days"]:>6}天  '
              f'{r["ar_gain_pct"]:>+8.1f}%  {r["ar_vol_ratio"]:>10.2f}x  {r["outcome"]}')

# ── 量价特征对比：成功 vs 失败 ──
success = df_r[df_r['outcome'].isin(['SUCCESS_DE', 'PARTIAL_DE'])]
failure = df_r[df_r['outcome'].isin(['STUCK_B', 'NEW_SC', 'AR_TIMEOUT'])]

print('\n=== 量价特征对比：成功进入 D/E vs 失败 ===')
print(f'{"指标":<25} {"成功组":>10} {"失败组":>10}')
print('-'*48)

def safe_mean(series):
    s = series.dropna()
    return f'{s.mean():.2f}' if len(s) > 0 else 'N/A'

for col, label in [
    ('sc_vol_ratio',     'SC成交量/背景量'),
    ('ar_days',          'AR完成天数'),
    ('ar_gain_pct',      'AR涨幅%'),
    ('ar_vol_ratio',     'AR峰值量/SC量'),
    ('ar_avg_vol_ratio', 'AR均量/SC量'),
]:
    print(f'{label:<25} {safe_mean(success[col]):>10} {safe_mean(failure[col]):>10}')

print()
print('★ AR峰值量/SC量 < 1.0 = AR 反弹量缩（量价协调，空头不再积极）')
print('★ AR峰值量/SC量 > 1.0 = AR 反弹放量（可能是逼空，需谨慎）')
