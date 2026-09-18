#!/usr/bin/env python3
"""
V3 对照腿测量 —— 「赚了钱是不是因为 AI500」
==========================================

**探索性——非结论，禁止引用为证据。不改变 AI500_V3_GATE3_PREREG.md §2 已冻结的
主判据（前 30 笔累计净收益 > 0），只是给它加上「跟什么比」的读数。**

为什么要有这个（2026-09-19 立，D3）：GATE3 的主判据只问「30 笔加起来是不是正的」。
但 `mean > 0` 不等于 AI500 score 有 alpha —— 大盘同期涨了、裸持不平仓更赚、
榜上第 4~10 名一样赚，三种情形下「正收益」都不构成 score 的证据。本脚本用**已经
落盘的 point-in-time 数据**把四条腿配对算出来：

  腿 S  策略腿      修复后的真实规则：首根「收盘晚于入场」的收阴 4h K 平仓，
                    −5% 止损，48h 上限。（2026-09-19 前这条规则被信号冲突吞掉，
                    所以旧实盘数据测不到它 —— 见策略 docstring）
  腿 H  裸持 48h    不止损、不看阴线，持满 48h。**S − H 就是「出场规则赚了还是亏了」**
  腿 B  BTC 同窗口  与腿 S 完全相同的持有窗口内 BTC 的收益。**S − B 剥掉 beta**
  腿 R  rank 4~10   同一 4h 边界上榜单第 4~10 名，套用腿 S 同一规则取等权均值。
                    **S − R 回答「top3 这个截断本身有没有意义」**

数据来源全部是**决策时刻已存在**的：
  · `ai500_score_history_v3.csv` —— 每 5 分钟全榜快照（含 rank 4~10 的 score/price）
  · 币安官方逐日归档 1m K（`data.binance.vision`，可重放、不含事后修订）
入场时刻重建与 `scripts/v3_entry_replay.py` 同口径（4h 边界 + top3 + score≥80
+ 可选门③），并复用其 K 线抓取与事件枚举函数 —— 那边改了这边跟着变。

已知局限（必须连同结果一起读）
------------------------------
1. **样本上限由开采日 2026-09-05 硬性决定**，n 只有十几个 episode。故本脚本
   **先算 MDE 再报点估计**；CI 跨零只能写「无 ≥MDE 量级的效应」，不得写成
   「AI500 无效」或「出场规则无效」（CLAUDE.md 零结果解读边界）。
2. **同一标的共享同一条价格路径**（ZEC/BR 占绝大多数），名义 n ≠ 有效 n。
   故自举按**日区块**重采样（同一日所有事件一起抽），且强制报告标的集中度。
3. 腿 R 的 rank 4~10 **不加 score≥80 门槛**（它是对照桶，不是候选池），
   且未过门③僵尸过滤 —— 它比腿 S 少一道数据质量过滤，对腿 R 偏不利。
4. 止损与出场信号落在同一根 1m bar 时**假定止损先触发**（保守，与
   AI500_PRICEBOARD_PREREG §3.3 同口径）。
5. 腿 H 无止损，尾部风险不可比；它只用来量「出场规则截掉了多少右尾」，
   **不是**一个可部署的替代方案。

用法：
    python3 scripts/v3_baseline_legs.py --score-history user_data/ai500_score_history_v3.csv
    python3 scripts/v3_baseline_legs.py --score-history ... --zombie-gate user_data/ai500_zombie_check.csv
"""
import argparse
import collections
import importlib.util
import os
import random
import statistics
import sys
from datetime import datetime, timedelta, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 复用 v3_entry_replay 的 K 线抓取 / 快照载入 / 事件枚举（同口径铁律）
_spec = importlib.util.spec_from_file_location(
    "v3_entry_replay", os.path.join(REPO, "scripts", "v3_entry_replay.py"))
_er = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_er)

TIMEFRAME_H = _er.TIMEFRAME_H       # 4
STOPLOSS = _er.STOPLOSS             # -0.05
TOP_N = _er.TOP_N                   # 3
MIN_SCORE = _er.MIN_SCORE           # 80.0

MAX_HOLD_H = 48                     # minimal_roi {"2880": -1}
FEE_ROUNDTRIP = 0.001               # 往返 0.1%
RANK_BUCKET = (4, 10)               # 腿 R 的 rank 区间（含两端）
BOOT_N = 2000
Z_SUM = 1.96 + 0.8416               # MDE = Z_SUM × sd_自举


# ------------------------------------------------------------------ K 线
def load_window(symbol: str, T: datetime, hours: int) -> dict:
    """取 [T, T+hours] 覆盖到的全部 1m K，跨日自动拼。缺任一日返回已有的部分。"""
    bars = {}
    day = T.date()
    last_day = (T + timedelta(hours=hours)).date()
    while day <= last_day:
        bars.update(_er.fetch_1m(symbol, day.isoformat()))
        day += timedelta(days=1)
    return bars


def _slice(bars: dict, T: datetime, hours: int):
    end = T + timedelta(hours=hours)
    return sorted((t, v) for t, v in bars.items() if T <= t < end)


# ------------------------------------------------------------------ 三条出场规则
def leg_strategy(bars: dict, T: datetime):
    """腿 S：−5% 止损 / 首根收盘晚于入场的收阴 4h K / 48h 上限。

    返回 (净收益, 持有小时数, 出场标记)；数据不足返回 None。
    """
    window = _slice(bars, T, MAX_HOLD_H)
    if not window or window[0][0] != T:
        return None
    entry = window[0][1][0]                  # 入场价 = 边界那一分钟的开盘
    if entry <= 0:
        return None
    # 以 1m bar 重建 4h K，边界处判「收阴」
    cur_open = None
    cur_close = None
    bucket_start = T
    for t, (o, h, l, c) in window:
        # 先判止损（同 bar 内假定止损先触发，保守）
        if l / entry - 1.0 <= STOPLOSS:
            return STOPLOSS - FEE_ROUNDTRIP, (t - T).total_seconds() / 3600, "stop"
        if t >= bucket_start + timedelta(hours=TIMEFRAME_H):
            # 上一根 4h K 已收盘，且其收盘时刻严格晚于入场 → 可以发言
            if cur_open is not None and cur_close is not None and cur_close < cur_open:
                return cur_close / entry - 1.0 - FEE_ROUNDTRIP, \
                    (bucket_start + timedelta(hours=TIMEFRAME_H) - T).total_seconds() / 3600, \
                    "bear4h"
            # 推进到包含 t 的那个桶（容错 1m 归档缺口，否则缺 >4h 会错位分桶）
            while t >= bucket_start + timedelta(hours=TIMEFRAME_H):
                bucket_start += timedelta(hours=TIMEFRAME_H)
            cur_open = None
        if cur_open is None:
            cur_open = o
        cur_close = c
    last_c = window[-1][1][3]
    return last_c / entry - 1.0 - FEE_ROUNDTRIP, MAX_HOLD_H, "maxhold"


def leg_hold(bars: dict, T: datetime, hours: int = MAX_HOLD_H):
    """腿 H：裸持 `hours` 小时，无止损无出场信号。"""
    window = _slice(bars, T, hours)
    if not window or window[0][0] != T:
        return None
    entry = window[0][1][0]
    if entry <= 0:
        return None
    return window[-1][1][3] / entry - 1.0 - FEE_ROUNDTRIP


# ------------------------------------------------------------------ 主流程
def rank_bucket_events(snaps, T_set):
    """给定边界集合，取每个边界最近快照的 rank 4~10 名单（不加 score 门槛，见局限 3）。"""
    ts = sorted(snaps)
    import bisect
    out = {}
    for T in T_set:
        i = bisect.bisect_right(ts, T) - 1
        if i < 0 or (T - ts[i]).total_seconds() > 3600:
            continue
        board = sorted(snaps[ts[i]], key=lambda r: -float(r["score"]))
        lo, hi = RANK_BUCKET
        out[T] = [r["symbol"] for r in board[lo - 1:hi]]
    return out


def block_bootstrap(pairs, n=BOOT_N, seed=20260919):
    """日区块自举：pairs = [(日期, 值)]。返回 (点估计, 5%分位, 95%分位, sd_自举)。"""
    if not pairs:
        return None
    by_day = collections.defaultdict(list)
    for d, v in pairs:
        by_day[d].append(v)
    days = list(by_day)
    point = statistics.fmean(v for _, v in pairs)
    rng = random.Random(seed)
    means = []
    for _ in range(n):
        pool = []
        for _ in range(len(days)):
            pool.extend(by_day[rng.choice(days)])
        if pool:
            means.append(statistics.fmean(pool))
    means.sort()
    sd = statistics.pstdev(means) if len(means) > 1 else float("nan")
    lo = means[int(0.05 * len(means))]
    hi = means[int(0.95 * len(means))]
    return point, lo, hi, sd


def report_leg(name, vals):
    if not vals:
        print(f"  {name:<22} 无样本")
        return
    wins = sum(1 for v in vals if v > 0)
    print(f"  {name:<22} n={len(vals):<4} mean={statistics.fmean(vals)*100:+7.2f}%  "
          f"median={statistics.median(vals)*100:+7.2f}%  胜率={wins/len(vals)*100:5.1f}%")


def report_diff(label, pairs):
    r = block_bootstrap(pairs)
    if r is None:
        print(f"  {label:<22} 无样本")
        return
    point, lo, hi, sd = r
    mde = Z_SUM * sd
    verdict = "CI 下界>0" if lo > 0 else ("CI 上界<0" if hi < 0 else "跨零 → 不可判定")
    print(f"  {label:<22} Δ={point*100:+7.2f}%  90%CI=[{lo*100:+6.2f}, {hi*100:+6.2f}]  "
          f"MDE={mde*100:5.2f}pp  {verdict}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--score-history", required=True)
    ap.add_argument("--zombie-gate", default=None,
                    help="开门③ 对照；与 v3_entry_replay 同口径，须配 --since 对齐窗口")
    ap.add_argument("--since", default=None, help="ISO UTC，只取该时刻之后的事件")
    args = ap.parse_args()

    print(__doc__.split("用法：")[0].strip())
    print("=" * 78)

    snaps = _er.load_snapshots(args.score_history)
    if not snaps:
        print("score 快照为空，退出。")
        return 1
    zgate = _er.load_zombie(args.zombie_gate) if args.zombie_gate else None
    since = (datetime.fromisoformat(args.since).astimezone(timezone.utc)
             if args.since else None)

    events = _er.entry_events(snaps, zgate, since)
    episodes = _er.to_episodes(events)
    print(f"\n事件 {len(events)} 个 → episode {len(episodes)} 个"
          f"（连续合格段只取首个；结论只能读 episode 那套）")

    # 标的集中度（局限 2 强制报告）
    conc = collections.Counter(sym for _, sym, _ in episodes)
    tot = sum(conc.values()) or 1
    print("标的集中度：" + "  ".join(
        f"{s}={c}({c/tot*100:.0f}%)" for s, c in conc.most_common()))

    buckets = rank_bucket_events(snaps, {T for T, _, _ in episodes})

    btc_cache = {}
    rows = []
    for T, sym, score in episodes:
        bars = load_window(sym, T, MAX_HOLD_H)
        s = leg_strategy(bars, T)
        if s is None:
            continue
        s_ret, s_hold, s_tag = s
        h_ret = leg_hold(bars, T)
        # 腿 B：BTC 在腿 S 完全相同的持有窗口内
        if T not in btc_cache:
            btc_cache[T] = load_window("BTC", T, MAX_HOLD_H)
        b_ret = leg_hold(btc_cache[T], T, max(1, int(round(s_hold)))) \
            if btc_cache[T] else None
        # 腿 R：rank 4~10 同规则等权均值
        r_vals = []
        for rsym in buckets.get(T, []):
            rb = load_window(rsym, T, MAX_HOLD_H)
            rr = leg_strategy(rb, T)
            if rr is not None:
                r_vals.append(rr[0])
        r_ret = statistics.fmean(r_vals) if r_vals else None
        rows.append(dict(T=T, sym=sym, score=score, s=s_ret, hold=s_hold, tag=s_tag,
                         h=h_ret, b=b_ret, r=r_ret, r_n=len(r_vals)))

    if not rows:
        print("\n无可测量的 episode（1m 归档缺失或窗口未走完）。")
        return 1

    print(f"\n可测量 episode：{len(rows)}")
    print("\n=== 逐腿分布 ===")
    report_leg("腿 S 策略腿", [r["s"] for r in rows])
    report_leg("腿 H 裸持48h", [r["h"] for r in rows if r["h"] is not None])
    report_leg("腿 B BTC 同窗口", [r["b"] for r in rows if r["b"] is not None])
    report_leg("腿 R rank4-10", [r["r"] for r in rows if r["r"] is not None])

    print("\n=== 出场标记分布（腿 S）===")
    for tag, c in collections.Counter(r["tag"] for r in rows).most_common():
        print(f"  {tag:<10} {c:>4}  ({c/len(rows)*100:.0f}%)")
    print("  ↑ bear4h 若仍为 0，说明 4h 阴线出场依然没有发言机会（止损先打掉），"
          "这是止损档与 timeframe 的结构性错配，另立预注册处理")

    print("\n=== 配对差（日区块自举 2000 次，先看 MDE 再看点估计）===")
    report_diff("S − H（出场规则）", [(r["T"].date(), r["s"] - r["h"])
                                      for r in rows if r["h"] is not None])
    report_diff("S − B（剥 beta）", [(r["T"].date(), r["s"] - r["b"])
                                     for r in rows if r["b"] is not None])
    report_diff("S − R（top3 截断）", [(r["T"].date(), r["s"] - r["r"])
                                       for r in rows if r["r"] is not None])

    print("\n=== 逐笔 ===")
    for r in sorted(rows, key=lambda x: x["T"]):
        f = lambda v: f"{v*100:+7.2f}%" if v is not None else "      -"
        print(f"  {r['T']:%m-%d %H:%M} {r['sym']:<7} score={r['score']:6.2f}  "
              f"S={f(r['s'])} H={f(r['h'])} B={f(r['b'])} R={f(r['r'])}(n={r['r_n']})  "
              f"持有={r['hold']:5.1f}h  {r['tag']}")

    print("\n怎么读：任何 CI 跨零的行只能写「无 ≥MDE 量级的效应」。"
          "\n本脚本是观测，不是判定 —— GATE3 §2 的主判据不因这里的结果改变。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
