"""
大级别趋势分析：5笔大亏TSL单的日线结构复盘
验证「更高周期结构过滤」能否有效屏蔽这些入场
"""
import zipfile, json
import pandas as pd
import numpy as np

# ── 加载数据 ──────────────────────────────────────────────────────
D4H = pd.read_feather("user_data/data/binance/futures/BTC_USDT_USDT-4h-futures.feather")
D1D = pd.read_feather("user_data/data/binance/futures/BTC_USDT_USDT-1d-futures.feather")

for df in [D4H, D1D]:
    df['date'] = pd.to_datetime(df['date'], utc=True)
    df.set_index('date', inplace=True)
    df.sort_index(inplace=True)

# 计算日线指标
D1D['ma50']  = D1D['close'].rolling(50).mean()
D1D['ma200'] = D1D['close'].rolling(200).mean()
D1D['ma20']  = D1D['close'].rolling(20).mean()
# 日线连续下跌根数
D1D['is_down'] = (D1D['close'] < D1D['open']).astype(int)
# 日线近N天最大跌幅（衡量趋势强度）
D1D['ret_7d']  = D1D['close'].pct_change(7)  * 100
D1D['ret_14d'] = D1D['close'].pct_change(14) * 100
D1D['ret_30d'] = D1D['close'].pct_change(30) * 100

# 4h 指标
D4H['ma20_4h']  = D4H['close'].rolling(20).mean()
D4H['ma50_4h']  = D4H['close'].rolling(50).mean()
# 4h近N根K线累计跌幅
D4H['ret_3c']  = D4H['close'].pct_change(3)  * 100   # 3根4h = 12h
D4H['ret_6c']  = D4H['close'].pct_change(6)  * 100   # 6根4h = 24h
D4H['ret_12c'] = D4H['close'].pct_change(12) * 100   # 12根4h = 2天


# ── 加载5笔大亏单 ─────────────────────────────────────────────────
ZP = 'user_data/backtest_results/backtest-result-2026-03-23_14-38-40.zip'
with zipfile.ZipFile(ZP) as z:
    jname = [n for n in z.namelist() if n.endswith('.json') and 'meta' not in n][0]
    with z.open(jname) as fh:
        data = json.load(fh)
strat  = list(data['strategy'].values())[0]
trades = strat['trades']
tsl    = [t for t in trades if t['exit_reason'] == 'trailing_stop_loss']
big5   = sorted([t for t in tsl if t['profit_ratio']*100 < -8], key=lambda x: x['open_date'])


def get_daily_bar(dt):
    """找dt当天或之前的最近日线收盘"""
    dt_d = dt.normalize()
    avail = D1D.index[D1D.index <= dt_d]
    if len(avail) == 0:
        return None
    return D1D.loc[avail[-1]]

def get_4h_bar(dt):
    """找dt对应的4h K线（之前最近）"""
    avail = D4H.index[D4H.index <= dt]
    if len(avail) == 0:
        return None
    return D4H.loc[avail[-1]]

def trend_label(close, ma20, ma50, ma200):
    """根据均线排列给趋势打标"""
    score = 0
    labels = []
    if close > ma200:
        score += 3; labels.append("价>MA200")
    else:
        score -= 3; labels.append("价<MA200⚠️")
    if close > ma50:
        score += 2; labels.append("价>MA50")
    else:
        score -= 2; labels.append("价<MA50⚠️")
    if close > ma20:
        score += 1; labels.append("价>MA20")
    else:
        score -= 1; labels.append("价<MA20⚠️")
    if ma20 > ma50:
        score += 1; labels.append("MA20>MA50")
    else:
        labels.append("MA20<MA50⚠️")
    if ma50 > ma200:
        score += 1; labels.append("MA50>MA200")
    else:
        labels.append("MA50<MA200⚠️")

    if score >= 5:
        trend = "强势上升趋势"
    elif score >= 2:
        trend = "弱势/震荡偏多"
    elif score >= -1:
        trend = "震荡/顶部警告"
    else:
        trend = "下降趋势⚠️⚠️"
    return trend, score, labels

# ══════════════════════════════════════════════════════════════════
print("5笔大亏TSL单：大级别趋势结构分析")
print("="*70)

results = []
for i, t in enumerate(big5):
    open_dt   = pd.to_datetime(t['open_date'], utc=True)
    open_rate = t['open_rate']
    profit_pct= t['profit_ratio']*100
    close_dt  = pd.to_datetime(t['close_date'], utc=True)

    # 日线数据（入场当天）
    d_bar  = get_daily_bar(open_dt)
    # 4h数据（入场K线）
    h4_bar = get_4h_bar(open_dt)

    if d_bar is None or pd.isna(d_bar['ma200']):
        print(f"#{i+1}: 日线数据不足")
        continue

    close_d  = d_bar['close']
    ma20_d   = d_bar['ma20']
    ma50_d   = d_bar['ma50']
    ma200_d  = d_bar['ma200']
    ret_7d   = d_bar['ret_7d']
    ret_14d  = d_bar['ret_14d']
    ret_30d  = d_bar['ret_30d']

    trend, score, labels = trend_label(close_d, ma20_d, ma50_d, ma200_d)

    # 日线MA斜率（用近5天MA20斜率判断方向）
    dt_d = open_dt.normalize()
    recent_d = D1D.loc[D1D.index <= dt_d].tail(5)
    ma20_slope = (recent_d['ma20'].iloc[-1] - recent_d['ma20'].iloc[0]) / recent_d['ma20'].iloc[0] * 100 if len(recent_d) >= 5 else float('nan')

    # 4h数据
    ret_3c  = h4_bar['ret_3c']  if h4_bar is not None else float('nan')
    ret_12c = h4_bar['ret_12c'] if h4_bar is not None else float('nan')
    ma20_4h = h4_bar['ma20_4h'] if h4_bar is not None else float('nan')
    ma50_4h = h4_bar['ma50_4h'] if h4_bar is not None else float('nan')

    print(f"\n{'─'*65}")
    print(f"#{i+1}  开仓: {open_dt.strftime('%Y-%m-%d %H:%M')}  @{open_rate:,.2f}  利润={profit_pct:+.2f}%")
    print(f"\n  【日线趋势结构（入场日）】")
    print(f"  当日收盘: {close_d:,.2f}")
    print(f"  MA20={ma20_d:,.2f}  MA50={ma50_d:,.2f}  MA200={ma200_d:,.2f}")
    print(f"  趋势: {trend}  (得分={score:+d})")
    print(f"  均线细节: {', '.join(labels)}")
    print(f"  MA20斜率(近5日): {ma20_slope:+.2f}%  ({'↗上行' if ma20_slope>0 else '↘下行⚠️'})")
    print(f"  近7日收益:  {ret_7d:+.2f}%")
    print(f"  近14日收益: {ret_14d:+.2f}%")
    print(f"  近30日收益: {ret_30d:+.2f}%")

    print(f"\n  【4h短期结构（入场K线）】")
    if h4_bar is not None:
        above_ma20 = "价>MA20_4h" if open_rate > ma20_4h else "价<MA20_4h⚠️"
        above_ma50 = "价>MA50_4h" if open_rate > ma50_4h else "价<MA50_4h⚠️"
        print(f"  4h MA20={ma20_4h:,.2f}  MA50={ma50_4h:,.2f}")
        print(f"  {above_ma20}  {above_ma50}")
        print(f"  前3根4h K线累计涨跌: {ret_3c:+.2f}%")
        print(f"  前12根4h K线累计涨跌: {ret_12c:+.2f}%")

    # 过滤判断
    print(f"\n  【日线结构过滤判断】")
    filters = []
    blocked = False

    # F1: 价格在MA200下方
    if close_d < ma200_d:
        filters.append(f"  ✅ F1-MA200: 价格({close_d:,.0f}) < MA200({ma200_d:,.0f}) → 屏蔽")
        blocked = True
    else:
        gap_pct = (close_d - ma200_d) / ma200_d * 100
        filters.append(f"  ❌ F1-MA200: 价格在MA200上方 +{gap_pct:.1f}% → 不屏蔽")

    # F2: 价格在MA50下方
    if close_d < ma50_d:
        filters.append(f"  ✅ F2-MA50:  价格({close_d:,.0f}) < MA50({ma50_d:,.0f}) → 屏蔽")
        blocked = True
    else:
        gap_pct = (close_d - ma50_d) / ma50_d * 100
        filters.append(f"  ❌ F2-MA50:  价格在MA50上方 +{gap_pct:.1f}% → 不屏蔽")

    # F3: MA20斜率向下 + 价格在MA20下方
    if ma20_slope < -1.0 and close_d < ma20_d:
        filters.append(f"  ✅ F3-趋势: MA20斜率{ma20_slope:+.2f}%且价<MA20 → 屏蔽")
        blocked = True
    elif ma20_slope < -0.5:
        filters.append(f"  ⚠️  F3-趋势: MA20斜率{ma20_slope:+.2f}%下行（警告但不屏蔽）")
    else:
        filters.append(f"  ❌ F3-趋势: MA20斜率{ma20_slope:+.2f}% → 不屏蔽")

    # F4: 近14日跌幅超过阈值
    if ret_14d < -12.0:
        filters.append(f"  ✅ F4-短期崩跌: 近14日{ret_14d:+.2f}% < -12% → 屏蔽")
        blocked = True
    elif ret_14d < -8.0:
        filters.append(f"  ⚠️  F4-短期崩跌: 近14日{ret_14d:+.2f}%（警告）")
    else:
        filters.append(f"  ❌ F4-短期崩跌: 近14日{ret_14d:+.2f}% → 不屏蔽")

    for f in filters:
        print(f)

    verdict = "✅ 已屏蔽（至少1个过滤器命中）" if blocked else "❌ 未屏蔽（所有过滤器均未命中）"
    print(f"\n  最终判断: {verdict}")

    results.append({
        'idx': i+1,
        'date': open_dt.strftime('%Y-%m-%d'),
        'open_rate': open_rate,
        'profit': profit_pct,
        'trend': trend,
        'score': score,
        'ma200_gap': (close_d - ma200_d) / ma200_d * 100,
        'ma50_gap':  (close_d - ma50_d)  / ma50_d  * 100,
        'ma20_slope': ma20_slope,
        'ret_14d': ret_14d,
        'blocked': blocked,
    })

print(f"\n{'='*70}")
print("【汇总：各过滤器覆盖情况】")
print("="*70)
blocked_n = sum(1 for r in results if r['blocked'])
print(f"\n  5笔大亏单中，日线结构过滤器能拦住: {blocked_n}/5 笔\n")
for r in results:
    b = "✅拦住" if r['blocked'] else "❌漏网"
    print(f"  #{r['idx']} {r['date']}  {r['trend']}(score={r['score']:+d})"
          f"  MA200{r['ma200_gap']:+.1f}%  MA50{r['ma50_gap']:+.1f}%"
          f"  MA20斜率{r['ma20_slope']:+.2f}%  14d={r['ret_14d']:+.1f}%  → {b}")

# ── 额外：看整体回测中 axis_support 按趋势得分分布的胜率 ──────────
print(f"\n{'='*70}")
print("【延伸：现有回测中 axis_support 按日线趋势分组的胜率】")
print("="*70)

ZP2 = 'user_data/backtest_results/backtest-result-2026-03-23_14-38-40.zip'
with zipfile.ZipFile(ZP2) as z:
    jname = [n for n in z.namelist() if n.endswith('.json') and 'meta' not in n][0]
    with z.open(jname) as fh:
        data2 = json.load(fh)
strat2  = list(data2['strategy'].values())[0]
all_tr  = strat2['trades']
axis_tr = [t for t in all_tr if t.get('enter_tag','') == 'wyckoff_axis_support']

print(f"\n  axis_support 总入场次数: {len(axis_tr)}")

# 给每笔交易打日线趋势分
buckets = {'强势上升趋势': [], '弱势/震荡偏多': [], '震荡/顶部警告': [], '下降趋势⚠️⚠️': []}
for t in axis_tr:
    open_dt = pd.to_datetime(t['open_date'], utc=True)
    d_bar   = get_daily_bar(open_dt)
    if d_bar is None or pd.isna(d_bar['ma200']):
        continue
    trend_name, sc, _ = trend_label(d_bar['close'], d_bar['ma20'], d_bar['ma50'], d_bar['ma200'])
    profit = t['profit_ratio'] * 100
    buckets[trend_name].append(profit)

print(f"\n  {'趋势分类':<18} {'笔数':>5}  {'胜率':>7}  {'均利润':>9}  {'最差':>9}  {'最佳':>9}")
print(f"  {'-'*65}")
for trend_name, profits in buckets.items():
    if not profits:
        print(f"  {trend_name:<18} {'0':>5}")
        continue
    wins     = sum(1 for p in profits if p > 0)
    win_rate = wins / len(profits) * 100
    avg_p    = sum(profits) / len(profits)
    min_p    = min(profits)
    max_p    = max(profits)
    print(f"  {trend_name:<18} {len(profits):>5}  {win_rate:>6.1f}%  {avg_p:>+8.2f}%  {min_p:>+8.2f}%  {max_p:>+8.2f}%")

print(f"\n  → 结论：下降趋势中 axis_support 的胜率和均利润是否显著更差？")
print(f"    如果是，则日线趋势过滤器有明确统计依据。")
