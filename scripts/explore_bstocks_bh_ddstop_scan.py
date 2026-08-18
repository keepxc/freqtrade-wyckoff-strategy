"""
探索性——非结论，禁止引用为证据。

BStocksHybridV1 买入持有腿回撤止损阈值扫描：
  - 复刻 BStocksHybridV1.py 的状态机（16支正股，TASK109 采纳策略）：
      1. 入场：close > don_upper（high.rolling(20).max().shift(1)），次日 open 成交；
      2. 战术腿：首次 don_exit 信号（close < don_exit，low.rolling(10).min().shift(1)）
         触发时平掉 50%，成交价次日 open；
      3. 买入持有腿：剩余 50% 只追踪收盘峰值，回撤 <= dd_stop 时全平（次日 open 成交）；
         dd_stop 默认 -0.50（当前采纳值）。
  - 扫描 dd_stop ∈ {-0.30, -0.40, -0.50, -0.60}，看阈值对总收益/退出结构/肥尾的形状。

口径：独立简化模拟（非 freqtrade 真实回测 26.91%），无手续费、无 vol-target 仓位
      （每笔 stake=1 等权）、无 max_open_trades 限制——仅用于本脚本内部一致性的
      档位间对比，数字不可与 TASK109 的 Freqtrade 基线直接对比。
"""
import numpy as np
import pandas as pd

DATA_DIR = "./user_data/data/binance"
# TASK109 的 16 支正股（bStocks 现货代号，正股 ticker + B 后缀）
TICKERS = [
    "NVDAB", "TSLAB", "CRCLB", "MUB", "SNDKB", "AAPLB", "AMZNB", "GOOGLB",
    "NFLXB", "ASMLB", "GSB", "PYPLB", "COINB", "HOODB", "IBMB", "NOKB",
]
N_ENTRY = 20
N_EXIT = 10
DD_STOPS = [-0.30, -0.40, -0.50, -0.60]


def load_daily(ticker):
    path = f"{DATA_DIR}/{ticker}_USDT-1d.feather"
    df = pd.read_feather(path)[["date", "open", "high", "low", "close"]]
    return df.set_index("date").sort_index()


def simulate_pair(daily, pair, dd_stop):
    close = daily["close"]
    open_ = daily["open"]
    high = daily["high"]
    low = daily["low"]

    don_upper = high.rolling(N_ENTRY).max().shift(1)
    don_exit = low.rolling(N_EXIT).min().shift(1)

    trades = []
    in_pos = False
    entry_price = None
    entry_idx = None
    tactical_done = False
    tactical_exit_price = None
    peak = None

    n_bars = len(daily)
    for i in range(n_bars - 1):
        if not in_pos:
            if (not pd.isna(don_upper.iloc[i])) and close.iloc[i] > don_upper.iloc[i]:
                in_pos = True
                entry_price = open_.iloc[i + 1]
                entry_idx = i + 1
                tactical_done = False
                tactical_exit_price = None
                peak = None
        else:
            if not tactical_done:
                # 战术腿：首次 don_exit 触发平 50%，成交价次日 open
                if (not pd.isna(don_exit.iloc[i])) and close.iloc[i] < don_exit.iloc[i]:
                    tactical_exit_price = open_.iloc[i + 1]
                    tactical_done = True
                    # bh 腿从下一根 bar 开始追踪收盘峰值
            else:
                # 买入持有腿：追踪收盘峰值 + 回撤止损
                peak = close.iloc[i] if peak is None else max(peak, close.iloc[i])
                dd = (close.iloc[i] - peak) / peak
                if dd <= dd_stop:
                    bh_exit_price = open_.iloc[i + 1]
                    tac_ret = tactical_exit_price / entry_price - 1
                    bh_ret = bh_exit_price / entry_price - 1
                    total_ret = 0.5 * tac_ret + 0.5 * bh_ret
                    trades.append({
                        "pair": pair,
                        "entry_date": daily.index[entry_idx],
                        "total_ret": total_ret,
                        "tactical_ret": tac_ret,
                        "bh_ret": bh_ret,
                        "bh_exit_reason": "bh_dd_stop",
                        "bh_hold_days": (i + 1) - entry_idx,
                    })
                    in_pos = False
                    entry_price, entry_idx = None, None
                    tactical_done, tactical_exit_price, peak = False, None, None
                    continue

    # 循环结束仍有持仓 -> 强制平仓（force_exit，按最后收盘价）
    if in_pos:
        last_close = close.iloc[-1]
        if not tactical_done:
            # 全程未触发 don_exit：整笔 100% 持有到结束
            trades.append({
                "pair": pair,
                "entry_date": daily.index[entry_idx],
                "total_ret": last_close / entry_price - 1,
                "tactical_ret": None,
                "bh_ret": None,
                "bh_exit_reason": "no_tactical_full_hold",
                "bh_hold_days": (n_bars - 1) - entry_idx,
            })
        else:
            tac_ret = tactical_exit_price / entry_price - 1
            bh_ret = last_close / entry_price - 1
            total_ret = 0.5 * tac_ret + 0.5 * bh_ret
            trades.append({
                "pair": pair,
                "entry_date": daily.index[entry_idx],
                "total_ret": total_ret,
                "tactical_ret": tac_ret,
                "bh_ret": bh_ret,
                "bh_exit_reason": "force_exit",
                "bh_hold_days": (n_bars - 1) - entry_idx,
            })
    return trades


def summarize(df, label):
    n = len(df)
    if n == 0:
        return {"dd_stop": label, "n": 0}
    ret = df["total_ret"]
    n_stop = int((df["bh_exit_reason"] == "bh_dd_stop").sum())
    n_force = int((df["bh_exit_reason"] == "force_exit").sum())
    n_no_tac = int((df["bh_exit_reason"] == "no_tactical_full_hold").sum())
    bh_ret = df["bh_ret"].dropna()
    tac_ret = df["tactical_ret"].dropna()
    return {
        "dd_stop": label,
        "n": n,
        "sum_ret": ret.sum(),
        "mean_ret": ret.mean(),
        "best": ret.max(),
        "n_stop": n_stop,
        "n_force": n_force,
        "n_no_tac": n_no_tac,
        "bh_mean": bh_ret.mean() if len(bh_ret) > 0 else np.nan,
        "tac_mean": tac_ret.mean() if len(tac_ret) > 0 else np.nan,
    }


def main():
    print("=" * 96)
    print("探索性——非结论，禁止引用为证据")
    print("BStocksHybridV1 买入持有腿回撤止损阈值扫描（dd_stop ∈ {-30%,-40%,-50%,-60%}）")
    print("口径：独立简化模拟（非 freqtrade 真实回测 26.91%），16支正股，等权无手续费")
    print("=" * 96)

    daily_by_ticker = {t: load_daily(t) for t in TICKERS}

    rows = []
    for dd in DD_STOPS:
        all_trades = []
        for t in TICKERS:
            all_trades.extend(simulate_pair(daily_by_ticker[t], t, dd))
        df = pd.DataFrame(all_trades)
        rows.append(summarize(df, f"{dd:.0%}"))

    out = pd.DataFrame(rows)
    out["sum_ret"] = out["sum_ret"] * 100
    out["mean_ret"] = out["mean_ret"] * 100
    out["best"] = out["best"] * 100
    out["bh_mean"] = out["bh_mean"] * 100
    out["tac_mean"] = out["tac_mean"] * 100

    print("\n【对比表】")
    print(out.to_string(index=False, float_format=lambda v: f"{v:.2f}"))

    print("\n【形状观察：回撤止损阈值 vs 总收益 / 逐笔均值】")
    for _, r in out.iterrows():
        print(f"  {r['dd_stop']:<6} 总收益(算术和)={r['sum_ret']:+.2f}%   "
              f"逐笔均值={r['mean_ret']:+.2f}%  最佳单笔={r['best']:+.2f}%")

    print("\n【退出结构分布（每档）】")
    for _, r in out.iterrows():
        print(f"  {r['dd_stop']:<6} 止损={r['n_stop']}  force_exit={r['n_force']}  "
              f"全程未分腿={r['n_no_tac']}  交易总数={r['n']}")

    print("\n【买入持有腿 vs 战术腿 均收益】")
    for _, r in out.iterrows():
        print(f"  {r['dd_stop']:<6} bh腿均收益={r['bh_mean']:+.2f}%   "
              f"战术腿均收益={r['tac_mean']:+.2f}%")

    print("\n【判读提示】")
    print("  止损阈值只影响买入持有腿（战术腿在 don_exit 即平，与阈值无关）。")
    print("  越宽的阈值(-60%) -> bh 腿越接近纯买入持有、force_exit 越多、长期收益越靠持有。")
    print("  越窄的阈值(-30%) -> bh 腿越早止损离场，牛市里可能过早砍掉长期涨幅。")
    print("  当前采纳值 -50% 是否落在合理区间，由曲线形状判断。")
    print("  本脚本为探索性，任何数字不得作为三档判定或证伪/采纳证据。")
    print("=" * 96)


if __name__ == "__main__":
    main()
