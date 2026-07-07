"""
TASK 57 子任务2：Binance 官方历史归档站 OI metrics 批量下载 + 1H 聚合

数据源：https://data.binance.vision/data/futures/um/daily/metrics/BTCUSDT/
  - 日度 zip，内含 5 分钟粒度 CSV
  - 字段：create_time, symbol, sum_open_interest, sum_open_interest_value,
          count_toptrader_long_short_ratio, sum_toptrader_long_short_ratio,
          count_long_short_ratio, sum_taker_long_short_vol_ratio
  - 覆盖起点约 2020-09（2020-06 为 404，2026-07-05 已实测验证）

输出：user_data/data/binance/futures/BTC_USDT_USDT-1h-oi_metrics.feather
  - 5min → 1H 聚合口径：每根 1H K 线取该小时内**最后一条** 5min 读数（label=left，
    即 candle [H, H+1) 存 H+1 收盘前最后已知 OI 状态，与 1H close 对齐，无未来函数）

用法：
  python user_data/scripts/download_oi_metrics.py            # 下载缺失日期 + 聚合
  python user_data/scripts/download_oi_metrics.py --aggregate-only
"""
import argparse
import io
import os
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

import pandas as pd
import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# 注意：不能放进 user_data/data/binance/futures/ —— freqtrade 会把
# *-1h-oi_metrics.feather 当 K 线数据扫描，'oi_metrics' 非法 CandleType 直接 Fatal
BASE_URL = RAW_DIR = OUT_FEATHER = None  # 由 set_symbol() 初始化


def set_symbol(symbol: str):
    """符号参数化（TASK 78 ETH 复制需求）。BTC 沿用旧 raw 目录保持续传兼容。"""
    global BASE_URL, RAW_DIR, OUT_FEATHER
    BASE_URL = (f"https://data.binance.vision/data/futures/um/daily/metrics/"
                f"{symbol}/{symbol}-metrics-{{d}}.zip")
    sub = "raw" if symbol == "BTCUSDT" else f"raw_{symbol}"
    RAW_DIR = os.path.join(SCRIPT_DIR, "..", "data", "oi_metrics", sub)
    base = symbol.replace("USDT", "") + "_USDT_USDT"
    OUT_FEATHER = os.path.join(SCRIPT_DIR, "..", "data", "oi_metrics",
                               f"{base}-1h-oi_metrics.feather")

START = date(2020, 8, 1)   # 起点前探一个月，404 自动跳过
END = date(2026, 7, 4)     # 对齐 futures 1h K 线数据终点
WORKERS = 8


def daterange(d0: date, d1: date):
    d = d0
    while d <= d1:
        yield d
        d += timedelta(days=1)


def fetch_one(d: date) -> str:
    """下载单日 zip 中的 CSV 存为本地 .csv；已存在则跳过。返回状态字符串。"""
    csv_path = os.path.join(RAW_DIR, f"{d.isoformat()}.csv")
    miss_path = csv_path + ".404"
    if os.path.exists(csv_path) or os.path.exists(miss_path):
        return "skip"
    url = BASE_URL.format(d=d.isoformat())
    try:
        r = requests.get(url, timeout=30)
    except requests.RequestException as e:
        return f"err {d}: {e}"
    if r.status_code == 404:
        open(miss_path, "w").close()  # 标记 404 避免重复请求
        return "404"
    if r.status_code != 200:
        return f"err {d}: HTTP {r.status_code}"
    try:
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            name = zf.namelist()[0]
            with zf.open(name) as f, open(csv_path, "wb") as out:
                out.write(f.read())
    except zipfile.BadZipFile:
        return f"err {d}: bad zip"
    return "ok"


def download():
    os.makedirs(RAW_DIR, exist_ok=True)
    dates = list(daterange(START, END))
    stats = {"ok": 0, "skip": 0, "404": 0, "err": 0}
    errors = []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = {ex.submit(fetch_one, d): d for d in dates}
        done = 0
        for fut in as_completed(futures):
            res = fut.result()
            done += 1
            if res.startswith("err"):
                stats["err"] += 1
                errors.append(res)
            else:
                stats[res] += 1
            if done % 200 == 0:
                print(f"  progress {done}/{len(dates)} {stats}", flush=True)
    print(f"download done: {stats}", flush=True)
    if errors:
        print(f"errors ({len(errors)}, 重跑本脚本可续传):", flush=True)
        for e in errors[:20]:
            print("  " + e, flush=True)
    return stats["err"] == 0


def aggregate():
    files = sorted(f for f in os.listdir(RAW_DIR) if f.endswith(".csv"))
    if not files:
        print("no csv files, nothing to aggregate")
        return
    print(f"aggregating {len(files)} daily csv files -> 1H ...", flush=True)
    frames = []
    for i, fn in enumerate(files):
        try:
            df = pd.read_csv(os.path.join(RAW_DIR, fn))
        except Exception as e:
            print(f"  bad csv {fn}: {e}", flush=True)
            continue
        frames.append(df)
        if (i + 1) % 400 == 0:
            print(f"  read {i + 1}/{len(files)}", flush=True)
    raw = pd.concat(frames, ignore_index=True)
    if pd.api.types.is_numeric_dtype(raw["create_time"]):
        raw["create_time"] = pd.to_datetime(raw["create_time"], unit="ms", utc=True)
    else:
        raw["create_time"] = pd.to_datetime(raw["create_time"], utc=True, format="mixed")
    raw = raw.sort_values("create_time").drop_duplicates(subset="create_time")

    num_cols = [c for c in raw.columns if c not in ("create_time", "symbol")]
    # 源数据坏行清洗：归档 CSV 存在 sum_open_interest=0 的占位行（实测 43 处，
    # 如 2022-03-07、2024-07-10 集群），OI 物理上不可能归零，置 NaN 后前向填充
    # （OI 是状态变量，ffill=最后已知状态，无未来函数；limit=24 防跨长缺口污染）
    bad = raw["sum_open_interest"] <= 0
    raw.loc[bad, num_cols] = pd.NA
    hourly = (
        raw.set_index("create_time")[num_cols]
        .resample("1h", label="left", closed="left")
        .last()
        .dropna(how="all")
        .reset_index()
        .rename(columns={"create_time": "date"})
    )
    hourly[["sum_open_interest", "sum_open_interest_value"]] = hourly[
        ["sum_open_interest", "sum_open_interest_value"]
    ].ffill(limit=24)
    n_bad = int(bad.sum())
    print(f"cleaned {n_bad} zero-OI placeholder rows (5min level)", flush=True)
    hourly.to_feather(OUT_FEATHER)
    print(f"saved {len(hourly)} hourly rows -> {OUT_FEATHER}", flush=True)
    print(f"coverage: {hourly['date'].min()} -> {hourly['date'].max()}", flush=True)
    gaps = hourly["date"].diff().dt.total_seconds().div(3600)
    big = gaps[gaps > 1]
    print(f"gaps>1h: {len(big)} 处，最大 {gaps.max():.0f}h", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--aggregate-only", action="store_true")
    ap.add_argument("--symbol", default="BTCUSDT")
    args = ap.parse_args()
    set_symbol(args.symbol)
    if not args.aggregate_only:
        ok = download()
        if not ok:
            print("存在下载错误，聚合仍继续（重跑可补齐缺口）", flush=True)
    aggregate()
