#!/usr/bin/env python3
"""
Core Audit 分析脚本
用于分析回测结果，支持 CA-1 / CA-2 / CA-3 / CA-4 审计
"""
import json
import sys
from pathlib import Path
from collections import defaultdict


def load_backtest_json(path: str) -> dict:
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def ca1_entry_analysis(s: dict):
    """CA-1: 真影响成交的链路分析"""
    print("\n" + "="*70)
    print("CA-1: 各入场标签统计（真影响成交的链路）")
    print("="*70)

    tags = s.get('results_per_enter_tag', [])
    if not tags:
        print("  无 enter_tag 数据")
        return

    # 排序：按总收益降序
    tags_sorted = sorted(tags, key=lambda x: x.get('profit_total', 0), reverse=True)

    total_trades = s.get('total_trades', 1) or 1
    fmt = "{:<38} {:>5} {:>6} {:>8} {:>10} {:>8}"
    print(fmt.format("入场标签", "笔数", "占比%", "胜率%", "均收益%", "总收益%"))
    print("-"*78)
    for t in tags_sorted:
        tag = t.get('enter_tag', '?')
        n = t.get('trades', 0)
        pct = n / total_trades * 100
        wins = t.get('wins', 0)
        wr = wins / n * 100 if n > 0 else 0
        pm = t.get('profit_mean', 0) * 100
        pt = t.get('profit_total', 0) * 100
        print(fmt.format(tag[:38], n, f"{pct:.1f}", f"{wr:.1f}", f"{pm:.2f}", f"{pt:.2f}"))

    print()
    # 汇总：前3条主导路径
    top3 = tags_sorted[:3]
    top3_trades = sum(t.get('trades', 0) for t in top3)
    print(f"前3条主导路径合计: {top3_trades} 笔 ({top3_trades/total_trades*100:.1f}%)")
    for i, t in enumerate(top3, 1):
        print(f"  #{i} {t.get('enter_tag')} — {t.get('trades')} 笔, 胜率{t.get('wins',0)/t.get('trades',1)*100:.1f}%, 均收益{t.get('profit_mean',0)*100:.2f}%")


def ca2_exit_analysis(s: dict):
    """CA-2: 真影响收益的退出链路分析"""
    print("\n" + "="*70)
    print("CA-2: 退出原因统计（真影响收益的退出链路）")
    print("="*70)

    exits = s.get('exit_reason_summary', [])
    if not exits:
        print("  无退出原因数据")
        return

    total_trades = s.get('total_trades', 1) or 1
    exits_sorted = sorted(exits, key=lambda x: x.get('profit_total_abs', 0), reverse=True)

    fmt = "{:<35} {:>5} {:>6} {:>8} {:>10} {:>9}"
    print(fmt.format("退出原因", "笔数", "占比%", "胜率%", "均收益%", "总绝对收益"))
    print("-"*76)
    for e in exits_sorted:
        reason = e.get('exit_reason', '?')
        n = e.get('trades', 0)
        pct = n / total_trades * 100
        wins = e.get('wins', 0)
        wr = wins / n * 100 if n > 0 else 0
        pm = e.get('profit_mean', 0) * 100
        abs_profit = e.get('profit_total_abs', 0)
        print(fmt.format(reason[:35], n, f"{pct:.1f}", f"{wr:.1f}", f"{pm:.2f}", f"{abs_profit:.2f}"))

    print()
    # 分析：亏损主要来自哪里
    losing_exits = [e for e in exits if e.get('profit_mean', 0) < 0]
    if losing_exits:
        losing_exits.sort(key=lambda x: x.get('profit_total_abs', 0))
        print("亏损退出路径（均收益为负）：")
        for e in losing_exits[:3]:
            print(f"  [{e.get('exit_reason')}] {e.get('trades')} 笔, 均收益 {e.get('profit_mean', 0)*100:.2f}%, 绝对亏损 {e.get('profit_total_abs', 0):.2f}")

    # ROI vs 止损 vs custom_exit 占比
    roi_exits = [e for e in exits if 'roi' in e.get('exit_reason', '').lower()]
    sl_exits = [e for e in exits if 'stop' in e.get('exit_reason', '').lower()]
    custom_exits = [e for e in exits if e.get('exit_reason', '') not in ['roi', 'stop_loss', 'trailing_stop_loss', 'force_sell', 'liquidation']]
    roi_count = sum(e.get('trades', 0) for e in roi_exits)
    sl_count = sum(e.get('trades', 0) for e in sl_exits)
    custom_count = sum(e.get('trades', 0) for e in custom_exits)
    print(f"\n退出类型汇总:")
    print(f"  ROI 触发: {roi_count} 笔 ({roi_count/total_trades*100:.1f}%)")
    print(f"  止损触发: {sl_count} 笔 ({sl_count/total_trades*100:.1f}%)")
    print(f"  custom_exit: {custom_count} 笔 ({custom_count/total_trades*100:.1f}%)")


def ca3_module_waste(s: dict):
    """CA-3: 找出浪费开发时间的模块"""
    print("\n" + "="*70)
    print("CA-3: 信号分布（识别低ROI方向）")
    print("="*70)

    tags = s.get('results_per_enter_tag', [])
    total_trades = s.get('total_trades', 1) or 1
    total_profit = s.get('profit_total', 0)

    # 找出贡献很少或负贡献的信号
    low_roi = [t for t in tags if t.get('profit_total', 0) < 0 or t.get('profit_mean', 0) < 0.005]
    high_roi = [t for t in tags if t.get('profit_mean', 0) >= 0.02]

    print("高ROI信号（均收益>=2%）：")
    for t in sorted(high_roi, key=lambda x: x.get('profit_mean', 0), reverse=True):
        print(f"  [{t.get('enter_tag')}] {t.get('trades')} 笔, 均收益 {t.get('profit_mean',0)*100:.2f}%, 胜率 {t.get('wins',0)/t.get('trades',1)*100:.1f}%")

    print("\n低ROI信号（均收益<0.5%或负收益）：")
    for t in sorted(low_roi, key=lambda x: x.get('profit_total', 0)):
        print(f"  [{t.get('enter_tag')}] {t.get('trades')} 笔, 均收益 {t.get('profit_mean',0)*100:.2f}%, 总贡献 {t.get('profit_total',0)*100:.2f}%")


def ca4_next_priority(s: dict):
    """CA-4: 确定下一步最值得做的开发项"""
    print("\n" + "="*70)
    print("CA-4: 综合判断 - 下一步开发优先级")
    print("="*70)

    tags = s.get('results_per_enter_tag', [])
    exits = s.get('exit_reason_summary', [])
    total_trades = s.get('total_trades', 1) or 1

    # 计算各退出方式的收益贡献
    sl_exits = [e for e in exits if 'stop' in e.get('exit_reason', '').lower()]
    roi_exits = [e for e in exits if 'roi' in e.get('exit_reason', '').lower()]
    custom_exits = [e for e in exits if e.get('exit_reason', '') not in ['roi', 'stop_loss', 'trailing_stop_loss', 'force_sell', 'liquidation']]

    sl_abs = sum(e.get('profit_total_abs', 0) for e in sl_exits)
    roi_abs = sum(e.get('profit_total_abs', 0) for e in roi_exits)
    custom_abs = sum(e.get('profit_total_abs', 0) for e in custom_exits)

    print(f"各退出方式绝对收益贡献:")
    print(f"  ROI: {roi_abs:.2f}")
    print(f"  止损: {sl_abs:.2f}")
    print(f"  custom_exit: {custom_abs:.2f}")

    # 最高潜力入场信号
    best_tags = sorted(tags, key=lambda x: x.get('profit_mean', 0), reverse=True)[:3]
    print(f"\n最高均收益入场信号 (Top3):")
    for t in best_tags:
        print(f"  [{t.get('enter_tag')}] 均收益 {t.get('profit_mean',0)*100:.2f}%, {t.get('trades')} 笔")

    # 最多交易量信号
    most_traded = sorted(tags, key=lambda x: x.get('trades', 0), reverse=True)[:3]
    print(f"\n交易量最大信号 (Top3):")
    for t in most_traded:
        print(f"  [{t.get('enter_tag')}] {t.get('trades')} 笔, 均收益 {t.get('profit_mean',0)*100:.2f}%")


def main():
    # 默认路径
    json_path = "user_data/backtest_results/temp_extract_ca/backtest-result-2026-03-16_03-08-50.json"
    if len(sys.argv) > 1:
        json_path = sys.argv[1]

    print(f"加载回测文件: {json_path}")
    data = load_backtest_json(json_path)

    strat_key = list(data.get('strategy', {}).keys())[0]
    s = data['strategy'][strat_key]

    print(f"\n策略: {strat_key}")
    print(f"总交易数: {s.get('total_trades', 0)}")
    print(f"多头: {s.get('trade_count_long', 0)} | 空头: {s.get('trade_count_short', 0)}")
    print(f"总收益率: {s.get('profit_total', 0)*100:.2f}%")
    print(f"平均收益: {s.get('profit_mean', 0)*100:.2f}%")
    print(f"最大回撤: {s.get('max_drawdown', s.get('max_drawdown_abs', 0)):.4f}")

    dur = s.get('holding_avg', s.get('holding_avg_s', 0))
    if dur:
        print(f"平均持仓: {dur}")

    ca1_entry_analysis(s)
    ca2_exit_analysis(s)
    ca3_module_waste(s)
    ca4_next_priority(s)


if __name__ == '__main__':
    main()
