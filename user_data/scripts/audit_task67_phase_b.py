"""
TASK 67（预研）：Phase B 入场设计——edge 结构定位 + 稳健性检验

背景：TASK 61 发现 Phase B fwd20d Δ+3.78pp(futures)/+4.03pp(spot) 双显著，
但当前策略 phase_block=['A','B'] 不交易 B，且 B 相无 PNF 目标（设计使然）。

预注册判据（跑数前写定）：
  进入实现设计，当且仅当：
  1. 情节级稳健：按 B 情节（连续 B 日段，n≥10 天）分解，两套数据上
     均有 ≥2/3 情节的平均 fwd20d 为正（B 相稀疏，年度切分太薄，情节是
     结构上诚实的分解单位；防单一情节拉动聚合均值）
  2. 位置可交易：TR 下三分之一（威科夫 ST 测试区）的 B 日 edge 不低于
     B 全体平均（edge 必须在可入场、有紧结构止损的位置，否则无法转化）
  3. 回测窗口覆盖：futures 2022-2026 内存在 ≥3 个 B 情节（否则无法验证）
辅助观测（不设门槛，供设计参考）：
  - B 龄分层（TASK 34 phase_b_min_days 软锁相关）
  - TR 下沿入场的几何 RR：到 TR Low 的止损距离 vs 到 TR High/mid 的目标距离
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import numpy as np
import pandas as pd

from user_data.strategies.v8_modules.sc_detector import add_v8_sc_detection
from user_data.strategies.v8_modules.phase_detector import add_v8_phase_detection

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DATA = [
    ("futures", os.path.join(BASE, "data", "binance", "futures", "BTC_USDT_USDT-1d-futures.feather")),
    ("spot", os.path.join(BASE, "data", "binance", "BTC_USDT-1d.feather")),
]


def main():
    for tag, path in DATA:
        df = pd.read_feather(path).sort_values("date").reset_index(drop=True)
        df = add_v8_sc_detection(df)
        df = add_v8_phase_detection(df)
        df["fwd20"] = df["close"].shift(-20) / df["close"] - 1
        warm = np.asarray(df.index >= 200)
        ph = df["v8_phase"].fillna("")
        is_b = (ph == "B").values & warm

        # TR 内位置（0=TR Low，1=TR High）
        rng = df["v8_tr_high"] - df["v8_tr_low"]
        df["tr_pos"] = np.where(rng > 0, (df["close"] - df["v8_tr_low"]) / rng, np.nan)

        # B 情节切分 + B 龄
        b_age = np.zeros(len(df), dtype=int)
        ep_id = np.full(len(df), -1)
        eid, cnt = -1, 0
        for i in range(len(df)):
            if is_b[i]:
                if i > 0 and is_b[i - 1]:
                    cnt += 1
                else:
                    eid += 1
                    cnt = 1
                b_age[i] = cnt
                ep_id[i] = eid
            else:
                cnt = 0

        b = df[is_b].copy()
        b["ep"] = ep_id[is_b]
        b["age"] = b_age[is_b]
        base = b["fwd20"].dropna().mean()
        print(f"\n========== {tag}: B 日 n={len(b)}, 情节数={b['ep'].nunique()} ==========")
        print(f"  B 全体 fwd20d 均值 {base:+.2%}")

        # 判据1：情节级
        print("  -- 情节分解（n≥10 天）--")
        pos_cnt = tot_cnt = 0
        for e, g in b.groupby("ep"):
            v = g["fwd20"].dropna()
            if len(v) < 10:
                continue
            tot_cnt += 1
            m = v.mean()
            pos_cnt += int(m > 0)
            d0, d1 = g["date"].iloc[0].date(), g["date"].iloc[-1].date()
            print(f"    {d0} ~ {d1}  n={len(v):<4d} fwd20d {m:+.2%} tr_pos中位 {g['tr_pos'].median():.2f}")
        frac = f"{pos_cnt}/{tot_cnt}"
        ok1 = tot_cnt > 0 and pos_cnt / tot_cnt >= 2 / 3
        print(f"  判据1（≥2/3 情节为正）：{frac} → {'通过' if ok1 else '未过'}")

        # 判据2：TR 下三分之一
        low3 = b[b["tr_pos"] <= 0.33]["fwd20"].dropna()
        mid_up = b[b["tr_pos"] > 0.33]["fwd20"].dropna()
        print(f"  判据2（TR下1/3 edge ≥ B均值）：下1/3 n={len(low3)} {low3.mean():+.2%} "
              f"vs 其余 n={len(mid_up)} {mid_up.mean():+.2%} vs B均值 {base:+.2%} → "
              f"{'通过' if (len(low3) and low3.mean() >= base) else '未过'}")

        # 辅助：B 龄分层（TASK 34 软锁相关）
        print("  -- 辅助：B 龄分层 --")
        for lo, hi, lab in [(1, 10, "第1-10天"), (11, 20, "第11-20天"), (21, 10**9, "第21天+")]:
            v = b[(b["age"] >= lo) & (b["age"] <= hi)]["fwd20"].dropna()
            if len(v):
                print(f"    {lab:<10s} n={len(v):<5d} fwd20d {v.mean():+.2%} 正率 {(v > 0).mean():.0%}")

        # 辅助：TR 下沿几何 RR（止损=TR Low 下方1%，目标=TR mid / TR High）
        lb = b[b["tr_pos"] <= 0.33].dropna(subset=["v8_tr_low", "v8_tr_high"])
        if len(lb):
            stop_d = (lb["close"] - lb["v8_tr_low"] * 0.99) / lb["close"]
            tgt_mid = ((lb["v8_tr_low"] + lb["v8_tr_high"]) / 2 - lb["close"]) / lb["close"]
            tgt_high = (lb["v8_tr_high"] - lb["close"]) / lb["close"]
            rr_mid = (tgt_mid / stop_d).replace([np.inf, -np.inf], np.nan)
            rr_high = (tgt_high / stop_d).replace([np.inf, -np.inf], np.nan)
            print(f"  -- 辅助：TR下1/3 入场几何（n={len(lb)}）--")
            print(f"    止损距离中位 {stop_d.median():.2%} | 到TR mid 中位 {tgt_mid.median():+.2%} "
                  f"(RR {rr_mid.median():.2f}) | 到TR High 中位 {tgt_high.median():+.2%} (RR {rr_high.median():.2f})")

        # 判据3：futures 回测窗口覆盖
        if tag == "futures":
            in_win = b[(b["date"] >= "2022-01-01") & (b["date"] <= "2026-07-04")]
            n_ep = in_win["ep"].nunique()
            print(f"  判据3（2022-2026 内 ≥3 情节）：{n_ep} 个情节 → {'通过' if n_ep >= 3 else '未过'}")


if __name__ == "__main__":
    main()
