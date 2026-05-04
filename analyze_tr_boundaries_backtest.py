#!/usr/bin/env python3
"""
分析回测中的 TR 边界分布
验证 P1 诊断日志是否正确工作
"""

import re
import sys
from collections import Counter, defaultdict

def analyze_tr_boundaries(log_file):
    """分析回测日志中的 TR 边界信息"""
    
    # 存储所有 TR 边界数据
    tr_data = []
    
    # 统计 TR 来源分布
    tr_sources = Counter()
    
    # 按来源统计 TR Height
    tr_heights_by_source = defaultdict(list)
    
    # 读取日志
    with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            # 查找 P&F 目标位锁定日志
            if '✅ P&F 目标位已锁定' in line or 'P&F 目标位已锁定' in line:
                # 提取 TR 边界信息
                # 格式: 🔍 TR边界: low=77000, high=79000, height=2000, source=rolling_window, detail=Window120
                
                tr_low_match = re.search(r'low=(\d+)', line)
                tr_high_match = re.search(r'high=(\d+)', line)
                tr_height_match = re.search(r'height=(\d+)', line)
                source_match = re.search(r'source=(\w+)', line)
                detail_match = re.search(r'detail=([^,\s]+)', line)
                
                if all([tr_low_match, tr_high_match, tr_height_match, source_match]):
                    tr_low = int(tr_low_match.group(1))
                    tr_high = int(tr_high_match.group(1))
                    tr_height = int(tr_height_match.group(1))
                    source = source_match.group(1)
                    detail = detail_match.group(1) if detail_match else 'N/A'
                    
                    tr_data.append({
                        'low': tr_low,
                        'high': tr_high,
                        'height': tr_height,
                        'source': source,
                        'detail': detail
                    })
                    
                    tr_sources[source] += 1
                    tr_heights_by_source[source].append(tr_height)
    
    # 输出分析结果
    print("=" * 60)
    print("TR 边界分析报告（回测验证 P1 诊断日志）")
    print("=" * 60)
    print()
    
    if not tr_data:
        print("❌ 未找到 TR 边界数据")
        print("可能原因：")
        print("  1. 回测日志中没有入场交易")
        print("  2. P1 修复未生效（检查代码是否正确部署）")
        print("  3. 日志格式不匹配")
        return
    
    print(f"📊 总样本数: {len(tr_data)} 笔交易")
    print()
    
    # TR 来源分布
    print("🔍 TR 来源分布:")
    print("-" * 60)
    for source, count in tr_sources.most_common():
        percentage = count / len(tr_data) * 100
        print(f"  {source:20s}: {count:3d} 笔 ({percentage:5.1f}%)")
    print()
    
    # 按来源统计 TR Height
    print("📏 TR Height 统计（按来源）:")
    print("-" * 60)
    for source in sorted(tr_heights_by_source.keys()):
        heights = tr_heights_by_source[source]
        avg_height = sum(heights) / len(heights)
        min_height = min(heights)
        max_height = max(heights)
        
        print(f"  {source:20s}:")
        print(f"    平均: {avg_height:8.0f}")
        print(f"    最小: {min_height:8.0f}")
        print(f"    最大: {max_height:8.0f}")
        print()
    
    # 整体 TR Height 分布
    all_heights = [d['height'] for d in tr_data]
    avg_height = sum(all_heights) / len(all_heights)
    min_height = min(all_heights)
    max_height = max(all_heights)
    
    print("📊 整体 TR Height 分布:")
    print("-" * 60)
    print(f"  平均: {avg_height:8.0f}")
    print(f"  最小: {min_height:8.0f}")
    print(f"  最大: {max_height:8.0f}")
    print()
    
    # TR Height 分段统计
    height_ranges = {
        '< 1000': 0,
        '1000-2000': 0,
        '2000-3000': 0,
        '3000-5000': 0,
        '5000-10000': 0,
        '> 10000': 0
    }
    
    for height in all_heights:
        if height < 1000:
            height_ranges['< 1000'] += 1
        elif height < 2000:
            height_ranges['1000-2000'] += 1
        elif height < 3000:
            height_ranges['2000-3000'] += 1
        elif height < 5000:
            height_ranges['3000-5000'] += 1
        elif height < 10000:
            height_ranges['5000-10000'] += 1
        else:
            height_ranges['> 10000'] += 1
    
    print("📊 TR Height 分段分布:")
    print("-" * 60)
    for range_name, count in height_ranges.items():
        percentage = count / len(all_heights) * 100 if all_heights else 0
        bar = '█' * int(percentage / 2)
        print(f"  {range_name:15s}: {count:3d} 笔 ({percentage:5.1f}%) {bar}")
    print()
    
    # 诊断建议
    print("=" * 60)
    print("🎯 诊断建议")
    print("=" * 60)
    print()
    
    # 检查是否主要使用 rolling_window
    rolling_window_pct = tr_sources.get('rolling_window', 0) / len(tr_data) * 100
    if rolling_window_pct > 70:
        print("⚠️  发现：70%+ 的交易使用 rolling_window 作为 TR 来源")
        print("   说明：SC 锚点和 Phase Context 检测不够充分")
        print("   建议：这是正常的，说明大部分交易没有明显的 SC 事件")
        print()
    
    # 检查 TR Height 是否过小
    small_height_pct = (height_ranges['< 1000'] + height_ranges['1000-2000']) / len(all_heights) * 100
    if small_height_pct > 50:
        print("⚠️  发现：50%+ 的交易 TR Height < 2000")
        print("   说明：TR 检测窗口可能过小，未能捕捉完整的积累结构")
        print("   建议：考虑实施 P2 优化（扩大窗口 120 → 240）")
        print()
    
    # 检查是否有 SC_anchor
    if tr_sources.get('SC_anchor', 0) > 0:
        print("✅ 发现：有 SC_anchor 来源的交易")
        print("   说明：SC 锚点检测正常工作")
        sc_heights = tr_heights_by_source.get('SC_anchor', [])
        if sc_heights:
            avg_sc_height = sum(sc_heights) / len(sc_heights)
            print(f"   SC 锚点平均 TR Height: {avg_sc_height:.0f}")
        print()
    
    # 检查是否有 phase_context
    if tr_sources.get('phase_context', 0) > 0:
        print("✅ 发现：有 phase_context 来源的交易")
        print("   说明：Phase Context 检测正常工作")
        phase_heights = tr_heights_by_source.get('phase_context', [])
        if phase_heights:
            avg_phase_height = sum(phase_heights) / len(phase_heights)
            print(f"   Phase Context 平均 TR Height: {avg_phase_height:.0f}")
        print()
    
    # 检查是否有 swing_fallback
    if tr_sources.get('swing_fallback', 0) > 0:
        print("ℹ️  发现：有 swing_fallback 来源的交易")
        print("   说明：滚动窗口 TR 过小，触发了 swing fallback")
        swing_heights = tr_heights_by_source.get('swing_fallback', [])
        if swing_heights:
            avg_swing_height = sum(swing_heights) / len(swing_heights)
            print(f"   Swing Fallback 平均 TR Height: {avg_swing_height:.0f}")
        print()
    
    print("=" * 60)
    print("✅ P1 验证完成")
    print("=" * 60)
    print()
    print("下一步:")
    print("  1. 如果 TR Height 普遍过小，考虑实施 P2（扩大窗口）")
    print("  2. 在 dry-run 环境验证 P0 修复（元数据链锁定）")
    print("  3. 观察 2-3 笔真实交易的完整日志")
    print()

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("用法: python analyze_tr_boundaries_backtest.py <backtest_log_file>")
        print("示例: python analyze_tr_boundaries_backtest.py backtest_output.log")
        sys.exit(1)
    
    log_file = sys.argv[1]
    analyze_tr_boundaries(log_file)
