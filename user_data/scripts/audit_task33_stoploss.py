import zipfile, json
from collections import defaultdict

ZF32 = '/freqtrade/user_data/backtest_results/backtest-result-2026-06-09_15-33-15.zip'
ZF33 = '/freqtrade/user_data/backtest_results/backtest-result-2026-06-10_12-43-02.zip'

def load(zf):
    with zipfile.ZipFile(zf) as z:
        fname = [n for n in z.namelist() if n.endswith('.json') and 'config' not in n and '.py' not in n][0]
        data = json.loads(z.read(fname))
    return list(data['strategy'].values())[0]['trades']

t32 = load(ZF32)
t33 = load(ZF33)

def key(t):
    return (t['pair'], t['open_date'], t.get('is_short', False))

k32 = {key(t): t for t in t32}
k33 = {key(t): t for t in t33}

new_in_33  = [t for k, t in k33.items() if k not in k32]
lost_in_33 = [t for k, t in k32.items() if k not in k33]
common     = [(k32[k], k33[k]) for k in k32 if k in k33]

print('=== TASK 33 vs TASK 32 差异分析 ===')
print(f'TASK 32: {len(t32)} trades  TASK 33: {len(t33)} trades')
print(f'新增: {len(new_in_33)}  消失: {len(lost_in_33)}  共同: {len(common)}')
print()

# 1. 共同交易：profit 变化
changed = [(a, b) for a, b in common if abs(a['profit_ratio'] - b['profit_ratio']) > 0.0001]
same    = [(a, b) for a, b in common if abs(a['profit_ratio'] - b['profit_ratio']) <= 0.0001]
print(f'共同交易 profit 有变化: {len(changed)} 笔  无变化: {len(same)} 笔')
print()

if changed:
    print('变化明细（按 delta 降序，前 30 笔）:')
    hdr = f'  {"date":<22} {"T32%":>9} {"T33%":>9} {"delta":>8}  {"exit32":<25} {"exit33"}'
    print(hdr)
    print('  ' + '-' * (len(hdr) - 2))
    changed_sorted = sorted(changed, key=lambda x: x[1]['profit_ratio'] - x[0]['profit_ratio'], reverse=True)
    for a, b in changed_sorted[:30]:
        d = (b['profit_ratio'] - a['profit_ratio']) * 100
        print(f'  {a["open_date"]:<22} {a["profit_ratio"]*100:>8.2f}% {b["profit_ratio"]*100:>8.2f}% {d:>+7.2f}pp  {a["exit_reason"]:<25} {b["exit_reason"]}')
    # exit_reason 分布变化
    from collections import Counter
    exit32 = Counter(a['exit_reason'] for a, b in changed)
    exit33 = Counter(b['exit_reason'] for a, b in changed)
    print()
    print('变化交易 exit_reason 分布（T32 → T33）:')
    all_reasons = sorted(set(list(exit32.keys()) + list(exit33.keys())))
    for r in all_reasons:
        print(f'  {r:<30} T32:{exit32.get(r,0):>4}  T33:{exit33.get(r,0):>4}')

print()

# 2. 新增交易分析
shorts_new = [t for t in new_in_33 if t.get('is_short')]
longs_new  = [t for t in new_in_33 if not t.get('is_short')]
print(f'新增: 空头 {len(shorts_new)} 笔  多头 {len(longs_new)} 笔')

if shorts_new:
    wins = [t for t in shorts_new if t['profit_ratio'] > 0]
    wr   = len(wins) / len(shorts_new) * 100
    avgp = sum(t['profit_ratio'] for t in shorts_new) / len(shorts_new) * 100
    print(f'新增空头 WR: {wr:.1f}%  avg profit: {avgp:.2f}%')
    print()
    hdr2 = f'  {"date":<22} {"profit%":>9} {"dur_h":>7}  {"exit_reason"}'
    print(hdr2)
    print('  ' + '-' * (len(hdr2) - 2))
    for t in sorted(shorts_new, key=lambda x: x['open_date']):
        p = t['profit_ratio'] * 100
        dur = t.get('trade_duration', 0) / 60
        mk = 'V' if p > 0 else 'X'
        print(f'  {t["open_date"]:<22} {p:>+8.2f}% {dur:>6.1f}h  {t["exit_reason"]}  {mk}')

print()

# 3. 消失交易（T32 有但 T33 没有）
shorts_lost = [t for t in lost_in_33 if t.get('is_short')]
longs_lost  = [t for t in lost_in_33 if not t.get('is_short')]
print(f'消失: 空头 {len(shorts_lost)} 笔  多头 {len(longs_lost)} 笔')
if shorts_lost:
    print('消失空头明细:')
    for t in sorted(shorts_lost, key=lambda x: x['open_date']):
        p = t['profit_ratio'] * 100
        print(f'  {t["open_date"]:<22} {p:>+8.2f}%  {t["exit_reason"]}')

print()

# 4. wave_breakdown 路径专项统计
def path_stats(trades, tag):
    wb = [t for t in trades if t.get('enter_tag', '') == tag and t.get('is_short')]
    if not wb:
        return None
    wins = [t for t in wb if t['profit_ratio'] > 0]
    sl   = [t for t in wb if t.get('exit_reason', '') == 'stop_loss']
    return {
        'n': len(wb),
        'wr': len(wins) / len(wb) * 100,
        'avg_p': sum(t['profit_ratio'] for t in wb) / len(wb) * 100,
        'sl_n': len(sl),
        'sl_pct': len(sl) / len(wb) * 100,
        'avg_dur': sum(t.get('trade_duration', 0) for t in wb) / len(wb) / 60,
    }

s32 = path_stats(t32, 'v8_short_wsdown')
s33 = path_stats(t33, 'v8_short_wsdown')
print('=== wave_breakdown (v8_short_wsdown) 专项 ===')
if s32 and s33:
    print(f'{"指标":<20} {"TASK 32":>12} {"TASK 33":>12} {"delta":>10}')
    print('-' * 56)
    print(f'{"交易数":<20} {s32["n"]:>12} {s33["n"]:>12} {s33["n"]-s32["n"]:>+10}')
    print(f'{"胜率":<20} {s32["wr"]:>11.1f}% {s33["wr"]:>11.1f}% {s33["wr"]-s32["wr"]:>+9.1f}pp')
    print(f'{"平均收益":<20} {s32["avg_p"]:>11.2f}% {s33["avg_p"]:>11.2f}% {s33["avg_p"]-s32["avg_p"]:>+9.2f}pp')
    print(f'{"止损次数":<20} {s32["sl_n"]:>12} {s33["sl_n"]:>12} {s33["sl_n"]-s32["sl_n"]:>+10}')
    print(f'{"止损率":<20} {s32["sl_pct"]:>11.1f}% {s33["sl_pct"]:>11.1f}% {s33["sl_pct"]-s32["sl_pct"]:>+9.1f}pp')
    print(f'{"平均持仓h":<20} {s32["avg_dur"]:>12.1f} {s33["avg_dur"]:>12.1f} {s33["avg_dur"]-s32["avg_dur"]:>+10.1f}')
