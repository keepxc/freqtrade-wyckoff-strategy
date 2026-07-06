import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import numpy as np, pandas as pd
from user_data.strategies.v8_modules.sc_detector import add_v8_sc_detection
from user_data.strategies.v8_modules.phase_detector import add_v8_phase_detection

for tag, path in [("futures", "user_data/data/binance/futures/BTC_USDT_USDT-1d-futures.feather"),
                  ("spot", "user_data/data/binance/BTC_USDT-1d.feather")]:
    df = pd.read_feather(path).sort_values("date").reset_index(drop=True)
    df = add_v8_sc_detection(df)
    df = add_v8_phase_detection(df)
    warm = (df.index >= 200).values if hasattr(df.index >= 200, "values") else np.asarray(df.index >= 200)
    fwd20 = df["close"].shift(-20) / df["close"] - 1

    ph = df["v8_phase"].fillna("")
    # E 相内的"进入E后天数"（每次进入E重新计数）
    days_in_e = np.full(len(df), -1)
    cnt = 0
    for i in range(len(df)):
        if ph.iloc[i] == "E":
            cnt = cnt + 1 if (i > 0 and ph.iloc[i-1] == "E") else 1
            days_in_e[i] = cnt
        else:
            cnt = 0
    base = fwd20[warm].dropna().mean()
    print(f"=== {tag}: 无条件基准 fwd20d {base:+.2%} ===")
    for lo, hi, label in [(1,5,"E第1-5天"),(6,10,"E第6-10天"),(11,20,"E第11-20天"),(21,60,"E第21-60天"),(61,10**9,"E第61天+")]:
        m = (days_in_e >= lo) & (days_in_e <= hi) & warm
        v = fwd20[m].dropna()
        if len(v)==0: continue
        print(f"  {label:<10s} n={len(v):<5d} fwd20d {v.mean():+.2%} (Δ{v.mean()-base:+.2%}) 中位 {v.median():+.2%} 正率 {(v>0).mean():.0%}")
    # 对照：D 相
    mD = (ph == "D").values & warm
    v = fwd20[mD].dropna()
    print(f"  {'Phase D':<10s} n={len(v):<5d} fwd20d {v.mean():+.2%} (Δ{v.mean()-base:+.2%})")
    print()
