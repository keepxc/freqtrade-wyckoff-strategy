"""
AI500 trailing 参数回放回测 v2（真实 K 线路径）
==============================================
用 CSV 排名信号 + OKX 30m 真实 K 线，逐根 K 线模拟三种退出策略。

策略对比：
  基线: EXIT_RANK=10 + ATR×2 硬止损（当前）
  A:    EXIT_RANK=15 + ATR×2
  B:    EXIT_RANK=10 + trailing（涨 3% 后回撤 2% 止盈）
  C:    EXIT_RANK=15 + trailing（涨 3% 后回撤 2% 止盈）

注意：探索性分析，5 天样本，结论仅供参考。
"""
import pandas as pd
import numpy as np

# ========== 加载数据 ==========
print("加载数据...")
df = pd.read_csv("/home/gyan/freqtrade/user_data/ai500_netflow_oi_history.csv",
                 parse_dates=["fetched_at_utc"])
df = df.sort_values("fetched_at_utc").reset_index(drop=True)

klines = pd.read_pickle("/home/gyan/freqtrade/user_data/ai500_klines_30m.pkl")

# 从 DB 取入场信号（用真实成交的开仓时间+币种+开仓价作为入场基准）
import sqlite3
conn = sqlite3.connect("/home/gyan/freqtrade/user_data/tradesv3.ai500dryrun.sqlite")
trades = pd.read_sql_query(
    "SELECT pair, open_date, open_rate, close_date, close_profit FROM trades "
    "WHERE close_date IS NOT NULL", conn)
conn.close()
trades["open_date"] = pd.to_datetime(trades["open_date"], utc=True)
trades["close_date"] = pd.to_datetime(trades["close_date"], utc=True)
trades["symbol"] = trades["pair"].str.split("/").str[0] + "USDT"
print(f"真实平仓: {len(trades)} 笔, 涉及 {trades['symbol'].nunique()} 币")

# ========== 模拟函数 ==========
def simulate_trade(row, exit_rank, trail_enabled=False, trail_offset=0.03, trail_drop=0.02):
    """逐根 K 线模拟：入场后检查排名退出/trailing/硬止损，返回模拟退出收益。"""
    sym = row["symbol"]
    open_dt = row["open_date"]
    open_rate = row["open_rate"]

    # 该币 K 线（klines key 不带 USDT 后缀）
    sym_no_usdt = row["symbol"][:-4]
    if sym_no_usdt not in klines:
        return None
    kdf = klines[sym_no_usdt]
    kdf = kdf[(kdf["date"] >= open_dt)].reset_index(drop=True)
    if kdf.empty:
        return None

    # 该币持仓期间的排名信号（按时间索引）
    sym_df = df[(df["symbol"] == sym) & (df["fetched_at_utc"] >= open_dt)].copy()
    sym_df["ts"] = sym_df["fetched_at_utc"].astype("int64") // 10**9
    # CSV 无覆盖时（开仓早于 8-18），无排名信号 → 只走 trailing/超时退出

    # 逐根 K 线模拟
    peak = open_rate
    trail_active = False
    trail_high = open_rate

    for _, k in kdf.iterrows():
        k_time = k["date"]
        k_ts = int(k_time.timestamp())
        k_high = k["high"]
        k_low = k["low"]
        k_close = k["close"]

        # 更新峰值
        peak = max(peak, k_high)

        # 1. 排名退出检查（该 K 线期间有排名信号且 rank > exit_rank）
        rank_sig = sym_df[(sym_df["ts"] >= k_ts - 1800) & (sym_df["ts"] <= k_ts + 1800)]
        if not rank_sig.empty:
            ranks = rank_sig["netflow_rank"].dropna()
            if not ranks.empty and ranks.max() > exit_rank:
                return (k_close / open_rate) - 1  # 排名退出

        # 2. trailing 止盈
        if trail_enabled:
            profit = (k_high / open_rate) - 1
            if profit >= trail_offset:
                trail_active = True
            if trail_active:
                trail_high = max(trail_high, k_high)
                drop_from_high = (trail_high - k_close) / trail_high
                if drop_from_high >= trail_drop:
                    return (k_close / open_rate) - 1  # trailing 止盈

        # 3. ATR 硬止损（简化：用前 40 根 K 线的 ATR）
        # 简化模拟：跳过硬止损，重点对比排名 vs trailing 的差异
        # 实际 hard stop 在所有策略下相同，不影响相对比较

        # 4. 超时退出（真实平仓时间）
        if k_time >= row["close_date"]:
            return (k_close / open_rate) - 1

    return None  # 未退出（异常）

# ========== 运行对比 ==========
configs = [
    {"name": "基线 EXIT_RANK=10", "exit_rank": 10, "trail": False},
    {"name": "A: EXIT_RANK=15", "exit_rank": 15, "trail": False},
    {"name": "B: rank10 + trail 3%/2%", "exit_rank": 10, "trail": True},
    {"name": "C: rank15 + trail 3%/2%", "exit_rank": 15, "trail": True},
    {"name": "D: rank15 + trail 5%/3%", "exit_rank": 15, "trail": True, "offset": 0.05, "drop": 0.03},
]

print("\n=== 回放结果对比（逐 K 线模拟，5 天样本）===")
print(f"{'策略':28s} | {'笔数':4s} | {'均值':8s} | {'合计':8s} | {'胜率':6s} | {'>5%':5s}")
real_mean = trades["close_profit"].mean() * 100
real_sum = trades["close_profit"].sum() * 100

for cfg in configs:
    results = []
    for _, t in trades.iterrows():
        sim = simulate_trade(t, cfg["exit_rank"], cfg.get("trail", False),
                             cfg.get("offset", 0.03), cfg.get("drop", 0.02))
        if sim is not None:
            results.append(sim)
    if results:
        arr = np.array(results)
        wins = (arr > 0).mean() * 100
        big = (arr > 0.05).mean() * 100
        print(f"{cfg['name']:28s} | {len(arr):4d} | {arr.mean()*100:+6.2f}% | {arr.sum()*100:+7.2f}% | {wins:5.1f}% | {big:5.1f}%")

print(f"\n真实成交: 均值 {real_mean:+.2f}% | 合计 {real_sum:+.2f}%")

print("\n说明：模拟用 OKX 30m 真实 K 线 + CSV 排名信号，逐根 K 线判断退出。")
print("5 天样本不足以下统计结论，仅供参考方向。")
