"""
W3：窄幅缩量下跌 bar（narrow_range_down_bar）信号有效性检验 —— 威科夫重建收官检验
预注册见 wyckoff_rebuild_tasks.md W3（判据冻结于 commit 42b2925，跑数前）

纯数据审计，禁碰 strategies/。主定义复用 w2_candidate_screening.py 中已过 W0
因果检查的实现，仅新增严格互补对照组。

**命名如实声明**：检验对象是"窄幅 + 缩量 + 下跌 bar"这一价格行为命题，
**不是威科夫 No Supply**（No Supply 要求上升趋势中的回调测试语境，本定义
无任何趋势/阶段前置条件）。结果不得归因到 No Supply 理论。

原则 W-B 算式对齐核对（每条判据测的是哪个量）：
  P1 : 量 = 训练段主定义组「市场中性 fwd10」均值 → 自举 90%CI 下界 > 0（单侧正向）
  P2 : 量 = 同一均值的点估计（真实数据，非自举均值） ≥ +2.33% (=1.5×MDE)
  P3 : 量 = 主定义均值 − **严格互补**对照组均值。
       两组共享「窄幅 ∧ 下跌」前提，唯一差异是成交量条件的真假，且
       **互不相交**（主定义要求 vol ≤ 0.5×ma，对照要求 vol > 0.5×ma），
       故该差值干净地隔离「缩量」这一个变量，不存在子集污染。
  P4 : 量 = 训练段内以事件日期中位数切分的前后两半均值，要求同号
  P5 : 量 = OOS 保留段均值，要求与训练段同号且 ≥ +1.16% (=floor/2)
  P6 : 量 = 训练段事件数，< 700 一票否决

保留段纪律：最后 20% 日历日期不参与 P1~P4，代码上先算完训练段再触碰。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
from w1_power_analysis_multi import load_universe
from w1c_spring_effect import date_block_bootstrap, event_frame, event_values
from w2_candidate_screening import _base, c4_no_supply

sys.stdout.reconfigure(encoding="utf-8")

FWD = 10
BLOCK_LEN = 20
N_BOOT = 2000
SEED = 20260726
P2_FLOOR = 0.0233        # 1.5 × MDE(1.55%)，冻结
P5_FLOOR = 0.0116        # floor / 2，冻结
P6_MIN_TRAIN_EVENTS = 700
HOLDOUT_FRAC = 0.20


def narrow_range_down_bar(df):
    """主定义：窄幅 ∧ 缩量 ∧ 下跌。复用摸底脚本实现（已过 W0）。"""
    return c4_no_supply(df)


def narrow_range_down_bar_complement(df):
    """W-D 严格互补对照：窄幅 ∧ **非缩量** ∧ 下跌。

    与主定义互不相交（vol ≤ 0.5×ma vs vol > 0.5×ma），故 P3 差值干净隔离
    「缩量」这一个变量；若改用"不要求缩量"的超集写法，主定义将成为对照组
    子集，对照均值被主定义事件污染、差值系统性稀释。
    """
    o = _base(df)
    o["nrdb_complement"] = (
        (o["_rng"] <= 0.5 * o["_rng_ma"])
        & (o["volume"] > 0.5 * o["_vol_ma"])
        & (o["close"] < o["close"].shift(1))
    ).fillna(False)
    return o[[c for c in o.columns if not c.startswith("_")]]


def main():
    universe, _ = load_universe()
    syms = sorted(universe)

    print("=" * 100)
    print("W3：窄幅缩量下跌 bar 信号有效性检验（威科夫收官检验，判据冻结于 42b2925）")
    print("⚠️ 检验对象是价格行为命题 narrow_range_down_bar，**不是威科夫 No Supply**")
    print("⚠️ 幸存者偏差：宇宙为存活至今品种，非点位时间宇宙")
    print("⚠️ 本次为同一批数据上第 3 个正式命题，未做多重比较校正，假阳性风险已累积")
    print(f"主口径: 市场中性 fwd{FWD}; block={BLOCK_LEN}, n_boot={N_BOOT}, seed={SEED}")
    print(f"冻结门槛: P2 ≥{P2_FLOOR:.2%}, P5 ≥{P5_FLOOR:.2%}, P6 训练段事件数 ≥{P6_MIN_TRAIN_EVENTS}")
    print("=" * 100)

    for s in syms:
        universe[s]["nrdb"] = narrow_range_down_bar(universe[s])["c4_no_supply"].values
        universe[s]["nrdb_c"] = narrow_range_down_bar_complement(universe[s])["nrdb_complement"].values
        # 互斥性自检（W-B）：两组不得有任何重叠
        overlap = int((universe[s]["nrdb"] & universe[s]["nrdb_c"]).sum())
        if overlap:
            print(f"❌ {s}: 主定义与互补对照有 {overlap} 处重叠，构造有误，中止")
            return 1

    raw = pd.DataFrame({s: universe[s].set_index("date")[f"fwd{FWD}"] for s in syms}).sort_index()
    mn = raw.sub(raw.mean(axis=1), axis=0)
    calendar = list(mn.index)

    ev_main = event_values(mn, event_frame(universe, syms, "nrdb"))
    ev_ctrl = event_values(mn, event_frame(universe, syms, "nrdb_c"))
    print(f"\n互斥性自检 ✅ 通过（主定义与互补对照零重叠）")

    split_pos = int(len(calendar) * (1 - HOLDOUT_FRAC))
    split_date = calendar[split_pos]
    cal_train = calendar[:split_pos]
    print(f"切分点: {pd.Timestamp(split_date).date()}  "
          f"训练段 {split_pos} 日 / 保留段 {len(calendar) - split_pos} 日")

    tr_main = ev_main[ev_main["date"] < split_date]
    tr_ctrl = ev_ctrl[ev_ctrl["date"] < split_date]
    ho_main = ev_main[ev_main["date"] >= split_date]

    # ---- P6 功效前提（一票否决）----
    p6 = len(tr_main) >= P6_MIN_TRAIN_EVENTS
    print(f"\n[P6 功效前提] 训练段主定义事件数 = {len(tr_main)}"
          f"（门槛 ≥{P6_MIN_TRAIN_EVENTS}） → {'PASS' if p6 else 'FAIL（一票否决）'}")
    if not p6:
        print("  功效前提失效，按预注册本次不得下结论。")
        return 1

    # ---- 训练段主分析 ----
    print(f"\n{'=' * 100}\n训练段主分析（保留段未参与）\n{'=' * 100}")
    m_main = tr_main["val"].mean()
    m_ctrl = tr_ctrl["val"].mean()
    print(f"  主定义(窄幅+缩量+下跌)   n={len(tr_main)}  均值 {m_main:+.2%}  "
          f"中位 {tr_main['val'].median():+.2%}  正比例 {(tr_main['val'] > 0).mean():.1%}")
    print(f"  互补对照(窄幅+放量+下跌) n={len(tr_ctrl)}  均值 {m_ctrl:+.2%}  "
          f"中位 {tr_ctrl['val'].median():+.2%}  正比例 {(tr_ctrl['val'] > 0).mean():.1%}")

    boot_main, boot_diff = date_block_bootstrap(cal_train, tr_main, tr_ctrl,
                                                BLOCK_LEN, N_BOOT, SEED)
    lo, hi = np.percentile(boot_main, [5, 95])
    dlo, dhi = np.percentile(boot_diff, [5, 95])

    p1 = lo > 0
    p2 = m_main >= P2_FLOOR
    p3 = dlo > 0
    print(f"\n  [P1 方向性(单侧正向)] 自举 90%CI = [{lo:+.2%}, {hi:+.2%}]，"
          f"下界>0 → {'PASS' if p1 else 'FAIL'}")
    print(f"  [P2 效应量] 点估计 {m_main:+.2%}（门槛 ≥{P2_FLOOR:.2%}） → {'PASS' if p2 else 'FAIL'}")
    print(f"  [P3 缩量增量] 主−互补对照 = {m_main - m_ctrl:+.2%}，"
          f"自举 90%CI = [{dlo:+.2%}, {dhi:+.2%}]，下界>0 → {'PASS' if p3 else 'FAIL'}")

    med = tr_main["date"].median()
    h1 = tr_main[tr_main["date"] <= med]["val"]
    h2 = tr_main[tr_main["date"] > med]["val"]
    p4 = len(h1) > 0 and len(h2) > 0 and np.sign(h1.mean()) == np.sign(h2.mean())
    print(f"  [P4 时间稳健] 前半(n={len(h1)}) {h1.mean():+.2%} / "
          f"后半(n={len(h2)}) {h2.mean():+.2%} → {'PASS' if p4 else 'FAIL'}")

    # ---- P5 OOS 保留段（本 TASK 中只使用一次）----
    print(f"\n{'=' * 100}\nOOS 保留段（本 TASK 中只使用一次）\n{'=' * 100}")
    m_hold = ho_main["val"].mean() if len(ho_main) else np.nan
    same_sign = np.isfinite(m_hold) and np.sign(m_hold) == np.sign(m_main)
    p5 = bool(same_sign and m_hold >= P5_FLOOR)
    if len(ho_main):
        print(f"  主定义 n={len(ho_main)}  均值 {m_hold:+.2%}  "
              f"正比例 {(ho_main['val'] > 0).mean():.1%}")
    print(f"  [P5 OOS] 与训练段同号={same_sign}，点估计 {m_hold:+.2%}"
          f"（门槛 ≥{P5_FLOOR:.2%}） → {'PASS' if p5 else 'FAIL'}")

    # ---- 报告级口径 ----
    print(f"\n{'=' * 100}\n报告级口径（功效不足，不参与判定）\n{'=' * 100}")
    for f in [10, 20]:
        r = pd.DataFrame({s: universe[s].set_index("date")[f"fwd{f}"] for s in syms}).sort_index()
        for kind, pn in [("原始收益", r), ("市场中性", r.sub(r.mean(axis=1), axis=0))]:
            if f == FWD and kind == "市场中性":
                continue
            vm = event_values(pn, event_frame(universe, syms, "nrdb"))["val"]
            vc = event_values(pn, event_frame(universe, syms, "nrdb_c"))["val"]
            print(f"  fwd{f:<3}[{kind}]  主定义 n={len(vm)} 均值 {vm.mean():+.2%}   "
                  f"互补对照 n={len(vc)} 均值 {vc.mean():+.2%}")

    # ---- 判定映射 ----
    print(f"\n{'=' * 100}\n判定映射（预注册冻结）\n{'=' * 100}")
    print(f"  P1={p1}  P2={p2}  P3={p3}  P4={p4}  P5={p5}  P6={p6}")
    if p1 and p2 and p3 and p4 and p5:
        v = "成立 → 转入策略级对决（W-E：封装为独立策略与 DonchianV1 正面比）"
    elif p1 and p2 and p4 and p5 and not p3:
        v = "窄幅下跌 bar 有效，但缩量教义证伪（须分开写，不得笼统称有效）"
    elif p1 and p2 and p4 and not p5:
        v = "⭕ 不采纳：训练段成立但 OOS 未复现（不得以'OOS 样本少'为由豁免）"
    else:
        v = "⭕ 不采纳 → 按停止规则 N=1，威科夫重建方向封板归档"
    print(f"  → {v}")
    print(f"\n  ⚠️ 解读边界（冻结）：MDE≈1.55%，零结果只能解读为'无 ≥1.55% 量级效应'，"
          f"\n     不得解读为'该价格行为无信息'。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
