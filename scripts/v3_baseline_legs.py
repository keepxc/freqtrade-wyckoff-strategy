#!/usr/bin/env python3
"""
V3 对照腿 + 协变量测量 —— 「赚了钱是不是因为 AI500」
=====================================================

**探索性——非结论，禁止引用为证据。不改变 AI500_V3_GATE3_PREREG.md §2 已冻结的
主判据（前 30 笔累计净收益 > 0），只是给它加上「跟什么比」和「按什么分层」的读数。**

为什么要有这个（2026-09-19 立）：GATE3 的主判据只问「30 笔加起来是不是正的」。
但 `mean > 0` 不等于 AI500 score 有 alpha —— 大盘同期涨了、裸持不平仓更赚、
低分档一样赚，三种情形下「正收益」都不构成 score 的证据。

四条腿（同一批入场时刻，配对）
------------------------------
  腿 S  策略腿      修复后的真实规则：首根「收盘晚于入场」的收阴 4h K 平仓，
                    −5% 止损，48h 上限
  腿 H  裸持 48h    不止损、不看阴线，持满 48h。**S − H = 出场规则赚了还是亏了**
  腿 B  BTC 同窗口  与腿 S 完全相同的持有窗口内 BTC 的收益。**S − B = 剥掉 beta**
  腿 L  score 60~80 同一 4h 边界上榜单里 60 ≤ score < 80 的行，套腿 S 同一规则
                    等权。**S − L = `MIN_SCORE=80` 这条线是故事还是统计结论**

### 为什么腿 L 取代了原设计的「rank 4~10」（2026-09-19 推翻）

原设计要比 top3 与 rank 4~10。**该前提是错的**，用仓库自己的数据即可证伪：

    ai500_zombie_check.csv  3,721 行 / 1,481 个决策轮次   (PREREG §1.2)
    _refresh_entry_state 每轮写 min(3, 有分资产数) 行
    3,721 / 1,481 = 2.51 行/轮       ← 若每轮都有 ≥3 个有分资产，应为 4,443 行
    解得：约 759 轮有 3 个、722 轮只有 2 个

**AI500 榜同时只挂 2~3 个活信号，`TOP_N=3` 等于「榜上有啥买啥」。**
大多数轮次根本不存在 rank 4~10 → 那条腿会几乎全空。三个连带结论：
  1. `门①(rank≤3) 通过率 100%` 不能当证据用 —— 双重空洞：重放只看 top3 行，
     而榜上本来也就 2~3 个；
  2. `max_open_trades=3` 同样不是约束；
  3. **本策略实际上没有横截面选择**。唯一的真实决策只剩 `score≥80`、僵尸门、
     出场规则 —— 所谓「AI500 alpha」整个归约为「这个榜上 80 这条线有没有预测力」，
     排名成分为零。这正是腿 L 要量的东西。

三个协变量（逐笔落盘 + 分层描述）
--------------------------------
  signal_age_hours  = 入场时刻 − start_time      ← 买的是「上涨第 1 小时」还是「第 5 天」
  prior_gain_pct    = change_pct_value           ← 入场时该信号已经涨了多少
  score_over_peak   = score / signal_peak        ← 入场时 score 距自身峰值衰减了多少

立项理由：V3 已成交样本里 #1 IOST 在 +91.58% 后入场、#3 BR 在 **+200.4%** 后入场
（signal age 54.9h，MFE **0.00%**）。若 30 笔全聚在 +100~300% 区间，那这 30 笔
测的其实是「买已经翻了两三倍的东西」，而该构造已被 `AI500_PRICEBOARD_PREREG`
在姊妹榜上以 n=6552 证伪（中位数恰好落在止损位）。**先记录，不进交易规则**
（两份外部评审与本项目判断一致：加信号年龄门属 B 类求 edge，本轮不做）。

数据来源全部是**决策时刻已存在**的：
  · `ai500_score_history_v3.csv` —— 每 5 分钟全榜快照（含 score/signal_peak/
    start_time/change_pct_value，即三个协变量与腿 L 名单的全部输入）
  · 币安官方逐日归档 1m K（`data.binance.vision`，可重放、不含事后修订）
入场时刻重建与 `scripts/v3_entry_replay.py` 同口径（4h 边界 + top3 + score≥80
+ 可选门③），并复用其 K 线抓取与事件枚举函数 —— 那边改了这边跟着变。

已知局限（必须连同结果一起读）
------------------------------
1. **样本上限由开采日 2026-09-05 硬性决定**，n 只有十几个 episode。故本脚本
   **先算 MDE 再报点估计**；CI 跨零只能写「无 ≥MDE 量级的效应」，不得写成
   「AI500 无效」或「80 这条线无意义」（CLAUDE.md 零结果解读边界）。
2. **同一标的共享同一条价格路径**（ZEC/BR 占绝大多数），名义 n ≠ 有效 n。
   故自举按**日区块**重采样（同一日所有事件一起抽），且强制报告标的集中度。
3. **腿 L 的僵尸门只对落在 top3 之内的行可得**（`ai500_zombie_check.csv` 只记
   top3）。因 top3≈全榜，绝大多数低分行其实有记录；取不到的标 `gate_unavailable`。
   `--zombie-gate` 给定时**同时报过门/不过门两版**（过门可比性更强，不过门对腿 L
   偏不利），不取其一。
4. 止损与出场信号落在同一根 1m bar 时**假定止损先触发**。⚠️ 这与引擎实际优先级
   **相反**：`should_exit` 的 append 顺序是 exit-signal → stoploss，`freqtradebot.py:1383`
   命中第一个即返回，故实盘同轮内 custom_exit 赢过 stoploss（按阴线收盘价平，
   通常优于止损价）。本脚本保留止损优先，作为**保守下界**。
5. 腿 H 无止损，尾部风险不可比；它只用来量「出场规则截掉了多少右尾」，
   **不是**一个可部署的替代方案。
6. 协变量分层是**描述性**的。n 十几个 episode 分 4 档后每档个位数，
   **不得**对分层差异做任何显著性声明。

用法：
    python3 scripts/v3_baseline_legs.py --score-history user_data/ai500_score_history_v3.csv
    python3 scripts/v3_baseline_legs.py --score-history ... --zombie-gate user_data/ai500_zombie_check.csv
"""
import argparse
import bisect
import collections
import importlib.util
import math
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
LOW_SCORE_BAND = (60.0, MIN_SCORE)  # 腿 L：60 ≤ score < 80（策略只观察、不进场那一档）
BOOT_N = 2000
Z_SUM = 1.96 + 0.8416               # MDE = Z_SUM × sd_自举

AGE_BUCKETS = [(0, 6), (6, 24), (24, 48), (48, math.inf)]
GAIN_BUCKETS = [(0, 20), (20, 50), (50, 100), (100, math.inf)]


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


# ------------------------------------------------------------------ 出场规则
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
    cur_open = None
    cur_close = None
    bucket_start = T
    for t, (o, h, l, c) in window:
        # 同 bar 内假定止损先触发（保守下界，见局限 4）
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


# ------------------------------------------------------------------ 快照与协变量
def snapshot_at(snaps, sorted_ts, T):
    """返回边界 T 实际使用的那个快照的 (时刻, 行列表)；无可用快照返回 (None, None)。

    与 `v3_entry_replay.entry_events` 同口径：取 T 之前最近的一个快照，
    且距 T 不超过 1 小时（超过则视为该边界无有效快照）。
    """
    i = bisect.bisect_right(sorted_ts, T) - 1
    if i < 0 or (T - sorted_ts[i]).total_seconds() > 3600:
        return None, None
    return sorted_ts[i], snaps[sorted_ts[i]]


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def covariates(row, T):
    """signal_age_hours / prior_gain_pct / score_over_peak。取不到的项为 None。"""
    age = None
    st = _f(row.get("start_time"))
    if st:
        age = (T - datetime.fromtimestamp(st, timezone.utc)).total_seconds() / 3600
    score, peak = _f(row.get("score")), _f(row.get("signal_peak"))
    return dict(
        signal_age_h=age,
        prior_gain_pct=_f(row.get("change_pct_value")),
        score_over_peak=(score / peak if score is not None and peak else None),
    )


def low_band_symbols(rows, zgate=None, snap_t=None):
    """腿 L 名单：60 ≤ score < 80。返回 (过门名单, 不过门名单, 门不可得数)。"""
    lo, hi = LOW_SCORE_BAND
    ungated, gated, unavail = [], [], 0
    for r in rows:
        s = _f(r.get("score"))
        if s is None or not (lo <= s < hi):
            continue
        ungated.append(r["symbol"])
        if zgate is None:
            continue
        key = (snap_t, r["symbol"])
        if key not in zgate:
            unavail += 1
        elif zgate[key]:
            gated.append(r["symbol"])
    return gated, ungated, unavail


# ------------------------------------------------------------------ 统计
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
    if not means:
        return None
    means.sort()
    sd = statistics.pstdev(means) if len(means) > 1 else float("nan")
    return point, means[int(0.05 * len(means))], means[int(0.95 * len(means))], sd


def report_leg(name, vals):
    if not vals:
        print(f"  {name:<24} 无样本")
        return
    wins = sum(1 for v in vals if v > 0)
    print(f"  {name:<24} n={len(vals):<4} mean={statistics.fmean(vals)*100:+7.2f}%  "
          f"median={statistics.median(vals)*100:+7.2f}%  胜率={wins/len(vals)*100:5.1f}%")


def report_diff(label, pairs):
    r = block_bootstrap(pairs)
    if r is None:
        print(f"  {label:<24} 无样本")
        return
    point, lo, hi, sd = r
    verdict = "CI 下界>0" if lo > 0 else ("CI 上界<0" if hi < 0 else "跨零 → 不可判定")
    print(f"  {label:<24} Δ={point*100:+7.2f}%  90%CI=[{lo*100:+6.2f}, {hi*100:+6.2f}]  "
          f"MDE={Z_SUM*sd*100:5.2f}pp  {verdict}")


def report_buckets(title, rows, key, buckets, unit):
    print(f"\n  按 {title} 分层（**描述性，n 太小不得做显著性声明**）")
    for lo, hi in buckets:
        sel = [r["s"] for r in rows
               if r[key] is not None and lo <= r[key] < hi]
        span = f"{lo:g}~{'∞' if hi == math.inf else f'{hi:g}'}{unit}"
        if sel:
            print(f"    {span:<12} n={len(sel):<3} 腿S mean={statistics.fmean(sel)*100:+7.2f}%")
        else:
            print(f"    {span:<12} n=0")
    miss = sum(1 for r in rows if r[key] is None)
    if miss:
        print(f"    (协变量缺失 {miss} 笔)")


# ------------------------------------------------------------------ 主流程
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
    sorted_ts = sorted(snaps)
    zgate = _er.load_zombie(args.zombie_gate) if args.zombie_gate else None
    since = (datetime.fromisoformat(args.since).astimezone(timezone.utc)
             if args.since else None)

    events = _er.entry_events(snaps, zgate, since)
    episodes = _er.to_episodes(events)
    print(f"\n事件 {len(events)} 个 → episode {len(episodes)} 个"
          f"（连续合格段只取首个；结论只能读 episode 那套）")

    # 榜深度：验证「top3 ≈ 全榜」这个前提（腿 L 取代腿 R 的依据）
    depths = [len([r for r in rows if _f(r.get("score")) is not None])
              for rows in snaps.values()]
    if depths:
        print(f"榜深度（每快照有分资产数）：中位 {statistics.median(depths):.0f}  "
              f"均值 {statistics.fmean(depths):.2f}  最大 {max(depths)}  "
              f"→ >3 的快照占 {sum(1 for d in depths if d > 3)/len(depths)*100:.1f}%")
        print("  ↑ 若中位 ≤3，则 TOP_N=3 不构成横截面选择，rank 对照腿无数据（见文件头）")

    conc = collections.Counter(sym for _, sym, _ in episodes)
    tot = sum(conc.values()) or 1
    print("标的集中度：" + "  ".join(
        f"{s}={c}({c/tot*100:.0f}%)" for s, c in conc.most_common()))

    btc_cache = {}
    rows_out = []
    lband_unavail = 0
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
        b_ret = (leg_hold(btc_cache[T], T, max(1, int(round(s_hold))))
                 if btc_cache[T] else None)

        # 协变量 + 腿 L 名单，都取自该边界实际使用的那个快照
        snap_t, board = snapshot_at(snaps, sorted_ts, T)
        cov = dict(signal_age_h=None, prior_gain_pct=None, score_over_peak=None)
        l_gated_syms, l_ungated_syms = [], []
        if board is not None:
            me = next((r for r in board if r["symbol"] == sym), None)
            if me:
                cov = covariates(me, T)
            l_gated_syms, l_ungated_syms, na = low_band_symbols(board, zgate, snap_t)
            lband_unavail += na

        def band_mean(syms):
            vals = []
            for ls in syms:
                lr = leg_strategy(load_window(ls, T, MAX_HOLD_H), T)
                if lr is not None:
                    vals.append(lr[0])
            return (statistics.fmean(vals) if vals else None), len(vals)

        l_un, l_un_n = band_mean(l_ungated_syms)
        l_ga, l_ga_n = band_mean(l_gated_syms) if zgate else (None, 0)

        rows_out.append(dict(T=T, sym=sym, score=score, s=s_ret, hold=s_hold, tag=s_tag,
                             h=h_ret, b=b_ret, l_un=l_un, l_un_n=l_un_n,
                             l_ga=l_ga, l_ga_n=l_ga_n, **cov))

    if not rows_out:
        print("\n无可测量的 episode（1m 归档缺失或窗口未走完）。")
        return 1

    print(f"\n可测量 episode：{len(rows_out)}")
    print("\n=== 逐腿分布 ===")
    report_leg("腿 S 策略腿", [r["s"] for r in rows_out])
    report_leg("腿 H 裸持48h", [r["h"] for r in rows_out if r["h"] is not None])
    report_leg("腿 B BTC 同窗口", [r["b"] for r in rows_out if r["b"] is not None])
    report_leg("腿 L score60-80 不过门", [r["l_un"] for r in rows_out if r["l_un"] is not None])
    if zgate:
        report_leg("腿 L score60-80 过门", [r["l_ga"] for r in rows_out if r["l_ga"] is not None])
        print(f"    (腿 L 僵尸门不可得 {lband_unavail} 行，见局限 3)")

    print("\n=== 出场标记分布（腿 S）===")
    for tag, c in collections.Counter(r["tag"] for r in rows_out).most_common():
        print(f"  {tag:<10} {c:>4}  ({c/len(rows_out)*100:.0f}%)")
    print("  ↑ bear4h 若仍为 0，说明 4h 阴线出场依然没有发言机会（止损先打掉），"
          "这是止损档与 timeframe 的结构性错配，另立预注册处理")

    print("\n=== 配对差（日区块自举 2000 次，先看 MDE 再看点估计）===")
    report_diff("S − H（出场规则）", [(r["T"].date(), r["s"] - r["h"])
                                      for r in rows_out if r["h"] is not None])
    report_diff("S − B（剥 beta）", [(r["T"].date(), r["s"] - r["b"])
                                     for r in rows_out if r["b"] is not None])
    report_diff("S − L（80 这条线）", [(r["T"].date(), r["s"] - r["l_un"])
                                      for r in rows_out if r["l_un"] is not None])
    if zgate:
        report_diff("S − L 过门", [(r["T"].date(), r["s"] - r["l_ga"])
                                   for r in rows_out if r["l_ga"] is not None])

    print("\n=== 协变量分层 ===")
    report_buckets("signal_age", rows_out, "signal_age_h", AGE_BUCKETS, "h")
    report_buckets("prior_gain", rows_out, "prior_gain_pct", GAIN_BUCKETS, "%")

    print("\n=== 逐笔 ===")
    for r in sorted(rows_out, key=lambda x: x["T"]):
        f = lambda v: f"{v*100:+7.2f}%" if v is not None else "      -"
        g = lambda v, w=6, p=1: (f"{v:{w}.{p}f}" if v is not None else " " * w)
        print(f"  {r['T']:%m-%d %H:%M} {r['sym']:<7} score={r['score']:6.2f}  "
              f"S={f(r['s'])} H={f(r['h'])} B={f(r['b'])} "
              f"L={f(r['l_un'])}(n={r['l_un_n']})  "
              f"age={g(r['signal_age_h'])}h gain={g(r['prior_gain_pct'],7)}% "
              f"s/pk={g(r['score_over_peak'],5,3)}  "
              f"持有={r['hold']:5.1f}h {r['tag']}")

    print("\n怎么读：任何 CI 跨零的行只能写「无 ≥MDE 量级的效应」。协变量分层是描述性的。"
          "\n本脚本是观测，不是判定 —— GATE3 §2 的主判据不因这里的结果改变。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
