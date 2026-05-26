"""直接对比：V7基线 vs 移除bull_market"""
import zipfile, json

def read_backtest(path):
    z = zipfile.ZipFile(path)
    names = [n for n in z.namelist() if n.endswith('.json') and not n.endswith('_config.json')]
    data = json.loads(z.read(names[0]))
    s = data['strategy'][list(data['strategy'].keys())[0]]
    return s

# V7 基线 (3,512%)
baseline = read_backtest('/home/gyan/freqtrade/user_data/backtest_results/backtest-result-2026-05-04_13-08-43.zip')
# 移除 bull_market
new = read_backtest('/home/gyan/freqtrade/user_data/backtest_results/backtest-result-2026-05-07_07-05-34.zip')

print("=" * 60)
print(f"{'指标':<25s} {'V7基线':>12s} {'移除bull_market':>15s} {'变化':>10s}")
print("=" * 60)

metrics = [
    ('总收益率(%)', 'profit_total', 100),
    ('总交易数', 'total_trades', 1),
    ('胜率(%)', 'winrate', 100),
    ('均利润(%)', 'profit_mean', 100),
    ('profit_total(abs)', 'profit_total_abs', 1),
    ('avg_stake', 'avg_stake_amount', 1),
    ('CAGR', 'cagr', 1),
    ('Sharpe', 'sharpe', 1),
    ('SQN', 'sqn', 1),
    ('profit_factor', 'profit_factor', 1),
]

for label, key, mult in metrics:
    b = baseline.get(key, 0)
    n = new.get(key, 0)
    if isinstance(b, (int, float)) and isinstance(n, (int, float)):
        bv = b * mult
        nv = n * mult
        if key in ('total_trades', 'avg_stake'):
            chg = f"{nv - bv:+.0f}"
        else:
            chg = f"{nv - bv:+.2f}"
        print(f"{label:<25s} {bv:>12.2f} {nv:>15.2f} {chg:>10s}")
    else:
        print(f"{label:<25s} {str(b):>12s} {str(n):>15s}")

# 最大回撤
b_dd = 0
for r in baseline.get('results_per_pair', []):
    dd = r.get('max_drawdown_percent', r.get('max_drawdown', 0))
    if isinstance(dd, (int, float)):
        b_dd = max(b_dd, dd)
n_dd = 0
for r in new.get('results_per_pair', []):
    dd = r.get('max_drawdown_percent', r.get('max_drawdown', 0))
    if isinstance(dd, (int, float)):
        n_dd = max(n_dd, dd)
print(f"{'最大回撤(%)':<25s} {b_dd:>12.2f} {n_dd:>15.2f} {n_dd-b_dd:>+10.2f}")

# 信号对比
print()
print("=" * 60)
print("信号明细对比")
print("=" * 60)
b_tags = {t['key']: t for t in baseline.get('results_per_enter_tag', [])}
n_tags = {t['key']: t for t in new.get('results_per_enter_tag', [])}

all_keys = sorted(set(list(b_tags.keys()) + list(n_tags.keys())))
print(f"{'信号':<25s} {'V7交易数':>8s} {'->':>4s} {'新交易数':>8s} {'V7均利':>8s} {'->':>4s} {'新均利':>8s}")
for k in all_keys:
    bt = b_tags.get(k, {})
    nt = n_tags.get(k, {})
    b_trades = bt.get('trades', 0)
    n_trades = nt.get('trades', 0)
    b_profit = bt.get('profit_mean_pct', 0)
    n_profit = nt.get('profit_mean_pct', 0)
    if b_trades > 0 or n_trades > 0:
        print(f"{k:<25s} {b_trades:>8d} {'->':>4s} {n_trades:>8d} {b_profit:>7.2f}% {'->':>4s} {n_profit:>7.2f}%")
