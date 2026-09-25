#!/usr/bin/env python3
"""跨资产唐奇安（20/10 日线 long-only）：给它最好的机会，看"广度"是不是缺失的那一块。

- 数据：币安现货日线（走代理），2024-01-01 起（含一段牛 + 一段震荡）
- 规则：don_upper=high.rolling(20).max().shift(1) 入场；don_exit=low.rolling(10).min().shift(1) 出场
- 组合：等资金 1/N 分配给每个资产，各自独立持/空；对照等权买入持有
- 成本：0.1%/边
"""
import json, os, time, urllib.parse, urllib.request
import pandas as pd

PX = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}
OP = urllib.request.build_opener(urllib.request.ProxyHandler(PX))
CACHE = "/tmp/cache_daily"
os.makedirs(CACHE, exist_ok=True)
SYMS = ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "AVAX", "DOGE", "LINK", "LTC",
        "BCH", "ATOM", "NEAR", "APT", "ARB", "OP", "INJ", "SUI", "FIL", "TRX"]
START = "2024-01-01"
FEE = 0.001


def fetch(sym):
    fn = f"{CACHE}/{sym}.csv"
    if os.path.exists(fn):
        return pd.read_csv(fn, parse_dates=["date"])
    url = ("https://api.binance.com/api/v3/klines?" +
           urllib.parse.urlencode({"symbol": sym + "USDT", "interval": "1d", "limit": 1000}))
    try:
        r = json.load(OP.open(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=25))
    except Exception as e:
        print(f"  !! {sym} 拉取失败 {type(e).__name__}: {str(e)[:60]}")
        return None
    df = pd.DataFrame([(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4])) for x in r],
                      columns=["ts", "open", "high", "low", "close"])
    df["date"] = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.tz_convert("Asia/Shanghai").dt.normalize().dt.tz_localize(None)
    df = df.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    df.to_csv(fn, index=False)
    return df


def simulate(df):
    d = df.copy()
    d["up"] = d.high.rolling(20).max().shift(1)
    d["dn"] = d.low.rolling(10).min().shift(1)
    d["pos"] = 0
    pos = False
    trades = 0
    for i, r in d.iterrows():
        if not pos and pd.notna(r.up) and r.close > r.up:
            pos, trades = True, trades + 1
        elif pos and pd.notna(r.dn) and r.close < r.dn:
            pos = False
        d.at[i, "pos"] = 1 if pos else 0
    d["ret"] = d.close.pct_change().fillna(0)
    d["strat"] = d.pos.shift(1).fillna(0) * d.ret - (d.pos.diff().abs().fillna(0)) * FEE
    return d, trades


data = {}
for s in SYMS:
    df = fetch(s)
    if df is None or len(df) < 60:
        continue
    data[s] = df[df.date >= START].reset_index(drop=True)
    time.sleep(0.15)
print(f"取得 {len(data)} 个资产：{', '.join(data)}")

idx = None
for d in data.values():
    ds = set(d.date)
    idx = ds if idx is None else (idx & ds)
idx = sorted(idx)
print(f"共同交易日 {len(idx)}：{idx[0]:%Y-%m-%d} → {idx[-1]:%Y-%m-%d}")

rows, per = {}, {}
for s, d in data.items():
    dd = d[d.date.isin(idx)].reset_index(drop=True)
    dd, trades = simulate(dd)
    rows[s] = dd.set_index("date")
    per[s] = (trades, (1 + dd.strat).prod() - 1, dd.close.iloc[-1] / dd.close.iloc[0] - 1, dd.strat)

S = pd.DataFrame({s: rows[s].strat for s in rows}).fillna(0)
B = pd.DataFrame({s: rows[s].ret for s in rows}).fillna(0)
port = S.mean(axis=1).add(1).cumprod()
bench = B.mean(axis=1).add(1).cumprod()

print("\n=== 单资产（同一套 20/10 规则）===")
print(f"  {'币':<6}{'笔数':>5}{'策略':>10}{'买入持有':>11}{'差':>10}")
for s, (n, st, bh, _) in sorted(per.items(), key=lambda kv: -kv[1][1]):
    print(f"  {s:<6}{n:>5}{st*100:>9.1f}%{bh*100:>10.1f}%{(st-bh)*100:>9.1f}pp")
pos_cnt = sum(1 for s, (n, st, bh, _) in per.items() if st > bh)
print(f"  → 跑赢买入持有的资产：{pos_cnt}/{len(per)}")

def mdd(eq):
    return float((eq / eq.cummax() - 1).min()) * 100

print("\n=== 组合（等资金 1/N，各自独立持/空）===")
print(f"  唐奇安组合: {((port.iloc[-1]-1)*100):+.2f}%   最大回撤 {mdd(port):.1f}%")
print(f"  等权买入持有: {((bench.iloc[-1]-1)*100):+.2f}%   最大回撤 {mdd(bench):.1f}%")
print(f"  差: {((port.iloc[-1]-bench.iloc[-1])*100):+.2f}pp")

print("\n=== 广度效应（资产数 N 从 1 → 全部，随机 200 次抽样）===")
import random
random.seed(5)
for N in (1, 3, 5, 10, len(per)):
    vals = []
    for _ in range(200):
        pick = random.sample(list(per), min(N, len(per)))
        eq = S[pick].mean(axis=1).add(1).cumprod()
        vals.append(eq.iloc[-1] - 1)
    vals.sort()
    print(f"  N={N:<3} 组合收益 中位 {vals[len(vals)//2]*100:+7.1f}%   p10 {vals[int(len(vals)*.1)]*100:+7.1f}%   p90 {vals[int(len(vals)*.9)]*100:+7.1f}%")
