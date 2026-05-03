"""
PNF Shadow Protect 超参批量测试脚本

测试不同组合：
- axis_mult: PNF目标乘数
- min_profit: 激活最小盈利门槛
- timeout: 超时时间

每次用 docker compose 跑回测，解析结果，汇总到 CSV。
"""
import subprocess, json, zipfile, os, sys, glob, csv, datetime

# === 参数网格 ===
PARAM_GRID = [
    # (axis_mult, min_profit, timeout_hours)
    (0.15, 0.02, 48),
    (0.15, 0.03, 48),
    (0.20, 0.02, 48),
    (0.20, 0.03, 48),
    (0.20, 0.04, 48),
    (0.25, 0.03, 48),
    (0.25, 0.04, 48),
    (0.30, 0.03, 48),
    (0.30, 0.04, 72),
    (0.35, 0.04, 72),
]

TIMERANGE = '20220101-20260502'
RESULTS_DIR = 'user_data/backtest_results'

def run_backtest(axis_mult, min_profit, timeout):
    """Run a single backtest with given parameters"""
    env = os.environ.copy()
    env['WY_PNF_AXIS_MULT'] = str(axis_mult)
    env['WY_PNF_MIN_PROFIT'] = str(min_profit)
    env['WY_PNF_TIMEOUT_HOURS'] = str(timeout)
    env['WY_PNF_SHADOW_PROTECT'] = '1'
    
    tag = f'm{axis_mult}_p{min_profit}_t{timeout}h'
    print(f'\n{"="*60}')
    print(f'Running: axis_mult={axis_mult}, min_profit={min_profit}, timeout={timeout}h')
    print(f'Tag: {tag}')
    
    # Run docker backtest
    cmd = [
        'docker', 'compose', 'run', '--rm',
        '-e', f'WY_PNF_AXIS_MULT={axis_mult}',
        '-e', f'WY_PNF_MIN_PROFIT={min_profit}',
        '-e', f'WY_PNF_TIMEOUT_HOURS={timeout}',
        '-e', 'WY_PNF_SHADOW_PROTECT=1',
        'freqtrade', 'backtesting',
        '--config', 'user_data/config_backtest.json',
        '--strategy', 'WyckoffV7_TrueMethod',
        '--timerange', TIMERANGE,
        '--cache', 'none',
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        print(f'ERROR: {result.stderr[-500:]}')
        return None
    
    # Find the latest result zip
    zips = glob.glob(os.path.join(RESULTS_DIR, '*.zip'))
    if not zips:
        print('ERROR: No result zip found')
        return None
    latest_zip = max(zips, key=os.path.getmtime)
    
    # Parse results
    with zipfile.ZipFile(latest_zip, 'r') as zf:
        for name in zf.namelist():
            if name.endswith('.json') and 'config' not in name:
                with zf.open(name) as f:
                    data = json.load(f)
                sk = list(data['strategy'].keys())[0]
                trades = data['strategy'][sk]['trades']
                
                total_pnl = sum(t.get('profit_abs', 0) for t in trades)
                total_profit_pct = data['strategy'][sk].get('profit_total', 0)
                
                from collections import Counter
                reasons = Counter(t['exit_reason'] for t in trades)
                
                shadow = reasons.get('shadow_protect_exit', 0)
                timeout_exits = reasons.get('pnf_timeout_exit', 0)
                hard_exits = reasons.get('pnf_target_1', 0) + reasons.get('pnf_target_2', 0)
                roi = reasons.get('roi', 0)
                
                shadow_pnl = sum(t.get('profit_abs', 0) for t in trades if t['exit_reason'] == 'shadow_protect_exit')
                timeout_pnl = sum(t.get('profit_abs', 0) for t in trades if t['exit_reason'] == 'pnf_timeout_exit')
                shadow_avg = shadow_pnl / shadow if shadow > 0 else 0
                
                winrate = reasons.get('shadow_protect_exit', 0) / max(1, shadow + timeout_exits) * 100 if (shadow + timeout_exits) > 0 else 0
                
                return {
                    'axis_mult': axis_mult,
                    'min_profit': min_profit,
                    'timeout_h': timeout,
                    'total_trades': len(trades),
                    'total_pnl': total_pnl,
                    'total_profit_pct': total_profit_pct,
                    'shadow_exits': shadow,
                    'shadow_pnl': shadow_pnl,
                    'shadow_avg_pct': shadow_avg,
                    'timeout_exits': timeout_exits,
                    'timeout_pnl': timeout_pnl,
                    'hard_exits': hard_exits,
                    'roi_exits': roi,
                    'pnf_total': shadow + timeout_exits + hard_exits,
                }
    return None


def main():
    results = []
    
    for axis_mult, min_profit, timeout in PARAM_GRID:
        r = run_backtest(axis_mult, min_profit, timeout)
        if r:
            results.append(r)
            # Print summary
            print(f"  → Total: {r['total_pnl']:.0f} USDT ({r['total_profit_pct']:.1f}%) | "
                  f"Shadow: {r['shadow_exits']}笔(+{r['shadow_pnl']:.0f}) | "
                  f"Timeout: {r['timeout_exits']}笔({r['timeout_pnl']:.0f}) | "
                  f"ROI: {r['roi_exits']}笔")
    
    # Save CSV
    if results:
        csv_path = f'pnf_hyperopt_results_{datetime.datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
        with open(csv_path, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=results[0].keys())
            writer.writeheader()
            writer.writerows(results)
        print(f'\nResults saved to {csv_path}')
        
        # Show best
        best = max(results, key=lambda x: x['total_pnl'])
        print(f'\n🏆 Best: axis_mult={best["axis_mult"]}, min_profit={best["min_profit"]}, '
              f'timeout={best["timeout_h"]}h → {best["total_pnl"]:.0f} USDT')


if __name__ == '__main__':
    main()
