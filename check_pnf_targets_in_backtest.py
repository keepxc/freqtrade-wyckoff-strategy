#!/usr/bin/env python3
"""
检查回测结果中的 PNF 目标值
"""
import json
import sys

def analyze_pnf_targets(result_file):
    """分析回测结果中的 PNF 目标"""
    with open(result_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # 尝试不同的数据结构
    if 'strategy_comparison' in data and len(data['strategy_comparison']) > 0:
        comparison = data['strategy_comparison'][0]
        if isinstance(comparison.get('trades'), list):
            trades = comparison['trades']
        else:
            # trades 可能是一个数字（交易数量）
            print(f"\n[INFO] 分析文件: {result_file}")
            print(f"[WARN] 无法获取交易列表，只有交易数量: {comparison.get('trades')}")
            return
    else:
        print(f"\n[INFO] 分析文件: {result_file}")
        print(f"[WARN] 无法找到 strategy_comparison 数据")
        return
    
    print(f"\n[INFO] 分析文件: {result_file}")
    print(f"总交易数: {len(trades)}")
    
    # 统计有 PNF 目标的交易
    trades_with_pnf = []
    for trade in trades:
        enter_tag = trade.get('enter_tag', '')
        
        # 检查是否是符合条件的 entry_tag
        if enter_tag in ['wyckoff_axis_support', 'wyckoff_sos_entry']:
            trades_with_pnf.append(trade)
    
    print(f"\n符合条件的 entry_tag 交易数: {len(trades_with_pnf)}")
    print(f"  - wyckoff_axis_support: {sum(1 for t in trades if t.get('enter_tag') == 'wyckoff_axis_support')}")
    print(f"  - wyckoff_sos_entry: {sum(1 for t in trades if t.get('enter_tag') == 'wyckoff_sos_entry')}")
    
    # 统计退出原因
    exit_reasons = {}
    for trade in trades:
        reason = trade.get('exit_reason', 'unknown')
        exit_reasons[reason] = exit_reasons.get(reason, 0) + 1
    
    print(f"\n退出原因统计:")
    for reason, count in sorted(exit_reasons.items(), key=lambda x: x[1], reverse=True)[:10]:
        print(f"  - {reason}: {count}")
    
    # 检查是否有达到高利润的交易（可能达到了 PNF 目标）
    high_profit_trades = [t for t in trades_with_pnf if t.get('profit_ratio', 0) > 0.04]
    print(f"\n利润 > 4% 的符合条件交易: {len(high_profit_trades)}")
    
    if high_profit_trades:
        print("\n前5个高利润交易:")
        for i, trade in enumerate(high_profit_trades[:5], 1):
            print(f"  {i}. entry_tag={trade.get('enter_tag')}, "
                  f"profit={trade.get('profit_ratio', 0)*100:.2f}%, "
                  f"exit_reason={trade.get('exit_reason')}")

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python check_pnf_targets_in_backtest.py <backtest_result.json>")
        sys.exit(1)
    
    for result_file in sys.argv[1:]:
        analyze_pnf_targets(result_file)
