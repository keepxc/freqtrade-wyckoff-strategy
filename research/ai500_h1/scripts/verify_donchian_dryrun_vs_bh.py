#!/usr/bin/env python3
"""唐奇安 V1（20/10，日线，long-only）真实战绩：dry-run 对照 + 2 年样本 + vs 买入持有。

规则（取自 user_data/strategies/DonchianV1.py）：
  don_upper = high.rolling(20).max().shift(1)   → close > don_upper 入场
  don_exit  = low.rolling(10).min().shift(1)    → close < don_exit  出场
成本：0.10%/边（OKX 现货吃单 + 缓冲）。
"""
import urllib.request, json, time
import pandas as pd

PX = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}
def okx_daily(limit=300):
    op = urllib.request.build_opener(urllib.request.ProxyHandler(PX))
    out, after = [], ""
    while len(out) < limit:
        url = f"https://www.okx.com/api/v5/market/history-candles?instId=BTC-USDT&bar=1D&limit=100{after}"
        r = json.load(op.open(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=25))
        d = r.get("data", [])
        if not d: break
        out += d
        after = "&after=" + d[-1][0]
    df = pd.DataFrame([(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4])) for x in out],
                      columns=["ts", "open", "high", "low", "close"])
    df["date"] = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.tz_convert("Asia/Shanghai").dt.normalize().dt.tz_localize(None)
    return df.sort_values("date").drop_duplicates("date").reset_index(drop=True)

loc = pd.read_feather("/home/gyan/freqtrade/user_data/data/okx/BTC_USDT-1d.feather")
loc["date"] = pd.to_datetime(loc["date"]).dt.tz_localize(None)
fresh = okx_daily(300)
print(f"本地 {len(loc)} 根 → {loc.date.max():%Y-%m-%d} | 新拉 {len(fresh)} 根 → {fresh.date.max():%Y-%m-%d}")
df = pd.concat([loc[["date", "open", "high", "low", "close"]], fresh[["date", "open", "high", "low", "close"]]])
df = df.drop_duplicates("date").sort_values("date").reset_index(drop=True)
print(f"合并 {len(df)} 根：{df.date.min():%Y-%m-%d} → {df.date.max():%Y-%m-%d}  末收 {df.close.iloc[-1]:,.0f}")

df["up"] = df.high.rolling(20).max().shift(1)
df["dn"] = df.low.rolling(10).min().shift(1)
FEE = 0.001

def run(start):
    d = df[df.date >= start].reset_index(drop=True)
    pos, entry, trades = False, 0.0, []
    for i, r in d.iterrows():
        if not pos and pd.notna(r.up) and r.close > r.up:
            pos, entry, et = True, r.close * (1 + FEE), r.date
        elif pos and pd.notna(r.dn) and r.close < r.dn:
            trades.append((et, r.date, entry, r.close * (1 - FEE), (r.close * (1 - FEE) / entry - 1) * 100))
            pos = False
    eq = 1.0
    for t in trades: eq *= (1 + t[4] / 100)
    bh = d.close.iloc[-1] / d.close.iloc[0] - 1
    days = (d.date.iloc[-1] - d.date.iloc[0]).days
    print(f"\n=== 从 {start} 起（{days} 天）===")
    print(f"  唐奇安: {len(trades)} 笔 | 累计 {((eq-1)*100):+.2f}% | 买入持有 {(bh*100):+.2f}% | 差 {((eq-1-bh)*100):+.2f}pp")
    if trades:
        holds = [(t[1] - t[0]).days for t in trades]
        wins = sum(1 for t in trades if t[4] > 0)
        print(f"  持仓中位 {sorted(holds)[len(holds)//2]} 天 | 胜率 {wins}/{len(trades)} | 最好 {max(t[4] for t in trades):+.1f}% | 最差 {min(t[4] for t in trades):+.1f}%")
        if pos: print(f"  ⚠️ 样本末端仍在仓（{et:%Y-%m-%d} 开 @{entry:,.0f}，未计入上面累计）")
        print("  逐笔: " + "  ".join(f"{t[0]:%y-%m-%d}→{t[1]:%y-%m-%d} {t[4]:+.1f}%" for t in trades))

for s in ["2025-01-01", "2025-07-01", "2026-01-01", "2026-07-15"]:
    if df.date.min() <= pd.Timestamp(s):
        run(s)

# dry-run 实盘对照（同一资本口径）
print("\n=== 实盘 dry-run 对照（07-15 起，同资本）===")
d = df[df.date >= "2026-07-15"].reset_index(drop=True)
print(f"  BTC 买入持有: {d.close.iloc[0]:,.0f} → {d.close.iloc[-1]:,.0f} = {((d.close.iloc[-1]/d.close.iloc[0]-1)*100):+.2f}%")
print(f"  唐奇安实盘: 已实现 +15.81%（07-15→09-16 65130→75656）+ 09-16~09-22 空仓 + 现仓 86613→{d.close.iloc[-1]:,.0f}")
print(f"  同窗口窗口内 65130→75656 = {(75656.1/65130.1-1)*100:+.2f}%（毛），实盘净 +15.81% ⇒ 成本约 {(75656.1/65130.1-1-0.1581)*100:.2f}pp")
print(f"  空仓期(09-16→09-22) BTC: 75656 → {float(df[df.date=='2026-09-22'].close.iloc[0]):,.0f} = {(float(df[df.date=='2026-09-22'].close.iloc[0])/75656.1-1)*100:+.2f}%  ← 错过的")
print(f"  现仓 unrealized: 86613 → {d.close.iloc[-1]:,.0f} = {((d.close.iloc[-1]/86613.3-1)*100):+.2f}%")
