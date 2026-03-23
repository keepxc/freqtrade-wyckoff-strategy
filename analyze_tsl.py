import zipfile, json, glob

for zp, label in [
    ('user_data/backtest_results/backtest-result-2026-03-17_16-34-15.zip', '03-17 965%'),
    ('user_data/backtest_results/backtest-result-2026-03-19_14-51-44.zip', '03-19 773%'),
    ('user_data/backtest_results/backtest-result-2026-03-23_14-19-33.zip', '03-23 809% (SOW fix)'),
]:
    with zipfile.ZipFile(zp, 'r') as z:
        names = z.namelist()
        json_name = [n for n in names if n.endswith('.json') and 'meta' not in n][0]
        with z.open(json_name) as fh:
            data = json.load(fh)
    strat = list(data['strategy'].values())[0]
    trades = strat.get('trades', [])

    tsl = [t for t in trades if t.get('exit_reason') == 'trailing_stop_loss']
    roi = [t for t in trades if t.get('exit_reason') == 'roi']
    dw  = [t for t in trades if t.get('exit_reason') == 'wyckoff_down_wave']

    roi_avg = sum(t['profit_ratio'] for t in roi) / len(roi) * 100 if roi else 0
    dw_avg  = sum(t['profit_ratio'] for t in dw)  / len(dw)  * 100 if dw  else 0

    print(f'\n{"="*60}')
    print(f'=== {label} | total_trades={len(trades)} ===')
    print(f'  ROI 退出: {len(roi)} 笔, avg={round(roi_avg,3)}%')
    print(f'  DownWave退出: {len(dw)} 笔, avg={round(dw_avg,3)}%')
    print(f'  TSL 退出: {len(tsl)} 笔')

    # TSL 按入场标签分组
    tsl_by_tag = {}
    for t in tsl:
        tag = t.get('enter_tag', '?')
        tsl_by_tag.setdefault(tag, []).append(t.get('profit_ratio', 0) * 100)
    for tag, profits in sorted(tsl_by_tag.items(), key=lambda x: -len(x[1])):
        avg = sum(profits) / len(profits)
        sample = [round(p, 2) for p in profits[:8]]
        print(f'    {tag:<35} n={len(profits):>3}  avg={round(avg,3):>7}%  sample={sample}')

    # TSL 利润区间分布
    if tsl:
        buckets = {'<0%': 0, '0-0.5%': 0, '0.5-1%': 0, '1-2%': 0, '2-5%': 0, '>5%': 0}
        for t in tsl:
            p = t.get('profit_ratio', 0) * 100
            if p < 0:
                buckets['<0%'] += 1
            elif p < 0.5:
                buckets['0-0.5%'] += 1
            elif p < 1:
                buckets['0.5-1%'] += 1
            elif p < 2:
                buckets['1-2%'] += 1
            elif p < 5:
                buckets['2-5%'] += 1
            else:
                buckets['>5%'] += 1
        print(f'  TSL 利润分布: {buckets}')

        # 看这些 TSL 出场时浮盈峰值是多少（max_profit 字段）
        max_profits = [t.get('max_profit', None) for t in tsl if t.get('max_profit') is not None]
        if max_profits:
            mp_avg = sum(max_profits) / len(max_profits) * 100
            mp_max = max(max_profits) * 100
            print(f'  TSL 峰值浮盈: avg={round(mp_avg,2)}%, max={round(mp_max,2)}%  (共{len(max_profits)}笔有峰值数据)')

    # 核心机会成本估算：45 笔多出来的 TSL vs 少掉的 ROI
    print()
