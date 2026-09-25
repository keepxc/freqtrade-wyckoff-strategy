#!/usr/bin/env python3
"""出场规则模拟 v2（修正时区）+ 用 bot 实际成交价校验。

关键修正：
- score 文件时间戳是 CST（+08:00）墙面时间 → 必须按 Asia/Shanghai 解释（v1 误当 UTC，偏 8 小时）
- 入场口径对齐 V3 实盘：**信号后的第一个 4h 边界**（DB 里 12 笔成交时间多为 HH:01）
- 止损成交价按实盘证据取"止损位 -0.5pp 滑点"（实盘 7 笔 stop 为 -5.07%~-6.57%）
"""
import csv, glob, os, re, shutil, sqlite3, statistics as st, tempfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

UD = "/home/gyan/freqtrade/user_data"; CACHE = "/tmp/klines_cache"
CST = ZoneInfo("Asia/Shanghai"); UTC = timezone.utc
NUM = re.compile(r"-?\d+(?:\.\d+)?")
def num(s):
    m = NUM.search(str(s).replace(",", "").replace("$", "")) if s else None
    return float(m.group()) if m else None
def kl(sym, tf):
    fn = f"{CACHE}/{sym}_{tf}.csv"
    return [tuple(map(float, l.split(","))) for l in open(fn) if l.strip()] if os.path.exists(fn) else []

rows = []
for path, tc in [(f"{UD}/ai500_score_history_v3.csv", "fetched_at_utc")] + \
                [(f, "fetched_at_cst") for f in sorted(glob.glob(f"{UD}/ai500_local/score_*.csv"))]:
    for r in csv.DictReader(open(path, errors="replace")):
        t, sc = (r.get(tc) or "").strip(), num(r.get("score"))
        if t and sc is not None:
            rows.append((datetime.fromisoformat(t).astimezone(CST).replace(tzinfo=None), r["symbol"], sc))
rows.sort()
bysym = defaultdict(list)
for t, s, sc in rows: bysym[s].append((t, sc))

eps = []
for s, rs in bysym.items():
    prev = None; last = None
    for i, (t, sc) in enumerate(rs):
        gap = (t - rs[i-1][0]).total_seconds() / 60 if i else None
        if sc >= 80 and (prev is None or prev < 80 or (gap and gap > 60)):
            if last is None or (t - last) > timedelta(hours=24):
                eps.append((s, t)); last = t
        prev = sc
eps.sort(key=lambda x: x[1])
print(f"episode = {len(eps)}（时区已按 CST 解释）")

FEE_SIDE = 0.0005; SLIP = 0.005
def simulate(sym, t_sig, stop_pct, exit_tf, max_hold_h, no_candle=False, fixed_hold=False,
             entry_mode="boundary", profit_only=False):
    k15, kx = kl(sym, "15m"), kl(sym, exit_tf)
    if not k15: return None
    t_sig_ms = int(t_sig.replace(tzinfo=CST).timestamp() * 1000)
    if entry_mode == "boundary":
        b0 = next((b for b in k15 if (b[0] + 900_000) % 14_400_000 == 0 and b[0] + 900_000 >= t_sig_ms), None)
        if b0 is None: b0 = next((b for b in k15 if b[0] >= t_sig_ms), None)
    else:
        b0 = next((b for b in k15 if b[0] >= t_sig_ms), None)
    if b0 is None: return None
    entry, t_ent = b0[4], b0[0]
    stop = entry * (1 - stop_pct) if stop_pct else -1e18
    cap = t_ent + max_hold_h * 3600_000
    tf_h = {"4h": 4, "1d": 24}[exit_tf] if not no_candle else 4
    for b in k15:
        if b[0] <= t_ent: continue
        if b[0] > cap:
            return ((b[1] / entry - 1) * 100 - FEE_SIDE * 200, f"cap{max_hold_h}h", (cap - t_ent) / 3600_000)
        if b[3] <= stop:
            return (-(stop_pct * 100) - SLIP * 100 - FEE_SIDE * 200, f"stop", (b[0] - t_ent) / 3600_000)
        if fixed_hold:
            if b[0] >= t_ent + max_hold_h * 3600_000 * 0.999:
                return ((b[4] / entry - 1) * 100 - FEE_SIDE * 200, f"fixed", max_hold_h)
            continue
        if no_candle: continue
        close_ms = b[0] + 900_000
        cd = datetime.fromtimestamp(close_ms / 1000, UTC)
        if close_ms % (tf_h * 3600_000) == 0:
            kk = next((x for x in reversed(kx) if x[0] + tf_h * 3600_000 <= close_ms), None)
            if kk and kk[4] < kk[1]:
                ret = (b[4] / entry - 1) * 100
                if profit_only and ret <= 0: continue
                return (ret - FEE_SIDE * 200, f"{exit_tf}阴线", (b[0] - t_ent) / 3600_000)
    return None

# --- 校验：与 bot 实际成交对照 ---
db = f"{UD}/tradesv3.ai500_v3_dryrun.sqlite"; d = tempfile.mkdtemp()
for s in ("", "-wal", "-shm"):
    if os.path.exists(db + s): shutil.copy2(db + s, os.path.join(d, "x.sqlite" + s))
con = sqlite3.connect(os.path.join(d, "x.sqlite"))
print("\n=== 校验：模拟入场价 vs bot 实际开仓价 ===")
print(f"  {'#':<4}{'pair':<16}{'bot 开仓(CST)':<18}{'bot 价':>10}{'模拟价':>10}{'偏差':>8}{'bot 出场':<18}{'bot 收益':>9}{'模拟出场':>10}{'模拟收益':>9}")
for tid, pair, od, orate, cd, crate, cp, reason in con.execute(
        "select id,pair,open_date,open_rate,close_date,close_rate,close_profit,exit_reason from trades order by open_date"):
    sym = pair.split("/")[0]
    t_utc = datetime.fromisoformat(od).replace(tzinfo=UTC)
    t_cst = t_utc.astimezone(CST).replace(tzinfo=None)
    sim = simulate(sym, t_cst - timedelta(minutes=1), 0.05, "4h", 48, entry_mode="immediate")
    sim2 = simulate(sym, t_cst - timedelta(minutes=1), 0.05, "4h", 48, entry_mode="boundary")
    k15 = kl(sym, "15m")
    b = next((x for x in k15 if x[0] >= int(t_utc.timestamp() * 1000)), None)
    real_px = b[4] if b else float("nan")
    dev = (real_px / orate - 1) * 100 if orate else float("nan")
    s1 = f"{sim[0]:+.2f}%" if sim else "--"
    s2 = f"{sim2[0]:+.2f}%" if sim2 else "--"
    print(f"  #{tid:<3}{pair:<16}{t_cst:%m-%d %H:%M:%S}  {orate:>10.6g}{real_px:>10.6g}{dev:>7.1f}%"
          f"  {reason:<18}{cp*100:>+8.2f}%{s1:>10}{s2:>9}")

RULES = [
    ("V3 现行：4h阴线 / -5% / 48h", 0.05, "4h", 48, {}),
    ("① 上限 48h → 72h", 0.05, "4h", 72, {}),
    ("② 去 4h 阴线（仅 -5% / 72h）", 0.05, "4h", 72, {"no_candle": True}),
    ("③ 去 4h 阴线（仅 -5% / 7d）", 0.05, "4h", 168, {"no_candle": True}),
    ("④ 改日线阴线 / -5% / 72h", 0.05, "1d", 72, {}),
    ("⑤ 止损 -10% / 4h阴线 / 72h", 0.10, "4h", 72, {}),
    ("⑥ 无止损：4h阴线 / 7d", 0.0, "4h", 168, {}),
    ("⑦ 无止损无阴线：固定 48h", 0.0, "4h", 48, {"fixed_hold": True}),
    ("⑧ 无止损无阴线：固定 7d", 0.0, "4h", 168, {"fixed_hold": True}),
]
print(f"\n=== 出场规则模拟（入场=信号后第一个 4h 边界，n={len(eps)}）===")
print(f"  {'规则':<30}{'n':>4}{'中位':>9}{'均值':>9}{'胜率':>7}{'累计':>10}{'最差':>8}{'均持仓h':>8}  出场")
for name, sp, tf, mh, kw in RULES:
    res = [r for s, t in eps if (r := simulate(s, t, sp, tf, mh, **kw))]
    if not res: continue
    rets = [r[0] for r in res]; rsn = defaultdict(int)
    for r in res: rsn[r[1]] += 1
    print(f"  {name:<30}{len(rets):>4}{st.median(rets):>8.2f}%{st.mean(rets):>8.2f}%"
          f"{sum(1 for x in rets if x>0)/len(rets)*100:>6.0f}%{sum(rets):>9.1f}%{min(rets):>7.1f}%"
          f"{st.mean(r[2] for r in res):>8.1f}  {dict(rsn)}")

print("\n=== 配对分析：同一 episode、同一入场，逐笔差值（比独立样本更有效）===")
import random
random.seed(11)
base = {s_: simulate(s_, t, 0.05, "4h", 48) for s_, t in eps}
for name, sp, tf, mh, kw in RULES[1:5] + RULES[6:]:
    diffs = []
    for s_, t in eps:
        a, b = base.get(s_), simulate(s_, t, sp, tf, mh, **kw)
        if a and b: diffs.append(b[0] - a[0])
    if len(diffs) < 5: continue
    boot = sorted(st.mean(random.choices(diffs, k=len(diffs))) for _ in range(4000))
    lo, hi = boot[int(len(boot)*.05)], boot[int(len(boot)*.95)]
    flag = "跨零→不可判定" if lo <= 0 <= hi else ("优于现行" if lo > 0 else "劣于现行")
    print(f"  {name:<30} 配对 n={len(diffs):<3} 均值差 {st.mean(diffs):+7.2f}pp  中位差 {st.median(diffs):+7.2f}pp"
          f"  90%CI [{lo:+.1f}, {hi:+.1f}]  {flag}")

print("\n注：n≤13、含死相位、15m 低点近似止损、右删失已排除；探索性，禁止作为上线依据")
