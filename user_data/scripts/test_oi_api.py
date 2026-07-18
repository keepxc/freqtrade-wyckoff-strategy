"""测试 Binance OI API 的可用参数"""
import requests, json

base = 'https://fapi.binance.com'

# 测试1：看支持哪些 period
for period in ['5m', '15m', '30m', '1h', '2h', '4h', '6h', '12h', '1d']:
    try:
        r = requests.get(
            f'{base}/futures/data/openInterestHist',
            params={'symbol': 'BTCUSDT', 'period': period, 'limit': 2},
            timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            if data:
                print(f'period={period} ✅  最新: {data[-1]}')
            else:
                print(f'period={period} ✅  空数据')
        else:
            print(f'period={period} ❌  {r.status_code}: {r.text[:80]}')
    except Exception as e:
        print(f'period={period} ❌  {e}')
