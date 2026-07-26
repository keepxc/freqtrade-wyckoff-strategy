"""
W1-c：Spring/Test 信号有效性检验（预注册见 wyckoff_rebuild_tasks.md W1-c，判据跑数前冻结）

纯数据审计，禁碰 strategies/。事件扫描直接复用 w1_spring_lookahead_check.py
（已过 W0 截断复算测试），不重新实现。

原则 W-B 算式对齐核对（每条判据测的是机制里的哪个量）：
  P1 方向性   : 量 = 主定义事件组的「市场中性 fwd10」均值
                检验 = 按日期区块的跨资产联合自举 90%CI 下界 > 0
                注意自举必须重采样「零效应下的同规模均值」用于构造 CI —— 此处采用
                对事件本身重采样（配对保留日期区块结构），CI 描述的是该均值的抽样不确定性
  P2 效应量   : 量 = 同一均值的点估计（真实数据算出，非自举均值）
  P3 对照差   : 量 = 主定义均值 − 对照定义均值。两组事件同源同宇宙，差值自举同样
                按日期区块联合抽样，保留横截面相关
  P4 时间稳健 : 量 = 以事件日期中位数切分的前后两段主定义均值，要求同号
  P5 功效前提 : 量 = 主定义事件总数，< 200 则一票否决

市场中性定义（冻结）：ret_mn[i,t] = fwd10[i,t] - mean_j(fwd10[j,t])
  其中 j 遍历当日有数据的全部宇宙品种。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
from w1_power_analysis_multi import load_universe
from w1_spring_lookahead_check import spring_indicators, spring_indicators_control

sys.stdout.reconfigure(encoding="utf-8")

FWD_MAIN = 10          # 主口径 horizon（冻结）
BLOCK_LEN = 20
N_BOOT = 2000
SEED = 20260726
P2_FLOOR = 0.02        # 效应量下限 +2.0%（冻结）
P5_MIN_EVENTS = 200    # 功效前提（冻结）


def build_panels(universe, syms):
    """返回 {horizon: {'raw': panel, 'mn': panel}}，panel: index=日期, columns=币种。"""
    panels = {}
    for f in [10, 20]:
        raw = pd.DataFrame({s: universe[s].set_index("date")[f"fwd{f}"] for s in syms}).sort_index()
        mn = raw.sub(raw.mean(axis=1), axis=0)
        panels[f] = {"raw": raw, "mn": mn}
    return panels


def event_frame(universe, syms, col):
    """收集事件 (日期, 币种) 对。"""
    recs = []
    for s in syms:
        df = universe[s]
        idx = np.flatnonzero(df[col].values)
        for i in idx:
            recs.append((df["date"].iloc[i], s))
    return pd.DataFrame(recs, columns=["date", "sym"])


def event_values(panel, ev):
    """取事件对应的收益值（缺失剔除）。"""
    vals = []
    for d, s in zip(ev["date"], ev["sym"]):
        if d in panel.index and s in panel.columns:
            v = panel.at[d, s]
            if np.isfinite(v):
                vals.append((d, v))
    return pd.DataFrame(vals, columns=["date", "val"])


def _values_by_date_pos(ev_vals, date_pos):
    """把事件值按"日期在日历轴上的位置"归组，返回 {位置: [值,...]}。"""
    grouped = {}
    for d, v in zip(ev_vals["date"], ev_vals["val"]):
        p = date_pos.get(d)
        if p is not None:
            grouped.setdefault(p, []).append(v)
    return {k: np.array(v) for k, v in grouped.items()}


def date_block_bootstrap(calendar, ev_main, ev_ctrl, block_len, n_boot, seed):
    """按**日历日期区块**重采样，返回 (主定义均值分布, 主−对照差值分布)。

    为什么按日期轴而非事件轴重采样：
      - 主定义与对照定义是两组不同源、不等长的事件（对照不要求缩量，找到的 t2 可能
        不同），事件轴上不存在一一配对关系。按事件序号"按比例映射"来配对没有正当性。
      - 按日期区块抽样则天然配对：同一批被抽中的日期上，主/对照各自的事件一起进入，
        既保留时间自相关（区块）与横截面相关（同日多币同时触发），又让两组共享
        完全相同的日期抽样，差值检验才成立。
    某次重采样若某组无事件落入，则该次差值记为 NaN（后续按有效样本取分位数）。
    """
    rng = np.random.RandomState(seed)
    date_pos = {d: i for i, d in enumerate(calendar)}
    g_main = _values_by_date_pos(ev_main, date_pos)
    g_ctrl = _values_by_date_pos(ev_ctrl, date_pos)

    n_dates = len(calendar)
    n_blocks = int(np.ceil(n_dates / block_len))
    max_start = max(0, n_dates - block_len)

    means = np.full(n_boot, np.nan)
    diffs = np.full(n_boot, np.nan)
    for b in range(n_boot):
        pos = []
        for _ in range(n_blocks):
            s = rng.randint(0, max_start + 1) if max_start > 0 else 0
            pos.extend(range(s, min(s + block_len, n_dates)))
        m_vals, c_vals = [], []
        for p in pos:
            if p in g_main:
                m_vals.append(g_main[p])
            if p in g_ctrl:
                c_vals.append(g_ctrl[p])
        if m_vals:
            mm = np.concatenate(m_vals).mean()
            means[b] = mm
            if c_vals:
                diffs[b] = mm - np.concatenate(c_vals).mean()
    return means[np.isfinite(means)], diffs[np.isfinite(diffs)]


def main():
    universe, excluded = load_universe()
    syms = sorted(universe)

    print("=" * 100)
    print("W1-c：Spring/Test 信号有效性检验（判据已于预注册冻结）")
    print(f"宇宙 {len(syms)} 品种: {syms}")
    print(f"主口径: 市场中性 fwd{FWD_MAIN}；block={BLOCK_LEN}, n_boot={N_BOOT}, seed={SEED}")
    print("⚠️ 幸存者偏差：宇宙为存活至今品种，非点位时间宇宙（结构性局限，结论均带此前提）")
    print("=" * 100)

    for s in syms:
        universe[s]["spring_confirm"] = spring_indicators(universe[s])["spring_confirm"].values
        universe[s]["ctrl_confirm"] = spring_indicators_control(universe[s])["ctrl_confirm"].values

    panels = build_panels(universe, syms)
    ev_main = event_frame(universe, syms, "spring_confirm")
    ev_ctrl = event_frame(universe, syms, "ctrl_confirm")

    # ---- P5 功效前提（一票否决）----
    n_main_total = len(ev_main)
    p5 = n_main_total >= P5_MIN_EVENTS
    print(f"\n[P5 功效前提] 主定义事件总数 = {n_main_total}（门槛 ≥{P5_MIN_EVENTS}）"
          f" → {'PASS' if p5 else 'FAIL（一票否决）'}")
    if not p5:
        print("  功效前提失效，按预注册本次不得下结论。")
        return 1

    # ---- 主口径 ----
    pmn = panels[FWD_MAIN]["mn"]
    v_main = event_values(pmn, ev_main)
    v_ctrl = event_values(pmn, ev_ctrl)

    mean_main = v_main["val"].mean()
    mean_ctrl = v_ctrl["val"].mean()

    calendar = list(pmn.index)
    boot_main, boot_diff = date_block_bootstrap(calendar, v_main, v_ctrl,
                                                BLOCK_LEN, N_BOOT, SEED)
    lo, hi = np.percentile(boot_main, [5, 95])
    dlo, dhi = np.percentile(boot_diff, [5, 95])

    print(f"\n{'=' * 100}\n主口径：市场中性 fwd{FWD_MAIN}\n{'=' * 100}")
    print(f"  主定义(缩量)  n={len(v_main)}  均值 {mean_main:+.2%}  "
          f"中位 {v_main['val'].median():+.2%}  正比例 {(v_main['val'] > 0).mean():.1%}")
    print(f"  对照(无缩量)  n={len(v_ctrl)}  均值 {mean_ctrl:+.2%}  "
          f"中位 {v_ctrl['val'].median():+.2%}  正比例 {(v_ctrl['val'] > 0).mean():.1%}")

    p1 = lo > 0
    p2 = mean_main >= P2_FLOOR
    p3 = dlo > 0
    print(f"\n  [P1 方向性]   自举 90%CI = [{lo:+.2%}, {hi:+.2%}] → {'PASS' if p1 else 'FAIL'}")
    print(f"  [P2 效应量]   点估计 {mean_main:+.2%}（门槛 ≥{P2_FLOOR:+.1%}）"
          f" → {'PASS' if p2 else 'FAIL'}")
    print(f"  [P3 缩量贡献] 主−对照 = {mean_main - mean_ctrl:+.2%}，"
          f"自举 90%CI = [{dlo:+.2%}, {dhi:+.2%}] → {'PASS' if p3 else 'FAIL'}")

    # ---- P4 时间稳健 ----
    med_date = v_main["date"].median()
    h1 = v_main[v_main["date"] <= med_date]["val"]
    h2 = v_main[v_main["date"] > med_date]["val"]
    p4 = (len(h1) > 0 and len(h2) > 0) and (np.sign(h1.mean()) == np.sign(h2.mean()))
    print(f"  [P4 时间稳健] 前段(n={len(h1)}) {h1.mean():+.2%} / "
          f"后段(n={len(h2)}) {h2.mean():+.2%}（切分点 {pd.Timestamp(med_date).date()}）"
          f" → {'PASS' if p4 else 'FAIL'}")

    # ---- 报告级口径（功效不足，只报告不判定）----
    print(f"\n{'=' * 100}\n报告级口径（功效不足，不参与判定）\n{'=' * 100}")
    for f in [10, 20]:
        for kind, label in [("raw", "原始收益"), ("mn", "市场中性")]:
            if f == FWD_MAIN and kind == "mn":
                continue
            pn = panels[f][kind]
            vm = event_values(pn, ev_main)["val"]
            vc = event_values(pn, ev_ctrl)["val"]
            print(f"  fwd{f:<3}[{label}]  主定义 n={len(vm)} 均值 {vm.mean():+.2%}   "
                  f"对照 n={len(vc)} 均值 {vc.mean():+.2%}")

    # ---- 判定映射 ----
    print(f"\n{'=' * 100}\n判定映射（预注册冻结）\n{'=' * 100}")
    print(f"  P1={p1}  P2={p2}  P3={p3}  P4={p4}  P5={p5}")
    if p1 and p2 and p3 and p4:
        verdict = "Spring 单元初步成立 → 进入与 DonchianV1 的策略级对比（原则 W-E）"
    elif p1 and p2 and p4 and not p3:
        verdict = ("部分成立：结构性二次探底有效，但缩量条件无增量 → "
                   "方向改为'结构性二次探底'，剔除缩量条件后重走")
    elif not p4:
        verdict = "⭕ 不采纳（效应不稳定）"
    else:
        verdict = "⭕ 不采纳"
    print(f"  → {verdict}")
    print("\n  ⚠️ 零结果解读边界（预注册已冻结）：本检验 MDE≈2.52%，"
          "零结果只能解读为\n     '无 ≥2.5% 量级的效应'，不得解读为'威科夫无效'。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
