"""
探索性——非结论，禁止引用为证据。

海龟式硬止损距离扫描：
  - 信号层复刻 DonchianV1.py：don_upper = high.rolling(20).max().shift(1)
    入场 close > don_upper；don_exit = low.rolling(10).min().shift(1)
    出场 close < don_exit（次日开盘成交）。
  - 叠加硬止损：止损价 = 突破信号K线最低价 - mult × ATR(20)，
    日内 low <= 止损价即触发（开盘价与止损价取低者成交，模拟跳空滑点）。
    止损与 10 日低点通道退出"先触发者优先"（止损日内先于收盘确认）。
  - 扫描 mult ∈ {1, 2, 3, 4, 5}，基线 = 纯 10 日通道退出（无止损）。

目的：看"止损距离"这个连续参数对总收益/肥尾的形状（单调性/是否有凸起），
      判断是否值得转确认阶段立项，不构成任何三档判定。

口径：独立简化模拟（非 Freqtrade 真实回测 93 笔），BTC/ETH/SOL，2020-2026。
      无手续费、无 vol-target 仓位、无 max_open_trades 限制——仅用于本脚本
      内部一致性的档位间对比，数字本身不可与 Freqtrade 基线直接对比。
"""
import numpy as np
import pandas as pd

DATA_DIR = "./user_data/data/binance/futures"
PAIRS = ["BTC", "ETH", "SOL"]
N_ENTRY = 20
N_EXIT = 10
ATR_WIN = 20
MULTS = [None, 1, 2, 3, 4, 5]  # None = 纯基线（不设止损）


def load_daily(pair):
    path = f"{DATA_DIR}/{pair}_USDT_USDT-1d-futures.feather"
    df = pd.read_feather(path)[["date", "open", "high", "low", "close"]]
    return df.set_index("date").sort_index()


def true_range(daily):
    high, low, close = daily["high"], daily["low"], daily["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr


def simulate_pair(daily, pair, mult):
    """mult=None 表示纯基线（不设止损）。返回该档位下的交易列表。"""
    close = daily["close"]
    open_ = daily["open"]
    high = daily["high"]
    low = daily["low"]

    don_upper = high.rolling(N_ENTRY).max().shift(1)
    don_exit = low.rolling(N_EXIT).min().shift(1)
    atr = true_range(daily).rolling(ATR_WIN).mean().shift(1)

    trades = []
    in_pos = False
    entry_price = None
    entry_idx = None
    signal_low = None
    stop_price = None

    n_bars = len(daily)
    for i in range(n_bars - 1):
        if not in_pos:
            if (not pd.isna(don_upper.iloc[i])) and close.iloc[i] > don_upper.iloc[i]:
                in_pos = True
                entry_price = open_.iloc[i + 1]
                entry_idx = i + 1
                signal_low = low.iloc[i]
                # 止损锚点：信号K线最低价 - mult × ATR(20)（ATR 只用截至信号日的数据）
                stop_price = None
                if mult is not None:
                    atr_sig = atr.iloc[i]
                    if (not pd.isna(atr_sig)) and atr_sig > 0:
                        sp = signal_low - mult * atr_sig
                        if sp > 0:
                            stop_price = sp
        else:
            # 1) 止损先触发（日内 low 跌破止损价）
            if stop_price is not None and low.iloc[i] <= stop_price:
                fill = min(open_.iloc[i], stop_price)
                trades.append({
                    "pair": pair,
                    "entry_date": daily.index[entry_idx],
                    "exit_date": daily.index[i],
                    "ret": fill / entry_price - 1,
                    "exit_reason": "stop",
                    "hold_days": i - entry_idx,
                })
                in_pos = False
                entry_price, entry_idx, signal_low, stop_price = None, None, None, None
                continue
            # 2) 10 日低点通道退出（收盘确认，次日开盘成交）
            if (not pd.isna(don_exit.iloc[i])) and close.iloc[i] < don_exit.iloc[i]:
                exit_price = open_.iloc[i + 1]
                trades.append({
                    "pair": pair,
                    "entry_date": daily.index[entry_idx],
                    "exit_date": daily.index[i + 1],
                    "ret": exit_price / entry_price - 1,
                    "exit_reason": "don_exit",
                    "hold_days": (i + 1) - entry_idx,
                })
                in_pos = False
                entry_price, entry_idx, signal_low, stop_price = None, None, None, None
    return trades


def summarize(df, mult_label):
    n = len(df)
    if n == 0:
        return {"mult": mult_label, "n": 0}
    ret = df["ret"]
    df_sorted = df.sort_values("ret", ascending=False).reset_index(drop=True)
    top_n = max(3, int(n * 0.10))
    top = df_sorted.head(top_n)
    n_stop = int((df["exit_reason"] == "stop").sum())
    stop_ret = ret[df["exit_reason"] == "stop"]
    return {
        "mult": mult_label,
        "n": n,
        "sum_ret": ret.sum(),
        "mean_ret": ret.mean(),
        "n_stop": n_stop,
        "stop_pct": n_stop / n,
        "stop_mean_ret": stop_ret.mean() if n_stop > 0 else np.nan,
        "best": ret.max(),
        "top_sum": top["ret"].sum(),
        "top_n": top_n,
    }


def main():
    print("=" * 96)
    print("探索性——非结论，禁止引用为证据")
    print("海龟式硬止损距离扫描：Donchian 20/10 突破 + ATR(mult) 硬止损（先触发者优先）")
    print("口径：独立简化模拟（非 Freqtrade 真实回测 93 笔），BTC/ETH/SOL，2020-2026")
    print("=" * 96)

    daily_by_pair = {p: load_daily(p) for p in PAIRS}

    rows = []
    for mult in MULTS:
        all_trades = []
        for pair in PAIRS:
            all_trades.extend(simulate_pair(daily_by_pair[pair], pair, mult))
        df = pd.DataFrame(all_trades)
        label = "基线(无止损)" if mult is None else f"{mult}×ATR"
        rows.append(summarize(df, label))

    out = pd.DataFrame(rows)
    out["sum_ret"] = out["sum_ret"] * 100
    out["mean_ret"] = out["mean_ret"] * 100
    out["stop_mean_ret"] = out["stop_mean_ret"] * 100
    out["best"] = out["best"] * 100
    out["top_sum"] = out["top_sum"] * 100

    print("\n【对比表】")
    print(out.to_string(index=False, float_format=lambda v: f"{v:.2f}"))

    print("\n【形状观察：止损距离 vs 总收益】")
    for _, r in out.iterrows():
        print(f"  {r['mult']:<12} 总收益(算术和)={r['sum_ret']:+.2f}%   "
              f"逐笔均值={r['mean_ret']:+.2f}%  最佳单笔={r['best']:+.2f}%")

    print("\n【形状观察：止损距离 vs 肥尾（Top10% 头部收益和）】")
    for _, r in out.iterrows():
        print(f"  {r['mult']:<12} 头部收益和={r['top_sum']:+.2f}%   "
              f"止损笔数={r['n_stop']}/{r['n']} ({r['stop_pct']:.1%})   "
              f"止损笔均收益={r['stop_mean_ret']:+.2f}%")

    print("\n【判读提示】")
    print("  若总收益/肥尾随止损距离单调变化且各档均不高于基线 -> 止损距离是连续参数，")
    print("  机制与 TASK112 根因（提前离场砍肥尾）一致，无需逐档立项。")
    print("  若某档出现孤立凸起（左右邻档均低） -> 大概率噪声/过拟合，不可单独采信。")
    print("  本脚本为探索性，任何数字不得作为三档判定或证伪/采纳证据。")
    print("=" * 96)


if __name__ == "__main__":
    main()
