"""
TASK 81（预研）：短视界均值回归电池——价格原语地图的最后一块空白

背景（自主立项）：原语层审判至今：趋势✅、无符号量能❌、带符号流❌、
价格行为形态❌、carry✅。价格系原语中唯一未测的是短视界反转
（"暴跌后接刀"是否有超额）。RSI超卖/布林下轨/威廉指标等零售指标全部
同构于此原语，本电池一次审判整族。

编码（跑数前固定，零调参）：
  信号：k 日收益的因果百分位（过去252日窗口）≤ 0.10（极端下跌分位）
  k ∈ {3, 7}（申明两档全报告，不做选择）
  视界：前瞻 5/10 日
  regime 分层：全样本 / 牛市(close≥EMA200) / 熊市(close<EMA200)
  ——反转在牛市 regime 的"回调买入"形态先验最高 [COMMON]
资产：BTC/ETH futures 日线。edge = 条件均值 − 无条件基线。

预注册判据（四重 + 双资产联合）：
  R1 edge bootstrap 95% CI 不含 0
  R2 效应量 ≥ 0.15%/天（5d≥0.75%，10d≥1.5%）
  R3 时间对半切同号
  R4 BTC 与 ETH 同 regime 同参数联合通过
多重检验申明：2 lookback × 2 视界 × 3 regime = 12 检验/资产；
双资产联合要求使噪声假阳性 ≈ (5%)² 量级/检验。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import numpy as np
import pandas as pd

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
FUT = os.path.join(BASE, "data", "binance", "futures")
ASSETS = ["BTC", "ETH"]
LOOKBACKS = [3, 7]
HORIZONS = [5, 10]
PCT_WINDOW = 252
PCT_THRESH = 0.10
FLOOR_PER_DAY = 0.0015
N_BOOT = 2000
SEED = 20260708


def causal_pct_rank(v, window):
    out = np.full(len(v), np.nan)
    for i in range(window, len(v)):
        out[i] = (v[i - window:i] < v[i]).mean()
    return out


def boot_ci(rets, uncond, rng):
    boot = np.array([rng.choice(rets, len(rets)).mean() for _ in range(N_BOOT)]) - uncond
    return np.percentile(boot, 2.5), np.percentile(boot, 97.5)


def main():
    rng = np.random.default_rng(SEED)
    results = {}
    for asset in ASSETS:
        df = pd.read_feather(os.path.join(FUT, f"{asset}_USDT_USDT-1d-futures.feather"))
        df = df.sort_values('date').reset_index(drop=True)
        c = df['close'].values
        ema200 = df['close'].ewm(span=200, adjust=False).mean().values
        bull = c >= ema200
        n = len(df)
        mid = n // 2
        print(f"\n========== {asset}: n={n} ==========")
        for k in LOOKBACKS:
            ret_k = np.full(n, np.nan)
            ret_k[k:] = c[k:] / c[:-k] - 1
            pct = causal_pct_rank(ret_k, PCT_WINDOW)
            sig = pct <= PCT_THRESH
            for h in HORIZONS:
                fwd = np.full(n, np.nan)
                fwd[:-h] = c[h:] / c[:-h] - 1
                valid = ~np.isnan(fwd) & ~np.isnan(pct)
                uncond = fwd[valid].mean()
                for regime, mask in [("all", valid), ("bull", valid & bull), ("bear", valid & ~bull)]:
                    ev = np.where(sig & mask)[0]
                    if len(ev) < 30:
                        print(f"  k={k} fwd{h:>2d} {regime:<4s}: 样本不足 n={len(ev)}")
                        results[(asset, k, h, regime)] = None
                        continue
                    rets = fwd[ev]
                    base = fwd[mask].mean()  # regime 内基线，控制 regime 本身的漂移
                    edge = rets.mean() - base
                    lo, hi = boot_ci(rets, base, rng)
                    r1 = (lo > 0) or (hi < 0)
                    r2 = abs(edge) >= FLOOR_PER_DAY * h
                    e1, e2 = ev[ev < mid], ev[ev >= mid]
                    r3 = (len(e1) >= 10 and len(e2) >= 10
                          and np.sign(fwd[e1].mean() - base) == np.sign(fwd[e2].mean() - base) == np.sign(edge))
                    ok = r1 and r2 and r3
                    results[(asset, k, h, regime)] = (edge, ok)
                    print(f"  k={k} fwd{h:>2d} {regime:<4s}: n={len(ev):4d} edge {edge:+.3%} "
                          f"CI[{lo:+.3%},{hi:+.3%}] R1{'√' if r1 else '×'} "
                          f"R2{'√' if r2 else '×'} R3{'√' if r3 else '×'} "
                          f"{'** 单资产过 **' if ok else ''}")
    print("\n" + "=" * 70)
    n_joint = 0
    for k in LOOKBACKS:
        for h in HORIZONS:
            for regime in ["all", "bull", "bear"]:
                a = results.get(("BTC", k, h, regime))
                b = results.get(("ETH", k, h, regime))
                if a and b and a[1] and b[1] and np.sign(a[0]) == np.sign(b[0]):
                    n_joint += 1
                    print(f"  ✅ k={k} fwd{h} {regime}: BTC {a[0]:+.3%} / ETH {b[0]:+.3%}")
    if n_joint == 0:
        print("  R4 双资产联合通过：（无）")
    print(f"\n总判定: {n_joint} 个组合联合通过 → "
          f"{'进入策略层评估' if n_joint else '短视界均值回归原语关闭，价格系原语地图completa'}")


if __name__ == "__main__":
    main()
