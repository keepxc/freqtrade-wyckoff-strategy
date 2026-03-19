"""
分析 axis_support × down_wave 亏损交易的浮盈历史

统计：到过 +1% / +2% / +3% 的笔数，及理论可挽回损失
"""
import json
import sys

RESULT_FILE = "user_data/backtest_results/extracted_latest/backtest-result-2026-03-17_16-34-15.json"

with open(RESULT_FILE, encoding="utf-8") as f:
    data = json.load(f)

# 取第一个策略的交易列表
strategy_key = list(data["strategy"].keys())[0]
trades = data["strategy"][strategy_key]["trades"]

print(f"总交易笔数: {len(trades)}")

# 筛选：axis_support × down_wave 亏损交易
target = [
    t for t in trades
    if t.get("enter_tag") == "wyckoff_axis_support"
    and t.get("exit_reason") == "wyckoff_down_wave"
    and t.get("profit_ratio", 0) < 0
]

print(f"\naxis_support × down_wave 亏损笔数: {len(target)}")
print()

# 分析字段
sample = target[0] if target else {}
print("可用字段示例:", [k for k in sample.keys() if "profit" in k.lower() or "max" in k.lower() or "min" in k.lower()])
print()

# 计算每笔的最大浮盈 max_profit_ratio
# Freqtrade 导出字段：max_rate = 持仓期间最高价, open_rate = 开仓价
thresholds = [0.01, 0.02, 0.03]
buckets = {t: [] for t in thresholds}

for trade in target:
    open_rate = trade.get("open_rate", 0)
    max_rate = trade.get("max_rate", open_rate)
    final_profit = trade.get("profit_ratio", 0)
    
    if open_rate <= 0:
        continue
    
    max_float_profit = (max_rate - open_rate) / open_rate
    
    for thresh in thresholds:
        if max_float_profit >= thresh:
            buckets[thresh].append({
                "max_float": max_float_profit,
                "final_profit": final_profit,
                "theoretical_save": final_profit * -1,  # 如保本，能挽回的亏损
                "pair": trade.get("pair"),
                "open_date": trade.get("open_date"),
            })

print("=" * 60)
print("浮盈分布统计（axis_support × down_wave 亏损笔，共 {} 笔）".format(len(target)))
print("=" * 60)

for thresh in thresholds:
    hits = buckets[thresh]
    n = len(hits)
    pct = n / len(target) * 100 if target else 0
    
    if hits:
        avg_max_float = sum(h["max_float"] for h in hits) / n
        avg_final = sum(h["final_profit"] for h in hits) / n
        total_saveable = sum(h["theoretical_save"] for h in hits)
        avg_saveable = total_saveable / n
    else:
        avg_max_float = avg_final = total_saveable = avg_saveable = 0
    
    print(f"\n曾到过 +{thresh*100:.0f}% 浮盈：")
    print(f"  笔数       : {n} / {len(target)}（{pct:.1f}%）")
    if hits:
        print(f"  平均最高浮盈: +{avg_max_float*100:.2f}%")
        print(f"  最终平均亏损: {avg_final*100:.2f}%")
        print(f"  理论可挽回  : 每笔平均 {avg_saveable*100:.2f}%，合计 {total_saveable*100:.2f}%")

# 补充：breakeven at +2% 的场景分析
print()
print("=" * 60)
print("场景模拟：在 +2% 时触发保本（移动止损至成本价）")
print("=" * 60)
hits_2 = buckets[0.02]
if hits_2:
    # 如果在 +2% 保本，最差成本价出场（profit=0），原本亏损单变成 0
    # 对比：原来这批单子的总亏损是多少
    original_loss = sum(h["final_profit"] for h in hits_2)
    rescued_profit = 0  # 保本出场 profit = 0
    net_improvement = rescued_profit - original_loss  # 相对于原来亏损的改善
    print(f"  这批 {len(hits_2)} 笔到过 +2% 的交易，原来总亏损: {original_loss*100:.2f}%")
    print(f"  若全部保本出场（profit=0），净改善: +{net_improvement*100:.2f}%")
    print(f"  但注意：保本出场会失去其中可能继续上涨到 roi 的笔数")
    
    # 看这批里有多少最大浮盈 > 4% (可能会走到 roi)
    might_be_roi = [h for h in hits_2 if h["max_float"] > 0.04]
    print(f"  其中最高浮盈 > 4% 的笔数: {len(might_be_roi)}（可能本可走到 roi）")
    print(f"  若这些 > 4% 的笔强制保本，损失潜在 roi 收益: 约 {len(might_be_roi)} × 7.92% = {len(might_be_roi)*7.92:.1f}%")
else:
    print("  没有到过 +2% 的亏损笔。")

# 最后看一下全量 axis_support × down_wave 的分布（含盈利和亏损）
all_dw = [
    t for t in trades
    if t.get("enter_tag") == "wyckoff_axis_support"
    and t.get("exit_reason") == "wyckoff_down_wave"
]
print()
print("=" * 60)
print(f"全量 axis_support × down_wave: {len(all_dw)} 笔")
profits = [t.get("profit_ratio", 0) for t in all_dw]
wins = [p for p in profits if p >= 0]
losses = [p for p in profits if p < 0]
print(f"  盈利: {len(wins)} 笔，平均 +{sum(wins)/len(wins)*100:.2f}%" if wins else "  盈利: 0")
print(f"  亏损: {len(losses)} 笔，平均 {sum(losses)/len(losses)*100:.2f}%" if losses else "  亏损: 0")
