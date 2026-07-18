"""检查现有数据文件中是否包含 OI 相关字段"""
import sys
sys.path.insert(0, '/freqtrade')
import pandas as pd
from pathlib import Path

data_dir = Path('/freqtrade/user_data/data/binance/futures')

for f in sorted(data_dir.glob('BTC*')):
    try:
        df = pd.read_feather(f)
        print(f'\n{f.name}')
        print(f'  行数: {len(df)}  列数: {len(df.columns)}')
        print(f'  列: {list(df.columns)}')
        # OI 相关
        oi_cols = [c for c in df.columns if 'open_interest' in c.lower() or c.lower() == 'oi']
        if oi_cols:
            print(f'  ★ OI 列: {oi_cols}')
            print(df[oi_cols].tail(3))
    except Exception as e:
        print(f'{f.name}: {e}')
