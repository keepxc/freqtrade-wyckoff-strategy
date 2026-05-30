"""
验证 sustained_markdown 在 Trade #1 入场时是否激活
=====================================================
Trade #1: BTC 多头 @ $77,866，2026-05-21 17:00 UTC

sustained_markdown 定义（来自 V7）：
  1. daily_choch_confirmed = True（宏观结构已破，close < EMA200）
  2. 1H close < daily_ema200 * 0.97（已从顶部下跌 3%+）
  3. 上述状态持续 >= 30 根 1H K线（连续确认）

运行: python scripts/validate_sustained_markdown.py
"""

import numpy as np
import pandas as pd

DATA_1H = r"user_data\data\binance\futures\BTC_USDT_USDT-1h-futures.feather"
TRADE_ENTRY = pd.Timestamp("2026-05-21 17:00:00", tz="UTC")
LOOKBACK_DAYS = 30   # 分析入场前多少天

# ── 加载 1H 数据 ──────────────────────────────────────────────────
df = pd.read_feather(DATA_1H)
df["date"] = pd.to_datetime(df["date"], utc=True)
df = df.sort_values("date").reset_index(drop=True)

# 截取入场前后的窗口
start = TRADE_ENTRY - pd.Timedelta(days=LOOKBACK_DAYS)
end   = TRADE_ENTRY + pd.Timedelta(days=3)
df = df[(df["date"] >= start) & (df["date"] <= end)].copy().reset_index(drop=True)

print(f"分析窗口: {df['date'].min()} → {df['date'].max()}")
print(f"共 {len(df)} 根 1H K线\n")

# ── EMA200（日线级别）——用 1H 数据近似：200×24 = 4800 根 1H EMA ──
# 实际 V7 是从日线 merge 过来的，这里用长周期 EMA 近似
# 先加载完整 1H 数据计算 EMA
df_full = pd.read_feather(DATA_1H)
df_full["date"] = pd.to_datetime(df_full["date"], utc=True)
df_full = df_full.sort_values("date").reset_index(drop=True)
df_full["ema200_daily"] = df_full["close"].ewm(span=200*24, adjust=False).mean()

# 再截取窗口
df = df_full[(df_full["date"] >= start) & (df_full["date"] <= end)].copy().reset_index(drop=True)

# ── daily_choch_confirmed 近似 ────────────────────────────────────
# V7 定义：宏观结构已破 = close < daily EMA200
# 简化：close < ema200_daily
df["daily_choch"] = df["close"] < df["ema200_daily"]

# ── sustained_markdown 各条件 ─────────────────────────────────────
df["cond_choch"]    = df["daily_choch"]
df["cond_below_97"] = df["close"] < df["ema200_daily"] * 0.97
df["sm_raw"]        = df["cond_choch"] & df["cond_below_97"]

# 连续 30 根 1H 全满足
df["sustained_markdown"] = (
    df["sm_raw"].astype(int)
    .rolling(30, min_periods=30).min()
    .fillna(0).astype(bool)
)

# ── 查看入场时刻状态 ──────────────────────────────────────────────
entry_row = df[df["date"] == TRADE_ENTRY]
if entry_row.empty:
    # 找最近一根
    idx = (df["date"] - TRADE_ENTRY).abs().idxmin()
    entry_row = df.iloc[[idx]]
    print(f"⚠️  精确时间未找到，使用最近 bar: {entry_row['date'].values[0]}")

row = entry_row.iloc[0]
print("=" * 55)
print(f"Trade #1 入场时刻状态  ({row['date']})")
print("=" * 55)
print(f"  close              : {row['close']:,.0f}")
print(f"  EMA200_daily       : {row['ema200_daily']:,.0f}")
print(f"  EMA200 * 0.97      : {row['ema200_daily']*0.97:,.0f}")
print(f"  cond_choch         : {row['cond_choch']}   (close < EMA200)")
print(f"  cond_below_97      : {row['cond_below_97']}   (close < EMA200*0.97)")
print(f"  sm_raw             : {row['sm_raw']}   (两条件同时满足)")
print(f"  sustained_markdown : {row['sustained_markdown']}  (持续30根)")
print()

# ── 入场前 40 根 K 线的明细 ───────────────────────────────────────
entry_idx = df[df["date"] == row["date"]].index[0]
window = df.iloc[max(0, entry_idx-40) : entry_idx+1].copy()

print("入场前 40 根 1H K 线（最后一行 = 入场时刻）")
print(f"{'日期':>24} {'收盘':>8} {'EMA200':>8} {'EMA*97':>8} {'choch':>6} {'<97%':>6} {'sm_raw':>7} {'sm':>6}")
print("-" * 80)
for _, r in window.iterrows():
    marker = " ← 入场" if r["date"] == row["date"] else ""
    print(f"{str(r['date']):>24} {r['close']:>8,.0f} {r['ema200_daily']:>8,.0f} "
          f"{r['ema200_daily']*0.97:>8,.0f} {str(r['cond_choch']):>6} "
          f"{str(r['cond_below_97']):>6} {str(r['sm_raw']):>7} "
          f"{str(r['sustained_markdown']):>6}{marker}")

# ── 连续满足 sm_raw 的根数统计 ────────────────────────────────────
# 从入场往前数连续 sm_raw=True 的根数
consec = 0
for i in range(entry_idx, max(0, entry_idx-60), -1):
    if df.iloc[i]["sm_raw"]:
        consec += 1
    else:
        break

print(f"\n入场前连续满足 sm_raw 的根数: {consec} 根（需要 >= 30 才激活）")
print(f"sustained_markdown 最终结论: {'✅ 激活' if row['sustained_markdown'] else '❌ 未激活'}")

if not row["sustained_markdown"]:
    # 查最近一次激活时间
    sm_active = df[df["sustained_markdown"]]
    if not sm_active.empty:
        last_active = sm_active[sm_active["date"] <= row["date"]]["date"].max()
        print(f"最近一次激活时间: {last_active}")
    else:
        print("分析窗口内从未激活")
