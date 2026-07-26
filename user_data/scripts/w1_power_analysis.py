"""
W1 第二步：功效分析（原则 W-F —— 先算功效，再跑回测）

目的：在跑任何收益评估**之前**回答——以 Spring 确认事件的实际数量，
当前样本量能检测出多大的效应？若最小可检测效应(MDE)远大于威科夫的合理
预期效应，则该实验从设计上就不可能给出结论，应改设计而非跑完再纠结 CI。

**本脚本刻意不计算事件组的实际收益**，只用"全体交易日"的收益分布估计
噪声水平。这样 MDE 的计算完全独立于待检验的结果，避免"看到结果再定标准"。

方法：
  - 噪声水平 sd：全体交易日 fwd{10,20} 收益的标准差（事件组同分布假设下的保守估计）
  - 解析 MDE：单样本 t 检验，MDE = (z_{α/2} + z_β) * sd / sqrt(n)，α=0.05 双侧，功效 0.80
  - 区块自举 MDE：BTC 日收益有自相关与波动聚集，解析式会低估所需效应。
    用区块自举（block=20 交易日）重采样 n 个事件的 fwd 收益均值分布，
    取其 90% 分位宽度反推可检测效应
  - 多重比较：2 数据集 × 2 定义 × 2 horizon = 8 次检验；报告 Bonferroni 校正后的 MDE
    （两个数据集是同一条 BTC 价格路径的衍生品，高度相关，实际有效检验数介于 4~8 之间，
     故同时报告未校正与校正两版，不做单一硬性选择）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
from w1_spring_lookahead_check import spring_indicators, spring_indicators_control

sys.stdout.reconfigure(encoding="utf-8")

FWD = [10, 20]
ALPHA = 0.05
POWER = 0.80
Z_ALPHA2 = 1.959964      # 双侧 α=0.05
Z_BETA = 0.841621        # 功效 0.80
BLOCK_LEN = 20
N_BOOT = 2000
SEED = 20260726
N_TESTS = 8              # 2 数据集 × 2 定义 × 2 horizon
Z_ALPHA2_BONF = 2.888    # 双侧 α=0.05/8


def block_bootstrap_means(series_vals, n_events, block_len, n_boot, seed):
    """区块自举：重采样 n_events 个事件的 fwd 收益均值分布。

    以区块为单位抽样以保留自相关/波动聚集结构，再截取 n_events 个观测求均值。
    """
    rng = np.random.RandomState(seed)
    n = len(series_vals)
    n_blocks = int(np.ceil(n_events / block_len))
    max_start = n - block_len
    means = np.zeros(n_boot)
    for b in range(n_boot):
        idx = []
        for _ in range(n_blocks):
            s = rng.randint(0, max_start + 1) if max_start > 0 else 0
            idx.extend(range(s, s + block_len))
        means[b] = series_vals[np.array(idx[:n_events])].mean()
    return means


def main():
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    datasets = [
        ("BTC-spot", os.path.join(base, "data", "binance", "BTC_USDT-1d.feather")),
        ("BTC-futures", os.path.join(base, "data", "binance", "futures",
                                     "BTC_USDT_USDT-1d-futures.feather")),
    ]

    print("=" * 100)
    print("W1 第二步：功效分析（先算功效，再跑回测 —— 原则 W-F）")
    print(f"α={ALPHA} 双侧, 功效={POWER}, 区块自举 block={BLOCK_LEN} × {N_BOOT} 次")
    print("注：本脚本不计算事件组实际收益，MDE 完全独立于待检结果")
    print("=" * 100)

    rows = []
    for tag, path in datasets:
        df = pd.read_feather(path).sort_values("date").reset_index(drop=True)
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
        for f in FWD:
            df[f"fwd{f}"] = df["close"].shift(-f) / df["close"] - 1.0

        main_out = spring_indicators(df)
        ctrl_out = spring_indicators_control(df)

        print(f"\n{'=' * 100}\n{tag}: {df['date'].iloc[0].date()} ~ {df['date'].iloc[-1].date()}"
              f"  n_bars={len(df)}")

        for f in FWD:
            vals = df[f"fwd{f}"].dropna().values
            sd = vals.std(ddof=1)
            print(f"\n  fwd{f} 全体交易日噪声水平: sd={sd:.2%}  (n={len(vals)})")

            for defname, col, out in [("主定义(缩量)", "spring_confirm", main_out),
                                      ("对照(无缩量)", "ctrl_confirm", ctrl_out)]:
                n_ev = int(out[col].sum())
                if n_ev == 0:
                    print(f"    {defname}: 事件数 0，无法评估")
                    continue

                mde = (Z_ALPHA2 + Z_BETA) * sd / np.sqrt(n_ev)
                mde_bonf = (Z_ALPHA2_BONF + Z_BETA) * sd / np.sqrt(n_ev)

                boot_means = block_bootstrap_means(vals, n_ev, BLOCK_LEN, N_BOOT, SEED)
                lo, hi = np.percentile(boot_means, [5, 95])
                boot_halfwidth = (hi - lo) / 2

                print(f"    {defname}  事件数 n={n_ev}")
                print(f"      解析 MDE(未校正)      : {mde:+.2%}   ← 需达到此效应才有 80% 把握检出")
                print(f"      解析 MDE(Bonferroni×8): {mde_bonf:+.2%}")
                print(f"      区块自举 90%CI 半宽   : ±{boot_halfwidth:.2%}  "
                      f"(零效应下 n={n_ev} 的均值波动范围 [{lo:+.2%}, {hi:+.2%}])")
                rows.append((tag, f, defname, n_ev, mde, mde_bonf, boot_halfwidth))

    print("\n" + "=" * 100)
    print("汇总：最小可检测效应 (MDE) vs 事件数")
    print("=" * 100)
    print(f"  {'数据集':<14}{'horizon':<9}{'定义':<16}{'n':>5}{'解析MDE':>11}"
          f"{'MDE(校正)':>12}{'自举半宽':>11}")
    for tag, f, defname, n_ev, mde, mde_bonf, hw in rows:
        print(f"  {tag:<14}fwd{f:<6}{defname:<16}{n_ev:>5}{mde:>10.2%}"
              f"{mde_bonf:>12.2%}{hw:>11.2%}")

    print("\n" + "=" * 100)
    print("判读（原则 W-F）")
    print("=" * 100)
    worst = max(r[4] for r in rows)
    best = min(r[4] for r in rows)
    print(f"  解析 MDE 区间: {best:.2%} ~ {worst:.2%}（未做多重比较校正）")
    print("  含义：Spring 事件组的 fwd 收益均值，必须比全体交易日基线高出上述量级，")
    print("        才有 80% 把握在 α=0.05 下被检出。低于此量级 → 实验无法给出结论，")
    print("        跑出来的任何 CI 跨零都不能解读为'威科夫无效'，只能解读为'测不出'。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
