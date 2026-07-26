"""
批量下载 Binance 官方历史归档站的 BTCUSDT OI 数据（5分钟粒度，2020-06起）
数据源：https://data.binance.vision/data/futures/um/daily/metrics/BTCUSDT/
用途：TASK 57（quant_system_tasks.md）OI 四象限分析的数据基建——用 OI×价格方向替代
     已被 TASK 29 Sell Test 2 证伪的原始成交量失衡比（原始成交量无区分力，OI 能分辨开仓/平仓）

与 download_oi.py 的区别：
  download_oi.py       用实时接口 /futures/data/openInterestHist，只保留约30天，做每日增量
  download_oi_history.py（本脚本）用官方历史归档站，一次性拉取 2020-06 至今的完整历史，仅需跑一次

支持断点续传：已下载的日度 CSV 缓存在 oi_raw_cache/，重跑只补拉缺失/失败的日期。

用法（host 或 docker 均可，脚本按自身路径定位项目根目录）：
  python user_data/scripts/download_oi_history.py [SYMBOL]   # 默认 BTCUSDT，可传 ETHUSDT
  docker compose run --rm --entrypoint python freqtrade //freqtrade/user_data/scripts/download_oi_history.py
"""
import io
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

SYMBOL = sys.argv[1].upper() if len(sys.argv) > 1 else 'BTCUSDT'
ARCHIVE_URL = 'https://data.binance.vision/data/futures/um/daily/metrics/{symbol}/{symbol}-metrics-{date}.zip'
# 2020-06-01 已实测 404，2020-09-01 已实测 200；起点留冗余，脚本自动跳过 404 日期
START_DATE = date(2020, 6, 1)
RETRIES = 3
TIMEOUT = 20
MAX_WORKERS = 8

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / 'user_data' / 'data' / 'binance' / 'futures' / 'oi_raw_cache'
_BASE_CCY = SYMBOL.replace('USDT', '')
OUT_PATH = (ROOT / 'user_data' / 'data' / 'binance' / 'futures'
            / f'{_BASE_CCY}_USDT_USDT-1h-open_interest_full.feather')


def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def fetch_one(d: date) -> str:
    """下载单日 CSV 到缓存目录。返回 'ok' / 'nodata' / 'fail'。"""
    csv_path = CACHE_DIR / f'{SYMBOL}-metrics-{d.isoformat()}.csv'
    marker_404 = CACHE_DIR / f'{SYMBOL}-metrics-{d.isoformat()}.nodata'
    if csv_path.exists():
        return 'ok'
    if marker_404.exists():
        return 'nodata'

    url = ARCHIVE_URL.format(symbol=SYMBOL, date=d.isoformat())
    for attempt in range(RETRIES):
        try:
            r = requests.get(url, timeout=TIMEOUT)
        except requests.RequestException:
            if attempt == RETRIES - 1:
                return 'fail'
            time.sleep(1.5 * (attempt + 1))
            continue

        if r.status_code == 404:
            marker_404.touch()
            return 'nodata'
        if r.status_code != 200:
            if attempt == RETRIES - 1:
                return 'fail'
            time.sleep(1.5 * (attempt + 1))
            continue

        try:
            with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
                inner_name = zf.namelist()[0]
                csv_path.write_bytes(zf.read(inner_name))
            return 'ok'
        except zipfile.BadZipFile:
            if attempt == RETRIES - 1:
                return 'fail'
            time.sleep(1.5 * (attempt + 1))
            continue
    return 'fail'


def download_all(end_date: date):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    days = list(daterange(START_DATE, end_date))
    print(f'目标区间: {START_DATE} ~ {end_date}（{len(days)} 天），缓存目录: {CACHE_DIR}')

    counts = {'ok': 0, 'nodata': 0, 'fail': 0}
    failed_days = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(fetch_one, d): d for d in days}
        for i, fut in enumerate(as_completed(futures)):
            d = futures[fut]
            result = fut.result()
            counts[result] += 1
            if result == 'fail':
                failed_days.append(d)
            if (i + 1) % 200 == 0:
                print(f'  进度 {i+1}/{len(days)}: ok={counts["ok"]} nodata={counts["nodata"]} fail={counts["fail"]}')

    print(f'下载完成: ok={counts["ok"]} nodata={counts["nodata"]} fail={counts["fail"]}')
    if failed_days:
        print(f'⚠️ {len(failed_days)} 天下载失败（示例: {failed_days[:5]}），重跑本脚本会自动重试这些日期')


def build_hourly_feather():
    csv_files = sorted(CACHE_DIR.glob(f'{SYMBOL}-metrics-*.csv'))
    if not csv_files:
        print('缓存目录无 CSV 文件，先运行下载。')
        return

    print(f'聚合 {len(csv_files)} 个日度 CSV → 1H feather...')
    frames = []
    for f in csv_files:
        try:
            df = pd.read_csv(f)
            if df.empty:
                continue
            frames.append(df)
        except Exception as e:
            print(f'  跳过损坏文件 {f.name}: {e}')

    raw = pd.concat(frames, ignore_index=True)
    raw['create_time'] = pd.to_datetime(raw['create_time'])
    raw = raw.sort_values('create_time').drop_duplicates('create_time')

    raw['hour'] = raw['create_time'].dt.floor('h')
    numeric_cols = [c for c in raw.columns if c not in ('create_time', 'symbol', 'hour')]
    hourly = raw.groupby('hour')[numeric_cols].last().reset_index()
    hourly = hourly.rename(columns={
        'hour': 'date',
        'sum_open_interest': 'open_interest',
        'sum_open_interest_value': 'open_interest_value',
        'count_toptrader_long_short_ratio': 'toptrader_ls_count_ratio',
        'sum_toptrader_long_short_ratio': 'toptrader_ls_position_ratio',
        'count_long_short_ratio': 'all_account_ls_ratio',
        'sum_taker_long_short_vol_ratio': 'taker_ls_vol_ratio',
    })
    keep_cols = ['date', 'open_interest', 'open_interest_value',
                 'toptrader_ls_count_ratio', 'toptrader_ls_position_ratio',
                 'all_account_ls_ratio', 'taker_ls_vol_ratio']
    hourly = hourly[[c for c in keep_cols if c in hourly.columns]]

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    hourly.to_feather(OUT_PATH)
    print(f'✅ 保存完成: {OUT_PATH}')
    print(f'   共 {len(hourly)} 行，范围: {hourly["date"].iloc[0]} ~ {hourly["date"].iloc[-1]}')
    print(hourly.tail(5).to_string(index=False))


if __name__ == '__main__':
    end = date.today() - timedelta(days=1)  # 今天的日度归档可能尚未生成
    download_all(end)
    build_hourly_feather()
