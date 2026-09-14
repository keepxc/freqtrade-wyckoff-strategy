#!/usr/bin/env python3
"""
数据可用性矩阵 —— 多路组合研究的前置盘点器
================================================

**探索性——非结论，禁止引用为证据。** 本脚本不做任何信号检验，只回答一个事实问题：
**如果要把 N 路数据组合起来检验，实际能用的联合样本有多长？**

背景（2026-09-14）：线路B 单信号已判无效，方向转为"多路数据组合找可盈利策略"。
但组合只能在各路 coverage 的**交集**上测，交集由**最晚开始的那一路**决定。
不可重建的数据（V3 score 榜、vergex 榜）起点 = bot 开采那天，永远补不回来；
可重建的（K 线派生、aggTrades、糖哥大单）能离线补成长历史。
先把这张矩阵量出来，才谈得上"组合什么"——否则跑出来的任何组合都可能只是
在十几天样本上拟合噪声。

用法（在 bot 机器上跑）：
    cd E:\freqtrade && python3 scripts/data_coverage_matrix.py
    python3 scripts/data_coverage_matrix.py --json out.json   # 额外存机器可读版

只读。不写任何数据文件（除非显式 --json）。
"""
from __future__ import annotations

import argparse
import csv
import glob
import gzip
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 可重建性分类（RESEARCH_FRAMEWORK 教训 7）
#   kline      = 纯 K 线派生，Binance Vision 有数年归档 → 可补到数年
#   archive    = 币安官方归档逐日全量（aggTrades）→ 可补到归档起点
#   vendor_ret = 第三方接口可回溯拉取，但有硬起点
#   NO         = 不可重建，停采即永久缺失
RECONSTRUCTIBLE = {
    "kline": "可补到数年（Binance Vision）",
    "archive": "可补到归档起点（data.binance.vision）",
    "vendor_ret": "可回溯拉取，受供应商硬起点限制",
    "NO": "❌ 不可重建，起点=开采日",
}

# 盘点目标。path 支持 glob；kind 决定解析方式。
TARGETS = [
    {
        "key": "netflow_oi",
        "desc": "#1 netflow_rank/median_rank #2 OI delta #3 price_delta（bot_loop_start 写）",
        "path": "user_data/ai500_netflow_oi_history.csv",
        "kind": "csv",
        "recon": "NO",
        "note": "vergex 专有排名，无历史接口；OI 列币安官方仅留 30 天",
    },
    {
        "key": "btc_mtf",
        "desc": "#4 MACD共振 #5 30min bias #6 糖哥大单净流 #7 imbalance",
        "path": "user_data/ai500_btc_mtf_history.csv",
        "kind": "csv",
        "recon": "kline",
        "note": "#4/#5 纯K线可重算；#6 糖哥可回溯(硬起点07-03)；#7 需 aggTrades 归档",
    },
    {
        "key": "score_v3",
        "desc": "V3 score 榜（策略循环写）",
        "path": "user_data/ai500_score_history_v3.csv",
        "kind": "csv",
        "recon": "NO",
        "note": "专有打分+幸存者偏差，标准的必须持续采",
    },
    {
        "key": "zombie",
        "desc": "V3 僵尸过滤检查",
        "path": "user_data/ai500_zombie_check.csv",
        "kind": "csv",
        "recon": "NO",
        "note": "派生自 score 榜快照",
    },
    {
        "key": "netflow_multiwin",
        "desc": "V3 netflow 五档（vergex）",
        "path": "user_data/ai500_netflow_multiwin.csv",
        "kind": "csv",
        "recon": "NO",
        "note": "vergex 源，间歇失败属正常抖动",
    },
    {
        "key": "b300_snapshots",
        "desc": "b300 榜单快照",
        "path": "user_data/collect/b300_snapshots.jsonl",
        "kind": "jsonl",
        "recon": "NO",
        "note": "第三方榜，不可回溯",
    },
    {
        "key": "b300_signals",
        "desc": "b300 信号",
        "path": "user_data/collect/b300_signals.jsonl",
        "kind": "jsonl",
        "recon": "NO",
        "note": "",
    },
    {
        "key": "b300_breakout",
        "desc": "b300 突破分类",
        "path": "user_data/collect/b300_breakout.jsonl",
        "kind": "jsonl",
        "recon": "NO",
        "note": "",
    },
    {
        "key": "macd_m8",
        "desc": "MACD-M8 状态机（已停采，转离线重建）",
        "path": "scripts/macd_m8.jsonl",
        "kind": "jsonl",
        "recon": "kline",
        "note": "已停采；重建器 scripts/macd_m8_rebuild_history.py",
    },
    {
        "key": "agg_trades",
        "desc": "#8 币安 aggTrades 逐笔（已停采 09-06 17:52）",
        "path": "user_data/agg_trades/agg_*.csv",
        "kind": "csv_multi",
        "recon": "archive",
        "note": "官方归档逐日全量可补，当日文件次日发布",
    },
    {
        "key": "oi_hist",
        "desc": "币安官方 OI 归档（每6h cron）",
        "path": "user_data/ai500_oi_hist/oi_*.csv.gz",
        "kind": "csvgz_multi",
        "recon": "vendor_ret",
        "note": "币安 openInterestHist 仅留 30 天 → 超窗即永久缺失，归档器在抢这个窗口",
    },
    {
        "key": "netflow_compact",
        "desc": "netflow 历史抽取产物（传回分析机用）",
        "path": "user_data/ai500_netflow_hist_compact.csv.gz",
        "kind": "csvgz",
        "recon": "NO",
        "note": "派生自 netflow_oi",
    },
]

TS_NAME_RE = re.compile(r"(^|_)(ts|time|timestamp|date|datetime|dt)($|_)", re.I)

# 观测时间列名（优先于业务时间列）—— 2026-09-14 教训：
# 只按 TS_NAME_RE 挑，会把 `start_time`（币种趋势起点，语义完全不同）当成采样时间。
# 实测 ai500_score_history_v3.csv / ai500_zombie_check.csv 因此把末点报成
# "最近一个币的 start_time"、滞后虚报 16.7h（真实是分钟级新鲜），起点/跨度/洞全跟着错。
# 根因：`fetched_at_utc` 这种名字 TS_NAME_RE 认不出（token 拆成 fetched/at/utc），
# 而 `start_time` 里的 `_time` 命中 → 被优先挑走。故单独给一条更靠前的规则。
FETCH_TS_RE = re.compile(
    r"(^|_)(fetched|observed|recorded|snapshot|collected|logged|ingested|written|updated|created)($|_)"
    r"|(^|_)(ts|timestamp|datetime)($|_)",
    re.I,
)


def _parse_ts(v):
    """尽量把一个值解析成 aware datetime。失败返回 None。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        n = float(v)
    else:
        s = str(v).strip()
        if not s:
            return None
        # ISO 形式优先（带或不带时区）
        if any(c in s for c in "-T:") and not s.replace(".", "").replace("-", "").isdigit():
            try:
                d = datetime.fromisoformat(s.replace("Z", "+00:00"))
                return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        try:
            n = float(s)
        except ValueError:
            return None
    # epoch：按量级判秒/毫秒
    if n > 1e17:      # 纳秒
        n /= 1e9
    elif n > 1e14:    # 微秒
        n /= 1e6
    elif n > 1e11:    # 毫秒
        n /= 1e3
    if not (1e9 < n < 4e9):   # 2001..2096 之外视为非时间戳
        return None
    try:
        return datetime.fromtimestamp(n, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _pick_ts_col(header, sample_rows):
    """在表头里挑一个能解析成时间的列。**观测时间列优先于业务时间列。**

    sample_rows 传多行：真实落盘文件常有空行/半截行，只看第一行会整份误判。

    候选顺序（2026-09-14 修）：① `FETCH_TS_RE` 命中的观测列名（fetched_at_utc 等）
    → ② `TS_NAME_RE` 命中的其他时间列名 → ③ 其余列按位置。
    旧版只有 ②③，于是在"既有观测列又有业务时间列"的文件上挑走了 `start_time`。
    """
    if sample_rows and isinstance(sample_rows[0], str):   # 容错：传了单行
        sample_rows = [sample_rows]
    rows = [r for r in sample_rows if r and any(str(c).strip() for c in r)]
    if not rows:
        return None
    observed = [i for i, h in enumerate(header) if FETCH_TS_RE.search(h or "")]
    named = [i for i, h in enumerate(header)
             if i not in observed and TS_NAME_RE.search(h or "")]
    ncol = max([len(header)] + [len(r) for r in rows])
    rest = [i for i in range(ncol) if i not in observed and i not in named]
    for i in observed + named + rest:
        if any(i < len(r) and _parse_ts(r[i]) for r in rows):
            return i
    return None


def _open(path):
    return gzip.open(path, "rt", encoding="utf-8", errors="replace") if path.endswith(".gz") \
        else open(path, "r", encoding="utf-8", errors="replace")


def scan_csv(path):
    """返回 (rows, ts_list)。ts_list 可能为空（没找到时间列）。"""
    ts = []
    rows = 0
    with _open(path) as f:
        rd = csv.reader(f)
        try:
            header = next(rd)
        except StopIteration:
            return 0, []
        # 取前若干行做探针：空行/半截行不能让整份文件误判成"无时间列"
        probe = []
        for r in rd:
            if r and any(str(c).strip() for c in r):
                probe.append(r)
            if len(probe) >= 20:
                break
        if not probe:
            return 0, []
        col = _pick_ts_col(header, probe)
        # 表头可能其实是数据（无表头文件）
        if col is None:
            hdr_as_data = _pick_ts_col([""] * len(header), [header])
            if hdr_as_data is not None:
                col = hdr_as_data
                t = _parse_ts(header[col])
                if t:
                    ts.append(t)
                    rows += 1
        if col is None:
            rows = len(probe) + sum(1 for r in rd if r and any(str(c).strip() for c in r))
            return rows, []
        for r in probe + list(rd):
            if not r or not any(str(c).strip() for c in r):
                continue
            rows += 1
            if col < len(r):
                t = _parse_ts(r[col])
                if t:
                    ts.append(t)
    return rows, ts


def scan_jsonl(path):
    ts = []
    rows = 0
    with _open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows += 1
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            # 观测时间键优先（同 CSV 的教训：别被 start_time 这类业务时间键截胡）
            for k, v in list(obj.items()):
                if FETCH_TS_RE.search(k):
                    t = _parse_ts(v)
                    if t:
                        ts.append(t)
                        break
            else:
                for k, v in obj.items():
                    if TS_NAME_RE.search(k):
                        t = _parse_ts(v)
                        if t:
                            ts.append(t)
                            break
    return rows, ts


def gaps(ts_sorted, expect_step):
    """返回 (最大间隔, 超过 3×预期步长的洞的个数)。"""
    if len(ts_sorted) < 2:
        return None, 0
    mx = timedelta(0)
    holes = 0
    thr = expect_step * 3
    for a, b in zip(ts_sorted, ts_sorted[1:]):
        d = b - a
        if d > mx:
            mx = d
        if d > thr:
            holes += 1
    return mx, holes


def median_step(ts_sorted):
    if len(ts_sorted) < 3:
        return timedelta(minutes=30)
    deltas = sorted((b - a for a, b in zip(ts_sorted, ts_sorted[1:])), key=lambda d: d.total_seconds())
    return deltas[len(deltas) // 2] or timedelta(minutes=30)


def scan_target(t):
    pat = os.path.join(REPO, t["path"])
    files = sorted(glob.glob(pat))
    out = dict(t)
    out["files"] = len(files)
    if not files:
        out["status"] = "MISSING"
        return out
    rows_total = 0
    ts_all = []
    newest_mtime = 0.0
    for fp in files:
        # glob 会把【断链的 symlink】也返回来（user_data/collect/ 这类汇聚目录常见，
        # 且这些 symlink 不在 git 里、换机即失效）。对它调 getmtime 抛 FileNotFoundError
        # 会让整次盘点零产物 —— 2026-09-14 首跑就是这么崩的。逐文件容错。
        try:
            newest_mtime = max(newest_mtime, os.path.getmtime(fp))
        except OSError as e:
            print(f"  [warn] 路径不可达，跳过: {fp} ({e.__class__.__name__})", file=sys.stderr)
            continue
        try:
            if t["kind"] in ("jsonl",):
                r, ts = scan_jsonl(fp)
            else:
                r, ts = scan_csv(fp)
        except OSError as e:
            print(f"  [warn] 读不了 {fp}: {e}", file=sys.stderr)
            continue
        rows_total += r
        ts_all.extend(ts)
    out["rows"] = rows_total
    out["mtime"] = (datetime.fromtimestamp(newest_mtime, timezone.utc)
                    if newest_mtime else None)
    if not ts_all:
        out["status"] = "NO_TS"
        return out
    ts_all.sort()
    step = median_step(ts_all)
    mx, holes = gaps(ts_all, step)
    out["status"] = "OK"
    out["first"] = ts_all[0]
    out["last"] = ts_all[-1]
    out["span_days"] = (ts_all[-1] - ts_all[0]).total_seconds() / 86400
    out["step"] = step
    out["max_gap"] = mx
    out["holes"] = holes
    out["stale_h"] = (datetime.now(timezone.utc) - ts_all[-1]).total_seconds() / 3600
    return out


def fmt_td(td):
    if td is None:
        return "-"
    s = td.total_seconds()
    if s < 60:
        return f"{s:.0f}s"
    if s < 3600:
        return f"{s/60:.0f}m"
    if s < 86400:
        return f"{s/3600:.1f}h"
    return f"{s/86400:.1f}d"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="额外把结果写成 JSON")
    args = ap.parse_args()

    print("=" * 100)
    print("数据可用性矩阵 —— 探索性，非结论，禁止引用为证据")
    print(f"仓库: {REPO}   生成: {datetime.now(timezone.utc).astimezone():%Y-%m-%d %H:%M:%S %z}")
    print("=" * 100)

    results = [scan_target(t) for t in TARGETS]

    print(f"\n{'key':<18}{'状态':<9}{'行数':>10}  {'起点(UTC)':<17}{'末点(UTC)':<17}"
          f"{'跨度':>7}{'步长':>7}{'最大洞':>8}{'洞数':>5}{'滞后':>8}  可重建")
    print("-" * 130)
    for r in results:
        if r["status"] == "MISSING":
            print(f"{r['key']:<18}{'缺失':<9}{'-':>10}  {'(文件不存在)':<52}{'':>28}  {r['recon']}")
            continue
        if r["status"] == "NO_TS":
            print(f"{r['key']:<18}{'无时间列':<9}{r['rows']:>10}  {'(解析不出时间戳，需人工看)':<52}{'':>28}  {r['recon']}")
            continue
        stale = r["stale_h"]
        flag = "🟢" if stale < 2 else ("🟡" if stale < 48 else "🔴")
        print(f"{r['key']:<18}{flag+' OK':<9}{r['rows']:>10}  "
              f"{r['first']:%Y-%m-%d %H:%M}  {r['last']:%Y-%m-%d %H:%M}  "
              f"{r['span_days']:>6.1f}d{fmt_td(r['step']):>7}{fmt_td(r['max_gap']):>8}"
              f"{r['holes']:>5}{stale:>7.1f}h  {r['recon']}")

    print("\n【逐项说明】")
    for r in results:
        print(f"  {r['key']:<18} {r['desc']}")
        print(f"  {'':<18} 可重建性: {RECONSTRUCTIBLE[r['recon']]}")
        if r["note"]:
            print(f"  {'':<18} 备注: {r['note']}")

    # ---- 核心：联合可测窗口 ----
    live = [r for r in results if r["status"] == "OK" and r["rows"] > 0]
    hard = [r for r in live if r["recon"] == "NO"]

    print("\n" + "=" * 100)
    print("联合可测窗口 —— 组合研究实际能用多长样本")
    print("=" * 100)
    def report_intersection(label, group, extra=None):
        """交集可能为空（有路子已停采）——必须显式说清，不能打负数。"""
        if not group:
            print(f"\n{label}: 无可用路子。")
            return
        sbind = max(group, key=lambda r: r["first"])
        ebind = min(group, key=lambda r: r["last"])
        start, end = sbind["first"], ebind["last"]
        print(f"\n{label}（{len(group)} 路）: {start:%Y-%m-%d %H:%M} ~ {end:%Y-%m-%d %H:%M}", end="")
        if end <= start:
            print("  =  ❌ 空集")
            print(f"  起点最晚: {sbind['key']}（{sbind['first']:%Y-%m-%d %H:%M}）")
            print(f"  末点最早: {ebind['key']}（{ebind['last']:%Y-%m-%d %H:%M}，已停采）")
            print("  → 这几路【从来没有同时在采过】，合起来一天也测不了。"
                  "要么补档拉齐，要么把已停的那路排除后重算。")
            return
        print(f"  =  {(end - start).total_seconds()/86400:.1f} 天")
        print(f"  起点最晚: {sbind['key']}（{sbind['first']:%Y-%m-%d %H:%M}）")
        print(f"  末点最早: {ebind['key']}（{ebind['last']:%Y-%m-%d %H:%M}）")
        if extra:
            print(extra)

    if not live:
        print("没有可用数据，无法计算交集。")
    else:
        report_intersection("全部路子取交集", live)
        # 已停采的会让交集塌成空集，单列一份"仍在采"的
        fresh = [r for r in live if r["stale_h"] < 48]
        if len(fresh) != len(live):
            stopped = sorted(r["key"] for r in live if r["stale_h"] >= 48)
            print(f"\n（排除已停采/滞后>48h 的 {len(live)-len(fresh)} 路: {', '.join(stopped)}）")
            report_intersection("仍在采的路子取交集", fresh)
        report_intersection(
            "只看【不可重建】的路子", hard,
            extra="  ⚠️ 这个数字是硬上限——可重建的几路补再长，组合仍受它约束。")

        print("\n【逐路起点排序】（越靠后越是瓶颈）")
        for r in sorted(live, key=lambda r: r["first"], reverse=True):
            mark = "❌不可重建" if r["recon"] == "NO" else "  可重建  "
            print(f"  {r['first']:%Y-%m-%d %H:%M}  {mark}  {r['key']}")

    print("\n" + "-" * 100)
    print("怎么读这张表：")
    print("  · 组合 N 路只能在这 N 路的交集上测，样本长度 = 交集天数，不是最长那一路的天数。")
    print("  · 可重建的几路能离线补长；不可重建的补不了，它们决定硬上限。")
    print("  · 交集短 → 任何组合搜索都极易拟合噪声。先算 MDE，再决定测不测（CLAUDE.md 🔒前置纪律）。")
    print("  · 🔴 滞后 >48h 的路子可能已经断采，先查再用。")
    print("-" * 100)

    if args.json:
        ser = []
        for r in results:
            d = {k: v for k, v in r.items() if k not in ("step", "max_gap", "mtime", "first", "last")}
            for k in ("first", "last", "mtime"):
                if isinstance(r.get(k), datetime):
                    d[k] = r[k].isoformat()
            for k in ("step", "max_gap"):
                if isinstance(r.get(k), timedelta):
                    d[k] = r[k].total_seconds()
            ser.append(d)
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(ser, f, ensure_ascii=False, indent=2)
        print(f"\n[json] 已写 {args.json}")


if __name__ == "__main__":
    main()
