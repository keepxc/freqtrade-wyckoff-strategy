"""
Market reference data store
===========================
统一管理市场参考数据（OI、funding rate 等），存入独立 SQLite。

DB 路径：user_data/data/market_data.db
表：
  oi_daily — BTC/USDT 日线 OI（open interest）

设计原则：
  - bot_loop_start() 每天调一次 fetch_and_save_oi()，数据自动积累
  - 回测时不调用（bot_loop_start 在回测中不执行）
  - 所有方法幂等：重复插入同一天数据无副作用
"""

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

# user_data/strategies/v8_modules/ → parents[2] = user_data/
_DB_PATH = Path(__file__).parents[2] / 'data' / 'market_data.db'

BINANCE_OI_URL = 'https://fapi.binance.com/futures/data/openInterestHist'
OI_SYMBOL      = 'BTCUSDT'
OI_PERIOD      = '1d'


# ── DB 初始化 ─────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS oi_daily (
    date                 TEXT PRIMARY KEY,  -- YYYY-MM-DD（UTC 日期）
    open_interest        REAL,              -- 持仓量，BTC 单位
    open_interest_value  REAL               -- 持仓价值，USDT 单位
);
"""


def _connect(db_path: Path = _DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


# ── OI：从 Binance API 拉取并写入 DB ─────────────────────────────────────────

def fetch_latest_oi(limit: int = 2) -> list[dict]:
    """从 Binance 拉取最新 OI（默认取最近 2 条，确保当天数据）"""
    r = requests.get(
        BINANCE_OI_URL,
        params={'symbol': OI_SYMBOL, 'period': OI_PERIOD, 'limit': limit},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def _rows_to_records(rows: list[dict]) -> list[dict]:
    records = []
    for row in rows:
        ts_ms = int(row['timestamp'])
        date_str = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime('%Y-%m-%d')
        records.append({
            'date':                date_str,
            'open_interest':       float(row['sumOpenInterest']),
            'open_interest_value': float(row['sumOpenInterestValue']),
        })
    return records


def save_oi_records(records: list[dict], db_path: Path = _DB_PATH) -> int:
    """写入 OI 记录，已存在则跳过（INSERT OR IGNORE）。返回实际新增条数。"""
    conn = _connect(db_path)
    inserted = 0
    with conn:
        for rec in records:
            cur = conn.execute(
                "INSERT OR IGNORE INTO oi_daily (date, open_interest, open_interest_value) "
                "VALUES (:date, :open_interest, :open_interest_value)",
                rec,
            )
            inserted += cur.rowcount
    conn.close()
    return inserted


def fetch_and_save_oi(db_path: Path = _DB_PATH) -> dict | None:
    """
    拉取最新 OI 并写入 DB。返回最新一条记录（dict），失败返回 None。
    在 bot_loop_start() 中每天调用一次。
    """
    try:
        rows = fetch_latest_oi(limit=2)
        if not rows:
            return None
        records = _rows_to_records(rows)
        n = save_oi_records(records, db_path)
        latest = records[-1]
        if n > 0:
            logger.info(f'[MarketDataStore] OI 新增 {n} 条，最新: {latest["date"]} '
                        f'OI={latest["open_interest"]:,.0f} BTC')
        return latest
    except Exception as e:
        logger.warning(f'[MarketDataStore] OI 获取失败: {e}')
        return None


# ── OI：查询接口 ─────────────────────────────────────────────────────────────

def get_latest_oi(db_path: Path = _DB_PATH) -> dict | None:
    """读取 DB 中最新一条 OI 记录。"""
    conn = _connect(db_path)
    row = conn.execute(
        "SELECT * FROM oi_daily ORDER BY date DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_oi_range(start_date: str, end_date: str, db_path: Path = _DB_PATH) -> list[dict]:
    """读取指定日期范围的 OI 记录（YYYY-MM-DD 格式）。"""
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT * FROM oi_daily WHERE date >= ? AND date <= ? ORDER BY date",
        (start_date, end_date),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_oi_chg_pct(since_date: str, db_path: Path = _DB_PATH) -> float | None:
    """
    计算从 since_date 到最新日期的 OI 变化百分比。
    用于判断 SC→AR 期间 OI 是否暴涨（逼空）。
    """
    conn = _connect(db_path)
    anchor = conn.execute(
        "SELECT open_interest FROM oi_daily WHERE date >= ? ORDER BY date LIMIT 1",
        (since_date,),
    ).fetchone()
    latest = conn.execute(
        "SELECT open_interest FROM oi_daily ORDER BY date DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if not anchor or not latest:
        return None
    return (latest['open_interest'] - anchor['open_interest']) / anchor['open_interest'] * 100
