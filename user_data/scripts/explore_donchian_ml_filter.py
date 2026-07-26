"""
探索性脚本——非结论，禁止引用为证据 (CLAUDE.md 探索性/确认性两阶段纪律)

目的：DonchianV1 (TASK72-75b 封板信号，20/10 通道纯多头) 历史入场里，
"真突破"(盈利笔) 与"假突破"(亏损笔，很快被10日通道止损打回) 在入场时点
的候选特征上有无可分性——为"是否值得为 ML 过滤立预注册电池"提供参考。
不构成 TASK 结论，不进证伪表/采纳表。

方法：完整复刻 DonchianV1.py 因果口径（don_upper/don_exit 均 shift(1)，
候选特征全部只用截至入场当日已收盘数据，无 df.iloc[i+n] 前视），
状态机模拟真实单仓 flat/in-position 序列，逐笔标注 exit 收益，
对比"盈利笔"与"亏损笔"入场时点特征均值差异。不做假设检验、不定阈值。
"""
import numpy as np
import pandas as pd

PATHS = {
    "spot": "user_data/data/binance/BTC_USDT-1d.feather",
    "futures": "user_data/data/binance/futures/BTC_USDT_USDT-1d-futures.feather",
}

N_ENTRY = 20
N_EXIT = 10

FEATURES = [
    "rsi14", "adx14", "vol_ratio", "breakout_pct",
    "days_above_ma200", "days_since_last_break", "realized_vol",
]


def compute_features(raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    df = raw.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    # --- DonchianV1 因果信号复刻（与 strategies/DonchianV1.py 完全一致）---
    df["don_upper"] = df["high"].rolling(N_ENTRY).max().shift(1)
    df["don_exit"] = df["low"].rolling(N_EXIT).min().shift(1)
    ret = df["close"].pct_change()
    df["realized_vol"] = ret.rolling(20).std().shift(1) * np.sqrt(365)

    # --- 候选 ML 特征（全部只用截至 t 已收盘数据，无前视）---
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss
    df["rsi14"] = 100 - 100 / (1 + rs)

    # ADX(14) 简化实现（rolling mean 代替 Wilder 平滑，探索阶段够用）
    high, low, close = df["high"], df["low"], df["close"]
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr14 = tr.rolling(14).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).rolling(14).mean() / atr14
    minus_di = 100 * pd.Series(minus_dm, index=df.index).rolling(14).mean() / atr14
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    df["adx14"] = dx.rolling(14).mean()

    df["vol_ma20"] = df["volume"].rolling(20).mean()
    df["vol_ratio"] = df["volume"] / df["vol_ma20"]

    df["breakout_pct"] = df["close"] / df["don_upper"] - 1.0

    df["ma200"] = df["close"].rolling(200).mean()
    above_ma200 = (df["close"] > df["ma200"]).astype(int)
    grp = (above_ma200 != above_ma200.shift(1)).cumsum()
    df["days_above_ma200"] = above_ma200.groupby(grp).cumcount() + 1
    df.loc[above_ma200 == 0, "days_above_ma200"] = 0

    is_break_raw = df["don_upper"].notna() & (df["close"] > df["don_upper"])
    last_break_idx = (
        pd.Series(np.where(is_break_raw, df.index, np.nan), index=df.index)
        .ffill()
        .shift(1)
    )
    df["days_since_last_break"] = df.index.to_series().reset_index(drop=True) - last_break_idx

    return df, is_break_raw


def simulate_trades(df: pd.DataFrame, is_break_raw: pd.Series) -> pd.DataFrame:
    """状态机复刻单仓 flat/in-position 序列，逐笔标注 exit 收益。"""
    trades = []
    in_pos = False
    entry_i = None
    for i in range(len(df)):
        if not in_pos:
            if bool(is_break_raw.iloc[i]):
                in_pos = True
                entry_i = i
        else:
            don_exit = df["don_exit"].iloc[i]
            exit_sig = pd.notna(don_exit) and df["close"].iloc[i] < don_exit
            if exit_sig:
                entry_close = df["close"].iloc[entry_i]
                exit_close = df["close"].iloc[i]
                trades.append({
                    "entry_i": entry_i,
                    "exit_i": i,
                    "entry_date": df["date"].iloc[entry_i],
                    "exit_date": df["date"].iloc[i],
                    "hold_days": i - entry_i,
                    "ret_pct": exit_close / entry_close - 1.0,
                })
                in_pos = False
    return pd.DataFrame(trades)


def analyze(path: str, label: str):
    raw = pd.read_feather(path)
    df, is_break_raw = compute_features(raw)
    trades = simulate_trades(df, is_break_raw)

    print(f"\n=== {label} ===")
    if trades.empty:
        print("无完整交易，跳过")
        return None

    feat_at_entry = df.loc[trades["entry_i"], FEATURES].reset_index(drop=True)
    trades = pd.concat([trades.reset_index(drop=True), feat_at_entry], axis=1)

    good = trades[trades["ret_pct"] > 0]
    bad = trades[trades["ret_pct"] <= 0]

    print(f"总交易 {len(trades)} 笔（盈利 {len(good)} / 亏损 {len(bad)}，"
          f"胜率 {len(good) / len(trades) * 100:.1f}%）")
    print(f"平均持仓 {trades['hold_days'].mean():.1f} 天，"
          f"平均收益 {trades['ret_pct'].mean() * 100:+.2f}%")

    print(f"\n{'特征':<22}{'盈利笔均值':>14}{'亏损笔均值':>14}{'全体均值':>12}{'n(盈/亏)':>12}")
    for f in FEATURES:
        g = good[f].dropna()
        b = bad[f].dropna()
        a = trades[f].dropna()
        if len(g) == 0 or len(b) == 0:
            continue
        print(f"{f:<22}{g.mean():>14.3f}{b.mean():>14.3f}{a.mean():>12.3f}"
              f"{f'{len(g)}/{len(b)}':>12}")

    return trades


if __name__ == "__main__":
    print("探索性脚本——非结论，禁止引用为证据")
    for label, path in PATHS.items():
        analyze(path, label)
