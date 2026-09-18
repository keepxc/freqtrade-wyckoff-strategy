#!/usr/bin/env python3
"""
V3 出场机制网格 —— 「放宽止损」vs「缩短周期」哪个更有利
========================================================

**探索性——非结论，禁止引用为证据。**

回答用户 2026-09-18 的问题 #2。**这不是选参数的脚本**，n=9 个 episode 做
3×8 网格搜索，最好的那格几乎必然是噪声（防过拟合工具箱：名义样本数≠有效样本数，
且 9 个 episode 只有 5 个标的、共享加密高 beta）。唯一能读的是**方向性/单调性**：
放宽止损是否单调改善？缩短周期是否单调改善？两者是否交互？

模拟口径（与 AI500V3ScoreHold 对齐）：
  · 入场   = 4h 边界那一分钟的 open（与 v3_entry_replay 验证过的口径一致，
             对 IOST/AIN 两笔真交易复现误差 0.06% / 0.26%）
  · 出场①  = 止损：任一分钟 low <= entry*(1+stop) → 以 stop 价成交
  · 出场②  = 信号：某根 tf K 收盘 close<open → 下一根 K 的 open 成交
  · 出场③  = 超时：minimal_roi {"2880": -1} → 入场后 48h 强制平仓
  · 同一分钟上「信号」先于「止损」（信号走 open，止损走 low）
  · 手续费 0.05%/边（由两笔真交易反推：IOST 价格 -5.32% 记账 -5.41%）

**已知乐观处**：止损假定在 stop 价精确成交。两笔真交易实际滑点 0.12~0.32pp
（IOST 实际价格走了 -5.32% 才记 -5.41%）。故所有含止损的格子都偏乐观，
**且止损越紧、触发越频繁，这份乐观越大** —— 这会系统性地偏袒「收紧止损」，
读结论时必须往反方向打折。
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import os
import random
import statistics
import sys
from datetime import datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 复用已验证过的入场重建与 K 线抓取，避免两份实现漂移
_spec = importlib.util.spec_from_file_location(
    "v3rep", os.path.join(REPO, "scripts", "v3_entry_replay.py"))
v3rep = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v3rep)

FEE = 0.0005          # 单边
MAX_HOLD_H = 48       # minimal_roi {"2880": -1}
BASE_TF, BASE_STOP = 4, -0.05

TF_GRID = [1, 2, 4]
STOP_GRID = [-0.03, -0.04, -0.05, -0.06, -0.08, -0.10, -0.15, None]


def load_minutes(symbol, T, hours):
    """取 [T, T+hours) 的 1m K，返回按时间排序的 [(t,(o,h,l,c))]。"""
    kl = {}
    d = T.date()
    last = (T + timedelta(hours=hours)).date()
    while d <= last:
        kl.update(v3rep.fetch_1m(symbol, d))
        d += timedelta(days=1)
    end = T + timedelta(hours=hours)
    w = sorted((t, v) for t, v in kl.items() if T <= t < end)
    return w


def simulate(mins, T, tf_h, stop, full):
    """返回 (毛收益, 出场原因, 持仓分钟数)。数据不足返回 None。

    `full`：该 episode 是否拿到了完整 48h 数据。没拿到而又走到数据末尾时，
    出场原因记 **trunc**（截断）而不是 timeout——那不是策略的超时平仓，
    是归档还没发布。把截断当成 timeout 会凭空捏造一个出场价。
    """
    if not mins or mins[0][0] != T:
        return None
    entry = mins[0][1][0]
    stop_px = entry * (1 + stop) if stop is not None else None

    bar_open = None          # 当前 tf K 的 open
    bar_start = T
    tf = timedelta(hours=tf_h)

    for t, (o, h, l, c) in mins:
        # --- tf 边界：上一根刚收盘，先结算信号出场（走 open）---
        if t >= bar_start + tf:
            if bar_open is not None and prev_close < bar_open:
                return o / entry - 1.0, "signal", int((t - T).total_seconds() // 60)
            # 推进到 t 所属的那根 K 的起点（容忍缺失分钟造成的跳格）
            k = int((t - T) / tf)
            bar_start = T + k * tf
            bar_open = o
        elif bar_open is None:
            bar_open = o
        prev_close = c

        # --- 止损（走 low）---
        if stop_px is not None and l <= stop_px:
            return stop, "stop", int((t - T).total_seconds() // 60)

    # --- 走到数据末尾：真超时 vs 归档截断 ---
    why = "timeout" if full else "trunc"
    return mins[-1][1][3] / entry - 1.0, why, int((mins[-1][0] - T).total_seconds() // 60)


def net(gross):
    return (1 + gross) * (1 - FEE) ** 2 - 1


def boot_ci(diffs, n=5000, seed=7):
    """成对自举（按 episode 重采样）。n=9 时 CI 必然很宽——这正是要展示的。"""
    rnd = random.Random(seed)
    sums = []
    for _ in range(n):
        s = sum(rnd.choice(diffs) for _ in diffs)
        sums.append(s)
    sums.sort()
    return sums[int(n * 0.05)], sums[int(n * 0.95)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--score-history", required=True)
    args = ap.parse_args()

    print("=" * 96)
    print("V3 出场机制网格 —— 探索性，非结论，禁止引用为证据")
    print(f"生成 {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC   "
          f"手续费 {FEE*1e4:.0f}bp/边   最长持仓 {MAX_HOLD_H}h")
    print("=" * 96)

    snaps = v3rep.load_snapshots(args.score_history)
    episodes = v3rep.to_episodes(v3rep.entry_events(snaps, None, None))
    print(f"入场 episode {len(episodes)} 个（门③关 ≈ 方案C 的入场面）")

    # 抓齐数据
    data, usable = {}, []
    for T, sym, sc in episodes:
        mins = load_minutes(sym, T, MAX_HOLD_H)
        # 允许尾部缺（最后一天归档未发布），但入场后至少要有 4h
        if len(mins) < 4 * 60 * 0.8 or mins[0][0] != T:
            print(f"  [跳过] {sym} {T:%m-%d %H:%M} 数据不足({len(mins)}分钟)")
            continue
        data[(T, sym)] = mins
        usable.append((T, sym, sc, len(mins) / 60.0))
    print(f"可用 {len(usable)} 个：")
    for T, sym, sc, h in usable:
        tag = "" if h >= MAX_HOLD_H - 0.1 else "   ⚠️ 归档未发布，尾部截断"
        print(f"    {T:%m-%d %H:%M}  {sym:<9} score={sc:6.2f}  可测 {h:5.1f}h{tag}")

    # ---- 网格。键必须是 (入场时刻, symbol)：同一标的有多个 episode（ZEC×4、BR×2），
    #      只用 symbol 做键会把它们互相覆盖，成对比较直接算错。----
    res = {}
    for tf in TF_GRID:
        for stop in STOP_GRID:
            rows = {}
            for T, sym, sc, h in usable:
                r = simulate(data[(T, sym)], T, tf, stop, h >= MAX_HOLD_H - 0.1)
                if r:
                    rows[(T, sym)] = (net(r[0]), r[1], r[2])
            res[(tf, stop)] = rows

    # 截断污染：任一格子里走到 trunc 的 episode，其收益是「数据没了」而非策略决定
    dirty = {k for cell in res.values() for k, v in cell.items() if v[1] == "trunc"}
    clean = [k for k in res[(BASE_TF, BASE_STOP)] if k not in dirty]
    print(f"\n截断污染 episode {len(dirty)} 个: "
          + (", ".join(f"{s}@{t:%m-%d %H:%M}" for t, s in sorted(dirty)) or "无"))
    print(f"干净集（所有格子都拿到真实出场）{len(clean)} 个 —— 成对比较只能用这一套")

    def sl(stop):
        return "无止损" if stop is None else f"{stop*100:.0f}%"

    def table(title, keys, fn, fmt):
        print(f"\n{title}（n={len(keys)}）")
        print(f"{'出场tf':>7}" + "".join(f"{sl(s):>11}" for s in STOP_GRID))
        for tf in TF_GRID:
            line = f"{tf:>5}h  "
            for stop in STOP_GRID:
                vals = [res[(tf, stop)][k] for k in keys]
                mark = "*" if (tf, stop) == (BASE_TF, BASE_STOP) else " "
                line += format(fn(vals), fmt) + mark
            print(line)

    print("\n" + "=" * 96)
    print("网格结果（行=出场K周期，列=止损档，* = 当前实盘配置 4h/-5%）")
    print("=" * 96)
    table("总净收益 %（等额仓位求和）", clean, lambda v: sum(x[0] for x in v) * 100, ">+10.2f")
    table("中位单笔净收益 %", clean,
          lambda v: statistics.median([x[0] for x in v]) * 100, ">+10.2f")
    table("胜率 %", clean,
          lambda v: sum(1 for x in v if x[0] > 0) / len(v) * 100, ">10.1f")
    table("止损出场占比 %（止损抢在信号之前）", clean,
          lambda v: sum(1 for x in v if x[1] == "stop") / len(v) * 100, ">10.1f")
    table("平均持仓 h", clean,
          lambda v: statistics.mean([x[2] for x in v]) / 60.0, ">10.1f")

    # ---- 单变量切面：一次只动一个维度 ----
    base_map = {k: res[(BASE_TF, BASE_STOP)][k][0] for k in clean}
    print("\n" + "=" * 96)
    print(f"相对当前配置（4h/-5%，干净集总计 {sum(base_map.values())*100:+.2f}%）的成对差异")
    print(f"成对自举 n=5000，按 episode 重采样；90% CI。"
          f"**n={len(clean)}，CI 宽是必然，不是 bug**")
    print("=" * 96)
    print(f"{'变体':<24}{'总净收益%':>11}{'Δ vs 基线':>11}{'90% CI':>22}{'CI下界>0?':>10}")
    variants = ([(BASE_TF, s) for s in STOP_GRID if s != BASE_STOP]
                + [(tf, BASE_STOP) for tf in TF_GRID if tf != BASE_TF])
    for tf, stop in variants:
        m = {k: res[(tf, stop)][k][0] for k in clean}
        diffs = [m[k] - base_map[k] for k in clean]
        lo, hi = boot_ci(diffs)
        tag = "只动止损" if tf == BASE_TF else "只动周期"
        ok = "✅" if lo > 0 else "—"
        print(f"{tag} {tf}h/{sl(stop):<12}{sum(m.values())*100:>+10.2f}"
              f"{sum(diffs)*100:>+11.2f}   [{lo*100:+7.2f}, {hi*100:+7.2f}]{ok:>8}")

    # ---- 逐笔明细 ----
    print("\n" + "=" * 96)
    print("逐 episode 明细（净收益% / 出场原因 / 持仓h）  trunc = 归档截断，非策略出场")
    print("=" * 96)
    show = [(4, -0.05), (4, -0.10), (4, None), (2, -0.05), (1, -0.05)]
    print(f"{'symbol':<9}{'入场':<13}" + "".join(f"{f'{tf}h/{sl(s)}':>21}" for tf, s in show))
    for T, sym, sc, _ in usable:
        flag = " " if (T, sym) in clean else "×"
        line = f"{flag}{sym:<8}{T:%m-%d %H:%M}  "
        for tf, s in show:
            v = res[(tf, s)].get((T, sym))
            line += f"{v[0]*100:>+9.2f} {v[1]:<7}{v[2]/60:>4.0f}h" if v else f"{'-':>21}"
        print(line)
    print("  × = 被截断污染，未进入上面的成对比较")

    # ---- 入场频率：给 30 笔判据估时间 ----
    print("\n" + "=" * 96)
    print("入场频率（用于估「30 笔」要多久）")
    print("=" * 96)
    ts = sorted(snaps)
    days = (ts[-1] - ts[0]).total_seconds() / 86400
    for tf in TF_GRID:
        v3rep.TIMEFRAME_H = tf
        ev = v3rep.entry_events(snaps, None, None)
        ep = v3rep.to_episodes(ev)
        rate = len(ep) / days
        print(f"  入场周期 {tf}h: {len(ev):>4} 事件 → {len(ep):>3} episode  "
              f"= {rate:.2f} 笔/天 → 30 笔约需 {30/rate:5.1f} 天")
    v3rep.TIMEFRAME_H = BASE_TF
    print("  ⚠️ 缩短入场周期得到的「更多笔数」大部分是同一条价格路径的细分，"
          "不产生新信息（防过拟合 #5）。")

    print("\n" + "-" * 96)
    print("怎么读：只看单调性与符号，不要挑最好的格子。任何一格的数字都建立在 9 个 episode、")
    print("       5 个标的、13 天上，MDE 远大于格子之间的差。CI 全部跨零即为「测不出来」。")
    print("-" * 96)


if __name__ == "__main__":
    main()
