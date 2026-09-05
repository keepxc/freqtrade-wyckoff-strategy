#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
B3.0 主流币结构策略 —— 快照采集 + 信号检测
用法:
    python b300_collector.py --once     # 单次执行（配合 Windows 任务计划程序每 4h 调度）
    python b300_collector.py --loop     # 内置循环，每 4h 执行一次（调试用）

输出（脚本同目录）:
    b300_snapshots.jsonl  每 4h 一行全量快照（回测/时延验证用）
    b300_signals.jsonl    触发 B3.0 入场门槛的事件

接口（公开、无需登录）:
    GET https://vergex.trade/api/v1/direction-change/leaderboard
    GET https://vergex.trade/api/v1/data-intelligence/markets/core_perp/core_perp%3A{SYM}/signals?chain=mainnet&liqBand=15
"""
import argparse
import json
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
SNAP_FILE = HERE / "b300_snapshots.jsonl"
SIG_FILE = HERE / "b300_signals.jsonl"

BASE = "https://vergex.trade/api/v1"
# 主流池：仅 core_perp 加密主流。ZEC/XMR/PONS 等属山寨，勿加入。
MAJORS = {"BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "LINK", "AAVE", "HYPE"}
LIQ_BAND = 15          # 强平带 ±%，平台默认
INTERVAL_H = 4         # 扫描周期（对齐 4h K 线收盘）
REQUEST_GAP = 1.5      # 请求间隔秒，勿高频
UA = "Mozilla/5.0"

# ---- B3.0 入场门槛 ----
MIN_DIRECTION_SCORE = 4    # leaderboard 满票
MIN_COMPOSITE_Z = 0.5      # 单币 Z 门槛
MIN_FACTOR_PCTL = 70       # cgo / wp 百分位门槛
REQUIRED_VOTES = 2         # 三条满足两条


def http_get_json(url: str, retries: int = 3):
    last_err = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last_err = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"GET failed after {retries} retries: {url} ({last_err})")


def fetch_leaderboard():
    """全市场方向排行榜（30 市场）。"""
    items = http_get_json(f"{BASE}/direction-change/leaderboard")
    if isinstance(items, dict):
        items = items.get("items") or items.get("data") or []
    return items


def fetch_signals(symbol: str):
    """单币完整结构信号。"""
    url = (f"{BASE}/data-intelligence/markets/core_perp/"
           f"core_perp%3A{symbol}/signals?chain=mainnet&liqBand={LIQ_BAND}")
    return http_get_json(url).get("data", {})


def dim_pct(data: dict, key: str):
    for d in data.get("dimensions", []):
        if d.get("key") == key:
            return d.get("percentile")
    return None


def check_entry(lb_item: dict, sig: dict) -> dict:
    """B3.0 入场门槛：三条满足两条，再叠加 bias 否决。"""
    conds = {
        "direction_score": lb_item.get("directionScore", 0) >= MIN_DIRECTION_SCORE
                           and lb_item.get("bearishCount", 0) == 0,
        "composite_z": (sig.get("compositeZ") or -99) >= MIN_COMPOSITE_Z,
        "cost_structure": any(
            (p or 0) >= MIN_FACTOR_PCTL
            for p in (dim_pct(sig, "cgo"), dim_pct(sig, "wp"))
        ),
    }
    passed = sum(conds.values())
    return {
        "ok": passed >= REQUIRED_VOTES and sig.get("bias") == "bullish",
        "conds_met": passed,
        "conds": conds,
    }


def scan_once():
    now = datetime.now(timezone(timedelta(hours=8)))
    ts = now.isoformat(timespec="seconds")
    lb = fetch_leaderboard()

    snapshot = {"ts": ts, "leaderboard": lb, "signals": {}}
    signals = []

    for item in lb:
        sym = item.get("symbol", "")
        if item.get("market", {}).get("marketType") != "core_perp" or sym not in MAJORS:
            continue
        time.sleep(REQUEST_GAP)
        try:
            sig = fetch_signals(sym)
        except RuntimeError as e:
            print(f"[warn] {sym} signals failed: {e}", file=sys.stderr)
            continue
        snapshot["signals"][sym] = {
            "compositeZ": sig.get("compositeZ"),
            "rank": sig.get("rank"),
            "bias": sig.get("bias"),
            "cgo_pctl": dim_pct(sig, "cgo"),
            "wp_pctl": dim_pct(sig, "wp"),
            "magnet": (sig.get("levels") or {}).get("magnet"),
            "support": (sig.get("levels") or {}).get("support"),
            "longLiqBelow": (sig.get("metrics") or {}).get("longLiqBelow"),
            "markPriceAsOf": sig.get("markPriceAsOf"),
            "factorAsOf_lb": item.get("factorAsOf"),
        }
        verdict = check_entry(item, sig)
        if verdict["ok"]:
            signals.append({
                "ts": ts, "symbol": sym,
                "directionScore": item.get("directionScore"),
                "compositeZ": sig.get("compositeZ"),
                "magnet": (sig.get("levels") or {}).get("magnet"),
                "markPrice": (sig.get("levels") or {}).get("markPrice"),
                "support": (sig.get("levels") or {}).get("support"),
                "note": f"conds {verdict['conds_met']}/3 {verdict['conds']}; "
                        f"入场需 4h 收阳; 止损 support-1% 或 -5%",
            })
            print(f"[SIGNAL] {ts} {sym} score={item.get('directionScore')} "
                  f"Z={sig.get('compositeZ')} magnet={(sig.get('levels') or {}).get('magnet')}")

    # 落盘
    with open(SNAP_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(snapshot, ensure_ascii=False) + "\n")
    if signals:
        with open(SIG_FILE, "a", encoding="utf-8") as f:
            for s in signals:
                f.write(json.dumps(s, ensure_ascii=False) + "\n")

    print(f"[done] {ts} leaderboard={len(lb)} scanned={len(snapshot['signals'])} signals={len(signals)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="执行一次后退出")
    ap.add_argument("--loop", action="store_true", help="每 4h 循环执行")
    args = ap.parse_args()

    if args.loop:
        while True:
            try:
                scan_once()
            except Exception as e:
                print(f"[error] {e}", file=sys.stderr)
            time.sleep(INTERVAL_H * 3600)
    else:
        scan_once()


if __name__ == "__main__":
    main()
