import json, zipfile, pandas as pd, numpy as np
from pathlib import Path

RD = Path('user_data/backtest_results')

def load_bt(name):
    with zipfile.ZipFile(RD / name) as zf:
        js = [n for n in zf.namelist() if n.endswith('.json') and 'meta' not in n and 'config' not in n]
        with zf.open(js[0]) as f: data = json.load(f)
    sn = list(data['strategy'].keys())[0]
    return pd.DataFrame(data['strategy'][sn]['trades']), data['strategy'][sn]

base_df, base_stats = load_bt('backtest-result-2026-05-07_05-59-42.zip')   # Test A: bull_market ON
new_df, new_stats = load_bt('backtest-result-2026-05-07_06-05-11.zip')     # Test B: bull_market OFF

print('='*70)
print('纯净 A/B: wave_start bull_market ON vs OFF')
print('(V8=OFF, phase_detector 不参与)')
print('='*70)

for label, df, stats in [
    ('Test A (bull_market ON)', base_df, base_stats),
    ('Test B (bull_market OFF)', new_df, new_stats)
]:
    ws = df[df['enter_tag'] == 'wyckoff_wave_start']
    ax = df[df['enter_tag'] == 'wyckoff_axis_support']
    n = len(ws)
    print(f'\n{label}:')
    print(f'  总交易: {len(df)}  总利润: {stats["profit_total"]:,.0f} USDT  胜率: {stats["winrate"]*100:.1f}%')
    if n > 0:
        wr = (ws['profit_ratio'] > 0).sum() / n * 100
        print(f'  ⭐ wave_start: {n}笔  胜率: {wr:.1f}%  均利润: {ws["profit_ratio"].mean()*100:+.2f}%  中位数: {ws["profit_ratio"].median()*100:+.2f}%  净利: {ws["profit_abs"].sum():,.0f}')
    print(f'  axis_support: {len(ax)}笔  均利润: {ax["profit_ratio"].mean()*100:+.2f}%  净利: {ax["profit_abs"].sum():,.0f}')
    ts = df['exit_reason'].value_counts()
    print(f'  TSL: {ts.get("trailing_stop_loss",0)}  down_wave: {ts.get("wyckoff_down_wave",0)}')

# Entry tag breakdown
print('\n--- Entry Tag 对比 ---')
for tag in ['wyckoff_wave_start', 'wyckoff_axis_support', 'wyckoff_sos', 'wyckoff_efficiency', 'wyckoff_sow']:
    bc = base_df[base_df['enter_tag'] == tag]
    nc = new_df[new_df['enter_tag'] == tag]
    ba = bc['profit_ratio'].mean()*100 if len(bc)>0 else 0
    na = nc['profit_ratio'].mean()*100 if len(nc)>0 else 0
    bw = (bc['profit_ratio']>0).sum()/max(len(bc),1)*100
    nw = (nc['profit_ratio']>0).sum()/max(len(nc),1)*100
    delta = len(nc) - len(bc)
    sign = '+' if delta > 0 else ''
    print(f'  {tag}: {len(bc)}→{len(nc)} ({sign}{delta}) | 胜率 {bw:.0f}%→{nw:.0f}% | 均利 {ba:+.2f}%→{na:+.2f}%')

print('\nDone.')
