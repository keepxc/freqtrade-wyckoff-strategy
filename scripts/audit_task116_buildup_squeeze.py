"""
TASK 116（波动率压缩/蓄势门控）前置法医核查
探索性核查——非正式判定，不入证伪/采纳表，仅用于是否立项的判断依据。
"""
import numpy as np
import pandas as pd

DATA_DIR = "./user_data/data/binance/futures"
PAIRS = ["BTC", "ETH", "SOL"]
N_ENTRY = 20
N_EXIT = 10
VOL_WIN = 20
MIN_HIST = 180
VOL_PCT_THRESHOLD = 0.25


def load_daily(pair):
    path = f"{DATA_DIR}/{pair}_USDT_USDT-1d-futures.feather"
    df = pd.read_feather(path)[["date", "open", "close"]]
    return df.set_index("date").sort_index()


def realized_vol(close, win=VOL_WIN):
    logret = np.log(close / close.shift(1))
    return logret.rolling(win).std()


def expanding_percentile(series, idx, min_hist=MIN_HIST):
    hist = series.iloc[:idx].dropna()
    cur = series.iloc[idx]
    if pd.isna(cur) or len(hist) < min_hist:
        return None
    return (hist < cur).mean()


def simulate_pair(daily, pair):
    close = daily["close"]
    open_ = daily["open"]
    don_upper = close.rolling(N_ENTRY).max().shift(1)
    don_exit = close.rolling(N_EXIT).min().shift(1)
    rv = realized_vol(close)

    trades = []
    in_pos = False
    entry_price = None
    entry_idx = None
    signal_idx = None
    n_bars = len(daily)
    for i in range(n_bars - 1):
        if not in_pos:
            if not pd.isna(don_upper.iloc[i]) and close.iloc[i] > don_upper.iloc[i]:
                in_pos = True
                entry_price = open_.iloc[i + 1]
                entry_idx = i + 1
                signal_idx = i
        else:
            if not pd.isna(don_exit.iloc[i]) and close.iloc[i] < don_exit.iloc[i]:
                exit_price = open_.iloc[i + 1]
                ret = exit_price / entry_price - 1
                vol_pct = expanding_percentile(rv, signal_idx)
                trades.append({
                    "pair": pair,
                    "entry_date": daily.index[entry_idx],
                    "exit_date": daily.index[i + 1],
                    "ret": ret,
                    "hold_days": i + 1 - entry_idx,
                    "vol_pct_at_signal": vol_pct,
                    "gate_pass": (vol_pct is not None) and (vol_pct <= VOL_PCT_THRESHOLD),
                    "gate_data_insufficient": vol_pct is None,
                })
                in_pos = False
                entry_price, entry_idx, signal_idx = None, None, None
    return trades


def main():
    all_trades = []
    for pair in PAIRS:
        daily = load_daily(pair)
        all_trades.extend(simulate_pair(daily, pair))

    df = pd.DataFrame(all_trades)
    print("=" * 90)
    print("TASK116（波动率压缩蓄势门控）前置法医核查：反事实门控命中率 vs 头部利润交易")
    print("=" * 90)
    print(f"\n模拟基线总交易数: {len(df)}  币种: {df['pair'].nunique()} (BTC/ETH/SOL)")

    n_insufficient = df["gate_data_insufficient"].sum()
    print(f"\n历史不足180天被剔除计数的信号数: {n_insufficient}/{len(df)}")

    valid = df[~df["gate_data_insufficient"]].copy()
    n_pass = valid["gate_pass"].sum()
    n_total_valid = len(valid)
    print(f"\n【门控通过率】有效样本 n={n_total_valid}，门控(vol20d分位<=25%)通过: "
          f"{n_pass} 笔 ({n_pass/n_total_valid:.1%})，拦截: {n_total_valid - n_pass} 笔 ({1 - n_pass/n_total_valid:.1%})")

    df_sorted = df.sort_values("ret", ascending=False).reset_index(drop=True)
    top_n = max(3, int(len(df) * 0.10))
    top = df_sorted.head(top_n)
    print(f"\n【审计重点：头部利润交易（Top {top_n}，约10%）的门控命中情况】")
    print(top[["pair", "entry_date", "exit_date", "ret", "hold_days",
               "vol_pct_at_signal", "gate_pass", "gate_data_insufficient"]].to_string(index=False))

    top_valid = top[~top["gate_data_insufficient"]]
    n_top_filtered = (~top_valid["gate_pass"]).sum()
    n_top_valid = len(top_valid)
    print(f"\n  头部交易中被门控拦截的笔数: {n_top_filtered}/{n_top_valid}"
          f"（另有 {len(top) - n_top_valid} 笔因历史不足未参与门控判断）")

    filtered_top = top_valid[~top_valid["gate_pass"]]
    if not filtered_top.empty:
        lost_profit_sum = filtered_top["ret"].sum()
        print(f"\n  【被拦截的头部大赢单明细】")
        print(filtered_top[["pair", "entry_date", "ret", "vol_pct_at_signal"]].to_string(index=False))
        print(f"\n  这些被拦截头部交易的收益总和（若门控启用则完全损失）: {lost_profit_sum:+.2%}")
    else:
        print("\n  头部10%利润交易全部通过门控，未被拦截。")

    biggest = df_sorted.iloc[0]
    print(f"\n【最大单笔交易】{biggest['pair']} {biggest['entry_date'].date()}, ret={biggest['ret']:+.2%}, "
          f"vol_pct_at_signal={biggest['vol_pct_at_signal']}, "
          f"gate_pass={biggest['gate_pass']}, data_insufficient={biggest['gate_data_insufficient']}")

    print("\n" + "=" * 90)


if __name__ == "__main__":
    main()
