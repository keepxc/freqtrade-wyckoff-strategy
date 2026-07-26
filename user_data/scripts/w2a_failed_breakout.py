"""
W2-a：失败突破（Failed Breakout）信号有效性检验
预注册见 wyckoff_rebuild_tasks.md W2-a（判据冻结于 commit 0f73dd2，跑数前）

纯数据审计，禁碰 strategies/。事件定义复用 w2_candidate_screening.py 中已过
W0 因果检查的实现，不重写。

**命名如实声明**：本检验对象是"失败突破"这一价格行为命题，**不是威科夫 UTAD**
（UTAD 要求派发区间语境，本定义无任何区间/阶段前置条件）。结果不得归因到
威科夫 UTAD 理论。

原则 W-B 算式对齐核对（每条判据测的是哪个量）：
  P1 : 量 = 训练段失败突破组「市场中性 fwd10」均值 → 自举 90%CI 上界 < 0
  P2 : 量 = 同一均值的点估计（真实数据，非自举均值） ≤ -1.56% (=1.5×MDE)
  P3 : 量 = 失败突破均值 − 成功突破均值。两组共享"盘中触及新高"前提，
       唯一差异是收盘位置，故此差值精确隔离"收盘跌回"这一个条件。
       自举按日历日期区块，两组共享同一日期抽样（W1-c 已确立的做法）
  P4 : 量 = 训练段内以事件日期中位数切分的前后两半均值，要求同号
  P5 : 量 = OOS 保留段均值，要求与训练段同号且 ≤ -0.78% (=floor/2)
  P6 : 量 = 训练段事件数，< 1100 一票否决

保留段纪律：最后 20% 日期不参与 P1~P4 任何计算，代码上先算完训练段再触碰。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
from w1_power_analysis_multi import load_universe
from w1c_spring_effect import date_block_bootstrap, event_frame, event_values
from w2_candidate_screening import c3_utad_upthrust, _base

sys.stdout.reconfigure(encoding="utf-8")

FWD = 10
BLOCK_LEN = 20
N_BOOT = 2000
SEED = 20260726
P2_FLOOR = -0.0156       # 1.5 × MDE(1.04%)，冻结
P5_FLOOR = -0.0078       # floor / 2，冻结
P6_MIN_TRAIN_EVENTS = 1100
HOLDOUT_FRAC = 0.20


def failed_breakout(df):
    """失败突破：盘中破前20日高点，收盘跌回。复用摸底脚本实现。"""
    return c3_utad_upthrust(df)


def success_breakout(df):
    """W-D 对照：成功突破——盘中破前20日高点且收盘站上。"""
    o = _base(df)
    o["success_breakout"] = (
        (o["high"] > o["_prior_high"]) & (o["close"] > o["_prior_high"])
    ).fillna(False)
    return o[[c for c in o.columns if not c.startswith("_")]]


def main():
    universe, _ = load_universe()
    syms = sorted(universe)

    print("=" * 100)
    print("W2-a：失败突破信号有效性检验（判据冻结于 commit 0f73dd2）")
    print("⚠️ 检验对象是'失败突破'价格行为命题，**不是威科夫 UTAD**（无派发区间语境）")
    print("⚠️ 幸存者偏差：宇宙为存活至今品种，非点位时间宇宙")
    print(f"主口径: 市场中性 fwd{FWD}; block={BLOCK_LEN}, n_boot={N_BOOT}, seed={SEED}")
    print(f"冻结门槛: P2 floor={P2_FLOOR:.2%}, P5 floor={P5_FLOOR:.2%}, "
          f"P6 训练段事件数≥{P6_MIN_TRAIN_EVENTS}")
    print("=" * 100)

    for s in syms:
        universe[s]["fail_bo"] = failed_breakout(universe[s])["c3_utad_upthrust"].values
        universe[s]["succ_bo"] = success_breakout(universe[s])["success_breakout"].values

    raw = pd.DataFrame({s: universe[s].set_index("date")[f"fwd{FWD}"] for s in syms}).sort_index()
    mn = raw.sub(raw.mean(axis=1), axis=0)
    calendar = list(mn.index)

    ev_fail = event_values(mn, event_frame(universe, syms, "fail_bo"))
    ev_succ = event_values(mn, event_frame(universe, syms, "succ_bo"))

    # ---- 训练/保留段切分（按日历日期，最后 20% 为保留段）----
    split_pos = int(len(calendar) * (1 - HOLDOUT_FRAC))
    split_date = calendar[split_pos]
    cal_train = calendar[:split_pos]
    cal_hold = calendar[split_pos:]
    print(f"\n切分点: {pd.Timestamp(split_date).date()}  "
          f"训练段 {len(cal_train)} 日 / 保留段 {len(cal_hold)} 日")

    tr_fail = ev_fail[ev_fail["date"] < split_date]
    tr_succ = ev_succ[ev_succ["date"] < split_date]
    ho_fail = ev_fail[ev_fail["date"] >= split_date]

    # ---- P6 功效前提（一票否决）----
    p6 = len(tr_fail) >= P6_MIN_TRAIN_EVENTS
    print(f"\n[P6 功效前提] 训练段失败突破事件数 = {len(tr_fail)}"
          f"（门槛 ≥{P6_MIN_TRAIN_EVENTS}） → {'PASS' if p6 else 'FAIL（一票否决）'}")
    if not p6:
        print("  功效前提失效，按预注册本次不得下结论。")
        return 1

    # ---- 训练段主分析（保留段此处不触碰）----
    print(f"\n{'=' * 100}\n训练段主分析（保留段未参与）\n{'=' * 100}")
    m_fail = tr_fail["val"].mean()
    m_succ = tr_succ["val"].mean()
    print(f"  失败突破  n={len(tr_fail)}  均值 {m_fail:+.2%}  "
          f"中位 {tr_fail['val'].median():+.2%}  正比例 {(tr_fail['val'] > 0).mean():.1%}")
    print(f"  成功突破  n={len(tr_succ)}  均值 {m_succ:+.2%}  "
          f"中位 {tr_succ['val'].median():+.2%}  正比例 {(tr_succ['val'] > 0).mean():.1%}")

    boot_fail, boot_diff = date_block_bootstrap(cal_train, tr_fail, tr_succ,
                                                BLOCK_LEN, N_BOOT, SEED)
    lo, hi = np.percentile(boot_fail, [5, 95])
    dlo, dhi = np.percentile(boot_diff, [5, 95])

    p1 = hi < 0
    p2 = m_fail <= P2_FLOOR
    p3 = dhi < 0
    print(f"\n  [P1 方向性(单侧负向)] 自举 90%CI = [{lo:+.2%}, {hi:+.2%}]，"
          f"上界<0 → {'PASS' if p1 else 'FAIL'}")
    print(f"  [P2 效应量] 点估计 {m_fail:+.2%}（门槛 ≤{P2_FLOOR:.2%}） → {'PASS' if p2 else 'FAIL'}")
    print(f"  [P3 W-D对照] 失败−成功 = {m_fail - m_succ:+.2%}，"
          f"自举 90%CI = [{dlo:+.2%}, {dhi:+.2%}]，上界<0 → {'PASS' if p3 else 'FAIL'}")

    med = tr_fail["date"].median()
    h1 = tr_fail[tr_fail["date"] <= med]["val"]
    h2 = tr_fail[tr_fail["date"] > med]["val"]
    p4 = len(h1) > 0 and len(h2) > 0 and np.sign(h1.mean()) == np.sign(h2.mean())
    print(f"  [P4 时间稳健] 前半(n={len(h1)}) {h1.mean():+.2%} / "
          f"后半(n={len(h2)}) {h2.mean():+.2%} → {'PASS' if p4 else 'FAIL'}")

    # ---- P5 OOS 保留段（本 TASK 中只使用一次）----
    print(f"\n{'=' * 100}\nOOS 保留段（本 TASK 中只使用一次）\n{'=' * 100}")
    m_hold = ho_fail["val"].mean() if len(ho_fail) else np.nan
    same_sign = np.isfinite(m_hold) and np.sign(m_hold) == np.sign(m_fail)
    p5 = same_sign and m_hold <= P5_FLOOR
    print(f"  失败突破 n={len(ho_fail)}  均值 {m_hold:+.2%}  "
          f"正比例 {(ho_fail['val'] > 0).mean():.1%}" if len(ho_fail) else "  保留段无事件")
    print(f"  [P5 OOS] 与训练段同号={same_sign}，点估计 {m_hold:+.2%}"
          f"（门槛 ≤{P5_FLOOR:.2%}） → {'PASS' if p5 else 'FAIL'}")

    # ---- 报告级口径 ----
    print(f"\n{'=' * 100}\n报告级口径（功效不足，不参与判定）\n{'=' * 100}")
    for f in [10, 20]:
        r = pd.DataFrame({s: universe[s].set_index("date")[f"fwd{f}"] for s in syms}).sort_index()
        for kind, pn in [("原始收益", r), ("市场中性", r.sub(r.mean(axis=1), axis=0))]:
            if f == FWD and kind == "市场中性":
                continue
            vf = event_values(pn, event_frame(universe, syms, "fail_bo"))["val"]
            vs = event_values(pn, event_frame(universe, syms, "succ_bo"))["val"]
            print(f"  fwd{f:<3}[{kind}]  失败突破 n={len(vf)} 均值 {vf.mean():+.2%}   "
                  f"成功突破 n={len(vs)} 均值 {vs.mean():+.2%}")

    # ---- 判定映射 ----
    print(f"\n{'=' * 100}\n判定映射（预注册冻结）\n{'=' * 100}")
    print(f"  P1={p1}  P2={p2}  P3={p3}  P4={p4}  P5={p5}  P6={p6}")
    if p1 and p2 and p3 and p4 and p5:
        v = "失败突破单元初步成立 → 进入'作为 Donchian 退出/规避信号'的策略级检验"
    elif p1 and p2 and p4 and not p3:
        v = "部分成立：突破日本身有信息，但'收盘跌回'条件无增量 → 命题须改写后重走"
    elif p1 and p2 and p4 and not p5:
        v = "⭕ 不采纳：训练段成立但 OOS 未复现（不得以'OOS 样本少'为由豁免）"
    else:
        v = "⭕ 不采纳"
    print(f"  → {v}")
    print(f"\n  ⚠️ 解读边界（预注册冻结）：MDE≈1.04%，零结果只能解读为"
          f"'无 ≥1.0% 量级效应'，\n     不得解读为'失败突破无信息'。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
