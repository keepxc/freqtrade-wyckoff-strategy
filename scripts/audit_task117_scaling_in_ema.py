"""
TASK 117 pre-check (Direction 2: scaling-in on 20EMA pullback after breakout)
Exploratory audit only -- not a confirmatory verdict, does not enter the
falsification/adoption tables. Purpose: decide whether to formally propose
TASK117 and invest in a full Freqtrade/Docker backtest.

Frozen parameters (user sign-off 2026-08-16, locked before running):
  Scale-in signal:
    - daily 20 EMA, same timeframe as DonchianV1
    - "pullback": today's Low < 20EMA
    - "bullish signal bar" proxy: today's Close > Open AND Close > 20EMA
    - "meaningful unrealized profit" threshold: unrealized return >= 1R,
      R = (entry_price - signal_bar_low) / entry_price (signal_bar = breakout
      confirmation bar, the day before entry fill)
    - scale-in fires at most once per trade
  Sizing / leverage:
    - scale-in size = 50% of the initial stake
    - total leverage capped at MAX_LEV=1.0; if the initial vol-target leverage
      already sits at the 1.0 cap, scaling in is disallowed for that trade
    - TARGET_VOL=0.20, VOL_LOOKBACK=20 (annualized *sqrt(365)), matching
      DonchianV1.py's existing vol-target formula
  Exit: unchanged 10-day channel exit, both legs closed together

Audit indicators (user-specified):
  A. Tail Thickening Ratio: scaled-leg avg profit / baseline avg profit among
     the biggest winners
  B. Capital Utilization: how long the add-on capital is deployed and its
     contribution
  C. Standalone win rate of the add-on leg (if <50%, judge circle immediately)
  D. Net profit comparison (blended cost) vs pure baseline
"""
import numpy as np
import pandas as pd

DATA_DIR = "./user_data/data/binance/futures"
PAIRS = ["BTC", "ETH", "SOL"]
N_ENTRY = 20
N_EXIT = 10
EMA_WIN = 20
VOL_LOOKBACK = 20
TARGET_VOL = 0.20
MAX_LEV = 1.0
SCALE_IN_RATIO = 0.5


def load_daily(pair):
    path = f"{DATA_DIR}/{pair}_USDT_USDT-1d-futures.feather"
    df = pd.read_feather(path)[["date", "open", "high", "low", "close"]]
    return df.set_index("date").sort_index()


def realized_vol_annualized(close, win=VOL_LOOKBACK):
    logret = np.log(close / close.shift(1))
    return logret.rolling(win).std().shift(1) * np.sqrt(365)


def simulate_pair(daily, pair):
    close = daily["close"]
    open_ = daily["open"]
    low = daily["low"]
    don_upper = close.rolling(N_ENTRY).max().shift(1)
    don_exit = close.rolling(N_EXIT).min().shift(1)
    ema20 = close.ewm(span=EMA_WIN, adjust=False).mean()
    rv = realized_vol_annualized(close)

    trades = []
    in_pos = False
    entry_price = None
    entry_idx = None
    signal_low = None
    lev_initial = None
    scaled_in = False
    scale_in_idx = None
    scale_in_price = None

    n_bars = len(daily)
    for i in range(n_bars - 1):
        if not in_pos:
            if not pd.isna(don_upper.iloc[i]) and close.iloc[i] > don_upper.iloc[i]:
                in_pos = True
                entry_price = open_.iloc[i + 1]
                entry_idx = i + 1
                signal_low = low.iloc[i]
                rv_i = rv.iloc[i]
                lev_initial = min(MAX_LEV, TARGET_VOL / rv_i) if (not pd.isna(rv_i) and rv_i > 0) else None
                scaled_in = False
                scale_in_idx = None
                scale_in_price = None
        else:
            if (not scaled_in) and (i > entry_idx) and (lev_initial is not None) and (lev_initial < MAX_LEV):
                r_pct = (entry_price - signal_low) / entry_price if entry_price > signal_low else None
                if r_pct is not None and r_pct > 0:
                    unrealized_pct = close.iloc[i] / entry_price - 1
                    if unrealized_pct >= r_pct:
                        pulled_back = low.iloc[i] < ema20.iloc[i]
                        signal_bar = (close.iloc[i] > open_.iloc[i]) and (close.iloc[i] > ema20.iloc[i])
                        if pulled_back and signal_bar:
                            scaled_in = True
                            scale_in_idx = i + 1
                            scale_in_price = open_.iloc[i + 1]

            exit_hit = (not pd.isna(don_exit.iloc[i])) and close.iloc[i] < don_exit.iloc[i]
            if exit_hit:
                exit_price = open_.iloc[i + 1]
                ret_base = exit_price / entry_price - 1

                if scaled_in:
                    base_units = 1.0
                    add_units = SCALE_IN_RATIO
                    total_cost = base_units * entry_price + add_units * scale_in_price
                    total_units = base_units + add_units
                    blended_entry = total_cost / total_units
                    ret_scaled = exit_price / blended_entry - 1
                    add_leg_ret = exit_price / scale_in_price - 1
                    hold_days_add = (i + 1) - scale_in_idx
                else:
                    ret_scaled = ret_base
                    add_leg_ret = None
                    hold_days_add = None

                trades.append({
                    "pair": pair,
                    "entry_date": daily.index[entry_idx],
                    "exit_date": daily.index[i + 1],
                    "ret_base": ret_base,
                    "ret_scaled": ret_scaled,
                    "scaled_in": scaled_in,
                    "lev_initial": lev_initial,
                    "scale_in_date": daily.index[scale_in_idx] if scaled_in else None,
                    "add_leg_ret": add_leg_ret,
                    "hold_days_add": hold_days_add,
                    "hold_days_total": (i + 1) - entry_idx,
                })
                in_pos = False
                entry_price, entry_idx, signal_low = None, None, None
                lev_initial, scaled_in, scale_in_idx, scale_in_price = None, False, None, None
    return trades


def block_bootstrap_diff_ci(df, ret_col_a, ret_col_b, n_boot=2000, block_days=40, seed=42):
    rng = np.random.RandomState(seed)
    d = df.copy()
    d["block"] = d["entry_date"].astype("int64") // (block_days * 24 * 3600 * 10**9)
    blocks = d["block"].unique()
    grouped = {b: g for b, g in d.groupby("block")}
    diffs = []
    for _ in range(n_boot):
        sampled = rng.choice(blocks, size=len(blocks), replace=True)
        sub = pd.concat([grouped[b] for b in sampled])
        diffs.append(sub[ret_col_a].mean() - sub[ret_col_b].mean())
    diffs = np.array(diffs)
    return np.percentile(diffs, 5), np.percentile(diffs, 95)


def main():
    all_trades = []
    for pair in PAIRS:
        daily = load_daily(pair)
        all_trades.extend(simulate_pair(daily, pair))

    df = pd.DataFrame(all_trades)
    print("=" * 92)
    print("TASK117 pre-check: scaling-in on 20EMA pullback after breakout")
    print("Exploratory audit only, not a confirmatory verdict")
    print("=" * 92)
    print(f"\nTotal simulated trades: {len(df)}  pairs: {df['pair'].nunique()}")

    n_scaled = df["scaled_in"].sum()
    n_lev_capped = ((df["lev_initial"].notna()) & (df["lev_initial"] >= MAX_LEV)).sum()
    n_lev_na = df["lev_initial"].isna().sum()
    print(f"\nTrades with scale-in triggered: {n_scaled}/{len(df)}")
    print(f"  blocked by leverage already at cap: {n_lev_capped}")
    print(f"  insufficient vol history: {n_lev_na}")

    if n_scaled == 0:
        print("\nNo scale-in events triggered at all -> verdict circle (no signal under frozen params).")
        return

    scaled_trades = df[df["scaled_in"]].copy()

    add_win_rate = (scaled_trades["add_leg_ret"] > 0).mean()
    print(f"\n[Indicator C] add-leg standalone win rate: n={len(scaled_trades)} winrate={add_win_rate:.1%}")
    if add_win_rate < 0.5:
        print("  >>> add-leg win rate < 50%, per pre-declared threshold this should be judged circle immediately.")

    top_n = max(3, int(len(df) * 0.10))
    df_sorted_base = df.sort_values("ret_base", ascending=False).reset_index(drop=True)
    top_base = df_sorted_base.head(top_n)
    n_top_scaled = top_base["scaled_in"].sum()
    print(f"\n[Indicator A] Tail Thickening: top {top_n} baseline trades")
    print(top_base[["pair", "entry_date", "ret_base", "ret_scaled", "scaled_in"]].to_string(index=False))
    print(f"\n  of head trades, scaled: {n_top_scaled}/{top_n}")
    if n_top_scaled > 0:
        sub = top_base[top_base["scaled_in"]]
        ratio = sub["ret_scaled"].mean() / sub["ret_base"].mean()
        print(f"  ratio (scaled_ret/base_ret) among scaled head trades = {ratio:.3f}")
    else:
        print("  none of the head trades were touched by scale-in -> tail-thickening premise not supported.")

    print(f"\n[Indicator B] Capital Utilization")
    mean_hold_add = scaled_trades["hold_days_add"].mean()
    mean_hold_total = scaled_trades["hold_days_total"].mean()
    print(f"  mean add-leg hold days: {mean_hold_add:.1f}  mean total hold days: {mean_hold_total:.1f}"
          f"  ratio: {mean_hold_add/mean_hold_total:.1%}")
    print(f"  add-leg mean ret: {scaled_trades['add_leg_ret'].mean():+.2%}"
          f"  median: {scaled_trades['add_leg_ret'].median():+.2%}")

    print(f"\n[Indicator D] full-sample (n={len(df)}) net comparison: scaled vs base")
    sum_base = df["ret_base"].sum()
    sum_scaled = df["ret_scaled"].sum()
    print(f"  base (arith sum): {sum_base:+.2%}")
    print(f"  scaled (arith sum): {sum_scaled:+.2%}")
    print(f"  diff: {sum_scaled - sum_base:+.2%}")

    mean_base = df["ret_base"].mean()
    mean_scaled = df["ret_scaled"].mean()
    print(f"\n  per-trade mean: base={mean_base:+.4%} scaled={mean_scaled:+.4%} diff={mean_scaled-mean_base:+.4%}")

    n_valid_for_boot = len(df)
    if n_valid_for_boot < 30:
        print(f"\n  n={n_valid_for_boot} < 30, switching to block bootstrap (40d block, 2000 resamples)")
        lo, hi = block_bootstrap_diff_ci(df, "ret_scaled", "ret_base")
        print(f"  block bootstrap 90% CI (scaled-base mean diff) = [{lo:+.4%}, {hi:+.4%}]")
        if lo > 0:
            print("  CI lower bound > 0: directional support (still needs joint judgment with effect size/DD/OOS)")
        else:
            print("  CI straddles zero: cannot rule out null effect, must be treated as circle regardless of point estimate")

    print("\n" + "=" * 92)


if __name__ == "__main__":
    main()
