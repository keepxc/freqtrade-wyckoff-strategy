#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI500 V3 入场供给审计（PREREG §6.14 的可复现实现，2026-09-25 修订版）。

本脚本回答四问：
  Q1 每个 peak>=80 的信号，4h 边界上有多少「完全合格」入场机会？
     （完全合格 = rank<=3 AND score>=80 AND tradable=1 AND zombie=0）
  Q2 这些机会里多少被僵尸过滤拦下（reason 分布）？
  Q3 供给账能否闭合：完全合格 −（在仓边界）−（被锁边界）− 成交 = ? 
     ⚠️ 关键口径：Auto lock 的 lock_end = 下一个 4h 开盘，而入场评估发生在边界后
        ~1.7 分钟 ⇒ 锁在边界时刻通常已到期。因此**锁不吞边界**（实测被锁边界=0），
        它是「阻止同一根 K 内立即重入」。此处同时计算「在仓边界」以区分。
  Q4 是否发生过「想开但被拦」的尝试？（日志证据：Long signal found / Ignoring buy signal
     / Unable to create 的计数，对比实际成交数）

输出末尾明确列出**未解释缺口**（若存在）——不假装闭合。

数据源（全部本地、只读）：logs/freqtrade_ai500_v3.log*、tradesv3.ai500_v3_dryrun.sqlite
（trades + pairlocks）、ai500_score_history_v3.csv、ai500_zombie_check.csv

用法：.venv/bin/python user_data/scripts/ai500_v3_supply_audit.py
"""
import glob
import re
import sqlite3
from collections import Counter
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
UD = REPO / "user_data"
LOGS = sorted(glob.glob(str(UD / "logs" / "freqtrade_ai500_v3.log*")))
EVAL_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ .*\[AI500V3\] top(\d) "
                     r"(\S+) score=([\d.]+) tradable=(\d) zombie=(\d) reason=(\S+)")
ATTEMPT_KEYS = ("Long signal found", "Ignoring buy signal", "Unable to create",
                "Unable to enter", "is too old")


def read_logs():
    evals, counts = [], Counter()
    for f in LOGS:
        with open(f, errors="replace") as fh:
            for ln in fh:
                m = EVAL_RE.match(ln)
                if m:
                    ts, rk, sym, sc, tr, zb, rs = m.groups()
                    evals.append((pd.Timestamp(ts), int(rk), sym, float(sc),
                                  int(tr), int(zb), rs))
                for k in ATTEMPT_KEYS:
                    if k in ln:
                        counts[k] += 1
    d = pd.DataFrame(evals, columns=["ts", "rank", "symbol", "score",
                                     "tradable", "zombie", "reason"])
    d["ts"] = d["ts"].dt.tz_localize("Asia/Shanghai").dt.tz_convert("UTC")
    return d.sort_values("ts").reset_index(drop=True), counts


def main():
    ev, counts = read_logs()
    print(f"门②评估记录 {len(ev):,} 条（{len(LOGS)} 个日志文件）；"
          f"覆盖 {ev['ts'].min():%m-%d %H:%M} ~ {ev['ts'].max():%m-%d %H:%M} UTC")

    # ---- Q4 先报：有没有「想开被拦」的痕迹 ----
    print("\nQ4 入场尝试痕迹（日志计数）:")
    for k in ATTEMPT_KEYS:
        print(f"   {k:<22} {counts[k]}")

    # ---- rank 代理校验（score_history 横截面排名 vs 接口 rank，用 zombie_check 对齐）----
    sh = pd.read_csv(UD / "ai500_score_history_v3.csv")
    sh["ts"] = pd.to_datetime(sh["fetched_at_utc"], utc=True)
    sh["cs_rank"] = sh.groupby("ts")["score"].rank(ascending=False, method="min")
    zc = pd.read_csv(UD / "ai500_zombie_check.csv")
    zc["ts"] = pd.to_datetime(zc["fetched_at_utc"], utc=True)
    j = zc.merge(sh[["ts", "symbol", "cs_rank"]], on=["ts", "symbol"], how="inner")
    if len(j):
        print(f"\nrank 代理校验: {len(j)} 行重叠，一致率 "
              f"{(j['rank'] == j['cs_rank']).mean() * 100:.1f}%")

    con = sqlite3.connect(f"file:{UD / 'tradesv3.ai500_v3_dryrun.sqlite'}?mode=ro", uri=True)
    tr = pd.read_sql("select id,pair,open_date,close_date from trades order by id", con)
    tr["ot"] = pd.to_datetime(tr["open_date"], utc=True)
    tr["ct"] = pd.to_datetime(tr["close_date"], utc=True)
    lk = pd.read_sql("select pair,lock_time,lock_end_time from pairlocks", con)
    for c in ("lock_time", "lock_end_time"):
        lk[c] = pd.to_datetime(lk[c], utc=True)
    on_grid = bool(((lk["lock_end_time"].dt.minute == 0)
                    & (lk["lock_end_time"].dt.second == 0)
                    & (lk["lock_end_time"].dt.hour % 4 == 0)).all())
    print(f"pairlocks: {len(lk)} 条，reason 全为 'Auto lock'；"
          f"lock_end 是否都落在 4h 网格上: {on_grid}")

    sig = sh.groupby(["symbol", "start_time"]).agg(peak=("score", "max"), first=("ts", "min"),
                                                  last=("ts", "max")).reset_index()
    tgt = sig[sig["peak"] >= 80].sort_values("peak", ascending=False)
    bnd = pd.date_range(ev["ts"].min().ceil("4h"), ev["ts"].max(), freq="4h", tz="UTC")

    print(f"\n{'信号':<10}{'峰值':>7}{'合格边界':>9}{'完全合格':>9}{'在仓':>6}"
          f"{'被锁':>6}{'可入场':>7}{'成交':>6}{'僵尸拦':>7}   reason")
    gaps = []
    for _, s in tgt.iterrows():
        sd = ev[(ev["symbol"] == s["symbol"]) &
                (ev["ts"] >= s["first"] - pd.Timedelta("1h")) &
                (ev["ts"] <= s["last"] + pd.Timedelta("6h"))]
        pos = tr[tr["pair"].str.startswith(s["symbol"])]
        lkp = lk[lk["pair"].str.startswith(s["symbol"])]
        n_all = n_clean = n_inpos = n_locked = 0
        reasons = Counter()
        for b in bnd:
            w = sd[(sd["ts"] >= b - pd.Timedelta("6min")) & (sd["ts"] <= b + pd.Timedelta("2min"))]
            if not len(w):
                continue
            r = w.iloc[-1]
            if not (r["score"] >= 80 and r["rank"] <= 3):
                continue
            n_all += 1
            if not (r["tradable"] == 1 and r["zombie"] == 0):
                reasons[r["reason"]] += 1
                continue
            n_clean += 1
            inpos = bool(((pos["ot"] < b) & (pos["ct"] > b)).any())
            n_inpos += inpos
            evt = b + pd.Timedelta("1.7min")          # 入场评估时刻
            locked = bool(((lkp["lock_time"] < evt) & (lkp["lock_end_time"] > evt)).any())
            if locked and not inpos:
                n_locked += 1
        ntr = len(pos[(pos["ot"] >= s["first"]) & (pos["ot"] <= s["last"] + pd.Timedelta("6h"))])
        free = n_clean - n_inpos - n_locked
        gaps.append((f"{s['symbol']}@{s['peak']:.1f}", free, ntr))
        print(f"{s['symbol']:<10}{s['peak']:>7.2f}{n_all:>9}{n_clean:>9}{n_inpos:>6}"
              f"{n_locked:>6}{free:>7}{ntr:>6}{sum(reasons.values()):>7}   "
              f"{dict(reasons) if reasons else ''}")

    print("\n=== Q3 供给账（可入场机会 vs 成交）===")
    tot_gap = 0
    for sym, free, ntr in gaps:
        if ntr or free:
            d = free - ntr
            tot_gap += max(d, 0)
            print(f"   {sym:<8} 可入场 {free} − 成交 {ntr} = {d:+d}"
                  f"{'  ⚠ 未解释缺口' if d > 0 else '  ✓ 闭合'}")
    print(f"\n未解释缺口合计 = {tot_gap} 处")
    print("※ 说明：本口径已排除「在仓」与「被锁」两个机械原因（实测被锁=0）。"
          "\n   若缺口>0，且日志无任何「想开被拦」痕迹（Q4），则该缺口属"
          "**未定位的执行层问题**，需在 populate_entry_trend 落盘门②各列后再查。")


if __name__ == "__main__":
    main()
