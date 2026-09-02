"""
AI500 trailing 参数回放回测
============================
用 ai500_netflow_oi_history.csv 的排名信号 + OKX 30m K 线价格，
模拟三种退出策略在同一批入场信号下的表现。

注意：探索性分析，样本仅 5 天，结论仅供参考（策略文件头已声明不可回测）。
"""
import sqlite3
import pandas as pd
import numpy as np
from datetime import timedelta

HISTORY_CSV = "/home/gyan/freqtrade/user_data/ai500_netflow_oi_history.csv"
TRADES_DB = "/home/gyan/freqtrade/user_data/tradesv3.ai500dryrun.sqlite"

# ========== 1. 加载数据 ==========
print("加载 CSV（排名信号）...")
df = pd.read_csv(HISTORY_CSV, parse_dates=["fetched_at_utc"])
df = df.sort_values("fetched_at_utc").reset_index(drop=True)
print(f"CSV: {len(df)} 行, {df['symbol'].nunique()} 币")

# 从 DB 取实际入场信号（用真实成交的开仓时间+币种作为入场）
print("加载 dry-run 成交记录...")
conn = sqlite3.connect(TRADES_DB)
trades = pd.read_sql_query(
    "SELECT pair, open_date, open_rate, close_date, close_profit, close_rate, max_rate, min_rate "
    "FROM trades WHERE close_date IS NOT NULL", conn)
conn.close()
trades["open_date"] = pd.to_datetime(trades["open_date"], utc=True)
trades["close_date"] = pd.to_datetime(trades["close_date"], utc=True)
trades["symbol"] = trades["pair"].str.split("/").str[0] + "USDT"
print(f"真实平仓: {len(trades)} 笔")

# ========== 2. 获取 OKX 30m K 线（用于模拟持有期价格路径）==========
# 这里用 CSV 里的 price 快照 + 真实交易的 open/close 价做近似
# 更精确的做法：拉 OKX klines。为了探索性分析，先用已有数据。

def simulate_exit(row, exit_rank, atr_mult, trail_enabled=False,
                  trail_offset=0.03, trail_drop=0.02):
    """
    模拟单笔交易的退出。
    用真实成交的 max_rate/min_rate 近似价格路径（保守假设：峰值可达，但
    无法精确还原盘中路径，只比较"退出逻辑差异"的相对效果）。
    """
    sym = row["symbol"]
    open_dt = row["open_date"]
    close_dt = row["close_date"]

    # 该币在持仓期间的所有排名信号
    sym_mask = (df["symbol"] == sym) & (df["fetched_at_utc"] >= open_dt) & (df["fetched_at_utc"] <= close_dt + timedelta(hours=4))
    sym_df = df[sym_mask]

    if sym_df.empty:
        return None  # 无信号，跳过

    open_rate = row["open_rate"]
    # 模拟路径：从 open 到 max 到 close 的简化路径
    peak = row["max_rate"] if pd.notna(row["max_rate"]) else open_rate
    close_rate = row["close_rate"] if pd.notna(row["close_rate"]) else open_rate

    # 简化：假设价格线性到峰值再回落到 close
    # 检查排名退出触发（rank > exit_rank）
    rank_exit = False
    for _, r in sym_df.iterrows():
        rank = r["netflow_rank"]
        if pd.notna(rank) and rank > exit_rank:
            rank_exit = True
            break

    # 检查 trailing 止盈（涨过 offset 后回撤 drop）
    peak_profit = (peak / open_rate) - 1
    exit_profit = (close_rate / open_rate) - 1

    if trail_enabled and peak_profit >= trail_offset:
        # trailing 触发：回撤 trail_drop 止盈
        trail_exit = peak_profit - trail_drop
        return max(trail_exit, exit_profit * 0.5)  # 至少保留一半
    elif rank_exit:
        return exit_profit  # 排名退出
    else:
        return exit_profit  # 其他原因退出

# ========== 3. 对比三种策略 ==========
configs = [
    {"name": "基线 EXIT_RANK=10", "exit_rank": 10, "atr_mult": 2.0, "trail": False},
    {"name": "A: EXIT_RANK=15", "exit_rank": 15, "atr_mult": 2.0, "trail": False},
    {"name": "B: trailing 3%/2%", "exit_rank": 10, "atr_mult": 2.0, "trail": True, "offset": 0.03, "drop": 0.02},
    {"name": "C: EXIT_RANK=15 + trail 3%/2%", "exit_rank": 15, "atr_mult": 2.0, "trail": True, "offset": 0.03, "drop": 0.02},
]

print("\n=== 回放结果对比（5 天样本）===")
print(f"{'策略':28s} | {'模拟均值':8s} | {'真实均值':8s} | {'模拟合计':8s}")
for cfg in configs:
    results = []
    for _, t in trades.iterrows():
        sim = simulate_exit(t, cfg["exit_rank"], cfg["atr_mult"],
                            cfg.get("trail", False), cfg.get("offset", 0.03), cfg.get("drop", 0.02))
        if sim is not None:
            results.append(sim)
    if results:
        sim_mean = np.mean(results) * 100
        sim_sum = np.sum(results) * 100
        real_mean = trades["close_profit"].mean() * 100
        print(f"{cfg['name']:28s} | {sim_mean:+6.2f}% | {real_mean:+6.2f}% | {sim_sum:+7.2f}%")

print("\n说明：模拟用 max_rate/min_rate 近似价格路径，只能比较相对差异，")
print("不是精确回测。5 天样本不足以下统计结论，仅供参考方向。")
