"""验证两次回测的初始条件是否一致"""
import zipfile, json, os, glob

# 找到所有回测结果
results_dir = '/home/gyan/freqtrade/user_data/backtest_results'
files = sorted(glob.glob(os.path.join(results_dir, 'backtest-result-*.zip')), reverse=True)

print("=== 回测结果文件列表 ===")
for f in files:
    name = os.path.basename(f)
    size = os.path.getsize(f)
    print(f"  {name}  ({size:,} bytes)")

# 读取最新的两个
for i, f in enumerate(files[:2]):
    print(f"\n=== 回测 #{i+1}: {os.path.basename(f)} ===")
    z = zipfile.ZipFile(f)
    data = json.loads(z.read([n for n in z.namelist() if n.endswith('.json') and not n.endswith('_config.json')][0]))
    s = data['strategy'][list(data['strategy'].keys())[0]]
    
    print(f"  总收益率: {s['profit_total']*100:.2f}%")
    print(f"  总交易数: {s['total_trades']}")
    print(f"  profit_total: {s['profit_total']:.4f}")
    print(f"  profit_total_abs: {s['profit_total_abs']:.2f}")
    print(f"  avg_stake_amount: {s.get('avg_stake_amount', 'N/A')}")
    print(f"  胜率: {s.get('winrate', s.get('winning_trades_pct', 0))*100 if s.get('winrate') else 0:.1f}%")
    print(f"  max_drawdown_account: {s.get('max_drawdown_account', 'N/A')}")
    
    # 信号表现
    print(f"  信号明细:")
    for t in s.get('results_per_enter_tag', []):
        print(f"    {t.get('key','?'):25s}: {t.get('trades',0):4d}笔  均利{t.get('profit_mean_pct',0):5.2f}%  胜率{t.get('winrate',0)*100:5.1f}%  净利{t.get('profit_total_abs',0):10.0f}")
