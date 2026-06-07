"""
TASK 28: 多空槽位交互行为验证
验证 wave_breakdown 短空与 ws_d/ws_e 长多在 max_open_trades=2 下的共存是否物理有序。
"""
import zipfile, json, pandas as pd, numpy as np
from pathlib import Path

# ── 加载最新回测结果（TASK 27）────────────────────────────────────
RESULTS_DIR = Path('user_data/backtest_results')
BASELINE_ZIP = RESULTS_DIR / 'backtest-result-2026-06-05_13-04-58.zip'  # TASK 24 基线
TASK27_ZIP   = RESULTS_DIR / 'backtest-result-2026-06-05_16-42-07.zip'  # TASK 27

def load_trades(zip_path):
    zf = zipfile.ZipFile(zip_path)
    fname = [f for f in zf.namelist() if f.endswith('.json') and 'meta' not in f][0]
    data = json.loads(zf.read(fname))
    df = pd.DataFrame(data['strategy']['WyckoffV8']['trades'])
    df['open_date']  = pd.to_datetime(df['open_date'], utc=True)
    df['close_date'] = pd.to_datetime(df['close_date'], utc=True)
    return df

df27 = load_trades(TASK27_ZIP)
df24 = load_trades(BASELINE_ZIP)

BAR = pd.Timedelta('1h')
START = pd.Timestamp('2022-01-01', tz='UTC')
END   = pd.Timestamp('2026-06-02', tz='UTC')
ALL_BARS = pd.date_range(START, END, freq='1h')
N_BARS = len(ALL_BARS)

print("=" * 70)
print("TASK 28：多空槽位交互行为验证")
print("=" * 70)

# ══════════════════════════════════════════════════════════════════
# 验证 1：双槽同时占用时间比例
# ══════════════════════════════════════════════════════════════════
print("\n【验证 1】双槽同时占用时间比例")
print("-" * 50)

# 对每个 bar，统计在场交易数量及方向
long_bars  = np.zeros(N_BARS, dtype=int)
short_bars = np.zeros(N_BARS, dtype=int)

bar_idx = {t: i for i, t in enumerate(ALL_BARS)}

for _, row in df27.iterrows():
    o = row['open_date'].floor('1h')
    c = row['close_date'].floor('1h')
    side = row.get('is_short', False)
    # 遍历持仓区间内所有 bar
    cur = o
    while cur < c:
        if cur in bar_idx:
            i = bar_idx[cur]
            if side:
                short_bars[i] += 1
            else:
                long_bars[i] += 1
        cur += BAR

both_occupied  = np.sum((long_bars > 0) & (short_bars > 0))
long_only      = np.sum((long_bars > 0) & (short_bars == 0))
short_only     = np.sum((long_bars == 0) & (short_bars > 0))
empty          = np.sum((long_bars == 0) & (short_bars == 0))
any_occupied   = N_BARS - empty

print(f"总 bar 数（1H）    : {N_BARS:,}")
print(f"空置（无持仓）      : {empty:,}  ({empty/N_BARS*100:.1f}%)")
print(f"仅多头              : {long_only:,}  ({long_only/N_BARS*100:.1f}%)")
print(f"仅空头              : {short_only:,}  ({short_only/N_BARS*100:.1f}%)")
print(f"多空同时在场 ★      : {both_occupied:,}  ({both_occupied/N_BARS*100:.2f}%)")
print(f"  占有仓时段的比例  : {both_occupied/max(any_occupied,1)*100:.2f}%")

# ══════════════════════════════════════════════════════════════════
# 验证 2：方向冲突事件 & 碰撞时间戳
# ══════════════════════════════════════════════════════════════════
print("\n【验证 2】方向冲突事件（做空信号触发时已有做多持仓）")
print("-" * 50)

shorts = df27[df27['is_short'] == True].copy()
longs  = df27[df27['is_short'] == False].copy()

conflicts = []
for _, s in shorts.iterrows():
    # 找在 short 开仓时刻正在持有的 long
    active_longs = longs[(longs['open_date'] <= s['open_date']) & (longs['close_date'] > s['open_date'])]
    if len(active_longs) > 0:
        for _, l in active_longs.iterrows():
            conflicts.append({
                'short_open'   : s['open_date'],
                'short_tag'    : s['enter_tag'],
                'short_profit' : s['profit_ratio'],
                'long_open'    : l['open_date'],
                'long_tag'     : l['enter_tag'],
                'long_profit'  : l['profit_ratio'],
                'long_still_open_h': (s['open_date'] - l['open_date']).total_seconds()/3600,
            })

print(f"short 开仓时已有 long 在场的碰撞事件数: {len(conflicts)}")
if conflicts:
    cdf = pd.DataFrame(conflicts)
    print(f"\n碰撞事件列表（short_open / long_open / 两者盈亏）：")
    print(cdf[['short_open','short_tag','short_profit','long_open','long_tag','long_profit']].to_string(index=False))
    print(f"\n碰撞期间 short 平均盈利 : {cdf['short_profit'].mean():.4f}")
    print(f"碰撞期间 long  平均盈利 : {cdf['long_profit'].mean():.4f}")
else:
    print("→ 零碰撞：short 开仓时从未与 long 同时在场。槽位完全隔离。")

# ══════════════════════════════════════════════════════════════════
# 验证 3：消失的 5 笔多头
# ══════════════════════════════════════════════════════════════════
print("\n【验证 3】基线 476L → TASK27 471L，消失的 5 笔多头追踪")
print("-" * 50)

longs24 = df24[df24['is_short'] == False].copy()
longs27 = longs[['open_date','close_date','open_rate','profit_ratio','enter_tag','exit_reason']].copy()

# 按 open_date + enter_tag 做 join，找在基线有但 TASK27 没有的
longs24_key = set(longs24['open_date'].dt.round('1h').astype(str))
longs27_key = set(longs27['open_date'].dt.round('1h').astype(str))

missing_keys = longs24_key - longs27_key
print(f"基线独有多头 open_date 数（去重）: {len(missing_keys)}")

missing_trades = longs24[longs24['open_date'].dt.round('1h').astype(str).isin(missing_keys)]
print(f"\n消失的多头明细：")
cols = ['open_date','close_date','open_rate','profit_ratio','enter_tag','exit_reason']
print(missing_trades[[c for c in cols if c in missing_trades.columns]].to_string(index=False))

if len(missing_trades) > 0:
    print(f"\n消失多头盈亏：")
    print(f"  平均 profit_ratio : {missing_trades['profit_ratio'].mean():.4f}")
    print(f"  亏损笔数          : {(missing_trades['profit_ratio'] < 0).sum()}")
    print(f"  最低单笔           : {missing_trades['profit_ratio'].min():.4f}")

    # 检查这些 bar 上 TASK27 是否有 short 在场
    print(f"\n在消失多头开仓时刻，TASK27 是否有空头在场：")
    for _, row in missing_trades.iterrows():
        t = row['open_date']
        active_s = shorts[(shorts['open_date'] <= t) & (shorts['close_date'] > t)]
        if len(active_s) > 0:
            for _, s in active_s.iterrows():
                print(f"  {t.strftime('%Y-%m-%d %H:%M')} 多头被拒 → 空头 {s['enter_tag']} 在场"
                      f"（{s['open_date'].strftime('%m-%d')} 开，profit={s['profit_ratio']:.4f}）")
        else:
            print(f"  {t.strftime('%Y-%m-%d %H:%M')} 多头消失，但无空头在场（需进一步查原因）")

# ══════════════════════════════════════════════════════════════════
# 验证 4：12 笔止损短空 与 long 的协同行为
# ══════════════════════════════════════════════════════════════════
print("\n【验证 4】12 笔止损短空 与 long 的协同行为")
print("-" * 50)

sl_shorts = shorts[shorts['exit_reason'] == 'stop_loss'].copy()
print(f"止损短空笔数: {len(sl_shorts)}")

for _, s in sl_shorts.iterrows():
    # 检查同期是否有 long 在场
    active_longs_during = longs[
        (longs['open_date'] < s['close_date']) &
        (longs['close_date'] > s['open_date'])
    ]
    if len(active_longs_during) > 0:
        for _, l in active_longs_during.iterrows():
            print(f"  Short {s['open_date'].strftime('%Y-%m-%d')} → {s['close_date'].strftime('%Y-%m-%d')}"
                  f" profit={s['profit_ratio']:.4f}"
                  f" | Long {l['open_date'].strftime('%Y-%m-%d')} profit={l['profit_ratio']:.4f}"
                  f" ← 方向反向同期持仓！")
    else:
        print(f"  Short {s['open_date'].strftime('%Y-%m-%d %H:%M')} → {s['close_date'].strftime('%Y-%m-%d %H:%M')}"
              f" profit={s['profit_ratio']:.4f} | 无同期 long，槽位隔离✓")

# ══════════════════════════════════════════════════════════════════
# 验证 5：资金利用曲线（月度粒度）
# ══════════════════════════════════════════════════════════════════
print("\n【验证 5】资金利用曲线（月度统计）")
print("-" * 50)

# 构建月度 bar 状态统计
monthly = pd.DataFrame({'bar': ALL_BARS})
monthly['ym'] = monthly['bar'].dt.to_period('M')
monthly['long']  = [long_bars[i]  for i in range(N_BARS)]
monthly['short'] = [short_bars[i] for i in range(N_BARS)]
monthly['both']  = ((monthly['long'] > 0) & (monthly['short'] > 0)).astype(int)
monthly['any']   = ((monthly['long'] > 0) | (monthly['short'] > 0)).astype(int)
monthly['empty_flag'] = ((monthly['long'] == 0) & (monthly['short'] == 0)).astype(int)

grp = monthly.groupby('ym').agg(
    total_bars=('bar','count'),
    both=('both','sum'),
    long_only=('long', lambda x: ((x > 0) & (monthly.loc[x.index,'short'] == 0)).sum()),
    short_only=('short', lambda x: ((x > 0) & (monthly.loc[x.index,'long'] == 0)).sum()),
    empty=('empty_flag','sum'),
).reset_index()
grp['both_pct']  = grp['both']  / grp['total_bars'] * 100
grp['util_pct']  = (grp['total_bars'] - grp['empty']) / grp['total_bars'] * 100

print(f"{'月份':<10} {'空置%':>7} {'仅多%':>7} {'仅空%':>7} {'双满%':>7} {'利用率%':>8}")
for _, r in grp.iterrows():
    lo = r['long_only'] / r['total_bars'] * 100
    so = r['short_only'] / r['total_bars'] * 100
    em = r['empty'] / r['total_bars'] * 100
    print(f"{str(r['ym']):<10} {em:>6.1f}% {lo:>6.1f}% {so:>6.1f}% {r['both_pct']:>6.2f}% {r['util_pct']:>7.1f}%")

print("\n── 全周期汇总 ──")
print(f"平均利用率（有任意持仓）: {(N_BARS - empty)/N_BARS*100:.1f}%")
print(f"wave_breakdown 新增有效空置填充: {short_only:,} bars ({short_only/N_BARS*100:.1f}%)")

# 对比基线：TASK 24 的利用率
long_bars_24 = np.zeros(N_BARS, dtype=int)
for _, row in df24[df24['is_short']==False].iterrows():
    cur = row['open_date'].floor('1h')
    while cur < row['close_date'].floor('1h'):
        if cur in bar_idx:
            long_bars_24[bar_idx[cur]] += 1
        cur += BAR

empty_24 = np.sum(long_bars_24 == 0)
print(f"\n基线 TASK24 利用率 : {(N_BARS-empty_24)/N_BARS*100:.1f}%  (空置 {empty_24/N_BARS*100:.1f}%)")
print(f"TASK27 利用率     : {(N_BARS-empty)/N_BARS*100:.1f}%  (空置 {empty/N_BARS*100:.1f}%)")
print(f"wave_breakdown 填充增量: +{(empty_24-empty)/N_BARS*100:.1f}pp")
