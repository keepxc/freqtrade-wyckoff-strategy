import json, pathlib

p = pathlib.Path('user_data/backtest_results/axis_extract/backtest-result-2026-03-31_15-37-38.json')
data = json.loads(p.read_text(encoding='utf-8'))
strat = list(data['strategy'].values())[0]
trades = strat['trades']

# 找被 axis_support_failed_exit 截断的交易
failed = [t for t in trades if t.get('exit_reason') == 'axis_support_failed_exit']
print(f'axis_support_failed_exit 共 {len(failed)} 笔:\n')

for t in failed:
    od = t['open_date']
    cd = t['close_date']
    op = t['open_rate']
    cl = t['close_rate']
    mx = t['max_rate']
    mn = t['min_rate']
    pct = t['profit_ratio'] * 100
    dur_h = t['trade_duration'] / 60
    max_p = (mx - op) / op * 100 if op else 0
    dd_peak = (mx - cl) / mx * 100 if mx else 0
    print(f'  开仓: {od}  @${op:,.0f}')
    print(f'  收盘: {cd}  @${cl:,.0f}')
    print(f'  最高: ${mx:,.0f} (浮盈 {max_p:.2f}%)  最低: ${mn:,.0f}')
    print(f'  利润: {pct:.2f}%  持仓: {dur_h:.1f}h  峰值回撤: {dd_peak:.2f}%')
    print()
