"""
拉取 OKX 30m K 线（覆盖 AI500 88 笔平仓涉及的币种和时段）
用于 trailing 参数回放回测。
"""
import ccxt
import pandas as pd
from datetime import datetime, timezone, timedelta

# OKX 交易所（走代理）
exchange = ccxt.okx({
    "enableRateLimit": True,
    "proxies": {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"},
})

# 从 DB 取涉及的币
import sqlite3
conn = sqlite3.connect("/home/gyan/freqtrade/user_data/tradesv3.ai500dryrun.sqlite")
trades = pd.read_sql_query(
    "SELECT DISTINCT pair, open_date, close_date FROM trades WHERE close_date IS NOT NULL", conn)
conn.close()

symbols = trades["pair"].str.split("/").str[0].unique()
print(f"涉及 {len(symbols)} 个币")

# 时间范围
start = pd.to_datetime(trades["open_date"], utc=True).min() - timedelta(hours=24)
end = pd.to_datetime(trades["close_date"], utc=True).max() + timedelta(hours=24)
print(f"时间范围: {start} → {end}")

tf = "30m"
all_klines = {}
for sym in symbols:
    pair = f"{sym}/USDT:USDT"
    try:
        since = int(start.timestamp() * 1000)
        klines = exchange.fetch_ohlcv(pair, tf, since=since, limit=1000)
        if not klines:
            continue
        # 循环拉全
        while klines and klines[-1][0] < int(end.timestamp() * 1000):
            more = exchange.fetch_ohlcv(pair, tf, since=klines[-1][0] + 1, limit=1000)
            if not more:
                break
            klines.extend(more)
        df = pd.DataFrame(klines, columns=["ts", "open", "high", "low", "close", "volume"])
        df["date"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        all_klines[sym] = df
        print(f"  {sym}: {len(df)} 根 K 线")
    except Exception as e:
        print(f"  {sym}: 失败 {e}")

# 保存
out = {}
for sym, df in all_klines.items():
    out[sym] = df[["date", "open", "high", "low", "close", "volume"]]
pd.to_pickle(out, "/home/gyan/freqtrade/user_data/ai500_klines_30m.pkl")
print(f"\n保存 {len(out)} 个币到 ai500_klines_30m.pkl")
