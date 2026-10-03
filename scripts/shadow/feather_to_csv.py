#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
feather_to_csv.py —— 把 freqtrade 的 .feather K 线转成 shadow_book.py 能直接吃的 CSV。

【为什么需要它】
  shadow_book.py 能直接读 .feather（懒加载 pandas/pyarrow）。但如果你的 venv 里
  没有这两个包，就用这个脚本先转一层 —— 转换只需要 pandas，不需要别的。

【用法】
  # 只转 score_v3 里真正出现过的标的（推荐：少读几百个文件）
  python feather_to_csv.py --from-score ai500_score_history_v3.csv \
         --src data/binance/1h --dst klines_1h

  # 全量转（目录里所有文件）
  python feather_to_csv.py --src data/binance/1h --dst klines_1h

  # 指定几个标的
  python feather_to_csv.py --src data/binance/1h --dst klines_1h --symbols AIN,BTC,ETH

【注意】
  · 一定要转 **1h** 目录。4h 会让"入场后第一根 K 线开盘价"偏最多 4 小时
    （实测第 2 笔：4h 开盘价 0.17859 vs 真实成交 0.18304 → 差 2.49%）。
  · 输出文件名用 kline_sym() 归一化：AIN / AIN/USDT:USDT → AINUSDT.csv
  · 已存在的输出文件会跳过（除非 --overwrite）
"""
import argparse
import csv
import os
import re
import sys


# ── 与 shadow_book.py / build_klines_15m.py 逐字一致的归一化 ──
def base_of(sym: str) -> str:
    s = sym.strip()
    if "/" in s:
        s = s.split("/", 1)[0]
    elif s.upper().endswith("USDT"):
        s = s[:-4]
    return re.sub(r"[^A-Za-z0-9]", "", s)


def kline_sym(sym: str) -> str:
    return base_of(sym) + "USDT"


TS_CANDS = ("date", "ts", "ts_utc", "open_time", "time", "timestamp", "datetime")
OHLC_CANDS = dict(o=("open", "o"), h=("high", "h"), l=("low", "l"), c=("close", "c"))


def symbols_from_score(path):
    """读 score CSV，返回归一化后的 symbol 集合。"""
    out = set()
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rd = csv.DictReader(fh)
        F = rd.fieldnames or []
        low = {f.lower(): f for f in F}
        c_sy = None
        for cand in ("symbol", "sym", "pair", "ticker"):
            if cand in low:
                c_sy = low[cand]
                break
        if c_sy is None:
            sys.exit("✗ %s 找不到 symbol 列。实得列：%s" % (path, F))
        for r in rd:
            v = (r.get(c_sy) or "").strip()
            if v:
                out.add(kline_sym(v))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="freqtrade .feather → CSV")
    ap.add_argument("--src", required=True, help="源目录（如 data/binance/1h）")
    ap.add_argument("--dst", required=True, help="输出目录")
    ap.add_argument("--from-score", help="只转这个 score CSV 里出现过的标的")
    ap.add_argument("--symbols", help="逗号分隔，只转这些（原始形态即可）")
    ap.add_argument("--overwrite", action="store_true")
    a = ap.parse_args(argv)

    try:
        import pandas as pd
    except ImportError:
        sys.exit("✗ 需要 pandas：pip install pandas pyarrow")

    if not os.path.isdir(a.src):
        sys.exit("✗ --src 不是目录：%s" % a.src)
    os.makedirs(a.dst, exist_ok=True)

    want = None
    if a.from_score:
        want = symbols_from_score(a.from_score)
        print("score 里出现 %d 个标的（归一化后）" % len(want))
    if a.symbols:
        s = {kline_sym(x) for x in a.symbols.split(",") if x.strip()}
        want = s if want is None else (want & s)

    files = [f for f in sorted(os.listdir(a.src))
             if os.path.splitext(f)[1].lower() in (".feather", ".ft", ".arrow")]
    print("源目录 %d 个 feather 文件" % len(files))

    n_ok = n_skip = n_miss = n_row = 0
    for fn in files:
        key = kline_sym(os.path.splitext(fn)[0])
        if want is not None and key not in want:
            n_skip += 1
            continue
        out = os.path.join(a.dst, key + ".csv")
        if os.path.exists(out) and not a.overwrite:
            n_skip += 1
            continue
        try:
            df = pd.read_feather(os.path.join(a.src, fn))
        except Exception as e:
            print("  ✗ %s 读失败：%s" % (fn, e))
            n_miss += 1
            continue
        low = {str(c).strip().lower(): c for c in df.columns}
        c_ts = next((low[k] for k in TS_CANDS if k in low), None)
        if c_ts is None and hasattr(df.index, "tz"):
            df = df.reset_index()
            low = {str(c).strip().lower(): c for c in df.columns}
            c_ts = next((low[k] for k in TS_CANDS + ("index",) if k in low), None)
        cc = {}
        bad = False
        for k, cands in OHLC_CANDS.items():
            got = next((low[c] for c in cands if c in low), None)
            if got is None:
                print("  ✗ %s 缺 %s 列：%s" % (fn, k, list(df.columns)))
                bad = True
                break
            cc[k] = got
        if c_ts is None or bad:
            n_miss += 1
            continue
        with open(out, "w", newline="", encoding="utf-8") as fh:
            wr = csv.writer(fh)
            wr.writerow(["ts", "open", "high", "low", "close"])
            for r in df[[c_ts, cc["o"], cc["h"], cc["l"], cc["c"]]].itertuples(index=False):
                t = r[0]
                # 统一成 epoch 秒（shadow_book 两种都吃，但 CSV 里用 epoch 最稳）
                try:
                    naive = getattr(t, "tzinfo", None) is None and getattr(t, "tz", None) is None
                    if naive:
                        t = t.replace(tzinfo=__import__("datetime").timezone.utc)
                    ts = t.timestamp()
                except Exception:
                    ts = t
                wr.writerow([ts, r[1], r[2], r[3], r[4]])
                n_row += 1
        n_ok += 1

    print()
    print("转换完成：%d 个文件 / %d 行 → %s" % (n_ok, n_row, a.dst))
    print("跳过 %d（不在目标集 / 已存在），失败 %d" % (n_skip, n_miss))
    if want is not None:
        got = {os.path.splitext(f)[0] for f in os.listdir(a.dst) if f.endswith(".csv")}
        missing = sorted(want - got)
        print("目标 %d 个，已落地 %d 个" % (len(want), len(want) - len(missing)))
        if missing:
            print("⚠️ 源目录里缺 %d 个：%s" % (len(missing), ", ".join(missing[:10])))
            print("   （缺标的不会报错，只会让影子书少一批样本 —— 所以这里必须列出来）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
