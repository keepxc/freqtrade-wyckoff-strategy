import zipfile, json
from collections import defaultdict

zf = '/freqtrade/user_data/backtest_results/backtest-result-2026-06-08_09-34-43.zip'
with zipfile.ZipFile(zf) as z:
    fname = [n for n in z.namelist()
             if n.endswith('.json') and 'config' not in n and 'WyckoffV8.py' not in n][0]
    data = json.loads(z.read(fname))

strat = list(data['strategy'].values())[0]
trades = strat['trades']

path_stats = defaultdict(lambda: {'w': 0, 'l': 0, 'p': [], 'dur': [], 'exits': defaultdict(int)})

for t in trades:
    tag = t.get('enter_tag', 'unknown')
    profit = t.get('profit_ratio', 0)
    dur = t.get('trade_duration', 0)
    short = t.get('is_short', False)
    exit_reason = t.get('exit_reason', '')
    key = tag + ('_S' if short else '_L')
    if profit > 0:
        path_stats[key]['w'] += 1
    else:
        path_stats[key]['l'] += 1
    path_stats[key]['p'].append(profit)
    path_stats[key]['dur'].append(dur)
    path_stats[key]['exits'][exit_reason] += 1

print('Path Stats by enter_tag (TASK 31b baseline, rolling=1)')
print()
header = f'{"path":<32} {"n":>4} {"W":>4} {"L":>4} {"WR%":>7} {"avgP%":>8} {"avgDurH":>8}'
print(header)
print('-' * len(header))
for tag in sorted(path_stats, key=lambda x: -(path_stats[x]['w']+path_stats[x]['l'])):
    s = path_stats[tag]
    total = s['w'] + s['l']
    wr = s['w'] / total * 100
    avg_p = sum(s['p']) / total * 100
    avg_d = sum(s['dur']) / total / 60
    print(f'{tag:<32} {total:>4}  {s["w"]:>3}  {s["l"]:>3}  {wr:>6.1f}%  {avg_p:>+7.2f}%  {avg_d:>7.1f}h')

print()
print('Exit reason breakdown for wave_breakdown short (v8_short_wsdown_S):')
wb = path_stats.get('v8_short_wsdown_S', {})
if wb:
    for reason, cnt in sorted(wb['exits'].items(), key=lambda x: -x[1]):
        pct = cnt / (wb['w'] + wb['l']) * 100
        print(f'  {reason:<30} {cnt:>4}  ({pct:.1f}%)')
