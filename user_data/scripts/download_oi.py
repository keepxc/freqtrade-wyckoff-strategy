"""
从 Binance Futures API 增量下载 BTC/USDT 历史 OI 数据（1d 粒度）
endpoint: GET /futures/data/openInterestHist

增量逻辑：
  - 若本地已有 feather，只拉取 last_date+1 之后的新数据并追加
  - 若本地无文件，拉取 API 能返回的全部历史（约 30 天）
  - 每天运行一次即可持续积累历史

用法：
  docker compose run --rm --entrypoint python freqtrade //freqtrade/user_data/scripts/download_oi.py
"""
import time
import requests
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone

SYMBOL   = 'BTCUSDT'
PERIOD   = '1d'
BASE_URL = 'https://fapi.binance.com'
OUT_PATH = Path('/home/gyan/freqtrade/user_data/data/binance/futures/BTC_USDT_USDT-1d-open_interest.feather')
LIMIT    = 500


def fetch_chunk(start_ms: int = None, end_ms: int = None) -> list:
    params = {'symbol': SYMBOL, 'period': PERIOD, 'limit': LIMIT}
    if start_ms is not None:
        params['startTime'] = start_ms
    if end_ms is not None:
        params['endTime'] = end_ms
    r = requests.get(f'{BASE_URL}/futures/data/openInterestHist', params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def rows_to_df(rows: list) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df['date'] = pd.to_datetime(df['timestamp'].astype(int), unit='ms', utc=True).dt.tz_localize(None)
    df['open_interest']       = df['sumOpenInterest'].astype(float)
    df['open_interest_value'] = df['sumOpenInterestValue'].astype(float)
    return df[['date', 'open_interest', 'open_interest_value']]


def load_existing() -> pd.DataFrame | None:
    if OUT_PATH.exists():
        df = pd.read_feather(OUT_PATH)
        df['date'] = pd.to_datetime(df['date']).dt.tz_localize(None)
        return df
    return None


def main():
    existing = load_existing()

    if existing is not None:
        last_date = existing['date'].max()
        last_ts_ms = int(last_date.timestamp() * 1000) + 86_400_000  # 从次日开始
        print(f'本地已有 {len(existing)} 条，最新: {last_date.date()}，拉取新数据…')
        new_rows: list = []
        start_ms = last_ts_ms
        while True:
            try:
                chunk = fetch_chunk(start_ms=start_ms)
            except Exception as e:
                print(f'  请求失败: {e}')
                break
            if not chunk:
                break
            new_rows.extend(chunk)
            last_ts = chunk[-1]['timestamp']
            print(f'  获取 {len(chunk)} 条，最新: {datetime.fromtimestamp(last_ts/1000, tz=timezone.utc).date()}')
            if len(chunk) < LIMIT:
                break
            start_ms = last_ts + 86_400_000
            time.sleep(0.3)

        if not new_rows:
            print('无新数据，已是最新。')
            print(f'当前共 {len(existing)} 条，范围: {existing["date"].iloc[0].date()} ~ {existing["date"].iloc[-1].date()}')
            return

        df_new = rows_to_df(new_rows)
        df_all = pd.concat([existing, df_new], ignore_index=True)

    else:
        # 首次运行：往前翻页拉取全部可用历史
        print(f'本地无数据，首次下载 {SYMBOL} OI 历史（{PERIOD}）…')
        first = fetch_chunk()
        if not first:
            print('API 无返回数据')
            return

        all_rows = list(first)
        oldest_ts = first[0]['timestamp']
        print(f'  首批 {len(first)} 条，最早: {datetime.fromtimestamp(oldest_ts/1000, tz=timezone.utc).date()}')

        # 往前翻页
        while True:
            try:
                chunk = fetch_chunk(end_ms=oldest_ts - 1)
            except Exception as e:
                print(f'  请求失败: {e}')
                break
            if not chunk:
                break
            all_rows = chunk + all_rows
            oldest_ts = chunk[0]['timestamp']
            print(f'  往前翻页: 最早 {datetime.fromtimestamp(oldest_ts/1000, tz=timezone.utc).date()}，累计 {len(all_rows)} 条')
            if len(chunk) < LIMIT:
                break
            time.sleep(0.3)

        df_all = rows_to_df(all_rows)

    df_all = df_all.drop_duplicates('date').sort_values('date').reset_index(drop=True)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df_all.to_feather(OUT_PATH)

    print(f'\n✅ 保存完成: {OUT_PATH}')
    print(f'   共 {len(df_all)} 条，范围: {df_all["date"].iloc[0].date()} ~ {df_all["date"].iloc[-1].date()}')
    print(df_all.tail(5).to_string(index=False))


if __name__ == '__main__':
    main()
