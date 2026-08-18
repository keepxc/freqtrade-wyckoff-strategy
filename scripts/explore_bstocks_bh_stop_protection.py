"""
探索性——非结论，禁止引用为证据。

BStocksHybridV1 买入持有腿 -50% 回撤止损的"熊市保护价值"检验。

背景：上一脚本(explore_bstocks_bh_ddstop_scan.py)显示牛市窗口里止损越宽收益越高，
      但那无法回答"止损在下跌市里是否真的保护了本金"。本脚本做两件事：
  1. 分窗口阈值扫描：按 entry_date 是否落在熊市窗口(2021-11-15~2023-01-15，
     纳指主要下跌段)分组，看各阈值在熊市/非熊市建仓交易上的表现。
  2. 反事实保护价值：对 -50% 下所有触发 bh_dd_stop 的交易，算
     protection = (止损离场 bh 腿收益) - (若不止损、持有到数据最后一根close的 bh 腿收益)
       正 = 止损避免了更大亏损(保护有效)；负 = 止损砍掉了后续反弹(保护有害)。

口径：独立简化模拟(非 freqtrade 真实回测 26.91%)，16支正股，等权无手续费、
      无 vol-target 仓位——仅用于本脚本内部一致性的档位间对比。
"""
import numpy as np
import pandas as pd

DATA_DIR = "./user_data/data/binance"
TICKERS = [
    "NVDAB", "TSLAB", "CRCLB", "MUB", "SNDKB", "AAPLB", "AMZNB", "GOOGLB",
    "NFLXB", "ASMLB", "GSB", "PYPLB", "COINB", "HOODB", "IBMB", "NOKB",
]
N_ENTRY = 20
N_EXIT = 10
DD_STOPS = [-0.30, -0.40, -0.50, -0.60]
BEAR_START = pd.Timestamp("2021-11-15", tz="UTC")
BEAR_END = pd.Timestamp("2023-01-15", tz="UTC")


def load_daily(ticker):
    path = f"{DATA_DIR}/{ticker}_USDT-1d.feather"
    df = pd.read_feather(path)[["date", "open", "high", "low", "close"]]
    return df.set_index("date").sort_index()


def simulate_pair(daily, pair, dd_stop):
    close = daily["close"]
    open_ = daily["open"]
    high = daily["high"]
    low = daily["low"]
    last_close = close.iloc[-1]

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
                if (not pd.isna(don_exit.iloc[i])) and close.iloc[i] < don_exit.iloc[i]:
                    tactical_exit_price = open_.iloc[i + 1]
                    tactical_done = True
            else:
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
                        "bh_exit_price": bh_exit_price,
                        "bh_exit_reason": "bh_dd_stop",
                    })
                    in_pos = False
                    entry_price, entry_idx = None, None
                    tactical_done, tactical_exit_price, peak = False, None, None
                    continue

    if in_pos:
        if not tactical_done:
            trades.append({
                "pair": pair,
                "entry_date": daily.index[entry_idx],
                "total_ret": last_close / entry_price - 1,
                "tactical_ret": None,
                "bh_ret": None,
                "bh_exit_price": None,
                "bh_exit_reason": "no_tactical_full_hold",
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
                "bh_exit_price": last_close,
                "bh_exit_reason": "force_exit",
            })
    return trades


def main():
    print("=" * 96)
    print("探索性——非结论，禁止引用为证据")
    print("BStocksHybridV1 买入持有腿 -50% 回撤止损：熊市窗口保护价值检验")
    print(f"熊市窗口定义：entry_date ∈ [{BEAR_START.date()}, {BEAR_END.date()})（纳指主要下跌段）")
    print("口径：独立简化模拟(非 freqtrade 真实回测 26.91%)，16支正股，等权无手续费")
    print("=" * 96)

    daily_by_ticker = {t: load_daily(t) for t in TICKERS}

    # ---- Part 1: 分窗口阈值扫描 ----
    print("\n【Part 1：分窗口阈值扫描（熊市 vs 非熊市建仓交易）】")
    rows = []
    for dd in DD_STOPS:
        all_trades = []
        for t in TICKERS:
            all_trades.extend(simulate_pair(daily_by_ticker[t], t, dd))
        df = pd.DataFrame(all_trades)
        df["bear"] = (df["entry_date"] >= BEAR_START) & (df["entry_date"] < BEAR_END)
        bear = df[df["bear"]]
        bull = df[~df["bear"]]
        rows.append({
            "dd_stop": f"{dd:.0%}",
            "n_total": len(df),
            "n_bear": len(bear),
            "sum_bear": bear["total_ret"].sum() * 100 if len(bear) else np.nan,
            "mean_bear": bear["total_ret"].mean() * 100 if len(bear) else np.nan,
            "n_stop_bear": int((bear["bh_exit_reason"] == "bh_dd_stop").sum()) if len(bear) else 0,
            "n_bull": len(bull),
            "sum_bull": bull["total_ret"].sum() * 100 if len(bull) else np.nan,
            "mean_bull": bull["total_ret"].mean() * 100 if len(bull) else np.nan,
        })
    out = pd.DataFrame(rows)
    print(out.to_string(index=False, float_format=lambda v: f"{v:.2f}"))

    # ---- Part 2: -50% 止损交易的反事实保护价值 ----
    print("\n【Part 2：-50% 止损交易的反事实保护价值】")
    print("  protection = 止损离场 bh 腿收益 - 若不止损持有到数据末尾的 bh 腿收益")
    print("  （正 = 止损避免了更大亏损；负 = 止损砍掉了后续反弹）")
    all_trades = []
    for t in TICKERS:
        all_trades.extend(simulate_pair(daily_by_ticker[t], t, -0.50))
    df = pd.DataFrame(all_trades)
    df["bear"] = (df["entry_date"] >= BEAR_START) & (df["entry_date"] < BEAR_END)

    # 重放 -50% 状态机，记录止损交易的 entry_price / bh_exit_price / last_close
    # 用于计算反事实（若不止损持有到数据末尾）的保护价值
    stop_trades_with_entry = []
    for t in TICKERS:
        daily = daily_by_ticker[t]
        close = daily["close"]
        open_ = daily["open"]
        high = daily["high"]
        low = daily["low"]
        last_close = close.iloc[-1]
        don_upper = high.rolling(N_ENTRY).max().shift(1)
        don_exit = low.rolling(N_EXIT).min().shift(1)
        in_pos = False
        entry_price = None
        entry_idx = None
        tactical_done = False
        tactical_exit_price = None
        peak = None
        for i in range(len(daily) - 1):
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
                    if (not pd.isna(don_exit.iloc[i])) and close.iloc[i] < don_exit.iloc[i]:
                        tactical_exit_price = open_.iloc[i + 1]
                        tactical_done = True
                else:
                    peak = close.iloc[i] if peak is None else max(peak, close.iloc[i])
                    dd = (close.iloc[i] - peak) / peak
                    if dd <= -0.50:
                        bh_exit_price = open_.iloc[i + 1]
                        stop_trades_with_entry.append({
                            "pair": t,
                            "entry_date": daily.index[entry_idx],
                            "entry_price": entry_price,
                            "bh_exit_price": bh_exit_price,
                            "last_close": last_close,
                            "bear": (daily.index[entry_idx] >= BEAR_START) and (daily.index[entry_idx] < BEAR_END),
                        })
                        in_pos = False
                        entry_price, entry_idx = None, None
                        tactical_done, tactical_exit_price, peak = False, None, None
                        continue

    st = pd.DataFrame(stop_trades_with_entry)
    if len(st) == 0:
        print("  无止损交易，无法分析")
    else:
        st["actual_bh_ret"] = st["bh_exit_price"] / st["entry_price"] - 1
        st["counterfactual_bh_ret"] = st["last_close"] / st["entry_price"] - 1
        st["protection"] = st["actual_bh_ret"] - st["counterfactual_bh_ret"]

        print(f"\n  -50% 止损交易总数: {len(st)}")
        for label, sub in [("熊市窗口建仓", st[st["bear"]]), ("非熊市建仓", st[~st["bear"]])]:
            if len(sub) == 0:
                print(f"\n  [{label}] 无交易")
                continue
            pos = (sub["protection"] > 0).sum()
            neg = (sub["protection"] <= 0).sum()
            print(f"\n  [{label}] n={len(sub)}")
            print(f"    止损后继续跌(保护有效, protection>0): {pos} 笔")
            print(f"    止损后反弹收复(保护有害, protection<=0): {neg} 笔")
            print(f"    protection 均值: {sub['protection'].mean()*100:+.2f}pp "
                  f"（相对入场价的百分点）")
            print(f"    实际 bh 腿均收益: {sub['actual_bh_ret'].mean()*100:+.2f}%")
            print(f"    反事实(不止损) bh 腿均收益: {sub['counterfactual_bh_ret'].mean()*100:+.2f}%")

        # 最极端案例：止损保护最大的几笔 与 止损砍掉反弹最多的几笔
        st_sorted = st.sort_values("protection", ascending=False)
        print("\n  保护价值最大(止损最值得)的前5笔：")
        print(st_sorted.head(5)[["pair", "entry_date", "actual_bh_ret", "counterfactual_bh_ret", "protection"]]
              .to_string(index=False, float_format=lambda v: f"{v:.3f}"))
        print("\n  保护价值最小(止损砍掉反弹最狠)的前5笔：")
        print(st_sorted.tail(5)[["pair", "entry_date", "actual_bh_ret", "counterfactual_bh_ret", "protection"]]
              .to_string(index=False, float_format=lambda v: f"{v:.3f}"))

    print("\n【判读提示】")
    print("  若熊市窗口内止损交易的 protection 均值显著为正 -> -50% 止损在下跌市有保护价值。")
    print("  若普遍为负 -> 止损砍在底部附近，-50% 更像追认式离场而非保护。")
    print("  本脚本为探索性，任何数字不得作为三档判定或证伪/采纳证据。")
    print("=" * 96)


if __name__ == "__main__":
    main()
