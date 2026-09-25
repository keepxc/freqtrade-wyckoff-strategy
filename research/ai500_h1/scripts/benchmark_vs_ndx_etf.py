#!/usr/bin/env python3
"""同窗口对照：分散化唐奇安组合 vs 纳指100ETF(513300) vs 两者组合。

窗口：2024-01-01 → 2026-09-25（与 DONCHIAN_BREADTH_FINDINGS.md 同一段）
指标：累计收益、年化、最大回撤、Calmar（年化/|MDD|）
"""
import io, json, os, urllib.parse, urllib.request
import pandas as pd

PROXY = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}
OP_P = urllib.request.build_opener(urllib.request.ProxyHandler(PROXY))
OP_D = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 国内直连
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}


def get(url, direct=False, timeout=25):
    return (OP_D if direct else OP_P).open(urllib.request.Request(url, headers=UA), timeout=timeout).read()


# ---------- 1. 513300 纳指100ETF（国内直连） ----------
def fetch_513300():
    fn = "/tmp/cache_daily/513300.csv"
    if os.path.exists(fn):
        return pd.read_csv(fn, parse_dates=["date"])
    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=1.513300"
           "&fields1=f1,f2,f3,f4,f5&fields2=f51,f53&klt=101&fqt=1&beg=20240101&end=20500101")
    try:
        j = json.loads(get(url, direct=True).decode())
        kl = (j.get("data") or {}).get("klines") or []
        df = pd.DataFrame([(x.split(",")[0], float(x.split(",")[1])) for x in kl], columns=["date", "close"])
        df["date"] = pd.to_datetime(df["date"])
        df.to_csv(fn, index=False)
        print(f"  513300: {len(df)} 行  {df.date.min():%Y-%m-%d} → {df.date.max():%Y-%m-%d}")
        return df
    except Exception as e:
        print(f"  !! 513300 直连失败({type(e).__name__})，试代理")
        j = json.loads(get(url).decode())
        kl = (j.get("data") or {}).get("klines") or []
        df = pd.DataFrame([(x.split(",")[0], float(x.split(",")[1])) for x in kl], columns=["date", "close"])
        df["date"] = pd.to_datetime(df["date"]); df.to_csv(fn, index=False)
        print(f"  513300: {len(df)} 行")
        return df


# ---------- 2. 纳指100 指数（stooq，走代理） ----------
def fetch_ndx():
    for sym in ("^ndq", "^ndx"):
        try:
            raw = get(f"https://stooq.com/q/d/l/?s={urllib.parse.quote(sym)}&i=d").decode()
            if "Date" not in raw.split("\n")[0]:
                continue
            df = pd.read_csv(io.StringIO(raw))
            df.columns = [c.lower() for c in df.columns]
            df["date"] = pd.to_datetime(df["date"])
            print(f"  {sym}: {len(df)} 行  {df.date.min():%Y-%m-%d} → {df.date.max():%Y-%m-%d}")
            return df[["date", "close"]]
        except Exception as e:
            print(f"  !! {sym} 失败 {type(e).__name__}")
    return None


# ---------- 3. 唐奇安组合（复用缓存日线） ----------
def donchian_portfolio(start="2024-01-01"):
    FEE = 0.001
    syms = [f[:-4] for f in os.listdir("/tmp/cache_daily") if f.endswith(".csv") and len(f) > 6 and f[:-4].isupper()]
    frames_s, frames_b = [], []
    for s in syms:
        d = pd.read_csv(f"/tmp/cache_daily/{s}.csv", parse_dates=["date"])
        d = d[d.date >= start].reset_index(drop=True)
        if len(d) < 60:
            continue
        d["up"] = d.high.rolling(20).max().shift(1)
        d["dn"] = d.low.rolling(10).min().shift(1)
        pos, p = False, []
        for _, r in d.iterrows():
            if not pos and pd.notna(r.up) and r.close > r.up:
                pos = True
            elif pos and pd.notna(r.dn) and r.close < r.dn:
                pos = False
            p.append(1 if pos else 0)
        d["pos"] = p
        d["ret"] = d.close.pct_change().fillna(0)
        d["strat"] = d.pos.shift(1).fillna(0) * d.ret - d.pos.diff().abs().fillna(0) * FEE
        frames_s.append(d.set_index("date")["strat"].rename(s))
        frames_b.append(d.set_index("date")["ret"].rename(s))
    S = pd.concat(frames_s, axis=1).dropna(how="all").fillna(0)
    B = pd.concat(frames_b, axis=1).dropna(how="all").fillna(0)
    print(f"  唐奇安组合: {S.shape[1]} 个资产 × {len(S)} 天")
    return S.mean(axis=1), B.mean(axis=1)


def stats(r, label):
    eq = (1 + r).cumprod()
    days = (r.index[-1] - r.index[0]).days
    tot = eq.iloc[-1] - 1
    ann = (1 + tot) ** (365 / days) - 1
    mdd = float((eq / eq.cummax() - 1).min())
    print(f"  {label:<28} 累计 {tot*100:>+8.2f}%   年化 {ann*100:>+6.2f}%   MDD {mdd*100:>7.1f}%   Calmar {ann/abs(mdd) if mdd else float('nan'):>5.2f}")
    return eq


print("=== 数据 ===")
ndx = fetch_ndx()
etf = fetch_513300()
ds, db = donchian_portfolio()

print("\n=== 同窗口对照（2024-01-01 → 2026-09-25，日频）===")
series = {}
series["唐奇安组合(20币)"] = ds
if ndx is not None:
    n = ndx[(ndx.date >= "2024-01-01") & (ndx.date <= "2026-12-31")].set_index("date")["close"]
    series["纳指100 (NDX, USD)"] = n.pct_change().dropna()
if etf is not None:
    e = etf.set_index("date")["close"].pct_change().dropna()
    series["513300 (CNY 场内)"] = e
eqs = {}
for k, v in series.items():
    eqs[k] = stats(v, k)

# BTC 买入持有
btc = pd.read_csv("/tmp/cache_daily/BTC.csv", parse_dates=["date"])
btc = btc[btc.date >= "2024-01-01"].set_index("date")["close"].pct_change().dropna()
eqs["BTC 买入持有"] = stats(btc, "BTC 买入持有")

# 组合：唐奇安 + 513300，按比例月度再平衡
if "513300 (CNY 场内)" in series:
    idx = ds.index.intersection(series["513300 (CNY 场内)"].index)
    a, b = ds.reindex(idx).fillna(0), series["513300 (CNY 场内)"].reindex(idx).fillna(0)
    for w in (0.3, 0.5, 0.7):
        r = w * a + (1 - w) * b
        stats(r, f"唐奇安{w:.0%} + 513300{1-w:.0%}")
print("\n=== 相关性（日频，同窗口）===")
if "513300 (CNY 场内)" in series:
    idx = ds.index.intersection(series["513300 (CNY 场内)"].index)
    print(f"  唐奇安组合 vs 513300: corr = {ds.reindex(idx).corr(series['513300 (CNY 场内)'].reindex(idx)):+.3f}")
if ndx is not None:
    idx = ds.index.intersection(series["纳指100 (NDX, USD)"].index)
    print(f"  唐奇安组合 vs NDX:    corr = {ds.reindex(idx).corr(series['纳指100 (NDX, USD)'].reindex(idx)):+.3f}")
