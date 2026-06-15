"""一次性：把本地 feather 的 OI 数据迁移进 market_data.db"""
import sys
sys.path.insert(0, '/freqtrade')

import pandas as pd
from pathlib import Path
from user_data.strategies.v8_modules.market_data_store import save_oi_records, get_oi_range

FEATHER = Path('/freqtrade/user_data/data/binance/futures/BTC_USDT_USDT-1d-open_interest.feather')

df = pd.read_feather(FEATHER)
df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')

records = df[['date', 'open_interest', 'open_interest_value']].to_dict('records')
n = save_oi_records(records)
print(f'迁移完成：{n} 条新增写入 market_data.db')

rows = get_oi_range(records[0]['date'], records[-1]['date'])
print(f'DB 现有 {len(rows)} 条，范围: {rows[0]["date"]} ~ {rows[-1]["date"]}')
