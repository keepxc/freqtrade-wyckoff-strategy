#!/usr/bin/env python3
"""
AI500 本机独立采集器 —— 多窗口 net_flow（含流出榜）+ AI500 本尊 score
=====================================================================
探索性——非结论，禁止引用为证据。本采集器只积累观察数据，不参与任何
入场/出场决策，也不改动任何策略代码。

为什么独立进程（2026-09-02 用户拍板"不采取修改策略，只观察"）:
  - 策略跑在远程 bot 机器上，本机无法读写它的 CSV
  - 观察项不应污染策略进程：采集失败不能影响交易，采集口径变更不该重启 bot
  - 本机代理可用（127.0.0.1:10809），独立跑即可

采集内容
--------
1) net_flow 多窗口（核心）：duration = 5m/15m/30m/1h 各拉一次
   实测确认（2026-09-02）：接口**默认 duration = 1h**（不传参时与 duration=1h
   逐位相同，与 30m 中位差 37.7%）。策略当前 _fetch_list 不传 duration，
   即 median_rank 跑在 1h 累计净流窗口上，而 OI/BTC bias/K线全是 30m。
   本采集器同时记录 4 个窗口的 rank，用于回答"哪个窗口的 rank 对未来收益
   预测力最强"——历史 CSV（977k 采样点）全是 1h 口径，无法回溯对比，只能新采。

2) 流出榜 low（做空信号原料，2026-09-02 用户要求）：
   同一个响应里 {"top": [流入榜], "low": [流出榜]}，策略只读了 top，
   low 一直被丢弃。做空信号需要另一套原料，从现在开始积累。

3) AI500 本尊 score（trending-category?key=ai500）：
   与远程 bot 的 score 采集同口径（5 分钟节流），本机独立留一份。
   注意：8/17-18 那批旧 score 数据只存在于远程 bot 机器，接口无历史回放，
   本采集器是从启动时刻开始的新时钟。

数据结构（CSV, 追加写, 每天一个文件）
------------------------------------
netflow_multiwin_YYYYMMDD.csv:
  fetched_at_cst, duration, side, rank, symbol, amount, price, price_delta_percent
  side: top=流入榜 / low=流出榜；rank 为该 side 内的名次
score_YYYYMMDD.csv:
  fetched_at_cst, symbol, pair, score, signal_peak, price,
  start_price, start_time, change_pct_value

时间口径：全部东八区北京时间（CST），与项目其余 CSV 一致。
"""
import csv
import datetime
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vergex_client import VergeX  # noqa: E402

# ---- 路径：从脚本位置推导，不写死绝对路径（本机/容器都能跑）----
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
DATA_DIR = os.path.join(_REPO_ROOT, "user_data", "ai500_local")

TRENDING_URL = "https://vergex.trade/trending-crypto"
CATEGORY_URL = "https://vergex.trade/trending-category"
TIMEOUT = 15

# 采集窗口：只取可作为交易信号的短窗口。4h/8h/12h/24h 已实测会整个换掉币种池
# （24h 榜首是 HFT/UAI/USELESS 这类小币，BTC/ETH 反而在流出榜前二），
# 与"抓山寨暴涨主升段"的用途不符，不采。
DURATIONS = ["5m", "15m", "30m", "1h"]
LIST_LIMIT = 20          # top20 + low20 × 4 窗口 = 160 行/轮 ≈ 20MB/天
POLL_SEC = 60            # net_flow 轮询间隔（bot 是 30s，观察用 60s 足够且体积减半）
SCORE_POLL_SEC = 300     # score 节流（与远程 bot 同口径）
KEEP_DAYS = 60

CST = datetime.timezone(datetime.timedelta(hours=8))

NETFLOW_FIELDS = [
    "fetched_at_cst", "duration", "side", "rank", "symbol",
    "amount", "price", "price_delta_percent",
]
SCORE_FIELDS = [
    "fetched_at_cst", "symbol", "pair", "score", "signal_peak", "price",
    "start_price", "start_time", "change_pct_value",
]


def now_cst() -> datetime.datetime:
    return datetime.datetime.now(CST)


def day_path(prefix: str, ts: datetime.datetime) -> str:
    return os.path.join(DATA_DIR, f"{prefix}_{ts.strftime('%Y%m%d')}.csv")


def append_rows(path: str, fields: list, rows: list) -> None:
    if not rows:
        return
    write_header = not os.path.exists(path)
    try:
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            if write_header:
                w.writeheader()
            w.writerows(rows)
    except OSError as e:
        print(f"[warn] write failed {path}: {e}", flush=True)


# 2026-09-25：传输层改走 vergex_client（urllib + 完整浏览器头）。
# 旧实现 requests + "Mozilla/5.0" 自 09-13 vergex 上 Cloudflare 后 100% 403，
# 本采集器实际从 09-04 起就没写出过 score 行（collector.log: score_rows=0）。
# 代理：LOCAL_PROXY 显式设置时使用，否则直连（本机直连 vergex 实测 200）。
_VX = VergeX(proxy=os.environ.get("LOCAL_PROXY"), timeout=TIMEOUT, retries=3)


def _get(url: str, params: dict):
    """GET JSON。全部失败返回 None（本轮该项跳过，不写陈旧值）。"""
    path = "/" + url.rstrip("/").rsplit("/", 1)[-1]
    try:
        return _VX.get_json(path, params)
    except Exception as e:
        print(f"[warn] fetch failed {params}: {type(e).__name__}: {e}", flush=True)
        return None


def collect_netflow(ts: datetime.datetime) -> int:
    """拉 4 个窗口 × (top + low)，返回写入行数。"""
    stamp = ts.isoformat()
    rows = []
    for dur in DURATIONS:
        data = _get(TRENDING_URL, {"tab": "net_flow", "duration": dur,
                                   "limit": LIST_LIMIT})
        if data is None:
            continue
        for side in ("top", "low"):
            for item in (data.get(side) or []):
                sym = item.get("symbol")
                if not sym:
                    continue
                rows.append({
                    "fetched_at_cst": stamp,
                    "duration": dur,
                    "side": side,
                    "rank": item.get("rank"),
                    "symbol": sym,
                    "amount": item.get("amount"),
                    "price": item.get("price"),
                    "price_delta_percent": item.get("price_delta_percent"),
                })
    append_rows(day_path("netflow_multiwin", ts), NETFLOW_FIELDS, rows)
    return len(rows)


def collect_score(ts: datetime.datetime) -> int:
    stamp = ts.isoformat()
    data = _get(CATEGORY_URL, {"lang": "zh", "key": "ai500"})
    if data is None:
        return 0
    assets = (data.get("category") or {}).get("assets") or []
    rows = []
    for a in assets:
        sym = a.get("symbol")
        if not sym:
            continue
        rows.append({
            "fetched_at_cst": stamp,
            "symbol": sym,
            "pair": f"{sym}/USDT:USDT",
            "score": a.get("score"),
            "signal_peak": a.get("signal"),
            "price": a.get("price"),
            "start_price": a.get("startPrice"),
            "start_time": a.get("startTime"),
            "change_pct_value": a.get("changePctValue"),
        })
    append_rows(day_path("score", ts), SCORE_FIELDS, rows)
    return len(rows)


def cleanup_old() -> None:
    cutoff = now_cst() - datetime.timedelta(days=KEEP_DAYS)
    try:
        for name in os.listdir(DATA_DIR):
            if not name.endswith(".csv"):
                continue
            if name.startswith("score_"):
                continue   # score 榜无历史接口、体积小（~1MB/天），永不清理（H1/H2 唯一数据源）
            stem = name.rsplit("_", 1)[-1][:-4]  # YYYYMMDD
            try:
                d = datetime.datetime.strptime(stem, "%Y%m%d").replace(tzinfo=CST)
            except ValueError:
                continue
            if d < cutoff:
                os.remove(os.path.join(DATA_DIR, name))
                print(f"[info] removed old file {name}", flush=True)
    except OSError:
        pass


def main() -> int:
    os.makedirs(DATA_DIR, exist_ok=True)
    once = "--once" in sys.argv
    print(f"[info] AI500 local collector start | dir={DATA_DIR}", flush=True)
    print(f"[info] durations={DURATIONS} limit={LIST_LIMIT} "
          f"poll={POLL_SEC}s score_poll={SCORE_POLL_SEC}s", flush=True)

    last_score = 0.0
    last_cleanup = 0.0
    cycles = 0
    while True:
        t0 = time.time()
        ts = now_cst()
        n_nf = collect_netflow(ts)
        n_sc = 0
        if t0 - last_score >= SCORE_POLL_SEC:
            n_sc = collect_score(ts)
            if n_sc:
                last_score = t0   # 只有成功才消耗节流档位，失败下轮立即重试
        if t0 - last_cleanup >= 3600:
            cleanup_old()
            last_cleanup = t0

        cycles += 1
        if once or cycles % 30 == 0:
            print(f"[info] {ts.strftime('%m-%d %H:%M:%S')} "
                  f"netflow_rows={n_nf} score_rows={n_sc} cycles={cycles}",
                  flush=True)
        if once:
            return 0
        time.sleep(max(1.0, POLL_SEC - (time.time() - t0)))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[info] stopped by user", flush=True)
