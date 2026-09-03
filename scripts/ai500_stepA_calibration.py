#!/usr/bin/env python3
"""
Step A —— CVD 净流代理 vs VergeX net_flow 校准
==============================================
探索性——非结论，禁止引用为证据。

目的
----
VergeX 的 /trending-crypto 只返回当前快照，无历史回放，因此多窗口 rank 历史
不存在，无法直接回测"趋势中段"判定。唯一的历史来源是用交易所 K 线里的
taker buy/sell 拆分重建一个净流代理：

    net_flow_quote(bar) = taker_buy_quote - (quote_volume - taker_buy_quote)
                        = 2 * taker_buy_quote_volume - quote_volume

本脚本回答一个问题：**这个代理能不能复现 VergeX 的 net_flow？**

  能  -> 可以倒推出多年份的 5m/15m/30m/1h 历史排名，Step B 回测才成立
  不能 -> 代理不可用，只能向前积累数周真实快照

判定用三组指标（不设通过门槛，本步骤只出数，门槛在 Step B 冻结前另议）：
  1. 数值层：proxy_amount vs vergex amount 的 Pearson / Spearman / 尺度比
  2. 排名层：代理全宇宙排名 vs VergeX top20 的 overlap@20、Spearman
  3. 窗口对齐：trailing / aligned_partial 两种窗口语义哪个更贴合

已知局限（写进结论时必须带上）
------------------------------
- VergeX 币池含非币安标的（AAPLUSDT/AVGOUSDT/ASTSUSDT 等股票永续），
  这些无法用币安 K 线重建，会被排除，导致排名有系统性位移。脚本会报告
  被排除标的在 top20 中的出现频率。
- VergeX 是否跨交易所聚合、用现货还是合约，均未知。校准只能证明"贴合/不贴合"，
  不能证明口径相同。
- 校准窗口目前仅 2026-09-02 22:04 起的少量快照，且中间有 11.4h 停机缺口。
  样本量不足以做确认性结论，只用于判断代理路线通不通。

数据源
------
  --source api     币安 fapi /klines，覆盖到当前，但受 IP 限频/封禁影响
  --source vision  data.binance.vision 静态包，不限频，但发布延迟 1-2 天

K 线缓存在 user_data/ai500_local/klines_cache/ ，重跑不重复下载。
"""
import argparse
import io
import os
import sys
import time
import zipfile

import pandas as pd
import requests

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
DATA_DIR = os.path.join(_REPO_ROOT, "user_data", "ai500_local")
CACHE_DIR = os.path.join(DATA_DIR, "klines_cache")
_MARKET = "futures"   # 由 --source 覆盖；缓存按市场分开，避免现货/合约数据混用

PROXY = {"http": os.environ.get("LOCAL_PROXY", "http://127.0.0.1:10809"), "https": os.environ.get("LOCAL_PROXY", "http://127.0.0.1:10809")}
FAPI = "https://fapi.binance.com/fapi/v1"
# 现货镜像：与合约是**独立限频桶**。2026-09-03 合约出口 IP 被 -1003 长期封禁
# （到期时间持续后滚，是共享代理上其他用户打的），现货这条路仍通。
# 现货 K 线同样含 taker_buy_quote_volume，可算同口径净流。
SPOT = "https://data-api.binance.vision/api/v3"
VISION = "https://data.binance.vision/data/futures/um/daily/klines"
TIMEOUT = 30

# 复用连接。每次请求新建 TLS 连接会被本机代理频繁重置（WinError 10054），
# 用 Session 做连接池 + 对连接层错误退避重试，只有连续失败才判定为无该标的。
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0"})
NET_RETRY = 3

# VergeX 的 duration -> 分钟
DUR_MIN = {"5m": 5, "15m": 15, "30m": 30, "1h": 60}

KLINE_COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
              "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume",
              "ignore"]


# ------------------------------------------------------------------ 载入快照

def load_snapshots() -> pd.DataFrame:
    files = sorted(f for f in os.listdir(DATA_DIR) if f.startswith("netflow_multiwin_"))
    if not files:
        sys.exit("[fatal] 没有 netflow_multiwin_*.csv，先跑采集器")
    df = pd.concat([pd.read_csv(os.path.join(DATA_DIR, f)) for f in files],
                   ignore_index=True)
    df["t"] = pd.to_datetime(df.fetched_at_cst, utc=True)
    return df


# ------------------------------------------------------------------ K 线获取

def _cache_path(symbol: str) -> str:
    return os.path.join(CACHE_DIR, f"{symbol}-1m-{_MARKET}.feather")


def fetch_api(symbol: str, start_ms: int, end_ms: int, market: str = "futures"):
    """币安 1m K 线。market='futures' 走 fapi，'spot' 走现货镜像。"""
    base = FAPI if market == "futures" else SPOT
    out = []
    cur = start_ms
    while cur < end_ms:
        try:
            # limit=1000 -> 权重 5（1500 是权重 10）。221 个币 × 5 ≈ 1105 权重，
            # 配合下面 0.5s 间隔约 600 权重/分钟，远低于 2400/min 上限。
            # 出口是共享代理 IP，2026-09-03 已被 -1003 封过一次，宁慢勿快。
            r = None
            for attempt in range(NET_RETRY):
                try:
                    r = SESSION.get(f"{base}/klines", proxies=PROXY,
                                    timeout=TIMEOUT,
                                    params={"symbol": symbol, "interval": "1m",
                                            "startTime": cur, "endTime": end_ms,
                                            "limit": 1000})
                    break
                except requests.exceptions.RequestException:
                    if attempt == NET_RETRY - 1:
                        raise
                    time.sleep(1.5 * (attempt + 1))
            if r.status_code in (400, 404):
                return None                      # 该标的不在币安合约
            if r.status_code in (418, 429):
                sys.exit(f"[fatal] 币安限频/封禁 ({r.status_code}): {r.text[:160]}")
            r.raise_for_status()
            batch = r.json()
        except SystemExit:
            raise
        except Exception as e:
            print(f"[warn] {symbol} fetch failed: {e}", flush=True)
            return None
        if not batch:
            break
        out.extend(batch)
        nxt = batch[-1][0] + 60_000
        if nxt <= cur:
            break
        cur = nxt
        time.sleep(0.5)                           # 保守限速，共享出口 IP 已被封过一次
    if not out:
        return None
    d = pd.DataFrame(out, columns=KLINE_COLS)
    return _normalize(d)


def fetch_vision(symbol: str, days: list):
    """data.binance.vision 每日包。不限频但有 1-2 天发布延迟。"""
    frames = []
    for day in days:
        url = f"{VISION}/{symbol}/1m/{symbol}-1m-{day}.zip"
        try:
            r = requests.get(url, proxies=PROXY, timeout=60)
            if r.status_code != 200:
                continue
            z = zipfile.ZipFile(io.BytesIO(r.content))
            raw = z.read(z.namelist()[0]).decode()
            has_header = raw.lstrip().startswith("open_time")
            d = pd.read_csv(io.StringIO(raw), header=0 if has_header else None,
                            names=None if has_header else KLINE_COLS)
            frames.append(d)
        except Exception as e:
            print(f"[warn] {symbol} {day} vision failed: {e}", flush=True)
    if not frames:
        return None
    return _normalize(pd.concat(frames, ignore_index=True))


def _normalize(d: pd.DataFrame) -> pd.DataFrame:
    keep = ["open_time", "close", "quote_volume", "taker_buy_quote_volume"]
    d = d[keep].astype({"open_time": "int64", "close": float,
                        "quote_volume": float, "taker_buy_quote_volume": float})
    # 净流（quote 计价）= 主动买 - 主动卖
    d["net_flow"] = 2.0 * d.taker_buy_quote_volume - d.quote_volume
    d["t"] = pd.to_datetime(d.open_time, unit="ms", utc=True)
    return d.sort_values("t").drop_duplicates("t").reset_index(drop=True)


def get_klines(symbol: str, source: str, start_ms: int, end_ms: int, days: list):
    os.makedirs(CACHE_DIR, exist_ok=True)
    cp = _cache_path(symbol)
    if os.path.exists(cp):
        d = pd.read_feather(cp)
        lo, hi = d.t.min().timestamp() * 1000, d.t.max().timestamp() * 1000
        if lo <= start_ms and hi >= end_ms - 60_000:
            return d
    if source == "vision":
        d = fetch_vision(symbol, days)
    else:
        d = fetch_api(symbol, start_ms, end_ms,
                      market="spot" if source == "spot" else "futures")
    if d is not None and len(d):
        d.to_feather(cp)
    return d


# ------------------------------------------------------------------ 代理计算

def proxy_netflow(kl: pd.DataFrame, t_end, minutes: int, mode: str) -> float:
    """
    在 t_end 时刻、窗口长 minutes 的净流代理。

    mode='trailing'         : 滚动窗口 (t_end - minutes, t_end]
    mode='aligned_partial'  : 自最近的整点/整档边界起到 t_end 的部分桶
                              （若接口是"本档累计"语义，这个会更贴合）
    """
    if kl is None or not len(kl):
        return None
    if mode == "trailing":
        lo = t_end - pd.Timedelta(minutes=minutes)
    else:
        lo = t_end.floor(f"{minutes}min")
        if lo == t_end:
            return None
    seg = kl[(kl.t >= lo) & (kl.t < t_end)]
    if not len(seg):
        return None
    return float(seg.net_flow.sum())


# ------------------------------------------------------------------ 主流程

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["api", "spot", "vision"], default="api")
    ap.add_argument("--max-symbols", type=int, default=0, help="0=全部，调试用")
    args = ap.parse_args()
    global _MARKET
    _MARKET = "spot" if args.source == "spot" else "futures"

    snaps = load_snapshots()
    t_min, t_max = snaps.t.min(), snaps.t.max()
    # 多留 1h 头部，最长窗口需要
    start_ms = int((t_min - pd.Timedelta(hours=2)).timestamp() * 1000)
    end_ms = int((t_max + pd.Timedelta(minutes=2)).timestamp() * 1000)
    days = sorted({d.strftime("%Y-%m-%d") for d in
                   pd.date_range(t_min - pd.Timedelta(hours=2), t_max, freq="D",
                                 tz="UTC")}
                  | {t_max.strftime("%Y-%m-%d")})

    symbols = sorted(snaps.symbol.unique())
    if args.max_symbols:
        symbols = symbols[:args.max_symbols]

    print(f"[info] 快照 {snaps.t.nunique()} 个 | 时段 {t_min} -> {t_max}")
    print(f"[info] 币种 {len(symbols)} | source={args.source} | days={days}")

    klines, missing = {}, []
    for i, sym in enumerate(symbols, 1):
        d = get_klines(sym, args.source, start_ms, end_ms, days)
        if d is None or not len(d):
            missing.append(sym)
        else:
            klines[sym] = d
        if i % 25 == 0:
            print(f"[info]   {i}/{len(symbols)} 已取，缺 {len(missing)}", flush=True)
    print(f"[info] K 线到手 {len(klines)} | 缺失 {len(missing)}")
    if missing:
        print(f"[info] 缺失标的(非币安合约或无数据): {missing}")

    # ---- 缺失标的对排名的污染程度 ----
    top20 = snaps[snaps.side == "top"]
    miss_share = top20.symbol.isin(missing).mean() * 100
    print(f"[info] 缺失标的占 VergeX top20 行数比例: {miss_share:.1f}%")

    # ---- 预计算分钟级净流累计和 ----
    # 逐币逐快照做 dataframe 切片是 O(快照×币×窗口)，63 万次太慢。
    # 改为：把所有币对齐到同一条分钟轴，累计求和后用两点差取任意窗口。
    # cum[x] 定义为「open_time < x 的净流之和」，故窗口 [lo, hi) = cum[hi] - cum[lo]。
    lo_t = (t_min - pd.Timedelta(hours=2)).floor("min")
    hi_t = t_max.ceil("min")
    minute_idx = pd.date_range(lo_t, hi_t, freq="1min", tz="UTC")
    mat = pd.DataFrame({s: kl.set_index("t").net_flow.reindex(minute_idx).fillna(0.0)
                        for s, kl in klines.items()}, index=minute_idx)
    cum = mat.cumsum().shift(1).fillna(0.0)
    print(f"[info] 净流累计矩阵 {cum.shape[0]} 分钟 × {cum.shape[1]} 币")

    def window_vec(t_end, minutes, mode):
        """返回该时刻全宇宙的净流代理 Series；不可用返回 None。"""
        # 只用已收盘的整分钟 bar：hi 取 floor 而非 ceil，宁可少算 <1 分钟，
        # 也不把快照时刻之后才收盘的那根 bar 算进去（Lookahead 清单第 3 条）。
        hi = t_end.floor("min")
        lo = (hi - pd.Timedelta(minutes=minutes)) if mode == "trailing"             else t_end.floor(f"{minutes}min")
        if lo >= hi or lo < minute_idx[0] or hi > minute_idx[-1]:
            return None
        return cum.loc[hi] - cum.loc[lo]

    rows = []
    for mode in ("trailing", "aligned_partial"):
        for dur, minutes in DUR_MIN.items():
            sub = snaps[snaps.duration == dur]
            for t, g in sub.groupby("t"):
                vec = window_vec(t, minutes, mode)
                if vec is None:
                    continue
                px = vec[vec != 0.0]
                if len(px) < 10:
                    continue
                pser = px.sort_values(ascending=False)
                prank = {s: i + 1 for i, s in enumerate(pser.index)}

                gt = g[(g.side == "top") & g.symbol.isin(px.index)]
                if len(gt) < 5:
                    continue
                pv = gt.symbol.map(px)
                rows.append({
                    "mode": mode, "duration": dur, "t": t,
                    "n_matched": len(gt),
                    "pearson": gt.amount.corr(pv, method="pearson"),
                    "spearman_val": gt.amount.corr(pv, method="spearman"),
                    "spearman_rank": gt["rank"].corr(gt.symbol.map(prank),
                                                     method="spearman"),
                    "overlap20": len(set(g[g.side == "top"].symbol)
                                     & set(pser.index[:20])),
                    # 策略实际用的是 median_rank<=3，top20 对得上但 top3 对不上
                    # 等于代理不可用，必须单独测这一档。
                    "overlap3": len(set(g[(g.side == "top") & (g["rank"] <= 3)].symbol)
                                    & set(pser.index[:3])),
                    "top3_in_proxy10": len(set(g[(g.side == "top") & (g["rank"] <= 3)].symbol)
                                           & set(pser.index[:10])),
                    "scale_ratio": float((pv / gt.amount).median()),
                })

    if not rows:
        sys.exit("[fatal] 没有可比对的样本")
    res = pd.DataFrame(rows)
    out = os.path.join(DATA_DIR, "stepA_calibration_raw.csv")
    res.to_csv(out, index=False)

    print("\n" + "=" * 78)
    print("Step A 校准结果（探索性——非结论，禁止引用为证据）")
    print("=" * 78)
    agg = res.groupby(["mode", "duration"]).agg(
        n=("t", "size"),
        匹配币数=("n_matched", "median"),
        Pearson=("pearson", "median"),
        Spearman值=("spearman_val", "median"),
        Spearman秩=("spearman_rank", "median"),
        重叠20=("overlap20", "median"),
        重叠3=("overlap3", "mean"),
        真top3落在代理前10=("top3_in_proxy10", "mean"),
        尺度比=("scale_ratio", "median"),
    ).round(3)
    print(agg.to_string())
    print(f"\n[info] 明细已写入 {out}")
    print("\n解读提示：Spearman值/秩 接近 1 且 重叠20 接近 20 => 代理可用；")
    print("          尺度比稳定（不随窗口漂移）说明口径一致，偏离 1 只是单位/交易所范围差异。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
