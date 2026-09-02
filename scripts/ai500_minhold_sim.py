"""
AI500 最小持仓 2h 改动验证
==========================
对比：改前（rank 立即退出）vs 改后（开仓 2h 内屏蔽 rank 退出）。
用真实 K 线 + CSV 排名信号逐根模拟。
"""
import pandas as pd
import numpy as np
import sqlite3

df = pd.read_csv("/home/gyan/freqtrade/user_data/ai500_netflow_oi_history.csv",
                 parse_dates=["fetched_at_utc"])
df = df.sort_values("fetched_at_utc").reset_index(drop=True)
klines = pd.read_pickle("/home/gyan/freqtrade/user_data/ai500_klines_30m.pkl")

conn = sqlite3.connect("/home/gyan/freqtrade/user_data/tradesv3.ai500dryrun.sqlite")
trades = pd.read_sql_query(
    "SELECT pair, open_date, open_rate, close_date, close_profit FROM trades "
    "WHERE close_date IS NOT NULL", conn)
conn.close()
trades["open_date"] = pd.to_datetime(trades["open_date"], utc=True)
trades["close_date"] = pd.to_datetime(trades["close_date"], utc=True)
trades["symbol"] = trades["pair"].str.split("/").str[0] + "USDT"

def simulate(row, min_hold_sec=0, exit_rank=10):
    """min_hold_sec>0 时开仓后该秒数内屏蔽 rank 退出"""
    sym = row["symbol"]
    sym_no = sym[:-4]
    if sym_no not in klines:
        return None
    kdf = klines[sym_no]
    kdf = kdf[kdf["date"] >= row["open_date"]].reset_index(drop=True)
    if kdf.empty:
        return None
    sym_df = df[(df["symbol"] == sym) & (df["fetched_at_utc"] >= row["open_date"])].copy()
    sym_df["ts"] = sym_df["fetched_at_utc"].astype("int64") // 10**9

    for _, k in kdf.iterrows():
        k_ts = int(k["date"].timestamp())
        k_close = k["close"]
        held = (k["date"] - row["open_date"]).total_seconds()
        # rank 退出（受最小持仓约束）
        if held >= min_hold_sec:
            rank_sig = sym_df[(sym_df["ts"] >= k_ts - 1800) & (sym_df["ts"] <= k_ts + 1800)]
            if not rank_sig.empty:
                ranks = rank_sig["netflow_rank"].dropna()
                if not ranks.empty and ranks.max() > exit_rank:
                    return (k_close / row["open_rate"]) - 1
        # 超时退出（真实平仓时间 + 4h 宽容）
        if k["date"] >= row["close_date"] + pd.Timedelta(hours=4):
            return (k_close / row["open_rate"]) - 1
        # 接近真实平仓时间（模拟原策略实际退出点）
        if k["date"] >= row["close_date"] and held >= min_hold_sec:
            return (k_close / row["open_rate"]) - 1
    return None

# 对比
configs = [
    ("改前: rank 立即退出", 0),
    ("改后: 最小持仓 2h", 7200),
]
print("=== 回放对比（5 天样本，88 笔）===")
print(f"{'策略':22s} | {'笔数':4s} | {'均值':8s} | {'合计':8s} | {'胜率':6s}")
for name, min_hold in configs:
    results = []
    for _, t in trades.iterrows():
        r = simulate(t, min_hold)
        if r is not None:
            results.append(r)
    arr = np.array(results)
    print(f"{name:22s} | {len(arr):4d} | {arr.mean()*100:+6.2f}% | {arr.sum()*100:+7.2f}% | {(arr>0).mean()*100:5.1f}%")

# 逐笔对比：被 2h 屏蔽"救回来"或"害了"的单
print("\n=== 逐笔差异（2h 屏蔽 vs 立即退出）===")
diffs = []
for _, t in trades.iterrows():
    a = simulate(t, 0)
    b = simulate(t, 7200)
    if a is not None and b is not None:
        diffs.append((t["symbol"], a, b, b - a))
saved = [d for d in diffs if d[3] > 0.005]
hurt = [d for d in diffs if d[3] < -0.005]
print(f"被 2h 屏蔽救回（收益增加>0.5pp）: {len(saved)} 笔")
for s in sorted(saved, key=lambda x: -x[3])[:5]:
    print(f"  {s[0]}: 立即退 {s[1]*100:+.2f}% → 2h退 {s[2]*100:+.2f}% (Δ{s[3]*100:+.2f}pp)")
print(f"被 2h 屏蔽害了（收益减少>0.5pp）: {len(hurt)} 笔")
for h in sorted(hurt, key=lambda x: x[3])[:5]:
    print(f"  {h[0]}: 立即退 {h[1]*100:+.2f}% → 2h退 {h[2]*100:+.2f}% (Δ{h[3]*100:+.2f}pp)")
