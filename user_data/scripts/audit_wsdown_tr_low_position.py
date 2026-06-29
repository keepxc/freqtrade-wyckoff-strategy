"""
TASK 42 预研：wsdown 入场价相对 TR Low 位置审计
================================================
结构性问题：
  空头（wsdown）入场价是否位于 TR Low（冰线）之上？
  - 入场在冰线之上 = 有向下空间，结构正确
  - 入场在冰线之下 = 在支撑下方做空，掉入空头陷阱（Spring 区域）

TR Low 代理：
  WyckoffV8 custom_stoploss 对 wsdown 短仓设 stop = v8_tr_low
  Freqtrade 空头的 stop_loss_abs 是止损触发价（高于开仓价）
  因此：stop_loss_abs ≈ TR Low（冰线）

位置关系：
  - stop_margin = (stop_loss_abs - open_rate) / open_rate * 100
  - 止损空间 > 0 始终成立（空头止损在上方）
  - 止损空间越小 = 入场越靠近甚至穿越 TR Low
  - stop_margin < ~0.5% 可视为"入场已在冰线附近或以下"
"""
import zipfile, json, pandas as pd, numpy as np

BT_ZIP = 'user_data/backtest_results/backtest-result-2026-06-26_00-42-22.zip'

# ── 加载交易 ──────────────────────────────────────────────────────────────
def load_trades(zf_path):
    with zipfile.ZipFile(zf_path) as z:
        fname = [n for n in z.namelist()
                 if n.endswith('.json') and 'config' not in n and '.py' not in n][0]
        data = json.loads(z.read(fname))
    return list(data['strategy'].values())[0]['trades']

trades = load_trades(BT_ZIP)
wsdown = [t for t in trades if t.get('enter_tag') == 'v8_short_wsdown']
print(f'v8_short_wsdown 总笔数: {len(wsdown)}')

records = []
for t in wsdown:
    entry      = t['open_rate']
    sl_abs     = t['stop_loss_abs']       # 空头止损价 ≈ TR Low
    profit     = t['profit_ratio'] * 100
    is_win     = profit > 0
    exit_rsn   = t['exit_reason']
    open_date  = t['open_date']

    # 止损空间 = (TR Low - entry) / entry * 100
    # 空头：TR Low 在入场价上方，止损空间 > 0
    stop_margin = (sl_abs - entry) / entry * 100

    records.append({
        'open_date':    open_date,
        'entry':        entry,
        'tr_low_proxy': sl_abs,
        'stop_margin':  stop_margin,   # TR Low 距入场价的涨幅%
        'profit':       profit,
        'is_win':       is_win,
        'exit_rsn':     exit_rsn,
    })

df = pd.DataFrame(records)

print(f'\n止损空间（TR Low 距入场价）统计:')
print(f'  中位数:  {df["stop_margin"].median():.2f}%')
print(f'  均值:    {df["stop_margin"].mean():.2f}%')
print(f'  最小值:  {df["stop_margin"].min():.2f}%')
print(f'  最大值:  {df["stop_margin"].max():.2f}%')
print(f'  分位数:  p10={df["stop_margin"].quantile(0.1):.2f}%  '
      f'p25={df["stop_margin"].quantile(0.25):.2f}%  '
      f'p75={df["stop_margin"].quantile(0.75):.2f}%')

# ── 分层：按止损空间（即入场与 TR Low 的距离）─────────────────────────────
bins   = [0, 0.5, 1.0, 2.0, 4.0, 999]
labels = ['<0.5%（危险：贴近/穿越冰线）', '0.5-1%', '1-2%', '2-4%', '4%+（安全）']
df['margin_bucket'] = pd.cut(df['stop_margin'], bins=bins, labels=labels)

print('\n=== 入场与 TR Low 距离分层 ===')
print(f'{"距离区间":<30} {"笔数":>5} {"胜率":>7} {"avg profit":>11} {"avg(Win)":>10} {"avg(Loss)":>10}')
print('-' * 78)
for bucket in labels:
    grp = df[df['margin_bucket'] == bucket]
    if len(grp) == 0:
        continue
    n     = len(grp)
    wr    = grp['is_win'].mean() * 100
    avg_p = grp['profit'].mean()
    avg_w = grp[grp['is_win']]['profit'].mean() if grp['is_win'].any() else float('nan')
    avg_l = grp[~grp['is_win']]['profit'].mean() if (~grp['is_win']).any() else float('nan')
    print(f'{bucket:<30} {n:>5} {wr:>6.1f}% {avg_p:>+10.2f}% {avg_w:>+9.2f}% {avg_l:>+9.2f}%')

# ── 关键门控测试：拒绝止损空间过小的入场 ───────────────────────────────────
print('\n=== 止损空间门控效果预估 ===')
print(f'{"最小止损空间":>12} {"拒绝笔数":>8} {"拒绝率":>7} {"拒绝WR":>8} {"保留WR":>8} {"保留avg":>10}')
print('-' * 60)
for thr in [0.3, 0.5, 0.8, 1.0, 1.5, 2.0]:
    rejected = df[df['stop_margin'] < thr]
    kept     = df[df['stop_margin'] >= thr]
    if len(kept) == 0:
        continue
    rej_wr  = rejected['is_win'].mean() * 100 if len(rejected) > 0 else float('nan')
    kept_wr = kept['is_win'].mean() * 100
    kept_av = kept['profit'].mean()
    print(f'>={thr:.1f}%        {len(rejected):>8} {len(rejected)/len(df)*100:>6.1f}% '
          f'{rej_wr:>7.1f}% {kept_wr:>7.1f}% {kept_av:>+9.2f}%')

# ── 亏损 vs 盈利的止损空间对比 ────────────────────────────────────────────
losses = df[~df['is_win']]
wins   = df[df['is_win']]
print(f'\n=== 亏损 vs 盈利的 TR Low 距离对比 ===')
print(f'亏损 {len(losses)} 笔: 止损空间 中位={losses["stop_margin"].median():.2f}%  '
      f'均值={losses["stop_margin"].mean():.2f}%')
print(f'盈利 {len(wins)} 笔:  止损空间 中位={wins["stop_margin"].median():.2f}%  '
      f'均值={wins["stop_margin"].mean():.2f}%')

# ── 极端案例：止损空间 < 0.5% 的危险入场 ────────────────────────────────
danger = df[df['stop_margin'] < 0.5].sort_values('stop_margin')
print(f'\n=== 危险入场（止损空间 < 0.5%，贴近/穿越冰线）共 {len(danger)} 笔 ===')
if len(danger) > 0:
    print(danger[['open_date','entry','tr_low_proxy','stop_margin','profit','exit_rsn']].to_string(index=False))

# ── 最差 10 笔的止损空间 ─────────────────────────────────────────────────
print(f'\n=== 最差 10 笔（亏损最深）的 TR Low 距离 ===')
worst = df.nsmallest(10, 'profit')[['open_date','entry','tr_low_proxy','stop_margin','profit','exit_rsn']]
print(worst.to_string(index=False))
