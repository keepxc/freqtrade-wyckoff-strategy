import zipfile, json
from collections import defaultdict, Counter

ZF_BASE = '/freqtrade/user_data/backtest_results/backtest-result-2026-06-12_04-04-59.zip'  # TASK 33b
ZF_T34  = '/freqtrade/user_data/backtest_results/backtest-result-2026-06-15_13-23-56.zip'  # TASK 34

def load(zf):
    with zipfile.ZipFile(zf) as z:
        fname = [n for n in z.namelist() if n.endswith('.json') and 'config' not in n and '.py' not in n][0]
        data = json.loads(z.read(fname))
    return list(data['strategy'].values())[0]['trades']

base = load(ZF_BASE)
t34  = load(ZF_T34)

def key(t):
    return (t['pair'], t['open_date'], t.get('is_short', False))

kb = {key(t): t for t in base}
k4 = {key(t): t for t in t34}

new_in_34  = [t for k, t in k4.items() if k not in kb]
lost_in_34 = [t for k, t in kb.items() if k not in k4]
common     = [(kb[k], k4[k]) for k in kb if k in k4]
changed    = [(a, b) for a, b in common if abs(a['profit_ratio'] - b['profit_ratio']) > 0.0001]

print('=== TASK 34 vs TASK 33b 差异分析 ===')
print(f'基线: {len(base)} trades  TASK 34: {len(t34)} trades')
print(f'新增: {len(new_in_34)}  消失: {len(lost_in_34)}  共同: {len(common)}  profit变化: {len(changed)}')
print()

# ── 新增交易分析 ──
longs_new  = [t for t in new_in_34 if not t.get('is_short')]
shorts_new = [t for t in new_in_34 if t.get('is_short')]
print(f'新增: 多头 {len(longs_new)} 笔  空头 {len(shorts_new)} 笔')

if longs_new:
    wins = [t for t in longs_new if t['profit_ratio'] > 0]
    wr   = len(wins)/len(longs_new)*100
    avgp = sum(t['profit_ratio'] for t in longs_new)/len(longs_new)*100
    total_p = sum(t['profit_ratio'] for t in longs_new)*100
    print(f'新增多头 WR={wr:.1f}%  avg={avgp:.2f}%  sum={total_p:.2f}%')
    print()
    print(f'  {"open_date":<22} {"profit%":>9} {"dur_h":>7}  {"exit_reason":<28} {"enter_tag"}')
    print('  ' + '-'*90)
    for t in sorted(longs_new, key=lambda x: x['open_date']):
        p = t['profit_ratio']*100
        dur = t.get('trade_duration',0)/60
        mk = 'V' if p>0 else 'X'
        print(f'  {t["open_date"]:<22} {p:>+8.2f}% {dur:>6.1f}h  {t["exit_reason"]:<28} {t.get("enter_tag","")}  {mk}')

print()

# ── 消失交易分析 ──
longs_lost  = [t for t in lost_in_34 if not t.get('is_short')]
shorts_lost = [t for t in lost_in_34 if t.get('is_short')]
print(f'消失: 多头 {len(longs_lost)} 笔  空头 {len(shorts_lost)} 笔')

if longs_lost:
    wins = [t for t in longs_lost if t['profit_ratio'] > 0]
    avgp = sum(t['profit_ratio'] for t in longs_lost)/len(longs_lost)*100
    print(f'消失多头 WR={len(wins)/len(longs_lost)*100:.1f}%  avg={avgp:.2f}%')
    for t in sorted(longs_lost, key=lambda x: x['open_date']):
        p = t['profit_ratio']*100
        print(f'  {t["open_date"]:<22} {p:>+8.2f}%  {t["exit_reason"]:<28} {t.get("enter_tag","")}')

if shorts_lost:
    wins = [t for t in shorts_lost if t['profit_ratio'] > 0]
    avgp = sum(t['profit_ratio'] for t in shorts_lost)/len(shorts_lost)*100
    print(f'消失空头 WR={len(wins)/len(shorts_lost)*100:.1f}%  avg={avgp:.2f}%')
    for t in sorted(shorts_lost, key=lambda x: x['open_date']):
        p = t['profit_ratio']*100
        print(f'  {t["open_date"]:<22} {p:>+8.2f}%  {t["exit_reason"]:<28} {t.get("enter_tag","")}')

print()

# ── 净利润影响拆解 ──
gain_new   = sum(t['profit_ratio'] for t in new_in_34)*100
loss_lost  = sum(t['profit_ratio'] for t in lost_in_34)*100
gain_changed = sum((b['profit_ratio']-a['profit_ratio']) for a,b in changed)*100
print('=== 净利润影响拆解（以单笔 profit_ratio 之和衡量，复利链路影响需结合排序） ===')
print(f'新增交易贡献:   {gain_new:>+8.2f}%')
print(f'消失交易损失:   {loss_lost:>+8.2f}%  （这些笔在基线里赚的，T34 没了）')
print(f'共同交易变化:   {gain_changed:>+8.2f}%')
print()

# ── 消失交易中的高收益笔 ──
if lost_in_34:
    print('消失交易按收益排序（Top 10）:')
    for t in sorted(lost_in_34, key=lambda x: x['profit_ratio'], reverse=True)[:10]:
        p = t['profit_ratio']*100
        side = 'S' if t.get('is_short') else 'L'
        print(f'  [{side}] {t["open_date"]:<22} {p:>+8.2f}%  {t["exit_reason"]:<25} {t.get("enter_tag","")}')

print()

# ── 新增多头的 Phase 上下文（通过 enter_tag 推断） ──
print('新增多头 enter_tag 分布:')
for tag, cnt in Counter(t.get('enter_tag','') for t in longs_new).most_common():
    print(f'  {tag}: {cnt}')

print()
print('消失空头 enter_tag 分布:')
for tag, cnt in Counter(t.get('enter_tag','') for t in shorts_lost).most_common():
    print(f'  {tag}: {cnt}')
