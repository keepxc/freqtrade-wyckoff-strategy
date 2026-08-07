"""
探索性——非结论，禁止引用为证据。

币安 bStocks 候选方向：正股历史（近5年，Nasdaq 官方API，非Yahoo）上
Donchian 20/10（与 DonchianV1 完全一致的零调参参数）突破信号的前瞻收益长相。

标的清单为已知子集（16支，非币安当前全部46+支——完整清单待用户补充），
仅用于看数据、判断是否值得转入确认阶段，不构成任何结论。

方法沿用本项目 funding carry / Wyckoff 系列已验证的纪律：
- 市场中性口径（减当日截面均值）剥离共同市场beta
- 按日期区块的跨标的联合自举（不假装"n=事件数"独立）
"""
import random
import re
import time

import pandas as pd
import requests

TICKERS = [
    "NVDA", "TSLA", "CRCL", "MU", "SNDK", "AAPL", "AMZN", "GOOGL",
    "NFLX", "ASML", "GS", "PYPL", "COIN", "HOOD", "IBM", "NOK",
]

N_ENTRY = 20
N_EXIT = 10
HORIZONS = (10, 20)
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def fetch_nasdaq_daily(ticker, fromdate="2020-08-01", todate="2026-08-06"):
    url = (
        f"https://api.nasdaq.com/api/quote/{ticker}/historical"
        f"?assetclass=stocks&fromdate={fromdate}&todate={todate}&limit=9999"
    )
    r = requests.get(url, headers=HEADERS, timeout=20)
    if r.status_code != 200:
        return None, f"status={r.status_code}"
    try:
        rows = r.json()["data"]["tradesTable"]["rows"]
    except Exception as e:
        return None, f"parse_error={e}"
    if not rows:
        return None, "empty_rows"
    df = pd.DataFrame(rows)
    for col in ("close", "open", "high", "low"):
        df[col] = df[col].astype(str).str.replace("$", "", regex=False).str.replace(",", "", regex=False)
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["volume"] = df["volume"].astype(str).str.replace(",", "", regex=False)
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
    df["date"] = pd.to_datetime(df["date"], format="%m/%d/%Y")
    df = df.sort_values("date").reset_index(drop=True)
    df["ticker"] = ticker
    return df, None


def date_block_bootstrap(event_frames_by_ticker, col, n_boot=1000, seed=20260806):
    """按日期区块跨标的联合自举：同一 resample 出的日期，所有标的当天的事件一起入组。"""
    all_events = []
    for tk, df in event_frames_by_ticker.items():
        for _, row in df.iterrows():
            all_events.append((row["date_only"], row[col]))
    if not all_events:
        return None
    dates = sorted(set(d for d, _ in all_events))
    by_date = {}
    for d, v in all_events:
        by_date.setdefault(d, []).append(v)
    rng = random.Random(seed)
    n_dates = len(dates)
    boot_means = []
    for _ in range(n_boot):
        resampled = [dates[rng.randrange(n_dates)] for _ in range(n_dates)]
        vals = []
        for d in resampled:
            vals.extend(by_date[d])
        if vals:
            boot_means.append(sum(vals) / len(vals))
    boot_means.sort()
    lo = boot_means[int(0.05 * len(boot_means))]
    hi = boot_means[max(int(0.95 * len(boot_means)) - 1, 0)]
    point = sum(v for _, v in all_events) / len(all_events)
    return {"point": point, "ci_lo": lo, "ci_hi": hi, "n_events": len(all_events), "n_dates": n_dates}


def main():
    print("=" * 78)
    print("探索性——非结论，禁止引用为证据")
    print("bStocks 候选正股 x Donchian 20/10 突破 —— 前瞻收益长相（16支子集，近5年，Nasdaq源）")
    print("=" * 78)

    frames = {}
    for i, tk in enumerate(TICKERS):
        df, err = fetch_nasdaq_daily(tk)
        if err:
            print(f"[SKIP] {tk}: {err}")
            continue
        frames[tk] = df
        print(f"  fetched {tk}: {len(df)} rows, {df['date'].min().date()} -> {df['date'].max().date()}")
        time.sleep(0.5)

    print(f"\n成功获取 {len(frames)}/{len(TICKERS)} 支标的历史数据")

    # 市场中性口径：先算全体标的每日 close-to-close forward return，再减当日截面均值
    all_df = pd.concat(frames.values(), ignore_index=True)
    all_df["date_only"] = all_df["date"].dt.date
    for h in HORIZONS:
        all_df[f"fwd_ret_{h}d"] = all_df.groupby("ticker")["close"].transform(
            lambda s, h=h: s.shift(-h) / s - 1
        )
    width_by_day = all_df.groupby("date_only")["ticker"].transform("nunique")
    all_df = all_df[width_by_day >= 8].copy()  # 至少8支同一天有数据才纳入截面均值
    for h in HORIZONS:
        cross_mean = all_df.groupby("date_only")[f"fwd_ret_{h}d"].transform("mean")
        all_df[f"fwd_ret_{h}d_mn"] = all_df[f"fwd_ret_{h}d"] - cross_mean

    # Donchian 20/10 通道（shift(1)，因果，与 DonchianV1 完全一致）
    events_by_ticker = {}
    for tk, df in frames.items():
        df = df.copy()
        df["don_upper"] = df["high"].rolling(N_ENTRY).max().shift(1)
        df["breakout"] = df["don_upper"].notna() & (df["close"] > df["don_upper"])
        merged = df.merge(
            all_df[all_df["ticker"] == tk][["date_only"] + [f"fwd_ret_{h}d_mn" for h in HORIZONS]],
            left_on=df["date"].dt.date, right_on="date_only", how="inner",
        )
        ev = merged[merged["breakout"]].dropna(subset=[f"fwd_ret_{h}d_mn" for h in HORIZONS])
        if not ev.empty:
            events_by_ticker[tk] = ev

    total_events = sum(len(v) for v in events_by_ticker.values())
    print(f"\n突破事件总数（全部标的合计）：{total_events}")
    for tk, ev in events_by_ticker.items():
        print(f"  {tk}: {len(ev)} 次突破")

    print("\n" + "=" * 78)
    print("市场中性前瞻收益 —— 按日期区块跨标的联合自举（1000次，90% CI）")
    print("=" * 78)
    for h in HORIZONS:
        col = f"fwd_ret_{h}d_mn"
        result = date_block_bootstrap(events_by_ticker, col)
        if result is None:
            print(f"fwd{h}d: 无有效事件")
            continue
        print(
            f"fwd{h}d 市场中性: n_events={result['n_events']} n_dates={result['n_dates']} "
            f"point={result['point']:+.4%} 90%CI=[{result['ci_lo']:+.4%}, {result['ci_hi']:+.4%}]"
        )

    print("\n" + "=" * 78)
    print("探索性——非结论，禁止引用为证据。标的清单为16支已知子集，非币安当前全部46+支。")
    print("=" * 78)


if __name__ == "__main__":
    main()
