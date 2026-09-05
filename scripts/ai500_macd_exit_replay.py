#!/usr/bin/env python3
"""
【探索性——非结论，禁止引用为证据】
MACD whisper 出场的离线配对反事实
==================================
AI500V1MacdExitDryRun 结构上不可回测（populate_indicators 把实时标量广播到整段
dataframe，_whisper_for_symbol 走实时 ccxt）。所以要在上实盘前估出这套出场逻辑
的收益，只有一条路：拿**真实发生过的入场**，用本地 OHLCV 离线重放 whisper，
看它会在哪里出场。

样本
----
user_data/ai500_local/ai500_live_trades.csv 的 101 笔实盘（2026-08-23 ~ 09-01）。
它们来自 AI500V1DryRun —— **入场逻辑与 MACD 版完全相同**（median_rank<=3 +
OI 闸门 + BTC 闸门，enter_tag 都是 netflow_oi_entry），只有出场不同。所以是
干净的配对：同一批入场、同一条价格路径，只换出场规则。

⚠️ 样本本身的两条局限（结论必须带着说）
  1. 这 101 笔跨越了策略中途改版（AI500V1DryRun 文档记 08-27 回退到 16eee16），
     实盘那一侧的出场口径不是单一政策，所以「实盘 vs 模拟」的差不能全记在
     出场规则头上。
  2. n=101、9 天、28 个标的，加密资产共同 beta 极高 —— 名义 101 笔远不等于
     101 份独立证据。故一切显著性判断走**按日区块自举**，不用 sd/sqrt(n)。

时点口径（同源铁律）
--------------------
whisper 在每个 30m 边界 T 上算。实盘里 custom_exit 有按 30m bar 缓存，即每根
bar 的第一个 loop（T 之后几秒）算一次，所以离线也在 T 上算。
  - 已收盘 bar：该周期 open_t + 周期长度 <= T 的
  - **形成中 bar**：live 的 ccxt fetch_ohlcv 会带回未收盘那根，Pine 的
    lookahead_on 右沿也是它，_power_from_series 的 m[-1] 就指它。本地 feather
    存的是已收盘 bar，所以这里用 T 之前的 15m bar **重建**形成中 bar。
    漏掉这一步就等于把 m[-1] 换成上一根已收盘 bar，与实盘错位一格。
  - 出场成交价：T 时刻价 = T 之前最后一根已收盘 15m 的 close。不取 T 所在
    bar 的任何数据（那是未来）。

对比的两条规则
--------------
  OLD  bearish = whisper in {karisik, bicak}
  NEW  bearish = whisper == bicak  or  (karisik 且 p1>=2 且 p4<=-1)   ← A 支
均需连续 MACD_CONFIRM_BARS 根 bearish 才出场。

用法
----
  python3 scripts/ai500_macd_exit_replay.py
  python3 scripts/ai500_macd_exit_replay.py --max-hold-h 24 --boot 2000
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "user_data", "strategies"))

from AI500V1MacdExitDryRun import AI500V1MacdExitDryRun as S  # noqa: E402

LOCAL = os.path.join(_ROOT, "user_data", "ai500_local")
OHLCV = os.path.join(LOCAL, "ohlcv")
TRADES = os.path.join(LOCAL, "ai500_live_trades.csv")
TFS = ["15m", "1h", "4h", "1d"]
DUR = {"15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
FEE_RT = 0.001          # 币安永续 taker 约 0.045~0.05%/边 → 往返 0.1%
BAR = 1800              # 策略 timeframe


class Book:
    """单标的的多周期 OHLCV，支持「截至 T」的切片（含重建形成中 bar）。"""

    def __init__(self, sym: str):
        self.a = {}
        for tf in TFS:
            df = pd.read_feather(os.path.join(OHLCV, f"{sym}-{tf}.feather"))
            self.a[tf] = {
                "t": (df.t.astype("int64") // 10 ** 9).to_numpy(),
                "open": df.open.to_numpy(float), "high": df.high.to_numpy(float),
                "low": df.low.to_numpy(float), "close": df.close.to_numpy(float),
                "volume": df.volume.to_numpy(float),
            }
        self.m15 = self.a["15m"]

    def price_at(self, ts: int):
        """T 时刻可观测价 = T 之前最后一根已收盘 15m 的 close。"""
        i = np.searchsorted(self.m15["t"], ts - DUR["15m"], side="right") - 1
        return float(self.m15["close"][i]) if i >= 0 else None

    def slice_at(self, tf: str, ts: int, limit: int):
        """返回 limit 根：末根是**形成中** bar，其余为已收盘 bar。"""
        d, dur = self.a[tf], DUR[tf]
        n_closed = np.searchsorted(d["t"], ts - dur, side="right")
        if n_closed < 40:
            return None
        lo = max(0, n_closed - (limit - 1))
        out = {k: list(d[k][lo:n_closed]) for k in ("high", "low", "close", "volume")}

        # 形成中 bar：把 [周期起点, T) 之间的 15m bar 聚合起来
        pstart = (ts // dur) * dur
        t15 = self.m15["t"]
        i0 = np.searchsorted(t15, pstart, side="left")
        i1 = np.searchsorted(t15, ts - DUR["15m"], side="right")   # 只取已收盘的
        if i1 > i0:
            out["high"].append(float(self.m15["high"][i0:i1].max()))
            out["low"].append(float(self.m15["low"][i0:i1].min()))
            out["close"].append(float(self.m15["close"][i1 - 1]))
            out["volume"].append(float(self.m15["volume"][i0:i1].sum()))
        else:
            # T 正好落在周期起点：形成中 bar 刚开、几乎没成交
            p = self.price_at(ts)
            if p is None:
                return None
            for k, v in (("high", p), ("low", p), ("close", p), ("volume", 0.0)):
                out[k].append(v)
        return {k: np.asarray(v, dtype=float) for k, v in out.items()}


def whisper_at(s, sym: str, book: Book, ts: int, cache: dict):
    """whisper 只取决于 (标的, 时刻)，与是哪一笔交易无关 —— 跨交易复用。"""
    key = (sym, ts)
    if key in cache:
        return cache[key]
    powers = []
    for tf in TFS:
        o = book.slice_at(tf, ts, S.MACD_FETCH_LIMIT)
        if o is None:
            cache[key] = None
            return None
        f, sl, sg = s._adaptive_periods_series(o, S.MACD_TF_PARAMS[tf])
        m, si, h = s._macd_series(o["close"], f, sl, sg)
        p = s._power_from_series(m, si, h)
        if p is None:
            cache[key] = None
            return None
        powers.append(p)
    res = (S._classify(powers), powers)
    cache[key] = res
    return res


def bearish_old(w, _p):
    return w in ("karisik", "bicak")


def bearish_new(w, p):
    if w == "bicak":
        return True
    return w == "karisik" and p[0] >= 2 and p[3] <= -1


CACHE = {}


def simulate(s, sym, book, entry_ts, entry_px, rule, max_bars, atr_floor):
    """从 entry_ts 后的第一个 30m 边界起逐 bar 判定。返回 (毛收益, 小时, 原因)。"""
    t = ((entry_ts // BAR) + 1) * BAR
    confirm = 0
    for _ in range(max_bars):
        px = book.price_at(t)
        if px is None:
            break
        if atr_floor is not None and px <= atr_floor:
            return px / entry_px - 1.0, (t - entry_ts) / 3600, "atr_stop"
        r = whisper_at(s, sym, book, t, CACHE)
        if r is not None:
            w, p = r
            confirm = confirm + 1 if rule(w, p) else 0
            if confirm >= S.MACD_CONFIRM_BARS:
                return px / entry_px - 1.0, (t - entry_ts) / 3600, f"whisper({w})"
        t += BAR
    px = book.price_at(t)
    if px is None:
        t = int(book.m15["t"][-1]) + DUR["15m"]
        px = float(book.m15["close"][-1])
    return px / entry_px - 1.0, (t - entry_ts) / 3600, "timeout"


def atr_at(book, entry_ts, period=S.ATR_PERIOD, mult=S.ATR_MULTIPLIER):
    """复现 custom_stoploss 的静态 ATR 地板：入场 bar 的 low - mult*ATR(30m)。"""
    m = book.m15
    # 15m -> 30m（成对合并）。先对齐到 30m 边界再配对，否则错一格会把
    # 相邻两根 30m 的半边拼在一起。
    j = int(np.argmax(m["t"] % BAR == 0))
    if m["t"][j] % BAR != 0:
        return None
    n = len(m["t"]) - j
    k = j + n - (n % 2)
    t30 = m["t"][j:k:2]
    h30 = np.maximum(m["high"][j:k:2], m["high"][j + 1:k:2])
    l30 = np.minimum(m["low"][j:k:2], m["low"][j + 1:k:2])
    c30 = m["close"][j + 1:k:2]
    i = np.searchsorted(t30, entry_ts, side="right") - 1
    if i < period:
        return None
    atr = S._rma_np(S._true_range_np(h30[:i + 1], l30[:i + 1], c30[:i + 1]), period)[-1]
    if np.isnan(atr):
        return None
    return float(l30[i] - mult * atr)


def day_block_boot(days, vals, n=2000, seed=7):
    """按日区块自举（同一天整块抽），避免把 101 笔当 101 份独立证据。"""
    rng = np.random.default_rng(seed)
    days = np.asarray(days)
    vals = np.asarray(vals)
    groups = [vals[days == d] for d in sorted(set(days.tolist()))]
    out = np.empty(n)
    for i in range(n):
        pick = rng.integers(0, len(groups), len(groups))
        out[i] = np.concatenate([groups[j] for j in pick]).mean()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-hold-h", type=float, default=48.0)
    ap.add_argument("--boot", type=int, default=2000)
    args = ap.parse_args()
    max_bars = int(args.max_hold_h * 3600 / BAR)

    s = S.__new__(S)
    d = pd.read_csv(TRADES)
    d["sym"] = d.pair.str.split("/").str[0] + "USDT"
    d["open_ts"] = pd.to_datetime(d.open_date, utc=True).astype("int64") // 10 ** 9
    d["day"] = pd.to_datetime(d.open_date).dt.strftime("%Y-%m-%d")

    books, rows = {}, []
    for _, r in d.iterrows():
        if r.sym not in books:
            books[r.sym] = Book(r.sym)
        b = books[r.sym]
        floor = atr_at(b, int(r.open_ts))
        rec = {"id": r.id, "sym": r.sym, "day": r.day,
               "live_ret": float(r.close_profit),
               "live_h": (pd.to_datetime(r.close_date) -
                          pd.to_datetime(r.open_date)).total_seconds() / 3600}
        for tag, rule in (("old", bearish_old), ("new", bearish_new)):
            for stop, fl in (("", None), ("_atr", floor)):
                g, hh, why = simulate(s, r.sym, b, int(r.open_ts),
                                      float(r.open_rate), rule, max_bars, fl)
                rec[f"{tag}{stop}_ret"] = g - FEE_RT
                rec[f"{tag}{stop}_h"] = hh
                rec[f"{tag}{stop}_why"] = why
        rows.append(rec)
        if len(rows) % 20 == 0:
            print(f"  ... {len(rows)}/{len(d)}  (whisper cache {len(CACHE)})",
                  flush=True)
    res = pd.DataFrame(rows)
    out = os.path.join(LOCAL, "macd_exit_replay.csv")
    res.to_csv(out, index=False)

    print(f"\n{'='*78}\nPAIRED COUNTERFACTUAL | n={len(res)} | {res.day.min()} ~ "
          f"{res.day.max()} | {res.sym.nunique()} symbols | cap {args.max_hold_h:.0f}h"
          f"\n{'='*78}")
    print(f"{'variant':<28} {'mean%':>9} {'med%':>9} {'win%':>7} "
          f"{'med_hold_h':>11} {'sum%':>9}")
    print("-" * 78)
    cols = [("LIVE actual (V1DryRun)", "live_ret", "live_h"),
            ("MACD OLD (karisik+bicak)", "old_ret", "old_h"),
            ("MACD OLD + ATR stop", "old_atr_ret", "old_atr_h"),
            ("MACD NEW (bicak+karisikA)", "new_ret", "new_h"),
            ("MACD NEW + ATR stop", "new_atr_ret", "new_atr_h")]
    for name, rc, hc in cols:
        v = res[rc]
        print(f"{name:<28} {v.mean()*100:>9.3f} {v.median()*100:>9.3f} "
              f"{(v>0).mean()*100:>7.1f} {res[hc].median():>11.2f} "
              f"{v.sum()*100:>9.2f}")

    print("\nexit reason mix")
    for tag in ("old", "old_atr", "new", "new_atr"):
        vc = res[f"{tag}_why"].str.split("(").str[0].value_counts().to_dict()
        print(f"  {tag:<8} {vc}")

    print(f"\nday-block bootstrap 90% CI ({args.boot} draws; mean per-trade delta, %)")
    print("-" * 78)
    pairs = [("NEW - OLD  (this session's bugfix)", "new_ret", "old_ret"),
             ("NEW+ATR - OLD+ATR", "new_atr_ret", "old_atr_ret"),
             ("NEW+ATR - LIVE actual", "new_atr_ret", "live_ret"),
             ("OLD+ATR - LIVE actual", "old_atr_ret", "live_ret")]
    for name, a, b_ in pairs:
        diff = (res[a] - res[b_]).to_numpy()
        if np.allclose(diff, 0):
            print(f"  {name:<36} diff identically 0 -- no divergence in this "
                  f"sample; CI not applicable")
            continue
        bs = day_block_boot(res.day.tolist(), diff, args.boot)
        lo, hi = np.percentile(bs, [5, 95])
        mde = (1.96 + 0.8416) * bs.std()
        print(f"  {name:<36} actual {diff.mean()*100:+.3f}  "
              f"CI[{lo*100:+.3f}, {hi*100:+.3f}]  MDE {mde*100:.3f}")

    print(f"\ndetail -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
