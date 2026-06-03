"""
空头入场漏斗 第二层审计 — 2022年零空单根因追查

在日线层逐层检查每个做空前置条件，找出2022年的真实阻断点。

运行：
  docker compose run --rm --entrypoint "" freqtrade \
    sh -c "python user_data/scripts/audit_short_funnel2.py"
"""

import sys
sys.path.insert(0, '/freqtrade')

import pandas as pd
import numpy as np
from wyckoff.daily_event_detector import DailyEventDetector
from user_data.strategies.v8_modules.pnf_calculator import add_v8_short_pnf_targets

# ─── 加载日线数据 ─────────────────────────────────────────────────────────────
df_raw = pd.read_feather('user_data/data/binance/futures/BTC_USDT_USDT-1d-futures.feather')
df_raw['date'] = pd.to_datetime(df_raw['date'])
df_raw = df_raw.sort_values('date').reset_index(drop=True)

# 含预热期
df_raw = df_raw[df_raw['date'] >= '2021-01-01'].reset_index(drop=True)

# ─── 运行检测器 ──────────────────────────────────────────────────────────────
detector = DailyEventDetector()
df = detector.detect(df_raw.copy())

# 运行 PNF 空头目标位
df = add_v8_short_pnf_targets(df)

# ─── EMA200 计算（detector 内部用，这里显式加一列方便审计）────────────────────
df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()

# ─── 模拟 sm_v2 和 ema200_persist（从策略代码逆推近似条件）────────────────────
# sm_v2：收于EMA200下方 + volume放量（简化）
df['below_ema200'] = df['close'] < df['ema200']
# ema200_persist：连续7根以上收于EMA200下方（TASK14实现）
ema_below_streak = (df['below_ema200'].astype(int)
                    .groupby((~df['below_ema200']).cumsum())
                    .cumcount() + 1)
ema_below_streak[~df['below_ema200']] = 0
df['ema200_persist_approx'] = ema_below_streak >= 7

# ─── 合成 _bear_gate（日线近似）──────────────────────────────────────────────
df['bear_gate'] = (
    df['dist_cause_sufficient'] |
    df['ema200_persist_approx']
)

# ─── pnf_valid_short ─────────────────────────────────────────────────────────
df['pnf_valid_short'] = df['target_short_conservative'].fillna(0) > 0

# ─── short_intent_ok：composite_intent 在 V8 中始终是 'unclear' → True ────────
df['short_intent_ok'] = True  # composite_intent 不在 V8 中计算，默认 unclear

# ─── BE entry 条件链 ─────────────────────────────────────────────────────────
df['be_gate_ok'] = (
    df['buying_exhaustion'] &
    df['bear_gate'] &
    df['pnf_valid_short'] &
    df['short_intent_ok']
)

# ─── SOW/fallback_sow 简化（需 sm_v2，此处用 ema200_persist 近似）─────────────
vol_ma20 = df['volume'].rolling(20, min_periods=10).mean()
low_20d  = df['low'].rolling(20, min_periods=10).min().shift(1)
df['fallback_sow_approx'] = (
    df['ema200_persist_approx'] &
    (df['close'] < df['open']) &
    ((df['open'] - df['close']) / df['open'].replace(0, np.nan) >= 0.02) &
    (df['volume'] > vol_ma20 * 1.5) &
    (df['close'] < low_20d)
)
df['sow_gate_ok'] = (
    (df['daily_dist_sow_detected'] | df['fallback_sow_approx']) &
    df['bear_gate'] &
    df['pnf_valid_short'] &
    df['short_intent_ok']
)

# rr_short_ok（SOW 还需要 RR >= 1.0，这里只检查是否满足）
df['rr_short_ok'] = df['rr_short_standard'].fillna(0) >= 1.0

# ─── 分析区间 ────────────────────────────────────────────────────────────────
df_a = df[df['date'] >= '2022-01-01'].copy()

print("=" * 70)
print("空头入场漏斗审计 — 日线层逐门检查")
print("=" * 70)

def sec(title):
    print()
    print(f"{'─'*3} {title} {'─'*(60-len(title))}")

# ─────────────────────────────────────────────────────────────────────────────
sec("BE 路径：2022年触发详情")

be_dates = df_a[df_a['buying_exhaustion']]['date'].dt.date.tolist()
print(f"BE 触发日期（共 {len(be_dates)} 次）: {be_dates}")

be_gate_dates = df_a[df_a['be_gate_ok']]['date'].dt.date.tolist()
print(f"BE 全条件满足（共 {len(be_gate_dates)} 次）: {be_gate_dates}")

print()
print("BE 触发日期 逐条件拆解：")
print(f"  {'日期':12s} {'BE':>5} {'DCS':>5} {'pnf_ok':>7} {'bear_gate':>10} {'rr':>8} {'target_cons':>12} {'close':>8}")
for _, row in df_a[df_a['buying_exhaustion']].iterrows():
    print(f"  {str(row['date'].date()):12s} "
          f"  {'✓':>3}  "
          f"{'✓' if row['dist_cause_sufficient'] else '✗':>5}  "
          f"{'✓' if row['pnf_valid_short'] else '✗':>5}  "
          f"{'✓' if row['bear_gate'] else '✗':>8}  "
          f"{row['rr_short_standard']:>6.2f}  "
          f"{row['target_short_conservative']:>10.0f}  "
          f"{row['close']:>8.0f}")

# ─────────────────────────────────────────────────────────────────────────────
sec("SOW/fallback 路径：2022年触发详情")

sow_dates = df_a[df_a['daily_dist_sow_detected']]['date'].dt.date.tolist()
print(f"经典 SOW 触发: {sow_dates}")

fb_dates = df_a[df_a['fallback_sow_approx']]['date'].dt.date.tolist()
print(f"fallback SOW（近似）: {fb_dates}")

sow_gate_dates = df_a[df_a['sow_gate_ok']]['date'].dt.date.tolist()
print(f"SOW 全条件满足: {sow_gate_dates}")

# ─────────────────────────────────────────────────────────────────────────────
sec("2022年 DCS + BE + 各门控逐月汇总")

print(f"  {'月份':8} {'DCS天':>7} {'BE触发':>7} {'BE门ok':>7} {'pnf_ok':>7} {'rr_ok':>7}")
for month in range(1, 13):
    mask = (df_a['date'].dt.year == 2022) & (df_a['date'].dt.month == month)
    sub = df_a[mask]
    if len(sub) == 0:
        continue
    print(f"  2022-{month:02d}  "
          f"{sub['dist_cause_sufficient'].sum():>6}  "
          f"{sub['buying_exhaustion'].sum():>6}  "
          f"{sub['be_gate_ok'].sum():>6}  "
          f"{sub['pnf_valid_short'].sum():>6}  "
          f"{sub['rr_short_ok'].sum():>6}")

# ─────────────────────────────────────────────────────────────────────────────
sec("目标价合理性检查（BE触发日 vs 当时价格）")

for _, row in df_a[df_a['buying_exhaustion']].iterrows():
    tsc = row['target_short_conservative']
    close = row['close']
    ema200 = row['ema200']
    print(f"  {str(row['date'].date()):12s}  "
          f"close={close:.0f}  EMA200={ema200:.0f}  target_cons={tsc:.0f}  "
          f"差距={close-tsc:.0f}  {'目标在下方✓' if tsc < close else '目标在上方✗(无效)'}")

# ─────────────────────────────────────────────────────────────────────────────
sec("结论汇总")

total = len(df_a)
print(f"  2022-2026 总日线数: {total}")
print(f"  DCS=True: {df_a['dist_cause_sufficient'].sum()} 天")
print(f"  BE 触发:  {df_a['buying_exhaustion'].sum()} 次")
print(f"  BE 全门控 OK（日线层）: {df_a['be_gate_ok'].sum()} 次")
print(f"  SOW 全门控 OK（日线层）: {df_a['sow_gate_ok'].sum()} 次")

print()
print("注：1H 入场还需要 1H wave_start 等信号，以上只是日线层条件是否满足。")
print("    若日线层全OK但仍无空单，则阻断点在 1H 层（wave_start/swing_high/ChoCh）。")
print("=" * 70)
