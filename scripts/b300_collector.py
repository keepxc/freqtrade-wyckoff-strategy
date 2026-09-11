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
    b300_breakout.jsonl   爆发观察线：币安涨幅榜 ∩ VergeX 有合约 的结构分类结果

接口（公开、无需登录）:
    GET https://vergex.trade/api/v1/direction-change/leaderboard
    GET https://vergex.trade/api/v1/data-intelligence/markets/core_perp/core_perp%3A{SYM}/signals?chain=mainnet&liqBand=15
"""
import argparse
import json
import os
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


# ==================== 爆发观察线 ====================

BREAKOUT_FILE = HERE / "b300_breakout.jsonl"
STABLES = {"USDC", "FDUSD", "TUSD", "BUSD", "DAI", "EUR", "USDP", "AEUR"}
# 币安 API 需走本地代理（系统默认代理对 binance 隧道 502）
BINANCE_PROXY = os.environ.get("B300_PROXY", "http://127.0.0.1:10809")


def _proxied_get_json(url: str, retries: int = 3):
    last_err = None
    for i in range(retries):
        try:
            op = urllib.request.build_opener(urllib.request.ProxyHandler(
                {"http": BINANCE_PROXY, "https": BINANCE_PROXY}))
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with op.open(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            last_err = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"GET(proxied) failed after {retries} retries: {url} ({last_err})")


def fetch_binance_gainers(top_n=20):
    """币安 24h 涨幅榜 top N（USDT 现货对，剔除杠杆代币和稳定币）。"""
    items = _proxied_get_json("https://api.binance.com/api/v3/ticker/24hr")
    rows = []
    for it in items:
        sym = it.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        base = sym[:-4]
        if base.endswith(("UP", "DOWN", "BULL", "BEAR")) or base in STABLES:
            continue
        pct = float(it.get("priceChangePercent") or 0)
        qv = float(it.get("quoteVolume") or 0)
        if qv < 5_000_000:      # 流动性门槛：24h 成交额 ≥ 500 万 USDT
            continue
        rows.append({"symbol": base, "pct": pct, "quoteVolume": qv,
                     "lastPrice": float(it.get("lastPrice") or 0)})
    rows.sort(key=lambda r: r["pct"], reverse=True)
    return rows[:top_n]


CORE_DIMS = ("cgo", "wp", "vap", "lfa")   # 主要投票维度（skw 不投票，convexity/cascade 多为中性）


def classify_breakout(sig: dict) -> dict:
    """爆发线结构分类（四级）。

    - no_market       : 503/无数据 → VergeX 无此合约，放弃
    - candidate       : state=ok + bias=bullish + gShort>gLong（完整数据，正常仓）
    - candidate_minor : state=degraded 但 Z 可算 + CORE_DIMS ≥3 个有 percentile
                        + bias=bullish + gShort>gLong（ZEC 类：次要维度 skw/convexity/
                        cascade 缺失连累 state，核心结构完好）→ 记录为准候选，
                        观察期内不交易，两周后用数据裁决是否放宽
    - watch           : ARB 类，Z=0/核心 percentile 缺失，真·数据未积累
    - reject          : bias 非 bullish 或多头套牢占优
    """
    metrics = sig.get("metrics") or {}
    state = metrics.get("state")
    g_long = metrics.get("gLong") or 0
    g_short = abs(metrics.get("gShort") or 0)
    pctl = {d.get("key"): d.get("percentile") for d in sig.get("dimensions", [])}
    core_ok = sum(1 for k in CORE_DIMS if pctl.get(k) is not None)
    z = sig.get("compositeZ")
    bias = sig.get("bias")

    structure_bull = bias == "bullish" and g_short > g_long
    if structure_bull and state == "ok":
        return {"tier": "candidate", "reason": f"state=ok gShort={g_short:.2f}>gLong={g_long:.2f} Z={z}",
                "z": z, "bias": bias, "gLong": round(g_long, 3), "gShort": round(g_short, 3),
                "core_ok": core_ok, "size_hint": "normal" if (z or 0) >= MIN_COMPOSITE_Z else "half"}
    if structure_bull and core_ok >= 3 and z is not None and (z or 0) >= MIN_COMPOSITE_Z:
        return {"tier": "candidate_minor",
                "reason": f"state={state} 但核心维度{core_ok}/4 有值 Z={z} 结构完好（准候选，观察期不交易）",
                "z": z, "bias": bias, "gLong": round(g_long, 3), "gShort": round(g_short, 3),
                "core_ok": core_ok, "size_hint": "half(待政策)"}
    if core_ok >= 1 or z is not None:
        return {"tier": "watch", "reason": f"state={state} Z={z} 核心维度{core_ok}/4 数据不全",
                "z": z, "bias": bias, "gLong": round(g_long, 3), "gShort": round(g_short, 3),
                "core_ok": core_ok}
    return {"tier": "watch", "reason": f"state={state} Z=0 无 percentile，数据未积累",
            "z": z, "bias": bias, "gLong": round(g_long, 3), "gShort": round(g_short, 3),
            "core_ok": core_ok}


def breakout_scan(top_n=20):
    now = datetime.now(timezone(timedelta(hours=8)))
    ts = now.isoformat(timespec="seconds")
    gainers = fetch_binance_gainers(top_n)
    print(f"[breakout] 币安 24h 涨幅榜 top{len(gainers)}: "
          + ", ".join(f"{r['symbol']}+{r['pct']:.1f}%" for r in gainers[:8]) + " ...")

    results = []
    for g in gainers:
        sym = g["symbol"]
        time.sleep(REQUEST_GAP)
        try:
            sig = fetch_signals(sym)
        except RuntimeError as e:
            results.append({**g, "ts": ts, "tier": "no_market", "reason": str(e)[:80]})
            print(f"  {sym:<10} +{g['pct']:.1f}%  no_market（VergeX 无合约）")
            continue
        v = classify_breakout(sig)
        lv = sig.get("levels") or {}
        results.append({**g, "ts": ts, **v,
                        "bias": sig.get("bias"),
                        "markPrice": lv.get("markPrice"),
                        "magnet": lv.get("magnet"),
                        "support": lv.get("support")})
        print(f"  {sym:<10} +{g['pct']:.1f}%  {v['tier']:<9} {v['reason']}")

    with open(BREAKOUT_FILE, "a", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    cands = [r for r in results if r["tier"] == "candidate"]
    minors = [r for r in results if r["tier"] == "candidate_minor"]
    print(f"[done] {ts} 共{len(results)} | candidate={len(cands)} "
          f"candidate_minor={len(minors)} watch={sum(1 for r in results if r['tier']=='watch')} "
          f"no_market={sum(1 for r in results if r['tier']=='no_market')}")
    for m in cands + minors:
        print(f"  [准候选] {m['symbol']} +{m['pct']:.1f}% Z={m.get('z')} core={m.get('core_ok')}/4 -> {m.get('size_hint')}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="执行一次 B3.0 快照后退出")
    ap.add_argument("--loop", action="store_true", help="每 4h 循环执行 B3.0 快照")
    ap.add_argument("--breakout", action="store_true",
                    help="爆发观察线：币安 24h 涨幅榜 ∩ VergeX 有合约，结构分类落盘")
    ap.add_argument("--top", type=int, default=20, help="爆发线取币安涨幅榜前 N（默认 20）")
    args = ap.parse_args()

    if args.breakout:
        breakout_scan(args.top)
    elif args.loop:
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
