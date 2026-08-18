"""
TASK 116 终审判据：门控通过组(A) vs 拦截组(B) 期望值对比 + 区块自举CI
探索性核查——非正式判定，不入证伪/采纳表，仅用于是否立项/推进Docker回测的判断依据。

判定逻辑（用户2026-08-16确认）：
  绿灯（值得推进全量回测）：Mean_A > Mean_B 且差距 > 1pp，且区块自举90% CI下界>0
  红灯（❌，直接不推进）：Mean_A <= Mean_B，或差异在自举CI下跨零
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
N_BOOT = 2000
BLOCK_DAYS = 40


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
                    "vol_pct_at_signal": vol_pct,
                    "gate_pass": (vol_pct is not None) and (vol_pct <= VOL_PCT_THRESHOLD),
                    "gate_data_insufficient": vol_pct is None,
                })
                in_pos = False
                entry_price, entry_idx, signal_idx = None, None, None
    return trades


def block_bootstrap_diff_ci(df, n_boot=N_BOOT, seed=42):
    """Group A(gate_pass) - Group B(拦截) 均值差 的跨资产联合区块自举90%CI"""
    rng = np.random.RandomState(seed)
    d = df.copy()
    d["block"] = d["entry_date"].astype("int64") // (BLOCK_DAYS * 24 * 3600 * 10**9)
    blocks = d["block"].unique()
    grouped = {b: g for b, g in d.groupby("block")}
    diffs = []
    for _ in range(n_boot):
        sampled = rng.choice(blocks, size=len(blocks), replace=True)
        sub = pd.concat([grouped[b] for b in sampled])
        a = sub[sub["gate_pass"]]["ret"]
        b = sub[~sub["gate_pass"]]["ret"]
        if len(a) >= 5 and len(b) >= 5:
            diffs.append(a.mean() - b.mean())
    diffs = np.array(diffs)
    if len(diffs) < 100:
        return None, None, len(diffs)
    return np.percentile(diffs, 5), np.percentile(diffs, 95), len(diffs)


def main():
    all_trades = []
    for pair in PAIRS:
        daily = load_daily(pair)
        all_trades.extend(simulate_pair(daily, pair))

    df = pd.DataFrame(all_trades)
    valid = df[~df["gate_data_insufficient"]].copy()

    grpA = valid[valid["gate_pass"]]["ret"]
    grpB = valid[~valid["gate_pass"]]["ret"]

    print("=" * 90)
    print("TASK116 终审判据：门控通过组(A) vs 拦截组(B) 期望值对比 + 区块自举CI")
    print("探索性核查——非正式判定，不入证伪/采纳表")
    print("=" * 90)
    print(f"\n有效样本 n={len(valid)}（Group A 通过门控={len(grpA)}，Group B 被拦截={len(grpB)}）")

    print(f"\n【Group A（通过门控，vol_pct<=25%）】")
    print(f"  n={len(grpA)}  均值={grpA.mean():+.4%}  中位数={grpA.median():+.4%}  标准差={grpA.std():.4%}")

    print(f"\n【Group B（被拦截，vol_pct>25%）】")
    print(f"  n={len(grpB)}  均值={grpB.mean():+.4%}  中位数={grpB.median():+.4%}  标准差={grpB.std():.4%}")

    diff_mean = grpA.mean() - grpB.mean()
    diff_median = grpA.median() - grpB.median()
    print(f"\n【组间差】")
    print(f"  均值差(A-B) = {diff_mean:+.4%}")
    print(f"  中位数差(A-B) = {diff_median:+.4%}")

    lo, hi, n_eff = block_bootstrap_diff_ci(valid)
    if lo is None:
        print(f"\n【区块自举90% CI】自举失败（有效重抽样本不足，仅{n_eff}次）")
        ci_pass = False
    else:
        print(f"\n【区块自举90% CI（跨资产联合，40天分块，{n_eff}次有效重抽）】")
        print(f"  CI = [{lo:+.4%}, {hi:+.4%}]")
        ci_pass = lo > 0

    print("\n" + "=" * 90)
    print("【判定（按用户2026-08-16确认的判定逻辑）】")
    green = (diff_mean > 0.01) and ci_pass
    red = (diff_mean <= 0) or (not ci_pass)
    print(f"  绿灯条件(Mean_A>Mean_B 且差距>1pp 且 CI下界>0): {'PASS' if green else 'FAIL'}")
    print(f"  红灯条件(Mean_A<=Mean_B 或 CI跨零): {'触发' if red else '未触发'}")
    if green:
        verdict = "绿灯——期望值改善显著且自举CI支持方向性，值得推进到Docker全量回测"
    elif red:
        verdict = "红灯 ❌——门控对信号质量无统计增益（期望值未改善或CI跨零），按⭕/❌处理，不推进"
    else:
        verdict = "中间态——需人工复核（如差距为正但<1pp，或CI边界情况）"
    print(f"\n  最终判定: {verdict}")
    print("=" * 90)


if __name__ == "__main__":
    main()
