"""
TASK 61：日线 Wyckoff 上下文层独立 edge 审计（观测性，不动策略代码）

背景：TASK 60 证实 1H 波段层未来函数贡献了旧基线 98.5% 利润。日线层
（SC/Phase/BC/UTAD/SOW/BE 状态机）经 Review 确认是因果实现，但其 edge
从未脱离被污染的 1H 执行层被独立度量过。本审计回答：日线论题本身是否成立。

方法：在日线收盘时点取状态/事件，计算 forward return（close[T] → close[T+h]），
与全样本无条件基准对比。事件在 T 日收盘可知 → 从 T 收盘起算前瞻，因果。

多头上下文（预期正向）：v8_phase D/E
空头事件（预期负向）：daily_bc_detected / daily_utad_detected /
                      daily_dist_sow_detected / buying_exhaustion
空头状态（预期负向）：close < EMA200（熊市 regime 门控的物理基础）
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import numpy as np
import pandas as pd

from user_data.strategies.v8_modules.sc_detector import add_v8_sc_detection
from user_data.strategies.v8_modules.phase_detector import add_v8_phase_detection
from wyckoff.daily_event_detector import DailyEventDetector

HORIZONS = [5, 10, 20]
DATA = [
    ("futures_1d", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "data", "binance", "futures", "BTC_USDT_USDT-1d-futures.feather")),
    ("spot_1d", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "..", "data", "binance", "BTC_USDT-1d.feather")),
]


def bootstrap_diff_ci(a: np.ndarray, b: np.ndarray, n_boot=5000, seed=42):
    """均值差 a-b 的 95% bootstrap CI"""
    rng = np.random.default_rng(seed)
    diffs = np.array([
        rng.choice(a, len(a)).mean() - rng.choice(b, len(b)).mean()
        for _ in range(n_boot)
    ])
    return np.percentile(diffs, 2.5), np.percentile(diffs, 97.5)


def report(label, mask, fwd, base_stats, direction):
    """direction: +1 = 预期正向（多头上下文），-1 = 预期负向（空头事件/状态）"""
    n = int(mask.sum())
    if n == 0:
        print(f"  {label:<28s} n=0（无样本）")
        return
    line = f"  {label:<28s} n={n:<5d}"
    for h in HORIZONS:
        v = fwd[h][mask].dropna()
        if len(v) == 0:
            line += f" | fwd{h}d: --"
            continue
        base_mean = base_stats[h]["mean"]
        delta = v.mean() - base_mean
        edge_ok = (delta * direction) > 0
        marker = "+" if edge_ok else "×"
        line += f" | fwd{h}d {v.mean():+.2%} (Δ{delta:+.2%}{marker})"
    print(line)
    # 主口径 fwd20d 给 bootstrap CI（样本 ≥20 才有意义）
    v20 = fwd[20][mask].dropna()
    base20 = fwd[20][~mask].dropna()
    if len(v20) >= 20:
        lo, hi = bootstrap_diff_ci(v20.values, base20.values)
        sig = "显著" if (lo > 0 or hi < 0) else "CI含零"
        print(f"  {'':<28s} fwd20d vs 补集 95%CI [{lo:+.2%}, {hi:+.2%}] ({sig})")


def main():
    for tag, path in DATA:
        df = pd.read_feather(path).sort_values("date").reset_index(drop=True)
        df = add_v8_sc_detection(df)
        df = add_v8_phase_detection(df)
        df = DailyEventDetector().detect(df)

        ema200 = df["close"].ewm(span=200, adjust=False).mean()
        warm = df.index >= 200  # EMA/检测器预热期排除

        fwd = {h: (df["close"].shift(-h) / df["close"] - 1) for h in HORIZONS}
        base_stats = {h: {"mean": fwd[h][warm].dropna().mean()} for h in HORIZONS}

        print(f"\n================ {tag}: {df['date'].min().date()} -> {df['date'].max().date()}, n={len(df)} ================")
        print(f"  无条件基准（预热后）: " + " | ".join(
            f"fwd{h}d {base_stats[h]['mean']:+.2%}" for h in HORIZONS))

        print("\n  -- 多头上下文（预期 Δ 为正）--")
        phase = df.get("v8_phase", pd.Series("", index=df.index)).fillna("")
        for ph in ["A", "B", "C", "D", "E"]:
            report(f"v8_phase == {ph}", (phase == ph) & warm, fwd, base_stats, +1)

        print("\n  -- 空头事件（预期 Δ 为负）--")
        for col in ["daily_bc_detected", "daily_utad_detected",
                    "daily_dist_sow_detected", "buying_exhaustion"]:
            if col in df.columns:
                mask = df[col].fillna(False).astype(bool) & warm
                report(col, mask, fwd, base_stats, -1)
            else:
                print(f"  {col:<28s} 列不存在")

        print("\n  -- 空头状态（预期 Δ 为负）--")
        report("close < EMA200", (df["close"] < ema200) & warm, fwd, base_stats, -1)
        # 连续 5 日低于 EMA200（daily_ema200_persist 的结构等价）
        below = (df["close"] < ema200)
        persist5 = below.rolling(5).sum() >= 5
        report("EMA200下方持续>=5日", persist5.fillna(False) & warm, fwd, base_stats, -1)


if __name__ == "__main__":
    main()
