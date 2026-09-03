#!/usr/bin/env python3
"""
AI500 多窗口 net_flow 对照分析
================================
探索性——非结论，禁止引用为证据。

目的：回答"策略当前用的 1h 窗口 rank，相比 5m/15m/30m 窗口，是不是一个滞后信号"。
背景：2026-09-02 实测确认 /trending-crypto 不传 duration 时默认 = 1h（与 duration=1h
逐位相同，与 30m 中位差 37.7%），而策略的 OI/BTC bias/K线/执行粒度全是 30m。

分四部分，前三部分**不需要未来价格**，样本再短也成立；第四部分需要前瞻窗口，
数据不足时只作管道验证（明确标注，不得当结论）。

  1. 窗口间 top3 重叠度 —— 同一时刻各窗口选出的是不是同一批币
  2. top3 成员换手率   —— 各窗口的稳定性/噪声水平
  3. 领先-滞后         —— 同一个币先进哪个窗口的 top3，1h 比 5m 晚多久（核心）
  4. 前瞻收益          —— 币安 1m K 线，信号 ts 后第一根已收盘 bar 起算（TASK130 同源铁律）

用法:
  python scripts/ai500_window_analysis.py [--fwd-min 30] [--no-price]
"""
import argparse
import glob
import os
import sys
from itertools import combinations

import pandas as pd

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
DATA_DIR = os.path.join(_REPO_ROOT, "user_data", "ai500_local")

PROXY = {"http": os.environ.get("LOCAL_PROXY", "http://127.0.0.1:10809"), "https": os.environ.get("LOCAL_PROXY", "http://127.0.0.1:10809")}
DURATIONS = ["5m", "15m", "30m", "1h"]
TOP_N = 3            # 与策略 ENTRY_MEDIAN=3 对齐
FEE_PCT = 0.1        # 双边 0.05%+0.05%

BANNER = "探索性——非结论，禁止引用为证据"


def load() -> pd.DataFrame:
    files = sorted(glob.glob(os.path.join(DATA_DIR, "netflow_multiwin_*.csv")))
    if not files:
        print(f"[err] no data in {DATA_DIR}")
        sys.exit(1)
    df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    df["t"] = pd.to_datetime(df["fetched_at_cst"])
    return df


def part1_overlap(df: pd.DataFrame) -> None:
    print(f"\n{'='*66}\n1. 同一时刻各窗口 top{TOP_N} 的重叠度\n{'='*66}")
    top = df[(df.side == "top") & (df["rank"] <= TOP_N)]
    sets = {}
    for t, g in top.groupby("t"):
        for d in DURATIONS:
            sets.setdefault((t, d), set(g[g.duration == d].symbol))
    times = sorted({t for t, _ in sets})
    print(f"快照数 {len(times)}")
    print(f"\n{'窗口对':<14}{'平均重叠数':>10}{'完全相同占比':>14}")
    for a, b in combinations(DURATIONS, 2):
        ov, same = [], []
        for t in times:
            sa, sb = sets.get((t, a), set()), sets.get((t, b), set())
            if not sa or not sb:
                continue
            ov.append(len(sa & sb))
            same.append(sa == sb)
        if ov:
            print(f"{a:>4} vs {b:<6}{sum(ov)/len(ov):>10.2f}/{TOP_N}"
                  f"{100*sum(same)/len(same):>13.0f}%")


def part2_churn(df: pd.DataFrame) -> None:
    print(f"\n{'='*66}\n2. top{TOP_N} 成员换手率（相邻快照间成员变化）\n{'='*66}")
    top = df[(df.side == "top") & (df["rank"] <= TOP_N)]
    print(f"\n{'窗口':<8}{'相邻快照有变化占比':>20}{'平均换掉几个':>14}")
    for d in DURATIONS:
        g = top[top.duration == d]
        snaps = [set(x.symbol) for _, x in g.groupby("t")]
        if len(snaps) < 2:
            continue
        ch = [len(snaps[i] ^ snaps[i - 1]) / 2 for i in range(1, len(snaps))]
        print(f"{d:<8}{100*sum(c > 0 for c in ch)/len(ch):>19.0f}%"
              f"{sum(ch)/len(ch):>14.2f}")


def part3_leadlag(df: pd.DataFrame) -> None:
    print(f"\n{'='*66}\n3. 领先-滞后：同一个币先进哪个窗口的 top{TOP_N}\n{'='*66}")
    top = df[(df.side == "top") & (df["rank"] <= TOP_N)]
    t0 = df.t.min()
    first = (top.groupby(["symbol", "duration"])["t"].min().unstack())
    for d in DURATIONS:
        if d not in first.columns:
            first[d] = pd.NaT
    # 左删失：首个快照就已在 top3 的，无法知道它其实何时进的
    censored = first[(first[DURATIONS] == t0).any(axis=1)]
    clean = first[~first.index.isin(censored.index)]
    print(f"出现过 top{TOP_N} 的币 {len(first)} 个 | 左删失（首快照即在榜）{len(censored)} 个"
          f" | 可用 {len(clean)} 个")
    if clean.empty:
        print("  可用样本为 0（采集时间太短，全部左删失）——本节需要更多数据")
        return
    print(f"\n{'币':<14}" + "".join(f"{d:>9}" for d in DURATIONS) + f"{'1h-5m滞后':>12}")
    lags = []
    for sym, r in clean.iterrows():
        cells = []
        for d in DURATIONS:
            v = r.get(d)
            cells.append("-" if pd.isna(v) else v.strftime("%H:%M:%S")[:8])
        lag = ""
        if not pd.isna(r.get("1h")) and not pd.isna(r.get("5m")):
            m = (r["1h"] - r["5m"]).total_seconds() / 60
            lags.append(m)
            lag = f"{m:+.0f}min"
        print(f"{str(sym):<14}" + "".join(f"{c:>9}" for c in cells) + f"{lag:>12}")
    if lags:
        lags_s = pd.Series(lags)
        print(f"\n1h 相对 5m 的滞后：n={len(lags)} 中位 {lags_s.median():+.1f}min "
              f"均值 {lags_s.mean():+.1f}min | 1h 更晚的占 "
              f"{100*(lags_s > 0).mean():.0f}%")
        print("  注：受采集窗口长度截断，滞后量是下界（超出观察窗的滞后看不到）")


def part4_forward(df: pd.DataFrame, fwd_min: int) -> None:
    print(f"\n{'='*66}\n4. 前瞻收益 {fwd_min}min（币安 1m K 线，扣费 {FEE_PCT}%）\n{'='*66}")
    try:
        import ccxt
    except ImportError:
        print("  ccxt 未安装，跳过")
        return
    ex = ccxt.binanceusdm({"proxies": PROXY, "timeout": 30000,
                           "enableRateLimit": True,
                           "options": {"defaultType": "future"}})
    # 基准组：同一批快照里 rank 4-20 的币。没有基准就分不清"信号差"和"大盘在跌"
    # （40min 样本里所有币一起跌，各窗口都会是负数，那是 beta 不是信号）
    top = df[(df.side == "top") & (df["rank"] <= TOP_N)]
    base = df[(df.side == "top") & (df["rank"] > TOP_N)]
    t_end = df.t.max()
    usable = top[top.t <= t_end - pd.Timedelta(minutes=fwd_min)]
    usable_base = base[base.t <= t_end - pd.Timedelta(minutes=fwd_min)]
    print(f"数据跨度 {(df.t.max()-df.t.min()).total_seconds()/60:.0f}min | "
          f"可算前瞻的信号点 {len(usable)} 个（需要 {fwd_min}min 未来）")
    if usable.empty:
        print(f"  ** 样本为 0：采集时长不足 {fwd_min}min。本节无输出，非结论。**")
        return
    if len(usable) < 30:
        print(f"  ** 警告：样本 {len(usable)} < 30，本节仅作管道验证，"
              f"数字不得引用为结论 **")

    klines = {}
    need = sorted(set(usable.symbol) | set(usable_base.symbol))
    for sym in need:
        pair = f"{sym[:-4]}/USDT:USDT" if sym.endswith("USDT") else sym
        try:
            since = int((df.t.min() - pd.Timedelta(minutes=5)).timestamp() * 1000)
            k = ex.fetch_ohlcv(pair, "1m", since=since, limit=500)
            if k:
                kdf = pd.DataFrame(k, columns=["ms", "o", "h", "l", "c", "v"])
                kdf["t"] = pd.to_datetime(kdf.ms, unit="ms", utc=True).dt.tz_convert(
                    df.t.dt.tz)
                klines[sym] = kdf
        except Exception as e:
            print(f"  [skip] {sym}: {str(e)[:60]}")

    def fwd(rows_df):
        out = []
        for _, r in rows_df.iterrows():
            k = klines.get(r.symbol)
            if k is None:
                continue
            # 同源铁律：信号 ts 之后第一根【已收盘】1m bar 的收盘价为入场价
            entry = k[k.t > r.t]
            exit_ = k[k.t > r.t + pd.Timedelta(minutes=fwd_min)]
            if entry.empty or exit_.empty:
                continue
            p0, p1 = entry.iloc[0].c, exit_.iloc[0].c
            out.append({"duration": r.duration, "symbol": r.symbol,
                        "gross": (p1 / p0 - 1) * 100})
        return pd.DataFrame(out)

    res, res_b = fwd(usable), fwd(usable_base)
    if res.empty:
        print("  无可用配对（K 线拉取失败或时间不覆盖）")
        return
    print(f"\n{'窗口':<6}{'n':>5}{'毛均值%':>10}{'扣费均值%':>11}{'扣费胜率':>9}"
          f"{'基准(rank4-20)%':>17}{'超额%':>9}")
    for d in DURATIONS:
        g = res[res.duration == d]
        if g.empty:
            continue
        gb = res_b[res_b.duration == d]
        bmean = gb.gross.mean() if not gb.empty else float("nan")
        print(f"{d:<6}{len(g):>5}{g.gross.mean():>10.3f}"
              f"{g.gross.mean()-FEE_PCT:>11.3f}"
              f"{100*((g.gross-FEE_PCT) > 0).mean():>8.0f}%"
              f"{bmean:>17.3f}{g.gross.mean()-bmean:>9.3f}")
    print("\n  超额 = top3 毛均值 - 同窗口 rank4-20 毛均值。只有超额显著为正，")
    print("  才说明 rank≤3 本身有信息；全列同号只反映这段时间大盘方向（beta）。")


def part5_median(df: pd.DataFrame) -> None:
    """复刻策略的 median_rank 判据，量化"中位数平滑贡献了降噪还是只贡献延迟"。

    策略逻辑（AI500V1DryRun.populate_indicators）:
      median_rank = 过去 RANK_WINDOW_SEC(30m) 内 rank 的中位数，
      样本覆盖 < MIN_WINDOW_SEC(15m) 则视为无效（不入场）；
      不在榜的采样点记 RANK_ABSENT=99。
      入场要求 median_rank <= 3。
    """
    RANK_WINDOW_SEC, MIN_WINDOW_SEC, RANK_ABSENT = 1800, 900, 99
    print(f"\n{'='*66}\n5. 中位数平滑 vs 当前 rank —— 降噪还是延迟\n{'='*66}")
    top = df[df.side == "top"]
    times = sorted(df.t.unique())
    print(f"\n{'窗口':<6}{'当前rank≤3触发':>16}{'中位数≤3触发':>15}"
          f"{'中位数额外延迟':>16}{'被平滑掉的':>12}")
    for d in DURATIONS:
        g = top[top.duration == d]
        # symbol -> {t: rank}
        ranks = {}
        for sym, sg in g.groupby("symbol"):
            ranks[sym] = dict(zip(sg.t, sg["rank"]))
        cur_hits, med_hits = set(), set()      # (symbol, t)
        first_cur, first_med = {}, {}
        for t in times:
            lo = t - pd.Timedelta(seconds=RANK_WINDOW_SEC)
            covered = [x for x in times if lo <= x <= t]
            if not covered or (t - covered[0]).total_seconds() < MIN_WINDOW_SEC:
                continue                        # 样本不足，策略视为不在前 3
            for sym in ranks:
                cur = ranks[sym].get(t, RANK_ABSENT)
                if cur <= TOP_N:
                    cur_hits.add((sym, t))
                    first_cur.setdefault(sym, t)
                series = [ranks[sym].get(x, RANK_ABSENT) for x in covered]
                med = pd.Series(series).median()
                if med <= TOP_N:
                    med_hits.add((sym, t))
                    first_med.setdefault(sym, t)
        both = set(first_cur) & set(first_med)
        lags = [(first_med[s] - first_cur[s]).total_seconds() / 60 for s in both]
        # 被平滑掉的 = 当前 rank 触发过、但中位数从未触发的币
        filtered = set(first_cur) - set(first_med)
        lag_txt = (f"{pd.Series(lags).median():+.1f}min(n={len(lags)})"
                   if lags else "-")
        print(f"{d:<6}{len(cur_hits):>16}{len(med_hits):>15}"
              f"{lag_txt:>20}{len(filtered):>12}")
    print("\n  「中位数额外延迟」= 同一个币首次满足中位数≤3 的时刻 - 首次当前rank≤3 的时刻")
    print("  「被平滑掉的」= 当前rank曾≤3、但中位数从未≤3 的币数（平滑真正过滤掉的）")
    print("  若延迟大而被平滑掉的少 → 平滑主要在贡献延迟，不在降噪。")
    print("  ** 延迟一列同样受左删失影响（观察窗开始时就已在榜的币，看不到它何时进的），")
    print("     采集时长 < 数小时前不可解读。「被平滑掉的」不受此影响，可读。**")


def score_status() -> None:
    print(f"\n{'='*66}\n附：score 采集状态\n{'='*66}")
    files = sorted(glob.glob(os.path.join(DATA_DIR, "score_*.csv")))
    if not files:
        print("  无 score 数据")
        return
    d = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    d["t"] = pd.to_datetime(d["fetched_at_cst"])
    span = (d.t.max() - d.t.min()).total_seconds() / 60
    print(f"采样 {len(d)} 行 | {d.symbol.nunique()} 个币 | 跨度 {span:.0f}min")
    g = d.groupby("symbol")["score"].agg(["min", "max", "nunique"])
    print(g.to_string())
    if (g["nunique"] == 1).all():
        print("\n  ** 全部 score 在观察期内零变化 → 无法计算任何 score→收益关系。**")
        print("  ** 与 TASK 121 实测一致（事件驱动、中位 ~30min 一变）。需更长采集。**")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fwd-min", type=int, default=30)
    ap.add_argument("--no-price", action="store_true")
    a = ap.parse_args()

    print(f"\n{'#'*66}\n#  AI500 多窗口对照分析 —— {BANNER}\n{'#'*66}")
    df = load()
    print(f"\n数据 {len(df)} 行 | {df.t.nunique()} 快照 | "
          f"{df.t.min():%Y-%m-%d %H:%M} ~ {df.t.max():%H:%M} CST "
          f"({(df.t.max()-df.t.min()).total_seconds()/60:.0f}min)")
    part1_overlap(df)
    part2_churn(df)
    part3_leadlag(df)
    part5_median(df)
    if not a.no_price:
        part4_forward(df, a.fwd_min)
    score_status()
    print(f"\n{'#'*66}\n#  {BANNER}\n{'#'*66}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
