"""
STOCK-TASK 5 确认阶段——bStocks 候选池"质量门槛"（历史长度）重筛版
Donchian 20/10 突破前瞻收益检验。

背景：STOCK-TASK 4（原TASK118）事后拆解显示，TASK108原16支单独看效应稳健
（+1.51%，CI不跨零），新增的23支（多为2023年后上市/主题炒作次新股）把合并
效应从约+1.5%摊薄到+0.44%并使CI跨零。本轮改用机械规则（上市历史>=3年）
替代黑箱主题标签筛选候选池，验证"历史长度是关键筛选变量"这一假设。

诚实声明：该假设产生自TASK118同批数据的事后拆解，非独立先验，证据强度打折。
缓解：主判定用全部过筛标的；同时单独报告①过筛标的∩TASK108原16支、②过筛标的
中从未被TASK108/118检验过的子集——子集②是相对独立的验证，若同向且CI不跨零，
证据强度显著高于主判定本身。

判据（沿用TASK108冻结模板，2026-08-16用户确认原样复用）：
P1 fwd20d市场中性前瞻收益90% CI下界>0；P2 点估计>=1.0%；P3 前后半段同号；
P4 n>=30；OOS 最后20%时间段同号且>=0.5%。

用户确认的候选宇宙处理（2026-08-16）：
- 币安bStocks当前USDT交易对中baseAsset以B结尾的symbol，75支粗筛
- 剔除4支加密货币代号撞车：BNB(BN)/SHIB(SHI)/QNTB(QNT)/ARB(AR，用户判定按加密处理)
- 剔除7支ETF/杠杆产品：QQQ/SPY/SMH/SOXL/SOXS/TQQQ/EWY
- 剩64支单一正股候选，作为本次历史长度筛选的输入宇宙
- 历史门槛 X=3年（cutoff=2023-08-16），不加成交额第二道门槛（用户确认）

修正版（2026-08-16 第二次跑数，用户确认后修正两处执行缺陷）：
1. 宇宙污染修正：用"币安K线首根蜡烛早于bStocks上线（2026-06）即为加密"的机械
   检验抓出5支漏网撞车——BB(BounceBit)/DGB(DigiByte)/TRB(Tellor)/CKB(Nervos)/
   YB。其中B/DG/TR曾以Barnes/Dollar General/Tootsie Roll正股身份混入第一次跑数
   （且全部落在②未测过子集），本版连同CK/Y（此前仅因拉取失败偶然未混入）一并剔除，宇宙64→59支。
2. 窗口口径修正：第一次跑数误用fromdate=2010-01-01（16年窗口），偏离TASK108/118
   冻结口径fromdate=2020-08-01（约5年窗口）且未经用户确认，本版改回2020-08-01。
诚实声明：本次为已看过错误窗口结果后的重跑，非首次盲测，证据强度打折。
"""
import random
import time

import pandas as pd
import requests

CANDIDATES = [
    "AAOI", "AAPL", "ALAB", "AMAT", "AMD", "AMZN", "ARM", "ASML", "ASTS",
    "AVGO", "AXTI", "BABA", "BE", "BMNR", "CBRS", "COHR", "COIN",
    "CRCL", "CRDO", "CRWV", "DELL", "DRAM", "FLNC", "GLW", "GME",
    "GOOGL", "GS", "HOOD", "IBM", "INTC", "INTW", "IREN", "KORU", "LITE",
    "META", "MRVL", "MSFT", "MSTR", "MU", "MUU", "MVLL", "NBIS", "NFLX",
    "NOK", "NVDA", "ORCL", "PLTR", "PYPL", "QCOM", "RKLB", "SKHY", "SMCI",
    "SNDK", "SNXX", "SPCX", "TSLA", "TSM", "USAR", "WDC",
]

TASK108_16 = {
    "NVDA", "TSLA", "CRCL", "MU", "SNDK", "AAPL", "AMZN", "GOOGL", "NFLX",
    "ASML", "GS", "PYPL", "COIN", "HOOD", "IBM", "NOK",
}
TASK118_31 = {
    "AMAT", "AMZN", "ASML", "ASTS", "AVGO", "BE", "COHR", "COIN", "CRCL",
    "CRDO", "CRWV", "DELL", "FLNC", "GLW", "INTC", "IREN", "LITE", "META",
    "MRVL", "MSFT", "MU", "NBIS", "NVDA", "ORCL", "PLTR", "RKLB", "SNDK",
    "SPCX", "TSLA", "TSM", "WDC",
}
PREVIOUSLY_TESTED = TASK108_16 | TASK118_31

N_ENTRY = 20
HORIZON = 20
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

P2_THRESHOLD = 0.010
OOS_THRESHOLD = 0.005
OOS_FRACTION = 0.20
HISTORY_YEARS_MIN = 3
TODAY = pd.Timestamp("2026-08-16")
HISTORY_CUTOFF = TODAY - pd.Timedelta(days=365 * HISTORY_YEARS_MIN)


def fetch_nasdaq_daily(ticker, fromdate="2020-08-01", todate="2026-08-16"):
    url = (
        f"https://api.nasdaq.com/api/quote/{ticker}/historical"
        f"?assetclass=stocks&fromdate={fromdate}&todate={todate}&limit=9999"
    )
    r = requests.get(url, headers=HEADERS, timeout=30)
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


def date_block_bootstrap(events, col, n_boot=1000, seed=20260816):
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


def report_group(name, events, col):
    r = date_block_bootstrap(events, col)
    if r is None:
        print(f"  [{name}] 无事件")
        return
    print(f"  [{name}] n={r['n_events']} point={r['point']:+.4%} "
          f"90%CI=[{r['ci_lo']:+.4%},{r['ci_hi']:+.4%}]  "
          f"{'不跨零' if r['ci_lo']>0 else '跨零'}")


def main():
    print("=" * 78)
    print("STOCK-TASK 5 —— bStocks候选池质量门槛（历史长度>=3年）重筛版")
    print("=" * 78)

    # ---- 第一步：拉取全部64支候选的历史，做3年门槛过筛（机械规则，跑数前冻结）----
    frames = {}
    first_dates = {}
    for tk in CANDIDATES:
        df, err = fetch_nasdaq_daily(tk)
        if err:
            print(f"[SKIP] {tk}: {err}")
            time.sleep(1.0)
            continue
        first_dates[tk] = df["date"].min()
        frames[tk] = df
        time.sleep(1.0)
    print(f"\n成功获取历史数据 {len(frames)}/{len(CANDIDATES)} 支候选")

    qualified = [tk for tk, d in first_dates.items() if d <= HISTORY_CUTOFF]
    excluded = [tk for tk, d in first_dates.items() if d > HISTORY_CUTOFF]
    print(f"\n历史门槛过筛（>= {HISTORY_YEARS_MIN}年，即首个交易日 <= {HISTORY_CUTOFF.date()}）：")
    print(f"  通过：{len(qualified)}支 -> {sorted(qualified)}")
    print(f"  剔除（历史不足{HISTORY_YEARS_MIN}年）：{len(excluded)}支 -> "
          f"{[(tk, str(first_dates[tk].date())) for tk in sorted(excluded)]}")

    col = f"fwd_ret_{HORIZON}d_mn"
    qualified_frames = {tk: frames[tk] for tk in qualified}
    events = build_events(qualified_frames)
    print(f"\n过筛后({len(qualified)}支)全期突破事件总数：{len(events)}")

    # ---- 主判定：P1-P4 + OOS ----
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
        verdict = "PASS_ALL"
    elif full["ci_hi"] < 0:
        verdict = "FALSIFY"
    else:
        verdict = "NULL"
    print(f"主判定：{verdict}")

    # ---- 样本切分报告（本路径特有，必须做）----
    print("\n" + "-" * 78)
    print("样本切分报告（诊断用途，与主判定共同构成三档判定依据）：")
    qualified_set = set(qualified)
    overlap_tested = qualified_set & PREVIOUSLY_TESTED
    never_tested = qualified_set - PREVIOUSLY_TESTED
    print(f"  ①过筛∩TASK108/118已测过：{len(overlap_tested)}支 -> {sorted(overlap_tested)}")
    print(f"  ②过筛中从未被TASK108/118测过（相对独立子集）：{len(never_tested)}支 -> {sorted(never_tested)}")

    ev1 = events[events["ticker"].isin(overlap_tested)]
    ev2 = events[events["ticker"].isin(never_tested)]
    report_group("①已测过子集", ev1, col)
    report_group("②未测过子集（相对独立）", ev2, col)
    print("=" * 78)


if __name__ == "__main__":
    main()
