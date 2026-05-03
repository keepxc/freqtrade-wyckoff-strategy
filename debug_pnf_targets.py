#!/usr/bin/env python3
"""调试 PNF 目标值"""

import sys
sys.path.append('.')

from user_data.strategies.WyckoffV7_TrueMethod import WyckoffV7_TrueMethod
import pandas as pd
from freqtrade.data.dataprovider import DataProvider
from freqtrade.configuration import Configuration

# 初始化策略
config = {
    'stake_currency': 'USDT',
    'dry_run': True,
}

strategy = WyckoffV7_TrueMethod(config)

# 加载数据
from freqtrade.data.history import load_pair_history
from freqtrade.enums import CandleType

pair = 'BTC/USDT:USDT'
timeframe = '1h'
timerange = '20220101-20220201'  # 只加载一个月的数据进行测试

try:
    dataframe = load_pair_history(
        datadir='user_data/data/binance',
        timeframe=timeframe,
        pair=pair,
        candle_type=CandleType.FUTURES,
        timerange=timerange
    )
    
    print(f"加载数据: {len(dataframe)} 根K线")
    
    # 调用 populate_indicators
    dataframe = strategy.populate_indicators(dataframe.copy(), {'pair': pair})
    
    # 检查 PNF 目标列
    pnf_columns = [col for col in dataframe.columns if 'target' in col.lower()]
    print(f"\nPNF 相关列: {pnf_columns}")
    
    if 'target_conservative' in dataframe.columns:
        target_cons = dataframe['target_conservative']
        print(f"\ntarget_conservative 统计:")
        print(f"  非零值数量: {(target_cons > 0).sum()} / {len(target_cons)}")
        print(f"  最小值: {target_cons.min():.2f}")
        print(f"  最大值: {target_cons.max():.2f}")
        print(f"  平均值: {target_cons.mean():.2f}")
        
        if (target_cons > 0).any():
            print(f"\n前5个非零目标值:")
            non_zero = dataframe[target_cons > 0][['date', 'close', 'target_conservative']].head()
            print(non_zero)
        else:
            print("\n⚠️  所有 target_conservative 都是 0！")
    else:
        print("\n❌ target_conservative 列不存在！")
        
except Exception as e:
    print(f"错误: {e}")
    import traceback
    traceback.print_exc()
