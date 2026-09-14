#!/usr/bin/env python3
"""
AI500 币安 aggTrades 常驻采集器（TASK 127 分层 imbalance 观察）
==============================================================

⛔ 已停用，不再恢复（断点 2026-09-06 17:52，2026-09-13 拍板结案）
   ------------------------------------------------------------------
   本脚本保留只为读懂历史落盘数据的格式，**不要重启它**。

   停用原因（当时无人记录，以下为复盘）：
     - 事实（9/14 在 bot 上实测 `ls -l` + `ps aux`）：`agg_trades/` 里
       agg_20260831 ~ agg_20260906 七个文件齐全，`last_id.txt` 与 agg_20260906.csv
       同在 **9/6 17:52** 停更，进程已不在。**断点 = 2026-09-06 17:52**，
       9/11 文档记的"9/6 停"是对的。
       （9/13 曾据一次不完整的目录清点改写成"只剩 agg_20260831.csv → 断点 8/31"，
        已被推翻；"09-02 17:54 重启"那次其实成功了。别再引用 8/31 那个说法。）
     - 唯一消费方是 V1/V2 三件套的 `_get_btc_imb()`，它们 9/5 随 V3ScoreHold
       上线一并退役，V3 完全不读本目录 → 数据没有读者了。9/5 退役、9/6 傍晚停采，
       时间上相当贴合，但**没有直接记录能坐实因果**。
     - 另一可能：它是 5s 轮询的无守护常驻进程，掉线即静默死亡（且默认代理
       写死 10809，端口一变就连不上）。两条哪条是真因已不可考。

   为什么不恢复——**采集冗余**（RESEARCH_FRAMEWORK 教训 7）：
   BTCUSDT 合约 aggTrades 在币安官方归档有逐日全量，断多久都能补回来，
   不属于"断一小时永久缺一小时"的那类数据（score 榜才是）。
   需要补的只是 **2026-09-06 17:52 之后**的时段；8/31~9/6 本地已有落盘。
   2026-09-13 实测：
     https://data.binance.vision/data/futures/um/daily/aggTrades/BTCUSDT/
       BTCUSDT-aggTrades-YYYY-MM-DD.zip
     9/2~9/12 逐日均 HTTP 200（4.9~14.3 MB/天，9/12 解压 296,172 行）；
     当日文件次日才发布（9/13 查当天 → 404），故归档只能补历史，**喂不了实时**。
   归档 CSV 表头: agg_trade_id,price,quantity,first_trade_id,last_trade_id,
                  transact_time,is_buyer_maker
   与本脚本落盘的列映射（两边都按 **UTC 日**切文件，文件名 1:1 对应，无需重分桶）：
     ts_ms ← transact_time   agg_id ← agg_trade_id   price ← price
     qty   ← quantity        is_buyer_maker ← is_buyer_maker（true/false → 1/0）
   消费方按 `next(reader, None)` 跳一行表头，补档文件保留表头即可直接被读。

   要恢复实时采集才需要重启本脚本（届时先修：代理默认值 + 加守护/自启）。
   ------------------------------------------------------------------

数据源: 币安合约 /fapi/v1/aggTrades（原生，非第三方）
方式: fromId 增量轮询, 每 5 秒拉一次, 落盘到本地 CSV
用途: AI500 策略每轮读取最近 30m 的逐笔成交, 计算价格分层 imbalance
      观察积累（不进入场），每周回测统计。

为什么独立进程:
  - aggTrades REST 单次只能拉 ~1000 笔（约 14 秒窗口），必须高频增量拉取
  - 塞进 freqtrade bot_loop_start（每 30m 一次）会漏掉大量成交
  - 独立进程即使 freqtrade 重启也不中断采集

数据结构（CSV, 追加写）:
  ts_ms, agg_id, price, qty, is_buyer_maker
  is_buyer_maker: true=主动卖, false=主动买

落盘策略: 每天一个文件, 保留最近 30 天（自动清理更早的）
"""
import csv
import os
import sys
import time
import datetime
import requests

SYMBOL = "BTCUSDT"
API_BASE = "https://fapi.binance.com/fapi/v1/aggTrades"
PROXY = {"http": os.environ.get("LOCAL_PROXY", "http://127.0.0.1:10809"), "https": os.environ.get("LOCAL_PROXY", "http://127.0.0.1:10809")}
POLL_SEC = 5.0
MAX_ROWS_PER_POLL = 1000
DATA_DIR = "/home/gyan/freqtrade/user_data/agg_trades"
KEEP_DAYS = 30

_last_id_path = os.path.join(DATA_DIR, "last_id.txt")


def now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def day_path(ts_ms: int) -> str:
    d = datetime.datetime.fromtimestamp(ts_ms / 1000, datetime.timezone.utc)
    return os.path.join(DATA_DIR, f"agg_{d.strftime('%Y%m%d')}.csv")


def load_last_id() -> int | None:
    try:
        with open(_last_id_path) as f:
            return int(f.read().strip())
    except Exception:
        return None


def save_last_id(agg_id: int) -> None:
    try:
        with open(_last_id_path, "w") as f:
            f.write(str(agg_id))
    except OSError:
        pass


def fetch_batch(from_id: int | None) -> list[dict]:
    params = {"symbol": SYMBOL, "limit": MAX_ROWS_PER_POLL}
    if from_id is not None:
        params["fromId"] = from_id
    for attempt in range(3):
        try:
            r = requests.get(API_BASE, params=params, proxies=PROXY, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
    return []


def append_rows(rows: list[dict]) -> None:
    if not rows:
        return
    by_day: dict[str, list] = {}
    for t in rows:
        ts = int(t["T"])
        dp = day_path(ts)
        by_day.setdefault(dp, []).append(t)
    for dp, items in by_day.items():
        write_header = not os.path.exists(dp)
        try:
            with open(dp, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if write_header:
                    w.writerow(["ts_ms", "agg_id", "price", "qty", "is_buyer_maker"])
                for t in items:
                    w.writerow([t["T"], t["a"], t["p"], t["q"], 1 if t["m"] else 0])
        except OSError as e:
            print(f"[collector] write error {dp}: {e}", flush=True)


def cleanup_old() -> None:
    cutoff = time.time() - KEEP_DAYS * 86400
    for fn in os.listdir(DATA_DIR):
        if not fn.startswith("agg_") or not fn.endswith(".csv"):
            continue
        fp = os.path.join(DATA_DIR, fn)
        try:
            if os.path.getmtime(fp) < cutoff:
                os.remove(fp)
                print(f"[collector] cleaned {fn}", flush=True)
        except OSError:
            pass


def main() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    last_id = load_last_id()
    print(f"[collector] start last_id={last_id}", flush=True)
    consecutive_errors = 0
    while True:
        try:
            rows = fetch_batch(last_id)
            if rows:
                # fromId 语义: 返回 >= fromId 的行。第一行可能是重复的
                # （上次已写），跳过与 last_id 相同的
                rows_sorted = sorted(rows, key=lambda t: t["a"])
                to_write = rows_sorted
                if last_id is not None:
                    to_write = [t for t in rows_sorted if t["a"] > last_id]
                append_rows(to_write)
                if to_write:
                    last_id = max(t["a"] for t in to_write)
                    save_last_id(last_id)
                consecutive_errors = 0
            else:
                # 拉到空了说明追平，保持 last_id
                pass
            if int(time.time()) % 3600 < 5:
                cleanup_old()
        except Exception as e:
            consecutive_errors += 1
            print(f"[collector] error: {e} (x{consecutive_errors})", flush=True)
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
