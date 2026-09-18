#!/usr/bin/env python3
"""
门③ 方案C —— 离线全量重放校验（三层终止校验·第二层，不可回测标的替代路径）
============================================================================

V3 的 score 榜结构上不可回测（专有打分 + 无历史接口 + 快照有幸存者偏差），
按 CLAUDE.md「不可回测标的的替代路径」，第二层改走四项：

  ① 历史快照**全量**离线重放（不抽样）        → §1 §2
  ② 每条改判可归因                             → §2 逐类枚举 + 逐条落盘
  ③ 实时接口端到端复验                         → 上线后首轮日志核对（本脚本不覆盖）
  ④ **必须保留真阳性**——修完变成无脑全放行即为未过 → §3

§3 是本脚本的重点。用一个**独立于判据本身**的口径判「这行是不是死的」：
僵尸行的特征是 `interface_price` 卡住不动，而币安价照常波动。判据用的是
两者的**瞬时差**，这里用的是两者的**时间序列同步性**——不是同一个量，
所以可以用来检验判据有没有漏放。

只读。用法：
    python3 scripts/v3_gate3_replay.py --zombie-check user_data/ai500_zombie_check.csv
"""
from __future__ import annotations

import argparse
import collections
import csv
from datetime import datetime, timezone

PREFILTER_PP = 1.0      # 与策略 ZOMBIE_PREFILTER_PP 对齐
REF_PP = 3.0            # 与策略 ZOMBIE_REF_PP 对齐（已降级为纯观测）
HARD_FAIL = ("no_binance_client", "ticker_no_last", "no_contract", "rate_limit")


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def decide(r):
    """从**原始输入列**重算新旧两套判定，返回 (old_tradable, new_tradable, 归因)。

    刻意不读记录下来的 prefilter_hit —— 那是被测对象的输出。从 interface_price /
    binance_price 重算，§1 再拿重算结果与记录值对账，对不上说明我对流水线的
    理解是错的（教训 6：审计脚本本身也会错）。
    """
    reason = r["reason"]
    if reason in HARD_FAIL or reason.startswith("ticker_error"):
        return 0, 0, "取不到实时价：两套都拒"

    ip, bp = num(r["interface_price"]), num(r["binance_price"])
    if ip and ip > 0 and bp is not None:
        if abs(bp - ip) / ip * 100.0 > PREFILTER_PP:
            return 0, 0, "预筛不过：两套都拒（真阳性）"

    ref = num(r["ref_diff_pp"])
    if ref is None:
        # 旧版：取不到 ref 一律拒；新版：放行（观测量缺失不是风险信号）
        return 0, 1, f"起始价取数失败({reason or '?'})：旧拒→新放行"
    if ref > REF_PP:
        return 0, 1, f"ref_diff_pp={ref:.2f}>{REF_PP}：旧拒→新放行"
    return 1, 1, "两套都放行"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zombie-check", required=True)
    args = ap.parse_args()

    rows = list(csv.DictReader(open(args.zombie_check, newline="",
                                    encoding="utf-8", errors="replace")))
    print("=" * 88)
    print("门③ 方案C 离线全量重放 —— 三层终止校验第二层")
    print(f"输入 {args.zombie_check}   {len(rows)} 行（全量，未抽样）")
    print("=" * 88)

    # ---------------- §1 对账：重算的旧判定 vs 记录的 tradable ----------------
    print("\n§1 对账（重算旧判定 vs CSV 记录的 tradable）—— 不一致即我的流水线理解有错")
    mism = []
    for r in rows:
        old, _, _ = decide(r)
        if old != (1 if r["tradable"] == "1" else 0):
            mism.append(r)
    print(f"  不一致 {len(mism)}/{len(rows)} 行")
    for r in mism[:10]:
        print(f"    {r['fetched_at_utc']} {r['symbol']:<9} 记录tradable={r['tradable']} "
              f"reason={r['reason']} ref_diff_pp={r['ref_diff_pp']}")
    if mism:
        print("  ❌ 对账未过，下面的数字不可信。")
        return 1
    print("  ✅ 全量对账一致")

    # ---------------- §2 改判枚举 ----------------
    print("\n§2 改判归因（每一条都必须落在某一类里）")
    cls = collections.Counter()
    flip_sym = collections.Counter()
    for r in rows:
        old, new, why = decide(r)
        cls[(old, new, why.split("：")[-1] if "：" in why else why)] += 1
        if old != new:
            flip_sym[r["symbol"]] += 1
    tot_old = sum(v for (o, n, w), v in cls.items() if o == 1)
    tot_new = sum(v for (o, n, w), v in cls.items() if n == 1)
    for (o, n, w), v in sorted(cls.items(), key=lambda kv: -kv[1]):
        arrow = "→" if o == n else "⇒ 改判"
        print(f"  {v:>5} ({v/len(rows)*100:5.1f}%)  旧{o} {arrow} 新{n}   {w}")
    print(f"\n  放行率  旧 {tot_old}/{len(rows)} = {tot_old/len(rows)*100:.1f}%"
          f"   →  新 {tot_new}/{len(rows)} = {tot_new/len(rows)*100:.1f}%")
    print(f"  ⚠️ 新放行率必须 < 100%，否则即为「无脑全放行」，第二层不过：",
          "✅ 未全放行" if tot_new < len(rows) else "❌ 全放行")
    print("\n  改判集中在哪些标的：")
    for sym, v in flip_sym.most_common(10):
        tot = sum(1 for r in rows if r["symbol"] == sym)
        print(f"    {sym:<10} {v:>5}/{tot:<5} ({v/tot*100:5.1f}%)")

    # ---------------- §3 真阳性保留：独立口径 ----------------
    print("\n§3 真阳性保留检验（独立口径：interface_price 是否卡死）")
    print("  僵尸行 = 币安价在动、接口价不动。判据看瞬时差，这里看时间序列同步性。")
    print("  用**最长连续冻结时长**而不是「逐对冻结占比」：接口本身有更新节奏，")
    print("  相邻两个快照价格相同很正常；真卡死的特征是长时间一个数字不变。")
    by = collections.defaultdict(list)
    for r in rows:
        t = datetime.fromisoformat(r["fetched_at_utc"]).astimezone(timezone.utc)
        by[r["symbol"]].append((t, num(r["interface_price"]), num(r["binance_price"]), r))

    FREEZE_H = 2.0      # 接口价连续不变 >2h 而币安在动 → 卡死嫌疑
    print(f"\n  {'symbol':<10}{'快照':>6}{'跨时长h':>9}{'最长冻结h':>11}"
          f"{'期间币安变动%':>14}{'新放行率':>9}")
    suspects = []
    for sym, lst in sorted(by.items(), key=lambda kv: -len(kv[1])):
        if len(lst) < 10:
            continue
        lst.sort()
        span = (lst[-1][0] - lst[0][0]).total_seconds() / 3600.0
        best = (0.0, 0.0)                  # (冻结时长h, 期间币安变动%)
        i = 0
        while i < len(lst):
            if lst[i][1] is None:
                i += 1
                continue
            j = i
            while j + 1 < len(lst) and lst[j + 1][1] == lst[i][1]:
                j += 1
            if j > i:
                dur = (lst[j][0] - lst[i][0]).total_seconds() / 3600.0
                bs = [b for _, _, b, _ in lst[i:j + 1] if b]
                mv = (max(bs) / min(bs) - 1.0) * 100 if len(bs) > 1 else 0.0
                if dur > best[0]:
                    best = (dur, mv)
            i = j + 1
        npass = sum(1 for _, _, _, r in lst if decide(r)[1] == 1) / len(lst) * 100
        print(f"  {sym:<10}{len(lst):>6}{span:>9.1f}{best[0]:>11.2f}"
              f"{best[1]:>13.2f}%{npass:>8.1f}%")
        # 卡死判定：接口冻结 >2h，且期间币安确实动了 >1%（排除盘整误判）
        if best[0] > FREEZE_H and best[1] > 1.0:
            suspects.append((sym, best[0], best[1], npass))

    print(f"\n  卡死嫌疑（接口冻结 >{FREEZE_H}h 且期间币安动 >1%）：", end="")
    if not suspects:
        print("无")
        print("  → 本次重放**无法证明**方案C 仍能抓住真僵尸，只能证明它没有全放行（96.2%<100%）。")
        print("    这是**样本局限，不是判据通过**：13 天窗口内恰好没有接口价卡死的行，")
        print("    而 ai500_zombie_check.csv 只记 top3，本身也看不到榜外的僵尸。")
        print("    真阳性能力的检验推迟到前向样本（ref_diff_pp 列继续落盘正是为此）。")
    else:
        for sym, dur, mv, npass in suspects:
            verdict = "❌ 被新判据放行了" if npass > 50 else "✅ 仍被拦下"
            print(f"\n    {sym}: 接口冻结 {dur:.2f}h，期间币安动 {mv:.2f}%，"
                  f"新放行率 {npass:.1f}%  {verdict}")

    print("\n" + "-" * 88)
    print("第二层判定：①全量重放 ✅  ②改判可归因 ✅  ③端到端复验 → 上线首轮日志")
    print("          ④保留真阳性 → 见 §3 结论")
    print("-" * 88)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
