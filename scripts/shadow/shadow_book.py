#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
shadow_book.py —— 影子组合跟踪器（AI500 动量策略）

【为什么需要它】
  实盘 V3 的入场门槛 `rank<=3 & score>=80` 把样本掐死了：
  实测 9 天 1 笔 → 一年约 40 笔 → **统计上不可判**。
  但"放宽门槛"要冒真实资金风险。

  影子组合 = **不改实盘参数**，同时虚拟跟踪多个参数组合。
  样本量放大 5–10 倍，成本 0，风险 0。

【三个铁律（防止自我欺骗）】
  1. **入场价取"入场时刻之后第一根 K 线的开盘价"** —— 不用当时的价格。
     用当时价 = 前视偏差（你已知它会涨才会看到它）。
  2. **止损优先于其他出场** —— 同一根 K 线既触止损又收阴时，按止损算。
  3. **成本必须扣** —— 默认 20bp 往返（taker 0.05%×2 + 滑点）。
     止损路径要更贵：实测第 2 笔实盘（AIN/USDT）名义止损 −5.00%、实际 −5.17%，
     **多出的 17bp 就是止损路径上的滑点 + 手续费**。→ `--fees-sl-bp` 单独设止损档
     （建议跑 20bp 与 40bp 两遍，看结论会不会翻转）。

【用法】
  python shadow_book.py --score score_v3.csv --klines klines/ --out shadow/
  python shadow_book.py --score score_v3.csv --klines klines/ --combos p0,s1,s2
  python shadow_book.py --score score_v3.csv --klines klines/ --fees-bp 20 --fees-sl-bp 40
  python shadow_book.py --profile-only --score score_v3.csv     # 不需要 K 线
  python shadow_book.py --list-combos

【输入格式】
  --score   CSV。实测 ai500_score_history_v3.csv 的列：
              fetched_at_utc, symbol, pair, score, signal_peak, price,
              start_price, start_time, change_pct_value
            ⚠️ 它【没有 rank 列】→ 默认按 score 降序推导（--rank-mode）
            ⚠️ 涨幅列叫 change_pct_value，不是 change_pct
            ⚠️ fetched_at_utc 列名骗人，实测带 +08:00 偏移（真 CST）
  --klines  目录，每 symbol 一个文件：<BINANCE_SYMBOL>.feather / .csv
            freqtrade 落盘格式（列：date, open, high, low, close, volume）与
            普通 CSV（ts,open,high,low,close）都支持。
            文件名按 kline_sym() 归一化后匹配：AIN / AIN/USDT:USDT → AINUSDT
            ⚠️ 请用 1h 目录 —— 粒度越粗，铁律 1 的入场价偏移越大（--max-bar-h 会告警）

【输出】
  shadow_trades.csv    每笔明细（combo, symbol, entry_ts, entry_px, exit_ts,
                       exit_px, reason, hold_h, chg_at_entry, score_at_entry,
                       rank_at_entry, fee_bp, gross_bp, ret_bp）
  shadow_summary.csv   每组合统计（n, win_rate, mean_bp, sd_bp,
                       t, ci_lo_bp, ci_hi_bp, verdict）
  shadow_history.csv   **历史台账（追加，不覆盖）**。每行带 run_ts + span_days +
                       当次费率档 —— 用来查"读数漂移"，也用来防止手抄汇总。
                       `--no-history` 可关（冒烟/调试用）。
  shadow_diagnosis.txt 输入体检记录（带 run_ts）。回答"这个实验测得出东西吗"：
                       每快照宽度 / 低尾指纹（写盘端有没有过滤）/ 行序证据
                       （rank 在上游存不存在）/ 组合命中集重叠 / 退化对。
  shadow_rank_probe.csv 每行的 rank 溯源（ts, symbol, score, change_pct,
                       rank_derived, width_at_ts, pass_rank）。
                       把"rank<=3 到底过不过滤"变成可核对的事实。
                       `--no-probe` 可关。
"""
import argparse
import bisect
import csv
import datetime as dt
import math
import os
import re
import sys
from statistics import NormalDist

# ── 实盘一致的出场规则（不要改，除非实盘也改）────────────────────────────
SL_PCT = 5.0        # -5% 止损
MAX_HOLD_H = 48.0   # 最长持有
BAR_4H = 4 * 3600   # 4h 阴线检查粒度

# ── 预注册组合（写死。加组合要在这里加，不要事后改）──────────────────────
COMBOS = {
    "p0": dict(desc="现状：榜前3 且 score>=80",              rank_max=3,  score_min=80, chg_max=None),
    "s1": dict(desc="只限榜前3（不限 score）",                rank_max=3,  score_min=None, chg_max=None),
    "s2": dict(desc="只限 score>=80（不限榜）",               rank_max=None, score_min=80, chg_max=None),
    "s3": dict(desc="宽：榜前5 且 score>=70",                 rank_max=5,  score_min=70, chg_max=None),
    "s4": dict(desc="现状 + 涨幅上限25%（防追高）",            rank_max=3,  score_min=80, chg_max=25.0),
    "s5": dict(desc="现状 + 涨幅下限25%（追高对照）",          rank_max=3,  score_min=80, chg_min=25.0),
}
for _c in COMBOS.values():
    _c.setdefault("chg_min", None)
    _c.setdefault("chg_max", None)

# ── K 线输入：freqtrade 落盘格式（.feather）+ CSV 都支持 ─────────────────
KLINE_EXTS = (".feather", ".ft", ".arrow", ".csv")
KLINE_TS_CANDS = ("date", "ts", "ts_utc", "open_time", "time", "timestamp", "datetime")
KLINE_OHLC_CANDS = dict(o=("open", "o"), h=("high", "h"),
                        l=("low", "l"), c=("close", "c"))


def matches(spec, sn):
    """该快照是否满足组合的入场条件（不涉及持仓/冷却）。"""
    if spec["rank_max"] is not None:
        if sn["rank"] is None or sn["rank"] > spec["rank_max"]:
            return False
    if spec["score_min"] is not None:
        if sn["score"] is None or sn["score"] < spec["score_min"]:
            return False
    if spec["chg_max"] is not None:
        if sn["change_pct"] is None or sn["change_pct"] > spec["chg_max"]:
            return False
    if spec["chg_min"] is not None:
        if sn["change_pct"] is None or sn["change_pct"] < spec["chg_min"]:
            return False
    return True


def profile(snaps, combos, min_n=30):
    """
    【不需要 K 线】只描述每个组合的入场特征。
    回答的核心问题：满足条件的币，在入场那一刻【已经涨了多少】。
    """
    out = []
    for name in combos:
        spec = COMBOS[name]
        first = {}          # symbol -> 首次命中的快照（同一标的不重复计数）
        for sn in snaps:
            if not matches(spec, sn):
                continue
            if sn["symbol"] not in first:
                first[sn["symbol"]] = sn
        chg = sorted(s["change_pct"] for s in first.values() if s["change_pct"] is not None)
        n = len(first)
        def pct(q):
            if not chg:
                return None
            k = min(len(chg) - 1, max(0, int(round(q * (len(chg) - 1)))))
            return round(chg[k], 2)
        out.append(dict(combo=name, desc=spec["desc"], n=n,
                        n_uniq_sym=n,
                        chg_min=pct(0.0), chg_p25=pct(0.25), chg_med=pct(0.5),
                        chg_p75=pct(0.75), chg_max=pct(1.0),
                        verdict=("样本不足(n<%d)" % min_n) if n < min_n else "可看分布"))
    return out


# ══════════════════════ symbol 归一化（与 build_klines_15m.py 逐字一致）══════
def base_of(sym: str) -> str:
    """原始 symbol → base 资产名。对应 SQL 的 base_of() 宏（两边必须逐字一致）。

    ⚠️ 最后一步 alnum 过滤是必须的：实测源里有 'ZEC+/USDT:USDT' 这种形态，
       去掉 '/' 后剩下 'ZEC+'，而币安 symbol 只允许 [A-Z0-9]。
       不做这步 → 生成 'ZEC+USDT' → 币安返回 400 → 该标的静默消失。
    """
    s = sym.strip()
    if "/" in s:                       # ZEC+/USDT:USDT → ZEC+
        s = s.split("/", 1)[0]
    elif s.upper().endswith("USDT"):   # BTCUSDT → BTC
        s = s[:-4]
    return re.sub(r"[^A-Za-z0-9]", "", s)   # ZEC+ → ZEC；同时兜住任何其他杂字符


def kline_sym(sym: str) -> str:
    """原始 symbol → 币安合约 symbol。对应 SQL 的 kline_sym() 宏。"""
    return base_of(sym) + "USDT"


# ══════════════════════ 时间解析 ══════════════════════
def parse_ts(v):
    """ISO8601 / epoch 秒 / epoch 毫秒 → 秒（float, UTC）"""
    v = (v or "").strip()
    if not v:
        return None
    try:
        f = float(v)
        if f > 1e11:      # 毫秒
            f /= 1000.0
        return f
    except ValueError:
        pass
    s = v.replace("Z", "+00:00")
    try:
        d = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.timestamp()


def iso(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ══════════════════════ 读取 ══════════════════════
def pick_col(fields, cands):
    low = {f.lower(): f for f in fields}
    for c in cands:
        if c.lower() in low:
            return low[c.lower()]
    return None


def _first_num(s):
    """'峰值 86' → 86.0；'85.588' → 85.588；无数字 → None"""
    m = re.search(r"-?\d+(?:\.\d+)?", str(s or ""))
    return float(m.group()) if m else None


# score_v3 的实际列名（实测 ai500_score_history_v3.csv）：
#   fetched_at_utc, symbol, pair, score, signal_peak, price, start_price,
#   start_time, change_pct_value
# ⚠️ 两个坑（都会让组合【静默返回 0 笔交易】）：
#   ① 没有 rank 列 → 所有含 rank 的组合恒为 0
#   ② 涨幅列叫 change_pct_value，不叫 change_pct → s4/s5 恒为 0
#   → 见 read_score() 的 rank 推导 + main() 的列缺失预检
SCORE_COLS = dict(
    ts=["ts", "fetched_at_utc", "fetched_at", "timestamp", "time"],
    symbol=["symbol", "sym", "pair", "ticker"],
    score=["score", "ai500_score"],
    rank=["rank", "score_rank", "ranking", "rk"],
    change_pct=["change_pct_value", "change_pct", "interface_change_pct",
                "chg_pct", "pct_change", "pct"],
    price=["price", "interface_price", "binance_price", "last"],
    start_price=["start_price"],
    peak=["signal_peak", "peak"],
)

RANK_MODES = ("auto", "column", "score", "row")


def _derive_rank(rows, mode):
    """给同一快照内的标的排名（rank 列缺失时的兜底）。

    mode='score' → 按 score 降序；mode='row' → 按 **CSV 原始行序**。
    ⚠️ 这是【假设】，不是实盘事实。实盘的 rank 若另有来源，必须用 --rank-mode column。
    ⚠️ 'row' 必须用落盘时的原始行序（`_ord`），不能用排序后的顺序 ——
       否则 'row' 与 'score' 会退化成同一个东西，而差异正是我们要找的。
    """
    groups = {}
    for i, r in enumerate(rows):
        groups.setdefault(r["ts"], []).append(i)
    for ts, idxs in groups.items():
        if mode == "score":
            idxs.sort(key=lambda i: (-(rows[i]["score"] if rows[i]["score"] is not None
                                       else -1e18), rows[i]["symbol"]))
        elif mode == "row":
            idxs.sort(key=lambda i: rows[i]["_ord"])
        for k, i in enumerate(idxs, start=1):
            rows[i]["rank"] = float(k)


def read_score(path, rank_mode="auto"):
    """→ (rows, meta)。rows: list of dict(ts, symbol, score, rank, change_pct,
       price, start_price, peak, pair)"""
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rd = csv.DictReader(fh)
        F = rd.fieldnames or []
        c = {k: pick_col(F, v) for k, v in SCORE_COLS.items()}
        if not (c["ts"] and c["symbol"]):
            sys.exit("✗ score CSV 缺列：需要 ts 与 symbol。实得列：%s" % F)
        for i, r in enumerate(rd):
            t = parse_ts(r.get(c["ts"]))
            if t is None:
                continue
            def num(k):
                if k is None:
                    return None
                v = (r.get(k) or "").strip().replace("%", "")
                try:
                    return float(v)
                except ValueError:
                    return None
            rows.append(dict(
                _ord=i,                       # 落盘时的原始行序（--rank-mode row 用它）
                ts=t,
                symbol=(r.get(c["symbol"]) or "").strip().upper(),
                pair=(r.get(c["symbol"]) or "").strip().upper(),
                score=num(c["score"]),
                rank=num(c["rank"]),
                change_pct=num(c["change_pct"]),
                price=num(c["price"]),
                start_price=num(c["start_price"]),
                peak=_first_num(r.get(c["peak"])) if c["peak"] else None,
            ))
    rows.sort(key=lambda x: (x["ts"], x["symbol"]))

    # ── rank 来源判定 ──
    if rank_mode == "column":
        if c["rank"] is None:
            sys.exit("✗ --rank-mode column 但 CSV 里没有 rank 列。实得列：%s\n"
                     "  实测 ai500_score_history_v3.csv 就没有 rank 列 ——\n"
                     "  要么改用 --rank-mode score（按 score 降序推导），\n"
                     "  要么把实盘真正的排名列接进来。" % F)
        rank_source = "column"
    elif rank_mode == "auto":
        if c["rank"] is not None and any(r["rank"] is not None for r in rows):
            rank_source = "column"
        else:
            _derive_rank(rows, "score")
            rank_source = "derived_from_score"
    else:
        _derive_rank(rows, rank_mode)
        rank_source = "derived_from_%s" % rank_mode

    meta = dict(columns=F, rank_col=c["rank"], rank_source=rank_source,
                has_rank=(rank_source == "column"),
                has_change_pct=(c["change_pct"] is not None),
                n_rows=len(rows),
                n_snapshots=len({r["ts"] for r in rows}))
    return rows, meta


def _ts_of(v):
    """各种形态的时间戳 → epoch 秒（UTC）。

    ⚠️ 两个必须处理的陷阱：
      ① 字符串带 +08:00 偏移（本项目的 fetched_at_utc 列名骗人，实为 CST）
         → 必须尊重偏移，不能剥掉
      ② tz-naive 的 datetime/Timestamp 的 .timestamp() 会按【本地时区】解释
         → 必须先 replace(tzinfo=UTC)
    """
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return f / 1000.0 if f > 1e11 else f
    if hasattr(v, "timestamp"):                     # datetime / pd.Timestamp
        naive = getattr(v, "tzinfo", None) is None and getattr(v, "tz", None) is None
        try:
            if naive:
                return v.replace(tzinfo=dt.timezone.utc).timestamp()
            return v.timestamp()
        except Exception:
            pass
    return parse_ts(str(v))


def _feather_to_rows(path):
    """读 .feather / .arrow（freqtrade 落盘的格式）→ list[dict]。"""
    try:
        import pandas as pd
    except ImportError:
        sys.exit(
            "✗ 读到 %s 但没有 pandas/pyarrow。\n"
            "  freqtrade 环境本身带这两个包；若你用的是别的 venv：\n"
            "    ① pip install pyarrow pandas     或\n"
            "    ② 先用 feather_to_csv.py 把需要的标的导成 CSV，再用 --klines 指过去"
            % os.path.basename(path))
    df = pd.read_feather(path)
    return df


def _dataframe_to_bars(df, name):
    """DataFrame（freqtrade feather）→ list[(ts,o,h,l,c)]，按 ts 升序。"""
    import pandas as pd
    def colmap(d):
        return {str(c).strip().lower(): c for c in d.columns}

    low = colmap(df)
    c_ts = next((low[k] for k in KLINE_TS_CANDS if k in low), None)
    if c_ts is None:
        if isinstance(df.index, pd.DatetimeIndex):
            df = df.reset_index()
            low = colmap(df)
            c_ts = next((low[k] for k in KLINE_TS_CANDS + ("index",) if k in low), None)
    if c_ts is None:
        sys.exit("✗ %s 找不到时间列（试过 %s）。实得列：%s"
                 % (name, "/".join(KLINE_TS_CANDS), list(df.columns)))

    cc = {}
    for k, cands in KLINE_OHLC_CANDS.items():
        got = next((low[c] for c in cands if c in low), None)
        if got is None:
            sys.exit("✗ %s 缺 %s 列（试过 %s）。实得列：%s"
                     % (name, k, "/".join(cands), list(df.columns)))
        cc[k] = got

    bars = []
    for r in df[[c_ts, cc["o"], cc["h"], cc["l"], cc["c"]]].itertuples(index=False):
        t = _ts_of(r[0])
        if t is None:
            continue
        try:
            bars.append((t, float(r[1]), float(r[2]), float(r[3]), float(r[4])))
        except (TypeError, ValueError):
            continue
    bars.sort(key=lambda b: b[0])
    return bars


def read_klines(path, sym_col=None, normalize=True):
    """
    → dict(键 -> list[bar])。键 = 归一化后的币安 symbol（如 AINUSDT）。
    normalize=False 时键 = 文件名的 stem（原样大写）。
    """
    if not os.path.isdir(path):
        sys.exit("✗ --klines 不是目录：%s" % path)
    out, used = {}, []
    for fn in sorted(os.listdir(path)):
        stem, ext = os.path.splitext(fn)
        ext = ext.lower()
        if ext not in KLINE_EXTS:
            continue
        full = os.path.join(path, fn)
        key = (kline_sym(sym_col or stem) if normalize else stem.upper())
        if ext == ".csv":
            bars = _csv_to_bars(full)
        else:
            bars = _dataframe_to_bars(_feather_to_rows(full), fn)
        if bars:
            out[key] = bars
            used.append((fn, key, len(bars)))
    return out, used


def _csv_to_bars(full):
    bars = []
    with open(full, newline="", encoding="utf-8-sig") as fh:
        rd = csv.DictReader(fh)
        F = rd.fieldnames or []
        c_ts = pick_col(F, list(KLINE_TS_CANDS))
        cc = {}
        for k, cands in KLINE_OHLC_CANDS.items():
            got = pick_col(F, list(cands))
            if got is None:
                sys.exit("✗ K 线 %s 缺 %s 列。实得：%s" % (os.path.basename(full), k, F))
            cc[k] = got
        if c_ts is None:
            sys.exit("✗ K 线 %s 缺时间列。实得：%s" % (os.path.basename(full), F))
        for r in rd:
            t = _ts_of(r.get(c_ts))
            if t is None:
                continue
            try:
                bars.append((t, float(r[cc["o"]]), float(r[cc["h"]]),
                             float(r[cc["l"]]), float(r[cc["c"]])))
            except (TypeError, ValueError):
                continue
    bars.sort(key=lambda b: b[0])
    return bars


def bar_interval_sec(bars):
    """相邻 bar 间隔的众数（秒）。"""
    if not bars or len(bars) < 2:
        return None
    diffs = [bars[i + 1][0] - bars[i][0] for i in range(len(bars) - 1)]
    diffs = [d for d in diffs if d > 0]
    if not diffs:
        return None
    return max(set(diffs), key=diffs.count)


def granularity_report(klines, max_bar_h=1.0):
    """
    ⚠️ 为什么必须查这个：
      铁律 1 是「入场价 = 入场时刻【之后】第一根 K 线的开盘价」。
      bar 间隔 Δ 越大，这个价格离真实入场时刻越远（最多晚 Δ）。
      实测第 2 笔：4h 蜡烛的开盘价 0.17859 vs 真实成交 0.18304 → 差 +2.49%，
      用 4h 开盘价当入场价会让影子书凭空好 **249bp**。
      → 影子书应当用 **1h K 线**。
    """
    rows = []
    for k, bars in sorted(klines.items()):
        iv = bar_interval_sec(bars)
        rows.append((k, len(bars), iv))
    worst = max([r[2] for r in rows if r[2]], default=None)
    ok = (worst is None) or (worst <= max_bar_h * 3600 + 1)
    return ok, worst, rows


# ══════════════════════ 出场扫描 ══════════════════════
def simulate_exit(bars, i_entry, entry_px, sl_pct, max_hold_h):
    """
    从 bars[i_entry] 开始逐根扫描，返回 (exit_ts, exit_px, reason, i_exit)。
    优先级：止损 > 首根完整 4h 阴线 > MAX_HOLD
    保守约定：同一根既触止损又收阴 → 按止损价成交。
    """
    sl_px = entry_px * (1.0 - sl_pct / 100.0)
    t_entry = bars[i_entry][0]
    first4h_close = (math.floor(t_entry / BAR_4H) + 1) * BAR_4H   # 入场后第一根完整 4h 的收线时刻

    for i in range(i_entry, len(bars)):
        ts, o, h, l, c = bars[i]
        hold_h = (ts - t_entry) / 3600.0
        # ① 止损（用 low 判断，按止损价成交 = 保守，不假设更好的成交）
        if l <= sl_px:
            return ts, sl_px, "stop_loss", i
        # ② 首根完整 4h 阴线：该 bar 的收线时刻已越过 first4h_close，且该 4h 桶收阴
        if ts >= first4h_close:
            # 找该 4h 桶的第一根 bar 的开盘价作为桶开盘
            bucket_start = math.floor(ts / BAR_4H) * BAR_4H
            j = i
            while j > 0 and bars[j][0] >= bucket_start:
                j -= 1
            bucket_open = bars[j + 1][1]
            if c < bucket_open:
                return ts, c, "bear_4h", i
        # ③ 超时
        if hold_h >= max_hold_h:
            return ts, c, "max_hold", i
    # 数据用尽 → 按最后一根收盘平掉（标记未完成）
    ts, o, h, l, c = bars[-1]
    return ts, c, "eod_truncated", len(bars) - 1


# ══════════════════════ 主循环 ══════════════════════
def combo_needs(spec):
    """这个组合用到了哪些字段（用于"列缺失"预检）。"""
    need = []
    if spec["rank_max"] is not None:
        need.append("rank")
    if spec["score_min"] is not None:
        need.append("score")
    if spec["chg_max"] is not None or spec["chg_min"] is not None:
        need.append("change_pct")
    return need


def available_fields(snaps):
    return {k for k in ("rank", "score", "change_pct", "price")
            if any(s.get(k) is not None for s in snaps)}


def run_combo(name, spec, snaps, klines, fees_bp, cooldown_h, max_pos,
              fees_sl_bp=None, sym_mode="kline"):
    """
    fees_sl_bp：止损出场单独的成本档（None → 与 fees_bp 相同）。
    理由：实盘实测止损路径的滑点显著更大（AIN 那笔多出 17bp）。
    sym_mode：'kline' → 用 kline_sym() 归一化后查 K 线；'raw' → 原样查。
    """
    trades = []
    holding = {}           # symbol -> exit_ts（离线回放用未来数据算，这是允许的）
    last_exit = {}         # symbol -> ts

    for sn in snaps:
        raw = sn["symbol"]
        sym = kline_sym(raw) if sym_mode == "kline" else raw
        if sym not in klines or not klines[sym]:
            continue
        # ── 清理已平仓的持仓 ──
        for s in [k for k, x in holding.items() if x <= sn["ts"]]:
            del holding[s]
        # ── 入场条件 ──
        if not matches(spec, sn):
            continue
        # ── 持仓中 / 冷却 / 仓位上限 ──
        if sym in holding:
            continue
        if sym in last_exit and (sn["ts"] - last_exit[sym]) < cooldown_h * 3600:
            continue
        if len(holding) >= max_pos:
            continue

        bars = klines[sym]
        # ── 铁律 1：入场价 = 入场时刻【之后】第一根 K 线的开盘价 ──
        i = bisect.bisect_right([b[0] for b in bars], sn["ts"])
        if i >= len(bars):
            continue
        entry_ts, entry_px = bars[i][0], bars[i][1]
        if entry_px <= 0:
            continue

        exit_ts, exit_px, reason, _ = simulate_exit(bars, i, entry_px, SL_PCT, MAX_HOLD_H)
        gross_bp = (exit_px / entry_px - 1.0) * 1e4
        fee = fees_bp
        if reason == "stop_loss" and fees_sl_bp is not None:
            fee = fees_sl_bp
        ret_bp = gross_bp - fee
        trades.append(dict(
            combo=name, symbol=raw, ksym=sym,
            entry_ts=iso(entry_ts), entry_px=round(entry_px, 10),
            exit_ts=iso(exit_ts), exit_px=round(exit_px, 10),
            reason=reason, hold_h=round((exit_ts - entry_ts) / 3600.0, 2),
            chg_at_entry=(None if sn["change_pct"] is None else round(sn["change_pct"], 2)),
            score_at_entry=(None if sn["score"] is None else round(sn["score"], 2)),
            rank_at_entry=(None if sn["rank"] is None else int(sn["rank"])),
            fee_bp=round(fee, 2),
            gross_bp=round(gross_bp, 2), ret_bp=round(ret_bp, 2)))
        holding[sym] = exit_ts
        last_exit[sym] = exit_ts
    return trades


def summarize(trades, min_n=30):
    if not trades:
        return dict(n=0, win_rate=None, mean_bp=None, sd_bp=None,
                    t=None, ci_lo_bp=None, ci_hi_bp=None, verdict="无交易")
    r = [t["ret_bp"] for t in trades]
    n = len(r)
    mu = sum(r) / n
    sd = math.sqrt(sum((x - mu) ** 2 for x in r) / (n - 1)) if n > 1 else 0.0
    se = sd / math.sqrt(n) if n else 0.0
    t = (mu / se) if se else 0.0
    lo = mu - 1.96 * se
    hi = mu + 1.96 * se
    if n < min_n:
        verdict = "样本不足(n<%d)" % min_n
    elif lo > 0:
        verdict = "正向"
    elif hi < 0:
        verdict = "负向"
    else:
        verdict = "不可判"
    return dict(n=n, win_rate=round(100.0 * sum(1 for x in r if x > 0) / n, 1),
                mean_bp=round(mu, 2), sd_bp=round(sd, 2),
                t=round(t, 2), ci_lo_bp=round(lo, 2), ci_hi_bp=round(hi, 2),
                verdict=verdict)


def pctile(xs, q):
    """线性插值分位（xs 已排序）。"""
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    pos = q * (len(xs) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def snapshot_widths(snaps):
    """每个快照有几个标的（升序）。"""
    c = {}
    for s in snaps:
        c[s["ts"]] = c.get(s["ts"], 0) + 1
    return sorted(c.values())


def select_pairs(snaps, spec):
    """组合的【原始命中集】：{(ts, symbol)}，不考虑持仓/冷却。

    重叠矩阵用这个 —— 它回答的是"两个组合是不是在挑同一批东西"。
    """
    return {(s["ts"], s["symbol"]) for s in snaps if matches(spec, s)}


def jaccard(a, b):
    if not a and not b:
        return None
    return len(a & b) / float(len(a | b))


# ══════════════════════ 地板扫描（自动找下界，不硬编码阈值）══════════════════
FLOOR_CANDS = tuple(range(10, 100, 10))
# 「同一标的相邻两次出现」的间隔分桶（分钟）—— 用来检验"单一 T"这个模型
GAP_BUCKETS = ((0, 5), (5, 10), (10, 30), (30, 60), (60, 120), (120, float("inf")))


def _tail(xs, k=10):
    return [round(v, 4) for v in xs[:k]]


def floor_scan(snaps, field, cands=FLOOR_CANDS, pile_w=1.0, tol=1e-9,
               other_fields=("change_pct", "price", "peak")):
    """在低尾上【自动找地板】并判别它的性质。

    ⚠️ 旧实现只硬编码探测 80 / 40 两个阈值。用户实测真实下界在 **60**，
       而探测器根本没覆盖那个位置 → 报"无地板"，**是假阴性**。
       **报警器的量程太窄，等于没有报警器。**

    四种地板，处置完全不同 —— 所以必须分清：

    | 类型 | 特征 | 含义 | 处置 |
    |---|---|---|---|
    | `natural` | 低尾连续，20 个最低值几乎互不相同 | 值**没有被赋值**；**但分不出 filter** | 看【命名阈值检查】+ 采集端代码 |
    | `clamp`   | 堆积**跨 ≥3 个标的**，且该值**不是整十值** | 打分器**量程下限**（值被从下方截断） | 低分段失去分辨力；因阈值都 ≥70，无害 |
    | `freeze`  | 堆积全来自**同一标的的连续时点**，且**整行一字不变** | 不是地板，是**采集端卡住** | 查采集端 |
    | `filter_or_clamp` | 堆积**跨 ≥3 个标的**，且该值**正好是整十值** | 整数化分数+手写阈值 / 量程下限恰在整十值 | **去采集端看那几行代码** |
    | `ambiguous` | 堆积只在 **1 个标的**上（不管其他字段动没动） | ⛔ **粘滞与赋值同形** | **判不了**，去采集端看代码 |

    ── 三条证据，以及**为什么"堆积"只在跨多标的时才有效** ──

    **★ 唯一能排除 `filter` 的证据：堆积【跨 ≥3 个标的】。**
      **过滤只删行、不改值** —— 它让某些行消失，**绝不会让多行变成同一个值**。
      而 3 个以上标的的**真实**分数不可能同时等于同一个常数（连续量，概率 ≈ 0）。
      → 跨多标的的堆积 ⟹ 该值是被**赋值**的 ⟹ **排除 `filter`**。

    **⛔ 单标的的堆积什么也证明不了（第 3 次修订的关键）。**
      score 是**粘滞**的（实测 `p_same ≈ 0.83`，刷新周期小时级，而我们每 5 分钟采一次）。
      于是"同一个标的连续 N 个快照取到同一个值"在 `filter` 假设下**也是必然发生的**：
      被过滤剩下的那些币里，分数最低的那个如果恰好没被刷新，就会连续多屏保持同一个值。
      → **粘滞与赋值同形** ⟹ 单标的堆积**判不了**。
      ⛔ 特别注意：**"其他字段在动"不能把单标的堆积推向 `clamp`。**
        `change_pct` 由实时价格算出、每 5 分钟都在变；`score` 是小时级统计量。
        **"score 不动而涨幅在动"恰恰是粘滞的正常表现**，不是"值被赋值"的证据。
        （上一版就是因为这条推理把单标的堆积判成了 `clamp` —— 错。）

    **辅助证据 ①：堆积值是不是整十值。**
      手写阈值一定是整数/整十值。**只在"堆积已跨多标的"的前提下**用它区分
      `clamp`（怪值）与 `filter_or_clamp`（整十值）。
      ⛔ 单独看"是不是整十值"**不能**排除 `filter`：`score >= 60` 作用在**连续型**分数上，
      幸存行的最小值同样落在 `60.00xx`。

    **辅助证据 ②：堆积行里其他字段动没动。**
      只用来识别 `freeze`（**整行一字不变** = 采集端卡住 / 上游停更）。
      实测那 11 行 `60.0052`：score 一字不变，而**同期 `change_pct` 从 66.3 走到 69.5**
      → **不是 freeze**。但这**也不代表是 clamp** —— 见上面的粘滞说明。
    """
    rows = [s for s in snaps if s.get(field) is not None]
    if not rows:
        return None
    # ⚠️ 被扫的那一列**必须从"其他字段"里去掉** —— 否则"整行一字不变"里
    #    混进了它自己（自己当然等于自己），`n_move_other` 被系统性压低。
    #    实测后果：扫涨幅列时它自己那 11 行被判成 `freeze`（假报警）。
    other_fields = tuple(f for f in other_fields if f != field)
    vals = sorted(s[field] for s in rows)
    mn = vals[0]
    at_min = [s for s in rows if abs(s[field] - mn) < tol]
    syms = {}
    for s in at_min:
        syms.setdefault(s["symbol"], []).append(s["ts"])
    top_sym = max(syms, key=lambda k: len(syms[k])) if syms else None
    n_top = len(syms[top_sym]) if top_sym else 0
    span_top_h = ((max(syms[top_sym]) - min(syms[top_sym])) / 3600.0) if top_sym else 0.0
    low20 = vals[:20]
    n_distinct_low20 = len({round(v, 6) for v in low20})
    # 整个字段的重复率 —— 用来把"堆积"与"字段本身就是离散的"分开：
    # 若字段整体几乎不重复、只有最小值处堆积 → 那个值一定是被【赋值】的。
    n_distinct_all = len({round(v, 6) for v in vals})
    rep_all = 1.0 - n_distinct_all / float(len(vals))
    # 重复最多的【非最小值】出现几次 —— 用来判断"堆积"是不是特殊的。
    # 若最小值出现 1200 次而其他值最多 9 次 → 这个值一定是被【赋值】的。
    _cnt = {}
    for v in vals:
        _cnt[round(v, 6)] = _cnt.get(round(v, 6), 0) + 1
    n_second_max = max([c for v, c in _cnt.items() if v != round(mn, 6)] or [0])
    n_pile = sum(1 for v in vals if v < mn + pile_w)      # [min, min+1) 里有几行
    base = math.floor(mn / 10.0) * 10
    nearest_ten = round(mn / 10.0) * 10
    gap_near = mn - nearest_ten                           # <0 = 略低于整十值
    on_round = abs(gap_near) < 1e-6
    sweep = {t: sum(1 for v in vals if v < t) for t in cands}

    # ── 证据 2：堆积行里【其他字段】有没有动过 ──
    #    按标的、按时间排序后逐对比较；任一非空的其他字段不同 → 记一次"动"。
    n_move_other = n_pair_other = 0
    for _sym, tss in syms.items():
        mine = sorted((s for s in at_min if s["symbol"] == _sym), key=lambda s: s["ts"])
        for a, b in zip(mine, mine[1:]):
            shared = [f for f in other_fields
                      if a.get(f) is not None and b.get(f) is not None]
            if not shared:
                continue
            n_pair_other += 1
            if any(abs(a[f] - b[f]) > 1e-12 for f in shared):
                n_move_other += 1

    # ⚠️ 必须同时要求"最小值自己就有堆积"。
    #   只写 `n_distinct_low20 < 15` 时，**别的值**的堆积只要落进最低 20 个就
    #   会误触发：陪衬行的涨幅重复 11 次、而它们又恰好落在低 20 里 →
    #   该列被判成 ambiguous，把主列的 `freeze` 顶掉（夹具里真出现过）。
    #   所有后续证据（`at_min` / `syms` / `on_round`）都是**围着最小值**转的，
    #   所以 `piled` 的定义也必须围着最小值。
    piled = len(at_min) >= 3 and n_distinct_low20 < 15
    # ── 判定顺序（第 3 次修订：★★ 关键在【堆积跨几个标的】）──
    #    ① 整行一字不变  → freeze。最强、最无歧义的信号，优先。
    #    ② 堆积跨【≥3 个标的】→ 不同标的的真实分数不可能都等于同一个常数
    #       ⟹ 该值是被【赋值】的 ⟹ 排除 filter。**这是唯一能排除 filter 的形态。**
    #    ③ 堆积只在【1 个标的】上、而其他字段在动
    #       → ⛔ **粘滞与赋值同形**：score 刷新慢（p_same≈0.83）时，
    #         同一个标的连续 N 个快照取到同一个值是【必然】的，
    #         与"被赋值"完全不可分。**判不了，不许定案。**
    if not piled:
        kind = "natural"
    elif n_top >= 5 and span_top_h <= 2.0 and n_move_other == 0:
        kind = "freeze"
    elif len(syms) >= 3:
        # 跨多标的的堆积。若值正好是整十值 → 仍可能是「整数化分数 + 手写阈值」，分不开。
        kind = "filter_or_clamp" if on_round else "clamp"
    else:
        # 单标的堆积（含"其他字段在动"那种）→ 粘滞 vs 赋值，**同形**
        kind = "ambiguous"

    return dict(n=len(vals), mn=mn, base=base, gap_to_base=mn - base,
                nearest_ten=nearest_ten, gap_near=gap_near, on_round=on_round,
                n_at_min=len(at_min), n_sym_at_min=len(syms),
                top_sym=top_sym, n_top_sym_at_min=n_top, span_top_h=span_top_h,
                n_pair_other=n_pair_other, n_move_other=n_move_other,
                n_pile=n_pile, pile_w=pile_w, low20=low20, piled=piled,
                n_distinct_low20=n_distinct_low20,
                n_distinct_all=n_distinct_all, rep_all=rep_all,
                n_second_max=n_second_max,
                sweep=sweep, kind=kind)


def stickiness(snaps, fields=("score", "change_pct", "price")):
    """分数/字段的【粘滞】与【全榜同步冻结】—— 采样频率 vs 上游更新频率。

    ⚠️ 两个"冻结"必须分清（实测踩过：把两者混成一句）：
      · **score 层**：同一标的相邻时点 score 不变 —— 这是**粘滞**（上游刷新慢），
        **正常**，与采集端健康无关。
      · **全字段层**：整个快照的 (symbol+score+涨幅+price) 全都没变 —— 这是
        **采集端卡住 / 上游整体停更**。
      实测：score 层（**配对口径**）74.7%（第三轮）/ 62.9%（第四轮）—— 两轮口径没对齐，
      所以本函数把四种口径**一起印**；全字段层 **0.0%**（因为涨幅 99.2% 的相邻对都在动）
      ⟹ **采集端没卡住**，只是 score 本身粘。

    【为什么必须查】score 不变率很高 → **采样比上游更新快得多** →
    我们看到的"时点数"被严重高估，按 `(时点, 标的)` 计的命中集大小也跟着被抬高。

    两个必须分开的量：
      · **逐标的粘滞** `p_same` —— 上游刷新慢
      · **全榜同步冻结** —— 整个快照与前一个快照完全相同（采集端卡住 / 上游整体停更）
        判据：实测重复率 vs **独立粘滞下的期望值** Σ_w (n_w/N)·p_all^w。
        实测显著高于期望 → 存在同步冻结；**显著低于**期望 → 各标的"轮流"更新（负相关）。

    产出：`p_same` / **隐含更新周期** / **按 gap 分桶的 p_same** / 快照重复率 /
         **不同状态数（有效样本量）**。

    ⚠️⚠️ **反解用的 Δ 必须是【同一标的相邻两次出现】的间隔（ḡ），不是采样间隔。**
       正确：`T = ḡ / (1 − p_same)`。
       错误：拿快照间隔（5 分钟）当代替 → `T` 被算小 **约 10 倍**。

    ⚠️⚠️ **而且 ḡ 本身还是"混合口径"**：实测 `p_same` **强烈依赖 gap**
      （间隔 ≤5 分 → 0.85；>120 分 → 0.015）。长 gap 桶的 p_same 低，
      而长 gap 又把 ḡ 拉高 —— 两个量**负相关** ⟹ `T = ḡ/(1−p)` **系统性高估**。
      → 所以本函数额外输出 **`buckets`（按 gap 分桶）** 与 **`T_dominant`（主导桶的 T）**，
        并给出 `T_rising` 标志（桶内 T 随 gap 单调上升 ⟹ "固定周期刷新"模型不成立）。
      **引用 T 时用主导桶那个，不要用总体 ḡ 那个。**
    """
    out = {}
    by_sym = {}
    for s in snaps:
        by_sym.setdefault(s["symbol"], []).append(s)
    for f in fields:
        same = tot = 0
        gaps_f = []
        pairs_f = []            # (gap_min, 是否不变) —— 供【按 gap 分桶】用
        for sym, rows in by_sym.items():
            rows = sorted(rows, key=lambda r: r["ts"])
            for a, b in zip(rows, rows[1:]):
                if a.get(f) is None or b.get(f) is None:
                    continue
                tot += 1
                g = (b["ts"] - a["ts"]) / 60.0
                gaps_f.append(g)
                unchanged = abs(a[f] - b[f]) < 1e-12
                pairs_f.append((g, unchanged))
                if unchanged:
                    same += 1
        out[f] = dict(n=tot, same=same, p=(same / float(tot) if tot else None),
                      gap_mean_min=(sum(gaps_f) / len(gaps_f) if gaps_f else None),
                      gap_med_min=(pctile(sorted(gaps_f), 0.5) if gaps_f else None),
                      pairs=pairs_f)

    # 全字段同时不变的比例（用于期望重复率的计算）
    all_same = all_tot = 0
    for sym, rows in by_sym.items():
        rows = sorted(rows, key=lambda r: r["ts"])
        for a, b in zip(rows, rows[1:]):
            if any(a.get(f) is None or b.get(f) is None for f in fields):
                continue
            all_tot += 1
            if all(abs(a[f] - b[f]) < 1e-12 for f in fields):
                all_same += 1
    p_all = (all_same / float(all_tot)) if all_tot else None

    # 快照状态：每个时点的 (symbol, 各字段) 集合
    # ⚠️⚠️ 要维护**两套**状态，对应两个不同的"冻结"：
    #   · `state`    = 全字段（symbol+score+涨幅+price）→ 采集端卡住 / 上游整体停更
    #   · `state_sc` = **只看 score** → 粘滞，正常
    #   实测：全字段层 **0.0%**、score 层（配对口径）74.7% / 62.9%（两轮口径没对齐）。
    #   若把两者合成一套，就会拿
    #   score 层的重复率去说"采集端卡住"（用户上一轮正是这么读错的），
    #   也会让"有效样本量"这一行**恒等于 100%**（因为全字段层根本不重复）。
    state = {}
    state_sc = {}
    # ★★ 第四轮补：score 层「没变」有【四种】口径，而且它们是**嵌套**的：
    #       逐标的配对 (symbol,score) 全同  ⟹  只看分数·多重集  ⟹  只看分数·集合
    #     宽度是**独立**的一维（宽度没变 ≠ 分数没变）。
    #    ⚠️⚠️ 跨轮比较**必须同口径**。实测踩过：第三轮报 74.7%、第四轮报 62.9%，
    #       同期只多 9 个快照，不可能掉 333 次 —— 就是这两个数**口径没对齐**
    #       （配对口径要求榜成员也一模一样，集合口径不要求）。
    #       所以四个数必须**一起印出来**，引用时说清是哪一行。
    state_sc_ms = {}     # 只看分数，排序后**含重复**（多重集）
    state_sc_set = {}    # 只看分数，**去重**（集合）
    state_w = {}         # 只看**宽度**
    for s in snaps:
        state.setdefault(s["ts"], []).append(
            tuple([s["symbol"]] + [s.get(f) for f in fields]))
        state_sc.setdefault(s["ts"], []).append((s["symbol"], s.get("score")))
        state_sc_ms.setdefault(s["ts"], []).append(s.get("score"))
        state_sc_set.setdefault(s["ts"], []).append(s.get("score"))
        state_w.setdefault(s["ts"], []).append(s["symbol"])
    tss = sorted(state)
    _key = lambda x: (x is None, x)          # None 不许参与比较（会 TypeError）
    for k in state:
        state[k] = sorted(state[k])
        state_sc[k] = sorted(state_sc[k])
        state_sc_ms[k] = sorted(state_sc_ms[k], key=_key)
        state_sc_set[k] = sorted(set(state_sc_set[k]), key=_key)
        state_w[k] = len(state_w[k])
    reps = sum(1 for a, b in zip(tss, tss[1:]) if state[a] == state[b])
    reps_sc = sum(1 for a, b in zip(tss, tss[1:]) if state_sc[a] == state_sc[b])
    reps_sc_ms = sum(1 for a, b in zip(tss, tss[1:]) if state_sc_ms[a] == state_sc_ms[b])
    reps_sc_set = sum(1 for a, b in zip(tss, tss[1:]) if state_sc_set[a] == state_sc_set[b])
    reps_w = sum(1 for a, b in zip(tss, tss[1:]) if state_w[a] == state_w[b])
    n_trans = max(0, len(tss) - 1)

    # 独立粘滞下的期望重复率（按宽度加权）
    width_hist = {}
    for k in tss:
        w = len(state[k])
        width_hist[w] = width_hist.get(w, 0) + 1
    exp_rep = None
    if p_all is not None and tss:
        exp_rep = sum(v * (p_all ** w) for w, v in width_hist.items()) / float(len(tss))

    # 采样间隔（相邻时点的中位数，分钟）—— 只作背景，**不能**拿它当反解的 Δ
    gaps = sorted((tss[i + 1] - tss[i]) / 60.0 for i in range(len(tss) - 1))
    dt_med = pctile(gaps, 0.5) if gaps else None

    def implied(p, gap):
        """T = Δ/(1−p)。Δ 用【同一标的相邻出现的平均间隔】。"""
        if p is None or gap is None or p >= 1.0:
            return None
        return gap / (1.0 - p)

    def gap_buckets(pairs):
        """★ 按 gap 分桶算 p_same —— **检验"单一 T"这个模型本身**。

        若 `p_same` 强烈依赖 gap（间隔越短越不容易变），说明
        **"固定周期刷新 + 单一 T"这个模型不成立**（真实机制是自相关/多尺度刷新），
        那么用总体 ḡ 反解出的那个 T 就是**有偏的** —— 而且偏向哪边可以看：
        长 gap 桶的 p_same 低 ⟹ 把长 gap 混进来会把 ḡ 拉高、把 p_same 拉低，
        两个量**负相关** ⟹ `T = ḡ/(1−p)` **系统性高估**。
        """
        outb = []
        for lo, hi in GAP_BUCKETS:
            sel = [(g, s) for g, s in pairs if (g <= hi if lo <= 0 else lo < g <= hi)]
            if not sel:
                continue
            p = sum(1 for _, s in sel if s) / float(len(sel))
            d = sum(g for g, _ in sel) / float(len(sel))
            outb.append(dict(lo=lo, hi=hi, n=len(sel), p=p, gap_mean_min=d,
                             T=(d / (1.0 - p) if p < 1.0 else None)))
        # ★★ Σn 自洽检查：分桶必须**覆盖全部 pair**。
        #    改桶边界 / 改端点含不含 / 漏掉一个桶，都会让一部分 pair **掉出所有桶**，
        #    而后果是"主导桶份额"的**分母变小、份额虚高**，且**不报错**。
        #    实测踩过：用户第四轮报的首桶 n=10148 与第三轮的 (0,5]=10098 +(5,10]=164
        #    对不上账 —— 桶边界不明确时这种账根本对不上。所以这里把 Σn 与总数
        #    一起交给上层去对。
        return outb, len(pairs), sum(b["n"] for b in outb)

    def _mk(f):
        d = out.get(f) or {}
        bk, n_bk_in, n_bk_cov = gap_buckets(d.get("pairs") or [])
        # 分桶的 Ts 是否随 gap 单调上升 —— 是则"单一 T"不成立
        ts_ = [b["T"] for b in bk if b["T"] is not None]
        rising = len(ts_) >= 3 and all(x <= y * 1.05 for x, y in zip(ts_, ts_[1:]))
        # 主导桶（样本最多的那个）的 T —— 这才是"主证据"，而不是总体 ḡ 那个
        dom = max(bk, key=lambda b: b["n"]) if bk else None
        return dict(implied=implied(d.get("p"), d.get("gap_mean_min")),
                    implied_wrong=implied(d.get("p"), dt_med),
                    gap_mean_min=d.get("gap_mean_min"),
                    gap_med_min=d.get("gap_med_min"),
                    buckets=bk,
                    # ★ 分桶覆盖率：Σ桶 n 必须 == pair 总数，否则份额的分母是错的
                    n_bucket_in=n_bk_in, n_bucket_cov=n_bk_cov,
                    bucket_covers_all=(n_bk_in == n_bk_cov),
                    T_rising=rising,
                    T_dominant=(dom or {}).get("T"),
                    dom_bucket=(dom or {}))

    sm, cm = _mk("score"), _mk("change_pct")
    return dict(p_same=out, p_all=p_all, n_trans=n_trans,
                n_repeat=reps, p_repeat=(reps / float(n_trans) if n_trans else None),
                # ★ score 层的重复率 —— **这才是"粘滞"那个数**
                #   ⚠️ 它是**配对口径**（要求榜成员也一模一样），**不是**"分数集合"。
                n_repeat_score=reps_sc,
                p_repeat_score=(reps_sc / float(n_trans) if n_trans else None),
                # ★★ 另外三种口径 —— 必须一起印，否则跨轮引用会串味
                n_repeat_score_ms=reps_sc_ms,
                p_repeat_score_ms=(reps_sc_ms / float(n_trans) if n_trans else None),
                n_repeat_score_set=reps_sc_set,
                p_repeat_score_set=(reps_sc_set / float(n_trans) if n_trans else None),
                n_repeat_width=reps_w,
                p_repeat_width=(reps_w / float(n_trans) if n_trans else None),
                exp_repeat=exp_rep, width_hist=width_hist,
                n_states=len(tss) - reps, n_snapshots=len(tss),
                # ★ 有效样本量必须按 **score 层** 数 —— 过采样是 score 粘滞造成的
                n_states_score=len(tss) - reps_sc,
                dt_med_min=dt_med, gap=dict(score=sm, change_pct=cm),
                implied_score_min=sm["implied"],
                implied_chg_min=cm["implied"],
                # ⛔ 用采样间隔当代替 Δ 会得到的错答案 —— 显式留着，好对照
                implied_score_min_wrong=sm["implied_wrong"],
                implied_chg_min_wrong=cm["implied_wrong"])


def bonferroni_table(ks=(3, 4, 5, 6)):
    """双侧 α=0.05/k 的 |t| 阈值。k 必须用【有效规格数】。"""
    nd = NormalDist()
    return {k: nd.inv_cdf(1.0 - 0.05 / (2.0 * k)) for k in ks}



def input_fingerprint(snaps, floor_score=80.0, floor_chg=40.0):
    """这份 CSV 是【完整榜单快照】，还是【采集端已过滤的候选日志】？

    【为什么必须问】两种情况在现有读数上**输出完全一样**：
      · H1 榜本身就窄      → 每快照 3.7 个标的
      · H2 采集端先过滤再写 → 也是每快照 3.7 个标的
    而两者的处置完全相反：H1 说明 rank<=3 真的没约束（策略本身没救），
    H2 说明 **rank 在上游是存在的，只是被采集端在写盘时丢掉了** ——
    后者只要改采集端就能拿回来。

    【判别法】假设最少的那种：看**低尾**。
      · 写盘前若有 `score >= 80` 过滤 → 最小 score 会贴着 80，且低尾有硬地板。
      · 写盘前若有 `change_pct >= 40` 过滤 → 最小涨幅贴着 40。
      · 无过滤 → 最小值离阈值有一段距离，低尾是连续的一片。

    ⚠️ 只看最小值有歧义（"榜上本来就都很强"也给出高最小值）。
       所以这里同时输出**低尾 10 个原值** —— 硬地板一眼可辨。
    ⚠️ 这是一个**指纹**，不是证明。它的价值在于：如果指纹命中，
       你就知道该去采集端找那一行过滤条件，而不是去怀疑策略。

    ⚠️ **命名阈值（80/40）只是"采集端有没有套用策略自己的门槛"这一条检查。**
       真实地板可能在任何位置 —— 用户实测在 **60**，而只探 80/40 的实现
       **报"无地板"，是假阴性**（"报警器的量程太窄，等于没有报警器"）。
       → 所以每列**额外跑一次 `floor_scan()` 自动扫描**，并报出它的性质。
    """
    def blk(field, floor):
        sc = floor_scan(snaps, field)
        if sc is None:
            return dict(n=0, mn=None, tail=[], n_below=None, floor=floor, hit=None,
                        kind=None, scan=None, sweep={}, low20=[], n_distinct_low20=0,
                        n_at_min=0, n_sym_at_min=0, top_sym=None, n_top_sym_at_min=0,
                        span_top_h=0.0, n_pile=0, base=None, gap_to_base=None,
                        nearest_ten=None, gap_near=None, on_round=None,
                        n_pair_other=0, n_move_other=0)
        sc = dict(sc)
        sc.update(floor=floor, tail=_tail(sc["low20"]),
                  n_below=sc["sweep"].get(floor),
                  # 命中 = 最小值贴着命名阈值（容差 0.5，覆盖浮点与四舍五入）
                  hit=(sc["mn"] >= floor - 0.5))
        return sc

    g = {}
    for s in snaps:
        g.setdefault(s["ts"], []).append(s)
    widths = sorted(len(v) for v in g.values())

    # ── 宽度直方图：中位数会掩盖右尾 ──
    # 实测教训（用户 2026-09-16 的读数）：中位 3 但 rank<=3 只覆盖 72.8% 的行
    #   → 差出来的 27.2% 全在宽度 >=4 的快照里 → 那里 rank<=3 是【真过滤】。
    #   只看中位数会得出"rank 不起作用"的错误结论。
    hist = {}
    for v in widths:
        hist[v] = hist.get(v, 0) + 1
    n_wide = sum(1 for v in widths if v >= 4)
    rows_wide = sum(v for v in widths if v >= 4)

    # ── 重复行检测（行级 vs 去重级）──
    # sel_n 等所有"命中集"都按 (ts, symbol) **去重**计数，而 n_rows 按**行**计。
    # 两者不等 ⟺ 同一快照里有重复行 ⟹ 所有按行算的读数都被污染。
    # （本项目在 OI 平台已栽过一次：`oi_hist` 目录残留 → n_dup_pairs = 403200
    #   → sqrt 斜率从 0.501 崩到 0.242。同一个病。）
    n_pairs = len({(s["ts"], s["symbol"]) for s in snaps})
    n_rank3_rows = sum(1 for s in snaps if s["rank"] is not None and s["rank"] <= 3)

    return dict(score=blk("score", floor_score), chg=blk("change_pct", floor_chg),
                n_rows=len(snaps), n_snapshots=len(widths),
                mean_width=(len(snaps) / float(len(widths))) if widths else None,
                width_max=(widths[-1] if widths else None),
                hist=hist, n_wide=n_wide, rows_wide=rows_wide,
                widest=widths[-5:][::-1],
                n_pairs=n_pairs, n_dup=len(snaps) - n_pairs,
                n_rank3_rows=n_rank3_rows,
                n_multi=(sum(1 for v in g.values() if len(v) >= 2) if g else 0))


def row_order_vs_score(snaps):
    """同一快照内，CSV 的【原始行序】是否等于 score 降序？

    ⛔ **这条读数【没有检定力】—— 不能当"上游有 rank"的证据。**
    两种情形输出完全一样：
      H1 接口返回的就是名次序              → 100%
      H2 采集端写盘前自己按 score 排过序   → **也是 100%**
    H2 成立时，这个 100% 是**我们自己造的**（拿自己的 ORDER BY 当上游行为）→
    **循环论证**，比"没有检定力"更糟。

    → 它只能把问题【改写】成「写盘代码为什么不写它」，**不能定案**。
      定案只能读采集端代码：有没有 sorted(...) / ORDER BY / sort_values。
    → 措辞纪律：只许写"两种情形下都可按 score 降序复算"，不许写"证明上游有 rank"。

    单行快照无法判别 → 单独计数。
    ⚠️ 如果多行快照很少（tot 小），这条检查**没有检定力** ——
       一个从没响过的报警器不是报警器。所以 tot 必须一起报出来。
    """
    g = {}
    for s in snaps:
        g.setdefault(s["ts"], []).append(s)
    # `_ord` 由 read_score() 写入（落盘原始行序）。若调用方直接构造 dict
    # 没有 `_ord`，就用**它在输入列表里的位置**兜底 —— 不抛异常、也不静默跳过。
    # （夹具抓到的：硬取 x["_ord"] 会让整个体检 KeyError 崩掉。）
    pos = {id(s): i for i, s in enumerate(snaps)}
    eq = tot = 0
    for ts, rows in g.items():
        if len(rows) < 2:
            continue
        tot += 1
        by_row = sorted(rows, key=lambda x: x.get("_ord", pos[id(x)]))
        by_score = sorted(rows, key=lambda x: (-(x["score"] if x["score"] is not None
                                                 else -1e18), x["symbol"]))
        if [x["symbol"] for x in by_row] == [x["symbol"] for x in by_score]:
            eq += 1
    return eq, tot


def rank_probe(snaps, rank_max=3):
    """每行的 rank 来源溯源 → 写 shadow_rank_probe.csv。

    这张表的作用：把"rank<=3 到底过不过滤"变成**可核对的事实**，
    而不是一句判断。列：
      ts, symbol, score, change_pct, rank_derived, width_at_ts, pass_rank
    """
    w = {}
    for s in snaps:
        w[s["ts"]] = w.get(s["ts"], 0) + 1
    out = []
    for s in snaps:
        out.append(dict(
            ts=iso(s["ts"]), symbol=s["symbol"],
            score=("" if s["score"] is None else round(s["score"], 4)),
            change_pct=("" if s["change_pct"] is None else round(s["change_pct"], 4)),
            rank_derived=("" if s["rank"] is None else int(s["rank"])),
            width_at_ts=w[s["ts"]],
            pass_rank=int(bool(s["rank"] is not None and s["rank"] <= rank_max))))
    return out


def diagnose(snaps, combos, width_warn=4.0, overlap_warn=0.9,
             floor_score=80.0, floor_chg=40.0):
    """
    实验有效性体检（**不需要 K 线**）。

    回答三个会让整轮影子跟踪变成白干的问题：
      ① 每快照只有 ~4 个标的时，`rank <= 3` 几乎不构成约束
         → p0 / s1 / s3 的选择集可能几乎相同
      ② 若两个组合的命中集 Jaccard >= 0.9，**比较它们没有信息量**
         （同一件事测两遍 = 噪声翻倍、信息不翻倍）
      ③ 这份 CSV 是完整榜单，还是采集端已过滤的候选日志
         → 决定 rank 是"拿不到"还是"被丢了"
    """
    width = snapshot_widths(snaps)
    out = dict(n_rows=len(snaps), n_snapshots=len(width), n_symbols=len({s["symbol"] for s in snaps}))
    out["width"] = dict(min=width[0], p25=pctile(width, 0.25), med=pctile(width, 0.5),
                        p75=pctile(width, 0.75), max=width[-1]) if width else None
    for f in ("score", "change_pct"):
        xs = sorted(s[f] for s in snaps if s.get(f) is not None)
        out[f] = dict(n=len(xs), min=xs[0], p25=pctile(xs, .25), med=pctile(xs, .5),
                      p75=pctile(xs, .75), max=xs[-1]) if xs else None
    # 单项过滤器的通过率
    tot = len(snaps)
    out["pass_rate"] = {}
    if tot:
        out["pass_rate"]["rank<=3"] = sum(1 for s in snaps if s["rank"] is not None and s["rank"] <= 3) / tot
        out["pass_rate"]["score>=80"] = sum(1 for s in snaps if s["score"] is not None and s["score"] >= 80) / tot
    # 组合命中集与两两重叠
    sel = {n: select_pairs(snaps, COMBOS[n]) for n in combos}
    out["sel_n"] = {n: len(v) for n, v in sel.items()}
    pairs = []
    for i, a in enumerate(combos):
        for b in combos[i + 1:]:
            j = jaccard(sel[a], sel[b])
            if j is not None:
                pairs.append((a, b, j))
    out["overlap"] = sorted(pairs, key=lambda t: -t[2])
    out["width_warn"] = (out["width"] is not None and out["width"]["med"] <= width_warn)
    out["degenerate"] = [(a, b, j) for a, b, j in out["overlap"] if j >= overlap_warn]
    # 新增：输入画像 + 写盘端过滤指纹 + 行序证据
    out["fp"] = input_fingerprint(snaps, floor_score, floor_chg)
    out["roword"] = row_order_vs_score(snaps)
    # 新增：粘滞（采样频率 vs 上游更新频率）+ Bonferroni 阈值表
    out["sticky"] = stickiness(snaps)
    out["bonf"] = bonferroni_table()
    # 有效规格数 = 把 Jaccard >= overlap_warn 的退化对**合并**后的等价类个数
    # （用并查集，不是简单相减 —— 三个两两退化的组合只应合掉 2 个，不是 3 个）
    parent = {n: n for n in combos}

    def _find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b, _j in out["degenerate"]:
        ra, rb = _find(a), _find(b)
        if ra != rb:
            parent[ra] = rb
    cls = {}
    for n in combos:
        cls.setdefault(_find(n), []).append(n)
    out["spec_classes"] = sorted(cls.values())
    out["n_spec_classes"] = len(cls)
    return out


def print_diagnosis(dg, width_warn=4.0, overlap_warn=0.9):
    L = []
    L.append("=== 输入体检：这个实验测得出东西吗？===")
    L.append("行 %d / 快照 %d 个 / 标的 %d 个"
             % (dg["n_rows"], dg["n_snapshots"], dg["n_symbols"]))
    w = dg["width"]
    if w:
        L.append("每快照标的数：min %.0f / p25 %.0f / **中位 %.0f** / p75 %.0f / max %.0f"
                 % (w["min"], w["p25"], w["med"], w["p75"], w["max"]))
    for f, label in (("score", "score"), ("change_pct", "涨幅%")):
        d = dg[f]
        if d:
            L.append("%s：n=%d  min %.2f / p25 %.2f / 中位 %.2f / p75 %.2f / max %.2f"
                     % (label, d["n"], d["min"], d["p25"], d["med"], d["p75"], d["max"]))
        else:
            L.append("%s：**整列都是空**" % label)
    pr = dg["pass_rate"]
    if pr:
        L.append("单项通过率：rank<=3 → %.1f%%   score>=80 → %.1f%%"
                 % (100 * pr.get("rank<=3", 0), 100 * pr.get("score>=80", 0)))

    # ── [新] 写盘端过滤指纹（自动扫地板，不硬编码阈值）──
    fp = dg.get("fp")
    KIND_DOC = {
        "natural": "低尾连续、**值没有被赋值** → 不是 clamp、不是 freeze。"
                   "**但低尾本身分不出 `filter` 与 `natural`**（过滤只删行、不改值，"
                   "幸存值的最小值跟天然下界长得一样）→ 见下面【命名阈值检查】+ 采集端那几行。",
        "clamp": "堆积**跨多个标的**（不同标的的真实分数不可能都等于同一个常数）→ 这个值是被"
                 "【赋值】的 → 打分器**量程下限**。低分段失去分辨力；因所有阈值都 ≥70 → **对本轮无害**。",
        "freeze": "堆积全来自**同一个标的的连续时点、且整行一字不变** → "
                  "**不是地板，是采集端卡住/上游停更**。",
        "filter_or_clamp": "堆积**跨多个标的**，但值**正好是整十值** → 可能是"
                           "「整数化后的分数 + 手写阈值」，也可能是「量程下限恰好=整十值」。"
                           "**数据分不开** → 去采集端看那几行。",
        "ambiguous": "堆积**只在 1 个标的**上 → ⛔ **粘滞与赋值同形，判不了**："
                     "score 是粘滞的（`p_same ≈ 0.83`），同一个标的连续多屏取到同一个值"
                     "在 `filter` 假设下**也是必然的**。**去采集端看那几行代码。**",
    }
    # 证据 1 的【读法】按 kind 分 —— 同一个"堆积"数字在不同 kind 下含义不同：
    # `clamp` 的堆积 = 值被赋值；`freeze` 的堆积 = 整屏逐字重复；单标的的堆积 = 粘滞。
    # ⚠️ 必须是**函数**而不是常量 dict：里面要插 `n_sym_at_min`，而 b 是循环变量。
    def _ev1_text(kind, n_sym):
        return {
            "clamp": "→ 堆积**跨 %d 个标的** ⟹ 不同标的的真实分数不可能都等于同一个常数 "
                     "⟹ 这个值是**被【赋值】的**（过滤只删行、不会让多行变成同一个值）"
                     "⟹ **排除 filter**" % n_sym,
            "filter_or_clamp": "→ 堆积**跨 %d 个标的** ⟹ 值是**被【赋值】的** ⟹ 排除"
                               "**连续分数上的** filter；但堆积值**正好是整十值** ⟹ 仍可能是"
                               "「整数化后的分数 + 手写阈值」⟹ **分不开**" % n_sym,
            "freeze": "→ 这里的重复来自**整屏逐字重复** ⟹ 是**采集端卡住/上游停更**，"
                      "**不是地板**",
            "natural": "→ **没有堆积** ⟹ 值没有被赋值；**但过滤只删行、不改值，"
                       "与天然下界在低尾上同形** ⟹ 低尾本身**分不出 filter 与 natural**",
            "ambiguous": "→ 堆积**只在 1 个标的**上 ⟹ ⛔ **粘滞与赋值同形**：score 的 "
                         "`p_same ≈ 0.83`，同一个标的连续多屏不变是**必然**的，与「被赋值」"
                         "完全不可分 ⟹ **单标的堆积判不了**（「其他字段在动」也救不了 —— "
                         "那是粘滞的正常表现）",
        }.get(kind, "→ **判不出**，去看采集端那几行")
    if fp:
        L.append("")
        L.append("── 这份 CSV 是【完整榜单】还是【采集端过滤后的候选日志】？──")
        L.append("  【自动地板扫描】不硬编码阈值 —— 真实地板可能在任何位置。")
        for key, label in (("score", "score"), ("chg", "涨幅%")):
            b = fp[key]
            if not b["n"]:
                L.append("  %s：整列为空，无法判别" % label)
                continue
            L.append("  %s：min = %.4f ｜ 最近的整十值 %.0f（差 %+.4f）"
                     % (label, b["mn"], b["nearest_ten"], b["gap_near"]))
            sw = b["sweep"]
            L.append("      阈值扫描（低于 T 的行数）：%s"
                     % "  ".join("<%d→%d" % (t, sw[t]) for t in sorted(sw)))
            L.append("      低尾 20 个值里有 **%d 个不同值**（1=全同，20=全不同）｜"
                     " [min, min+%.0f) 里有 %d 行 ｜ 最小值涉及 **%d 个标的**"
                     % (b["n_distinct_low20"], b["pile_w"], b["n_pile"], b["n_sym_at_min"]))
            if b["top_sym"]:
                L.append("      最小值里最多的是 **%s**（%d 行，跨度 %.2f 小时）"
                         % (b["top_sym"], b["n_top_sym_at_min"], b["span_top_h"]))
            L.append("      低尾 10 个原值：%s" % ", ".join("%.4f" % v for v in b["tail"]))
            L.append("      **判定：%s** —— %s" % (b["kind"], KIND_DOC.get(b["kind"], "")))
            if b["n_at_min"] and b["n_sym_at_min"]:
                L.append("      ▸ 证据 1（堆积**跨几个标的**？← 这一条决定能不能排除 filter）：")
                L.append("         最小值上有 **%d 行逐位相同**（占全部 %d 行的 %.2f%%），"
                         "**涉及 %d 个标的**"
                         % (b["n_at_min"], b["n"], 100.0 * b["n_at_min"] / b["n"],
                            b["n_sym_at_min"]))
                L.append("         整个字段 %d 个不同值（整体重复率 %.2f%%）｜"
                         "重复最多的【非最小值】出现 %d 次"
                         % (b["n_distinct_all"], 100.0 * b["rep_all"], b["n_second_max"]))
                # ⚠️ 证据 1 的【读法】必须跟着 kind 走 —— 同一个数字在不同 kind 下
                #    含义完全不同（`freeze` 的堆积是整屏重复；单标的的堆积是粘滞）。
                L.append("         %s" % _ev1_text(b["kind"], b["n_sym_at_min"]))
                L.append("      ▸ 证据 2（整十值？**辅助**，只在「堆积已跨多标的」时才有用）："
                         "min 离最近的整十值 %.0f 差 %+.6f → %s"
                         % (b["nearest_ten"], b["gap_near"],
                            "**正好是整十值**（→ 仍可能是「整数化分数+手写阈值」，分不开）"
                            if b["on_round"] else
                            "**不是整十值**（单独看**不能**排除 filter —— "
                            "`score>=60` 作用在连续分数上同样落在 60.00xx）"))
                L.append("      ▸ 证据 3（整行动没动？**只用来识别 freeze**）：堆积行里相邻对的"
                         "**其他字段**（涨幅/价格/峰值）有 %d / %d 对发生了变化"
                         % (b["n_move_other"], b["n_pair_other"]))
                if b["n_pair_other"]:
                    L.append("         %s"
                             % ("→ 行在更新 ⟹ **不是 freeze**。"
                                "但⛔ 这**不代表**是 clamp —— `change_pct` 每 5 分钟变、"
                                "`score` 小时级才变，**「score 不动而涨幅在动」正是粘滞的正常表现**"
                                if b["n_move_other"] else
                                "→ 整行一字不变 ⟹ 是 **freeze**（采集端卡住/上游停更），不是地板"))
        L.append("  【命名阈值检查】采集端有没有套用策略自己的门槛：")
        for key, label in (("score", "score"), ("chg", "涨幅%")):
            b = fp[key]
            if not b["n"] or b["n_below"] is None:
                continue
            if b["hit"]:
                L.append("      ⚠️ %s：最小值落在 %.0f 附近（容差 0.5）—— **提示**：写盘端可能有 "
                         "`%s >= %.0f` 过滤。" % (label, b["floor"], label, b["floor"]))
                L.append("         但这是**提示不是判据**：连续型字段的最小值也可能恰好落在整十值附近。")
                # ⚠️ 这里曾写「**有堆积** ⟹ 排除 filter」—— **错的**：
                #    单标的的堆积与粘滞同形（`p_same≈0.83`），什么也排除不了。
                L.append("         要定案看上面的【证据 1】：**堆积跨 ≥3 个标的** ⟹ 排除 filter；"
                         "**只堆在 1 个标的上 / 没有堆积** ⟹ 都分不出，去采集端看那几行。")
            else:
                L.append("      ✅ %s：低于 %.0f 的有 %d / %d 条（%.2f%%）→ 该阈值上无地板。"
                         % (label, b["floor"], b["n_below"], b["n"],
                            100.0 * b["n_below"] / b["n"]))
        L.append("  ⚠️ 分离 `clamp` / `filter` / `natural` —— **只看一件事：堆积跨几个标的**")
        L.append("     ① **堆积跨 ≥3 个标的** ⟹ 不同标的的真实分数不可能都等于同一个常数")
        L.append("        ⟹ 该值被**赋值** ⟹ **排除 filter**（过滤只删行、不会让多行变成同一个值）。")
        L.append("     ② **堆积只在 1 个标的**上 ⟹ ⛔ **粘滞与赋值同形，判不了** ——")
        L.append("        score 是粘滞的（`p_same ≈ 0.83`），同一标的连续多屏不变在 filter 下")
        L.append("        **也是必然的**。**「其他字段在动」救不了**：`change_pct` 每 5 分钟变、")
        L.append("        `score` 小时级才变，两者不同节奏 —— 那正是粘滞的正常表现。")
        L.append("     ③ **整行一字不变** ⟹ `freeze`（采集端卡住/上游停更），与地板无关。")
        L.append("     ⚠️ 辅助：堆积值**是不是整十值** —— 只在 ① 成立时才用来分")
        L.append("        `clamp`（怪值）与 `filter_or_clamp`（整十值）。单独看它**不能**排除 filter。")
        if fp["mean_width"] is not None:
            L.append("  平均每快照 %.2f 行（%d 行 / %d 时点）；最宽的几个快照 %s 行"
                     % (fp["mean_width"], fp["n_rows"], fp["n_snapshots"],
                        "/".join(str(x) for x in fp["widest"])))
            h = fp["hist"]
            keys = sorted(h)
            n_wide, rows_wide = fp["n_wide"], fp["rows_wide"]
            narrow = [k for k in keys if k <= 3]
            L.append("  宽度分布（宽度→快照数）：%s"
                     % ("  ".join("%d→%d" % (k, h[k]) for k in narrow) if narrow
                        else "（没有宽度<=3 的快照）"))
            if n_wide:
                L.append("  **宽度 >=4 的快照 %d 个（占快照 %.1f%%），贡献 %d 行 = 全部行的 %.1f%%**"
                         % (n_wide, 100.0 * n_wide / fp["n_snapshots"], rows_wide,
                            100.0 * rows_wide / fp["n_rows"]))
                hidden = w and w["med"] <= 3
                L.append("  %s在那些宽快照里 `rank<=3` 是【真过滤】："
                         % ("⚠️ **中位数掩盖了这个右尾**。" if hidden else "注意："))
                L.append("     宽度>3 多出来的行数 = **%d**（这些行 rank 必然 >3）。"
                         % sum((k - 3) * h[k] for k in keys if k > 3))
                L.append("     实测 rank<=3 的行数 = %d / %d = %.1f%%。"
                         % (fp["n_rank3_rows"], fp["n_rows"],
                            100.0 * fp["n_rank3_rows"] / fp["n_rows"]))
                if hidden:
                    L.append("     → 「rank<=3 几乎不起作用」这个结论**只在窄快照里成立**，")
                    L.append("       不能拿中位数当全局结论。宽快照才是 rank 真正干活的地方。")
            else:
                L.append("  ✅ 没有任何快照宽 >=4 → `rank<=3` 在所有快照里都恒真，")
                L.append("     即：**这个 rank 条件在这份数据上完全没有约束力**。")
            L.append("  ⚠️ 地板扫描是**指纹**不是证明：榜单本身很强的日子也会给出高最小值。")
            L.append("     它的用法是——命中就去采集端找那行过滤条件，而不是去怀疑策略。")
        # ── 重复行检测 ──
        if fp.get("n_dup"):
            L.append("  ❌ **同一快照里有重复行**：%d 行 / %d 个不同 (时点,标的) → 多出 %d 行。"
                     % (fp["n_rows"], fp["n_pairs"], fp["n_dup"]))
            L.append("     所有按行计的读数都被污染（命中集是按 (时点,标的) 去重数的，")
            L.append("     所以 sel_n 与 n_rows 会对不上）。先清重复再解读。")
        else:
            L.append("  ✅ 无重复行：%d 行 = %d 个不同 (时点,标的)。" % (fp["n_rows"], fp["n_pairs"]))
        # ── 自洽断言（免费）：总行数 − rank<=3 的行数 必须 == 宽度>3 多出来的行数 ──
        # 只在 rank 是【推导】出来的时候成立（推导把 1..w 都填满）。
        # 若 rank 来自列且名次有跳号，这条会不等 —— 那本身就是有用的信号。
        if fp.get("hist"):
            br = sum((k - 3) * v for k, v in fp["hist"].items() if k > 3)
            excl = fp["n_rows"] - fp["n_rank3_rows"]
            if excl == br:
                L.append("  ✅ 自洽：总行 %d − (rank<=3 的行) %d = %d = 宽度>3 多出来的行数。"
                         % (fp["n_rows"], fp["n_rank3_rows"], br))
            else:
                L.append("  ⚠️ **不自洽**：总行 − (rank<=3 的行) = %d，但宽度>3 多出来的行数 = %d。"
                         % (excl, br))
                L.append("     说明 rank 不是「每个快照 1..w 填满」的推导值（可能有跳号/并列/来自列），")
                L.append("     或者存在重复行。→ 这会改变 p0/s1/s3 的选择集，先查清楚再解读重叠矩阵。")
        L.append("  ⚠️ 「每快照 3.7 个标的」这一读数**无法区分**下面两件事：")
        L.append("     H1 榜本身就窄   → rank<=3 真的没约束（策略本身的问题）")
        L.append("     H2 采集端先过滤再写 → 榜上原本更多标的（`n_board` 更大），")
        L.append("        rank 是在**更大的榜**上算的，写盘时连榜宽一起丢了")
        L.append("     两者输出一模一样。判别看上面的【地板判定】：")
        L.append("     `clamp`（堆积**跨多标的**）= **H1**（有检定力）；")
        L.append("     `freeze` = 采集端卡住，与 H1/H2 无关；")
        L.append("     `natural` / `ambiguous`（单标的堆积）/ `filter_or_clamp` = **判不了** ——")
        L.append("     唯一出路是去采集端看写盘那几行。**不要**把 `ambiguous` 读成 H1。")

    # ── [新] 粘滞：采样频率 vs 上游更新频率 ──
    st = dg.get("sticky")
    if st:
        L.append("")
        L.append("── score 粘滞：我们采样的比上游更新快多少？──")
        for f, label in (("score", "score"), ("change_pct", "涨幅%"), ("price", "price")):
            d = st["p_same"].get(f)
            if not d or not d["n"]:
                continue
            L.append("  %-7s 相邻【出现】不变：%d / %d = **%.1f%%**"
                     % (label, d["same"], d["n"], 100.0 * d["p"]))
        L.append("  快照网格间隔中位数 = %s 分钟"
                 % ("%.2f" % st["dt_med_min"] if st["dt_med_min"] else "—"))
        for f, label in (("score", "score"), ("change_pct", "涨幅%")):
            g = (st.get("gap") or {}).get(f) or {}
            if not g.get("gap_mean_min"):
                continue
            L.append("  %s 的相邻【出现】间隔：均值 %.2f 分钟 / 中位 %.2f 分钟"
                     % (label, g["gap_mean_min"], g["gap_med_min"] or 0.0))
        for f, label in (("implied_score_min", "score"), ("implied_chg_min", "涨幅%")):
            v = st.get(f)
            if v:
                L.append("  → **隐含上游更新周期 ≈ %.0f 分钟（%.1f 小时）**（%s）"
                         % (v, v / 60.0, label))
        L.append("     模型：周期性刷新 + 相位均匀 → T = Δ/(1−p_same)，")
        L.append("     **Δ 用【同一标的相邻两次出现】的平均间隔，不是采样间隔。**")
        # ⛔ 把"用错 Δ 会得到的答案"显式印出来，好让人一眼看出差别有多大。
        #    注意两者可能有一个是 None（p_same=1 时无解）→ 不许直接 max(None, x)。
        w2, v2 = st.get("implied_score_min_wrong"), st.get("implied_score_min")
        if w2 and v2:
            L.append("     ⛔ 若错用采样间隔（%.0f 分钟）当代替 → 会得到 %.0f 分钟，"
                     "比正解**小 %.1f 倍**（%.0f vs %.0f）。"
                     % (st["dt_med_min"] or 0.0, w2,
                        max(w2, v2) / max(min(w2, v2), 1e-9), w2, v2))
        elif w2:
            L.append("     ⛔ 若错用采样间隔（%.0f 分钟）当代替 → 会得到 %.0f 分钟"
                     "（正解无意义：p_same 太高）。" % (st["dt_med_min"] or 0.0, w2))
        L.append("  ⚠️⚠️ **这个反解会系统性【高估】。** 它假设刷新相位与采样相位无关；")
        L.append("     若两者有固定相位关系（周期刷新就会这样），估计值能偏高数倍。")
        L.append("     （已用已知真值的夹具验过：真周期 55 分钟 → 它给出 338 分钟。）")
        L.append("     → **T 只当【量级】读，不要当数字引用。**")
        # ── ★ 按 gap 分桶：检验"单一 T"这个模型本身 ──
        gs = (st.get("gap") or {}).get("score") or {}
        bk = gs.get("buckets") or []
        if len(bk) >= 2:
            L.append("")
            L.append("  ★ 按【出现间隔】分桶的 p_same（检验「单一 T」这个模型成不成立）：")
            for b in bk:
                if b["hi"] == float("inf"):
                    _edge = ">%.0f" % b["lo"]
                elif b["lo"] <= 0:
                    _edge = "≤%.0f" % b["hi"]
                else:
                    _edge = "(%.0f, %.0f]" % (b["lo"], b["hi"])
                L.append("     gap %-9s p_same=%.3f  n=%-6d 桶内 δ≈%-6.1f 分 → 桶内 T≈%s"
                         % (_edge, b["p"], b["n"], b["gap_mean_min"],
                            "%.0f 分" % b["T"] if b["T"] else "—"))
            # ★★ 分桶覆盖率自洽检查 —— 少了它，"主导桶份额"的分母错了也看不出来
            _in, _cov = gs.get("n_bucket_in") or 0, gs.get("n_bucket_cov") or 0
            if _in and gs.get("bucket_covers_all"):
                L.append("     ✅ 自洽：Σ桶 n = %d = pair 总数（分桶**覆盖全部**，没有 pair 掉出）"
                         % _cov)
            elif _in:
                L.append("     ⛔ **Σ桶 n = %d ≠ pair 总数 %d** —— 有 **%d 个 pair 掉出所有桶**："
                         % (_cov, _in, _in - _cov))
                L.append("        下面的「主导桶份额」分母偏小、份额**虚高**。**先修桶边界再看份额。**")
            dom = gs.get("dom_bucket") or {}
            if gs.get("T_rising"):
                L.append("     ⛔ **桶内 T 随 gap 单调上升** ⟹ **「固定周期刷新 + 单一 T」不成立**，")
                L.append("        真实机制是自相关/多尺度刷新。此时**总体 ḡ 反解出的 T 有偏**：")
                L.append("        长 gap 桶的 p_same 低 ⟹ 把长 gap 混进来会把 ḡ 拉高、把 p_same 拉低，")
                L.append("        两个量**负相关** ⟹ `T = ḡ/(1−p)` **系统性高估**。")
            if dom:
                n_bk = sum(x["n"] for x in bk)
                L.append("     → **主导桶是 gap %.0f–%.0f（占 %.1f%%）**，它给出的 **T ≈ %.0f 分钟**"
                         "才更可信；"
                         % (dom.get("lo", 0),
                            dom.get("hi", 0) if dom.get("hi") != float("inf") else 9999,
                            100.0 * dom.get("n", 0) / max(1, n_bk),
                            dom.get("T") or 0.0))
                L.append("        总体 ḡ 那个 T 是**混合口径**，不要引用它。")
        L.append("  ⚠️ 这个反解**依赖「周期性刷新」这个模型**。若 score 被量化（四舍五入到某一步长），")
        L.append("     即使刚更新也可能取到相同值 → p_same 会**高估**粘滞 → T 被高估。")
        L.append("  ⚠️ 真正要紧的不是 T 是多少，而是：**采样比上游刷新密得多**。")
        L.append("     分数只在小时尺度上变，多出来的时点是同一状态的重复计数。")
        if st["p_repeat"] is not None:
            L.append("")
            L.append("  ⚠️ 下面这个「全榜同步冻结」用的是**全字段**（symbol+score+涨幅+price），")
            L.append("     不是只看 score —— 两者结论可以完全相反，**不要混起来**：")
            _sp = ((st.get("p_same") or {}).get("score") or {}).get("p")
            if _sp is not None:
                L.append("     · score 层：逐标的 score 不变 ≈ %.1f%% 是**粘滞**（上游刷新慢，正常）；"
                         % (100.0 * _sp))
            if st.get("p_repeat_score") is not None:
                # ★★ 四种口径**一起印** —— 跨轮比较串味过一次（74.7% vs 62.9%）。
                #    嵌套关系：(a) ⟹ (b) ⟹ (c)，所以三个数必须**单调不减**；
                #    违反单调 ⟹ 代码有问题（拿这条当免费的自洽检查）。
                _n = st["n_trans"]
                L.append("       ⚠️ score 层「没变」有【四种】口径，是**嵌套**的；"
                         "跨轮引用必须同口径：")
                for _k, _lbl in (("score", "(a) 逐标的 (symbol,score) 配对全同"),
                                 ("score_ms", "(b) 只看分数·多重集（排序含重复）"),
                                 ("score_set", "(c) 只看分数·集合（去重）"),
                                 ("width", "(d) 只看宽度（独立维度）")):
                    _c = st.get("n_repeat_" + _k)
                    _pp = st.get("p_repeat_" + _k)
                    if _c is None or _pp is None:
                        continue
                    # ⚠️ 数字在前、标签在后 —— 标签含中文，`%-Ns` 按**字符数**补齐，
                    #    中英文混排会对不齐。数字列对齐更实用。
                    L.append("         %5d / %d = **%.1f%%**   ← %s"
                             % (_c, _n, 100.0 * _pp, _lbl))
                L.append("         ⟹ 恒有 (a) ≤ (b) ≤ (c)；(a) 与 (c) 的差 = **榜成员变动**贡献的。")
                L.append("         ⚠️ 「分数层约 1.4 倍市场级共振」引用的是 **(a) 那一行**，"
                         "**不是**「score 集合」。")
                L.append("            第三轮 74.7% 与第四轮 62.9% **口径没对齐**（同期只多 9 个快照，"
                         "不可能掉 333 次）→ 以本表为准，重跑一次即可对齐。")
                L.append("         ⚠️ 但它**不是**采集端卡住 —— 采集端卡住看下面的【全字段层】。")
            L.append("     · 全字段层不变 = **采集端卡住 / 上游整体停更**（才要报警）。")
            L.append("  全榜同步冻结（全字段）：与前一个快照**完全相同**的时点 %d / %d = **%.1f%%**"
                     % (st["n_repeat"], st["n_trans"], 100.0 * st["p_repeat"]))
            if st["exp_repeat"] is not None:
                L.append("  独立粘滞下的期望重复率 = %.1f%%（Σ_w (n_w/N)·p_all^w，p_all=%.3f）"
                         % (100.0 * st["exp_repeat"], st["p_all"]))
                if st["p_repeat"] > st["exp_repeat"] + 0.05:
                    L.append("  ⚠️ **实测显著高于期望 → 存在同步冻结**（不只是各自独立地粘）：")
                    L.append("     采集端卡住 / 上游整体停更。这些时点不是独立观测。")
                elif st["p_repeat"] < st["exp_repeat"] - 0.05:
                    L.append("  ⚠️ **实测显著低于期望 → 各标的在【轮流】更新**（负相关），")
                    L.append("     也不是独立观测。批量刷新的相位是错开的。")
                else:
                    L.append("  ✅ 实测与独立粘滞的期望相当 → **没有额外的同步冻结**，")
                    L.append("     冻结都是各标的独立发生的。")
            # ⚠️ 有效样本量必须按 **score 层** 数（`n_states_score`），
            #    不能按全字段层 —— 全字段层在真实数据里 0% 重复，会恒等于 100%，
            #    把"过采样"这件事整个抹掉。
            _ns = st.get("n_states_score") or st["n_states"]
            L.append("  **不同状态数（只看 score 层）= %d / %d 个时点** → 有效样本量只有名义的 **%.0f%%**"
                     % (_ns, st["n_snapshots"], 100.0 * _ns / st["n_snapshots"]))
            L.append("     （全字段层：%d / %d = %.0f%% —— 这一层不重复才是正常的，"
                     "它**不**用来算有效样本量）"
                     % (st["n_states"], st["n_snapshots"],
                        100.0 * st["n_states"] / st["n_snapshots"]))
            L.append("  ⚠️ 所有按 `(时点, 标的)` 计的**命中集大小**都被抬高了 ≈ %.1f 倍"
                     % (st["n_snapshots"] / float(_ns) if _ns else 0.0))
            L.append("     → 2152 这类数字**不是 2152 个独立机会**。")
            L.append("     但**交易级 n 不受影响**（24h 冷却已经把重复时点压掉了），")
            L.append("     **入场画像也不受影响**（按标的去重）。所以过采样没有虚增交易样本。")

    # ── [新] Bonferroni 阈值必须按【有效规格数】取 ──
    bf, cls = dg.get("bonf"), dg.get("spec_classes")
    if bf and cls:
        L.append("")
        L.append("── Bonferroni 的 k 取几？──")
        L.append("  退化对合并后的**有效规格**：%s"
                 % "  ".join("{%s}" % ",".join(c) for c in cls))
        L.append("  有效规格数 = **%d**（组合数 %d）" % (dg["n_spec_classes"], len(dg["sel_n"])))
        for k in sorted(bf):
            tag = ""
            if k == len(dg["sel_n"]):
                tag = "  ← 组合数（最保守，虚高）"
            elif k == dg["n_spec_classes"]:
                tag = "  ← 合并退化对后【按规格数报】"
            elif k == dg["n_spec_classes"] - 1:
                tag = "  ← 只报 %d 个 vs p0 的差值" % (dg["n_spec_classes"] - 1)
            L.append("    k=%d → |t| >= %.3f%s" % (k, bf[k], tag))
        L.append("  ⚠️ **k 是你将报告的【独立数字】的个数**，不是组合数：")
        L.append("     报 N 条均值 → k=N；报 M 个差值 → k=M。")
        L.append("     用组合数当 k 会**白白抬高门槛**（自己惩罚自己）；虚报更糟。")

    # ── [新] 行序证据：rank 在上游到底存不存在 ──
    ro = dg.get("roword")
    if ro:
        eq, tot = ro
        L.append("")
        L.append("── rank 在上游存在吗？（同一快照内 CSV 行序 == score 降序？）──")
        if tot == 0:
            L.append("  **每个快照都只有 1 行 → 这条检查没有检定力**（无从判别）。")
            L.append("  一个从没响过的报警器不是报警器 —— 这条现在等于没查。")
        else:
            L.append("  可比快照 %d 个（>=2 行的），其中行序与 score 降序一致的 %d 个 → %.1f%%"
                     % (tot, eq, 100.0 * eq / tot))
            if eq == tot:
                L.append("  ⚠️ **100% 一致** —— 但**这条读数没有检定力**，不能当证据：")
                L.append("     ① 接口返回的就是名次序            → 100%；")
                L.append("     ② **采集端写盘前自己按 score 排过序** → **也是 100%**。")
                L.append("     （② 成立时，这个 100% 是**我们自己造的** —— 循环论证。）")
                L.append("     → 它只能把问题【改写】成「写盘代码为什么不写它」，")
                L.append("       **不能**证明上游有 rank。定案只能读采集端代码：")
                L.append("       有没有 sorted(...) / ORDER BY / sort_values，顺序取自哪个字段。")
            elif eq * 2 >= tot:
                L.append("  多数一致 → 采集端大概按分数/排名写盘，但有例外，需看具体是哪些快照。")
            else:
                L.append("  不一致占多数 → 行序不是排名序，`--rank-mode row` 与 `score` 确实是两个规格。")

    L.append("")
    if dg["width_warn"]:
        # ⚠️ 不要只看中位数。中位 3 但宽度有右尾时，rank<=3 仍在真干活。
        #    用【被挡掉的行数】说话 —— 它是中位数的抗体。
        blocked_rows = 0
        if fp and fp.get("hist"):
            blocked_rows = sum((k - 3) * v for k, v in fp["hist"].items() if k > 3)
        L.append("⚠️  每快照标的中位数只有 %.0f 个 —— **窄快照里 `rank <= 3` 不构成约束**。" % w["med"])
        if fp and fp.get("n_wide"):
            L.append("    但宽度有右尾：`rank <= 3` 实际挡掉了 **%d 行 = 全部行的 %.1f%%**"
                     % (blocked_rows, 100.0 * blocked_rows / fp["n_rows"]))
            L.append("    → 所以**不要**把「中位数小」直接读成「rank 没用」。")
            L.append("      它对窄快照没用，对宽快照是真过滤 —— 两者混在一起才是真相。")
        else:
            L.append("    而且没有任何快照宽 >=4 → `rank <= 3` 在这份数据上**恒真**，")
            L.append("      即它一次都没起过作用。")
        L.append("    另外：p0 与 s2（只限 score）的差别才是真正被检验的那一项 → 看重叠矩阵。")
        L.append("")
    L.append("组合命中集大小（原始命中，未考虑持仓/冷却）：")
    for n in dg["sel_n"]:
        L.append("  %-4s %6d" % (n, dg["sel_n"][n]))
    L.append("")
    L.append("两两重叠（Jaccard，1.00 = 完全同一批）：")
    for a, b, j in dg["overlap"]:
        flag = "  ← 退化：测的是同一件事" if j >= overlap_warn else ""
        L.append("  %-4s vs %-4s  %.3f%s" % (a, b, j, flag))
    L.append("")
    if dg["degenerate"]:
        L.append("⚠️  有 %d 对组合的命中集几乎完全相同（Jaccard >= %.2f）。"
                 % (len(dg["degenerate"]), overlap_warn))
        L.append("    比较它们只会让噪声翻倍，不会让信息翻倍。")
        L.append("    处置：要么承认它们是一个规格（只报一条），要么把阈值拉开到能分开为止。")
    else:
        L.append("✅ 没有 Jaccard >= %.2f 的组合对 —— 六个组合确实在挑不同的东西。" % overlap_warn)

    # ── [新] 判定 ──
    L.append("")
    L.append("── 判定：rank 该怎么拿 ──")
    kinds = [fp[k]["kind"] for k in ("score", "chg")] if fp else []
    # ── 三档，严格按【证据 1（堆积）】分，不按"整十值"分 ──
    #    ⚠️ 这里原来用 `hit`（最小值贴命名阈值）直接说"H2 未被排除"，
    #       与【证据 1】的口径不一致 —— 连续型字段的最小值本来就可能落在整十值附近。
    #       现在：`hit` 只当**提示**，定案只看**堆积**。
    undecidable = "filter_or_clamp" in kinds          # 堆积跨多标的但值在整十值上 → 分不开
    clamped = "clamp" in kinds                        # 堆积跨多标的且是怪值 → 已排除 filter
    ambiguous = "ambiguous" in kinds                  # 单标的堆积 → 粘滞与赋值同形
    hint = bool(fp) and ((fp["score"]["n"] and fp["score"]["hit"])
                         or (fp["chg"]["n"] and fp["chg"]["hit"]))
    floor_txt = "/".join([k for k in kinds if k]) or "无"
    # ⚠️ 必须过滤 None：某一列整列为空时 kind 就是 None，
    #    直接 join 会 TypeError 崩掉整个体检。
    #    （夹具的 CLI 冒烟段抓到的 —— 它跑到了一条主路径没跑到的分支。）
    # ⚠️ 优先级：`freeze` 排在最前 —— 它是**采集端有病**（整行一字不变），
    #   一旦出现，这份数据的其它读数都不可信，比地板类型更紧急。
    #   （原来排在 `ambiguous` 之后，于是主列 freeze + 陪衬列 ambiguous 时
    #     会被 ambiguous 顶掉，判定块说出"判不了"而不是"采集端卡住"。）
    if "freeze" in kinds:
        _floor_verdict = ("⛔ **低尾的重复是 `freeze`**（堆积行**整行一字不变**）⟹ "
                          "**采集端卡住 / 上游整体停更**，与 `filter`/`natural` 无关 → "
                          "先修采集端，再谈 rank。")
    elif undecidable:
        _floor_verdict = ("**H2 未能排除**（堆积跨多标的、但值正好是整十值 → "
                          "整数化分数+手写阈值 / 量程下限恰在整十值，**数据分不开**）→ "
                          "去采集端看那几行。")
    elif clamped:
        _floor_verdict = ("**已排除 filter**（堆积**跨多个标的** ⟹ 该值是被【赋值】的，"
                          "而过滤只删行、不会让多行变成同一个值）→ 倾向 H1"
                          "（榜本身窄 / 打分器量程下限）。")
    elif ambiguous:
        _floor_verdict = ("⛔ **判不了**（堆积只在 **1 个标的**上 ⟹ **粘滞与赋值同形**："
                          "score 的 `p_same ≈ 0.83`，同一标的连续多屏不变在 `filter` 下"
                          "**也是必然的**）→ **H1 / H2 都没被排除**，唯一出路是采集端那几行。")
    elif hint:
        _floor_verdict = ("低尾**没有堆积** ⟹ **低尾分不出 filter 与 natural**（过滤只删行、"
                          "不改值）；最小值又落在命名阈值附近 → **H2 既没被排除也没被证实**，"
                          "唯一出路是采集端那几行。")
    else:
        _floor_verdict = ("低尾**没有堆积** ⟹ 值没有被赋值，但**低尾分不出 filter 与 natural**"
                          "（过滤只删行、不改值）→ **H1 / H2 都没被排除**。")
    if ro and ro[1] == 0:
        L.append("  现有文件里【没有一个快照超过 1 行】→ 榜单宽度无从判别，")
        L.append("  但同时也说明：这份 CSV 每时点只留了 1 行，**它一定不是完整榜单**。")
        L.append("  → 直接去采集端找那行过滤条件，别再在策略参数上打转。")
    elif ro and ro[0] == ro[1]:
        L.append("  ① 行序 100% 等于 score 降序 —— ⛔ **但这条读数没有检定力**：")
        L.append("     接口返回名次序 → 100%；**采集端写盘前自己排过序 → 也是 100%**。")
        L.append("     （后者成立时这 100% 是**我们自己造的**，循环论证。）")
        L.append("     → 只能把问题改写成「写盘代码为什么不写它」，**不能**证明上游有 rank。")
        L.append("  ② 地板判定 %s → %s" % (floor_txt, _floor_verdict))
        L.append("  ③ 处置：这个问题**统计上定不了案** → **换证据类型**，别在参数上打转：")
        L.append("     · 读采集端写盘那几行：有没有 `sorted(...)` / `ORDER BY` / `sort_values`？")
        L.append("       有 → 这个 100% 是我们自己造的；没有 → 行序就是上游排名序。")
        L.append("     · 直接调上游接口，换 `limit` / 页码，看返回的标的数变不变。")
        L.append("     ⚠️ **不要**再要求「补落 rank 列」：若采集端零过滤，")
        L.append("        `rank` = 行序、`n_board` = 该快照行数 —— **两者都已可从现有 CSV 复现**，")
        L.append("        加列是白做。真正要问的是「榜为什么这么窄」，而那**在上游**。")
    elif undecidable or ambiguous or hint:
        L.append("  ① 地板判定 %s → %s" % (floor_txt, _floor_verdict))
        L.append("  ② 处置：去采集端看写盘那几行代码（这是唯一能定案的办法），")
        L.append("     不要在策略参数上打转。")
    else:
        L.append("  ① 地板判定 %s → %s" % (floor_txt, _floor_verdict))
        L.append("  ② 处置：**换证据类型** —— 读采集端写盘那几行（有没有排序？筛没筛？），")
        L.append("     或直接调上游接口换参数看返回标的数变不变。")
    L.append("  ⚠️ 不要预设「必须补落 rank 列」：**若采集端零过滤**，")
    L.append("     `rank` = 该快照内的行序、`n_board` = 该快照的行数 ——")
    L.append("     **两者都已可从现有 CSV 复现**，加列是白做。")
    L.append("  ⚠️ 而且**补 rank 不增加样本**：瓶颈是**入场产出率**")
    L.append("     （入场画像每组合 < min-n 只），与 rank 可得性无关。")

    L.append("")
    for ln in L:
        print(ln)
    return L


def write_diagnosis(path, lines, run_ts):
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("run_ts: %s\n\n" % run_ts)
        fh.write("\n".join(lines) + "\n")
    return path


def append_history(path, summ, run_ts, span_days, fees_bp, fees_sl_bp, cooldown_h):
    """
    把本次运行的每组合汇总追加到历史台账（**带运行时间戳**）。
    存在的理由：设计卡要求"每周把 summary 追加到 history"，
    靠手抄必然只抄一部分 —— 本项目已三次栽在"手改实测数字"上。
    """
    cols = ["run_ts", "span_days", "fees_bp", "fees_sl_bp", "cooldown_h",
            "combo", "desc", "n", "win_rate", "mean_bp", "sd_bp", "t",
            "ci_lo_bp", "ci_hi_bp", "verdict"]
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        if new:
            w.writeheader()
        for s in summ:
            row = dict(s)
            row.update(run_ts=run_ts, span_days=span_days, fees_bp=fees_bp,
                       fees_sl_bp=("" if fees_sl_bp is None else fees_sl_bp),
                       cooldown_h=cooldown_h)
            w.writerow({k: row.get(k) for k in cols})
    return path


def print_rank_banner(meta):
    src = meta.get("rank_source")
    print("列映射：时间=%s  排名=%s  涨幅=%s"
          % ("OK", meta.get("rank_col") or "（无）",
             "OK" if meta.get("has_change_pct") else "（无）"))
    if src == "column":
        print("排名来源：CSV 自带的 rank 列  ✅ 与实盘口径一致")
    else:
        print("⚠️  排名来源：**CSV 里没有 rank 列** → 已按【%s】推导（%s）"
              % ("score 降序" if src == "derived_from_score" else "CSV 行序", src))
        print("    这是一个【假设】，不是实盘事实。若实盘的 rank 另有来源，")
        print("    请用 --rank-mode column 并把真实的排名列接进来 —— 否则 p0/s1/s3/s4/s5")
        print("    测的不是实盘在跑的那个策略。")


def print_kline_banner(klines, used, snaps, sym_mode, max_bar_h):
    want = sorted({(kline_sym(s["symbol"]) if sym_mode == "kline" else s["symbol"])
                   for s in snaps})
    have = set(klines)
    matched = [w for w in want if w in have]
    missing = [w for w in want if w not in have]
    print("K 线：读入 %d 个文件 → %d 个键；快照涉及 %d 个标的，匹配上 %d 个（%.1f%%）"
          % (len(used), len(klines), len(want), len(matched),
             100.0 * len(matched) / len(want) if want else 0.0))
    if missing:
        print("  ⚠️ 未匹配 %d 个，前 10 个：%s" % (len(missing), ", ".join(missing[:10])))
    if want and not matched:
        sys.exit("✗ 一个标的都没匹配上 —— 这是本项目最经典的静默错（join 返回 0 行不报错）。\n"
                 "  请检查：① 文件名是不是币安形态（AINUSDT.feather）\n"
                 "           ② 是不是该用 --sym-mode raw\n"
                 "           ③ 是不是取错了周期目录（1h/ vs 4h/）")
    ok, worst, rows = granularity_report(klines, max_bar_h)
    if worst:
        print("K 线粒度：众数间隔 %.2f 小时（%d 个标的）" % (worst / 3600.0, len(rows)))
        if not ok:
            print("  ⚠️  粒度粗于 %.1fh —— 铁律 1「入场价 = 入场后第一根 K 线开盘价」" % max_bar_h)
            print("     会被最多 %.1f 小时的偏移污染。实测第 2 笔：4h 蜡烛开盘价 0.17859" % (worst / 3600.0))
            print("     vs 真实成交 0.18304 → 差 +2.49%，会让影子书凭空好 249bp。")
            print("     → 请改用 1h K 线目录重跑。")


def main(argv=None):
    ap = argparse.ArgumentParser(description="影子组合跟踪器")
    ap.add_argument("--score", help="score_v3 历史 CSV")
    ap.add_argument("--klines", help="K 线目录（每 symbol 一个 .feather / .csv）")
    ap.add_argument("--out", default="shadow", help="输出目录")
    ap.add_argument("--combos", default="p0,s1,s2,s3,s4,s5")
    ap.add_argument("--fees-bp", type=float, default=20.0, help="往返成本 bp")
    ap.add_argument("--fees-sl-bp", type=float, default=None,
                    help="止损出场的往返成本 bp（默认同 --fees-bp）")
    ap.add_argument("--cooldown-h", type=float, default=24.0)
    ap.add_argument("--max-pos", type=int, default=99)
    ap.add_argument("--min-n", type=int, default=30)
    ap.add_argument("--rank-mode", choices=RANK_MODES, default="auto",
                    help="排名来源：auto（有列用列，无列按 score 降序）/ column / score / row")
    ap.add_argument("--sym-mode", choices=("kline", "raw"), default="kline",
                    help="查 K 线时是否做 symbol 归一化（默认 kline = 与 SQL 宏一致）")
    ap.add_argument("--max-bar-h", type=float, default=1.0,
                    help="K 线粒度上限（小时）；超过会告警")
    ap.add_argument("--overlap-warn", type=float, default=0.9,
                    help="两个组合命中集 Jaccard 超过它就判为「退化」")
    ap.add_argument("--width-warn", type=float, default=4.0,
                    help="每快照标的数中位数 <= 它就告警 rank 约束失效")
    ap.add_argument("--floor-score", type=float, default=80.0,
                    help="采集端可能存在的 score 下限（用于低尾指纹判别）")
    ap.add_argument("--floor-chg", type=float, default=40.0,
                    help="采集端可能存在的涨幅下限（用于低尾指纹判别）")
    ap.add_argument("--probe-rank-max", type=float, default=3.0,
                    help="rank 溯源表里判定 pass_rank 的阈值")
    ap.add_argument("--no-probe", action="store_true",
                    help="不写 shadow_rank_probe.csv")
    ap.add_argument("--no-diagnose", action="store_true",
                    help="跳过输入体检（调试用）")
    ap.add_argument("--history", default=None,
                    help="历史台账路径（默认 <out>/shadow_history.csv；--no-history 关闭）")
    ap.add_argument("--no-history", action="store_true",
                    help="不追加历史台账（冒烟/调试用）")
    ap.add_argument("--list-combos", action="store_true")
    ap.add_argument("--profile-only", action="store_true",
                    help="只描述入场特征（不需要 --klines）—— 今天就能跑")
    a = ap.parse_args(argv)

    if a.list_combos:
        for k, v in COMBOS.items():
            print("%-4s %s" % (k, v["desc"]))
        return 0

    # ── 只描述入场特征：不需要 K 线 ──
    if a.profile_only:
        if not a.score:
            ap.error("--profile-only 需要 --score")
        snaps, meta = read_score(a.score, a.rank_mode)
        print("读入快照 %d 条 / %d 个快照时点（列：%s）"
              % (len(snaps), meta["n_snapshots"], ",".join(meta["columns"])))
        print_rank_banner(meta)
        if snaps:
            print("快照区间 %s → %s（%.1f 天）"
                  % (iso(snaps[0]["ts"]), iso(snaps[-1]["ts"]),
                     (snaps[-1]["ts"] - snaps[0]["ts"]) / 86400.0))
        print()
        names = [x.strip() for x in a.combos.split(",") if x.strip()]
        run_ts = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if not a.no_diagnose:
            lines = print_diagnosis(diagnose(snaps, names, a.width_warn, a.overlap_warn,
                                             a.floor_score, a.floor_chg),
                                    a.width_warn, a.overlap_warn)
            dp = write_diagnosis(os.path.join(a.out, "shadow_diagnosis.txt"), lines, run_ts)
            print("→ 体检记录 %s（带 run_ts）" % dp)
            print()
        if not a.no_probe:
            pr = rank_probe(snaps, a.probe_rank_max)
            pf = os.path.join(a.out, "shadow_rank_probe.csv")
            pcols = ["ts", "symbol", "score", "change_pct", "rank_derived",
                     "width_at_ts", "pass_rank"]
            os.makedirs(a.out, exist_ok=True)
            with open(pf, "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=pcols)
                w.writeheader()
                for r in pr:
                    w.writerow(r)
            npass = sum(r["pass_rank"] for r in pr)
            print("→ rank 溯源 %s（%d 行，rank<=%.0f 的 %d 行 = %.1f%%）"
                  % (pf, len(pr), a.probe_rank_max, npass,
                     100.0 * npass / len(pr) if pr else 0.0))
            print("   读法：width_at_ts = 该时刻 CSV 里只有几个标的。"
                  "width 全是 1 → rank_derived 恒为 1，`rank<=3` 不可能过滤掉任何东西。")
            print()
        rows = profile(snaps, names, a.min_n)
        os.makedirs(a.out, exist_ok=True)
        f = os.path.join(a.out, "shadow_profile.csv")
        cols = ["combo", "desc", "n", "chg_min", "chg_p25", "chg_med",
                "chg_p75", "chg_max", "verdict"]
        with open(f, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k) for k in cols})
        print("=== 入场特征：满足条件的币【入场时已经涨了多少】（去重后按标的计）===")
        print("%-5s %5s %9s %9s %9s %9s %9s  %s"
              % ("组合", "n", "最小%", "p25%", "中位%", "p75%", "最大%", "判定"))
        for r in rows:
            def s(k):
                return "—" if r[k] is None else ("%.2f" % r[k])
            print("%-5s %5d %9s %9s %9s %9s %9s  %s"
                  % (r["combo"], r["n"], s("chg_min"), s("chg_p25"), s("chg_med"),
                     s("chg_p75"), s("chg_max"), r["verdict"]))
        print()
        print("→ %s" % f)
        print()
        print("读法：这一列只回答【入场点在动量曲线的什么位置】，")
        print("      不回答【入场后能不能赚】—— 后者要靠影子组合前瞻积累（去掉 --profile-only）。")
        return 0

    if not (a.score and a.klines):
        ap.error("--score 与 --klines 都必需（--list-combos / --profile-only 除外）")

    snaps, meta = read_score(a.score, a.rank_mode)
    klines, used = read_klines(a.klines, normalize=(a.sym_mode == "kline"))
    print("读入快照 %d 条 / K 线 %d 个标的" % (len(snaps), len(klines)))
    print_rank_banner(meta)
    if snaps:
        print("快照区间 %s → %s" % (iso(snaps[0]["ts"]), iso(snaps[-1]["ts"])))
    print_kline_banner(klines, used, snaps, a.sym_mode, a.max_bar_h)
    print()

    names0 = [x.strip() for x in a.combos.split(",") if x.strip()]
    run_ts0 = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not a.no_diagnose:
        lines = print_diagnosis(diagnose(snaps, names0, a.width_warn, a.overlap_warn,
                                         a.floor_score, a.floor_chg),
                                a.width_warn, a.overlap_warn)
        dp = write_diagnosis(os.path.join(a.out, "shadow_diagnosis.txt"), lines, run_ts0)
        print("→ 体检记录 %s（带 run_ts）" % dp)
        print()
    if not a.no_probe:
        pr = rank_probe(snaps, a.probe_rank_max)
        pf = os.path.join(a.out, "shadow_rank_probe.csv")
        pcols = ["ts", "symbol", "score", "change_pct", "rank_derived",
                 "width_at_ts", "pass_rank"]
        os.makedirs(a.out, exist_ok=True)
        with open(pf, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=pcols)
            w.writeheader()
            for r in pr:
                w.writerow(r)
        print("→ rank 溯源 %s（%d 行）" % (pf, len(pr)))
        print()

    # ── 列缺失预检：绝不允许"条件写错了"伪装成"没找到" ──
    names = names0
    for n in names:
        if n not in COMBOS:
            sys.exit("✗ 未知组合 %s（用 --list-combos 看）" % n)
    avail = available_fields(snaps)
    blocked = {}
    for n in names:
        miss = [f for f in combo_needs(COMBOS[n]) if f not in avail]
        if miss:
            blocked[n] = miss
    if blocked:
        print("⚠️  ══ 列缺失预检未通过 ══")
        for n, miss in blocked.items():
            print("    %-4s 需要 %s —— CSV 里这些字段【全为空】，该组合【未运行】"
                  % (n, "+".join(miss)))
        print("    （不运行 ≠ 没有交易机会。把 0 笔当成'这个组合不行'是本项目最常见的自欺。）")
        print()
        if "p0" in blocked:
            sys.exit("✗ 基线 p0 都被阻塞了，这次运行没有意义。先解决列缺失。")

    os.makedirs(a.out, exist_ok=True)
    all_tr, summ = [], []
    for name in names:
        if name in blocked:
            s = dict(combo=name, desc=COMBOS[name]["desc"], n=0, win_rate=None,
                     mean_bp=None, sd_bp=None, t=None, ci_lo_bp=None, ci_hi_bp=None,
                     verdict="未运行：缺 %s" % "+".join(blocked[name]))
            summ.append(s)
            continue
        tr = run_combo(name, COMBOS[name], snaps, klines,
                       a.fees_bp, a.cooldown_h, a.max_pos, a.fees_sl_bp, a.sym_mode)
        all_tr += tr
        s = summarize(tr, a.min_n)
        s["combo"] = name
        s["desc"] = COMBOS[name]["desc"]
        summ.append(s)

    tf = os.path.join(a.out, "shadow_trades.csv")
    with open(tf, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(all_tr[0].keys()) if all_tr else
                           ["combo", "symbol", "ksym", "entry_ts", "entry_px",
                            "exit_ts", "exit_px", "reason", "hold_h",
                            "chg_at_entry", "score_at_entry", "rank_at_entry",
                            "fee_bp", "gross_bp", "ret_bp"])
        w.writeheader()
        for t in all_tr:
            w.writerow(t)

    sf = os.path.join(a.out, "shadow_summary.csv")
    cols = ["combo", "desc", "n", "win_rate", "mean_bp", "sd_bp", "t",
            "ci_lo_bp", "ci_hi_bp", "verdict"]
    with open(sf, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for s in summ:
            w.writerow({k: s.get(k) for k in cols})

    print("=== 影子组合汇总（成本 %.0fbp 往返，止损 %.0fbp，冷却 %.0fh）==="
          % (a.fees_bp,
             a.fees_bp if a.fees_sl_bp is None else a.fees_sl_bp,
             a.cooldown_h))
    print("%-5s %5s %7s %9s %8s %6s %20s  %s"
          % ("组合", "n", "胜率%", "均值bp", "sd_bp", "t", "95% CI", "判定"))
    for s in summ:
        ci = ("[%.2f, %.2f]" % (s["ci_lo_bp"], s["ci_hi_bp"])) if s["n"] else "—"
        print("%-5s %5d %7s %9s %8s %6s %20s  %s"
              % (s["combo"], s["n"], s["win_rate"], s["mean_bp"], s["sd_bp"],
                 s["t"], ci, s["verdict"]))
    print()
    print("明细 → %s\n汇总 → %s" % (tf, sf))

    # ── 历史台账（带运行时间戳）──
    if not a.no_history:
        hp = a.history or os.path.join(a.out, "shadow_history.csv")
        run_ts = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        span = ((snaps[-1]["ts"] - snaps[0]["ts"]) / 86400.0) if snaps else 0.0
        append_history(hp, summ, run_ts, round(span, 2),
                       a.fees_bp, a.fees_sl_bp, a.cooldown_h)
        print("台账 → %s（本次追加 %d 行，run_ts=%s，数据跨度 %.1f 天）"
              % (hp, len(summ), run_ts, span))

    print()
    print("读法：")
    print("  · n < %d 的组合，任何数字都不构成证据（只是'还没测'）" % a.min_n)
    print("  · 均值要跟【成本】比：均值 ≤ 0 或 CI 跨 0 → 该组合不可用")
    print("  · 不要在多个组合里挑最好的那个 —— 那是选择偏误，见设计卡 §五")
    return 0


if __name__ == "__main__":
    sys.exit(main())
