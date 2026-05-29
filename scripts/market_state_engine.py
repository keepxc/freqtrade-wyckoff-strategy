#!/usr/bin/env python3
"""
Market State Engine
每15分钟扫描币种宇宙，输出结构质量标签，维护 watchlist。

Grade 定义：
  A          — ≥3维度对齐，无陷阱信号，可入场
  B          — 2维度对齐，观察
  SQUEEZE    — OI收缩+价格压缩，等待方向
  LONG_TRAP  — OI扩张 + CVD背离 + Funding过高，禁入
  SHORT_TRAP — OI扩张 + CVD持续 + 价格拒绝，禁入
  WEAK       — 无明显结构

Watchlist规则：
  进入 — 连续3个15m周期 grade=A
  退出 — 连续2个15m周期 grade∉{A,B}
"""

import sqlite3
import time
import logging
import requests
import numpy as np
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DB_PATH = Path(__file__).parent.parent / "user_data" / "market.db"
SCAN_INTERVAL_SEC = 900   # 15 minutes
MAX_WORKERS = 8
PROXY = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}

LIQUIDITY = {
    "min_24h_vol_usdt": 100_000_000,   # 100M USDT
    "min_oi_usdt":       50_000_000,   #  50M USDT
}

SCORE_WEIGHTS = {
    "oi_expand":    20,
    "cvd_rising":   25,
    "funding_ok":   20,
    "trend_accept": 20,
    "vol_surge":    15,
}

GRADE_A_MIN     = 70
GRADE_B_MIN     = 45
OI_EXPAND_MIN   = 0.005    # 0.5% in 4h
FUNDING_MAX     = 0.0005   # 0.05%
VOL_SURGE_MULT  = 1.2

WATCHLIST_ENTRY_N = 3   # consecutive A periods to enter
WATCHLIST_EXIT_N  = 2   # consecutive non-A/B periods to exit

BINANCE = "https://fapi.binance.com"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [MSE] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("mse")

# ---------------------------------------------------------------------------
# DB schema
# ---------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS coin_states (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol        TEXT    NOT NULL,
    ts            INTEGER NOT NULL,
    grade         TEXT    NOT NULL,
    score         REAL    NOT NULL,
    oi_usdt       REAL,
    vol_24h_usdt  REAL,
    oi_change_4h  REAL,
    cvd_rising    INTEGER,
    cvd_diverge   INTEGER,
    funding_rate  REAL,
    funding_ok    INTEGER,
    trend_accept  INTEGER,
    vol_surge     INTEGER,
    dim_count     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_cs_sym_ts ON coin_states(symbol, ts DESC);

CREATE TABLE IF NOT EXISTS watchlist (
    symbol      TEXT    PRIMARY KEY,
    added_ts    INTEGER NOT NULL,
    grade       TEXT    NOT NULL,
    score       REAL    NOT NULL,
    updated_ts  INTEGER NOT NULL
);
"""


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
_session = requests.Session()
_session.proxies.update(PROXY)


def _get(url: str, params: dict = None, timeout: int = 10):
    try:
        r = _session.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.debug(f"HTTP {url} params={params}: {e}")
        return None


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------
def fetch_universe() -> list[dict]:
    """Top 50 USDT perp symbols passing 24h volume filter."""
    tickers = _get(f"{BINANCE}/fapi/v1/ticker/24hr")
    if not tickers:
        return []
    candidates = []
    for t in tickers:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        vol = float(t.get("quoteVolume", 0))
        if vol < LIQUIDITY["min_24h_vol_usdt"]:
            continue
        candidates.append({"symbol": sym, "vol_24h_usdt": vol})
    candidates.sort(key=lambda x: -x["vol_24h_usdt"])
    return candidates[:50]


def fetch_oi_usdt(symbol: str) -> float | None:
    """Current OI converted to USDT."""
    oi_data = _get(f"{BINANCE}/fapi/v1/openInterest", {"symbol": symbol})
    px_data = _get(f"{BINANCE}/fapi/v1/premiumIndex", {"symbol": symbol})
    if not oi_data or not px_data:
        return None
    qty = float(oi_data.get("openInterest", 0))
    px  = float(px_data.get("markPrice", 0))
    return qty * px if px else None


def fetch_oi_change(symbol: str) -> float | None:
    """4h OI change rate using 30m history (8 periods = 4h)."""
    data = _get(
        f"{BINANCE}/futures/data/openInterestHist",
        {"symbol": symbol, "period": "30m", "limit": 10},
    )
    if not data or len(data) < 2:
        return None
    vals = [float(d["sumOpenInterest"]) for d in data]
    lookback = min(8, len(vals) - 1)
    base = vals[-(lookback + 1)]
    return (vals[-1] - base) / base if base else None


def fetch_cvd_metrics(symbol: str) -> dict | None:
    """CVD rising/diverge, trend acceptance, volume surge from klines."""
    data = _get(
        f"{BINANCE}/fapi/v1/klines",
        {"symbol": symbol, "interval": "30m", "limit": 60},
    )
    if not data or len(data) < 20:
        return None

    total_vol = np.array([float(r[5]) for r in data], dtype=float)
    taker_buy = np.array([float(r[9]) for r in data], dtype=float)
    closes    = np.array([float(r[4]) for r in data], dtype=float)

    vd  = 2 * taker_buy - total_vol
    cvd = pd.Series(vd).rolling(40, min_periods=10).sum().values

    # CVD slope: last 6 bars
    cvd_tail    = cvd[-6:]
    cvd_rising  = bool(not np.isnan(cvd_tail).any() and cvd_tail[-1] > cvd_tail[0])

    # Top diverge: price near 20-bar high, CVD not at peak
    price_high  = np.max(closes[-20:])
    cvd_window  = cvd[-20:]
    cvd_valid   = cvd_window[~np.isnan(cvd_window)]
    cvd_peak    = float(np.max(cvd_valid)) if len(cvd_valid) > 0 else 0.0
    top_diverge = (
        closes[-1] >= price_high * 0.999 and
        not np.isnan(cvd[-1]) and
        cvd_peak != 0 and
        cvd[-1] < cvd_peak * 0.95
    )

    # Trend acceptance: price above 20-bar mean and rising vs 5 bars ago
    vwap_approx  = float(np.mean(closes[-20:]))
    trend_accept = bool(closes[-1] > vwap_approx and closes[-1] > closes[-5])

    # Volume surge vs 20-bar average
    avg_vol   = float(np.mean(total_vol[-20:]))
    vol_surge = bool(avg_vol > 0 and total_vol[-1] > avg_vol * VOL_SURGE_MULT)

    return {
        "cvd_rising":   cvd_rising,
        "cvd_diverge":  top_diverge,
        "trend_accept": trend_accept,
        "vol_surge":    vol_surge,
    }


def fetch_funding(symbol: str) -> float | None:
    data = _get(f"{BINANCE}/fapi/v1/fundingRate", {"symbol": symbol, "limit": 1})
    if not data:
        return None
    return float(data[0].get("fundingRate", 0))


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def grade_coin(
    symbol: str,
    vol_24h: float,
    oi_usdt: float | None,
    oi_change: float | None,
    cvd: dict | None,
    funding: float | None,
) -> dict:
    # Defaults when data unavailable
    if cvd is None:
        cvd = {"cvd_rising": False, "cvd_diverge": False,
               "trend_accept": False, "vol_surge": False}
    oi_change  = oi_change  or 0.0
    funding    = funding    or 0.0

    oi_expand    = oi_change    > OI_EXPAND_MIN
    cvd_rising   = cvd["cvd_rising"]
    cvd_diverge  = cvd["cvd_diverge"]
    funding_ok   = abs(funding) <= FUNDING_MAX
    trend_accept = cvd["trend_accept"]
    vol_surge    = cvd["vol_surge"]

    score = 0.0
    if oi_expand:    score += SCORE_WEIGHTS["oi_expand"]
    if cvd_rising:   score += SCORE_WEIGHTS["cvd_rising"]
    if funding_ok:   score += SCORE_WEIGHTS["funding_ok"]
    if trend_accept: score += SCORE_WEIGHTS["trend_accept"]
    if vol_surge:    score += SCORE_WEIGHTS["vol_surge"]

    dim_count = sum([oi_expand, cvd_rising, funding_ok, trend_accept, vol_surge])

    # Trap detection takes priority
    long_trap  = oi_expand and cvd_diverge and not funding_ok
    short_trap = oi_expand and cvd_rising and not trend_accept and score >= GRADE_B_MIN

    if long_trap:
        grade = "LONG_TRAP"
    elif short_trap:
        grade = "SHORT_TRAP"
    elif score >= GRADE_A_MIN and not cvd_diverge:
        grade = "A"
    elif score >= GRADE_B_MIN:
        grade = "B"
    elif not oi_expand and not cvd_rising and funding_ok:
        grade = "SQUEEZE"
    else:
        grade = "WEAK"

    return {
        "symbol":       symbol,
        "grade":        grade,
        "score":        score,
        "oi_usdt":      oi_usdt,
        "vol_24h_usdt": vol_24h,
        "oi_change_4h": oi_change,
        "cvd_rising":   int(cvd_rising),
        "cvd_diverge":  int(cvd_diverge),
        "funding_rate": funding,
        "funding_ok":   int(funding_ok),
        "trend_accept": int(trend_accept),
        "vol_surge":    int(vol_surge),
        "dim_count":    dim_count,
    }


# ---------------------------------------------------------------------------
# Per-coin scan (called in thread pool)
# ---------------------------------------------------------------------------
def scan_coin(coin: dict) -> dict | None:
    symbol  = coin["symbol"]
    vol_24h = coin["vol_24h_usdt"]

    oi_usdt = fetch_oi_usdt(symbol)
    if oi_usdt is not None and oi_usdt < LIQUIDITY["min_oi_usdt"]:
        return None   # fails OI liquidity gate

    oi_change = fetch_oi_change(symbol)
    cvd       = fetch_cvd_metrics(symbol)
    funding   = fetch_funding(symbol)

    return grade_coin(symbol, vol_24h, oi_usdt, oi_change, cvd, funding)


# ---------------------------------------------------------------------------
# Watchlist persistence
# ---------------------------------------------------------------------------
def update_watchlist(conn: sqlite3.Connection, ts_now: int, results: list[dict]) -> None:
    cur = conn.cursor()

    # Index results by symbol for quick lookup
    by_sym = {r["symbol"]: r for r in results}

    # --- Entry check ---
    # For each symbol in current scan with grade A, check last N records
    a_grade_syms = {r["symbol"] for r in results if r["grade"] == "A"}
    for sym in a_grade_syms:
        rows = cur.execute(
            "SELECT grade FROM coin_states WHERE symbol=? ORDER BY ts DESC LIMIT ?",
            (sym, WATCHLIST_ENTRY_N),
        ).fetchall()
        if len(rows) >= WATCHLIST_ENTRY_N and all(r[0] == "A" for r in rows):
            r = by_sym[sym]
            cur.execute(
                """INSERT OR REPLACE INTO watchlist(symbol,added_ts,grade,score,updated_ts)
                   VALUES (?,?,?,?,?)""",
                (sym, ts_now, r["grade"], r["score"], ts_now),
            )
            log.info(f"WATCHLIST ADD   : {sym:15s} score={r['score']:.0f}")

    # --- Exit check ---
    cur.execute("SELECT symbol FROM watchlist")
    in_watchlist = [r[0] for r in cur.fetchall()]
    for sym in in_watchlist:
        rows = cur.execute(
            "SELECT grade FROM coin_states WHERE symbol=? ORDER BY ts DESC LIMIT ?",
            (sym, WATCHLIST_EXIT_N),
        ).fetchall()
        if len(rows) >= WATCHLIST_EXIT_N and all(r[0] not in ("A", "B") for r in rows):
            cur.execute("DELETE FROM watchlist WHERE symbol=?", (sym,))
            grades = [r[0] for r in rows]
            log.info(f"WATCHLIST REMOVE: {sym:15s} recent={grades}")

    conn.commit()


# ---------------------------------------------------------------------------
# One scan cycle
# ---------------------------------------------------------------------------
def run_scan(conn: sqlite3.Connection) -> None:
    ts_now = int(time.time())
    log.info("=" * 60)
    log.info("Scan start")

    universe = fetch_universe()
    log.info(f"Volume-filtered universe: {len(universe)} coins")
    if not universe:
        log.warning("Empty universe, skipping scan")
        return

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(scan_coin, c): c["symbol"] for c in universe}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                res = fut.result()
                if res:
                    results.append(res)
            except Exception as e:
                log.warning(f"{sym}: {e}")

    # Write coin_states
    cur = conn.cursor()
    for r in results:
        cur.execute(
            """INSERT INTO coin_states
               (symbol,ts,grade,score,oi_usdt,vol_24h_usdt,oi_change_4h,
                cvd_rising,cvd_diverge,funding_rate,funding_ok,
                trend_accept,vol_surge,dim_count)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (r["symbol"], ts_now, r["grade"], r["score"],
             r["oi_usdt"], r["vol_24h_usdt"], r["oi_change_4h"],
             r["cvd_rising"], r["cvd_diverge"], r["funding_rate"],
             r["funding_ok"], r["trend_accept"], r["vol_surge"], r["dim_count"]),
        )
    conn.commit()

    update_watchlist(conn, ts_now, results)

    # Summary
    grade_counts: dict[str, int] = {}
    for r in results:
        grade_counts[r["grade"]] = grade_counts.get(r["grade"], 0) + 1
    wl_count = conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]
    log.info(f"Results: {len(results)} coins | {grade_counts}")
    log.info(f"Watchlist: {wl_count} coins")

    wl = conn.execute(
        "SELECT symbol, grade, score, oi_change_4h, funding_rate FROM watchlist ORDER BY score DESC"
    ).fetchall()
    for sym, grade, score, oi_chg, fr in wl:
        log.info(
            f"  WL {sym:15s} {grade:10s} score={score:5.0f} "
            f"OI4h={oi_chg*100:+.2f}% FR={fr*100:.4f}%"
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    init_db(conn)
    log.info(f"Market State Engine started | DB: {DB_PATH}")
    log.info(f"Scan interval: {SCAN_INTERVAL_SEC}s | Workers: {MAX_WORKERS}")

    while True:
        try:
            run_scan(conn)
        except Exception as e:
            log.error(f"Scan cycle failed: {e}", exc_info=True)
        log.info(f"Next scan in {SCAN_INTERVAL_SEC // 60}m")
        time.sleep(SCAN_INTERVAL_SEC)


if __name__ == "__main__":
    main()
