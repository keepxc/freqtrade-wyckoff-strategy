#!/usr/bin/env python3
"""
币安永续 1m K 线批量下载 —— 供 Step A 扩展校准 / Step B 回测使用
================================================================
探索性——非结论，禁止引用为证据。

为什么走 data.binance.vision 而不是 fapi
----------------------------------------
fapi 有 IP 限频，2026-09-03 本机出口 IP 被 -1003 封过多次且到期时间持续后滚
（共享代理，其他用户打的）。Vision 是静态文件站，不受该封禁影响，适合批量。
只有 Vision 尚未发布的最近 1-2 天才需回落到 fapi。

覆盖区间
--------
远程 bot 的 netflow 历史起于 2026-08-18 23:32 CST，故只需 08-18 之后。
8 月用月包（一次请求覆盖整月，再裁掉 08-18 之前），9 月用日包。

产物
----
user_data/ai500_local/klines_hist/<SYMBOL>-1m.feather
列: t(UTC), close, quote_volume, taker_buy_quote_volume, net_flow
net_flow = 2*taker_buy_quote_volume - quote_volume  （主动买 - 主动卖）

断点续传：已存在的 symbol 直接跳过，中断后重跑即可。
"""
import io
import os
import sys
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
DATA_DIR = os.path.join(_REPO_ROOT, "user_data", "ai500_local")
OUT_DIR = os.path.join(DATA_DIR, "klines_hist")
SYMS_FILE = os.path.join(DATA_DIR, "binance_perp_symbols.txt")

PROXY = {"http": os.environ.get("LOCAL_PROXY", "http://127.0.0.1:10809"), "https": os.environ.get("LOCAL_PROXY", "http://127.0.0.1:10809")}
V_MONTH = "https://data.binance.vision/data/futures/um/monthly/klines"
V_DAY = "https://data.binance.vision/data/futures/um/daily/klines"

# Vision 是静态 CDN，可并发；单线程 526 个币约需 45 分钟。
# requests.Session 非严格线程安全，故每线程各持一个（threading.local）。
_TL = threading.local()


def _session():
    s = getattr(_TL, "s", None)
    if s is None:
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0"})
        _TL.s = s
    return s


WORKERS = 8
NET_RETRY = 3
TIMEOUT = 90

START = pd.Timestamp("2026-08-18", tz="UTC")
MONTHS = ["2026-08"]   # 9 月未结束，无月包，靠日包补
DAYS = ["2026-09-01", "2026-09-02", "2026-09-03"]

COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume",
        "ignore"]
KEEP = ["open_time", "close", "quote_volume", "taker_buy_quote_volume"]


def _get(url):
    for attempt in range(NET_RETRY):
        try:
            return _session().get(url, proxies=PROXY, timeout=TIMEOUT)
        except requests.exceptions.RequestException:
            if attempt == NET_RETRY - 1:
                return None
            time.sleep(1.5 * (attempt + 1))
    return None


def _parse_zip(content):
    z = zipfile.ZipFile(io.BytesIO(content))
    raw = z.read(z.namelist()[0]).decode()
    has_header = raw.lstrip().startswith("open_time")
    return pd.read_csv(io.StringIO(raw), header=0 if has_header else None,
                       names=None if has_header else COLS)


def fetch_symbol(sym):
    frames = []
    for m in MONTHS:
        r = _get(f"{V_MONTH}/{sym}/1m/{sym}-1m-{m}.zip")
        if r is not None and r.status_code == 200:
            try:
                frames.append(_parse_zip(r.content))
            except Exception:
                pass
    for d in DAYS:
        r = _get(f"{V_DAY}/{sym}/1m/{sym}-1m-{d}.zip")
        if r is not None and r.status_code == 200:
            try:
                frames.append(_parse_zip(r.content))
            except Exception:
                pass
    if not frames:
        return None
    d = pd.concat(frames, ignore_index=True)[KEEP]
    d = d.astype({"open_time": "int64", "close": float,
                  "quote_volume": float, "taker_buy_quote_volume": float})
    d["t"] = pd.to_datetime(d.open_time, unit="ms", utc=True)
    d = d[d.t >= START]
    if not len(d):
        return None
    d["net_flow"] = 2.0 * d.taker_buy_quote_volume - d.quote_volume
    return (d.sort_values("t").drop_duplicates("t").reset_index(drop=True)
            [["t", "close", "quote_volume", "taker_buy_quote_volume",
              "net_flow"]])


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    syms = [s.strip() for s in open(SYMS_FILE) if s.strip()]
    print(f"[info] 标的 {len(syms)} | 区间 {START.date()} 起 | 输出 {OUT_DIR}",
          flush=True)

    todo = [x for x in syms
            if not os.path.exists(os.path.join(OUT_DIR, f"{x}-1m.feather"))]
    skipped = len(syms) - len(todo)
    print(f"[info] 待下 {len(todo)} | 已存在跳过 {skipped} | 并发 {WORKERS}",
          flush=True)

    done = failed = 0
    t0 = time.time()
    lock = threading.Lock()

    def work(sym):
        d = fetch_symbol(sym)
        if d is None:
            return sym, False
        d.to_feather(os.path.join(OUT_DIR, f"{sym}-1m.feather"))
        return sym, True

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(work, s): s for s in todo}
        for n, fut in enumerate(as_completed(futs), 1):
            try:
                _, ok = fut.result()
            except Exception:
                ok = False
            with lock:
                if ok:
                    done += 1
                else:
                    failed += 1
            if n % 25 == 0:
                el = time.time() - t0
                eta = el / n * (len(todo) - n) / 60
                print(f"[info]   {n}/{len(todo)} | 成功 {done} 无数据 {failed}"
                      f" | 已用 {el/60:.1f}min ETA {eta:.1f}min", flush=True)

    size = sum(os.path.getsize(os.path.join(OUT_DIR, f))
               for f in os.listdir(OUT_DIR)) / 1e6
    print(f"\n[info] 完成: 新下 {done} | 跳过 {skipped} | 无数据 {failed}")
    print(f"[info] 落盘 {len(os.listdir(OUT_DIR))} 个文件, {size:.0f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
