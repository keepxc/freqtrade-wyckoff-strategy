#!/home/gyan/freqtrade/.venv/bin/python3
"""HybridTraderV1 Dry Run Diagnostics — run this on dryrun status queries"""
import sqlite3, subprocess, os, json, re

DB_PATH = '/home/gyan/freqtrade/user_data/tradesv3.hybrid_dryrun.sqlite'
MARKET_DB = '/home/gyan/freqtrade/user_data/market.db'
LOG_PATH = '/home/gyan/freqtrade/logs/hybrid-dryrun.log'

# Process
r = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
proc_lines = [l for l in r.stdout.split('\n') if 'HybridTraderV1' in l and 'grep' not in l]
if proc_lines:
    p = proc_lines[0]
    pid = p.split()[1]
    m = re.search(r'Ssl\s+(\S+)\s+(\d+:\d+)', p)
    start = m.group(1) + ' ' + m.group(2) if m else p.split()[5]
    proc_str = 'Running (PID {}) | 启动 {}'.format(pid, start)
else:
    proc_str = 'NOT RUNNING'

# Trade DB
conn = sqlite3.connect(DB_PATH)
open_trades = list(conn.execute('SELECT id, pair, open_rate, stop_loss, open_date, enter_tag FROM trades WHERE is_open=1'))
n_open = len(open_trades)
n_closed = list(conn.execute('SELECT COUNT(*) FROM trades WHERE is_open=0'))[0][0]
q = 'SELECT COUNT(*), COALESCE(SUM(CASE WHEN close_profit>0 THEN 1 ELSE 0 END),0), COALESCE(AVG(close_profit)*100,0), COALESCE(SUM(close_profit)*100,0) FROM trades WHERE is_open=0'
cw = list(conn.execute(q))[0]
q2 = 'SELECT exit_reason, COUNT(*), AVG(close_profit)*100 FROM trades WHERE is_open=0 GROUP BY exit_reason ORDER BY COUNT(*) DESC'
exit_dist = list(conn.execute(q2))
conn.close()
wr = cw[1]/cw[0]*100 if cw[0] else 0

# OIPairList whitelist from latest log (top pair only, full list truncates)
top_pair = 'N/A'
try:
    log_r = subprocess.run(['grep', 'OIPairList: Generated', LOG_PATH], capture_output=True, text=True)
    if log_r.stdout:
        last_line = log_r.stdout.strip().split('\n')[-1]
        m2 = re.search(r'Top: ([^ ]+)', last_line)
        if m2:
            top_pair = m2.group(1)
except:
    pass

# MSE Watchlist
mse_wl = []
try:
    conn2 = sqlite3.connect(MARKET_DB)
    mse_wl = list(conn2.execute('SELECT symbol, grade, score FROM watchlist ORDER BY score DESC'))
    conn2.close()
except:
    pass

# Recent log analysis for issues
try:
    log_tail = subprocess.run(['tail', '-50', LOG_PATH], capture_output=True, text=True).stdout
except:
    log_tail = ''
issues = []
if 'BadSymbol' in log_tail:
    issues.append('部分 OIPairList 币种不在 OKX 上线，自动跳过')
if 'unable to open database' in log_tail:
    issues.append('数据库连接失败')
if 'Conflict' in log_tail:
    issues.append('Telegram 冲突')
if 'No currency pair' in log_tail:
    issues.append('白名单为空')

# Print report
L = '\u3010'   # 【
R = '\u3011'   # 】
print('=== Hybrid Dry Run ===')
print(L + '\u8fdb\u7a0b' + R + proc_str)
print(L + '\u6301\u4ed3' + R + str(n_open) + '\u7b14', end='')
for t in open_trades:
    pair_short = t[1].split(':')[0]
    print(' | #{} {} rate={:.0f} sl={:.0f} tag={}'.format(t[0], pair_short, t[2], t[3], t[5]), end='')
print()
print(L + '\u5df2\u5e73\u4ed3' + R + '{}笔 | 胜率{:.0f}% | 总损益{:+.2f}%'.format(cw[0], wr, cw[3]))
if exit_dist:
    parts = ['{}({}笔{:+.1f}%)'.format(r[0], r[1], r[2]) for r in exit_dist]
    print('  Exit: ' + ' / '.join(parts))
if top_pair != 'N/A':
    print(L + 'OIPairList Top 10' + R + top_pair + ' (top)')
else:
    print(L + 'OIPairList Top 10' + R + 'N/A')
print(L + 'MSE Watchlist' + R + '{}个'.format(len(mse_wl)), end='')
if mse_wl:
    syms = ' / '.join(['{}({:.0f})'.format(r[0], r[2]) for r in mse_wl])
    print(' | ' + syms, end='')
print()
issues_str = '; '.join(issues) if issues else '\u65e0'
print(L + '\u6838\u5fc3\u95ee\u9898' + R + issues_str)
