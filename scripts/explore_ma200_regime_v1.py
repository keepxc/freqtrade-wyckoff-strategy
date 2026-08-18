"""
探索性——非结论，禁止引用为证据。

方向：200日均线regime能否系统化替代"大周期判断"，作为Donchian多头信号的过滤条件。

假设：价格在200日SMA上方时做多Donchian信号，表现是否明显优于均线下方。

方法：BTC/ETH/SOL 日线全历史，Donchian(20/10)signal-level模拟器（entry=突破20日高点，
exit=跌破10日低点），按entry时刻收盘价相对200日SMA的位置分组，比较两组per-trade收益、
胜率、笔数、regime覆盖时间占比。SMA(200)用shift(1)后的历史数据算，避免未来函数。
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


def simulate(df):
    df = df.sort_values("date").reset_index(drop=True)
    df["hh20"] = df["high"].rolling(ENTRY_N).max().shift(1)
    df["ll10"] = df["low"].rolling(EXIT_N).min().shift(1)
    df["sma200"] = df["close"].rolling(MA_N).mean().shift(1)
    df["regime"] = np.where(df["close"] > df["sma200"], "bull", "bear")

    trades = []
    in_pos = False
    entry_price = None
    entry_regime = None
    for i in range(len(df)):
        row = df.iloc[i]
        if pd.isna(row["hh20"]) or pd.isna(row["ll10"]) or pd.isna(row["sma200"]):
            continue
        if not in_pos and row["close"] > row["hh20"]:
            in_pos = True
            entry_price = row["close"]
            entry_regime = row["regime"]
        elif in_pos and row["close"] < row["ll10"]:
            ret = row["close"] / entry_price - 1
            trades.append({"regime": entry_regime, "ret": ret})
            in_pos = False
    return trades, df


def main():
    all_trades = []
    for sym, path in FILES.items():
        df = pd.read_feather(path)
        trades, dfx = simulate(df)
        n_bull = (dfx["regime"] == "bull").sum()
        n_bear = (dfx["regime"] == "bear").sum()
        n_total = n_bull + n_bear
        print(f"\n=== {sym} === regime覆盖: bull {n_bull/n_total*100:.0f}%  bear {n_bear/n_total*100:.0f}%  (共{n_total}天)")
        for t in trades:
            t["sym"] = sym
        all_trades.extend(trades)
        bull_t = [t["ret"] for t in trades if t["regime"] == "bull"]
        bear_t = [t["ret"] for t in trades if t["regime"] == "bear"]
        print(f"  bull regime entry: n={len(bull_t)} mean={np.mean(bull_t)*100:.2f}% win%={np.mean([r>0 for r in bull_t])*100:.0f}%" if bull_t else "  bull regime entry: 无交易")
        print(f"  bear regime entry: n={len(bear_t)} mean={np.mean(bear_t)*100:.2f}% win%={np.mean([r>0 for r in bear_t])*100:.0f}%" if bear_t else "  bear regime entry: 无交易")

    print("\n=== 三币合并（仅描述性汇总，不当独立样本算功效） ===")
    bull_all = [t["ret"] for t in all_trades if t["regime"] == "bull"]
    bear_all = [t["ret"] for t in all_trades if t["regime"] == "bear"]
    print(f"bull: n={len(bull_all)} mean={np.mean(bull_all)*100:.2f}% median={np.median(bull_all)*100:.2f}%")
    print(f"bear: n={len(bear_all)} mean={np.mean(bear_all)*100:.2f}% median={np.median(bear_all)*100:.2f}%" if bear_all else "bear: 无交易")


if __name__ == "__main__":
    main()
