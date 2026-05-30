#!/usr/bin/env python3
"""
Market State Engine
每15分钟扫描币种宇宙，输出结构质量标签，维护 watchlist。

评分系统（设计文档 OI+CVD Hybrid Strategy，100分制）：
  Price Acceptance  25  — 突破前高8 + 持续站稳7 + 回踩不破5 + 强收盘5
  OI Expansion      25  — OI绝对值8 + OI加速度10 + 连续上升7
  CVD Persistence   20  — 斜率正向8 + 连续3根7 + 更高高点5
  Trend Structure   15  — 30m趋势5 + 4h对齐5 + 斜率正向5
  Funding Health    10  — 中性10 / 略热5 / 极热0 / 极端-10
  Liquidity Quality  5  — 24h量2 + OI规模2 + 价量比合理1

Grade 定义：
  A          — ≥85分，强趋势结构，多维度共振
  B          — 75-85分，可交易结构
  WATCH      — 65-75分，有潜力但不稳定
  NOISE      — <65分，噪音概率高
  BLOCKED    — 硬过滤器触发（Funding过热/价量背离/波动率爆炸/低流动性）
  LONG_TRAP  — OI扩张 + CVD背离 + Funding过高
  SHORT_TRAP — OI扩张 + CVD持续 + 价格拒绝
  SQUEEZE    — OI收缩 + CVD不上升 + Funding正常，等待方向

Watchlist规则：
  进入 — 连续3个15m周期 grade=A
  退出 — 连续2个15m周期 grade∉{A,B}

Hard Filters（任一触发 → BLOCKED）：
  Funding > 0.08%
  OI涨但价格不动（吸收结构）
  ATR > ATR_MA × 3（波动率爆炸）
  OI < 20M
"""

import sqlite3
import time
import logging
import os
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

MIN_24H_VOL_USDT = 100_000_000   # 100M USDT — universe volume gate

# ── Scoring Config (design doc: OI+CVD Hybrid Strategy, 100-point system) ──
GRADE_A_MIN     = 85    # A级：强趋势结构
GRADE_B_MIN     = 75    # B级：可交易结构
GRADE_WATCH_MIN = 65    # 观察级：有潜力但不稳定，<65=忽略/噪音

OI_EXPAND_MIN   = 0.005   # OI 4h 增幅 > 0.5%
OI_ACCEL_MIN    = 0.002   # OI 加速度阈值
FUNDING_MAX     = 0.0005  # 0.05%，中性偏多
FUNDING_WARN    = 0.0008  # 0.08%，禁止追多
VOL_SURGE_MULT  = 1.2     # 量能突增倍数
VOL_EXPLOSION   = 3.0     # ATR > ATR_MA * 3
MIN_OI_USDT     = 50_000_000

WATCHLIST_ENTRY_N = 3   # consecutive WATCH periods to enter
WATCHLIST_EXIT_N  = 2   # consecutive non-WATCH periods to exit
# Gap must be within ±30% of SCAN_INTERVAL_SEC for genuine consecutive scans
WATCHLIST_GAP_MIN = int(SCAN_INTERVAL_SEC * 0.7)   # ~630s
WATCHLIST_GAP_MAX = int(SCAN_INTERVAL_SEC * 1.3)   # ~1170s

BINANCE = "https://fapi.binance.com"

# ── 窗口参数（支持环境变量覆盖，用于超参扫描）────────────
def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(f"MSE_{name}", default))

CVD_WINDOW         = _env_int("CVD_WINDOW", 40)          # CVD 滚动窗口（bar数，15m×40=10h）
CVD_SLOPE_N        = _env_int("CVD_SLOPE_N", 6)           # CVD 斜率判断（bar数，15m×6=1.5h）
CVD_DIVERGE_WINDOW = _env_int("CVD_DIVERGE_WINDOW", 20)   # 背离检测 + 趋势均值窗口
PA_RANGE_N         = _env_int("PA_RANGE_N", 6)             # Price Acceptance 突破范围（bar数）
PA_SUSTAIN_N       = _env_int("PA_SUSTAIN_N", 3)           # Price Acceptance 持续站稳（bar数）
TREND_MA_WINDOW    = _env_int("TREND_MA_WINDOW", 40)       # 4h 趋势参考 MA 窗口（15m×40=10h 平滑）
VOL_MA_WINDOW      = _env_int("VOL_MA_WINDOW", 20)         # 量能均值窗口

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [MSE] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("mse")

# ── 历史回放模式 ─────────────────────────────────────────
# 设置 MSE_SIMULATE_TS=unix_timestamp 后，所有 API 调用使用 endTime 获取历史数据
SIMULATE_TS: int | None = None
_sim_ts = os.environ.get("MSE_SIMULATE_TS", "")
if _sim_ts:
    SIMULATE_TS = int(_sim_ts)
    log.info(f"Simulate mode: endTime={SIMULATE_TS} ({time.strftime('%Y-%m-%d %H:%M', time.localtime(SIMULATE_TS))})")

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

CREATE TABLE IF NOT EXISTS top10 (
    rank         INTEGER PRIMARY KEY,
    symbol       TEXT    NOT NULL,
    grade        TEXT    NOT NULL,
    score        REAL    NOT NULL,
    oi_change_4h REAL,
    funding_rate REAL,
    cvd_rising   INTEGER,
    trend_accept INTEGER,
    vol_surge    INTEGER,
    updated_ts   INTEGER NOT NULL
);

-- ── V2 scoring (design doc: Price Acceptance 25 + OI Expansion 25 + CVD 20 + Trend 15 + Funding 10 + Liquidity 5) ──
CREATE TABLE IF NOT EXISTS coin_states_v2 (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol           TEXT    NOT NULL,
    ts               INTEGER NOT NULL,
    grade            TEXT    NOT NULL,
    score            REAL    NOT NULL,
    -- sub-scores
    score_price_acc  REAL,   -- Price Acceptance (0-25)
    score_oi_expand  REAL,   -- OI Expansion with acceleration (0-25)
    score_cvd_pers   REAL,   -- CVD Persistence (0-20)
    score_trend      REAL,   -- Trend Structure (0-15)
    score_funding    REAL,   -- Funding Health (0-10, can be negative)
    score_liquidity  REAL,   -- Liquidity Quality (0-5)
    -- raw metrics
    oi_usdt          REAL,
    vol_24h_usdt     REAL,
    oi_change_4h     REAL,
    oi_acceleration  REAL,
    funding_rate     REAL,
    cvd_rising       INTEGER,
    cvd_diverge      INTEGER,
    cvd_continuous   INTEGER,  -- 连续3根CVD正向
    trend_accept     INTEGER,
    vol_surge        INTEGER,
    dim_count        INTEGER,
    -- trap flags
    is_long_trap     INTEGER,
    is_short_trap    INTEGER,
    is_squeeze       INTEGER,
    -- hard filter triggers
    hf_funding_hot   INTEGER,
    hf_oi_price_div  INTEGER,
    hf_vol_explosion INTEGER,
    hf_low_liquidity INTEGER
);
CREATE INDEX IF NOT EXISTS idx_cs2_sym_ts ON coin_states_v2(symbol, ts DESC);
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
    if params is None:
        params = {}
    if SIMULATE_TS and "endTime" not in params:
        params["endTime"] = SIMULATE_TS * 1000  # Binance uses ms
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
        if SIMULATE_TS:
            # 24hr ticker doesn't support endTime; use static top-30 universe for replay
            STATIC_UNIVERSE = [
                "BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT","ADAUSDT","DOGEUSDT",
                "AVAXUSDT","LINKUSDT","DOTUSDT","MATICUSDT","UNIUSDT","ATOMUSDT",
                "LTCUSDT","ETCUSDT","FILUSDT","APTUSDT","ARBUSDT","OPUSDT",
                "NEARUSDT","INJUSDT","SUIUSDT","SEIUSDT","TIAUSDT","WLDUSDT",
                "PEPEUSDT","WIFUSDT","BONKUSDT","RNDRUSDT","FETUSDT",
            ]
            log.info(f"Simulate: using static universe ({len(STATIC_UNIVERSE)} coins)")
            return [{"symbol": s, "vol_24h_usdt": 0} for s in STATIC_UNIVERSE]
        return []
    candidates = []
    for t in tickers:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        vol = float(t.get("quoteVolume", 0))
        if vol < MIN_24H_VOL_USDT:
            continue
        candidates.append({"symbol": sym, "vol_24h_usdt": vol})
    candidates.sort(key=lambda x: -x["vol_24h_usdt"])
    return candidates[:50]


def fetch_oi_usdt(symbol: str) -> float | None:
    """Current OI converted to USDT."""
    if SIMULATE_TS:
        return 100_000_000  # dummy value, skip OI check in replay
    oi_data = _get(f"{BINANCE}/fapi/v1/openInterest", {"symbol": symbol})
    px_data = _get(f"{BINANCE}/fapi/v1/premiumIndex", {"symbol": symbol})
    if not oi_data or not px_data:
        return None
    qty = float(oi_data.get("openInterest", 0))
    px  = float(px_data.get("markPrice", 0))
    return qty * px if px else None


def fetch_funding(symbol: str) -> float | None:
    data = _get(f"{BINANCE}/fapi/v1/fundingRate", {"symbol": symbol, "limit": 1})
    if not data:
        return None
    return float(data[0].get("fundingRate", 0))


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------
def fetch_oi_metrics(symbol: str) -> dict | None:
    """Returns 4h OI change rate + acceleration (16×15m bars = 4h window)."""
    data = _get(
        f"{BINANCE}/futures/data/openInterestHist",
        {"symbol": symbol, "period": "15m", "limit": 40},
    )
    if not data or len(data) < 18:
        return None
    vals = [float(d["sumOpenInterest"]) for d in data]
    # current 4h = last 16 periods vs 16 periods ago (16×15m=4h)
    if len(vals) < 17:
        return None
    cur_base  = vals[-17]
    cur_oi    = vals[-1]
    oi_change = (cur_oi - cur_base) / cur_base if cur_base else 0.0
    # previous 4h = periods -33 to -17
    if len(vals) >= 34:
        prev_base  = vals[-33]
        prev_oi    = vals[-17]
        prev_change = (prev_oi - prev_base) / prev_base if prev_base else 0.0
        acceleration = oi_change - prev_change
    else:
        acceleration = 0.0
    # consecutive OI up periods (last 3)
    oi_series = pd.Series(vals)
    oi_deltas = oi_series.diff().tail(3)
    consecutive_up = int((oi_deltas > 0).all())
    return {
        "oi_change_4h":    oi_change,
        "oi_acceleration": acceleration,
        "oi_consecutive_up": consecutive_up,
    }


def fetch_price_cvd_metrics(symbol: str) -> dict | None:
    """Kline analysis: returns CVD fields + price acceptance + CVD persistence sub-scores."""
    kline_limit = max(CVD_WINDOW, TREND_MA_WINDOW, PA_RANGE_N, VOL_MA_WINDOW, CVD_DIVERGE_WINDOW) * 2
    kline_limit = max(kline_limit, 80)  # minimum 80 bars
    data = _get(
        f"{BINANCE}/fapi/v1/klines",
        {"symbol": symbol, "interval": "15m", "limit": kline_limit},
    )
    min_bars = max(CVD_WINDOW, TREND_MA_WINDOW) // 2
    if not data or len(data) < min_bars:
        return None

    opens   = np.array([float(r[1]) for r in data], dtype=float)
    highs   = np.array([float(r[2]) for r in data], dtype=float)
    lows    = np.array([float(r[3]) for r in data], dtype=float)
    closes  = np.array([float(r[4]) for r in data], dtype=float)
    total_vol = np.array([float(r[5]) for r in data], dtype=float)
    taker_buy = np.array([float(r[9]) for r in data], dtype=float)

    n = len(data)
    vd  = 2 * taker_buy - total_vol
    cvd = pd.Series(vd).rolling(CVD_WINDOW, min_periods=max(10, CVD_WINDOW // 4)).sum().values

    # ── CVD slope: last CVD_SLOPE_N bars ──
    cvd_tail   = cvd[-CVD_SLOPE_N:]
    cvd_rising = bool(not np.isnan(cvd_tail).any() and cvd_tail[-1] > cvd_tail[0])

    # ── Top diverge: price near CVD_DIVERGE_WINDOW-bar high, CVD not at peak ──
    dw = CVD_DIVERGE_WINDOW
    price_high  = np.max(closes[-dw:])
    cvd_window  = cvd[-dw:]
    cvd_valid   = cvd_window[~np.isnan(cvd_window)]
    cvd_peak    = float(np.max(cvd_valid)) if len(cvd_valid) > 0 else 0.0
    top_diverge = (
        closes[-1] >= price_high * 0.999 and
        not np.isnan(cvd[-1]) and
        cvd_peak != 0 and
        cvd[-1] < cvd_peak * 0.95
    )

    # ── Trend acceptance: price above TREND_MA_WINDOW-bar mean ──
    tw = TREND_MA_WINDOW
    vwap_approx  = float(np.mean(closes[-tw:]))
    trend_accept = bool(closes[-1] > vwap_approx and closes[-1] > closes[-5])

    # ── Volume surge vs VOL_MA_WINDOW-bar average ──
    avg_vol   = float(np.mean(total_vol[-VOL_MA_WINDOW:]))
    vol_surge = bool(avg_vol > 0 and total_vol[-1] > avg_vol * VOL_SURGE_MULT)

    # ── Price Acceptance (25分) ──
    # 1) breakout: close > previous range high (last PA_RANGE_N bars, excluding current)
    range_high_n = np.max(highs[-(PA_RANGE_N + 1):-1])
    breakout     = closes[-1] > range_high_n

    # 2) sustained: close > breakout level for PA_SUSTAIN_N consecutive bars
    sustained_bars = int(closes[-1] > range_high_n)
    for i in range(2, min(PA_SUSTAIN_N + 1, n)):
        if closes[-i] > range_high_n:
            sustained_bars += 1
        else:
            break
    sustained = sustained_bars >= PA_SUSTAIN_N

    # 3) pullback OK: recent pullback low doesn't break below breakout level
    pullback_lows = lows[-5:]
    pullback_ok   = bool(np.all(pullback_lows >= range_high_n * 0.995))

    # 4) strong close: close in upper 30% of bar range
    bar_range = highs[-1] - lows[-1]
    strong_close = bool(bar_range > 0 and (closes[-1] - lows[-1]) / bar_range >= 0.70)

    # ── CVD Persistence (20分) ──
    cvd_slope_pos = cvd_rising  # reuse

    # 连续3根CVD正向
    cvd_deltas = pd.Series(vd).tail(3).values
    cvd_continuous = bool(len(cvd_deltas) >= 3 and np.all(cvd_deltas > 0))

    # CVD higher high (last CVD_SLOPE_N bars)
    cvd_tail_n = cvd[-CVD_SLOPE_N:]
    cvd_valid_n = cvd_tail_n[~np.isnan(cvd_tail_n)]
    cvd_higher_high = bool(
        len(cvd_valid_n) >= 2 and
        not np.isnan(cvd[-1]) and
        cvd[-1] > np.max(cvd_valid_n[:-1])
    )

    # ── Trend Structure (15分) ──
    trend_30m_up = bool(closes[-1] > vwap_approx)
    # 4h alignment: price > TREND_MA_WINDOW-bar mean
    if n >= TREND_MA_WINDOW:
        ma_long = float(np.mean(closes[-TREND_MA_WINDOW:]))
        trend_4h_up = bool(closes[-1] > ma_long)
    else:
        trend_4h_up = trend_30m_up
    # slope positive: 5-bar linear slope > 0
    if n >= 5:
        x = np.arange(5)
        y = closes[-5:]
        slope = np.polyfit(x, y, 1)[0] if np.std(y) > 0 else 0.0
        slope_pos = bool(slope > 0)
    else:
        slope_pos = trend_30m_up

    # ── V2: Vol explosion check (ATR / ATR_MA) ──
    tr_arr = np.maximum(
        highs[1:] - lows[1:],
        np.maximum(
            np.abs(highs[1:] - closes[:-1]),
            np.abs(lows[1:] - closes[:-1]),
        )
    )
    atr14 = float(np.mean(tr_arr[-14:])) if len(tr_arr) >= 14 else float(np.mean(tr_arr))
    atr_ma20 = float(np.mean(tr_arr[-20:])) if len(tr_arr) >= 20 else atr14
    vol_explosion = bool(atr_ma20 > 0 and atr14 > atr_ma20 * VOL_EXPLOSION)

    return {
        # v1 compatible
        "cvd_rising":    cvd_rising,
        "cvd_diverge":   top_diverge,
        "trend_accept":  trend_accept,
        "vol_surge":     vol_surge,
        # v2 price acceptance
        "pa_breakout":      breakout,
        "pa_sustained":     sustained,
        "pa_pullback_ok":   pullback_ok,
        "pa_strong_close":  strong_close,
        # v2 CVD persistence
        "cvd_slope_pos":    cvd_slope_pos,
        "cvd_continuous":   cvd_continuous,
        "cvd_higher_high":  cvd_higher_high,
        # v2 trend
        "trend_30m_up":   trend_30m_up,
        "trend_4h_up":    trend_4h_up,
        "trend_slope_pos": slope_pos,
        # v2 risk
        "vol_explosion": vol_explosion,
        "atr14":         atr14,
    }


# ---------------------------------------------------------------------------
# Scoring (design doc: OI+CVD Hybrid Strategy, 100-point system)
# ---------------------------------------------------------------------------
def grade_coin(
    symbol: str,
    vol_24h: float,
    oi_usdt: float | None,
    oi_metrics: dict | None,
    pcvd: dict | None,
    funding: float | None,
) -> dict:
    """
    Design-doc scoring system:
      Price Acceptance  25  — 市场接受新价格区间
      OI Expansion      25  — 杠杆资金加速进入
      CVD Persistence   20  — 主动买卖盘持续性
      Trend Structure   15  — 大级别趋势背景
      Funding Health    10  — 拥挤交易检测
      Liquidity Quality  5  — 数据可信度
    Grade: A(85+), B(75-85), Watch(65-75), Ignore(<65)
    Plus trap detection (borrowed from v1).
    """
    # ── Defaults ──
    if pcvd is None:
        pcvd = {
            "cvd_rising": False, "cvd_diverge": False,
            "trend_accept": False, "vol_surge": False,
            "pa_breakout": False, "pa_sustained": False,
            "pa_pullback_ok": False, "pa_strong_close": False,
            "cvd_slope_pos": False, "cvd_continuous": False,
            "cvd_higher_high": False,
            "trend_30m_up": False, "trend_4h_up": False,
            "trend_slope_pos": False,
            "vol_explosion": False, "atr14": 0.0,
        }
    if oi_metrics is None:
        oi_metrics = {"oi_change_4h": 0.0, "oi_acceleration": 0.0, "oi_consecutive_up": 0}
    funding = funding or 0.0
    oi_usdt = oi_usdt or 0.0

    # ── Sub-score 1: Price Acceptance (0-25) ──
    score_pa = 0.0
    if pcvd["pa_breakout"]:
        score_pa += 8
    if pcvd["pa_sustained"]:
        score_pa += 7
    if pcvd["pa_pullback_ok"]:
        score_pa += 5
    if pcvd["pa_strong_close"]:
        score_pa += 5

    # ── Sub-score 2: OI Expansion (0-25) ──
    oi_change = oi_metrics["oi_change_4h"]
    oi_accel  = oi_metrics["oi_acceleration"]
    score_oi = 0.0
    # 历史回放模式下 OI 接口可能不可用 → 满分放行
    if SIMULATE_TS and oi_change == 0.0 and oi_accel == 0.0:
        score_oi = 25
    else:
        # OI change absolute
        if oi_change > OI_EXPAND_MIN:
            score_oi += 8
        elif oi_change > OI_EXPAND_MIN * 0.5:
            score_oi += 4
        # OI acceleration
        if oi_accel > OI_ACCEL_MIN:
            score_oi += 10
        elif oi_accel > 0:
            score_oi += 5
        # consecutive OI up periods
        if oi_metrics["oi_consecutive_up"]:
            score_oi += 7
        elif oi_change > 0:
            score_oi += 3

    # ── Sub-score 3: CVD Persistence (0-20) ──
    score_cvd = 0.0
    if pcvd["cvd_slope_pos"]:
        score_cvd += 8
    if pcvd["cvd_continuous"]:
        score_cvd += 7
    if pcvd["cvd_higher_high"]:
        score_cvd += 5

    # ── Sub-score 4: Trend Structure (0-15) ──
    score_trend = 0.0
    if pcvd["trend_30m_up"]:
        score_trend += 5
    if pcvd["trend_4h_up"]:
        score_trend += 5
    if pcvd["trend_slope_pos"]:
        score_trend += 5

    # ── Sub-score 5: Funding Health (0-10, can go negative) ──
    score_funding = 0.0
    abs_fr = abs(funding)
    # 历史回放模式下 Funding 接口可能不可用 → 满分放行
    if SIMULATE_TS and funding == 0.0:
        score_funding = 10
    elif abs_fr <= FUNDING_MAX:
        score_funding = 10       # 中性偏多
    elif abs_fr <= FUNDING_WARN:
        score_funding = 5        # 略热
    elif abs_fr <= FUNDING_WARN * 3:
        score_funding = 0        # 极热
    else:
        score_funding = -10      # 极端，惩罚

    # ── Sub-score 6: Liquidity Quality (0-5) ──
    score_liq = 0.0
    if vol_24h > 100_000_000:
        score_liq += 2
    if oi_usdt > 50_000_000:
        score_liq += 2
    # spread proxy: if OI/volume ratio is reasonable (not a ghost coin)
    if vol_24h > 0 and oi_usdt > 0:
        oi_vol_ratio = oi_usdt / vol_24h
        if 0.01 < oi_vol_ratio < 10:  # reasonable range
            score_liq += 1

    # ── Total score ──
    total_score = score_pa + score_oi + score_cvd + score_trend + score_funding + score_liq

    # ── Hard filters ──
    hf_funding_hot   = int(abs_fr > FUNDING_WARN)
    hf_oi_price_div  = int(
        oi_change > OI_EXPAND_MIN and
        not pcvd["pa_breakout"] and
        not pcvd["pa_strong_close"]
    )  # OI涨但价格不动 = 吸收结构
    hf_vol_explosion = int(pcvd["vol_explosion"])
    hf_low_liquidity = int(oi_usdt < MIN_OI_USDT)

    hard_triggered = hf_funding_hot or hf_oi_price_div or hf_vol_explosion or hf_low_liquidity

    # ── Trap detection ──
    oi_expand   = oi_change > OI_EXPAND_MIN
    cvd_rising  = pcvd["cvd_rising"]
    cvd_diverge = pcvd["cvd_diverge"]
    funding_ok  = abs_fr <= FUNDING_MAX
    trend_acc   = pcvd["trend_accept"]

    long_trap  = oi_expand and cvd_diverge and not funding_ok
    short_trap = oi_expand and cvd_rising and not trend_acc and total_score >= GRADE_B_MIN
    squeeze    = not oi_expand and not cvd_rising and funding_ok

    # ── Final grade ──
    dim_count = sum([
        score_pa >= 15,
        score_oi >= 15,
        score_cvd >= 12,
        score_trend >= 10,
        score_funding >= 5,
        score_liq >= 3,
    ])

    if hard_triggered:
        grade = "BLOCKED"
    elif long_trap:
        grade = "LONG_TRAP"
    elif short_trap:
        grade = "SHORT_TRAP"
    elif squeeze:
        grade = "SQUEEZE"
    elif total_score >= GRADE_A_MIN and not cvd_diverge:
        grade = "A"
    elif total_score >= GRADE_B_MIN:
        grade = "B"
    elif total_score >= GRADE_WATCH_MIN:
        grade = "WATCH"
    else:
        grade = "NOISE"

    return {
        "symbol":           symbol,
        "grade":            grade,
        "score":            total_score,
        "score_price_acc":  score_pa,
        "score_oi_expand":  score_oi,
        "score_cvd_pers":   score_cvd,
        "score_trend":      score_trend,
        "score_funding":    score_funding,
        "score_liquidity":  score_liq,
        "oi_usdt":          oi_usdt,
        "vol_24h_usdt":     vol_24h,
        "oi_change_4h":     oi_change,
        "oi_acceleration":  oi_accel,
        "funding_rate":     funding,
        "cvd_rising":       int(pcvd["cvd_rising"]),
        "cvd_diverge":      int(pcvd["cvd_diverge"]),
        "cvd_continuous":   int(pcvd["cvd_continuous"]),
        "trend_accept":     int(pcvd["trend_accept"]),
        "vol_surge":        int(pcvd["vol_surge"]),
        "dim_count":        dim_count,
        "is_long_trap":     int(long_trap),
        "is_short_trap":    int(short_trap),
        "is_squeeze":       int(squeeze),
        "hf_funding_hot":   hf_funding_hot,
        "hf_oi_price_div":  hf_oi_price_div,
        "hf_vol_explosion": hf_vol_explosion,
        "hf_low_liquidity": hf_low_liquidity,
    }


# ---------------------------------------------------------------------------
# Per-coin scan
# ---------------------------------------------------------------------------
def scan_coin(coin: dict) -> dict | None:
    symbol  = coin["symbol"]
    vol_24h = coin["vol_24h_usdt"]

    oi_usdt = fetch_oi_usdt(symbol)
    if oi_usdt is not None and oi_usdt < MIN_OI_USDT:
        return None

    oi_metrics = fetch_oi_metrics(symbol)
    pcvd       = fetch_price_cvd_metrics(symbol)
    funding    = fetch_funding(symbol)

    return grade_coin(symbol, vol_24h, oi_usdt, oi_metrics, pcvd, funding)


# ---------------------------------------------------------------------------
# Watchlist persistence
# ---------------------------------------------------------------------------
def update_watchlist(conn: sqlite3.Connection, ts_now: int, results: list[dict]) -> None:
    cur = conn.cursor()

    # Index results by symbol for quick lookup
    by_sym = {r["symbol"]: r for r in results}

    # --- Entry check ---
    # Accept any grade >= WATCH_MIN: WATCH, B, A
    # Validate scan timestamps are from the regular 15-min cycle (±30% tolerance)
    WATCH_GRADES = {"WATCH", "B", "A"}
    a_grade_syms = {r["symbol"] for r in results if r["grade"] in WATCH_GRADES}
    for sym in a_grade_syms:
        rows = cur.execute(
            "SELECT grade, ts FROM coin_states_v2 WHERE symbol=? ORDER BY ts DESC LIMIT ?",
            (sym, WATCHLIST_ENTRY_N),
        ).fetchall()
        if len(rows) >= WATCHLIST_ENTRY_N and all(r[0] in WATCH_GRADES for r in rows):
            # Verify genuine scan cycle gaps (not back-to-back cache hits)
            timestamps = [r[1] for r in rows]
            gaps_ok = True
            for i in range(len(timestamps) - 1):
                gap = timestamps[i] - timestamps[i + 1]
                if gap < WATCHLIST_GAP_MIN or gap > WATCHLIST_GAP_MAX:
                    gaps_ok = False
                    break
            if not gaps_ok:
                gaps = [timestamps[i] - timestamps[i+1] for i in range(len(timestamps)-1)]
                log.debug(
                    f"WATCHLIST SKIP  : {sym:15s} gaps={gaps}s "
                    f"(need {WATCHLIST_GAP_MIN}-{WATCHLIST_GAP_MAX}s)"
                )
                continue
            r = by_sym[sym]
            cur.execute(
                """INSERT OR REPLACE INTO watchlist(symbol,added_ts,grade,score,updated_ts)
                   VALUES (?,?,?,?,?)""",
                (sym, ts_now, r["grade"], r["score"], ts_now),
            )
            log.info(
                f"WATCHLIST ADD   : {sym:15s} grade={r['grade']} score={r['score']:.0f} "
                f"@ {time.strftime('%H:%M', time.localtime(ts_now))}"
            )

    # --- Exit check ---
    KEEP_GRADES = {"WATCH", "B", "A", "SQUEEZE"}
    cur.execute("SELECT symbol FROM watchlist")
    in_watchlist = [r[0] for r in cur.fetchall()]
    for sym in in_watchlist:
        rows = cur.execute(
            "SELECT grade FROM coin_states_v2 WHERE symbol=? ORDER BY ts DESC LIMIT ?",
            (sym, WATCHLIST_EXIT_N),
        ).fetchall()
        if len(rows) >= WATCHLIST_EXIT_N and all(r[0] not in KEEP_GRADES for r in rows):
            cur.execute("DELETE FROM watchlist WHERE symbol=?", (sym,))
            grades = [r[0] for r in rows]
            log.info(f"WATCHLIST REMOVE: {sym:15s} recent={grades}")

    conn.commit()


# ---------------------------------------------------------------------------
# Top 10 persistence (always refreshed each scan, not cumulative)
# ---------------------------------------------------------------------------
def update_top10(conn: sqlite3.Connection, ts_now: int, results: list[dict]) -> None:
    """Replace top10 table with current scan's top 10 by score."""
    cur = conn.cursor()
    cur.execute("DELETE FROM top10")

    # Sort by score descending, take top 10
    ranked = sorted(results, key=lambda r: -r["score"])[:10]
    for i, r in enumerate(ranked, start=1):
        cur.execute(
            """INSERT INTO top10
               (rank, symbol, grade, score, oi_change_4h, funding_rate,
                cvd_rising, trend_accept, vol_surge, updated_ts)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (i, r["symbol"], r["grade"], r["score"],
             r["oi_change_4h"], r["funding_rate"],
             r["cvd_rising"], r["trend_accept"], r["vol_surge"],
             ts_now),
        )

    conn.commit()

    # Log top 10
    log.info("Top 10 by score:")
    for i, r in enumerate(ranked, start=1):
        log.info(
            f"  #{i:2d} {r['symbol']:12s} {r['grade']:10s} "
            f"score={r['score']:5.0f} | OI4h={r['oi_change_4h']*100:+.2f}% "
            f"FR={r['funding_rate']*100:.4f}% | "
            f"CVD={r['cvd_rising']} TA={r['trend_accept']} VS={r['vol_surge']}"
        )


# ---------------------------------------------------------------------------
# One scan cycle
# ---------------------------------------------------------------------------
def run_scan(conn: sqlite3.Connection) -> None:
    ts_now = SIMULATE_TS if SIMULATE_TS else int(time.time())
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
                if res is not None:
                    results.append(res)
            except Exception as e:
                log.warning(f"{sym}: {e}")

    # ── Write coin_states_v2 ──
    cur = conn.cursor()
    for r in results:
        cur.execute(
            """INSERT INTO coin_states_v2
               (symbol,ts,grade,score,
                score_price_acc,score_oi_expand,score_cvd_pers,
                score_trend,score_funding,score_liquidity,
                oi_usdt,vol_24h_usdt,oi_change_4h,oi_acceleration,
                funding_rate,cvd_rising,cvd_diverge,cvd_continuous,
                trend_accept,vol_surge,dim_count,
                is_long_trap,is_short_trap,is_squeeze,
                hf_funding_hot,hf_oi_price_div,hf_vol_explosion,hf_low_liquidity)
               VALUES (?,?,?,?,
                       ?,?,?,
                       ?,?,?,
                       ?,?,?,?,
                       ?,?,?,?,
                       ?,?,?,
                       ?,?,?,
                       ?,?,?,?)""",
            (r["symbol"], ts_now, r["grade"], r["score"],
             r["score_price_acc"], r["score_oi_expand"], r["score_cvd_pers"],
             r["score_trend"], r["score_funding"], r["score_liquidity"],
             r["oi_usdt"], r["vol_24h_usdt"], r["oi_change_4h"], r["oi_acceleration"],
             r["funding_rate"], r["cvd_rising"], r["cvd_diverge"], r["cvd_continuous"],
             r["trend_accept"], r["vol_surge"], r["dim_count"],
             r["is_long_trap"], r["is_short_trap"], r["is_squeeze"],
             r["hf_funding_hot"], r["hf_oi_price_div"], r["hf_vol_explosion"], r["hf_low_liquidity"]),
        )
    conn.commit()

    # Watchlist + Top10 from V2 results
    update_watchlist(conn, ts_now, results)
    update_top10(conn, ts_now, results)

    # ── Summary ──
    grade_counts: dict[str, int] = {}
    for r in results:
        grade_counts[r["grade"]] = grade_counts.get(r["grade"], 0) + 1
    wl_count = conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]
    log.info(f"Results: {len(results)} coins | {grade_counts}")
    log.info(f"Watchlist: {wl_count} coins")

    # Top 5 detail
    top5 = sorted(results, key=lambda r: -r["score"])[:5]
    log.info("Top 5:")
    for r in top5:
        log.info(
            f"  {r['symbol']:12s} {r['grade']:10s} total={r['score']:5.0f} | "
            f"PA={r['score_price_acc']:3.0f} OI={r['score_oi_expand']:3.0f} "
            f"CVD={r['score_cvd_pers']:3.0f} TR={r['score_trend']:3.0f} "
            f"FR={r['score_funding']:3.0f} LQ={r['score_liquidity']:3.0f} | "
            f"OI4h={r['oi_change_4h']*100:+.2f}% OIacc={r['oi_acceleration']*100:+.2f}%"
        )

    # Watchlist detail
    wl = conn.execute(
        "SELECT symbol, grade, score FROM watchlist ORDER BY score DESC"
    ).fetchall()
    for sym, grade, score in wl:
        log.info(f"  WL {sym:15s} {grade:10s} score={score:5.0f}")


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
