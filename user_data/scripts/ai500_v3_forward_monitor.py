# -*- coding: utf-8 -*-
"""P8 V3 Frozen Forward OOS Monitoring（2026-09-25 修订）。

可重复运行的监控器：每次 bot 产生新交易 / 新 score 快照后，把最新的
sqlite + score_history + zombie_check 放到 user_data/，重跑本脚本即生成
最新前向 OOS 报告。

原则：
  - V3 frozen，不修改任何策略参数。
  - 只用 entry_time 之前可观测数据回填（防 lookahead）。
  - 不做 cooldown 参数搜索。
  - P9 门槛**只数 OOS 段**（OOS_START_UTC 之后开仓的已平仓交易）。

2026-09-25 修订（P0-P8 独立复核的结论，见 AI500_NEXT_PHASE_RESEARCH.md §9）：
  1. OOS 起点 = 假设冻结时刻。此前 11 笔是「假设生成样本」，不计入 P9 门槛。
  2. episode 主口径 = 接口信号标识 (symbol, start_time)。旧的 2h gap 口径会把
     全局采集断档误判为信号断开（BR_2/3/4 实为同一信号），只作为辅助列保留，
     并改为「断档感知」：gap 期间全榜也没有快照时不判断开。
  3. 未平仓交易单列，不进 n / 胜率 / 门槛（旧版把 NaN PnL 算进 n、当作亏损）。
  4. score_change 拆成两个名字：score_delta_5m（相邻快照差）与
     score_vs_prev_trade（相对同 symbol 上一笔入场 score）。
  5. summary 全部由数据动态生成（旧版 "Confirmed" 段是写死的文本）。
  6. 读入策略 _append_csv 表头轮转产生的 .bak 分片，防历史静默丢失。
"""
import glob
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

UD = "user_data"
GAP_THRESHOLD = pd.Timedelta(hours=2)

# ---- 冻结常量（改动须另立 commit 说明，不得随结果调整） ----
# OOS 起点 = 复核所用数据的导出时刻（sqlite/score_history mtime 2026-09-24 13:33 CST）。
# 此前的交易全部参与了 H1-H5 的提出，此后开仓的交易当时无人见过。
OOS_START_UTC = pd.Timestamp("2026-09-24 05:33:00", tz="UTC")
# 规则分段（PREREG §6.4 副作用条款：分段之间不得混池）
SEGMENTS = [
    ("S0_pre_gate3C", pd.Timestamp("2026-09-18 07:15:28", tz="UTC")),   # e43d7cb 方案C
    ("S1_gate3C_old_exit", pd.Timestamp("2026-09-19 00:00:00", tz="UTC")),  # GATE3_C_RESTART_UTC
    ("S2_restart_insample", OOS_START_UTC),
    ("S3_oos", None),
]
# P9 门槛（2026-09-24 冻结；episode 口径改为 signal_id）
P9_MIN_REENTRY = 30
P9_MIN_SYMBOLS = 10
P9_MIN_SIGNALS = 5
P9_MAX_SYMBOL_SHARE = 50.0


def segment_of(t):
    for name, end in SEGMENTS:
        if end is None or t < end:
            return name
    return SEGMENTS[-1][0]


def read_csv_with_shards(path):
    """读主文件 + 表头轮转产生的 .bak 分片（同列名才合并），按全行去重。"""
    frames = [pd.read_csv(path)]
    shards = sorted(glob.glob(f"{path}.*.bak"))
    for p in shards:
        d = pd.read_csv(p)
        common = [c for c in frames[0].columns if c in d.columns]
        if "fetched_at_utc" not in common or "symbol" not in common:
            print(f"⚠️ 分片 {p} 缺关键列，跳过")
            continue
        frames.append(d[common])
    if shards:
        print(f"⚠️ 发现 {len(shards)} 个 .bak 分片并已合并: {shards}")
    return pd.concat(frames, ignore_index=True).drop_duplicates()


# ================================================================ 1. 加载
con = sqlite3.connect(f"{UD}/tradesv3.ai500_v3_dryrun.sqlite")
trades = pd.read_sql_query("SELECT * FROM trades ORDER BY open_date", con)
con.close()

sh = read_csv_with_shards(f"{UD}/ai500_score_history_v3.csv")
zc = read_csv_with_shards(f"{UD}/ai500_zombie_check.csv")

trades["entry_time"] = pd.to_datetime(trades["open_date"], format="ISO8601").dt.tz_localize("UTC")
trades["exit_time"] = pd.to_datetime(trades["close_date"], format="ISO8601").dt.tz_localize("UTC")
trades["symbol"] = trades["pair"].str.split("/").str[0]
trades["is_closed"] = trades["is_open"] == 0

sh["t"] = pd.to_datetime(sh["fetched_at_utc"]).dt.tz_convert("UTC")
zc["t"] = pd.to_datetime(zc["fetched_at_utc"]).dt.tz_convert("UTC")
sh = sh.sort_values("t").reset_index(drop=True)
zc = zc.sort_values("t").reset_index(drop=True)

data_start, data_end = sh["t"].min(), sh["t"].max()
warnings = []

# ================================================================ 2. episode
# 主口径：接口信号标识
sh["signal_id"] = np.where(
    sh["start_time"].notna(),
    sh["symbol"] + "@" + sh["start_time"].astype("Int64").astype(str),
    None)

# 辅助口径：断档感知 2h gap（gap 期间全榜有快照才判断开）
all_snaps = np.sort(sh["t"].dt.tz_localize(None).unique())   # naive UTC，与下方 ts 同口径
sh["episode_2h_aware"] = None
for symbol, sub in sh.groupby("symbol"):
    ts = sub["t"].dt.tz_localize(None).values
    new = np.zeros(len(ts), dtype=bool)
    new[0] = True
    for i in range(1, len(ts)):
        if ts[i] - ts[i - 1] > GAP_THRESHOLD:
            inside = np.searchsorted(all_snaps, ts[i], "left") - np.searchsorted(all_snaps, ts[i - 1], "right")
            new[i] = inside > 0          # 全榜在采集而该币缺席 → 真断开
    sh.loc[sub.index, "episode_2h_aware"] = symbol + "_" + pd.Series(np.cumsum(new), index=sub.index).astype(str)


def last_before(df, symbol, t):
    sub = df[(df["symbol"] == symbol) & (df["t"] <= t)]
    return sub.iloc[-1] if len(sub) else None


def nth_last_before(df, symbol, t, n):
    sub = df[(df["symbol"] == symbol) & (df["t"] <= t)]
    return sub.iloc[-(n + 1)] if len(sub) >= n + 1 else None


# ================================================================ 3. ledger
rows = []
for _, tr in trades.iterrows():
    sym, entry_t = tr["symbol"], tr["entry_time"]
    last = last_before(sh, sym, entry_t)
    prev_snap = nth_last_before(sh, sym, entry_t, 1)
    if last is None:
        warnings.append(f"trade #{tr['id']} {sym} 入场前无 score 快照（数据覆盖 {data_start} 起）")
    if last is not None and (entry_t - last["t"]) > pd.Timedelta(minutes=30):
        warnings.append(f"trade #{tr['id']} {sym} 入场前最近快照已过 {(entry_t - last['t'])}，回填字段可能陈旧")

    signal_id = last["signal_id"] if last is not None else None
    if signal_id is None and last is not None:
        signal_id = "gap:" + last["episode_2h_aware"]
    ep2h = last["episode_2h_aware"] if last is not None else None

    score_peak = ep_high = None
    if last is not None:
        key = "signal_id" if last["signal_id"] is not None else "episode_2h_aware"
        hist = sh[(sh[key] == last[key]) & (sh["t"] <= entry_t)]
        score_peak = hist["score"].max()
        ep_high = hist["price"].max()

    rank_last = last_before(zc, sym, entry_t)
    sig_start = signal_age_h = prior_gain = None
    if last is not None and pd.notna(last.get("start_time")):
        sig_start = pd.to_datetime(int(last["start_time"]), unit="s", utc=True)
        signal_age_h = (entry_t - sig_start).total_seconds() / 3600
        prior_gain = last["change_pct_value"]

    score_at_entry = last["score"] if last is not None else None
    rows.append({
        "trade_id": tr["id"],
        "symbol": sym,
        "segment": segment_of(entry_t),
        "is_oos": entry_t >= OOS_START_UTC,
        "is_closed": bool(tr["is_closed"]),
        "entry_time": entry_t,
        "entry_price": tr["open_rate"],
        "exit_time": tr["exit_time"],
        "exit_price": tr["close_rate"],
        "exit_reason": tr["exit_reason"],
        "trade_pnl_pct": tr["close_profit"] * 100 if pd.notna(tr["close_profit"]) else None,
        "trade_pnl_usdt": tr["close_profit_abs"],
        "signal_id": signal_id,
        "episode_2h_aware": ep2h,
        "signal_start_time": sig_start,
        "signal_age_hours": signal_age_h,
        "prior_gain_pct": prior_gain,
        "score_at_entry": score_at_entry,
        "score_delta_5m": (score_at_entry - prev_snap["score"]) if (last is not None and prev_snap is not None) else None,
        "score_peak_so_far": score_peak,
        "score_over_peak": (score_at_entry - score_peak) if score_peak is not None else None,
        "rank_at_entry": rank_last["rank"] if rank_last is not None else None,
        "drawdown_from_signal_high_pct": ((tr["open_rate"] - ep_high) / ep_high * 100) if ep_high else None,
    })

ledger = pd.DataFrame(rows).sort_values("entry_time").reset_index(drop=True)

# 同 symbol 上一笔 / 同 signal 内序号
ledger["previous_trade_id"] = ledger.groupby("symbol")["trade_id"].shift(1)
ledger["symbol_reentry_flag"] = ledger["previous_trade_id"].notna()
for col in ["exit_reason", "exit_time", "entry_price", "exit_price", "score_at_entry", "rank_at_entry", "signal_id"]:
    ledger[f"previous_{col}"] = ledger.groupby("symbol")[col].shift(1)
ledger["reentry_interval_hours"] = (ledger["entry_time"] - ledger["previous_exit_time"]).dt.total_seconds() / 3600
# Auto lock 锁到出场后下一根 4h 开盘 → 最早可在「出场所在 K 的下一根」再入场。
# bars_skipped = 0 表示在最早可入场的那根 K 就再入场了（冷却期应以此为单位，而非小时）。
ledger["reentry_bars_skipped_4h"] = (
    (ledger["entry_time"].dt.floor("4h") - ledger["previous_exit_time"].dt.floor("4h")) / pd.Timedelta(hours=4) - 1
).where(ledger["symbol_reentry_flag"])
ledger["same_signal_as_previous"] = ledger["symbol_reentry_flag"] & (ledger["signal_id"] == ledger["previous_signal_id"])
ledger["price_vs_previous_entry_pct"] = (ledger["entry_price"] / ledger["previous_entry_price"] - 1) * 100
ledger["price_vs_previous_exit_pct"] = (ledger["entry_price"] / ledger["previous_exit_price"] - 1) * 100
ledger["score_vs_prev_trade"] = ledger["score_at_entry"] - ledger["previous_score_at_entry"]
ledger["rank_vs_prev_trade"] = ledger["rank_at_entry"] - ledger["previous_rank_at_entry"]
ledger["signal_trade_index"] = ledger.groupby("signal_id").cumcount() + 1
ledger["signal_cum_pnl_usdt"] = ledger.groupby("signal_id")["trade_pnl_usdt"].transform(lambda s: s.fillna(0).cumsum())

# ================================================================ 4. 统计（只用已平仓）
def idx_grp(i):
    if pd.isna(i):
        return "unknown"
    return {1: "first", 2: "second", 3: "third"}.get(int(i), "fourth+")


ledger["idx_grp"] = ledger["signal_trade_index"].apply(idx_grp)
ibins = [0, 1, 3, 6, 12, 24, np.inf]
ilab = ["0-1h", "1-3h", "3-6h", "6-12h", "12-24h", "24h+"]
ledger["interval_grp"] = pd.cut(ledger["reentry_interval_hours"], bins=ibins, labels=ilab, right=False)
ledger["recovery"] = np.where(ledger["price_vs_previous_entry_pct"] >= 0, "price_recovered", "price_not_recovered")
ledger.loc[~ledger["symbol_reentry_flag"], "recovery"] = None


def agg(df, by):
    if df.empty:
        return pd.DataFrame()
    return df.groupby(by, observed=True).agg(
        n=("trade_id", "count"),
        wins=("trade_pnl_pct", lambda s: int((s > 0).sum())),
        mean_pnl_pct=("trade_pnl_pct", "mean"),
        median_pnl_pct=("trade_pnl_pct", "median"),
        sum_pnl_usdt=("trade_pnl_usdt", "sum"),
    ).round(2)


def coverage(df):
    """df = 某段已平仓交易。返回 re-entry 覆盖度指标。"""
    re = df[df["symbol_reentry_flag"]]
    n_re = len(re)
    conc = re.groupby("symbol")["trade_id"].count().sort_values(ascending=False)
    return {
        "trades": len(df),
        "reentry": n_re,
        "reentry_symbols": re["symbol"].nunique(),
        "reentry_signals": re["signal_id"].nunique(),
        "largest_symbol": conc.index[0] if len(conc) else None,
        "largest_share": float(conc.iloc[0] / n_re * 100) if n_re else 0.0,
        "largest_signal_trades": int(df.groupby("signal_id")["trade_id"].count().max()) if len(df) else 0,
        "sum_pnl_pct": round(df["trade_pnl_pct"].sum(), 2),
        "sum_pnl_usdt": round(df["trade_pnl_usdt"].sum(), 2),
    }


closed = ledger[ledger["is_closed"]]
open_tr = ledger[~ledger["is_closed"]]
oos = closed[closed["is_oos"]]
cov_oos = coverage(oos)
cov_seg = {name: coverage(closed[closed["segment"] == name]) for name, _ in SEGMENTS}

gates = {
    f"re-entry ≥ {P9_MIN_REENTRY}": cov_oos["reentry"] >= P9_MIN_REENTRY,
    f"unique symbols ≥ {P9_MIN_SYMBOLS}": cov_oos["reentry_symbols"] >= P9_MIN_SYMBOLS,
    f"unique signals ≥ {P9_MIN_SIGNALS}": cov_oos["reentry_signals"] >= P9_MIN_SIGNALS,
    f"单一 symbol 占比 < {P9_MAX_SYMBOL_SHARE:.0f}%": cov_oos["reentry"] > 0 and cov_oos["largest_share"] < P9_MAX_SYMBOL_SHARE,
}
status = "READY_FOR_P9" if all(gates.values()) else "INSUFFICIENT_OOS_SAMPLE"

# PREREG GATE3-C 主判据（自 GATE3_C_RESTART_UTC 起、按平仓时间排序的前 30 笔）
g3 = closed[closed["entry_time"] >= SEGMENTS[1][1]].sort_values("exit_time").head(30)
g3_n, g3_sum = len(g3), round(g3["trade_pnl_pct"].sum(), 2)
g3_state = ("EARLY_STOP(≤-20%)" if g3_sum <= -20 else
            ("DECIDE" if g3_n >= 30 else "ACCUMULATING"))

# ================================================================ 5. 输出
ledger.to_csv("AI500_V3_FORWARD_REENTRY_LEDGER.csv", index=False)

daily = closed.copy()
daily["day_utc"] = daily["exit_time"].dt.date
daily = daily.groupby(["day_utc", "segment"]).agg(
    trades=("trade_id", "count"),
    reentry=("symbol_reentry_flag", "sum"),
    pnl_pct=("trade_pnl_pct", "sum"),
    pnl_usdt=("trade_pnl_usdt", "sum"),
    wins=("trade_pnl_pct", lambda s: int((s > 0).sum())),
).round(4)
daily.to_csv("AI500_V3_FORWARD_REENTRY_DAILY.csv")


def md(df):
    return df.to_markdown() if len(df) else "_（无样本）_"


oos_re = oos[oos["symbol_reentry_flag"]]
L = []
L.append("# AI500 V3 Forward OOS Re-entry Summary\n")
L.append(f"**生成时间**: {pd.Timestamp.now(tz='UTC').isoformat()}  ")
L.append(f"**数据范围**: score {data_start} → {data_end} | 最新平仓 {closed['exit_time'].max()}  ")
L.append(f"**OOS 起点（冻结）**: {OOS_START_UTC} —— 此前交易为假设生成样本，不计入 P9 门槛  ")
L.append(f"**episode 主口径**: 接口信号标识 (symbol, start_time)\n")
if warnings:
    L.append("## ⚠️ 数据告警\n")
    L.extend(f"- {w}" for w in warnings)
    L.append("")
L.append("## A. 分段累计（已平仓，段间不得混池）\n")
seg_df = pd.DataFrame(cov_seg).T
L.append(md(seg_df) + "\n")
L.append(f"- 未平仓交易: **{len(open_tr)}**（不计入任何统计）" +
         (f" — {', '.join(f'#{int(i)} {s}' for i, s in zip(open_tr['trade_id'], open_tr['symbol']))}" if len(open_tr) else "") + "\n")
L.append("## B. PREREG GATE3-C 主判据（自 2026-09-19 起前 30 笔已平仓，含手续费算术和）\n")
L.append(f"- 进度 **{g3_n}/30**，累计 **{g3_sum:+.2f}%**，状态 **{g3_state}**（提前中止闸 ≤ -20%）\n")
L.append("## C. P9 门槛（仅 OOS 段）\n")
for k, v in gates.items():
    L.append(f"- {'✅' if v else '❌'} {k}")
L.append(f"\n- 当前: re-entry={cov_oos['reentry']}, symbols={cov_oos['reentry_symbols']}, "
         f"signals={cov_oos['reentry_signals']}, 最大占比={cov_oos['largest_share']:.0f}%"
         f"（{cov_oos['largest_symbol']}）")
L.append(f"- 判定: **{status}**\n")
L.append("## D. OOS 段分层（描述性，n<30 不作推断）\n")
L.append("### D1. Re-entry interval（小时；注意 Auto lock 使其≈距下一根 4h 开盘的时间）\n\n" + md(agg(oos_re, "interval_grp")) + "\n")
L.append("### D2. Previous exit reason\n\n" + md(agg(oos_re, "previous_exit_reason")) + "\n")
L.append("### D3. Price recovery（相对上一笔入场价，边界 0%）\n\n" + md(agg(oos_re, "recovery")) + "\n")
L.append("### D4. Signal 内交易序号\n\n" + md(agg(oos, "idx_grp")) + "\n")
L.append("### D5. Symbol 集中度\n\n" + md(agg(oos_re, "symbol")) + "\n")
L.append("---\n")
L.append("## 状态\n\n```\nV3 FROZEN\nNO PARAMETER CHANGE\nNO COOLDOWN IMPLEMENTED\nOOS ACCUMULATION ONLY\n```\n")
L.append("> 本文件全部由脚本从数据生成；结论分级见 AI500_NEXT_PHASE_RESEARCH.md，不在此处手写。\n")

with open("AI500_V3_FORWARD_REENTRY_SUMMARY.md", "w", encoding="utf-8") as f:
    f.write("\n".join(L))

print("=" * 78)
print("P8 V3 Frozen Forward OOS Monitoring")
print("=" * 78)
for w in warnings:
    print("⚠️", w)
print(seg_df.to_string())
print(f"未平仓: {len(open_tr)}")
print(f"GATE3-C: {g3_n}/30, {g3_sum:+.2f}%, {g3_state}")
print(f"P9: {status}  {cov_oos}")
print("\n已写出: FORWARD_REENTRY_LEDGER.csv / FORWARD_REENTRY_DAILY.csv / FORWARD_REENTRY_SUMMARY.md")
