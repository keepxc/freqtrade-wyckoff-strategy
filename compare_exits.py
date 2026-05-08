"""对比两个版本的退出原因"""
import zipfile, json

def load(path):
    z = zipfile.ZipFile(path)
    names = [n for n in z.namelist() if n.endswith('.json') and not n.endswith('_config.json')]
    data = json.loads(z.read(names[0]))
    return data['strategy'][list(data['strategy'].keys())[0]]

v2 = load('/freqtrade/user_data/backtest_results/backtest-result-2026-05-07_07-34-50.zip')
v3 = load('/freqtrade/user_data/backtest_results/backtest-result-2026-05-07_07-58-30.zip')

print(f"{'Exit Reason':35s} {'V7.2(uni)':>8s} {'V7.3(diff)':>10s} {'Change':>8s}")
print("-" * 65)

all_keys = set()
for e in v2.get('exit_reason_summary', []):
    all_keys.add(e['key'])
for e in v3.get('exit_reason_summary', []):
    all_keys.add(e['key'])

e2m = {e['key']: e for e in v2.get('exit_reason_summary', [])}
e3m = {e['key']: e for e in v3.get('exit_reason_summary', [])}

for k in sorted(all_keys):
    n2 = e2m.get(k, {}).get('trades', 0)
    n3 = e3m.get(k, {}).get('trades', 0)
    p2 = e2m.get(k, {}).get('profit_mean_pct', 0)
    p3 = e3m.get(k, {}).get('profit_mean_pct', 0)
    print(f"{k:35s} {n2:8d} {n3:10d} {n3-n2:+8d}  | V7.2均利{p2:5.2f}% V7.3均利{p3:5.2f}%")

print(f"\nTotal trades: {v2['total_trades']} -> {v3['total_trades']} ({v3['total_trades']-v2['total_trades']:+d})")
print(f"Total profit: {v2['profit_total']*100:.1f}% -> {v3['profit_total']*100:.1f}%")
