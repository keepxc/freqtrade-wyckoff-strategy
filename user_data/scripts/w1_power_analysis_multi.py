"""
W1 第三步：多币种扩样后的功效分析（原则 W-F）

BTC 单币日线功效不足（见 w1_power_analysis_out.txt：事件数 22~43，
检出 +2% 效应需 n≥202~518）。本脚本评估扩到多币种宇宙后的功效。

**关键方法选择**：跨币种样本不独立（加密资产 beta 高），不能用
"事件数 ×15"来宣称功效提升。此处不使用任何手算的"有效 n"折算公式，
而是用 **按日期区块的跨资产联合自举** 构造零效应下的均值分布——
同一个日期区块内所有币种的收益被一起抽出，时间自相关与横截面相关
结构都由重采样自动保留，不需要假设相关系数。

宇宙选择规则（**先于任何结果冻结**）：
  - 取 user_data/data/binance/futures/ 下全部 *-1d-futures.feather
  - 剔除 bar 数 < MIN_BARS(1500) 的品种（HYPE 仅 366 根）
  - 不做任何基于收益/表现的筛选

**幸存者偏差（如实声明，无法修正）**：该列表是"当前数据目录中存在的品种"，
即存活至 2026 年的资产，非点位时间宇宙。已退市/归零的品种不在其中，
Spring 在这些品种上的表现无从得知。此为结构性局限，不是可通过调整
方法消除的问题；后续任何结论都必须带此前提。

本脚本仍**不计算事件组的实际收益**，MDE 完全独立于待检结果。
"""
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
from lookahead_truncation_test import truncation_test
from w1_spring_lookahead_check import spring_indicators, spring_indicators_control

sys.stdout.reconfigure(encoding="utf-8")

MIN_BARS = 1500
FWD = [10, 20]
Z_ALPHA2 = 1.959964
Z_BETA = 0.841621
BLOCK_LEN = 20
N_BOOT = 2000
SEED = 20260726


def load_universe():
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    paths = sorted(glob.glob(os.path.join(base, "data", "binance", "futures",
                                          "*-1d-futures.feather")))
    universe, excluded = {}, []
    for p in paths:
        sym = os.path.basename(p).split("_")[0]
        df = pd.read_feather(p).sort_values("date").reset_index(drop=True)
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
        if len(df) < MIN_BARS:
            excluded.append((sym, len(df)))
            continue
        for f in FWD:
            df[f"fwd{f}"] = df["close"].shift(-f) / df["close"] - 1.0
        universe[sym] = df
    return universe, excluded


def date_block_bootstrap_pooled(panel, n_events, block_len, n_boot, seed):
    """按日期区块的跨资产联合自举：抽日期区块 → 取该区块内所有币种的收益 →
    从中随机取 n_events 个求均值。时间自相关与横截面相关由重采样保留。

    panel: DataFrame，index=日期，columns=币种，值为该币种该日的 fwd 收益
    """
    rng = np.random.RandomState(seed)
    dates = panel.index.values
    n_dates = len(dates)
    max_start = n_dates - block_len
    vals_by_row = panel.values  # (n_dates, n_syms)

    means = np.zeros(n_boot)
    # 每次自举抽取足够多的日期区块，使池内非 NaN 观测数 >= n_events
    n_blocks = int(np.ceil(n_events / max(1, np.isfinite(vals_by_row).sum(axis=1).mean()))) + 2
    for b in range(n_boot):
        rows = []
        for _ in range(n_blocks):
            s = rng.randint(0, max_start + 1) if max_start > 0 else 0
            rows.extend(range(s, s + block_len))
        pool = vals_by_row[np.array(rows)].ravel()
        pool = pool[np.isfinite(pool)]
        if len(pool) < n_events:
            means[b] = pool.mean() if len(pool) else np.nan
            continue
        pick = rng.choice(len(pool), size=n_events, replace=False)
        means[b] = pool[pick].mean()
    return means[np.isfinite(means)]


def main():
    universe, excluded = load_universe()
    syms = sorted(universe)

    print("=" * 104)
    print("W1 第三步：多币种扩样功效分析（原则 W-F，MDE 独立于待检结果）")
    print("=" * 104)
    print(f"\n宇宙（规则先于结果冻结：全部 *-1d-futures，剔除 bar 数 < {MIN_BARS}）：")
    print(f"  纳入 {len(syms)} 个: {syms}")
    if excluded:
        print(f"  剔除: {[(s, n) for s, n in excluded]}（bar 数不足）")
    print("  ⚠️ 幸存者偏差：该列表为存活至今的品种，非点位时间宇宙，已退市/归零者不在其中。"
          "\n     此为结构性局限，无法通过方法修正，后续结论必须带此前提。")

    # 逐币种因果检查 + 事件计数
    print(f"\n{'=' * 104}\n逐币种：截断复算测试（因果性） + Spring 事件计数\n{'=' * 104}")
    print(f"  {'币种':<8}{'bars':>6}{'因果检查':>12}{'主定义事件':>12}{'对照事件':>10}")
    ev_main, ev_ctrl = {}, {}
    causal_all_pass = True
    for s in syms:
        df = universe[s]
        rep = truncation_test(spring_indicators, df, n_samples=120)
        rep_c = truncation_test(spring_indicators_control, df, n_samples=120)
        ok = rep.passed and rep_c.passed
        causal_all_pass = causal_all_pass and ok
        m = spring_indicators(df)["spring_confirm"]
        c = spring_indicators_control(df)["ctrl_confirm"]
        ev_main[s], ev_ctrl[s] = int(m.sum()), int(c.sum())
        universe[s]["spring_confirm"] = m.values
        universe[s]["ctrl_confirm"] = c.values
        print(f"  {s:<8}{len(df):>6}{'✅ PASS' if ok else '❌ FAIL':>12}"
              f"{ev_main[s]:>12}{ev_ctrl[s]:>10}")

    n_main, n_ctrl = sum(ev_main.values()), sum(ev_ctrl.values())
    print(f"\n  合计事件数：主定义 {n_main}，对照 {n_ctrl}"
          f"   （BTC 单币时分别为 {ev_main.get('BTC')} / {ev_ctrl.get('BTC')}）")
    print(f"  因果性：{'✅ 全部品种通过' if causal_all_pass else '❌ 存在违反，禁止继续'}")
    if not causal_all_pass:
        return 1

    # 功效分析
    print(f"\n{'=' * 104}\n功效分析：零效应下的均值分布（按日期区块的跨资产联合自举）\n{'=' * 104}")
    rows = []
    for f in FWD:
        panel = pd.DataFrame({s: universe[s].set_index("date")[f"fwd{f}"] for s in syms})
        panel = panel.sort_index()
        pooled = panel.values.ravel()
        pooled = pooled[np.isfinite(pooled)]
        sd = pooled.std(ddof=1)
        print(f"\n  fwd{f}: 池化噪声 sd={sd:.2%}  (总观测 {len(pooled)}，日期 {len(panel)})")

        for label, n_ev in [("主定义(缩量)", n_main), ("对照(无缩量)", n_ctrl)]:
            naive = (Z_ALPHA2 + Z_BETA) * sd / np.sqrt(n_ev)
            boot = date_block_bootstrap_pooled(panel, n_ev, BLOCK_LEN, N_BOOT, SEED)
            lo, hi = np.percentile(boot, [5, 95])
            hw = (hi - lo) / 2
            # 自举 sd → 考虑相关结构后的真实 MDE
            boot_sd = boot.std(ddof=1)
            mde_boot = (Z_ALPHA2 + Z_BETA) * boot_sd
            infl = boot_sd / (sd / np.sqrt(n_ev))
            print(f"    {label}  n={n_ev}")
            print(f"      朴素 MDE(假装独立，**不可用**)     : {naive:+.2%}")
            print(f"      自举 MDE(含时间+横截面相关)        : {mde_boot:+.2%}   "
                  f"← 相关性使方差膨胀 {infl:.1f}×")
            print(f"      零效应 90%CI                       : [{lo:+.2%}, {hi:+.2%}]（半宽 ±{hw:.2%}）")
            rows.append((f, label, n_ev, naive, mde_boot, infl))

    print(f"\n{'=' * 104}\n汇总 & 判读\n{'=' * 104}")
    print(f"  {'horizon':<10}{'定义':<16}{'n':>6}{'朴素MDE':>11}{'自举MDE':>11}{'方差膨胀':>10}")
    for f, label, n_ev, naive, mde_boot, infl in rows:
        print(f"  fwd{f:<7}{label:<16}{n_ev:>6}{naive:>10.2%}{mde_boot:>11.2%}{infl:>9.1f}×")

    best = min(r[4] for r in rows)
    worst = max(r[4] for r in rows)
    print(f"\n  多币种扩样后自举 MDE 区间: {best:.2%} ~ {worst:.2%}")
    print("  （BTC 单币时为 4.67% ~ 9.14%，且当时未计入横截面相关）")
    print("\n  判读：朴素 MDE 与自举 MDE 的差距即'假装样本独立'会带来的高估幅度。")
    print("        以自举 MDE 为准来决定预注册中应检验的效应量级。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
