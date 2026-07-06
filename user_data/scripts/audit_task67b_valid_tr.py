import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import numpy as np, pandas as pd
from user_data.strategies.v8_modules.sc_detector import add_v8_sc_detection
from user_data.strategies.v8_modules.phase_detector import add_v8_phase_detection

for tag, path in [("futures", "user_data/data/binance/futures/BTC_USDT_USDT-1d-futures.feather"),
                  ("spot", "user_data/data/binance/BTC_USDT-1d.feather")]:
    df = pd.read_feather(path).sort_values("date").reset_index(drop=True)
    df = add_v8_sc_detection(df); df = add_v8_phase_detection(df)
    df["fwd20"] = df["close"].shift(-20) / df["close"] - 1
    warm = np.asarray(df.index >= 200)
    is_b = (df["v8_phase"].fillna("") == "B").values & warm
    rng = df["v8_tr_high"] - df["v8_tr_low"]
    df["tr_pos"] = np.where(rng > 0, (df["close"] - df["v8_tr_low"]) / rng, np.nan)
    b = df[is_b].copy()
    valid = b[(b["tr_pos"] >= -0.1) & (b["tr_pos"] <= 1.1)]
    inval = b[~b.index.isin(valid.index)]
    print(f"=== {tag}: B日 {len(b)}，TR有效 {len(valid)}，无效剔除 {len(inval)} ===")
    base = valid["fwd20"].dropna().mean()
    low3 = valid[valid["tr_pos"] <= 0.33]["fwd20"].dropna()
    rest = valid[valid["tr_pos"] > 0.33]["fwd20"].dropna()
    print(f"  有效B均值 {base:+.2%} | 下1/3 n={len(low3)} {low3.mean():+.2%} | 其余 n={len(rest)} {rest.mean():+.2%}"
          f" → 判据2' {'通过' if len(low3) and low3.mean() >= base else '未过'}")
    lb = valid[valid["tr_pos"] <= 0.33].dropna(subset=["v8_tr_low", "v8_tr_high"])
    if len(lb):
        stop_d = (lb["close"] - lb["v8_tr_low"] * 0.99) / lb["close"]
        tgt_high = (lb["v8_tr_high"] - lb["close"]) / lb["close"]
        print(f"  下1/3 几何: 止损中位 {stop_d.median():.2%}, 到TR High 中位 {tgt_high.median():+.2%}, RR中位 {(tgt_high/stop_d).median():.2f}")
    if len(inval):
        print(f"  被剔除天的 fwd20d 均值 {inval['fwd20'].dropna().mean():+.2%}（供状态机缺陷影响评估）")
