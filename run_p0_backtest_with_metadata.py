#!/usr/bin/env python3
"""
运行P0混合策略回测并导出metadata
通过monkey-patching Freqtrade的backtest来捕获axis_support的metadata
"""

import json
import sys
from pathlib import Path
from datetime import datetime

# 存储metadata的全局变量
axis_metadata_log = []

def capture_axis_metadata(original_confirm_trade_entry):
    """包装confirm_trade_entry方法来捕获metadata"""
    def wrapper(self, pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs):
        # 调用原始方法
        result = original_confirm_trade_entry(self, pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs)
        
        # 如果是axis_support交易，记录metadata
        if result and entry_tag == 'wyckoff_axis_support':
            dataframe = kwargs.get('dataframe')
            if dataframe is not None and len(dataframe) > 0:
                last_candle = dataframe.iloc[-1]
                axis_metadata = last_candle.get('axis_metadata')
                if axis_metadata is not None and isinstance(axis_metadata, dict):
                    metadata_record = {
                        'pair': pair,
                        'timestamp': str(current_time),
                        'rate': float(rate),
                        'tr_source': axis_metadata.get('tr_source', 'unknown'),
                        'tr_position': float(axis_metadata.get('tr_position', 0.5)),
                        'tr_low': float(axis_metadata.get('tr_low', 0.0)),
                        'tr_high': float(axis_metadata.get('tr_high', 0.0)),
                        'tr_height': float(axis_metadata.get('tr_height', 0.0)),
                        'tr_source_detail': axis_metadata.get('tr_source_detail', ''),
                    }
                    axis_metadata_log.append(metadata_record)
                    print(f"✓ 捕获metadata: {pair} @ {current_time}, TR来源={metadata_record['tr_source']}, TR位置={metadata_record['tr_position']:.2f}")
        
        return result
    
    return wrapper

def run_backtest_with_metadata_capture():
    """运行回测并捕获metadata"""
    print("="*80)
    print("P0混合策略回测 - Metadata捕获模式")
    print("="*80)
    
    # 导入freqtrade
    try:
        from freqtrade.optimize.backtesting import Backtesting
        from freqtrade.configuration import Configuration
    except ImportError as e:
        print(f"错误: 无法导入freqtrade模块: {e}")
        print("请确保在freqtrade环境中运行此脚本")
        sys.exit(1)
    
    # 加载配置
    config_path = 'user_data/config_backtest.json'
    if not Path(config_path).exists():
        print(f"错误: 配置文件不存在: {config_path}")
        sys.exit(1)
    
    print(f"\n加载配置: {config_path}")
    config = Configuration.from_files([config_path])
    
    # 设置回测参数
    config['timerange'] = '20220101-20260504'
    config['strategy'] = 'WyckoffV7_TrueMethod'
    config['cache'] = 'none'
    
    print(f"策略: {config['strategy']}")
    print(f"时间范围: {config['timerange']}")
    
    # 创建Backtesting实例
    print("\n初始化回测引擎...")
    backtesting = Backtesting(config)
    
    # Monkey-patch strategy的confirm_trade_entry方法
    if hasattr(backtesting.strategy, 'confirm_trade_entry'):
        print("✓ 找到confirm_trade_entry方法，开始包装...")
        original_method = backtesting.strategy.confirm_trade_entry
        backtesting.strategy.confirm_trade_entry = capture_axis_metadata(original_method).__get__(
            backtesting.strategy, type(backtesting.strategy)
        )
    else:
        print("⚠️  策略没有confirm_trade_entry方法")
    
    # 运行回测
    print("\n开始回测...")
    print("-"*80)
    
    try:
        backtesting.start()
    except Exception as e:
        print(f"\n回测执行出错: {e}")
        import traceback
        traceback.print_exc()
    
    # 保存metadata
    if axis_metadata_log:
        output_file = f'p0_axis_metadata_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(axis_metadata_log, f, indent=2, ensure_ascii=False)
        
        print("\n" + "="*80)
        print(f"✓ 捕获了 {len(axis_metadata_log)} 条axis_support metadata")
        print(f"✓ 已保存到: {output_file}")
        print("="*80)
        
        # 快速统计
        from collections import Counter
        tr_sources = Counter(m['tr_source'] for m in axis_metadata_log)
        print("\nTR Source分布:")
        for source, count in tr_sources.items():
            pct = count / len(axis_metadata_log) * 100
            print(f"  {source}: {count} ({pct:.1f}%)")
        
        # TR位置分段统计
        bins = [(0.0, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 1.0)]
        segment_counts = {f"{low}-{high}": 0 for low, high in bins}
        
        for m in axis_metadata_log:
            tr_pos = m['tr_position']
            for low, high in bins:
                if low <= tr_pos < high or (high == 1.0 and tr_pos == 1.0):
                    segment_counts[f"{low}-{high}"] += 1
                    break
        
        print("\nTR位置分段:")
        for segment, count in segment_counts.items():
            pct = count / len(axis_metadata_log) * 100
            print(f"  TR {segment}: {count} ({pct:.1f}%)")
    else:
        print("\n⚠️  未捕获到任何axis_support metadata")
        print("可能原因:")
        print("  1. 回测中没有axis_support交易")
        print("  2. metadata未正确设置在dataframe中")
        print("  3. confirm_trade_entry方法未被调用")

if __name__ == '__main__':
    run_backtest_with_metadata_capture()
