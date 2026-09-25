#!/usr/bin/env python3
"""诊断：逐 episode 明细 + 验证可疑的 -87% 是否数据口径问题。"""
import csv, glob, os, re, statistics as st
from collections import defaultdict
from datetime import datetime, timedelta, timezone

UD = "/home/gyan/freqtrade/user_data"; CACHE = "/tmp/klines_cache"
NUM = re.compile(r"-?\d+(?:\.\d+)?")
def num(s):
    m = NUM.search(str(s).replace(",", "").replace("$", "")) if s else None
    return float(m.group()) if m else None

def kl(sym, tf):
    fn = f"{CACHE}/{sym}_{tf}.csv"
    return [tuple(map(float, l.split(","))) for l in open(fn) if l.strip()] if os.path.exists(fn) else []

# 榜上价格序列（独立数据源，用于交叉验证 K 线）
board = defaultdict(list)
for path, tc in [(f"{UD}/ai500_score_history_v3.csv", "fetched_at_utc")] + \
                [(f, "fetched_at_cst") for f in sorted(glob.glob(f"{UD}/ai500_local/score_*.csv"))]:
    for r in csv.DictReader(open(path, errors="replace")):
        t, px = (r.get(tc) or "").strip(), num(r.get("price"))
        if t and px: board[r["symbol"]].append((datetime.fromisoformat(t).replace(tzinfo=None), px))

EPS = [("ZEC","09-05 14:32"),("BULLA","09-05 15:16"),("BULLA","09-06 22:21"),("ZEC","09-08 04:12"),
       ("IOST","09-10 02:05"),("ZEC","09-10 17:45"),("ZEC","09-11 20:52"),("BR","09-15 02:44"),
       ("AIN","09-15 22:40"),("BR","09-16 21:17"),("BR","09-18 15:32"),("BR","09-19 18:18"),("PROM","09-25 09:44")]

print("=== 逐 episode：K 线 vs 榜上价格 交叉验证（入场点附近） ===")
print(f"  {'标的':<7}{'入场(CST)':<13}{'入场价(K线)':>12}{'榜上价':>11}{'一致?':>7}{'7d最低':>10}{'7d最高':>10}{'最大单根跌幅':>12}")
for sym, hm in EPS:
    t_ep = datetime.strptime("2026-" + hm, "%Y-%m-%d %H:%M")
    k15 = kl(sym, "15m")
    if not k15: continue
    t0 = int(t_ep.replace(tzinfo=timezone.utc).timestamp() * 1000)
    ent = next((b for b in k15 if b[0] >= t0), None)
    if not ent: continue
    ent_px = ent[4]
    bp = min(board[sym], key=lambda x: abs((x[0] - t_ep).total_seconds()))[1] if board.get(sym) else None
    win = [b for b in k15 if ent[0] <= b[0] <= ent[0] + 7 * 86400_000]
    lo = min(b[3] for b in win); hi = max(b[2] for b in win)
    worst_bar = min((b[4] / b[1] - 1) * 100 for b in win) if win else 0
    ok = "✓" if bp and abs(ent_px / bp - 1) < 0.02 else f"✗({bp})"
    print(f"  {sym:<7}{hm:<13}{ent_px:>12.6g}{bp if bp else float('nan'):>11.6g}{ok:>7}"
          f"{(lo/ent_px-1)*100:>9.1f}%{(hi/ent_px-1)*100:>9.1f}%{worst_bar:>11.1f}%")

print("\n=== 价格序列跳变检查（相邻 15m 收盘比值 < 0.7 或 > 1.4 视为可疑）===")
for sym in sorted({s for s, _ in EPS}):
    k15 = kl(sym, "15m")
    jumps = [(datetime.fromtimestamp(b[0]/1000, timezone.utc).strftime("%m-%d %H:%M"), round(p[4]/p[1], 3), round(b[4]/b[1], 3))
             for p, b in zip(k15, k15[1:]) if b[4] / max(1e-12, p[4]) < 0.7 or b[4] / max(1e-12, p[4]) > 1.4]
    print(f"  {sym:<7} 可疑跳变 {len(jumps)} 处: {jumps[:4]}")

print("\n=== 各 episode 的 7d 收益（K 线口径）===")
for sym, hm in EPS:
    t_ep = datetime.strptime("2026-" + hm, "%Y-%m-%d %H:%M")
    k15 = kl(sym, "15m")
    t0 = int(t_ep.replace(tzinfo=timezone.utc).timestamp() * 1000)
    ent = next((b for b in k15 if b[0] >= t0), None)
    if not ent: continue
    e = ent[4]
    row = [f"{sym:<7}{hm:<13}"]
    for h in (24, 48, 72, 168):
        b = next((x for x in k15 if x[0] >= ent[0] + h * 3600_000), None)
        row.append(f"{h}h={(b[4]/e-1)*100:+7.1f}%" if b else f"{h}h={'--':>7}")
    print("  " + "  ".join(row))
