"""
A-2: down_wave 误杀分析
=======================
目标：统计 wyckoff_down_wave 出场交易的利润分布，找出低利润（过早退出）的特征。

用法：
    python scripts/_a2_downwave_analysis.py
"""
import json, pathlib, statistics

BACKTEST_JSON = pathlib.Path(
    "temp_extract_ca/backtest-result-2026-03-16_03-08-50.json"
)

def main():
    data = json.loads(BACKTEST_JSON.read_text(encoding="utf-8"))
    strategy_key = next(iter(data.get("strategy", data)))
    trades = data["strategy"][strategy_key]["trades"]

    dw = [t for t in trades if t.get("exit_reason") == "wyckoff_down_wave"]
    roi = [t for t in trades if t.get("exit_reason") == "roi"]
    all_trades = trades

    print(f"── 基础统计 ──────────────────────────────")
    print(f"  全部交易:        {len(all_trades)}")
    print(f"  down_wave 出场:  {len(dw)}")
    print(f"  roi 出场:        {len(roi)}")

    # 利润分桶
    def profit_buckets(trades_list, label):
        profits = [t.get("profit_ratio", 0) for t in trades_list]
        if not profits:
            return
        buckets = {
            "≤0%":          0,
            "0–0.5%":       0,
            "0.5–1%":       0,
            "1–2%":         0,
            "2–5%":         0,
            ">5%":          0,
        }
        for p in profits:
            pp = p * 100
            if pp <= 0:      buckets["≤0%"]      += 1
            elif pp < 0.5:   buckets["0–0.5%"]   += 1
            elif pp < 1.0:   buckets["0.5–1%"]   += 1
            elif pp < 2.0:   buckets["1–2%"]     += 1
            elif pp < 5.0:   buckets["2–5%"]     += 1
            else:            buckets[">5%"]       += 1
        mean_p = statistics.mean(profits) * 100
        med_p  = statistics.median(profits) * 100
        total_abs = sum(t.get("profit_abs", 0) for t in trades_list)
        win = sum(1 for p in profits if p > 0)
        print(f"\n── {label} (n={len(profits)}) ─────────────────")
        print(f"  均值: {mean_p:+.3f}%  中位数: {med_p:+.3f}%  胜率: {win/len(profits)*100:.1f}%")
        print(f"  累计 profit_abs: {total_abs:+,.0f}")
        print("  利润分布:")
        for k, v in buckets.items():
            bar = "█" * v
            print(f"    {k:12s}: {v:4d}  {bar}")

    profit_buckets(dw,  "wyckoff_down_wave 出场")
    profit_buckets(roi, "roi 出场")

    # down_wave 低利润交易：0-0.5%（疑似正常回踩被误判）
    low_dw = [t for t in dw if 0 < t.get("profit_ratio", 0) * 100 < 0.5]
    print(f"\n── 低利润 down_wave (0–0.5%, 疑似误判) n={len(low_dw)} ──")
    print(f"  占 down_wave 比例: {len(low_dw)/len(dw)*100:.1f}%")
    print(f"  持仓时长分布:")
    durations = [t.get("trade_duration", 0) for t in low_dw]
    if durations:
        print(f"    中位数 {statistics.median(durations):.0f} min  均值 {statistics.mean(durations):.0f} min")
        short_hold = sum(1 for d in durations if d <= 60)
        print(f"    ≤60min 持仓 (1根K): {short_hold} 笔 ({short_hold/len(durations)*100:.1f}%)")

    # 入场来源 × 出场路径
    print("\n── 入场来源 × 出场路径（down_wave 出场）──")
    from collections import Counter
    entry_cnt = Counter(t.get("enter_tag", "?") for t in dw)
    for tag, cnt in entry_cnt.most_common():
        sub = [t for t in dw if t.get("enter_tag") == tag]
        mean_p = statistics.mean(t.get("profit_ratio", 0) for t in sub) * 100
        print(f"  {tag:40s}: {cnt:4d} 笔  均利润 {mean_p:+.3f}%")

    # 结论
    print("\n── 结论 & A-3 建议 ──────────────────────────")
    low_ratio = len(low_dw) / len(dw) * 100
    print(f"  低利润退出占比: {low_ratio:.1f}%")
    if low_ratio > 20:
        print("  → 低利润退出较多（>20%），建议 A-3 提高 wave_efficiency 或加 wave_displacement 约束")
    else:
        print("  → 低利润退出占比可接受（≤20%），A-3 可保守实施")


if __name__ == "__main__":
    main()
