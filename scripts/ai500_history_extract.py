#!/usr/bin/env python3
"""
远程 bot netflow 历史抽取器 —— 在 bot 机器上运行
=================================================
探索性——非结论，禁止引用为证据。

用途
----
/home/gyan/freqtrade/user_data/ai500_netflow_oi_history.csv 约 330MB，是策略
bot_loop_start 每 ~30s 写入的资金流榜快照，覆盖数月。Step A 校准需要它作为
"成对数据"的 VergeX 那一半——本机只有 2026-09-02 之后的快照，样本仅覆盖约
19 小时单一行情，405 个名义快照因 60s 间距高度自相关，有效样本极少。
接上这批历史，校准样本才能跨月份、跨行情。

只读，不修改原文件。

做两件事
--------
1) 诊断（必须先看）：时间戳口径是否一致
   列名是 fetched_at_utc，但实际格式带 +08:00。仓库有过 "CSV 现为北京时间"
   的口径变更提交，说明历史上改过写法。若早期行是真 UTC、后期行是 CST，
   而解析时一视同仁，整段历史会错位 8 小时，校准结果全部作废。
   脚本会列出所有出现过的时区偏移及各自的时间范围与行数——
   **看到多于一种偏移，先别用，回来商量怎么切分。**

2) 抽取：只保留需要的列 + 降采样
   30s 采样对 1h 窗口是严重过采样，且相邻快照几乎完全相关。
   默认降到 5 分钟一采，体积约降到 1/10，再 gzip 后通常几 MB。

   2026-09-03 追加 oi_delta_percent 等列。第一版只留 median_rank，
   但策略真实入场是三闸门 AND（median_rank<=3 且 OI 30m 变化>3% 且
   BTC 30m 涨幅>0），只测 median_rank 得到的 796 个事件里有多少能过 OI
   闸门是未知的，那批收益数字因此不能直接用来判断实盘策略有效与否。
   加上 OI 列才能量出各闸门各自的贡献。

用法
----
  python3 ai500_history_extract.py                     # 诊断 + 抽取（默认 5 分钟）
  python3 ai500_history_extract.py --diagnose-only     # 只诊断，不写文件
  python3 ai500_history_extract.py --every-min 2       # 改用 2 分钟粒度

产物: ai500_netflow_hist_compact.csv.gz （与源文件同目录）
"""
import argparse
import os
import sys
from collections import Counter, defaultdict

import pandas as pd

SRC_DEFAULT = "/home/gyan/freqtrade/user_data/ai500_netflow_oi_history.csv"
OUT_NAME = "ai500_netflow_hist_compact.csv.gz"

# 必需列：时间 + 标的 + 名次 + 净流金额，缺任何一个都没法用。
KEEP_REQ = ["fetched_at_utc", "symbol", "netflow_rank", "median_rank",
            "netflow_amount", "price"]

# 可选列（2026-09-03 追加）。第一版只留了 median_rank，但策略的真实入场是
#   median_rank <= 3  AND  oi_delta_percent > OI_MIN  AND  BTC 30m 涨幅 > 0
# 三个闸门的 AND。只测 median_rank 测的是"闸门之一"，不是策略实际会开的仓——
# 796 个事件里有多少能过 OI 闸门完全未知，所以那批数字不可迁移到实盘判断。
#
# btc_large_net_* 不是 BTC 涨幅（那个 CSV 里没有，但可直接从 K 线算），
# 是 BTC 大单净流；顺手留着，增量成本很低。
#
# 缺列不致命：早期行可能还没写这些字段，缺了就跳过该列并提示。
KEEP_OPT = ["oi_delta_percent", "oi_source",
            "btc_large_net_30m", "btc_large_net_15m"]

CHUNK = 500_000


def offset_of(s: str) -> str:
    """提取时间戳字符串的时区偏移标记，用于口径一致性检查。"""
    s = s.strip()
    if s.endswith("Z"):
        return "Z(UTC)"
    # ISO 尾部形如 +08:00 / -05:00
    if len(s) >= 6 and s[-6] in "+-" and s[-3] == ":":
        return s[-6:]
    return "naive(无偏移)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=SRC_DEFAULT)
    ap.add_argument("--every-min", type=int, default=5,
                    help="降采样粒度（分钟），默认 5")
    ap.add_argument("--diagnose-only", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(args.src):
        sys.exit(f"[fatal] 找不到 {args.src}")
    print(f"[info] 源文件 {args.src} "
          f"({os.path.getsize(args.src)/1e6:.1f} MB)", flush=True)

    off_counts = Counter()
    off_range = defaultdict(lambda: [None, None])
    total = 0
    symbols = set()
    kept_frames = []
    seen_buckets = set()
    keep = None          # 首个 chunk 里按实际存在的列确定

    reader = pd.read_csv(args.src, chunksize=CHUNK, dtype=str,
                         on_bad_lines="skip")
    for i, ch in enumerate(reader, 1):
        total += len(ch)
        missing = [c for c in KEEP_REQ if c not in ch.columns]
        if missing:
            sys.exit(f"[fatal] 缺必需列 {missing}；实际列={list(ch.columns)}")
        if keep is None:
            have = [c for c in KEEP_OPT if c in ch.columns]
            absent = [c for c in KEEP_OPT if c not in ch.columns]
            keep = KEEP_REQ + have
            if absent:
                print(f"[warn] 可选列缺失，已跳过: {absent}", flush=True)
            print(f"[info] 抽取列: {keep}", flush=True)

        ts = ch["fetched_at_utc"].astype(str)
        offs = ts.map(offset_of)
        off_counts.update(offs.value_counts().to_dict())
        for o in offs.unique():
            sub = ts[offs == o]
            lo, hi = sub.min(), sub.max()
            cur = off_range[o]
            cur[0] = lo if cur[0] is None else min(cur[0], lo)
            cur[1] = hi if cur[1] is None else max(cur[1], hi)

        symbols.update(ch["symbol"].dropna().unique())

        if not args.diagnose_only:
            sub = ch[keep].copy()
            # utc=True 会按各行自带的偏移换算到同一绝对时刻；
            # 只有当上面诊断显示偏移口径一致时，这个结果才可信。
            t = pd.to_datetime(sub["fetched_at_utc"], errors="coerce", utc=True)
            sub = sub[t.notna()]
            t = t[t.notna()]
            bucket = t.dt.floor(f"{args.every_min}min")
            # 每个时间桶只保留第一个快照（整轮 top/low 一起保留）
            first_ts = t.groupby(bucket).transform("min")
            sel = (t == first_ts) & ~bucket.isin(seen_buckets)
            seen_buckets.update(bucket[sel].unique())
            if sel.any():
                out = sub[sel].copy()
                out["ts_utc"] = t[sel].dt.tz_convert("UTC")
                kept_frames.append(out.drop(columns=["fetched_at_utc"]))

        if i % 20 == 0:
            print(f"[info]   已读 {total:,} 行", flush=True)

    print(f"\n[info] 总行数 {total:,} | 标的数 {len(symbols)}")

    print("\n" + "=" * 70)
    print("时间戳口径诊断  ← 先看这里")
    print("=" * 70)
    for o, n in off_counts.most_common():
        lo, hi = off_range[o]
        print(f"  {o:>14} : {n:>12,} 行 | {lo}  ->  {hi}")
    if len(off_counts) > 1:
        print("\n  ⚠ 出现多于一种时区口径。历史上写法变更过，"
              "跨变更点直接混用会造成 8 小时错位。")
        print("    先把这段输出发回来，确定切分点后再用抽取结果。")
    else:
        print("\n  ✓ 全程单一口径，可直接换算。")

    if args.diagnose_only:
        return 0

    if not kept_frames:
        sys.exit("[fatal] 没有可用行")
    res = pd.concat(kept_frames, ignore_index=True).sort_values("ts_utc")
    out_path = os.path.join(os.path.dirname(args.src), OUT_NAME)
    res.to_csv(out_path, index=False, compression="gzip")

    print("\n" + "=" * 70)
    print(f"抽取完成: {out_path}")
    print("=" * 70)
    print(f"  行数      {len(res):,}  (源 {total:,}，降采样 {args.every_min} 分钟)")
    print(f"  快照数    {res.ts_utc.nunique():,}")
    print(f"  时间范围  {res.ts_utc.min()}  ->  {res.ts_utc.max()}")
    print(f"  体积      {os.path.getsize(out_path)/1e6:.1f} MB")
    print("\n把这个 .csv.gz 传回来即可（scp / rsync 都行）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
