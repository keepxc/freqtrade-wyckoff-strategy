"""
§3c Phase 检测预研 — TV 指标 vs V8 入场交叉验证
===============================================
1. 复现 TradingView "Wyckoff Accumulation Distribution" (faytterro, Pine v5)
   机制：RSI(14) + pivot high/low → Accumulation/Distribution boxes + SC/AR/ST/BC/DAR/DST
2. 对比 V8 回测入场时间戳（enter_tag → Phase 近似）
3. 输出：
   - 一致率报告（两套标签同意/不同意的分布）
   - 高置信度 Phase 标签 → phase_labels_crossval.parquet（§3c 训练数据基础）
"""
import zipfile, json
import numpy as np
import pandas as pd

BT_ZIP    = 'user_data/backtest_results/backtest-result-2026-06-26_00-42-22.zip'
DATA_FILE = 'user_data/data/binance/futures/BTC_USDT_USDT-1h-futures.feather'
OUT_FILE  = 'user_data/scripts/phase_labels_crossval.parquet'

RSI_LEN   = 14
RSI_SENS  = 20   # Pine 默认：bull>70, bear<30
PIVOT_LEN = 5    # Pine 默认 pivotLength=5

RSI_HIGH  = 50 + RSI_SENS   # 70
RSI_LOW   = 50 - RSI_SENS   # 30

# ── 加载数据 ─────────────────────────────────────────────────────────────────
def load_trades(zf_path):
    with zipfile.ZipFile(zf_path) as z:
        fname = [n for n in z.namelist()
                 if n.endswith('.json') and 'config' not in n and '.py' not in n][0]
        data = json.loads(z.read(fname))
    return list(data['strategy'].values())[0]['trades']

trades = load_trades(BT_ZIP)
print(f'V8 总交易笔数: {len(trades)}')
for tag in ['v8_ws_e', 'v8_ws_d', 'v8_short_wsdown']:
    n = sum(1 for t in trades if t.get('enter_tag') == tag)
    print(f'  {tag}: {n}')

ohlcv = pd.read_feather(DATA_FILE)
ohlcv['date'] = pd.to_datetime(ohlcv['date'], utc=True)
ohlcv = ohlcv.sort_values('date').reset_index(drop=True)
print(f'\n1H K线: {len(ohlcv)} 根  {ohlcv["date"].iloc[0]} ~ {ohlcv["date"].iloc[-1]}')

# ── RSI（Wilder EMA，与 Pine 对齐）────────────────────────────────────────────
def calc_rsi(close, length):
    delta = close.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_g = gain.ewm(alpha=1.0 / length, adjust=False).mean()
    avg_l = loss.ewm(alpha=1.0 / length, adjust=False).mean()
    rs = avg_g / avg_l.replace(0, np.nan)
    return 100 - 100 / (1 + rs)

ohlcv['rsi'] = calc_rsi(ohlcv['close'], RSI_LEN)

rsi   = ohlcv['rsi']
rsi_p = rsi.shift(1)

# RSI 状态（Pine 的 bull/bear/side 定义）
ohlcv['tv_bull'] = (rsi > RSI_HIGH) & (rsi_p > RSI_HIGH)   # 两根都 >70
ohlcv['tv_bear'] = (rsi < RSI_LOW)  & (rsi_p < RSI_LOW)    # 两根都 <30
ohlcv['tv_side'] = ((rsi < RSI_HIGH) & (rsi > RSI_LOW)) | \
                   ((rsi_p < RSI_HIGH) & (rsi_p > RSI_LOW))

# Spring / UTAD（RSI 穿越）
ohlcv['utad']   = (rsi < RSI_HIGH) & (rsi_p >= RSI_HIGH)
ohlcv['spring'] = (rsi > RSI_LOW)  & (rsi_p <= RSI_LOW)

# ── Pivot High/Low（离线，允许向后看 PIVOT_LEN 根确认，标记在实际转折点）────────
# Pine: ta.pivotlow(low, pivotLen, pivotLen) → pivot 在 bar[i-pivotLen] 处，
#       offset=-pivotLen 把标注画回到实际转折点
# 离线标注：可以用两侧信息，训练时的 lookahead 在结构级别标签上可接受
print('计算 Pivot High/Low（约 2-3 秒）...')

def find_pivot_lows(low_series, n):
    """local minimum：low[i] <= all low[i-n:i+n+1]"""
    arr = low_series.values
    res = np.zeros(len(arr), dtype=bool)
    for i in range(n, len(arr) - n):
        if arr[i] <= arr[i-n:i+n+1].min():
            res[i] = True
    return pd.Series(res, index=low_series.index)

def find_pivot_highs(high_series, n):
    """local maximum：high[i] >= all high[i-n:i+n+1]"""
    arr = high_series.values
    res = np.zeros(len(arr), dtype=bool)
    for i in range(n, len(arr) - n):
        if arr[i] >= arr[i-n:i+n+1].max():
            res[i] = True
    return pd.Series(res, index=high_series.index)

ohlcv['pivot_low']  = find_pivot_lows(ohlcv['low'],   PIVOT_LEN)
ohlcv['pivot_high'] = find_pivot_highs(ohlcv['high'], PIVOT_LEN)

print(f'  Pivot Lows:  {ohlcv["pivot_low"].sum()}')
print(f'  Pivot Highs: {ohlcv["pivot_high"].sum()}')

# ── SC / AR / ST / BC / DAR / DST 状态机 ─────────────────────────────────────
# 对应 Pine Script 逐 bar 状态机，但 flag reset 每 bar 都发生（即无防重复保护）
sc_idx_list   = []
ar_idx_list   = []
st_idx_list   = []
bc_idx_list   = []
dar_idx_list  = []
dst_idx_list  = []

sc_bar   = None    # 最近一个 SC 的 bar index（用于 AR 检测）
bc_bar   = None    # 最近一个 BC 的 bar index（用于 DAR 检测）
dar_bar  = None    # 最近一个 DAR 的 bar index（用于 DST 检测）
pv_cnt   = 0       # pivotCounter（Pine 的 accumulation ST 计数）

pivot_low  = ohlcv['pivot_low'].values
pivot_high = ohlcv['pivot_high'].values
rsi_vals   = ohlcv['rsi'].values
spring_v   = ohlcv['spring'].values
utad_v     = ohlcv['utad'].values

for i in range(len(ohlcv)):
    is_pl = bool(pivot_low[i])
    is_ph = bool(pivot_high[i])
    rsi_i = rsi_vals[i]

    # SC
    if is_pl and rsi_i < RSI_LOW and not spring_v[i]:
        sc_idx_list.append(i)
        sc_bar  = i
        pv_cnt  = 0

    # AR（积累端：SC 之后第一个 pivot high）
    if sc_bar is not None and is_ph and i > sc_bar:
        ar_idx_list.append(i)
        sc_bar = None

    # ST 积累端：pivotCounter == 1（SC 后第一个非 bear-RSI pivot low）
    if is_pl:
        pv_cnt += 1
    if rsi_i < RSI_LOW and is_pl:   # SC 重置计数（与 Pine 执行顺序一致）
        pv_cnt = 0
    if pv_cnt == 1 and is_pl:
        st_idx_list.append(i)

    # BC（分发端）
    if is_ph and rsi_i > RSI_HIGH and not utad_v[i]:
        bc_idx_list.append(i)
        bc_bar  = i
        dar_bar = None

    # DAR（BC 之后第一个 pivot low）
    if bc_bar is not None and is_pl and i > bc_bar:
        dar_idx_list.append(i)
        dar_bar = i
        bc_bar  = None

    # DST（DAR 之后第一个 pivot high，不是新 BC）
    if dar_bar is not None and is_ph and i > dar_bar:
        is_new_bc = (rsi_i > RSI_HIGH and not utad_v[i])
        if not is_new_bc:
            dst_idx_list.append(i)
            dar_bar = None

print(f'\nTV 结构标签:')
for label, lst in [('SC', sc_idx_list), ('AR(积累)', ar_idx_list),
                   ('ST(积累)', st_idx_list), ('BC', bc_idx_list),
                   ('DAR(分发)', dar_idx_list), ('DST(分发)', dst_idx_list)]:
    print(f'  {label:<12}: {len(lst)}')

# ── TV Phase（RSI 状态近似）───────────────────────────────────────────────────
# markup   = RSI 连续两根 >70（趋势上行）
# markdown = RSI 连续两根 <30（趋势下行）
# trading_range = RSI 在 30~70（sideways，可能是 Accumulation 或 Distribution）
ohlcv['tv_phase'] = 'trading_range'
ohlcv.loc[ohlcv['tv_bull'], 'tv_phase'] = 'markup'
ohlcv.loc[ohlcv['tv_bear'], 'tv_phase'] = 'markdown'

# ── V8 入场 Phase 近似 ────────────────────────────────────────────────────────
v8_phase_map = {}
for t in trades:
    ts  = pd.to_datetime(t['open_date'], utc=True)
    tag = t.get('enter_tag', '')
    if tag == 'v8_ws_e':
        v8_phase_map[ts] = 'phase_e_long'    # Phase E：突破 TR High
    elif tag == 'v8_ws_d':
        v8_phase_map[ts] = 'phase_d_long'    # Phase D：LPS 区域
    elif tag == 'v8_short_wsdown':
        v8_phase_map[ts] = 'wsdown_short'    # 分发/下跌延续

ohlcv['v8_entry'] = ohlcv['date'].map(v8_phase_map).fillna('')

# ── 交叉验证 ─────────────────────────────────────────────────────────────────
print('\n' + '='*60)
print('交叉验证：V8 入场 bar 的 TV Phase 分布')
print('='*60)

for v8tag, label in [('phase_e_long', 'V8 ws_e (Phase E 多头)'),
                      ('phase_d_long', 'V8 ws_d (Phase D 多头)'),
                      ('wsdown_short', 'V8 wsdown (空头)')]:
    subset = ohlcv[ohlcv['v8_entry'] == v8tag]
    if len(subset) == 0:
        continue
    dist = subset['tv_phase'].value_counts()
    print(f'\n{label} ({len(subset)} 笔):')
    for ph, cnt in dist.items():
        pct = cnt / len(subset) * 100
        agree = '✅' if (
            (v8tag in ('phase_e_long', 'phase_d_long') and ph in ('markup', 'trading_range')) or
            (v8tag == 'wsdown_short' and ph in ('markdown', 'trading_range'))
        ) else '❌'
        print(f'  {agree} TV={ph:<16} {cnt:>4} ({pct:.1f}%)')

# 一致率汇总
long_mask  = ohlcv['v8_entry'].isin(['phase_e_long', 'phase_d_long'])
short_mask = ohlcv['v8_entry'] == 'wsdown_short'

long_agree  = ohlcv.loc[long_mask,  'tv_phase'].isin(['markup', 'trading_range']).mean()
short_agree = ohlcv.loc[short_mask, 'tv_phase'].isin(['markdown', 'trading_range']).mean()

print(f'\n{"="*60}')
print(f'多头一致率 (TV=markup/trading_range): {long_agree*100:.1f}%')
print(f'空头一致率 (TV=markdown/trading_range): {short_agree*100:.1f}%')

# ── 高置信度标签（两套都同意）─────────────────────────────────────────────────
print('\n=== 高置信度标签分布（V8 入场且 TV 一致）===')

high_conf_long  = ohlcv[long_mask  & ohlcv['tv_phase'].isin(['markup', 'trading_range'])]
high_conf_short = ohlcv[short_mask & ohlcv['tv_phase'].isin(['markdown', 'trading_range'])]
high_conf_long_markup  = ohlcv[long_mask  & (ohlcv['tv_phase'] == 'markup')]
high_conf_long_tr      = ohlcv[long_mask  & (ohlcv['tv_phase'] == 'trading_range')]

print(f'高置信度多头: {len(high_conf_long)} 笔')
print(f'  TV=markup:        {len(high_conf_long_markup)}')
print(f'  TV=trading_range: {len(high_conf_long_tr)}')
print(f'高置信度空头: {len(high_conf_short)} 笔')
print(f'分歧（两套不同意）多头: {long_mask.sum() - len(high_conf_long)} 笔')
print(f'分歧（两套不同意）空头: {short_mask.sum() - len(high_conf_short)} 笔')

# ── SC/AR/BC 时间点样本 ──────────────────────────────────────────────────────
print('\n=== 最近 5 个 SC（卖出高潮）时间点 ===')
for idx in sc_idx_list[-5:]:
    d = ohlcv['date'].iloc[idx]
    r = ohlcv['rsi'].iloc[idx]
    print(f'  {str(d)[:16]}  RSI={r:.1f}  close={ohlcv["close"].iloc[idx]:.0f}')

print('\n=== 最近 5 个 BC（买入高潮）时间点 ===')
for idx in bc_idx_list[-5:]:
    d = ohlcv['date'].iloc[idx]
    r = ohlcv['rsi'].iloc[idx]
    print(f'  {str(d)[:16]}  RSI={r:.1f}  close={ohlcv["close"].iloc[idx]:.0f}')

# ── 保存标签数据 ──────────────────────────────────────────────────────────────
sc_set  = set(sc_idx_list)
ar_set  = set(ar_idx_list)
st_set  = set(st_idx_list)
bc_set  = set(bc_idx_list)
dar_set = set(dar_idx_list)
dst_set = set(dst_idx_list)

label_df = ohlcv[['date', 'open', 'high', 'low', 'close', 'volume',
                   'rsi', 'tv_bull', 'tv_bear', 'tv_side',
                   'pivot_low', 'pivot_high',
                   'spring', 'utad',
                   'tv_phase', 'v8_entry']].copy()

label_df['tv_sc']  = label_df.index.isin(sc_set)
label_df['tv_ar']  = label_df.index.isin(ar_set)
label_df['tv_st']  = label_df.index.isin(st_set)
label_df['tv_bc']  = label_df.index.isin(bc_set)
label_df['tv_dar'] = label_df.index.isin(dar_set)
label_df['tv_dst'] = label_df.index.isin(dst_set)

# 高置信度标签（§3c 训练候选）
label_df['high_conf_long']  = label_df['v8_entry'].isin(['phase_e_long', 'phase_d_long']) & \
                               label_df['tv_phase'].isin(['markup', 'trading_range'])
label_df['high_conf_short'] = (label_df['v8_entry'] == 'wsdown_short') & \
                               label_df['tv_phase'].isin(['markdown', 'trading_range'])

label_df.to_parquet(OUT_FILE, index=False)
print(f'\n标签数据已保存: {OUT_FILE}')
print(f'  行数: {len(label_df)}  列: {len(label_df.columns)}')
print(f'  高置信度多头 bar: {label_df["high_conf_long"].sum()}')
print(f'  高置信度空头 bar: {label_df["high_conf_short"].sum()}')
print(f'\n下一步 §3c：以 tv_phase 为标签，用 48-bar OHLCV 训练 Phase 分类器')
print('  高置信度 bar 作为训练核心，分歧 bar 排除或作为噪声标签')
