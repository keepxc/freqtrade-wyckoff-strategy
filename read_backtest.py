import zipfile, json

z = zipfile.ZipFile('/freqtrade/user_data/backtest_results/backtest-result-2026-05-07_09-41-08.zip')
data = json.loads(z.read('backtest-result-2026-05-07_09-41-08.json'))
s = data['strategy'][list(data['strategy'].keys())[0]]

profit_pct = s['profit_total'] * 100
print(f"总收益率: {profit_pct:.2f}%")
print(f"总交易数: {s['total_trades']}")
print(f"profit_mean: {s['profit_mean']:.4f}")
print(f"profit_median: {s['profit_median']:.4f}")
print(f"profit_total: {s['profit_total']:.4f}")
print(f"Sharpe: {s['sharpe']:.2f}")
print(f"SQN: {s['sqn']:.2f}")
print(f"CAGR: {s['cagr']:.2f}")
print(f"profit_factor: {s['profit_factor']:.2f}")
print(f"expectancy: {s['expectancy']:.4f}")

# 最大回撤
drawdown = 0
for r in s.get('results_per_pair', []):
    dd = r.get('max_drawdown_percent', r.get('max_drawdown', 0))
    if isinstance(dd, (int, float)):
        drawdown = max(drawdown, dd)
print(f"最大回撤: {drawdown:.2f}%")

print(f"\n--- 信号表现 ---")
for t in s.get('results_per_enter_tag', []):
    print(f"  {t}")

print(f"\n--- 退出原因 ---")
print(json.dumps(s.get('exit_reason_summary', {}), indent=2))

print(f"\n--- mix_tag_stats ---")
print(json.dumps(s.get('mix_tag_stats', {}), indent=2))
