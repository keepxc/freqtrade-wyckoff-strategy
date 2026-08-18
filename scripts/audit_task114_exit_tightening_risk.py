"""
TASK 114（方向4：波动率周期反持续性应用于出场收紧）前置法医核查
探索性核查——非正式判定，不入证伪/采纳表，仅用于是否立项的判断依据。
"""
import numpy as np
import pandas as pd

DATA_DIR = "./user_data/data/binance/futures"
PAIRS = ["BTC", "ETH", "SOL"]
N_ENTRY = 20
N_EXIT = 10
N_EXIT_TIGHT = 5
ER_WIN = 40
MIN_HIST = 180


def load_daily(pair):
    path = f"{DATA_DIR}/{pair}_USDT_USDT-1d-futures.feather"
    df = pd.read_feather(path)[["date", "open", "close"]]
    return df.set_index("date").sort_index()


def rolling_er(close, win=ER_WIN):
    net = (close - close.shift(win - 1)).abs()
    path = close.diff().abs().rolling(win - 1).sum()
    return net / path.replace(0, np.nan)


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
    don_exit_10 = close.rolling(N_EXIT).min().shift(1)
    don_exit_5 = close.rolling(N_EXIT_TIGHT).min().shift(1)
    er = rolling_er(close)

    trades = []
    in_pos = False
    entry_price = None
    entry_idx = None
    n_bars = len(daily)
    for i in range(n_bars - 1):
        if not in_pos:
            if not pd.isna(don_upper.iloc[i]) and close.iloc[i] > don_upper.iloc[i]:
                in_pos = True
                entry_price = open_.iloc[i + 1]
                entry_idx = i + 1
        else:
            exit_10_hit = (not pd.isna(don_exit_10.iloc[i])) and close.iloc[i] < don_exit_10.iloc[i]
            if exit_10_hit:
                exit_price_10 = open_.iloc[i + 1]
                ret_10 = exit_price_10 / entry_price - 1
                ret_5 = None
                exit_date_5 = None
                for j in range(entry_idx, i + 1):
                    if (not pd.isna(don_exit_5.iloc[j])) and close.iloc[j] < don_exit_5.iloc[j]:
                        exit_price_5 = open_.iloc[j + 1]
                        ret_5 = exit_price_5 / entry_price - 1
                        exit_date_5 = daily.index[j + 1]
                        break
                idx_minus5 = max(i - 5, 0)
                idx_minus10 = max(i - 10, 0)
                pct_minus5 = expanding_percentile(er, idx_minus5)
                pct_minus10 = expanding_percentile(er, idx_minus10)
                trades.append({
                    "pair": pair,
                    "entry_date": daily.index[entry_idx],
                    "exit_date_10": daily.index[i + 1],
                    "ret_10": ret_10,
                    "ret_5": ret_5,
                    "exit_date_5": exit_date_5,
                    "er_pct_at_exit_minus5d": pct_minus5,
                    "er_pct_at_exit_minus10d": pct_minus10,
                    "hold_days_10": i + 1 - entry_idx,
                })
                in_pos = False
                entry_price, entry_idx = None, None
    return trades


def main():
    all_trades = []
    for pair in PAIRS:
        daily = load_daily(pair)
        all_trades.extend(simulate_pair(daily, pair))

    df = pd.DataFrame(all_trades)
    print("=" * 90)
    print("TASK114（方向4）前置法医核查：ER40高位区间收紧出场对肥尾交易的反事实利润流失")
    print("探索性核查——非正式判定，不入证伪/采纳表，仅用于是否立项的判断依据")
    print("=" * 90)
    print(f"\n模拟基线（10日通道出场）总交易数: {len(df)}  币种: {df['pair'].nunique()} (BTC/ETH/SOL)")
    print("（注：此数字为本脚本独立简化模拟口径，非Freqtrade真实回测93笔，仅用于本次审计内部一致性）")

    df_sorted = df.sort_values("ret_10", ascending=False).reset_index(drop=True)
    top_n = max(3, int(len(df) * 0.10))
    top = df_sorted.head(top_n)
    print(f"\n【指标A：贡献利润最大的头部交易（Top {top_n}，约10%）】")
    print(top[["pair", "entry_date", "exit_date_10", "ret_10", "hold_days_10",
               "er_pct_at_exit_minus5d", "er_pct_at_exit_minus10d"]].to_string(index=False))

    high_er_mask = (top["er_pct_at_exit_minus5d"] >= 0.5) | (top["er_pct_at_exit_minus10d"] >= 0.5)
    n_high = high_er_mask.sum()
    n_valid = top[["er_pct_at_exit_minus5d", "er_pct_at_exit_minus10d"]].notna().any(axis=1).sum()
    print(f"\n  头部交易中，出场前5天或10天ER40处于历史高位(≥0.5分位)的笔数: {n_high}/{n_valid}（历史不足180天者剔除计数）")

    print(f"\n【指标B：头部交易 反事实收紧出场(10日→5日) 利润对比】")
    top_valid = top.dropna(subset=["ret_5"]).copy()
    top_valid["loss_pct_of_ret10"] = 1 - (top_valid["ret_5"] / top_valid["ret_10"])
    print(top_valid[["pair", "entry_date", "ret_10", "ret_5", "exit_date_5", "loss_pct_of_ret10"]].to_string(index=False))

    if not top_valid.empty:
        mean_loss = top_valid["loss_pct_of_ret10"].mean()
        print(f"\n  头部交易平均利润流失比例（相对基线10日出场）: {mean_loss:+.1%}")
        total_10 = top_valid["ret_10"].sum()
        total_5 = top_valid["ret_5"].sum()
        print(f"  头部交易收益总和：基线(10日)={total_10:+.2%}  反事实(5日收紧)={total_5:+.2%}  "
              f"差={total_5 - total_10:+.2%}")

    all_valid = df.dropna(subset=["ret_5"]).copy()
    if not all_valid.empty:
        print(f"\n【对照：全样本(非仅头部) 10日 vs 5日出场收益对比，n={len(all_valid)}】")
        print(f"  10日出场总收益(算术和): {all_valid['ret_10'].sum():+.2%}")
        print(f"  5日出场总收益(算术和): {all_valid['ret_5'].sum():+.2%}")
        print(f"  差(5日-10日): {all_valid['ret_5'].sum() - all_valid['ret_10'].sum():+.2%}")
        print("  （算术和非复利，仅作方向性参考，不作为最终判据口径）")

    print("\n" + "=" * 90)
    print("【结论判断，供人工决策，非自动判据】")
    print("若指标A显示头部交易出场前普遍处于高ER状态 且 指标B显示反事实收紧后利润大幅流失，")
    print("则方向4大概率重复TASK112/113的肥尾压缩机制，建议不再投入Freqtrade集成测试成本。")
    print("=" * 90)


if __name__ == "__main__":
    main()
