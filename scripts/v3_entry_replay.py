#!/usr/bin/env python3
"""
V3 入场重放 —— 止损档与标的波动率是否错配的描述性测量
=========================================================

**探索性——非结论，禁止引用为证据。**

本脚本**不搜索参数、不做判定**，只量一个分布，回答一个事实问题：

    V3 按 score 榜 top3 + score>=80 入场后，**在第一根 4h K 走完之前**，
    价格最多向不利方向走多远（MAE，Maximum Adverse Excursion）？

**为什么要量这个**（2026-09-18 立）：V3 实盘 13 天仅 2 笔，**2/2 都死在 `stoploss=-5%`**，
持仓 2h19m / 4h27m，设计中的「4h 收阴线出场」一次都没真正决定过结果。
若 MAE 分布的主体就超过 5%，则 `-5%` 止损与 timeframe=4h 属**结构性错配**——
仓位在出场逻辑有机会发言之前就被打掉，策略的真实出场规则是止损而非阴线。
这是机制问题，不是「亏了所以调参」，故先测量、后决策。

**口径与已知局限（必须连同结果一起读）**：
  · 入场时刻由 `ai500_score_history_v3.csv` 重建。该榜**不可重建**（教训 7），
    起点 = 开采日 2026-09-05，所以样本上限就是十几天，天花板改不了。
  · 默认**关闭门③（僵尸过滤）**——这正是要测的对照面；`--zombie-gate` 可开。
  · 同一标的在连续多个 4h 边界重复合格会产生多个事件，它们**共享同一条价格路径**，
    `名义样本数 ≠ 有效样本数`（防过拟合 #5）。故同时输出「逐事件」与「逐 episode
    （连续合格段只取首个）」两套分布，**结论只能读 episode 那套**。
  · 价格来自币安官方归档 `data.binance.vision`（逐日全量、可重放）。
    当日文件次日发布，故最后一天通常缺，脚本会明确报出跳过了哪些。

用法：
    python3 scripts/v3_entry_replay.py --score-history <ai500_score_history_v3.csv>
    python3 scripts/v3_entry_replay.py --score-history ... --zombie-gate <ai500_zombie_check.csv>
只读；除 K 线缓存外不写任何文件。
"""
from __future__ import annotations

import argparse
import bisect
import collections
import csv
import io
import os
import statistics
import sys
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(REPO, "user_data", "vision_cache")
VISION = ("https://data.binance.vision/data/futures/um/daily/klines"
          "/{sym}/1m/{sym}-1m-{day}.zip")

# 策略常量（与 AI500V3ScoreHold 对齐；改了那边这里要同步）
TOP_N = 3
MIN_SCORE = 80.0
TIMEFRAME_H = 4
STOPLOSS = -0.05


# ---------------------------------------------------------------- K 线

def fetch_1m(symbol: str, day) -> dict:
    """取某币某日的 1m K 线，返回 {open_time_utc: (o,h,l,c)}。失败返回 {}。"""
    sym = f"{symbol}USDT"
    key = f"{sym}-1m-{day}.zip"
    path = os.path.join(CACHE, key)
    if not os.path.exists(path):
        os.makedirs(CACHE, exist_ok=True)
        url = VISION.format(sym=sym, day=day)
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                blob = r.read()
        except Exception as e:                      # 归档缺当日 / 该币无合约
            print(f"  [skip] {sym} {day}: {e.__class__.__name__} {e}", file=sys.stderr)
            return {}
        with open(path, "wb") as f:
            f.write(blob)
    out = {}
    with zipfile.ZipFile(path) as z:
        with z.open(z.namelist()[0]) as fh:
            for row in csv.reader(io.TextIOWrapper(fh, "utf-8")):
                if not row or not row[0].strip():
                    continue
                try:
                    ot = int(row[0])
                except ValueError:                  # 新版归档带表头
                    continue
                # 币安 2025 起部分归档改用微秒，按量级判
                ts = ot / 1e6 if ot > 1e14 else ot / 1e3
                out[datetime.fromtimestamp(ts, timezone.utc)] = (
                    float(row[1]), float(row[2]), float(row[3]), float(row[4]))
    return out


# ---------------------------------------------------------------- 入场重建

def load_snapshots(path):
    snaps = collections.defaultdict(list)
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        for r in csv.DictReader(f):
            if r.get("fetched_at_utc"):
                t = datetime.fromisoformat(r["fetched_at_utc"]).astimezone(timezone.utc)
                snaps[t].append(r)
    return snaps


def load_zombie(path):
    """(快照时刻, symbol) -> tradable。用于 --zombie-gate 对照。"""
    z = {}
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        for r in csv.DictReader(f):
            if r.get("fetched_at_utc"):
                t = datetime.fromisoformat(r["fetched_at_utc"]).astimezone(timezone.utc)
                z[(t, r["symbol"])] = r["tradable"] == "1"
    return z


def entry_events(snaps, zgate=None, since=None):
    """枚举 4h 边界上的入场事件。返回 [(边界时刻, symbol, score)]。

    `since`：只保留该时刻之后的事件。**开/关门③ 做对比时必须用**——
    `ai500_zombie_check.csv` 的覆盖（2026-09-11 16:53 UTC 起）比 score 榜
    （09-05）晚 6 天，不对齐窗口就会把「没有僵尸记录」误读成「被僵尸过滤拦下」，
    把门③的杀伤力夸大成 58→3。
    """
    ts = sorted(snaps)
    t0 = ts[0].replace(minute=0, second=0, microsecond=0)
    t0 -= timedelta(hours=t0.hour % TIMEFRAME_H)
    events, t = [], t0
    while t <= ts[-1]:
        if t >= ts[0] and (since is None or t >= since):
            i = bisect.bisect_right(ts, t) - 1
            if i >= 0 and (t - ts[i]).total_seconds() <= 3600:
                snap_t = ts[i]
                top = sorted(snaps[snap_t], key=lambda r: -float(r["score"]))[:TOP_N]
                for r in top:
                    if float(r["score"]) < MIN_SCORE:
                        continue
                    if zgate is not None and not zgate.get((snap_t, r["symbol"]), False):
                        continue
                    events.append((t, r["symbol"], float(r["score"])))
        t += timedelta(hours=TIMEFRAME_H)
    return events


def to_episodes(events):
    """连续 4h 边界上同一标的的重复合格压成一个 episode，只留首个事件。

    同一 episode 内各事件共享同一条价格路径，不是独立样本（防过拟合 #5）。
    """
    by_sym = collections.defaultdict(list)
    for T, sym, sc in events:
        by_sym[sym].append((T, sc))
    first = []
    for sym, lst in by_sym.items():
        lst.sort()
        prev = None
        for T, sc in lst:
            if prev is None or (T - prev) > timedelta(hours=TIMEFRAME_H):
                first.append((T, sym, sc))
            prev = T
    return sorted(first)


# ---------------------------------------------------------------- 测量

def measure(T, symbol):
    """返回 (entry, mae, mfe, bar_ret, hit_stop_before_close)。数据缺失返回 None。"""
    kl = fetch_1m(symbol, T.date())
    end = T + timedelta(hours=TIMEFRAME_H)
    if end.date() != T.date():
        kl.update(fetch_1m(symbol, end.date()))
    if not kl:
        return None
    # 入场价 = 边界那一分钟的开盘（freqtrade 在 4h K 收盘后的下一根即刻下单）
    entry_bar = kl.get(T)
    if entry_bar is None:
        return None
    entry = entry_bar[0]
    window = [(t, v) for t, v in kl.items() if T <= t < end]
    if len(window) < TIMEFRAME_H * 60 * 0.8:        # 缺超过 20% 的分钟数不算
        return None
    window.sort()
    lows = [v[2] for _, v in window]
    highs = [v[1] for _, v in window]
    mae = min(lows) / entry - 1.0
    mfe = max(highs) / entry - 1.0
    bar_ret = window[-1][1][3] / entry - 1.0
    # 止损是否在这根 4h K 走完前就被打掉（用 low 触及判定，与实时 ticker 口径近似）
    hit = False
    for _, v in window:
        if v[2] / entry - 1.0 <= STOPLOSS:
            hit = True
            break
    return entry, mae, mfe, bar_ret, hit


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * q))]


def report(label, rows):
    if not rows:
        print(f"\n{label}: 无可测事件。")
        return
    maes = [r["mae"] for r in rows]
    hits = sum(1 for r in rows if r["hit"])
    bars = [r["bar_ret"] for r in rows]
    print(f"\n{'='*84}\n{label}（n={len(rows)}）\n{'='*84}")
    print(f"  MAE（入场后第一根 4h K 内最大不利偏移）")
    print(f"    中位 {statistics.median(maes)*100:7.2f}%   "
          f"p25 {pct(maes,0.25)*100:7.2f}%   p75 {pct(maes,0.75)*100:7.2f}%   "
          f"最差 {min(maes)*100:7.2f}%   最好 {max(maes)*100:7.2f}%")
    print(f"  ★ MAE 触及 -5%（即止损先于出场逻辑生效）: {hits}/{len(rows)} = "
          f"{hits/len(rows)*100:.1f}%")
    print(f"  该 4h K 的收益（若无止损、持满一根）: 中位 {statistics.median(bars)*100:+.2f}%  "
          f"正收益占比 {sum(1 for b in bars if b>0)/len(bars)*100:.1f}%")
    print(f"\n  MAE 分档:")
    edges = [(0, -0.02), (-0.02, -0.05), (-0.05, -0.10), (-0.10, -1)]
    for hi, lo in edges:
        n = sum(1 for m in maes if lo < m <= hi)
        print(f"    {hi*100:>5.0f}% ~ {lo*100:>5.0f}%  {n:>3}  ({n/len(maes)*100:5.1f}%) "
              + "█" * int(n / len(maes) * 40))
    print(f"\n  逐事件:")
    for r in rows:
        mark = "  ← 止损" if r["hit"] else ""
        print(f"    {r['T']:%m-%d %H:%M}  {r['symbol']:<8} score={r['score']:6.2f}  "
              f"entry={r['entry']:<12.6g} MAE={r['mae']*100:7.2f}%  "
              f"MFE={r['mfe']*100:+7.2f}%  4hK={r['bar_ret']*100:+7.2f}%{mark}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--score-history", required=True)
    ap.add_argument("--zombie-gate", help="给了就开门③做对照")
    ap.add_argument("--since", help="只测该 UTC 时刻之后的入场，如 2026-09-11T16:53。"
                                    "开/关门③ 对比时必须用，见 entry_events 文档")
    args = ap.parse_args()
    since = (datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
             if args.since else None)

    print("=" * 84)
    print("V3 入场重放 —— 探索性，非结论，禁止引用为证据")
    print(f"生成: {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC")
    print("=" * 84)

    snaps = load_snapshots(args.score_history)
    ts = sorted(snaps)
    print(f"score 榜快照 {len(snaps)} 个   {ts[0]:%Y-%m-%d %H:%M} ~ {ts[-1]:%Y-%m-%d %H:%M} UTC")

    zgate = load_zombie(args.zombie_gate) if args.zombie_gate else None
    events = entry_events(snaps, zgate, since)
    episodes = to_episodes(events)
    print(f"入场事件 {len(events)} 个 → 压成 {len(episodes)} 个 episode"
          f"（门③ {'开' if zgate else '关'}"
          + (f"，窗口 >= {since:%Y-%m-%d %H:%M} UTC" if since else "") + "）")

    skipped = []
    for name, evs in (("逐 episode（可读作结论的那套）", episodes),
                      ("逐事件（重复路径，仅供对照，勿据此下结论）", events)):
        rows = []
        for T, sym, sc in evs:
            m = measure(T, sym)
            if m is None:
                skipped.append((T, sym))
                continue
            entry, mae, mfe, bar_ret, hit = m
            rows.append(dict(T=T, symbol=sym, score=sc, entry=entry,
                             mae=mae, mfe=mfe, bar_ret=bar_ret, hit=hit))
        report(name, rows)

    if skipped:
        u = sorted(set((f"{t:%m-%d %H:%M}", s) for t, s in skipped))
        print(f"\n[缺数据跳过] {len(u)} 个: {u}")

    print("\n" + "-" * 84)
    print("怎么读：MAE 主体若落在 -5% 之外，说明 stoploss=-5% 在 4h 出场逻辑发言前就会触发，")
    print("       属止损档与 timeframe 的结构性错配，而非「亏了所以要调参」。")
    print("       样本受 score 榜开采日（2026-09-05）硬约束，且加密高 beta → 有效 n 远小于名义 n。")
    print("-" * 84)


if __name__ == "__main__":
    main()
