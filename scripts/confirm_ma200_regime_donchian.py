"""
确认性检验（判据已冻结，2026-08-11用户确认）——200日均线regime × Donchian(20/10)信号层。

冻结判据：
- 主判据：三币合并交易样本，bull regime入场组 vs bear regime入场组的中位数收益差
- C1: 中位数差 >= +3pp
- C2: 三币联合区块自举(90天分块,2000次)90% CI下界 > 0
- C3: 去掉每币最大3笔交易后，方向依然一致(三币至少2/3保持同号)
- 同数据声明：与探索阶段v1/v2用同一批数据，证据强度打折
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
TRIM_N = 3


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


def block_bootstrap_median_diff_ci(trades_df):
    trades_df = trades_df.copy()
    trades_df["block"] = (trades_df["entry_date"] - trades_df["entry_date"].min()).dt.days // BLOCK_DAYS
    blocks = trades_df["block"].unique()
    real_diff = trades_df[trades_df.regime == "bull"]["ret"].median() - trades_df[trades_df.regime == "bear"]["ret"].median()

    rng = np.random.RandomState(42)
    diffs = []
    for _ in range(N_BOOT):
        sampled_blocks = rng.choice(blocks, size=len(blocks), replace=True)
        rows = pd.concat([trades_df[trades_df.block == b] for b in sampled_blocks], ignore_index=True)
        b_bull = rows[rows.regime == "bull"]["ret"]
        b_bear = rows[rows.regime == "bear"]["ret"]
        if len(b_bull) == 0 or len(b_bear) == 0:
            continue
        diffs.append(b_bull.median() - b_bear.median())
    diffs = np.array(diffs)
    lo, hi = np.percentile(diffs, [5, 95])
    return real_diff, lo, hi, len(diffs)


def main():
    all_trades = []
    per_coin = {}
    for sym, path in FILES.items():
        df = pd.read_feather(path)
        trades = simulate(df, sym)
        all_trades.extend(trades)
        per_coin[sym] = trades

    trades_df = pd.DataFrame(all_trades)

    print("=== C1: 中位数差 ===")
    med_bull = trades_df[trades_df.regime == "bull"]["ret"].median()
    med_bear = trades_df[trades_df.regime == "bear"]["ret"].median()
    med_diff = med_bull - med_bear
    print(f"bull中位数={med_bull*100:.2f}%  bear中位数={med_bear*100:.2f}%  差={med_diff*100:.2f}pp")
    c1 = med_diff >= 0.03
    print(f"C1 (>=3pp): {'PASS' if c1 else 'FAIL'}")

    print("\n=== C2: 区块自举CI（中位数差） ===")
    real_diff, lo, hi, n_eff = block_bootstrap_median_diff_ci(trades_df)
    print(f"实际中位数差={real_diff*100:.2f}%  90% CI=[{lo*100:.2f}%, {hi*100:.2f}%]  (有效{n_eff}/{N_BOOT})")
    c2 = lo > 0
    print(f"C2 (CI下界>0): {'PASS' if c2 else 'FAIL'}")

    print("\n=== C3: 去掉每币最大3笔交易后方向 ===")
    same_sign_count = 0
    for sym, trades in per_coin.items():
        bull = [t["ret"] for t in trades if t["regime"] == "bull"]
        bear = [t["ret"] for t in trades if t["regime"] == "bear"]
        bull_trim = sorted(bull, reverse=True)[TRIM_N:] if len(bull) > TRIM_N else bull
        bear_trim = sorted(bear, reverse=True)[TRIM_N:] if len(bear) > TRIM_N else bear
        b_mean = np.mean(bull_trim) if bull_trim else float("nan")
        r_mean = np.mean(bear_trim) if bear_trim else float("nan")
        sign_ok = b_mean > r_mean
        same_sign_count += int(sign_ok)
        print(f"  {sym}: 去极值后 bull_mean={b_mean*100:.2f}%  bear_mean={r_mean*100:.2f}%  方向一致={sign_ok}")
    c3 = same_sign_count >= 2
    print(f"C3 (>=2/3币方向一致): {'PASS' if c3 else 'FAIL'}  (实际{same_sign_count}/3)")

    print(f"\n=== 总判定：C1={'PASS' if c1 else 'FAIL'}  C2={'PASS' if c2 else 'FAIL'}  C3={'PASS' if c3 else 'FAIL'} ===")
    verdict = "✅方向性支持" if (c1 and c2 and c3) else "⭕未通过全部判据，噪声级或不稳健"
    print(f"结论: {verdict}")


if __name__ == "__main__":
    main()
