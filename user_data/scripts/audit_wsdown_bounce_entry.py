"""
TASK 42 预研：wsdown 入场前反弹幅度审计
======================================
目标：量化 v8_short_wsdown 每笔交易在入场前的最大反弹幅度，
      检验"大反弹后入场"是否显著劣化 WR 和收益。

结构判断等价：
  - 当前系统用 rolling(24h) 作为时间代理
  - 结构判断应为：入场前 down wave 位移是否仍然成立
  - 若反弹幅度 > X%，意味着空头努力已被部分抵消，结构存疑

方法：
  1. 加载最新回测 trades（v8_short_wsdown 笔）
  2. 加载 1H K 线数据
  3. 对每笔交易，在入场前 N 根 K 线内找最低价，计算反弹幅度：
     bounce = (entry_price - recent_low) / recent_low * 100
  4. 按反弹幅度分层（0-1%, 1-2%, 2-3%, 3%+），统计 WR 和 avg profit
"""
import zipfile, json, pandas as pd
from pathlib import Path

# ── 配置 ──────────────────────────────────────────────────────────────────
BT_ZIP   = 'user_data/backtest_results/backtest-result-2026-06-26_00-42-22.zip'
DATA_FILE = 'user_data/data/binance/futures/BTC_USDT_USDT-1h-futures.feather'

# 入场前回看窗口（根）：捕捉 "这次 down wave 从哪里开始" 的范围
LOOKBACK_BARS = 48   # 最多往前看 48 根 1H K线（2天），足够覆盖 wsdown 触发链路

# ── 加载回测交易 ─────────────────────────────────────────────────────────
def load_trades(zf_path):
    with zipfile.ZipFile(zf_path) as z:
        fname = [n for n in z.namelist()
                 if n.endswith('.json') and 'config' not in n and '.py' not in n][0]
        data = json.loads(z.read(fname))
    return list(data['strategy'].values())[0]['trades']

trades = load_trades(BT_ZIP)
wsdown = [t for t in trades if t.get('enter_tag') == 'v8_short_wsdown']
print(f'v8_short_wsdown 总笔数: {len(wsdown)}')

# ── 加载 1H K线 ───────────────────────────────────────────────────────────
df = pd.read_feather(DATA_FILE)
df['date'] = pd.to_datetime(df['date'], utc=True)
df = df.sort_values('date').reset_index(drop=True)
print(f'1H K线范围: {df["date"].iloc[0]} ~ {df["date"].iloc[-1]}  共 {len(df)} 根')

# ── 对每笔 wsdown 计算入场前反弹幅度 ──────────────────────────────────────
records = []
for t in wsdown:
    open_date = pd.to_datetime(t['open_date'], utc=True)
    entry     = t['open_rate']
    profit    = t['profit_ratio'] * 100
    is_win    = profit > 0
    exit_rsn  = t['exit_reason']

    # 找入场 bar 的位置
    idx = df.index[df['date'] <= open_date]
    if len(idx) == 0:
        continue
    i = idx[-1]

    # 往前 LOOKBACK_BARS 根 K 线内找最低价
    start_i = max(0, i - LOOKBACK_BARS)
    window  = df.iloc[start_i:i+1]
    if len(window) == 0:
        continue

    recent_low  = window['low'].min()
    recent_high = window['high'].max()

    # 反弹幅度 = 从低点到入场价的涨幅（越大 = 空头入场越晚，结构越弱）
    bounce_pct = (entry - recent_low) / recent_low * 100

    # 位移幅度 = 从高点到入场价的跌幅（越大 = 下跌越充分）
    drop_pct   = (recent_high - entry) / recent_high * 100

    records.append({
        'open_date':  open_date,
        'entry':      entry,
        'profit':     profit,
        'is_win':     is_win,
        'exit_rsn':   exit_rsn,
        'bounce_pct': bounce_pct,
        'drop_pct':   drop_pct,
        'recent_low': recent_low,
    })

df_r = pd.DataFrame(records)
print(f'\n有效分析笔数: {len(df_r)}')

# ── 分层统计 ──────────────────────────────────────────────────────────────
bins   = [0, 1.0, 2.0, 3.0, 5.0, 999]
labels = ['0-1%', '1-2%', '2-3%', '3-5%', '5%+']
df_r['bounce_bucket'] = pd.cut(df_r['bounce_pct'], bins=bins, labels=labels)

print('\n=== 入场前反弹幅度分层（反弹 = entry - recent_low）===')
print(f'{"反弹幅度":<10} {"笔数":>5} {"胜率":>7} {"avg profit":>11} {"avg profit(Win)":>15} {"avg profit(Loss)":>16}')
print('-' * 68)

for bucket in labels:
    grp = df_r[df_r['bounce_bucket'] == bucket]
    if len(grp) == 0:
        continue
    n       = len(grp)
    wr      = grp['is_win'].mean() * 100
    avg_p   = grp['profit'].mean()
    avg_w   = grp[grp['is_win']]['profit'].mean() if grp['is_win'].any() else 0
    avg_l   = grp[~grp['is_win']]['profit'].mean() if (~grp['is_win']).any() else 0
    print(f'{bucket:<10} {n:>5} {wr:>6.1f}% {avg_p:>+10.2f}% {avg_w:>+14.2f}% {avg_l:>+15.2f}%')

# ── 关键阈值：反弹 > X% 后入场的汇总 ────────────────────────────────────
print('\n=== 反弹阈值门控效果预估 ===')
print(f'{"阈值":<10} {"拒绝笔数":>8} {"拒绝率":>7} {"拒绝WR":>8} {"保留WR":>8} {"保留avg":>10}')
print('-' * 55)
for thr in [1.0, 1.5, 2.0, 2.5, 3.0, 4.0]:
    rejected = df_r[df_r['bounce_pct'] >= thr]
    kept     = df_r[df_r['bounce_pct'] <  thr]
    if len(kept) == 0:
        continue
    rej_wr  = rejected['is_win'].mean() * 100 if len(rejected) > 0 else float('nan')
    kept_wr = kept['is_win'].mean() * 100
    kept_av = kept['profit'].mean()
    print(f'>={thr:.1f}%  {len(rejected):>8} {len(rejected)/len(df_r)*100:>6.1f}% '
          f'{rej_wr:>7.1f}% {kept_wr:>7.1f}% {kept_av:>+9.2f}%')

# ── 亏损笔的反弹幅度分布 ────────────────────────────────────────────────
losses = df_r[~df_r['is_win']]
wins   = df_r[df_r['is_win']]
print(f'\n=== 亏损 vs 盈利的反弹幅度对比 ===')
print(f'亏损 {len(losses)} 笔: bounce 中位={losses["bounce_pct"].median():.2f}%  '
      f'均值={losses["bounce_pct"].mean():.2f}%  max={losses["bounce_pct"].max():.2f}%')
print(f'盈利 {len(wins)} 笔:  bounce 中位={wins["bounce_pct"].median():.2f}%  '
      f'均值={wins["bounce_pct"].mean():.2f}%  max={wins["bounce_pct"].max():.2f}%')

# ── 最差 10 笔（亏损最深）的反弹幅度 ────────────────────────────────────
print(f'\n=== 最差 10 笔（止损）入场前反弹幅度 ===')
worst = df_r.nsmallest(10, 'profit')[['open_date','entry','profit','bounce_pct','drop_pct','exit_rsn']]
print(worst.to_string(index=False))
