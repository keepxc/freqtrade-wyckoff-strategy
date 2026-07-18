"""
方法论独立重设计（2026-07-18）：区块自举检验的功效分析——不引用 TASK 100 结果

背景：CLAUDE.md 里 P(总delta>0)>=0.90 这个阈值是类比统计惯例（90%置信）拍板的，
没有做过功效分析。用户指出该阈值可能对低频策略结构性过严，要求独立于任何具体
TASK 结果重新设计判据——本脚本不看 TASK 100 的任何输出，只用合成注入信号回答：
"这套区块自举检验在不同 P 阈值下，功效(检出真实优势的概率)和假阳性率各是多少？"

方法：
1. 用真实 BTC-futures 日线收益序列作为噪声背景（保留真实市场的波动率/自相关结构，
   比纯高斯噪声更贴近实际检验环境）。
2. 零假设(H0)模拟：不注入任何优势，用两条独立随机子集模拟"集成"和"基线"，
   跑自举检验，统计"假阳性率"= P(delta>0)>=阈值 的比例（应该接近声称的显著性水平）。
3. 备择假设(H1)模拟：注入已知大小的合成优势（在M个随机bar上给"集成"额外的
   固定正收益增量，M/bar数模拟真实TASK100观察到的"仅2/3一致"分歧频率量级），
   跑自举检验，统计"检出率"(功效)= P(delta>0)>=阈值 的比例。
4. 对多个候选阈值(0.75/0.80/0.85/0.90/0.95)和多个注入强度，输出功效-假阳性权衡表。

样本量对齐 TASK 100 实际情形：n_bars≈1645（主区间量级），block_len=20。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
FUT_PATH = os.path.join(BASE, "data", "binance", "futures", "BTC_USDT_USDT-1d-futures.feather")

BLOCK_LEN = 20
N_BOOT = 1000
N_TRIALS = 300           # 每个(注入强度,场景)重复的独立模拟次数
SEED = 20260718
CANDIDATE_THRESH = [0.75, 0.80, 0.85, 0.90, 0.95]
# 注入强度：每笔分歧 bar 给"集成"额外多少收益，以及分歧 bar 的数量占比
# 参照 TASK 100 主区间实测：约 24 笔交易、1645 根 bar，分歧多为个别边际 bar
INJECT_SCENARIOS = [
    ("零效应(H0/假阳性率)", 0.0, 0),
    ("微弱效应", 0.0008, 15),   # 15根bar，每根+0.08%（约等于1笔交易的边际差异量级）
    ("TASK100量级效应", 0.0012, 25),  # 近似主区间实测delta量级
    ("强效应", 0.003, 40),
]


def load_returns(path):
    df = pd.read_feather(path).sort_values('date').reset_index(drop=True)
    closes = df['close'].values
    ret = np.zeros(len(df))
    ret[1:] = closes[1:] / closes[:-1] - 1
    return ret


def block_bootstrap_p(diff, block_len, n_boot, rng):
    n = len(diff)
    n_blocks = int(np.ceil(n / block_len))
    max_start = n - block_len
    totals = np.zeros(n_boot)
    for b in range(n_boot):
        idx = []
        for _ in range(n_blocks):
            start = rng.randint(0, max_start + 1) if max_start > 0 else 0
            idx.extend(range(start, start + block_len))
        idx = np.array(idx[:n])
        totals[b] = diff[idx].sum()
    return float((totals > 0).mean())


def simulate_scenario(base_ret, inject_mag, n_inject, n_trials, rng):
    n = len(base_ret)
    p_values = []
    for _ in range(n_trials):
        # 基线/集成各自独立重排真实收益序列，制造与真实数据同等波动率的
        # 双侧噪声背景（而非彼此复制），diff 天然有正有负，不再退化为恒定符号
        base_sim = base_ret[rng.permutation(n)]
        ens_sim = base_ret[rng.permutation(n)]
        if n_inject > 0:
            inject_idx = rng.choice(n, size=n_inject, replace=False)
            ens_sim[inject_idx] += inject_mag
        diff = ens_sim - base_sim
        p = block_bootstrap_p(diff, BLOCK_LEN, N_BOOT, rng)
        p_values.append(p)
    return np.array(p_values)


def main():
    print("=" * 78)
    print("区块自举检验功效分析（独立于 TASK 100 结果，先设计判据再应用）")
    print(f"block_len={BLOCK_LEN}, n_boot(每次内层)={N_BOOT}, n_trials(外层重复)={N_TRIALS}")
    print("=" * 78)

    base_ret = load_returns(FUT_PATH)
    rng = np.random.RandomState(SEED)

    all_results = {}
    for tag, mag, n_inj in INJECT_SCENARIOS:
        pvals = simulate_scenario(base_ret, mag, n_inj, N_TRIALS, rng)
        all_results[tag] = pvals
        print(f"\n--- {tag}（注入幅度={mag:+.2%}/bar, 注入bar数={n_inj}） ---")
        print(f"  P(delta>0) 分布: 均值{pvals.mean():.3f}  中位{np.median(pvals):.3f}  "
              f"[5%,95%]=[{np.percentile(pvals,5):.3f},{np.percentile(pvals,95):.3f}]")
        for th in CANDIDATE_THRESH:
            rate = (pvals >= th).mean()
            print(f"  阈值{th:.2f}: 触发率(该场景下P>=阈值的比例) = {rate:.1%}")

    print("\n" + "=" * 78)
    print("汇总：各阈值的假阳性率(H0) vs 功效(各注入场景)")
    print("=" * 78)
    header = "阈值   假阳性率   " + "   ".join(t for t, _, _ in INJECT_SCENARIOS[1:])
    print(f"  {header}")
    for th in CANDIDATE_THRESH:
        fp = (all_results[INJECT_SCENARIOS[0][0]] >= th).mean()
        power_strs = []
        for tag, _, _ in INJECT_SCENARIOS[1:]:
            power_strs.append(f"{(all_results[tag] >= th).mean():.1%}")
        print(f"  {th:.2f}   {fp:.1%}      " + "        ".join(power_strs))

    print("\n结论性观察（供人工判断阈值取舍，不自动下结论）：")
    print("  - 假阳性率应接近 1-阈值（如阈值0.90，理想假阳性率≈10%）——若实测远高于此，")
    print("    说明区块自举本身对该样本量/区块长度存在偏差，需要调整block_len或n_boot，而不只是调阈值。")
    print("  - 功效低于50%的场景意味着即使真实存在该量级效应，这套检验也大概率检不出——")
    print("    这类场景下'不显著'不能反向解读为'没有效应'，只能解读为'检验功效不足'。")


if __name__ == "__main__":
    main()
