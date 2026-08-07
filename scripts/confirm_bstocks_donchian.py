"""
确认阶段——TASK 108 冻结判据执行脚本。判据已于 2026-08-06 冻结（见
TASK_NEW/quant_system_tasks.md TASK108），本脚本只跑数、不得改判据。

探索阶段与本次用同一批历史数据，证据强度按项目纪律打折（如实声明，不包装成独立验证）。
"""
import random
import time

import pandas as pd
import requests

TICKERS = [
    "NVDA", "TSLA", "CRCL", "MU", "SNDK", "AAPL", "AMZN", "GOOGL",
    "NFLX", "ASML", "GS", "PYPL", "COIN", "HOOD", "IBM", "NOK",
]

N_ENTRY = 20
HORIZON = 20  # 主判据口径：fwd20d 市场中性
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

P2_THRESHOLD = 0.010
OOS_THRESHOLD = 0.005
OOS_FRACTION = 0.20


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


def date_block_bootstrap(events, col, n_boot=1000, seed=20260806):
    if events.empty:
        return None
    by_date = {}
    for d, v in zip(events["date_only"], events[col]):
        by_date.setdefault(d, []).append(v)
    dates = sorted(by_date.keys())
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
    point = events[col].mean()
    return {"point": point, "ci_lo": lo, "ci_hi": hi, "n_events": len(events), "n_dates": n_dates}


def build_events(frames):
    all_df = pd.concat(frames.values(), ignore_index=True)
    all_df["date_only"] = all_df["date"].dt.date
    all_df[f"fwd_ret_{HORIZON}d"] = all_df.groupby("ticker")["close"].transform(
        lambda s: s.shift(-HORIZON) / s - 1
    )
    width_by_day = all_df.groupby("date_only")["ticker"].transform("nunique")
    all_df = all_df[width_by_day >= 8].copy()
    cross_mean = all_df.groupby("date_only")[f"fwd_ret_{HORIZON}d"].transform("mean")
    all_df[f"fwd_ret_{HORIZON}d_mn"] = all_df[f"fwd_ret_{HORIZON}d"] - cross_mean

    events_list = []
    for tk, df in frames.items():
        df = df.copy()
        df["don_upper"] = df["high"].rolling(N_ENTRY).max().shift(1)
        df["breakout"] = df["don_upper"].notna() & (df["close"] > df["don_upper"])
        sub = all_df[all_df["ticker"] == tk][["date_only", f"fwd_ret_{HORIZON}d_mn"]]
        merged = df.merge(sub, left_on=df["date"].dt.date, right_on="date_only", how="inner")
        ev = merged[merged["breakout"]].dropna(subset=[f"fwd_ret_{HORIZON}d_mn"])
        if not ev.empty:
            events_list.append(ev)
    return pd.concat(events_list, ignore_index=True) if events_list else pd.DataFrame()


def main():
    print("=" * 78)
    print("确认阶段 —— TASK 108 冻结判据执行（P1-P4 + OOS）")
    print("=" * 78)

    frames = {}
    for tk in TICKERS:
        df, err = fetch_nasdaq_daily(tk)
        if err:
            print(f"[SKIP] {tk}: {err}")
            continue
        frames[tk] = df
        time.sleep(0.5)
    print(f"成功获取 {len(frames)}/{len(TICKERS)} 支标的\n")

    col = f"fwd_ret_{HORIZON}d_mn"
    events = build_events(frames)
    print(f"全期突破事件总数：{len(events)}")

    # ---- 全期：P1/P2/P3 ----
    full = date_block_bootstrap(events, col)
    p1 = full["ci_lo"] > 0
    p2 = full["point"] >= P2_THRESHOLD
    print(f"\n[全期] n={full['n_events']} point={full['point']:+.4%} "
          f"90%CI=[{full['ci_lo']:+.4%},{full['ci_hi']:+.4%}]  P1={'PASS' if p1 else 'FAIL'}  P2={'PASS' if p2 else 'FAIL'}")

    events_sorted = events.sort_values("date_only")
    mid = len(events_sorted) // 2
    first_half, second_half = events_sorted.iloc[:mid], events_sorted.iloc[mid:]
    m1, m2 = first_half[col].mean(), second_half[col].mean()
    p3 = (m1 > 0) == (m2 > 0)
    print(f"[P3 时间稳健性] 前半段均值={m1:+.4%}  后半段均值={m2:+.4%}  同号={'PASS' if p3 else 'FAIL'}")

    p4 = full["n_events"] >= 30
    print(f"[P4 样本量] n={full['n_events']} >=30: {'PASS' if p4 else 'FAIL'}")

    # ---- OOS：最后20%时间段独立复核 ----
    all_dates = sorted(events["date_only"].unique())
    cutoff = all_dates[int(len(all_dates) * (1 - OOS_FRACTION))]
    oos_events = events[events["date_only"] >= cutoff]
    oos = date_block_bootstrap(oos_events, col)
    oos_same_sign = (oos["point"] > 0) == (full["point"] > 0)
    oos_pass_threshold = oos["point"] >= OOS_THRESHOLD if full["point"] > 0 else oos["point"] <= -OOS_THRESHOLD
    oos_pass = oos_same_sign and oos_pass_threshold
    print(f"\n[OOS 最后20%，cutoff={cutoff}] n={oos['n_events']} point={oos['point']:+.4%} "
          f"90%CI=[{oos['ci_lo']:+.4%},{oos['ci_hi']:+.4%}]  同号={'Y' if oos_same_sign else 'N'}  "
          f"OOS={'PASS' if oos_pass else 'FAIL'}")

    print("\n" + "=" * 78)
    all_pass = p1 and p2 and p3 and p4 and oos_pass
    if all_pass:
        verdict = "✅ 采纳候选（P1+P2+P3+P4+OOS 全过）"
    else:
        neg_direction = full["ci_hi"] < 0
        if neg_direction:
            verdict = "❌ 证伪（反向且可定位）"
        else:
            verdict = "⭕ 不采纳/无改善（未全部满足判定映射）"
    print(f"最终判定：{verdict}")
    print("=" * 78)


if __name__ == "__main__":
    main()
