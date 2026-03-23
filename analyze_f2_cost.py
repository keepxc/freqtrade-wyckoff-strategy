"""
F2过滤器代价分析：
「日线价格 < MA50 时屏蔽 axis_support」
精确计算：能拦住什么，会误杀什么，净收益如何
"""
import zipfile, json
import pandas as pd
import numpy as np

# ── 加载数据 ──────────────────────────────────────────────────────
D1D = pd.read_feather("user_data/data/binance/futures/BTC_USDT_USDT-1d-futures.feather")
D1D['date'] = pd.to_datetime(D1D['date'], utc=True)
D1D.set_index('date', inplace=True)
D1D.sort_index(inplace=True)
D1D['ma50']  = D1D['close'].rolling(50).mean()
D1D['ma200'] = D1D['close'].rolling(200).mean()
D1D['ma20']  = D1D['close'].rolling(20).mean()

def get_daily_bar(dt):
    dt_d = dt.normalize()
    avail = D1D.index[D1D.index <= dt_d]
    if len(avail) == 0:
        return None
    return D1D.loc[avail[-1]]

# ── 加载所有回测交易 ──────────────────────────────────────────────
ZP = 'user_data/backtest_results/backtest-result-2026-03-23_14-38-40.zip'
with zipfile.ZipFile(ZP) as z:
    jname = [n for n in z.namelist() if n.endswith('.json') and 'meta' not in n][0]
    with z.open(jname) as fh:
        data = json.load(fh)
strat  = list(data['strategy'].values())[0]
all_tr = strat['trades']

print("过滤器代价-收益精确分析")
print("="*70)
print(f"  回测总交易数: {len(all_tr)}")

# 只分析 axis_support 信号
axis_tr = [t for t in all_tr if t.get('enter_tag','') == 'wyckoff_axis_support']
total_profit_axis = sum(t['profit_ratio'] for t in axis_tr) * 100
print(f"  axis_support 信号: {len(axis_tr)} 笔  总利润贡献: {total_profit_axis:+.2f}%\n")

# 给每笔交易打标：是否触发F2（价格 < 日线MA50）
passed = []   # 通过过滤（价格 >= MA50）→ 允许入场
blocked = []  # 被过滤（价格 < MA50）→ 屏蔽入场

for t in axis_tr:
    open_dt = pd.to_datetime(t['open_date'], utc=True)
    d_bar   = get_daily_bar(open_dt)
    if d_bar is None or pd.isna(d_bar['ma50']):
        passed.append(t)  # 数据不足，默认通过
        continue

    close_d = d_bar['close']
    ma50_d  = d_bar['ma50']
    ma200_d = d_bar['ma200'] if not pd.isna(d_bar['ma200']) else 0

    # F2单独：price < MA50
    if close_d < ma50_d:
        blocked.append(t)
    else:
        passed.append(t)

# ── 统计 ──────────────────────────────────────────────────────────
def stats(trades, label):
    if not trades:
        print(f"  {label}: 0 笔")
        return
    profits = [t['profit_ratio'] * 100 for t in trades]
    wins    = [p for p in profits if p > 0]
    losses  = [p for p in profits if p <= 0]
    big_loss= [p for p in profits if p < -8]
    total_p = sum(profits)
    avg_p   = total_p / len(profits)
    wr      = len(wins) / len(profits) * 100

    print(f"  {label}:")
    print(f"    笔数={len(trades)}  胜率={wr:.1f}%  均利润={avg_p:+.3f}%  总利润={total_p:+.2f}%")
    print(f"    盈利笔: {len(wins)} 笔  均盈={sum(wins)/len(wins):+.3f}% 合计={sum(wins):+.2f}%") if wins else None
    print(f"    亏损笔: {len(losses)} 笔  均亏={sum(losses)/len(losses):+.3f}% 合计={sum(losses):+.2f}%") if losses else None
    print(f"    大亏(>-8%)笔: {len(big_loss)} 笔  {[round(p,1) for p in sorted(big_loss)]}")
    # 按profit区间分布
    dist = {'<-8%':0, '-8~-4%':0, '-4~0%':0, '0~2%':0, '2~5%':0, '>5%':0}
    for p in profits:
        if p < -8:   dist['<-8%'] += 1
        elif p < -4: dist['-8~-4%'] += 1
        elif p < 0:  dist['-4~0%'] += 1
        elif p < 2:  dist['0~2%'] += 1
        elif p < 5:  dist['2~5%'] += 1
        else:        dist['>5%'] += 1
    print(f"    利润分布: {dist}")

print("\n【F2过滤器：日线价格 < MA50 → 屏蔽入场】")
print("─"*65)
stats(passed,  "✅ 通过过滤（允许入场）")
print()
stats(blocked, "🚫 被屏蔽（原来会入场的）")

# ── 代价-收益汇总 ─────────────────────────────────────────────────
passed_profit  = sum(t['profit_ratio'] for t in passed)  * 100
blocked_profit = sum(t['profit_ratio'] for t in blocked) * 100
blocked_wins   = [t for t in blocked if t['profit_ratio'] > 0]
blocked_losses = [t for t in blocked if t['profit_ratio'] <= 0]
blocked_bigL   = [t for t in blocked if t['profit_ratio']*100 < -8]

print(f"\n{'='*70}")
print("【代价-收益汇总】")
print("="*70)
print(f"  F2过滤器净效果:")
print(f"    通过(保留)的 axis_support 总利润: {passed_profit:+.2f}%  ({len(passed)}笔)")
print(f"    被屏蔽的 axis_support 总利润:     {blocked_profit:+.2f}%  ({len(blocked)}笔)")
print(f"    → 如果应用F2，axis_support 贡献变化: 原{total_profit_axis:+.2f}% → 新{passed_profit:+.2f}%")
print(f"    → 差值: {passed_profit - total_profit_axis:+.2f}%  ({'+有利' if passed_profit > total_profit_axis else '-不利'})")
print()
print(f"    被屏蔽的 {len(blocked)} 笔中:")
print(f"      赢单: {len(blocked_wins)} 笔  总利润: {sum(t['profit_ratio']*100 for t in blocked_wins):+.2f}%  (损失的机会成本)")
print(f"      亏单: {len(blocked_losses)} 笔  总损失: {sum(t['profit_ratio']*100 for t in blocked_losses):+.2f}%  (避免的亏损)")
print(f"      大亏(>-8%): {len(blocked_bigL)} 笔  → 这就是5笔吗? {[round(t['profit_ratio']*100,1) for t in blocked_bigL]}")

# ── 更细分：被屏蔽中按结果分布 ───────────────────────────────────
print(f"\n{'─'*65}")
print("【被屏蔽的交易明细（按利润排序）】")
blocked_sorted = sorted(blocked, key=lambda x: x['profit_ratio'])
print(f"  {'日期':<17} {'利润':>8}  {'出场原因':<25}  {'止损触发?'}")
print(f"  {'-'*60}")
for t in blocked_sorted:
    p    = t['profit_ratio'] * 100
    dt   = t['open_date'][:16]
    exit_= t['exit_reason']
    is_tsl = "⚠️ TSL" if exit_ == 'trailing_stop_loss' else ""
    mark = "🔴" if p < -8 else ("🟡" if p < 0 else "🟢")
    print(f"  {dt:<17} {p:>+7.2f}%  {exit_:<25}  {is_tsl}{mark}")

# ── 回测整体：F2过滤后的全局影响 ────────────────────────────────
print(f"\n{'='*70}")
print("【全局影响：F2过滤后整体回测变化】")
print("="*70)
all_non_axis = [t for t in all_tr if t.get('enter_tag','') != 'wyckoff_axis_support']
non_axis_profit = sum(t['profit_ratio'] for t in all_non_axis) * 100
original_total  = sum(t['profit_ratio'] for t in all_tr) * 100
new_total       = non_axis_profit + passed_profit

print(f"  原始总利润(所有信号): {original_total:+.2f}%  ({len(all_tr)}笔)")
print(f"  非axis信号利润:       {non_axis_profit:+.2f}%  ({len(all_non_axis)}笔)")
print(f"  F2过滤后axis贡献:     {passed_profit:+.2f}%  ({len(passed)}笔)")
print(f"  F2过滤后预估总利润:   {new_total:+.2f}%  ({len(all_non_axis)+len(passed)}笔)")
print(f"  净变化: {new_total - original_total:+.2f}%  "
      f"({'✅有利' if new_total > original_total else '❌不利'})")
