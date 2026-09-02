import sqlite3
from datetime import datetime, timezone, timedelta

c = sqlite3.connect('/home/gyan/freqtrade/user_data/market.db')
cur = c.cursor()

# 周定义（CST 用户约定，但 DB ts 为 UTC epoch；周报周期按 UTC 对齐）
# 本周: 2026-08-24 00:00 ~ 2026-08-31 00:00 UTC
# 上周: 2026-08-17 00:00 ~ 2026-08-24 00:00 UTC
W_END   = int(datetime(2026,8,31,0,0,tzinfo=timezone.utc).timestamp())
W_START = int(datetime(2026,8,24,0,0,tzinfo=timezone.utc).timestamp())
LW_START= int(datetime(2026,8,17,0,0,tzinfo=timezone.utc).timestamp())

def latest_snapshot_per_symbol(start, end):
    """返回该周内每个 symbol 的最新一行（用 max ts）。"""
    cur.execute("""
        SELECT symbol, ts, grade, score, oi_usdt, vol_24h_usdt, oi_change_4h,
               funding_rate, cvd_rising, cvd_diverge, vol_surge, dim_count,
               is_long_trap, is_short_trap, is_squeeze, hf_funding_hot,
               hf_oi_price_div, hf_vol_explosion, hf_low_liquidity
        FROM coin_states_v2
        WHERE ts >= ? AND ts < ?
        AND (symbol, ts) IN (
            SELECT symbol, MAX(ts) FROM coin_states_v2
            WHERE ts >= ? AND ts < ? GROUP BY symbol
        )
    """, (start, end, start, end))
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    return cols, {r[0]: dict(zip(cols, r)) for r in rows}

def agg(snap):
    n = len(snap)
    if n == 0:
        return None
    grades = {}
    for s,d in snap.items():
        grades[d['grade']] = grades.get(d['grade'],0)+1
    scores = [d['score'] for d in snap.values() if d['score'] is not None]
    oi = [d['oi_usdt'] for d in snap.values() if d['oi_usdt'] is not None]
    vol = [d['vol_24h_usdt'] for d in snap.values() if d['vol_24h_usdt'] is not None]
    fund = [d['funding_rate'] for d in snap.values() if d['funding_rate'] is not None]
    oich = [d['oi_change_4h'] for d in snap.values() if d['oi_change_4h'] is not None]
    flags = {k: sum(1 for d in snap.values() if d.get(k)==1)
             for k in ['is_long_trap','is_short_trap','is_squeeze','hf_funding_hot','hf_oi_price_div','hf_vol_explosion','hf_low_liquidity']}
    scores_sorted = sorted(scores)
    def med(x):
        if not x: return None
        m=len(x)//2
        return x[m] if len(x)%2 else (x[m-1]+x[m])/2
    return {
        'n': n, 'grades': grades,
        'score_mean': sum(scores)/len(scores), 'score_med': med(scores),
        'score_max': max(scores), 'score_min': min(scores),
        'oi_mean': sum(oi)/len(oi), 'vol_mean': sum(vol)/len(vol),
        'fund_mean': sum(fund)/len(fund),
        'oich_mean': sum(oich)/len(oich),
        'flags': flags,
        'top': sorted(snap.items(), key=lambda kv: (kv[1]['score'] or -1), reverse=True)[:12],
    }

_, w  = latest_snapshot_per_symbol(W_START, W_END)
_, lw = latest_snapshot_per_symbol(LW_START, W_START)
w  = agg(w)
lw = agg(lw)

print("=== 覆盖度 ===")
print(f"本周 distinct 币数: {w['n']}")
print(f"上周 distinct 币数: {lw['n']}")
print(f"环比: {w['n']-lw['n']:+d}")

print("\n=== 等级分布 (grade) ===")
allg = sorted(set(list(w['grades'])+list(lw['grades'])), key=lambda g:-w['grades'].get(g,0))
print(f"{'grade':<12}{'本周':>6}{'上周':>6}{'Δ':>6}")
for g in allg:
    a=w['grades'].get(g,0); b=lw['grades'].get(g,0)
    print(f"{g:<12}{a:>6}{b:>6}{a-b:>+6}")

print("\n=== 评分 score ===")
print(f"均值 本周 {w['score_mean']:.2f}  上周 {lw['score_mean']:.2f}  Δ {w['score_mean']-lw['score_mean']:+.2f}")
print(f"中位 本周 {w['score_med']:.2f}  上周 {lw['score_med']:.2f}  Δ {w['score_med']-lw['score_med']:+.2f}")
print(f"最大 本周 {w['score_max']:.2f}  上周 {lw['score_max']:.2f}")
print(f"最小 本周 {w['score_min']:.2f}  上周 {lw['score_min']:.2f}")

print("\n=== 市场结构均值 ===")
print(f"OI均值(亿USDT) 本周 {w['oi_mean']/1e8:.3f}  上周 {lw['oi_mean']/1e8:.3f}  Δ {(w['oi_mean']-lw['oi_mean'])/1e8:+.3f}")
print(f"24h量均值(亿)   本周 {w['vol_mean']/1e8:.3f}  上周 {lw['vol_mean']/1e8:.3f}  Δ {(w['vol_mean']-lw['vol_mean'])/1e8:+.3f}")
print(f" funding均值    本周 {w['fund_mean']*1e6:.1f}ppm  上周 {lw['fund_mean']*1e6:.1f}ppm")
print(f" OI_4h变化均值  本周 {w['oich_mean']*100:.2f}%  上周 {lw['oich_mean']*100:.2f}%")

print("\n=== 风险/异动 flags 计数 ===")
for k in w['flags']:
    print(f"{k:<18} 本周 {w['flags'][k]:>4}  上周 {lw['flags'][k]:>4}  Δ {w['flags'][k]-lw['flags'][k]:>+4}")

print("\n=== 本周 Top12 by score ===")
for s,d in w['top']:
    print(f"{s:<14} score={d['score']:.1f} grade={d['grade']:<10} oi={d['oi_usdt']/1e8:.2f}亿 vol={d['vol_24h_usdt']/1e8:.2f}亿 fund={d['funding_rate']*1e6:.1f}ppm")

# 新进 / 退出 板块
cur.execute("SELECT DISTINCT symbol FROM coin_states_v2 WHERE ts>=? AND ts<?", (W_START, W_END))
w_sym = set(r[0] for r in cur.fetchall())
cur.execute("SELECT DISTINCT symbol FROM coin_states_v2 WHERE ts>=? AND ts<?", (LW_START, W_START))
lw_sym = set(r[0] for r in cur.fetchall())
entrants = sorted(w_sym - lw_sym)
departed = sorted(lw_sym - w_sym)
print("\n=== 新进 / 退出 板块 (按本周/上周是否有任何采样) ===")
print(f"新进 {len(entrants)}: {entrants}")
print(f"退出 {len(departed)}: {departed}")

# 评分变动（取两周均出现的币，对比各自周末快照 score）
cur.execute("""
SELECT symbol, score FROM coin_states_v2 WHERE ts>=? AND ts<?
AND (symbol,ts) IN (SELECT symbol,MAX(ts) FROM coin_states_v2 WHERE ts>=? AND ts<? GROUP BY symbol)
""", (W_START, W_END, W_START, W_END))
w_score = {r[0]: r[1] for r in cur.fetchall()}
cur.execute("""
SELECT symbol, score FROM coin_states_v2 WHERE ts>=? AND ts<?
AND (symbol,ts) IN (SELECT symbol,MAX(ts) FROM coin_states_v2 WHERE ts>=? AND ts<? GROUP BY symbol)
""", (LW_START, W_START, LW_START, W_START))
lw_score = {r[0]: r[1] for r in cur.fetchall()}
common = set(w_score) & set(lw_score)
movers = sorted(common, key=lambda s: (w_score[s] or 0)-(lw_score[s] or 0), reverse=True)
print("\n=== 评分变动 Top8 上升 ===")
for s in movers[:8]:
    print(f"{s:<14} {lw_score[s]:.1f} -> {w_score[s]:.1f}  ({ (w_score[s] or 0)-(lw_score[s] or 0):+.1f})")
print("=== 评分变动 Top8 下降 ===")
for s in movers[-8:][::-1]:
    print(f"{s:<14} {lw_score[s]:.1f} -> {w_score[s]:.1f}  ({ (w_score[s] or 0)-(lw_score[s] or 0):+.1f})")
