import json, glob, os

files = glob.glob('user_data/backtest_results/sow_check*.json')
if not files:
    files = glob.glob('user_data/backtest_results/*.json')
    files = [f for f in files if 'meta' not in f]
files.sort(key=os.path.getmtime, reverse=True)
print('文件:', files[0])

with open(files[0]) as f:
    data = json.load(f)

strat = list(data['strategy'].values())[0]
trades = strat['trades']

sow_trades = [t for t in trades if t.get('enter_tag') == 'wyckoff_sow']
print(f'\nSOW 空头交易: {len(sow_trades)} 笔')
for t in sow_trades:
    od = t['open_date']
    cd = t['close_date']
    op = t['open_rate']
    cl = t['close_rate']
    pct = t['profit_ratio'] * 100
    ex = t['exit_reason']
    short = t.get('is_short', False)
    print(f'  {"空头" if short else "多头"}  开仓: {od}  @{op:.1f}  收盘: {cd}  @{cl:.1f}  利润: {pct:.2f}%  退出: {ex}')
