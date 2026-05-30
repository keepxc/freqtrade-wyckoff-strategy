#!/home/gyan/freqtrade/.venv/bin/python3
"""
HybridTraderV1 Watchdog — 监控开仓事件
每 N 分钟跑一次，检测到新开仓时输出消息，由 cron 传递。
"""
import sqlite3, json, os, time, re
from pathlib import Path

# ── 路径 ──────────────────────────────────────
HYBRID_DB = '/home/gyan/freqtrade/user_data/tradesv3.hybrid_dryrun.sqlite'
MARKET_DB = '/home/gyan/freqtrade/user_data/market.db'
LOG_PATH  = '/home/gyan/freqtrade/logs/hybrid-dryrun.log'
STATE_FILE = '/home/gyan/freqtrade/user_data/.hybrid_watchdog_state.json'

# ── 状态文件：记录已通知过的 trade ID ──────────
def load_state() -> set:
    try:
        with open(STATE_FILE) as f:
            return set(json.load(f).get('notified_ids', []))
    except:
        return set()

def save_state(ids: set):
    Path(STATE_FILE).parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, 'w') as f:
        json.dump({'notified_ids': list(ids), 'updated_ts': int(time.time())}, f)

# ── 检查新开仓 ──────────────────────────────
notified = load_state()
conn = sqlite3.connect(HYBRID_DB)
new_trades = list(conn.execute('''
    SELECT id, pair, open_rate, stop_loss, open_date, enter_tag, amount
    FROM trades WHERE is_open=1
'''))
conn.close()

# 找到未通知的新开仓
unnotified = [t for t in new_trades if t[0] not in notified]
if not unnotified:
    # 无新开仓 → 静默退出（cron 无输出 = 不通知）
    # 但也更新已通知列表（防止已关闭的旧 ID 残留）
    save_state({t[0] for t in new_trades})
    exit(0)

# ── 有新开仓 → 构建通知消息 ─────────────────
lines = []
lines.append('HybridTraderV1 新开仓')
lines.append('')

for t in unnotified:
    pair_short = t[1].split(':')[0]
    lines.append('  {} #{}'.format(t[1], t[0]))
    lines.append('    入场价: {:.2f}'.format(t[2]))
    lines.append('    止损:   {:.2f} ({:.1f}%)'.format(t[3], (t[3]-t[2])/t[2]*100))
    lines.append('    仓位:   {:.4f}'.format(t[6]))
    lines.append('    时间:   {}'.format(t[4]))
    lines.append('    标签:   {}'.format(t[5] if t[5] else 'N/A'))
    lines.append('')

# ── 附加上下文 ─────────────────────────────
# MSE watchlist
try:
    conn2 = sqlite3.connect(MARKET_DB)
    wl = list(conn2.execute('SELECT symbol, grade, score FROM watchlist ORDER BY score DESC'))
    conn2.close()
    if wl:
        lines.append('MSE Watchlist:')
        for r in wl:
            lines.append('  {}  grade={} score={:.0f}'.format(r[0], r[1], r[2]))
except:
    pass

# OIPairList top
try:
    log_r = os.popen('grep "OIPairList: Generated" {} | tail -1'.format(LOG_PATH)).read()
    m = re.search(r'Top: ([^ ]+)', log_r)
    if m:
        lines.append('')
        lines.append('OIPairList Top: {}'.format(m.group(1)))
except:
    pass

# ── 输出 ─────────────────────────────────────
print('\n'.join(lines))

# ── 更新状态 ─────────────────────────────────
all_ids = {t[0] for t in new_trades}
save_state(all_ids)
