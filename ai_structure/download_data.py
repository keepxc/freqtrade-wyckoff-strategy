# -*- coding: utf-8 -*-
"""首次全量下载 BTCUSDT 1h 历史（币安公开数据，无需 API key）。

为什么单独一个脚本：swing_watch.py --update 是增量逻辑，
首次全量约需 80 次分页请求，独立跑便于看进度、便于断点续传。

用法：
    python3 download_data.py              # 从 2017-08-17 拉到现在
    python3 download_data.py --from 2020-01-01
"""
import argparse
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.request

import config as C

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

H_MS = 3_600_000
LIMIT = 1000
START_DEFAULT = "2017-08-17"


def ms(ymd):
    return int(datetime.datetime.strptime(ymd, "%Y-%m-%d")
               .replace(tzinfo=datetime.UTC).timestamp() * 1000)


def opener():
    if C.PROXY:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": C.PROXY, "https": C.PROXY}))
    return urllib.request.build_opener()


def fetch(start, end, op):
    out, cursor, calls = [], start, 0
    while cursor < end:
        url = (f"{C.BINANCE_KLINES}?symbol={C.SYMBOL}&interval={C.INTERVAL}"
               f"&startTime={cursor}&limit={LIMIT}")
        try:
            with op.open(url, timeout=30) as r:
                batch = json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (418, 429):
                print("  被限频，等 5 秒 …")
                time.sleep(5)
                continue
            raise
        if not batch:
            break
        out.extend(batch)
        cursor = int(batch[-1][0]) + H_MS
        calls += 1
        if calls % 10 == 0:
            d = datetime.datetime.fromtimestamp(cursor / 1000, datetime.UTC)
            print(f"  {len(out):>7,} 根  拉到 {d:%Y-%m-%d}")
        time.sleep(0.12)          # 对公共接口客气一点
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="frm", default=START_DEFAULT)
    ap.add_argument("--out", default=C.CSV)
    args = ap.parse_args()

    start = ms(args.frm)
    end = int(datetime.datetime.now(datetime.UTC).timestamp() * 1000)
    print(f"下载 {C.SYMBOL} {C.INTERVAL}  {args.frm} → 现在")
    print(f"代理：{C.PROXY or '直连'}")

    op = opener()
    # 只保留已收盘 bar（k[6] = close_time）。进行中的那根不能落盘，见 swing_watch.closed_only
    rows = [k for k in fetch(start, end, op) if int(k[6]) < end]
    if not rows:
        print("[失败] 未取到数据。若在中国大陆，请在 .env 里设置 HTTPS_PROXY。",
              file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    seen, uniq = set(), []
    for k in rows:
        if k[0] in seen:
            continue
        seen.add(k[0])
        uniq.append(k)
    uniq.sort(key=lambda k: k[0])

    with open(args.out, "w", encoding="utf-8", newline="") as fh:
        fh.write("ts,date,open,high,low,close,volume\n")
        for k in uniq:
            dt = datetime.datetime.fromtimestamp(k[0] / 1000, datetime.UTC)
            fh.write(f"{int(k[0])},{dt:%Y-%m-%d %H:%M},{k[1]},{k[2]},{k[3]},"
                     f"{k[4]},{k[5]}\n")

    a = datetime.datetime.fromtimestamp(uniq[0][0] / 1000, datetime.UTC)
    b = datetime.datetime.fromtimestamp(uniq[-1][0] / 1000, datetime.UTC)
    expect = int((uniq[-1][0] - uniq[0][0]) / H_MS) + 1
    print(f"\n已写 {args.out}")
    print(f"  {len(uniq):,} 根  {a:%Y-%m-%d %H:%M} → {b:%Y-%m-%d %H:%M} UTC")
    print(f"  覆盖率 {len(uniq) / expect * 100:.2f}%（应接近 100%）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
