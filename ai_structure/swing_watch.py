# -*- coding: utf-8 -*-
"""BTC 波段结构定时分析。

回答三个问题：
  1. 现在处于什么位置？（严格基于已确认的枢轴，无前视）
  2. 各期限（7/15/30/60/90/180/365 天）方向能不能判断？
  3. 当前状态相比「随便挑一天买入」有没有增量？

方法论要点
----------
· **非重叠子样本**：这是本脚本相对早期版本最重要的修正。
  连续取样会让名义样本虚高几十倍，置信区间完全是假的。
  本脚本对每个状态按「步长 ≥ 期限」贪心抽取非重叠观测，
  所有统计量（中位数、P(up)、置信区间、二项检验）都在这个子样本上计算。

· **Wilson 区间**：判断「方向能否确认」的标准是 P(up) 的 95% 置信区间是否排除 50%。
  用 Wilson 而非正态近似，小样本下更可靠。

· **事件去重随期限缩放**：两个观测只有在「前瞻窗口重叠」时才是重复计数。
  合并阈值 = 该期限本身。15 天期限下相隔 9 天的两段是独立的，
  365 天期限下则不是——用固定阈值（如 180 天）会在短期限上过度合并。

· **基准率是必须的对照组**：BTC 无条件上涨，不设对照的「看多」没有信息量。

用法
----
    python swing_watch.py                # 用本地数据跑一次
    python swing_watch.py --update       # 先从币安增量拉取最新 1h 数据
    python swing_watch.py --update --json out.json --html out.html
"""

import argparse
import datetime
import json
import math
import os
import sys
import urllib.request

import numpy as np
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ================================================================ 配置
import config as C

DATA = C.CSV                    # 绝对路径，与脚本位置绑定
THETA = C.THETA                 # 主口径阈值
THETA_ALT = C.THETA_ALT         # 稳健性口径
HORIZONS = C.HORIZONS
MIN_INDEP = C.MIN_INDEP         # 独立样本下限，低于此不给结论
API = C.BINANCE_KLINES
PROXY = C.PROXY or None         # 留空则直连
DAY = 24
H_MS = 3_600_000

sep = "=" * 100

# 配色与字体（图表与 HTML 共用；图表函数是模块级的，不能依赖 render_html 的局部变量）
INK, MUTED, LINE = "#2C2C2A", "#8A8A8A", "#E8E8E4"
GREEN, RED, AMBER, GRAY = "#0F6E56", "#A32D2D", "#BA7517", "#5F5E5A"
BLUE, PURPLE = "#2F5FA8", "#6B4FA8"
MONO = "'SFMono-Regular',Consolas,'Liberation Mono',monospace"
FONT = ("-apple-system,'Segoe UI','PingFang SC','Hiragino Sans GB',"
        "'Microsoft YaHei',sans-serif")


# ================================================================ 数据
def load_data(path=DATA):
    df = pd.read_csv(path)
    return (df["ts"].to_numpy(np.int64), df["high"].to_numpy(float),
            df["low"].to_numpy(float), df["close"].to_numpy(float))


def closed_only(rows, now_ms):
    """只保留已收盘的 K 线。

    币安 klines 会返回**正在进行中**的那根 bar（close_time = k[6] 在未来）。
    旧实现把它原样写进 CSV，且增量从 last_ts+1h 开始拉，这根残缺 bar
    永远不会被覆盖——每跑一次就埋一根（2026-09-27 实测：末行 volume 69.5，
    相邻整根约 200）。决策时刻只能用已收盘数据。
    """
    return [k for k in rows if int(k[6]) < now_ms]


def update_data(path=DATA, symbol="BTCUSDT", interval="1h"):
    """从币安公开数据接口增量拉取。失败不抛异常，返回状态字符串。

    从本地最后一根的 ts **本身**开始拉（而不是 +1h），并以新数据覆盖同 ts 行：
    即使历史上写进过一根未收盘 bar，下一次更新也会把它修正。
    """
    if not os.path.exists(path):
        print(f"  [数据] {path} 不存在，需要全量下载")
        start = int(datetime.datetime(2017, 8, 17, tzinfo=datetime.UTC)
                    .timestamp() * 1000)
    else:
        df = pd.read_csv(path)
        start = int(df["ts"].iloc[-1])
    now = int(datetime.datetime.now(datetime.UTC).timestamp() * 1000)
    if start + H_MS > now:
        return "已是最新，无需更新"

    rows, cursor, calls = [], start, 0
    # 上限要够全量下载：2017-08 至今约 8 万根 1h，1000 根/次 → 约 80 次。
    # 原上限 60 会在首次全量拉取时静默截断。
    MAX_CALLS = 300
    # PROXY 为空时不要构造带 None 的 ProxyHandler，直接建空 opener
    if PROXY:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))
    else:
        opener = urllib.request.build_opener()
    try:
        while cursor + H_MS <= now and calls < MAX_CALLS:
            url = (f"{API}?symbol={symbol}&interval={interval}"
                   f"&startTime={cursor}&limit=1000")
            with opener.open(url, timeout=25) as r:
                batch = json.load(r)
            if not batch:
                break
            rows.extend(batch)
            cursor = int(batch[-1][0]) + H_MS
            calls += 1
    except Exception as e:
        return f"更新失败（沿用本地数据）：{type(e).__name__}: {e}"

    rows = closed_only(rows, now)
    if not rows:
        return "无新增数据"
    new = pd.DataFrame([{
        "ts": int(k[0]), "date": datetime.datetime.fromtimestamp(
            k[0] / 1000, datetime.UTC).strftime("%Y-%m-%d %H:%M"),
        "open": float(k[1]), "high": float(k[2]), "low": float(k[3]),
        "close": float(k[4]), "volume": float(k[5])} for k in rows])
    new = new.drop_duplicates(subset="ts", keep="last")
    n_old = 0
    if os.path.exists(path):
        old = pd.read_csv(path)
        n_old = len(old)
        # keep="last"：同 ts 以新拉到的（已收盘）数据为准
        merged = (pd.concat([old, new], ignore_index=True)
                  .drop_duplicates(subset="ts", keep="last").sort_values("ts")
                  .reset_index(drop=True))
    else:
        merged = new.sort_values("ts").reset_index(drop=True)
    merged.to_csv(path, index=False)
    return f"新增 {len(merged) - n_old} 行，现共 {len(merged):,} 行"


# ================================================================ 摆动点
def zigzag(ts, high, low, thr):
    """百分比阈值 ZigZag。返回按时间排序的已确认枢轴。

    · 只用 high/low，不用 close —— 摆动的极值发生在影线上。
    · 每个枢轴带 conf_i：确认它的那根 K 线的下标。point_states 严格只用
      conf_i <= i 的枢轴，这是避免前视偏差的关键。

    初始化阶段（dir_ == 0）的两个坑，都踩过：
      ① 高点与低点的下标**必须分开跟踪**。早期版本共用一个 ext_i，
         最后被谁覆盖就指向谁，导致首个枢轴的价格正确、位置错位
         （价格取自高点，下标却指向低点所在的 bar）。
      ② 不能固定假设「高点在前」。要先比 ext_pi 与 ext_loi，
         谁在前谁才是候选枢轴，只有从它反向走够 thr 才确认。
    """
    piv = []
    dir_, ext_p, ext_pi, ext_lo, ext_loi = 0, high[0], 0, low[0], 0
    n = len(ts)
    for i in range(1, n):
        if dir_ == 0:
            if high[i] > ext_p:
                ext_p, ext_pi = high[i], i
            if low[i] < ext_lo:
                ext_lo, ext_loi = low[i], i
            if ext_pi < ext_loi:
                # 高点在前 → 等价格从该高点回落够 thr，它才是枢轴
                if low[i] <= ext_p * (1 - thr):
                    piv.append({"i": ext_pi, "ts": ts[ext_pi], "price": ext_p,
                                "kind": "H", "conf_i": i})
                    dir_, ext_lo, ext_loi = -1, low[i], i
            else:
                # 低点在前 → 等价格从该低点上涨够 thr
                if high[i] >= ext_lo * (1 + thr):
                    piv.append({"i": ext_loi, "ts": ts[ext_loi], "price": ext_lo,
                                "kind": "L", "conf_i": i})
                    dir_, ext_p, ext_pi = 1, high[i], i
        elif dir_ == 1:
            if high[i] > ext_p:
                ext_p, ext_pi = high[i], i
            elif low[i] <= ext_p * (1 - thr):
                piv.append({"i": ext_pi, "ts": ts[ext_pi], "price": ext_p,
                            "kind": "H", "conf_i": i})
                dir_, ext_lo, ext_loi = -1, low[i], i
        else:
            if low[i] < ext_lo:
                ext_lo, ext_loi = low[i], i
            elif high[i] >= ext_lo * (1 + thr):
                piv.append({"i": ext_loi, "ts": ts[ext_loi], "price": ext_lo,
                            "kind": "L", "conf_i": i})
                dir_, ext_p, ext_pi = 1, high[i], i
    return piv


def point_states(ts, high, low, close, piv, n):
    """逐 K 线的时点状态，严格只用 conf_i <= i 的枢轴。"""
    out = [None] * n
    k = -1
    for i in range(n):
        while k + 1 < len(piv) and piv[k + 1]["conf_i"] <= i:
            k += 1
        if k < 0:
            continue
        p = piv[k]
        prev = piv[k - 1] if k >= 1 else None
        phase = "up" if p["kind"] == "L" else "down"
        out[i] = {"phase": phase, "ref_p": p["price"],
                  "move": close[i] / p["price"] - 1,
                  "days": (ts[i] - p["ts"]) / 1000 / 86400,
                  "prev_p": prev["price"] if prev else None,
                  "prev_amp": (p["price"] / prev["price"] - 1) if prev else None,
                  "breakout": bool(phase == "up" and prev is not None
                                   and close[i] > prev["price"]),
                  "k": k}
    return out


# ================================================================ 统计工具
def _ab_masks(n, frac=0.6):
    """A/B 两段的布尔掩码。"""
    cut = int(n * frac)
    a = np.zeros(n, dtype=bool)
    b = np.zeros(n, dtype=bool)
    a[:cut], b[cut:] = True, True
    return (a, b)


def nonoverlap(mask, h):
    """按「步长 >= h 天」贪心抽取非重叠观测的下标。

    这是全部统计量的基础。连续取样的前瞻窗口互相重叠，
    名义样本会虚高几十倍，直接算置信区间是错的。
    """
    idx = np.where(mask)[0]
    if len(idx) == 0:
        return np.array([], dtype=int)
    step = h * DAY
    keep, last = [idx[0]], idx[0]
    for i in idx[1:]:
        if i - last >= step:
            keep.append(i)
            last = i
    return np.array(keep)


def wilson(k, n, z=1.96):
    """Wilson 区间：小样本下比正态近似可靠。"""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    hw = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - hw), min(1.0, c + hw))


def binom_tail(k, n, p):
    """按观测偏离方向取单侧尾概率。固定用上尾会把偏空证据算成 p≈0.96。"""
    if n == 0:
        return 1.0, "—"
    if k / n >= p:
        return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i)
                   for i in range(k, n + 1)), "偏多"
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i)
               for i in range(0, k + 1)), "偏空"


def summarise(fwd, mask, h):
    """在非重叠子样本上给出该状态的统计量与置信区间。"""
    idx = nonoverlap(mask, h)
    if len(idx) < 3:
        return {"n_indep": int(len(idx)), "usable": False}
    v = fwd[idx]
    v = v[np.isfinite(v)]
    if len(v) < 3:
        return {"n_indep": int(len(v)), "usable": False}
    k = int((v > 0).sum())
    lo, hi = wilson(k, len(v))
    return {"n_indep": int(len(v)), "usable": True,
            "median": float(np.median(v)), "mean": float(v.mean()),
            "p_up": float(k / len(v)),
            "p_up_lo": float(lo), "p_up_hi": float(hi),
            "p25": float(np.percentile(v, 25)),
            "p75": float(np.percentile(v, 75)),
            "wins": k}


def ab_split(fwd, mask, h, frac=0.6):
    """样本外对半检验：前 frac 与后 (1-frac) 两段各自独立评估。

    这是必须的。全样本上的 p 值不足以支撑结论——
    28 组组合检验下，出现 1 组「显著」本就是随机预期。
    """
    n = len(fwd)
    cut = int(n * frac)
    a = np.zeros(n, dtype=bool)
    b = np.zeros(n, dtype=bool)
    a[:cut], b[cut:] = True, True
    return (summarise(fwd, mask & a, h), summarise(fwd, mask & b, h))


def verdict(s, base, h, ab=None, min_indep=MIN_INDEP, n_tests=1):
    """三层判定，逐层收紧。

    第一层 样本量   —— 独立样本是否够
    第二层 方向     —— P(上涨) 的 95%CI 是否排除 50%（相对抛硬币）
    第三层 增量     —— 相对「任意一天买入」的基准率是否有显著差异（相对基准）
    第四层 样本外   —— A/B 两段是否同向

    只有四层全过才叫「可判断」。只过第二层的，只是「方向偏某侧」，
    不构成可利用的增量。
    """
    if not s or not s.get("usable"):
        return "样本不足", "no", None, False
    if s["n_indep"] < min_indep:
        return f"样本不足（n={s['n_indep']}）", "no", None, False
    pv, direc = binom_tail(s["wins"], s["n_indep"], base["p_up"])
    dir_ok = s["p_up_lo"] > 0.5 or s["p_up_hi"] < 0.5
    alpha = 0.05 / max(n_tests, 1)
    edge_ok = pv < alpha
    ab_ok = False
    if ab and ab[0].get("usable") and ab[1].get("usable"):
        a, b = ab
        ab_ok = ((a["p_up"] - 0.5) * (b["p_up"] - 0.5)) > 0
    if edge_ok and dir_ok and ab_ok:
        return f"可判断（{direc}）", "edge", pv, True
    if edge_ok and dir_ok:
        return f"增量显著但样本外不一致", "weak", pv, False
    if dir_ok:
        return f"方向偏{direc[1]}，但无增量（p={pv:.2f}）", "dir", pv, False
    return f"无法确认（p={pv:.2f}）", "none", pv, False


# ================================================================ 分析
def analyse(ts, high, low, close, thr=THETA, horizons=HORIZONS):
    n = len(ts)
    piv = zigzag(ts, high, low, thr)
    st = point_states(ts, high, low, close, piv, n)
    fwd = {}
    for h in horizons:
        a = np.full(n, np.nan)
        s = h * DAY
        if n > s:
            a[:n - s] = close[s:] / close[:n - s] - 1
        fwd[h] = a

    valid = np.array([x is not None for x in st])
    phase = np.array([x["phase"] if x else "na" for x in st])
    amove = np.array([abs(x["move"]) if x else np.nan for x in st])
    days = np.array([x["days"] if x else np.nan for x in st])
    brk = np.array([x["breakout"] if x else False for x in st])

    cur = st[-1]
    if cur is None or not piv:
        raise ValueError(
            f"数据不足以确认任何枢轴：共 {n} 根 K 线，θ={thr:.0%} 需要至少一次"
            f"≥{thr:.0%} 的反向运动才能确认首个枢轴。"
            f"请检查数据文件完整性，或降低 θ。")
    ca = abs(cur["move"])
    MB = [("0~10%", 0.00, 0.10), ("10~30%", 0.10, 0.30), ("30~60%", 0.30, 0.60),
          ("60~120%", 0.60, 1.20), (">120%", 1.20, 99.0)]
    DB = [("<14 天", 0, 14), ("14~60 天", 14, 60),
          ("60~180 天", 60, 180), (">180 天", 180, 99999)]
    cur_mb = next(l for l, lo, hi in MB if lo <= ca < hi)
    cur_db = next(l for l, lo, hi in DB if lo <= cur["days"] < hi)

    base = {h: summarise(fwd[h], np.ones(n, dtype=bool), h) for h in horizons}
    cond = {}
    masks = {}
    mb_lo = next(x[1] for x in MB if x[0] == cur_mb)
    mb_hi = next(x[2] for x in MB if x[0] == cur_mb)
    db_lo = next(x[1] for x in DB if x[0] == cur_db)
    db_hi = next(x[2] for x in DB if x[0] == cur_db)
    masks["move"] = valid & (phase == "up") & (amove >= mb_lo) & (amove < mb_hi)
    masks["days"] = valid & (phase == "up") & (days >= db_lo) & (days < db_hi)
    masks["breakout"] = valid & brk
    masks["up_all"] = valid & (phase == "up")
    for h in horizons:
        d = {}
        for key, m in masks.items():
            d[key] = summarise(fwd[h], m, h)
            d[key]["ab"] = [summarise(fwd[h], m & seg, h)
                            for seg in _ab_masks(n)]
        cond[h] = d

    # 历史类比片段（去重阈值 = 该期限）
    analog = {}
    seg_mask = (valid & (phase == "up") & (np.abs(amove - ca) <= 0.15)
                & (np.abs(days - cur["days"]) <= 45) & brk)
    idx = np.where(seg_mask)[0]
    segs = []
    if len(idx):
        s0 = idx[0]
        for a, b in zip(idx[:-1], idx[1:]):
            if b != a + 1:
                if (a - s0) / DAY >= 3:
                    segs.append((s0, a))
                s0 = b
        if (idx[-1] - s0) / DAY >= 3:
            segs.append((s0, idx[-1]))
    for h in horizons:
        # 合并阈值 = 期限：只有前瞻窗口重叠才算重复计数
        merged, curg = [], []
        for s0, s1 in segs:
            if curg and (ts[s0] - ts[curg[0][0]]) / 86400000 < h:
                curg.append((s0, s1))
            else:
                if curg:
                    merged.append(curg)
                curg = [(s0, s1)]
        if curg:
            merged.append(curg)
        recs = []
        for g in merged:
            s0 = g[0][0]
            v = fwd[h][s0]
            recs.append({"start": datetime.datetime.fromtimestamp(
                ts[s0] / 1000, datetime.UTC).strftime("%Y-%m-%d"),
                "n_seg": len(g),
                "move": float(close[s0] / st[s0]["ref_p"] - 1),
                "days": float(st[s0]["days"]),
                "fwd": None if not np.isfinite(v) else float(v)})
        vals = [r["fwd"] for r in recs if r["fwd"] is not None]
        k = sum(1 for x in vals if x > 0)
        lo, hi = wilson(k, len(vals))
        analog[h] = {"n_events": len(recs), "n_eval": len(vals), "wins": k,
                     "p_up": (k / len(vals)) if vals else None,
                     "p_up_lo": lo, "p_up_hi": hi,
                     "median": float(np.median(vals)) if vals else None,
                     "events": recs}

    return {"ts": ts, "high": high, "low": low, "close": close, "piv": piv,
            "st": st, "cur": cur, "cur_mb": cur_mb, "cur_db": cur_db,
            "base": base, "cond": cond, "analog": analog,
            "run_hi": float(high[piv[-1]["i"]:].max()),
            "run_lo": float(low[piv[-1]["i"]:].min())}


# ================================================================ 输出
def pad(s, w):
    def dw(t):
        return sum(2 if ord(c) > 0x1100 and (
            0x2E80 <= ord(c) <= 0xA4CF or 0xAC00 <= ord(c) <= 0xD7A3
            or 0xF900 <= ord(c) <= 0xFAFF or 0xFE30 <= ord(c) <= 0xFE6F
            or 0xFF00 <= ord(c) <= 0xFF60 or 0xFFE0 <= ord(c) <= 0xFFE6
            or 0x20000 <= ord(c) <= 0x3FFFD) else 1 for c in t)
    return s + " " * max(w - dw(s), 0)


def print_report(A, thr=THETA):
    ts, close, piv, cur = A["ts"], A["close"], A["piv"], A["cur"]
    p = piv[-1]
    ft = lambda ms, f="%Y-%m-%d": datetime.datetime.fromtimestamp(
        ms / 1000, datetime.UTC).strftime(f)
    print()
    print(sep)
    print(f"BTC 波段结构分析 · θ={thr * 100:.0f}% · "
          f"数据截至 {ft(ts[-1], '%Y-%m-%d %H:%M')} UTC")
    print(sep)

    # ---- 位置
    print()
    print("【位置】")
    print(f"  当前阶段      : {'上行腿（已确认）' if cur['phase'] == 'up' else '下行腿（已确认）'}")
    print(f"  最近确认枢轴  : {'低点' if p['kind'] == 'L' else '高点'} {p['price']:,.0f}"
          f"  出现于 {ft(p['ts'])}  确认于 {ft(ts[p['conf_i']])}")
    print(f"  自该枢轴      : {cur['move'] * 100:+.1f}%   历时 {cur['days']:.0f} 天")
    print(f"  本腿区间      : {A['run_lo']:,.0f} → {A['run_hi']:,.0f}"
          f"   距最高 {(close[-1] / A['run_hi'] - 1) * 100:+.1f}%")
    if cur["prev_p"]:
        pk = "高点" if p["kind"] == "L" else "低点"
        print(f"  上一枢轴      : {pk} {cur['prev_p']:,.0f}"
              f"   突破={cur['breakout']}（{close[-1] / cur['prev_p'] - 1:+.1%}）")
    print(f"  本腿终结触发  : {A['run_hi'] * (1 - thr):,.0f}（自最高回撤 {thr * 100:.0f}%）")

    # ---- 基准率
    print()
    print("【无条件基准率】任意一天买入，持有 N 天。这是所有条件判断的对照组。")
    print("  " + pad("期限", 10) + f"{'独立n':>8}{'中位收益':>11}{'P(上涨)':>10}"
          f"{'P(上涨) 95%CI':>18}{'P25~P75':>16}")
    print("  " + "-" * 74)
    for h in HORIZONS:
        b = A["base"][h]
        if not b.get("usable"):
            print("  " + pad(f"{h} 天", 10) + f"{'样本不足':>12}")
            continue
        # 先算成变量再格式化：Python 3.12 之前不允许 f-string 里再嵌套
        # 使用同类引号的 f-string（PEP 701）。写成变量同时更易读。
        ci = f"[{b['p_up_lo'] * 100:.0f}%, {b['p_up_hi'] * 100:.0f}%]"
        iqr = f"{b['p25'] * 100:+.0f}% ~ {b['p75'] * 100:+.0f}%"
        print("  " + pad(f"{h} 天", 10)
              + f"{b['n_indep']:>8}{b['median'] * 100:>+10.1f}%"
              + f"{b['p_up'] * 100:>9.0f}%"
              + f"{ci:>18}"
              + f"{iqr:>16}")

    # ---- 当前状态
    keys = [("move", f"幅度{A['cur_mb']}"), ("days", f"天数{A['cur_db']}"),
            ("breakout", "突破前高"), ("up_all", "上行腿全部")]
    n_tests = len(keys) * len(HORIZONS)
    alpha = 0.05 / n_tests
    print()
    print(f"【当前状态的条件表现】上行腿 · 已行进 {A['cur_mb']} · "
          f"已持续 {A['cur_db']} · 突破前高={cur['breakout']}")
    print(f"  共做 {n_tests} 组检验（{len(keys)} 状态 × {len(HORIZONS)} 期限），"
          f"Bonferroni 校正阈值 α={alpha:.4f}")
    print("  " + pad("期限", 9) + pad("状态", 13)
          + f"{'独立n':>6}{'中位':>9}{'P(涨)':>7}{'95%CI':>13}"
          f"{'vs基准':>8}{'p值':>8}{'A/B':>10}   判定")
    print("  " + "-" * 104)
    for h in HORIZONS:
        b = A["base"][h]
        if not b.get("usable"):
            continue
        for key, lab in keys:
            s = A["cond"][h].get(key)
            if not s or not s.get("usable"):
                continue
            v, code, pv, _ = verdict(s, b, h, ab=s.get("ab"), n_tests=n_tests)
            edge = (s["median"] - b["median"]) * 100
            ab = s.get("ab") or [None, None]
            abtxt = ("—"
                     if not (ab[0] and ab[0].get("usable")
                             and ab[1] and ab[1].get("usable"))
                     else f"{ab[0]['p_up'] * 100:.0f}/{ab[1]['p_up'] * 100:.0f}%")
            ci = f"[{s['p_up_lo'] * 100:.0f}%,{s['p_up_hi'] * 100:.0f}%]"
            pvtxt = "—" if pv is None else f"{pv:.3f}"
            print("  " + pad(f"{h} 天", 9) + pad(lab, 13)
                  + f"{s['n_indep']:>6}{s['median'] * 100:>+8.1f}%"
                  + f"{s['p_up'] * 100:>6.0f}%"
                  + f"{ci:>13}"
                  + f"{edge:>+7.1f}%"
                  + f"{pvtxt:>8}"
                  + f"{abtxt:>10}   {v}")
        print()

    # ---- 历史类比
    print("【历史类比】与今天结构最像的时段（幅度±15pp + 天数±45d + 已突破前高）")
    print("  去重阈值 = 该期限本身（只有前瞻窗口重叠才算重复计数）")
    print("  " + pad("期限", 10) + f"{'事件数':>8}{'命中':>8}{'P(上涨)':>10}"
          f"{'95%CI':>16}{'中位收益':>11}   判定")
    print("  " + "-" * 82)
    for h in HORIZONS:
        a = A["analog"][h]
        if not a["n_eval"]:
            print("  " + pad(f"{h} 天", 10) + f"{'无可用事件':>12}")
            continue
        lo, hi = a["p_up_lo"], a["p_up_hi"]
        excl = lo > 0.5 or hi < 0.5
        v = "方向确认" if excl else "含 50%，无法确认"
        hit = f"{a['wins']}/{a['n_eval']}"
        ci = f"[{lo * 100:.0f}%, {hi * 100:.0f}%]"
        print("  " + pad(f"{h} 天", 10)
              + f"{a['n_events']:>8}{hit:>8}"
              + f"{a['p_up'] * 100:>9.0f}%"
              + f"{ci:>16}"
              + f"{a['median'] * 100:>+10.1f}%   {v}")
    print()
    for h in HORIZONS:
        a = A["analog"][h]
        if not a["events"]:
            continue
        print(f"  ── {h} 天期限的事件明细（{a['n_events']} 个）")
        for r in a["events"]:
            fv = "—" if r["fwd"] is None else f"{r['fwd'] * 100:+.1f}%"
            print(f"     {r['start']}  {r['n_seg']} 段  "
                  f"幅度 {r['move'] * 100:+.0f}% / {r['days']:.0f}天  → {fv}")
        print()


# ================================================================ HTML
def chart_position(A, thr=THETA):
    """最近 18 个月价格 + 摆动点 + 当前位置 + 本腿终结触发价。

    只画「已经发生且已确认」的东西：摆动点用的是事后确认的枢轴，
    绿色底纹从最后一个枢轴的**确认时刻**开始——那是无前视下唯一
    能说「我此刻知道自己在一条上行腿里」的时点。
    """
    ts, high, low, close = A["ts"], A["high"], A["low"], A["close"]
    piv = A["piv"]
    W, H = 680, 300
    L, R, T, B = 60, 22, 28, 42
    pw, ph = W - L - R, H - T - B
    n = len(ts)
    i0 = max(0, n - 24 * 540)                 # 最近 18 个月（540 天）
    span = max(float(high[i0:].max()) - float(low[i0:].min()), 1e-9)
    lo = float(low[i0:].min()) - span * 0.08
    hi = float(high[i0:].max()) + span * 0.12

    def X(i):
        return L + (i - i0) / max(n - 1 - i0, 1) * pw

    def Y(p):
        return T + ph - (p - lo) / max(hi - lo, 1e-9) * ph

    s = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" '
         f'aria-label="最近 18 个月价格、摆动点与当前位置">',
         '<title>当前位置</title>',
         '<desc>标注最近 18 个月的摆动高低点、当前上行腿区间，'
         '以及本腿终结的触发价。</desc>']

    # 网格与价格轴
    for k in range(5):
        p = lo + (hi - lo) * k / 4
        y = Y(p)
        s.append(f'<line x1="{L}" y1="{y:.1f}" x2="{W - R}" y2="{y:.1f}" '
                 f'stroke="{LINE}" stroke-width="1"/>')
        s.append(f'<text x="{L - 8}" y="{y + 4:.1f}" font-size="10.5" '
                 f'fill="{MUTED}" text-anchor="end" font-family="{MONO}">'
                 f'{p / 1000:.0f}k</text>')

    # 本腿底纹：从最后一个枢轴的确认时刻起
    rx0 = min(max(X(piv[-1]["conf_i"]), L), W - R)
    s.append(f'<rect x="{rx0:.1f}" y="{T}" width="{max(W - R - rx0, 1):.1f}" '
             f'height="{ph}" fill="{GREEN}" opacity="0.07"/>')

    # 价格曲线（等距抽稀到约 1400 点）
    step = max(1, (n - i0) // 1400)
    pts = " ".join(f"{X(i):.1f},{Y(close[i]):.1f}" for i in range(i0, n, step))
    s.append(f'<polyline points="{pts}" fill="none" stroke="{GRAY}" '
             f'stroke-width="1.1"/>')

    # 摆动点三角标记
    for p in piv:
        if p["i"] < i0:
            continue
        x, y = X(p["i"]), Y(p["price"])
        col = RED if p["kind"] == "H" else GREEN
        tri = (f'{x:.1f},{y - 1:.1f} {x - 4:.1f},{y + 6:.1f} {x + 4:.1f},{y + 6:.1f}'
               if p["kind"] == "H" else
               f'{x:.1f},{y + 1:.1f} {x - 4:.1f},{y - 6:.1f} {x + 4:.1f},{y - 6:.1f}')
        s.append(f'<polygon points="{tri}" fill="{col}" opacity="0.85"/>')

    # 本腿最高
    yh = Y(A["run_hi"])
    s.append(f'<line x1="{L}" y1="{yh:.1f}" x2="{W - R}" y2="{yh:.1f}" '
             f'stroke="{BLUE}" stroke-width="1.2" stroke-dasharray="5 3"/>')
    s.append(f'<text x="{L + 6}" y="{yh - 6:.1f}" font-size="10.5" fill="{BLUE}" '
             f'font-family="{MONO}">本腿最高 {A["run_hi"]:,.0f}</text>')

    # 终结触发
    yt = Y(A["run_hi"] * (1 - thr))
    s.append(f'<line x1="{L}" y1="{yt:.1f}" x2="{W - R}" y2="{yt:.1f}" '
             f'stroke="{RED}" stroke-width="1.2" stroke-dasharray="2 3"/>')
    s.append(f'<text x="{L + 6}" y="{yt + 14:.1f}" font-size="10.5" fill="{RED}" '
             f'font-family="{MONO}">终结触发 {A["run_hi"] * (1 - thr):,.0f}'
             f'（自最高 −{thr * 100:.0f}%）</text>')

    # 现价
    xc, yc = X(n - 1), Y(close[-1])
    s.append(f'<circle cx="{xc:.1f}" cy="{yc:.1f}" r="4.5" fill="{PURPLE}"/>')
    s.append(f'<text x="{xc - 9:.1f}" y="{yc - 9:.1f}" font-size="11.5" '
             f'fill="{PURPLE}" text-anchor="end" font-family="{MONO}">'
             f'{close[-1]:,.0f}</text>')

    kn = "低点" if piv[-1]["kind"] == "L" else "高点"
    d0 = datetime.datetime.fromtimestamp(piv[-1]["ts"] / 1000, datetime.UTC)
    s.append(f'<text x="{L}" y="{H - 10}" font-size="10.5" fill="{MUTED}">'
             f'最近 18 个月 · 绿底为本腿（自 {d0.strftime("%Y-%m-%d")} '
             f'{kn}被确认起）· 三角为已确认摆动点</text>')
    s.append('</svg>')
    return "\n".join(s)


def chart_odds(A, thr=THETA):
    """各期限 P(上涨)：条件状态（突破前高）vs 无条件基准，带 95% Wilson 区间。

    这张图存在的理由是防止「从点估计读方向」。两条线是否分离、
    区间是否越过 50% 竖线，一眼就能看出结论强度。
    """
    hs = [h for h in HORIZONS
          if A["base"][h].get("usable")
          and (A["cond"][h].get("breakout") or {}).get("usable")]
    if not hs:
        return ""
    W = 680
    rowh = 46
    L, R, T = 88, 104, 40
    H = T + rowh * len(hs) + 34
    pw = W - L - R

    def X(p):
        return L + p * pw

    ybase = T + rowh * len(hs) - 12
    s = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" '
         f'aria-label="各期限上涨概率：条件状态与无条件基准对比">',
         '<title>方向概率对比</title>',
         '<desc>每个期限给出无条件基准与当前条件状态（突破前高）的'
         '上涨概率及其 95% 置信区间。</desc>']
    for k in range(6):
        p = k / 5
        x = X(p)
        s.append(f'<line x1="{x:.1f}" y1="{T - 10}" x2="{x:.1f}" '
                 f'y2="{ybase:.1f}" stroke="{LINE}" stroke-width="1"/>')
        s.append(f'<text x="{x:.1f}" y="{T - 17}" font-size="10.5" '
                 f'fill="{MUTED}" text-anchor="middle" font-family="{MONO}">'
                 f'{p * 100:.0f}%</text>')
    x50 = X(0.5)
    s.append(f'<line x1="{x50:.1f}" y1="{T - 10}" x2="{x50:.1f}" '
             f'y2="{ybase:.1f}" stroke="{AMBER}" stroke-width="1.3" '
             f'stroke-dasharray="4 3"/>')
    s.append(f'<text x="{x50:.1f}" y="{H - 10}" font-size="10.5" '
             f'fill="{AMBER}" text-anchor="middle">50% 抛硬币线</text>')

    for j, h in enumerate(hs):
        y = T + j * rowh
        b, c = A["base"][h], A["cond"][h]["breakout"]
        s.append(f'<text x="{L - 12}" y="{y + 5:.1f}" font-size="11.5" '
                 f'fill="{INK}" text-anchor="end" font-family="{MONO}">'
                 f'{h} 天</text>')
        for val, lo, hi, col, yy, tag in (
                (b["p_up"], b["p_up_lo"], b["p_up_hi"], GRAY, y - 9, "基准"),
                (c["p_up"], c["p_up_lo"], c["p_up_hi"],
                 GREEN if c["p_up"] >= b["p_up"] else RED, y + 9, "条件")):
            s.append(f'<line x1="{X(lo):.1f}" y1="{yy:.1f}" x2="{X(hi):.1f}" '
                     f'y2="{yy:.1f}" stroke="{col}" stroke-width="2.2" '
                     f'opacity="0.45"/>')
            for xe in (lo, hi):
                s.append(f'<line x1="{X(xe):.1f}" y1="{yy - 3.5:.1f}" '
                         f'x2="{X(xe):.1f}" y2="{yy + 3.5:.1f}" stroke="{col}" '
                         f'stroke-width="1.6" opacity="0.45"/>')
            s.append(f'<circle cx="{X(val):.1f}" cy="{yy:.1f}" r="3.6" '
                     f'fill="{col}"/>')
            s.append(f'<text x="{X(hi) + 8:.1f}" y="{yy + 4:.1f}" '
                     f'font-size="10.5" fill="{col}" font-family="{MONO}">'
                     f'{tag} {val * 100:.0f}%</text>')
    s.append('</svg>')
    return "\n".join(s)


def render_html(A, thr=THETA, path="report_watch.html"):
    ts, close, piv, cur = A["ts"], A["close"], A["piv"], A["cur"]
    ft = lambda ms, f="%Y-%m-%d": datetime.datetime.fromtimestamp(
        ms / 1000, datetime.UTC).strftime(f)
    svg_pos = chart_position(A, thr)
    svg_odds = chart_odds(A, thr)

    def prow(s, b, h, lab):
        if not s or not s.get("usable"):
            return (f'<tr><td>{lab}</td><td class="mono">{h} 天</td>'
                    f'<td class="mono">—</td><td class="mono">—</td>'
                    f'<td class="mono">—</td><td class="mono">—</td>'
                    f'<td>样本不足</td></tr>')
        v, code, pv, _ = verdict(s, b, h, ab=s.get("ab"), n_tests=n_tests)
        col = GREEN if code in ("edge", "dir") else (
            RED if code == "weak" else MUTED)
        edge = s["median"] - b["median"]
        ab = s.get("ab") or [None, None]
        abtxt = ("—" if not (ab[0] and ab[0].get("usable")
                            and ab[1] and ab[1].get("usable"))
                 else f'{ab[0]["p_up"] * 100:.0f}/{ab[1]["p_up"] * 100:.0f}%')
        return (f'<tr><td>{lab}</td><td class="mono">{h} 天</td>'
                f'<td class="mono">{s["n_indep"]}</td>'
                f'<td class="mono">{s["median"] * 100:+.1f}%</td>'
                f'<td class="mono">{s["p_up"] * 100:.0f}%</td>'
                f'<td class="mono small">[{s["p_up_lo"] * 100:.0f}%, '
                f'{s["p_up_hi"] * 100:.0f}%]</td>'
                f'<td class="mono" style="color:{GREEN if edge > 0 else RED}">'
                f'{edge * 100:+.1f}%</td>'
                f'<td class="mono small">{"—" if pv is None else f"{pv:.3f}"}</td>'
                f'<td class="mono small">{abtxt}</td>'
                f'<td style="color:{col}">{v}</td></tr>')

    rows_cond = []
    n_tests = 4 * len(HORIZONS)
    keys = [("move", f"幅度 {A['cur_mb']}"), ("days", f"天数 {A['cur_db']}"),
            ("breakout", "突破前高"), ("up_all", "上行腿全部")]
    for h in HORIZONS:
        b = A["base"][h]
        if not b.get("usable"):
            continue
        for key, lab in keys:
            rows_cond.append(prow(A["cond"][h].get(key), b, h, lab))

    rows_base = []
    for h in HORIZONS:
        b = A["base"][h]
        if not b.get("usable"):
            continue
        rows_base.append(
            f'<tr><td class="mono">{h} 天</td><td class="mono">{b["n_indep"]}</td>'
            f'<td class="mono">{b["median"] * 100:+.1f}%</td>'
            f'<td class="mono">{b["p_up"] * 100:.0f}%</td>'
            f'<td class="mono small">[{b["p_up_lo"] * 100:.0f}%, '
            f'{b["p_up_hi"] * 100:.0f}%]</td>'
            f'<td class="mono small">{b["p25"] * 100:+.0f}% ~ '
            f'{b["p75"] * 100:+.0f}%</td></tr>')

    rows_an = []
    for h in HORIZONS:
        a = A["analog"][h]
        if not a["n_eval"]:
            rows_an.append(f'<tr><td class="mono">{h} 天</td>'
                           f'<td colspan="5">无可用事件</td></tr>')
            continue
        lo, hi = a["p_up_lo"], a["p_up_hi"]
        excl = lo > 0.5 or hi < 0.5
        col = GREEN if (excl and a["median"] > 0) else (
            RED if excl else MUTED)
        rows_an.append(
            f'<tr><td class="mono">{h} 天</td>'
            f'<td class="mono">{a["n_events"]}</td>'
            f'<td class="mono">{a["wins"]}/{a["n_eval"]}</td>'
            f'<td class="mono">{a["p_up"] * 100:.0f}%</td>'
            f'<td class="mono small">[{lo * 100:.0f}%, {hi * 100:.0f}%]</td>'
            f'<td class="mono">{a["median"] * 100:+.1f}%</td>'
            f'<td style="color:{col}">'
            f'{"方向确认" if excl else "含 50%，无法确认"}</td></tr>')

    det = []
    for h in HORIZONS:
        a = A["analog"][h]
        if not a["events"]:
            continue

        def fwd_cell(v):
            """前瞻收益单元格。单独一个函数是为了避开 f-string 嵌套同类引号
            （Python 3.12 之前不允许），顺带也更好读。"""
            return "—" if v is None else f"{v * 100:+.1f}%"

        cells = "".join(
            f'<tr><td class="mono">{r["start"]}</td>'
            f'<td class="mono small">{r["n_seg"]} 段</td>'
            f'<td class="mono small">{r["move"] * 100:+.0f}% / {r["days"]:.0f}d</td>'
            f'<td class="mono" style="color:'
            f'{GREEN if (r["fwd"] or 0) > 0 else RED}">'
            f'{fwd_cell(r["fwd"])}'
            f'</td></tr>' for r in a["events"])
        det.append(f'<h3>{h} 天期限 · {a["n_events"]} 个事件</h3>'
                   f'<table><thead><tr><th>起点</th><th>含片段</th>'
                   f'<th>状态</th><th>该期限收益</th></tr></thead>'
                   f'<tbody>{cells}</tbody></table>')

    HTML = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BTC 波段结构定时分析 · {ft(ts[-1], '%Y-%m-%d %H:%M')}</title>
<style>
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:#FAFAF8; color:{INK}; font-family:{FONT};
         font-size:14px; line-height:1.7; }}
  .wrap {{ max-width:1040px; margin:0 auto; padding:44px 26px 72px; }}
  h1 {{ font-size:23px; font-weight:500; margin:0 0 6px; }}
  .sub {{ color:{MUTED}; font-size:13px; margin-bottom:26px; }}
  h2 {{ font-size:16px; font-weight:500; margin:42px 0 4px; }}
  h3 {{ font-size:13.5px; font-weight:500; margin:22px 0 6px; color:{GRAY}; }}
  .lead {{ color:{GRAY}; font-size:13.5px; margin:0 0 14px; }}
  .card {{ background:#fff; border:1px solid {LINE}; border-radius:12px;
           padding:18px 20px; margin:14px 0; }}
  .posbox {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr));
             gap:1px; background:{LINE}; border:1px solid {LINE};
             border-radius:12px; overflow:hidden; margin:14px 0; }}
  .posbox div {{ background:#fff; padding:13px 15px; }}
  .posbox .l {{ font-size:11.5px; color:{MUTED}; margin-bottom:4px; }}
  .posbox .n {{ font-size:17px; font-weight:500; font-family:{MONO}; }}
  table {{ width:100%; border-collapse:collapse; font-size:12.5px; }}
  th {{ text-align:right; font-weight:500; color:{MUTED}; font-size:11.5px;
        padding:0 8px 9px 0; border-bottom:1px solid {LINE}; white-space:nowrap; }}
  th:first-child {{ text-align:left; }}
  td {{ padding:9px 8px 9px 0; border-bottom:1px solid #F2F2EE;
        text-align:right; vertical-align:top; }}
  td:first-child {{ text-align:left; }}
  .mono {{ font-family:{MONO}; font-variant-numeric:tabular-nums; white-space:nowrap; }}
  .small {{ font-size:11px; }}
  .note {{ font-size:12px; color:{MUTED}; margin-top:12px; line-height:1.6; }}
  .warn {{ background:#FAEEDA; border:1px solid #F0E0C0; border-radius:12px;
           padding:18px 20px; margin:14px 0; font-size:13px; color:#412402; }}
  .ok {{ background:#E1F5EE; border:1px solid #C5E8DB; border-radius:12px;
         padding:18px 20px; margin:14px 0; font-size:13px; color:#04342C; }}
  code {{ font-family:{MONO}; background:#F4F4F0; padding:1px 5px; border-radius:4px; }}
</style></head><body><div class="wrap">

<h1>BTC 波段结构定时分析</h1>
<div class="sub">数据截至 {ft(ts[-1], '%Y-%m-%d %H:%M')} UTC · 收盘
{close[-1]:,.2f} USDT · θ={thr * 100:.0f}% · 生成于
{datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%d %H:%M')} UTC</div>

<h2>位置</h2>
<div class="posbox">
  <div><div class="l">当前阶段</div><div class="n" style="color:{GREEN}">
    {'上行腿' if cur['phase'] == 'up' else '下行腿'}</div></div>
  <div><div class="l">自最近枢轴</div><div class="n">{cur['move'] * 100:+.1f}%</div></div>
  <div><div class="l">已持续</div><div class="n">{cur['days']:.0f} 天</div></div>
  <div><div class="l">本腿最高</div><div class="n">{A['run_hi']:,.0f}</div></div>
  <div><div class="l">距最高</div><div class="n">
    {(close[-1] / A['run_hi'] - 1) * 100:+.1f}%</div></div>
  <div><div class="l">终结触发</div><div class="n" style="color:{RED}">
    {A['run_hi'] * (1 - thr):,.0f}</div></div>
</div>
<div class="card"><table><tbody>
<tr><td>最近确认枢轴</td><td class="mono">
  {'低点' if piv[-1]['kind'] == 'L' else '高点'} {piv[-1]['price']:,.0f}</td></tr>
<tr><td>该枢轴出现时间</td><td class="mono">{ft(piv[-1]['ts'], '%Y-%m-%d %H:%M')}</td></tr>
<tr><td>该枢轴被确认时间</td><td class="mono">{ft(ts[piv[-1]['conf_i']], '%Y-%m-%d %H:%M')}</td></tr>
<tr><td>确认延迟</td><td class="mono">
  {(ts[piv[-1]['conf_i']] - piv[-1]['ts']) / 86400000:.0f} 天</td></tr>
<tr><td>本腿区间</td><td class="mono">{A['run_lo']:,.0f} → {A['run_hi']:,.0f}</td></tr>
<tr><td>是否突破上一枢轴</td><td class="mono">
  {cur['breakout']}（{close[-1] / cur['prev_p'] - 1:+.1%}）</td></tr>
</tbody></table></div>

<h2>位置图</h2>
<p class="lead">最近 18 个月。三角为<b>已确认</b>的摆动高低点（红=高点、绿=低点）；
绿底是当前本腿——起点取最后一个枢轴的<b>确认时刻</b>，那是无前视条件下
唯一能说「此刻我确实知道自己在上行腿里」的时点。
蓝虚线为本腿最高，红虚线为终结触发价。</p>
<div class="card">{svg_pos}</div>

<h2>无条件基准率（对照组）</h2>
<p class="lead">任意一天买入、持有 N 天。<b>所有条件判断都必须超过这张表。</b>
「独立 n」是按步长 ≥ 期限抽取的非重叠观测数。</p>
<div class="card"><table><thead><tr><th>期限</th><th>独立 n</th>
  <th>中位收益</th><th>P(上涨)</th><th>P(上涨) 95%CI</th><th>P25 ~ P75</th>
</tr></thead><tbody>{''.join(rows_base)}</tbody></table></div>

<h2>当前状态的条件表现</h2>
<p class="lead">上行腿 · 已行进 {A['cur_mb']} · 已持续 {A['cur_db']} ·
突破前高 = {cur['breakout']}。<br>
<b>判定是四层收紧的</b>：① 独立样本是否够 → ② P(上涨) 的 95%CI 是否排除 50%
→ ③ 相对基准率是否有显著增量（Bonferroni 校正 α={0.05 / n_tests:.4f}，
共 {n_tests} 组检验）→ ④ A/B 两段是否同向。
<b>四层全过才叫「可判断」</b>；只过第二层的只是「方向偏某侧」，不构成可利用的增量。</p>
<div class="card"><table><thead><tr><th>状态</th><th>期限</th><th>独立 n</th>
  <th>中位收益</th><th>P(上涨)</th><th>95%CI</th><th>vs 基准</th><th>p 值</th>
  <th>A/B 两段 P(涨)</th><th>判定</th>
</tr></thead><tbody>{''.join(rows_cond)}</tbody></table></div>

<h3>方向概率对比（条件 vs 基准）</h3>
<p class="lead">每个期限两条：灰=无条件基准，彩=当前条件状态（突破前高）。
横线是 95% Wilson 区间。<b>区间跨过 50% 竖线的，就是「无法判断方向」</b>——
不要从圆点的位置读方向。</p>
<div class="card">{svg_odds}</div>

<h2>历史类比</h2>
<p class="lead">与今天结构最像的历史时段（幅度 ±15pp + 天数 ±45d + 已突破前高）。
<b>去重阈值 = 该期限本身</b>——只有前瞻窗口重叠才算重复计数。
短期限下原本重叠的片段会被正确视为独立观测。</p>
<div class="card"><table><thead><tr><th>期限</th><th>事件数</th><th>命中</th>
  <th>P(上涨)</th><th>95%CI</th><th>中位收益</th><th>判定</th>
</tr></thead><tbody>{''.join(rows_an)}</tbody></table></div>

<div class="card">{''.join(det)}</div>

<div class="warn">
<b>读这张报告的方法</b><br>
1. 「独立 n」是硬约束。低于 {MIN_INDEP} 的一律标为样本不足——不是结论，是提示。
2. 短期（7/15/30 天）基准率接近 50%，条件状态要显著偏离才叫「能判断」。
   若 95%CI 含 50%，就是<b>无法判断</b>，不要从点估计读出方向。
3. 长期（180/365 天）独立样本本来就少（个位数），任何结论都是弱证据。
4. 本报告不构成交易建议。它回答的是「数据支持到什么程度」，
   不是「该买还是该卖」。
</div>

</div></body></html>"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(HTML)
    return path


# ================================================================ 主流程
def main():
    os.makedirs(C.OUT_DIR, exist_ok=True)
    ap = argparse.ArgumentParser(description="BTC 波段结构分析")
    ap.add_argument("--update", action="store_true", help="先从币安增量拉取数据")
    ap.add_argument("--data", default=DATA)
    ap.add_argument("--theta", type=float, default=THETA)
    ap.add_argument("--json", default=os.path.join(C.OUT_DIR, "watch_latest.json"))
    ap.add_argument("--html", default=os.path.join(C.OUT_DIR, "report_watch.html"))
    ap.add_argument("--history", default=os.path.join(C.OUT_DIR,
                                                      "watch_history.csv"),
                    help="把本次判定追加到该 CSV（跨次运行追踪）")
    ap.add_argument("--quiet", action="store_true", help="只输出结论摘要")
    args = ap.parse_args()

    if args.update:
        msg = update_data(args.data)
        print(f"[数据] {msg}")

    ts, high, low, close = load_data(args.data)
    try:
        A = analyse(ts, high, low, close, thr=args.theta)
    except ValueError as e:
        print(f"\n[中止] {e}", file=sys.stderr)
        return 2
    n_tests = 4 * len(HORIZONS)      # 4 个状态 × 各期限

    if not args.quiet:
        print_report(A, thr=args.theta)

    # ---- 摘要
    cur = A["cur"]
    ft = lambda ms: datetime.datetime.fromtimestamp(
        ms / 1000, datetime.UTC).strftime("%Y-%m-%d %H:%M")
    lines = [f"BTC 波段结构 · {ft(ts[-1])} UTC · 收盘 {close[-1]:,.0f}",
             f"位置：{'上行腿' if cur['phase'] == 'up' else '下行腿'}"
             f" 自枢轴 {cur['move'] * 100:+.1f}%  {cur['days']:.0f} 天"
             f"  突破前高={cur['breakout']}",
             f"本腿终结触发 {A['run_hi'] * (1 - args.theta):,.0f}",
             ""]
    for h in HORIZONS:
        b = A["base"][h]
        if not b.get("usable"):
            continue
        seg = [f"{h:>3}天 基准 {b['median'] * 100:+6.1f}% (P={b['p_up'] * 100:.0f}%)"]
        for key, lab in (("move", "幅度"), ("breakout", "突破"), ("up_all", "全腿")):
            s = A["cond"][h].get(key)
            if not s or not s.get("usable"):
                continue
            v, _, pv, _ = verdict(s, b, h, ab=s.get("ab"), n_tests=n_tests)
            seg.append(f"{lab} {s['median'] * 100:+6.1f}% "
                       f"[{s['p_up_lo'] * 100:.0f}%,{s['p_up_hi'] * 100:.0f}%] {v}")
        a = A["analog"][h]
        if a["n_eval"]:
            seg.append(f"类比 {a['wins']}/{a['n_eval']} "
                       f"[{a['p_up_lo'] * 100:.0f}%,{a['p_up_hi'] * 100:.0f}%]")
        lines.append("  ".join(seg))
    summary = "\n".join(lines)
    print()
    print(sep)
    print("摘要")
    print(sep)
    print(summary)

    # ---- 落盘
    out = {"as_of": ft(ts[-1]), "last_close": float(close[-1]),
           "theta": args.theta,
           "position": {"phase": cur["phase"], "move": float(cur["move"]),
                        "days": float(cur["days"]),
                        "breakout": bool(cur["breakout"]),
                        "prev_p": cur["prev_p"],
                        "run_hi": A["run_hi"], "run_lo": A["run_lo"],
                        "pivot_kind": piv_kind(A), "pivot_price": float(A["piv"][-1]["price"]),
                        "pivot_ts": int(A["piv"][-1]["ts"]),
                        "conf_ts": int(ts[A["piv"][-1]["conf_i"]])},
           "base": {str(h): A["base"][h] for h in HORIZONS},
           "conditional": {str(h): A["cond"][h] for h in HORIZONS},
           "analog": {str(h): {k: v for k, v in A["analog"][h].items()}
                      for h in HORIZONS},
           "summary": summary}
    with open(args.json, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    render_html(A, thr=args.theta, path=args.html)
    print(f"\n已写 {args.json} / {args.html}")
    if args.history:
        hp, how = append_history(args.history, A, args.theta, n_tests)
        print(f"已写 {hp}（{how}）")
    return 0


def piv_kind(A):
    return "L" if A["piv"][-1]["kind"] == "L" else "H"


def append_history(path, A, thr, n_tests):
    """把本次判定写入 CSV。

    定时分析里这份历史比单次报告更有用：判定若在相邻两次运行间来回翻转，
    说明它本就是噪声。这也是「结论是否稳定」的事后自证。

    同一 as_of 重复运行时**覆盖**该行而非追加——同一天跑两次不该产生两条。
    """
    import csv
    cur = A["cur"]
    ft = lambda ms: datetime.datetime.fromtimestamp(
        ms / 1000, datetime.UTC).strftime("%Y-%m-%d %H:%M")
    row = {"as_of": ft(A["ts"][-1]),
           "close": round(float(A["close"][-1]), 2),
           "phase": cur["phase"],
           "move_pct": round(float(cur["move"]) * 100, 2),
           "days": round(float(cur["days"]), 1),
           "breakout": int(bool(cur["breakout"])),
           "run_hi": round(float(A["run_hi"]), 2),
           "trigger": round(float(A["run_hi"]) * (1 - thr), 2)}
    for h in HORIZONS:
        b = A["base"][h]
        s = A["cond"][h].get("breakout") or {}
        _, code, _, _ = verdict(s, b, h, ab=s.get("ab"), n_tests=n_tests)
        row[f"v{h}"] = code
    cols = list(row)
    new_row = {k: str(v) for k, v in row.items()}

    rows, hit = [], False
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig", newline="") as fh:
            rows = [dict(r) for r in csv.DictReader(fh)]
        for i, r in enumerate(rows):
            if r.get("as_of") == new_row["as_of"]:
                rows[i], hit = new_row, True
                break
    if not hit:
        rows.append(new_row)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, restval="",
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return path, ("覆盖同日" if hit else f"追加第 {len(rows)} 条")


if __name__ == "__main__":
    sys.exit(main())
