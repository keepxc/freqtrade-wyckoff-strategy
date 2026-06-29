"""
审计 v8_short_wsdown 的退出原因分布
分析历史回测里止损实际触发了多少次 vs wave_reverse 退出
"""
import zipfile, json
from collections import Counter

ZF = '/freqtrade/user_data/backtest_results/backtest-result-2026-06-12_04-04-59.zip'

def load_trades(zf_path):
    with zipfile.ZipFile(zf_path) as z:
        fname = [n for n in z.namelist() if n.endswith('.json') and 'config' not in n and '.py' not in n][0]
        data = json.loads(z.read(fname))
    return list(data['strategy'].values())[0]['trades']

trades = load_trades(ZF)
wsdown = [t for t in trades if t.get('enter_tag') == 'v8_short_wsdown']
print(f'v8_short_wsdown 总笔数: {len(wsdown)}')

exit_dist = Counter(t['exit_reason'] for t in wsdown)
print('\n=== exit_reason 分布 ===')
for reason, cnt in exit_dist.most_common():
    print(f'  {reason:<35} {cnt:>4} 笔  {cnt/len(wsdown)*100:>5.1f}%')

stoploss_hits = [t for t in wsdown if 'stop' in t['exit_reason'].lower()]
print(f'\n=== 止损触发（{len(stoploss_hits)} 笔）===')
if stoploss_hits:
    for t in sorted(stoploss_hits, key=lambda x: x['profit_ratio']):
        p = t['profit_ratio'] * 100
        dur = t.get('trade_duration', 0) / 60
        print(f'  {t["open_date"]:<22} {p:>+8.2f}%  {dur:>6.1f}h  {t["exit_reason"]}')
else:
    print('  无止损触发记录')

wave_exits = [t for t in wsdown if t['exit_reason'] == 'v8_up_wave_reverse']
wins = [t for t in wave_exits if t['profit_ratio'] > 0]
loss = [t for t in wave_exits if t['profit_ratio'] <= 0]
print(f'\n=== v8_up_wave_reverse 退出（{len(wave_exits)} 笔）===')
if wave_exits:
    print(f'  盈利: {len(wins)}  亏损: {len(loss)}  WR: {len(wins)/len(wave_exits)*100:.1f}%')
    avg_p   = sum(t["profit_ratio"] for t in wave_exits) / len(wave_exits) * 100
    avg_dur = sum(t.get("trade_duration",0) for t in wave_exits) / len(wave_exits) / 60
    print(f'  平均收益: {avg_p:+.3f}%  平均持仓: {avg_dur:.1f}h')

print(f'\n=== wave_reverse 亏损笔明细（止损替代成本）===')
for t in sorted(loss, key=lambda x: x['profit_ratio']):
    print(f'  {t["open_date"]:<22} {t["profit_ratio"]*100:>+8.3f}%  {t.get("trade_duration",0)/60:>6.1f}h')

all_wins = [t for t in wsdown if t['profit_ratio'] > 0]
all_loss = [t for t in wsdown if t['profit_ratio'] <= 0]
print(f'\n=== 整体摘要 ===')
print(f'  WR: {len(all_wins)}/{len(wsdown)} = {len(all_wins)/len(wsdown)*100:.1f}%')
print(f'  平均赢: {sum(t["profit_ratio"] for t in all_wins)/max(1,len(all_wins))*100:+.3f}%')
print(f'  平均亏: {sum(t["profit_ratio"] for t in all_loss)/max(1,len(all_loss))*100:+.3f}%')
print(f'  盈亏比: {abs(sum(t["profit_ratio"] for t in all_wins)/max(1,len(all_wins))) / abs(sum(t["profit_ratio"] for t in all_loss)/max(1,len(all_loss))):.2f}')
