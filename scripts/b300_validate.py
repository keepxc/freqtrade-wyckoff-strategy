#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
【探索性——非结论，禁止引用为证据】

B3.0 主流币结构策略 —— 快照离线校验
====================================
回答操作手册第三节的"上实盘前必须验证的三件事"，全部离线、无需再调 VergeX 接口：

  1. 信号时延：leaderboard 的 factorAsOf / 单币的 markPriceAsOf 距当期 4h 收盘多久
  2. 正期望：触发入场门槛的币，随后 24h / 48h 收益与胜率（绝对 + 市场中性双口径）
  3. 磁吸位：magnet 触及率与触及后 48h 收盘是否站上 magnet（决定止盈 2 留不留）

数据源
------
  - b300_snapshots.jsonl —— bot 机器上 b300_collector.py 积累的全量快照（每 4h 一行）
  - freqtrade 标准 1h K 线 —— {klines_dir}/{SYM}_USDT_USDT-1h-futures.feather
    （4h K 线由 1h 聚合，保证全池口径一致，不混用个别币自带的 4h 文件）

同源铁律（与 scripts/ai500_gate_calibration.py 一致）
--------------------------------------------------
  - 决策时点 T = 快照 ts；入场 K 线 = 快照时刻之前**最后一根已收盘**的 4h K
  - 入场价 = 该 4h K 的 close（已收盘，不用未来数据）
  - "4h 收阳"判定 = 该 4h K 的 close > open（这是入场门槛的一票否决项）
  - 24h/48h 前向收益 = 入场价 → 未来第 6 / 12 根 4h K 的 close
  - 任何一处都不取 T 当根或之后的数据

口径说明（重要）
--------------
  - 绝对收益：回答"这个信号能不能择时赚钱"——实盘单边做多直接可得的收益
  - 市场中性：减同窗口主流池（有 K 线数据的币）横截面均值——回答"选出来的币
    是否跑赢同侪"。这是 B3.0 的主判据口径（信号本质是横截面选币，中性口径隔离
    大盘 beta，且 MDE 更小、功效更高），但实盘单边做多拿不到中性收益（需空头
    对冲腿）。两者别混，主判据取中性、绝对收益仅作实盘可实现性参考。
  - 本脚本是离线统计工具，不写任何 ✅/❌/⭕ 结论——样本攒够（手册要求 ≥2 周、
    ≥30 次触发）且判据冻结后，另走确认性检验流程。

用法
----
  python scripts/b300_validate.py
  python scripts/b300_validate.py --snapshots D:/bot/b300_snapshots.jsonl \
      --klines-dir D:/bot/user_data/data/binance/futures --out-csv b300_events.csv
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SNAP = os.path.join(_ROOT, "scripts", "b300_snapshots.jsonl")
DEFAULT_KLDIR = os.path.join(_ROOT, "user_data", "data", "binance", "futures")

# 主流池（与 b300_collector.py 的 MAJORS 保持一致）
MAJORS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "LINK", "AAVE", "HYPE"]

# B3.0 入场门槛（与 b300_collector.py 一致）
MIN_DIRECTION_SCORE = 4    # leaderboard 满票
MIN_COMPOSITE_Z = 0.5      # 单币 Z 门槛
MIN_FACTOR_PCTL = 70       # cgo / wp 百分位门槛
REQUIRED_VOTES = 2         # 三条满足两条

H4 = pd.Timedelta("4h")


# --------------------------------------------------------------------------- #
# 数据加载
# --------------------------------------------------------------------------- #
def load_snapshots(path: str) -> list:
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def load_4h(symbol: str, klines_dir: str) -> pd.DataFrame:
    """读 1h feather，聚合成 4h。无该币数据返回空 DataFrame。"""
    fp = os.path.join(klines_dir, f"{symbol}_USDT_USDT-1h-futures.feather")
    if not os.path.exists(fp):
        return pd.DataFrame()
    df = pd.read_feather(fp)
    if "date" not in df.columns:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"], utc=True)
    df = df.sort_values("date").set_index("date")
    o4 = df.resample("4h").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna(subset=["close"])
    return o4


# --------------------------------------------------------------------------- #
# 门槛复现（与 b300_collector.check_entry 同口径）
# --------------------------------------------------------------------------- #
def gate_conds(lb_item: dict, sig: dict) -> dict:
    conds = {
        "direction_score": (lb_item.get("directionScore", 0) or 0) >= MIN_DIRECTION_SCORE
                           and (lb_item.get("bearishCount", 0) or 0) == 0,
        "composite_z": (sig.get("compositeZ") is not None
                        and sig["compositeZ"] >= MIN_COMPOSITE_Z),
        "cost_structure": any(
            (p or 0) >= MIN_FACTOR_PCTL
            for p in (sig.get("cgo_pctl"), sig.get("wp_pctl"))
        ),
    }
    passed = sum(conds.values())
    ok = passed >= REQUIRED_VOTES and sig.get("bias") == "bullish"
    return {"ok": ok, "passed": passed, **conds}


# --------------------------------------------------------------------------- #
# 事件抽取
# --------------------------------------------------------------------------- #
def extract_events(snapshots: list) -> pd.DataFrame:
    """把每个快照 × 每个主流候选展开成一行事件。"""
    ev = []
    for sn in snapshots:
        ts = pd.to_datetime(sn["ts"])
        if ts.tzinfo is None:
            ts = ts.tz_localize("Asia/Shanghai")  # b300_collector 存 +08:00
        ts = ts.tz_convert("UTC")
        lb = sn.get("leaderboard", [])
        sigs = sn.get("signals", {})
        lb_by_sym = {}
        for it in lb:
            sym = it.get("symbol", "")
            if isinstance(sym, str):
                lb_by_sym[sym] = it

        for it in lb:
            sym = it.get("symbol", "")
            if not isinstance(sym, str) or sym not in MAJORS:
                continue
            if it.get("market", {}).get("marketType") != "core_perp":
                continue
            sig = sigs.get(sym) or {}
            g = gate_conds(it, sig)
            ev.append({
                "snap_ts": ts,
                "symbol": sym,
                "bias": sig.get("bias", it.get("bias")),
                "directionScore": it.get("directionScore"),
                "bullishCount": it.get("bullishCount"),
                "bearishCount": it.get("bearishCount"),
                "compositeZ": sig.get("compositeZ"),
                "cgo_pctl": sig.get("cgo_pctl"),
                "wp_pctl": sig.get("wp_pctl"),
                "magnet": sig.get("magnet"),
                "support": sig.get("support"),
                "markPriceAsOf": sig.get("markPriceAsOf"),
                "factorAsOf_lb": it.get("factorAsOf"),
                "passed": g["passed"],
                "gate_ok": g["ok"],
                "cond_direction_score": g["direction_score"],
                "cond_composite_z": g["composite_z"],
                "cond_cost_structure": g["cost_structure"],
            })
    return pd.DataFrame(ev)


# --------------------------------------------------------------------------- #
# 前向收益 + 4h 收阳 + magnet
# --------------------------------------------------------------------------- #
def attach_forward(ev: pd.DataFrame, klines_dir: str,
                   horizons_h=(24, 48)) -> pd.DataFrame:
    """给每行事件挂上：4h 收阳、绝对/中性前向收益、magnet 触及。"""
    cache = {sym: load_4h(sym, klines_dir) for sym in MAJORS}

    # 每个快照时点的主流池前向收益（用于中性口径）
    def pool_rets(ts):
        out = {}
        for sym in MAJORS:
            d = cache[sym]
            if d.empty:
                continue
            entry = d[d.index <= ts]
            after = d[d.index > ts]
            if entry.empty or len(after) < 12:
                continue
            base = float(entry.iloc[-1]["close"])
            out[sym] = {h: float(after.iloc[n - 1]["close"]) / base - 1
                        for h, n in [(24, 6), (48, 12)]}
        return out

    pool_cache = {}
    rows = []
    for _, r in ev.iterrows():
        sym, ts = r["symbol"], r["snap_ts"]
        d = cache.get(sym, pd.DataFrame())
        rec = dict(r)
        # 所有输出列先给默认值，保证后续切片不因缺列报错
        rec.update({
            "has_data": False,
            "note": "",
            "entry_bar_close": np.nan,
            "candle_bull": False,
            "abs_ret_24h": np.nan,
            "abs_ret_48h": np.nan,
            "mn_ret_24h": np.nan,
            "mn_ret_48h": np.nan,
            "magnet_touch": np.nan,
            "close48_above_magnet": np.nan,
        })
        if d.empty:
            rec["note"] = f"{sym} 无 1h K 线数据"
            rows.append(rec)
            continue
        entry = d[d.index <= ts]
        after = d[d.index > ts]
        if entry.empty:
            rec["note"] = "K线不覆盖该快照时刻"
            rows.append(rec)
            continue
        if len(after) < 12:
            rec["note"] = (f"K线止于 {d.index.max():%Y-%m-%d %H:%M}UTC，"
                           f"快照后不足 48h")
            rows.append(rec)
            continue
        entry_bar = entry.iloc[-1]
        base = float(entry_bar["close"])
        rec["has_data"] = True
        rec["entry_bar_close"] = base
        rec["candle_bull"] = bool(entry_bar["close"] > entry_bar["open"])

        # 绝对前向收益
        for h, n in [(24, 6), (48, 12)]:
            rec[f"abs_ret_{h}h"] = float(after.iloc[n - 1]["close"]) / base - 1

        # 市场中性前向收益：减同窗口主流池（有数据币，不含自己）均值
        if ts not in pool_cache:
            pool_cache[ts] = pool_rets(ts)
        prets = pool_cache[ts]
        for h in (24, 48):
            peer = [prets[s][h] for s in prets if s != sym and h in prets[s]]
            if peer:
                rec[f"mn_ret_{h}h"] = rec[f"abs_ret_{h}h"] - float(np.mean(peer))

        # magnet 触及：入场后 12 根 4h 的最高价是否触及 magnet
        if rec.get("magnet") is not None and not pd.isna(rec["magnet"]):
            fut_high = float(after.iloc[:12]["high"].max())
            rec["magnet_touch"] = bool(fut_high >= rec["magnet"])
            rec["close48_above_magnet"] = bool(float(after.iloc[11]["close"]) >= rec["magnet"])

        rows.append(rec)
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# 汇总
# --------------------------------------------------------------------------- #
def summarize(ev: pd.DataFrame, horizons_h=(24, 48)):
    print("\n" + "=" * 78)
    print("B3.0 快照校验汇总（探索性——非结论，禁止引用为证据）")
    print("=" * 78)
    print(f"快照数: {ev['snap_ts'].nunique()}   候选事件: {len(ev)}   "
          f"有K线数据: {int(ev['has_data'].sum())}")

    slices = {
        "① 全部主流候选": ev[ev["has_data"]],
        "② 满票0熊票(directionScore≥4)": ev[ev["has_data"] & (ev["directionScore"] >= 4)
                                                    & (ev["bearishCount"] == 0)],
        "③ 过门槛(3满2+bias=bullish)": ev[ev["has_data"] & ev["gate_ok"]],
        "④ 过门槛且4h收阳(=实盘入场)": ev[ev["has_data"] & ev["gate_ok"] & ev["candle_bull"]],
    }

    for name, sub in slices.items():
        n = len(sub)
        print(f"\n--- {name}  (n={n}) ---")
        if n == 0:
            print("    (无样本)")
            continue
        for h in horizons_h:
            a = sub[f"abs_ret_{h}h"].dropna()
            m = sub[f"mn_ret_{h}h"].dropna()
            if len(a):
                win = (a > 0).mean() * 100
                print(f"  {h}h 绝对收益: n={len(a)}  均值 {a.mean()*100:+.2f}%  "
                      f"中位 {a.median()*100:+.2f}%  胜率 {win:.0f}%")
            if len(m):
                winm = (m > 0).mean() * 100
                print(f"  {h}h 中性收益: n={len(m)}  均值 {m.mean()*100:+.2f}%  "
                      f"中位 {m.median()*100:+.2f}%  胜率 {winm:.0f}%")

    # magnet 触及
    mg = ev[ev["has_data"] & ev["magnet_touch"].notna()]
    if len(mg):
        touch = mg["magnet_touch"].mean() * 100
        still_above = mg.loc[mg["magnet_touch"], "close48_above_magnet"].mean() * 100 \
            if mg["magnet_touch"].any() else float("nan")
        print(f"\n--- magnet 触及 ---  (n={len(mg)})")
        print(f"  48h 内触及率: {touch:.0f}%   触及后 48h 收盘仍站上 magnet 比例: "
              f"{still_above:.0f}%" if not np.isnan(still_above) else "  (无触及样本)")

    # 时延
    # 注意两种时间戳格式不同：leaderboard.factorAsOf 是毫秒时间戳，
    # 单币 markPriceAsOf 是 ISO 字符串（如 2026-09-05T15:13:55Z）
    factor_ts = pd.to_datetime(ev["factorAsOf_lb"], unit="ms", utc=True, errors="coerce")
    price_ts = pd.to_datetime(ev["markPriceAsOf"], utc=True, errors="coerce")
    # 核心口径：因子时间戳比价格时间戳旧多久（手册：>1h 则因子用陈旧数据，B3.0 降级为确认器）
    ev["factor_lag_vs_price_min"] = (price_ts - factor_ts).dt.total_seconds() / 60
    # 辅助口径：快照时刻 vs 各时间戳
    ev["snap_vs_factor_min"] = (ev["snap_ts"] - factor_ts).dt.total_seconds() / 60
    ev["snap_vs_price_min"] = (ev["snap_ts"] - price_ts).dt.total_seconds() / 60

    core = ev["factor_lag_vs_price_min"].dropna()
    print("\n--- 信号时延 ---")
    print("  核心：因子时间戳 - 价格时间戳（正值=因子比价格旧，>60min 触发手册降级）")
    if len(core):
        print(f"    n={len(core)}  中位 {core.median():.1f} min  max {core.max():.1f} min")
    else:
        print("    无数据（factorAsOf 或 markPriceAsOf 缺失）")

    print("\n提示：手册要求 ≥2 周快照、≥30 次触发后才做正期望结论；"
          "当前仅为工具自检，数字不可引用为证据。")
    return ev


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshots", default=DEFAULT_SNAP, help="b300_snapshots.jsonl 路径")
    ap.add_argument("--klines-dir", default=DEFAULT_KLDIR, help="freqtrade futures 数据目录")
    ap.add_argument("--out-csv", default=None, help="事件明细输出 CSV（可选）")
    args = ap.parse_args()

    if not os.path.exists(args.snapshots):
        print(f"[error] 快照文件不存在: {args.snapshots}", file=sys.stderr)
        sys.exit(1)

    snaps = load_snapshots(args.snapshots)
    if not snaps:
        print("[error] 快照文件为空", file=sys.stderr)
        sys.exit(1)
    print(f"[info] 载入快照 {len(snaps)} 行")

    ev = extract_events(snaps)

    # K 线覆盖诊断：校验能否算出前向收益，取决于 K 线是否覆盖快照之后 48h
    print("\n--- K 线覆盖诊断 ---")
    latest = None
    for sym in MAJORS:
        d = load_4h(sym, args.klines_dir)
        if d.empty:
            print(f"  {sym:5s} 无 1h 数据")
            continue
        print(f"  {sym:5s} {d.index.min():%Y-%m-%d} ~ {d.index.max():%Y-%m-%d %H:%M} UTC")
        latest = d.index.max() if latest is None else max(latest, d.index.max())
    snap_max = ev["snap_ts"].max()
    if latest is not None and snap_max > latest:
        print(f"  ⚠ 快照最新 {snap_max:%Y-%m-%d %H:%M} UTC 晚于 K 线最新 "
              f"{latest:%Y-%m-%d %H:%M} UTC —— 前向收益算不出来，需先把 K 线更新到最新")

    ev = attach_forward(ev, args.klines_dir)
    summarize(ev)

    if args.out_csv:
        ev.to_csv(args.out_csv, index=False, encoding="utf-8-sig")
        print(f"\n[info] 明细已写 {args.out_csv}")


if __name__ == "__main__":
    main()
