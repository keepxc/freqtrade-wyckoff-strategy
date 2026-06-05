"""
TASK 22 前置诊断：再派发（Redistribution）结构识别
目标：下跌途中的横盘"小因"是否能提前定位 ice_lpsy 无法捕获的入场点

三项结构指纹：
1. Lower Highs 序列：连续 swing high 递降（N 根日线内高点<前高）
2. 横盘区间：价格在一个狭窄区间内震荡（range_width < ATR*N）
3. 需求匮乏：区间内上涨日（close>open）的成交量 < 20日均量

判定标准：
- 候选再派发：满足上述三项
- 真信号：事件后 20 日内价格继续下跌 >3%（空头方向正确）
- 假信号：事件后 20 日内价格上涨 >5%（V型反转）
"""
import sys
sys.path.insert(0, '/freqtrade')
import pandas as pd
import numpy as np

# ── 数据准备 ─────────────────────────────────────────────────────────────────
df1d = pd.read_feather('user_data/data/binance/futures/BTC_USDT_USDT-1d-futures.feather')
df1d['date'] = pd.to_datetime(df1d['date'], utc=True)
df1d = df1d.sort_values('date').reset_index(drop=True)

# 基础指标
df1d['ema200']   = df1d['close'].ewm(span=200, adjust=False).mean()
df1d['atr14']    = (df1d['high'] - df1d['low']).rolling(14).mean()
df1d['vol_ma20'] = df1d['volume'].rolling(20, min_periods=10).mean()
df1d['below_ema'] = df1d['close'] < df1d['ema200']

# ema200_persist：连续 close < ema200 天数
days_cnt = []
cnt = 0
for v in df1d['below_ema']:
    cnt = cnt + 1 if v else 0
    days_cnt.append(cnt)
df1d['persist_days'] = days_cnt

# ── 参数 ────────────────────────────────────────────────────────────────────
CONSOLIDATION_DAYS  = 7    # 横盘窗口（日线根数）
RANGE_ATR_THRESHOLD = 2.5  # 横盘宽度 <= N x ATR14
LOWER_HIGH_BARS     = 3    # 窗口内至少有 N 个局部高点且递降
NO_DEMAND_RATIO     = 0.85 # 上涨日量 < vol_ma20 * ratio（需求匮乏）
MIN_UP_DAYS         = 2    # 窗口内至少有 N 个上涨日才检验需求匮乏
MIN_PERSIST_DAYS    = 20   # 确认税：EMA200 以下连续天数门槛
STUDY_START         = '2022-01-01'

study = df1d[df1d['date'] >= STUDY_START].copy().reset_index(drop=True)

print('=' * 100)
print('  TASK 22 前置诊断 — 再派发（Redistribution）结构识别')
print(f'  参数：窗口={CONSOLIDATION_DAYS}日, 区间宽度<={RANGE_ATR_THRESHOLD}xATR, '
      f'上涨日量<={NO_DEMAND_RATIO}xVol_MA20, persist>={MIN_PERSIST_DAYS}天')
print('=' * 100)

# ── 滑动窗口扫描 ─────────────────────────────────────────────────────────────
events = []
COOLDOWN = 14  # 同一结构最短间隔天数（避免重复计数）
last_event_i = -COOLDOWN * 2

for i in range(CONSOLIDATION_DAYS, len(study) - 20):
    row = study.loc[i]

    # 前置条件：当前 close < EMA200 且 persist_days >= MIN_PERSIST_DAYS
    if not row['below_ema']:
        continue
    if row['persist_days'] < MIN_PERSIST_DAYS:
        continue
    if (i - last_event_i) < COOLDOWN:
        continue

    # 取窗口
    window = study.loc[i - CONSOLIDATION_DAYS + 1: i]
    if len(window) < CONSOLIDATION_DAYS:
        continue

    atr = row['atr14']
    if np.isnan(atr) or atr <= 0:
        continue

    # ── 条件1：横盘区间宽度 ─────────────────────────────────────────────────
    w_high = window['high'].max()
    w_low  = window['low'].min()
    range_width = w_high - w_low
    range_atr   = range_width / atr
    if range_atr > RANGE_ATR_THRESHOLD:
        continue

    # ── 条件2：Lower Highs 序列 ─────────────────────────────────────────────
    # 简化：取窗口前半段最高点 vs 后半段最高点，后者必须更低
    mid = CONSOLIDATION_DAYS // 2
    first_half_high  = window.iloc[:mid]['high'].max()
    second_half_high = window.iloc[mid:]['high'].max()
    if second_half_high >= first_half_high:
        continue
    lower_high_drop = (first_half_high - second_half_high) / atr

    # ── 条件3：需求匮乏（上涨日成交量萎缩）──────────────────────────────────
    up_days = window[window['close'] > window['open']]
    vol_ma  = row['vol_ma20']
    if np.isnan(vol_ma) or vol_ma <= 0:
        continue
    if len(up_days) < MIN_UP_DAYS:
        # 上涨日太少，无法判断需求——直接视为"需求本身就不存在"，视同满足
        no_demand_ok = True
        avg_up_vol_ratio = np.nan
    else:
        avg_up_vol = up_days['volume'].mean()
        avg_up_vol_ratio = avg_up_vol / vol_ma
        no_demand_ok = (avg_up_vol_ratio <= NO_DEMAND_RATIO)

    if not no_demand_ok:
        continue

    # ── 事件指标 ─────────────────────────────────────────────────────────────
    entry_price = row['close']
    persist     = int(row['persist_days'])
    ema200_val  = row['ema200']

    # 后续 14 / 30 日表现
    f14 = i + 14
    f30 = i + 30
    ret14 = (entry_price / study.loc[f14, 'close'] - 1) * 100 if f14 < len(study) else np.nan
    ret30 = (entry_price / study.loc[f30, 'close'] - 1) * 100 if f30 < len(study) else np.nan

    events.append({
        'date':        str(row['date'])[:10],
        'price':       entry_price,
        'ema200':      ema200_val,
        'persist':     persist,
        'range_atr':   range_atr,
        'lh_drop_atr': lower_high_drop,
        'up_vol_ratio': avg_up_vol_ratio,
        'ret14':       ret14,
        'ret30':       ret30,
    })
    last_event_i = i

# ── 结果表格 ──────────────────────────────────────────────────────────────────
print(f'\n识别到再派发候选事件: {len(events)} 笔\n')
print(f'{"日期":12} {"价格":>9} {"EMA200":>9} {"持续天":>7} {"区间ATR":>8} '
      f'{"LH跌幅ATR":>10} {"上涨日量/MA":>11} {"14D空头%":>10} {"30D空头%":>10}  判定')
print('-' * 100)

for ev in events:
    r14 = f'{ev["ret14"]:+.1f}%' if not np.isnan(ev["ret14"]) else 'N/A'
    r30 = f'{ev["ret30"]:+.1f}%' if not np.isnan(ev["ret30"]) else 'N/A'
    uvr = f'{ev["up_vol_ratio"]:.2f}x' if not np.isnan(ev["up_vol_ratio"]) else '(无上涨日)'

    is_true  = not np.isnan(ev["ret14"]) and ev["ret14"] > 3
    is_false = not np.isnan(ev["ret14"]) and ev["ret14"] < -5
    verdict  = '✅ 真空头' if is_true else ('❌ V型反弹' if is_false else '— 中性')

    print(f'{ev["date"]:12} {ev["price"]:>9,.0f} {ev["ema200"]:>9,.0f} '
          f'{ev["persist"]:>7} {ev["range_atr"]:>8.2f}x {ev["lh_drop_atr"]:>10.2f}x '
          f'{uvr:>11} {r14:>10} {r30:>10}  {verdict}')

# ── 分期统计 ──────────────────────────────────────────────────────────────────
df_ev = pd.DataFrame(events)
if len(df_ev) == 0:
    print('\n⚠️  无事件，调整参数后重试')
    exit()

df_ev['year_mo'] = df_ev['date'].str[:7]
df_ev['is_bear'] = df_ev['date'].apply(lambda d: d >= '2022-01-01' and d <= '2022-12-31' or d >= '2025-11-01')
df_ev['is_bull_correction'] = df_ev['date'].apply(lambda d: '2024-01' <= d[:7] <= '2025-10')

print('\n')
print('=' * 100)
print('  分期判定（核心区分度）')
print('=' * 100)

for label, mask in [
    ('2022 熊市全年',          df_ev['date'].str[:4] == '2022'),
    ('2024-01~2025-10 牛市区间', df_ev['is_bull_correction']),
    ('2025-11 至今 ATH后崩跌', df_ev['date'] >= '2025-11-01'),
    ('2025-03/04 假跌破期',    df_ev['date'].str[:7].isin(['2025-03','2025-04'])),
]:
    sub = df_ev[mask]
    if len(sub) == 0:
        print(f'  {label:30s}: 0 笔')
        continue
    valid_ret14 = sub['ret14'].dropna()
    true_cnt  = (valid_ret14 > 3).sum()
    false_cnt = (valid_ret14 < -5).sum()
    avg_ret14 = valid_ret14.mean()
    print(f'  {label:30s}: {len(sub):2d}笔  真空头(>+3%): {true_cnt}  V型(>-5%): {false_cnt}  '
          f'平均14D={avg_ret14:+.1f}%')

# ── 关键问题：2025-03/04 假跌破是否触发 ─────────────────────────────────────
print('\n')
print('=' * 100)
print('  关键验证：2025-03 / 2025-04 牛市深调期 是否产生再派发假信号')
print('=' * 100)

false_period = df_ev[df_ev['date'].str[:7].isin(['2025-03','2025-04'])]
if len(false_period) == 0:
    print('\n  ✅ 2025-03/04 期间：0 个再派发结构触发 → 结构性过滤有效，TASK 22 值得深入！')
else:
    print(f'\n  ❌ 2025-03/04 期间：{len(false_period)} 个结构触发：')
    for _, r in false_period.iterrows():
        r14 = f'{r["ret14"]:+.1f}%' if not np.isnan(r["ret14"]) else 'N/A'
        print(f'    {r["date"]}  price={r["price"]:,.0f}  persist={r["persist"]}天  14D={r14}')
    print('\n  → 存在假信号，需要额外过滤条件')

# ── 当前 Dry Run 盲区：2025-11 至今 ─────────────────────────────────────────
print('\n')
print('=' * 100)
print('  当前盲区：2025-11 至今，再派发结构分布')
print('=' * 100)
recent = df_ev[df_ev['date'] >= '2025-11-01']
if len(recent) == 0:
    print('\n  ⚠️  2025-11 至今：0 笔再派发结构 → 参数过严，考虑放宽 RANGE_ATR_THRESHOLD')
else:
    for _, r in recent.iterrows():
        r14 = f'{r["ret14"]:+.1f}%' if not np.isnan(r["ret14"]) else 'N/A'
        r30 = f'{r["ret30"]:+.1f}%' if not np.isnan(r["ret30"]) else 'N/A'
        uvr = f'{r["up_vol_ratio"]:.2f}x' if not np.isnan(r["up_vol_ratio"]) else '(无上涨日)'
        print(f'  {r["date"]}  price={r["price"]:,.0f}  persist={r["persist"]}天  '
              f'区间={r["range_atr"]:.2f}xATR  上涨日量={uvr}  14D={r14}  30D={r30}')

print('\n')
print('=' * 100)
print('  TASK 22 诊断结论')
print('=' * 100)
if len(false_period) == 0 and len(recent) > 0:
    print('  ✅ 通过：再派发结构能识别 ATH 后盲区，且不误触发牛市深调 → 立项 TASK 22')
elif len(false_period) == 0 and len(recent) == 0:
    print('  ⚠️  中性：无假信号，但 2025-11 后也无结构 → 参数调整后再判断')
else:
    print(f'  ❌ 有风险：{len(false_period)}个假信号需处理 → 追加过滤条件后再立项')
print('=' * 100)
