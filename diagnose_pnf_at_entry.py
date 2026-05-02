"""
诊断脚本：检查入场时DataFrame中的P&F目标位值
"""
import sys
import os
import pandas as pd
from pathlib import Path

# 添加路径
sys.path.insert(0, str(Path(__file__).parent))

from freqtrade.configuration import Configuration
from freqtrade.resolvers import StrategyResolver
from freqtrade.data.dataprovider import DataProvider
from freqtrade.exchange import Exchange

def main():
    # 加载配置
    config = Configuration.from_files(['user_data/config_backtest.json'])
    config['timerange'] = '20260419-20260502'
    
    # 初始化策略
    strategy = StrategyResolver.load_strategy(config)
    
    # 初始化数据提供者
    exchange = Exchange(config)
    dataprovider = DataProvider(config, exchange)
    
    # 获取数据
    pair = 'BTC/USDT:USDT'
    timeframe = '1h'
    
    # 加载数据
    dataframe = dataprovider.get_pair_dataframe(pair, timeframe)
    
    # 运行指标计算
    dataframe = strategy.advise_indicators(dataframe.copy(), {'pair': pair})
    
    # 运行入场信号
    dataframe = strategy.advise_entry(dataframe.copy(), {'pair': pair})
    
    # 找到所有入场信号
    entry_signals = dataframe[dataframe['enter_long'] == 1].copy()
    
    print(f"\n找到 {len(entry_signals)} 个入场信号\n")
    
    for idx, row in entry_signals.iterrows():
        print(f"=" * 80)
        print(f"入场信号 @ {row['date']}")
        print(f"价格: {row['close']:.2f}")
        print(f"入场标签: {row.get('enter_tag', 'N/A')}")
        print(f"\nP&F 目标位字段:")
        print(f"  tr_low:              {row.get('tr_low', 0):.2f}")
        print(f"  tr_high:             {row.get('tr_high', 0):.2f}")
        print(f"  tr_height:           {row.get('tr_height', 0):.2f}")
        print(f"  target_conservative: {row.get('target_conservative', 0):.2f}")
        print(f"  target_standard:     {row.get('target_standard', 0):.2f}")
        print(f"  target_aggressive:   {row.get('target_aggressive', 0):.2f}")
        print(f"\nPhase Context 字段:")
        print(f"  phase_tr_low:        {row.get('phase_tr_low', 0):.2f}")
        print(f"  phase_tr_high:       {row.get('phase_tr_high', 0):.2f}")
        print(f"  phase_tr_valid:      {row.get('phase_tr_valid', False)}")
        print(f"\n气候行动字段:")
        print(f"  climatic_type:       {row.get('climatic_type', 'none')}")
        print()

if __name__ == '__main__':
    main()
