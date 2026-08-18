"""
探索性——非结论，禁止引用为证据。v1基础上补：中位数、去极值稳健性检查、
三币联合区块自举CI（按trade的entry日期区块重采样，抵消跨币相关性虚高）。
"""
import pandas as pd
import numpy as np

FILES = {
    "BTC": "user_data/data/binance/futures/BTC_USDT_USDT-1d-futures.feather",
    "ETH": "user_data/data/binance/futures/ETH_USDT_USDT-1d-futures.feather",
    "SOL": "user_data/data/binance/futures/SOL_USDT_USDT-1d-futures.feather",
}

ENTRY_N = 20
EXIT_N = 10
MA_N = 200
BLOCK_DAYS = 90
N_BOOT = 2000


def simulate(df, sym):
    df = df.sort_values("date").reset_index(drop=True)
    df["hh20"] = df["high"].rolling(ENTRY_N).max().shift(1)
    df["ll10"] = df["low"].rolling(EXIT_N).min().shift(1)
    df["sma200"] = df["close"].rolling(MA_N).mean().shift(1)
    df["regime"] = np.where(df["close"] > df["sma200"], "bull", "bear")

    trades = []
    in_pos = False
    entry_price = None
    entry_regime = None
    entry_date = None
    for i in range(len(df)):
        row = df.iloc[i]
        if pd.isna(row["hh20"]) or pd.isna(row["ll10"]) or pd.isna(row["sma200"]):
            continue
        if not in_pos and row["close"] > row["hh20"]:
            in_pos = True
            entry_price = row["close"]
            entry_regime = row["regime"]
            entry_date = row["date"]
        elif in_pos and row["close"] < row["ll10"]:
            ret = row["close"] / entry_price - 1
            trades.append({"regime": entry_regime, "ret": ret, "sym": sym, "entry_date": entry_date})
            in_pos = False
    return trades


def block_bootstrap_diff_ci(trades):
    """按entry_date所在的BLOCK_DAYS区块重采样全部trade，重构后算bull-bear均值差的CI"""
    df = pd.DataFrame(trades)
    df["block"] = (df["entry_date"] - df["entry_date"].min()).dt.days // BLOCK_DAYS
    blocks = df["block"].unique()

    real_diff = df[df.regime == "bull"]["ret"].mean() - df[df.regime == "bear"]["ret"].mean()

    rng = np.random.RandomState(42)
    diffs = []
    for _ in range(N_BOOT):
        sampled_blocks = rng.choice(blocks, size=len(blocks), replace=True)
        rows = pd.concat([df[df.block == b] for b in sampled_blocks], ignore_index=True)
        b_bull = rows[rows.regime == "bull"]["ret"]
        b_bear = rows[rows.regime == "bear"]["ret"]
        if len(b_bull) == 0 or len(b_bear) == 0:
            continue
        diffs.append(b_bull.mean() - b_bear.mean())
    diffs = np.array(diffs)
    lo, hi = np.percentile(diffs, [5, 95])
    return real_diff, lo, hi, len(diffs)


def main():
    all_trades = []
    for sym, path in FILES.items():
        df = pd.read_feather(path)
        trades = simulate(df, sym)
        all_trades.extend(trades)

        bull = [t["ret"] for t in trades if t["regime"] == "bull"]
        bear = [t["ret"] for t in trades if t["regime"] == "bear"]
        print(f"\n=== {sym} ===")
        if bull:
            print(f"  bull: n={len(bull)} mean={np.mean(bull)*100:.2f}% median={np.median(bull)*100:.2f}% win%={np.mean([r>0 for r in bull])*100:.0f}%")
        if bear:
            print(f"  bear: n={len(bear)} mean={np.mean(bear)*100:.2f}% median={np.median(bear)*100:.2f}% win%={np.mean([r>0 for r in bear])*100:.0f}%")

        # 去掉最大的1-2笔bull trade看稳健性
        bull_sorted = sorted(bull, reverse=True)
        if len(bull_sorted) > 2:
            trimmed = bull_sorted[2:]
            print(f"  bull(去掉最大2笔): n={len(trimmed)} mean={np.mean(trimmed)*100:.2f}% median={np.median(trimmed)*100:.2f}%")

    print("\n=== 三币合并：中位数 + 去极值 ===")
    bull_all = [t["ret"] for t in all_trades if t["regime"] == "bull"]
    bear_all = [t["ret"] for t in all_trades if t["regime"] == "bear"]
    print(f"bull: n={len(bull_all)} mean={np.mean(bull_all)*100:.2f}% median={np.median(bull_all)*100:.2f}%")
    print(f"bear: n={len(bear_all)} mean={np.mean(bear_all)*100:.2f}% median={np.median(bear_all)*100:.2f}%")
    bull_trim = sorted(bull_all, reverse=True)[3:]  # 去掉三币合并里最大的3笔
    print(f"bull(去掉最大3笔): n={len(bull_trim)} mean={np.mean(bull_trim)*100:.2f}% median={np.median(bull_trim)*100:.2f}%")

    print("\n=== 三币联合区块自举CI（block={}天，按entry日期分块重采样）===".format(BLOCK_DAYS))
    real_diff, lo, hi, n_eff = block_bootstrap_diff_ci(all_trades)
    print(f"实际 bull-bear 均值差 = {real_diff*100:.2f}%")
    print(f"90% CI = [{lo*100:.2f}%, {hi*100:.2f}%]  (有效重采样{n_eff}/{N_BOOT}次)")
    print("CI下界>0 → 方向性证据较强；CI跨零 → 不能排除零效应")


if __name__ == "__main__":
    main()
