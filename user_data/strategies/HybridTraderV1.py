# HybridTraderV1 — 独立策略，与 WyckoffV8 无关联
# 基于：HMA55/63 + Supertrend(10,3)，15m K 线
# 架构：三层过滤 + 加权评分
#   Layer 1 — 趋势过滤：ST方向+持续性 (20) + HMA对齐+斜率 (15) = 35
#   Layer 2 — 资本确认：OI Efficiency (20) + CVD持续性 (20) + Price联动 (5) = 45
#   Layer 3 — 健康度：RSI (10) + Funding (5) + 成交量 (5) = 20
#   总分 100，≥75 入场 + MSE watchlist 门前 + Risk Gates

import time
import threading
import sqlite3
import json
import numpy as np
import pandas as pd
import requests
from datetime import date
from freqtrade.strategy import IStrategy, DecimalParameter, IntParameter
from pandas import DataFrame
from functools import reduce
from pathlib import Path


PROXIES = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}


class HybridTraderV1(IStrategy):
    """
    Hybrid Trader V1 — 15m 趋势跟踪系统
    趋势层：Supertrend(10,3) — 方向 + 动态止损参考
    执行层：HMA55 / HMA63 金叉/死叉
    动量过滤：RSI(14)
    止损：Supertrend 线（ATR 自适应）
    """

    INTERFACE_VERSION = 3
    timeframe = "15m"

    # ── 基础设置 ──────────────────────────────────────────
    can_short = True           # 多空双向
    use_custom_stoploss = True
    stoploss = -0.08
    trailing_stop = False

    # ROI：主要靠 custom_exit，这里设宽松兜底
    minimal_roi = {"0": 0.30, "1440": 0.15, "4320": 0.05}

    # 做空 entry/exit 占位（阶段1 未使用）
    entry_pricing = {"price_side": "same", "use_order_book": False}
    exit_pricing  = {"price_side": "same", "use_order_book": False}

    # ── 可调参数 ──────────────────────────────────────────
    hma_fast   = IntParameter(40, 80,  default=55, space="buy",  optimize=False)
    hma_slow   = IntParameter(55, 100, default=63, space="buy",  optimize=False)
    st_period  = IntParameter(7,  20,  default=10, space="buy",  optimize=False) # 回退到10
    st_mult    = DecimalParameter(2.0, 4.0, default=3.0, decimals=1, space="buy", optimize=False)
    rsi_low    = IntParameter(35, 55,  default=45, space="buy",  optimize=False)
    rsi_high   = IntParameter(55, 75,  default=65, space="buy",  optimize=False)

    # CVD 参数
    CVD_WINDOW         = 40    # 15m × 40 = 10 小时滚动窗口
    CVD_SLOPE_N        = 6     # CVD 斜率判断用 6 根（1.5h）
    CVD_DIVERGE_THRESH = 0.95  # 价格创高但 CVD < 历史峰值 95% → 顶背离

    # OI 参数
    OI_WINDOW    = 8           # OI 变化率窗口（8 × 15m = 2h）
    OI_RISE_MIN  = 0.005       # OI 2h 增幅 > 0.5% 才算有效多头建仓
    FUNDING_MAX  = 0.0005      # 资金费率 > 0.05% 视为多头过热

    # 置信度评分权重（三层架构，满分 100）
    CONF_MIN           = 80

    # Layer 1 — 趋势过滤（35 分）
    CONF_W_ST          = 20      # Supertrend 方向+持续性
    CONF_W_HMA         = 15      # HMA 对齐+斜率

    # Layer 2 — 资本确认（45 分）
    CONF_W_OI          = 15      # OI 建仓方向（简单布尔，比 Efficiency 更稳定）
    CONF_W_CVD         = 20      # CVD 持续性评分
    CONF_W_PRICE_LINK  = 5       # OI↑ + CVD↑ + Price↑ 联动加分

    # Layer 3 — 健康度（20 分）
    CONF_W_RSI         = 10      # RSI 位置
    CONF_W_FR          = 5       # Funding Rate
    CONF_W_VOL         = 5       # 成交量健康

    # ── 风险扣分参数 ──────────────────────────────────────
    DIVERGE_PENALTY    = 5       # 顶/底背离扣分

    # Partial Close 参数
    PARTIAL_CLOSE_PCT  = 0.85   # T1 到达时平仓 85%
    PARTIAL_T1_ATR_MUL = 2.0    # T1 = 入场价 + ATR14 × 2.0（3x 更差）

    # ── Risk Gates（借鉴 TradeCat fail-closed 设计）──────
    MAX_OPEN_TRADES     = 5       # 最大同时持仓数
    MAX_DAILY_LOSS_USDT = 50.0    # 日亏损上限（USDT），触发后当日不再开新仓
    ABNORMAL_MOVE_PCT   = 0.05    # P1: 5min 价格跳变 ≥5% → 熔断，禁止入场
    MAX_HOLDING_HOURS   = 999     # P2: 实际禁用（time_stop 胜率<20%，纯亏损）
    SYMBOL_BLOCK_LOSS   = -20.0   # P1: 币种累计亏损 ≤ -20 USDT → 自动加入黑名单

    # 日亏损追踪（class-level，按自然日重置，持久化到 JSON）
    _risk_state: dict = {"date": "", "realized_pnl": 0.0}
    _risk_lock = threading.Lock()
    _RISK_FILE  = Path(__file__).parent / ".hybrid_risk_state.json"

    # P1 币种黑名单（持久化）
    _blocked_symbols: set = set()
    _BLOCK_FILE = Path(__file__).parent / ".hybrid_blocked_symbols.json"

    # P2 Kill Switch 文件路径
    _KILL_SWITCH = Path(__file__).parent / ".hybrid_kill_switch"

    # 数据缓存（按交易对，TTL=10min）
    _cvd_cache: dict = {}
    _oi_cache:  dict = {}
    _fr_cache:  dict = {}
    _cvd_lock = threading.Lock()
    _oi_lock  = threading.Lock()
    _fr_lock  = threading.Lock()

    # ── 静态辅助方法 ──────────────────────────────────────

    @staticmethod
    def _wma(series: pd.Series, period: int) -> pd.Series:
        weights = np.arange(1, period + 1, dtype=float)
        return series.rolling(period).apply(
            lambda x: np.dot(x, weights) / weights.sum(), raw=True
        )

    @staticmethod
    def _hma(series: pd.Series, period: int) -> pd.Series:
        """Hull Moving Average：延迟约为 EMA 的 40-50%"""
        half_p = max(int(period / 2), 2)
        sqrt_p = max(int(np.sqrt(period)), 2)
        wma_half = HybridTraderV1._wma(series, half_p)
        wma_full = HybridTraderV1._wma(series, period)
        raw = 2 * wma_half - wma_full
        return HybridTraderV1._wma(raw, sqrt_p)

    @staticmethod
    def _supertrend(df: DataFrame, period: int, multiplier: float):
        """
        返回 (supertrend_line, direction)
        direction: +1 = 上升趋势（做多）, -1 = 下降趋势（做空）
        """
        high = df["high"]
        low  = df["low"]
        close = df["close"]

        # True Range → ATR
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low  - close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(period).mean()

        hl2 = (high + low) / 2
        upper = hl2 + multiplier * atr
        lower = hl2 - multiplier * atr

        # 最终上/下轨（随价格动态收紧）
        final_upper = upper.copy()
        final_lower = lower.copy()

        for i in range(1, len(df)):
            final_upper.iloc[i] = (
                upper.iloc[i]
                if upper.iloc[i] < final_upper.iloc[i - 1]
                   or close.iloc[i - 1] > final_upper.iloc[i - 1]
                else final_upper.iloc[i - 1]
            )
            final_lower.iloc[i] = (
                lower.iloc[i]
                if lower.iloc[i] > final_lower.iloc[i - 1]
                   or close.iloc[i - 1] < final_lower.iloc[i - 1]
                else final_lower.iloc[i - 1]
            )

        # 方向计算
        direction = pd.Series(1, index=df.index)
        st_line   = pd.Series(np.nan, index=df.index)

        for i in range(1, len(df)):
            if direction.iloc[i - 1] == 1:
                if close.iloc[i] < final_lower.iloc[i]:
                    direction.iloc[i] = -1
                else:
                    direction.iloc[i] = 1
            else:
                if close.iloc[i] > final_upper.iloc[i]:
                    direction.iloc[i] = 1
                else:
                    direction.iloc[i] = -1

            st_line.iloc[i] = (
                final_lower.iloc[i] if direction.iloc[i] == 1
                else final_upper.iloc[i]
            )

        return st_line, direction

    # ── CVD 数据获取与计算 ────────────────────────────────

    def _fetch_binance_taker(self, symbol: str, interval: str = "15m", limit: int = 600) -> pd.DataFrame:
        """
        调用 Binance USD-M Futures /fapi/v1/klines 获取含 taker 分量的 K 线。
        返回带 [timestamp, taker_buy_vol, volume, volume_delta] 的 DataFrame。
        limit=600 = 约 12.5 天，确保 CVD_WINDOW(40) 有足够历史预热。
        """
        url = "https://fapi.binance.com/fapi/v1/klines"
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        try:
            resp = requests.get(url, params=params, proxies=PROXIES, timeout=10)
            resp.raise_for_status()
            rows = resp.json()
        except Exception as e:
            return pd.DataFrame()

        records = []
        for row in rows:
            taker_buy = float(row[9])
            total_vol = float(row[5])
            records.append({
                "timestamp":      int(row[0]),
                "taker_buy_vol":  taker_buy,
                "volume":         total_vol,
                "volume_delta":   2.0 * taker_buy - total_vol,
            })
        df = pd.DataFrame(records)
        if df.empty:
            return df
        df["date"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        return df.set_index("date")

    @classmethod
    def _load_cvd_local(cls, symbol: str) -> pd.DataFrame | None:
        """
        从本地预取的 CVD feather 文件加载数据（用于回测）。
        文件路径: user_data/data/cvd/{SYM}-15m-cvd.feather
        不存在时返回 None，调用方回退到 API。
        """
        cvd_file = cls._DB_PATH.parent / "data" / "cvd" / f"{symbol}-15m-cvd.feather"
        try:
            if not cvd_file.exists():
                return None
            df = pd.read_feather(str(cvd_file))
            if df.empty or "cvd" not in df.columns:
                return None
            # feather 索引是 timestamp (ms int), 转为 datetime 索引
            ts_col = df.index if df.index.name == "timestamp" or df.index.dtype == "int64" else df["timestamp"]
            df["date"] = pd.to_datetime(ts_col, unit="ms", utc=True)
            df = df.set_index("date")
            return df
        except Exception:
            return None

    def _get_cvd_df(self, symbol: str) -> pd.DataFrame:
        """
        带缓存的 CVD 数据获取。
        缓存 TTL = 10 分钟（15m 策略不需要更频繁刷新）。
        回测时 Binance API 不可用，返回空 DataFrame（CVD 条件会被跳过）。
        """
        ttl = 600
        with self._cvd_lock:
            cached = self._cvd_cache.get(symbol)
            if cached and (time.time() - cached["ts"] < ttl):
                return cached["df"]

        raw = self._fetch_binance_taker(symbol)
        if raw.empty:
            return raw

        n = self.CVD_WINDOW
        raw["cvd"]          = raw["volume_delta"].rolling(n).sum()
        raw["cvd_prev"]     = raw["cvd"].shift(n)
        raw["cvd_change_pct"] = (
            (raw["cvd"] - raw["cvd_prev"]) / raw["cvd_prev"].abs() * 100
        )
        # CVD 斜率（过去 CVD_SLOPE_N 根）
        raw["cvd_rising"]   = raw["cvd"] > raw["cvd"].shift(self.CVD_SLOPE_N)

        # 顶背离：价格在 10h 高点附近但 CVD 未创新高
        raw["price_10h_high"] = raw.index.map(
            lambda _: np.nan  # 占位，merge 后用 OHLCV 计算
        )
        # CVD 10h 峰值
        raw["cvd_10h_peak"] = raw["cvd"].rolling(n).max()

        with self._cvd_lock:
            self._cvd_cache[symbol] = {"ts": time.time(), "df": raw}
        return raw

    @staticmethod
    def _merge_cvd(dataframe: DataFrame, cvd_df: pd.DataFrame) -> DataFrame:
        """
        将 CVD DataFrame 按时间戳 merge 到策略 dataframe。
        使用 merge_asof（向后匹配）确保无未来函数。
        """
        if cvd_df.empty:
            dataframe["cvd"]         = np.nan
            dataframe["cvd_rising"]  = True   # 无数据时不过滤（宽松 fallback）
            dataframe["top_diverge"] = False
            dataframe["bot_diverge"] = False
            return dataframe

        cvd_reset = cvd_df[["cvd", "cvd_rising", "cvd_10h_peak"]].reset_index()
        cvd_reset.columns = ["date_cvd", "cvd", "cvd_rising", "cvd_10h_peak"]
        # 统一去除时区（merge_asof 要求两侧类型完全一致）
        cvd_reset["date_cvd"] = pd.to_datetime(cvd_reset["date_cvd"]).dt.tz_localize(None)

        df_copy = dataframe.copy()
        # 策略 dataframe 的 date 可能带 UTC，统一转 tz-naive
        date_col = df_copy["date"]
        if hasattr(date_col.dtype, "tz") and date_col.dtype.tz is not None:
            df_copy["date_key"] = date_col.dt.tz_localize(None)
        else:
            df_copy["date_key"] = date_col

        merged = pd.merge_asof(
            df_copy.sort_values("date_key"),
            cvd_reset.sort_values("date_cvd"),
            left_on="date_key",
            right_on="date_cvd",
            direction="backward",
        )
        merged = merged.set_index(dataframe.index)

        # 顶/底背离检测
        # 顶背离：价格涨到 40-bar 高点 98.5% 以上，但 CVD < 峰值 95%
        # 底背离：价格跌到 40-bar 低点 101% 以下，但 CVD > 低谷 / 95%
        merged["price_10h_high"] = merged["high"].rolling(40).max()
        merged["price_10h_low"] = merged["low"].rolling(40).min()
        merged["cvd_10h_trough"] = merged["cvd"].rolling(40).min()
        merged["top_diverge"] = (
            (merged["high"] >= merged["price_10h_high"] * 0.985) &
            (merged["cvd"]  <  merged["cvd_10h_peak"]  * HybridTraderV1.CVD_DIVERGE_THRESH)
        )
        merged["bot_diverge"] = (
            (merged["low"] <= merged["price_10h_low"] * 1.01) &
            (merged["cvd"] >  merged["cvd_10h_trough"] / HybridTraderV1.CVD_DIVERGE_THRESH)
        )

        for col in ["cvd", "cvd_rising", "top_diverge", "bot_diverge"]:
            dataframe[col] = merged[col].values

        return dataframe

    # ── OI + Funding Rate 数据获取 ───────────────────────

    def _fetch_oi(self, symbol: str, limit: int = 100) -> pd.DataFrame:
        """
        Binance /futures/data/openInterestHist — 历史 OI，15m 周期。
        返回带 [date, oi, oi_change_pct, oi_rising] 的 DataFrame。
        """
        url = "https://fapi.binance.com/futures/data/openInterestHist"
        params = {"symbol": symbol, "period": "15m", "limit": limit}
        try:
            resp = requests.get(url, params=params, proxies=PROXIES, timeout=10)
            resp.raise_for_status()
            rows = resp.json()
        except Exception:
            return pd.DataFrame()

        records = [
            {"timestamp": int(r["timestamp"]), "oi": float(r["sumOpenInterest"])}
            for r in rows
        ]
        df = pd.DataFrame(records)
        if df.empty:
            return df
        df["date"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.set_index("date").sort_index()

        # OI 变化率（OI_WINDOW 根）+ 方向判断
        n = self.OI_WINDOW
        df["oi_change_pct"] = (df["oi"] - df["oi"].shift(n)) / df["oi"].shift(n)
        df["oi_rising"] = df["oi_change_pct"] > self.OI_RISE_MIN
        return df

    def _get_oi_df(self, symbol: str) -> pd.DataFrame:
        ttl = 600
        with self._oi_lock:
            cached = self._oi_cache.get(symbol)
            if cached and (time.time() - cached["ts"] < ttl):
                return cached["df"]
        df = self._fetch_oi(symbol)
        if not df.empty:
            with self._oi_lock:
                self._oi_cache[symbol] = {"ts": time.time(), "df": df}
        return df

    def _fetch_funding(self, symbol: str, limit: int = 50) -> pd.DataFrame:
        """
        Binance /fapi/v1/fundingRate — 历史资金费率（每 8h 结算一次）。
        返回带 [date, funding_rate] 的 DataFrame。
        """
        url = "https://fapi.binance.com/fapi/v1/fundingRate"
        params = {"symbol": symbol, "limit": limit}
        try:
            resp = requests.get(url, params=params, proxies=PROXIES, timeout=10)
            resp.raise_for_status()
            rows = resp.json()
        except Exception:
            return pd.DataFrame()

        records = [
            {"timestamp": int(r["fundingTime"]), "funding_rate": float(r["fundingRate"])}
            for r in rows
        ]
        df = pd.DataFrame(records)
        if df.empty:
            return df
        df["date"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        return df.set_index("date").sort_index()

    def _get_funding_df(self, symbol: str) -> pd.DataFrame:
        ttl = 600
        with self._fr_lock:
            cached = self._fr_cache.get(symbol)
            if cached and (time.time() - cached["ts"] < ttl):
                return cached["df"]
        df = self._fetch_funding(symbol)
        if not df.empty:
            with self._fr_lock:
                self._fr_cache[symbol] = {"ts": time.time(), "df": df}
        return df

    @staticmethod
    def _merge_oi_funding(
        dataframe: DataFrame,
        oi_df: pd.DataFrame,
        fr_df: pd.DataFrame,
    ) -> DataFrame:
        """
        将 OI 和 Funding Rate 按时间戳 merge 到策略 dataframe。
        两者均使用 merge_asof 向后匹配，确保无未来函数。
        API 不可用时 fallback：oi_rising=True, funding_ok=True（不过滤）。
        """
        # 策略 date 列统一转 tz-naive
        date_col = dataframe["date"]
        if hasattr(date_col.dtype, "tz") and date_col.dtype.tz is not None:
            date_key = date_col.dt.tz_localize(None)
        else:
            date_key = date_col

        df_work = dataframe.copy()
        df_work["_date_key"] = date_key

        # ── OI merge ──
        if not oi_df.empty:
            oi_reset = oi_df[["oi", "oi_change_pct", "oi_rising"]].reset_index()
            oi_reset.columns = ["date_oi", "oi", "oi_change_pct", "oi_rising"]
            oi_reset["date_oi"] = pd.to_datetime(oi_reset["date_oi"]).dt.tz_localize(None)
            merged_oi = pd.merge_asof(
                df_work.sort_values("_date_key"),
                oi_reset.sort_values("date_oi"),
                left_on="_date_key",
                right_on="date_oi",
                direction="backward",
            )
            dataframe["oi"] = merged_oi["oi"].values
            dataframe["oi_change_pct"] = merged_oi["oi_change_pct"].values
            dataframe["oi_rising"] = merged_oi["oi_rising"].values
        else:
            dataframe["oi"] = 0.0
            dataframe["oi_change_pct"] = 0.0
            dataframe["oi_rising"] = True  # fallback

        # ── Funding Rate merge ──
        if not fr_df.empty:
            fr_reset = fr_df[["funding_rate"]].reset_index()
            fr_reset.columns = ["date_fr", "funding_rate"]
            fr_reset["date_fr"] = pd.to_datetime(fr_reset["date_fr"]).dt.tz_localize(None)
            merged_fr = pd.merge_asof(
                df_work.sort_values("_date_key"),
                fr_reset.sort_values("date_fr"),
                left_on="_date_key",
                right_on="date_fr",
                direction="backward",
            )
            dataframe["funding_rate"] = merged_fr["funding_rate"].values
            # 多头过热判断：资金费率超过阈值时不入场
            dataframe["funding_ok"] = (
                dataframe["funding_rate"].fillna(0) <= HybridTraderV1.FUNDING_MAX
            )
        else:
            dataframe["funding_rate"] = 0.0
            dataframe["funding_ok"]   = True  # fallback

        return dataframe

    # ── 指标计算 ──────────────────────────────────────────

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # HMA 趋势双线
        dataframe["hma_fast"] = self._hma(dataframe["close"], self.hma_fast.value)
        dataframe["hma_slow"] = self._hma(dataframe["close"], self.hma_slow.value)

        # Supertrend 趋势方向 + 动态止损线
        dataframe["st_line"], dataframe["st_dir"] = self._supertrend(
            dataframe, self.st_period.value, self.st_mult.value
        )


        # RSI(14)
        delta = dataframe["close"].diff()
        gain  = delta.clip(lower=0).ewm(com=13, adjust=False).mean()
        loss  = (-delta.clip(upper=0)).ewm(com=13, adjust=False).mean()
        rs    = gain / loss.replace(0, np.nan)
        dataframe["rsi"] = 100 - (100 / (1 + rs))

        # SMA200 — 大趋势过滤（15m×200=50h≈2天），只在大趋势方向上入场
        dataframe["sma200"] = dataframe["close"].rolling(200).mean()

        # ATR(14) — 止损计算辅助
        tr = pd.concat([
            dataframe["high"] - dataframe["low"],
            (dataframe["high"] - dataframe["close"].shift(1)).abs(),
            (dataframe["low"]  - dataframe["close"].shift(1)).abs(),
        ], axis=1).max(axis=1)
        dataframe["atr14"] = tr.rolling(14).mean()

        # HMA 叉信号
        dataframe["hma_cross_up"]   = (
            (dataframe["hma_fast"] > dataframe["hma_slow"]) &
            (dataframe["hma_fast"].shift(1) <= dataframe["hma_slow"].shift(1))
        )
        dataframe["hma_cross_down"] = (
            (dataframe["hma_fast"] < dataframe["hma_slow"]) &
            (dataframe["hma_fast"].shift(1) >= dataframe["hma_slow"].shift(1))
        )

        # 将交易对格式 BTC/USDT:USDT → BTCUSDT（Binance 合约格式）
        pair = metadata.get("pair", "")
        binance_symbol = pair.split("/")[0] + pair.split("/")[1].split(":")[0]

        # ── CVD（阶段2）─ 优先读本地预取文件，回退 API ────
        cvd_df = self._load_cvd_local(binance_symbol)
        if cvd_df is not None:
            dataframe = self._merge_cvd(dataframe, cvd_df)
        else:
            cvd_df = self._get_cvd_df(binance_symbol)
            dataframe = self._merge_cvd(dataframe, cvd_df)

        # ── OI + Funding Rate（阶段3）──────────────────────
        oi_df = self._get_oi_df(binance_symbol)
        fr_df = self._get_funding_df(binance_symbol)
        dataframe = self._merge_oi_funding(dataframe, oi_df, fr_df)

        # ── 三层置信度评分 ──────────────────────────────────
        # Layer 1: 趋势过滤 (35 max) — 判断方向是否正确
        # Layer 2: 资本确认 (45 max) — 判断资金是否支撑趋势
        # Layer 3: 健康度 (20 max) — 风险扣分制
        # 总分 ≥ CONF_MIN 才允许入场

        hma_fast_rising = dataframe["hma_fast"] > dataframe["hma_fast"].shift(2)

        score = pd.Series(0.0, index=dataframe.index)

        # ═══ Layer 1: 趋势过滤 (35) ═══
        # Supertrend：持续 3 根以上满分，仅当前根半分（对称多空）
        st_dir_shift2 = dataframe["st_dir"].shift(2)
        score += np.where(
            st_dir_shift2 == 1, self.CONF_W_ST,
            np.where(dataframe["st_dir"] == 1, self.CONF_W_ST * 0.5,
            np.where(st_dir_shift2 == -1, self.CONF_W_ST,
                     np.where(dataframe["st_dir"] == -1, self.CONF_W_ST * 0.5, 0)))
        )
        # HMA：趋势方向对齐+斜率满分，仅对齐半分（对称多空）
        hma_aligned_long = dataframe["hma_fast"] > dataframe["hma_slow"]
        hma_aligned_short = dataframe["hma_fast"] < dataframe["hma_slow"]
        hma_fast_falling = dataframe["hma_fast"] < dataframe["hma_fast"].shift(2)
        score += np.where(
            hma_aligned_long & hma_fast_rising, self.CONF_W_HMA,
            np.where(hma_aligned_long, self.CONF_W_HMA * 0.5,
            np.where(hma_aligned_short & hma_fast_falling, self.CONF_W_HMA,
                     np.where(hma_aligned_short, self.CONF_W_HMA * 0.5, 0)))
        )

        # ═══ Layer 2: 资本确认 (45) ═══
        # OI 简单方向（15m 级别 Efficiency 噪声太大，回退布尔判断）
        score += np.where(dataframe["oi_rising"].fillna(True).astype(bool),
                          self.CONF_W_OI, 0)

        # CVD 持续性评分 — 过去 CVD_SLOPE_N 根中有几根正向
        cvd_val = dataframe["cvd"].ffill()
        cvd_positive_bars = (cvd_val > cvd_val.shift(1)).rolling(self.CVD_SLOPE_N).sum()
        cvd_negative_bars = (cvd_val < cvd_val.shift(1)).rolling(self.CVD_SLOPE_N).sum()
        # ≥4/6 根正向 → 满分；2-3 → 半分；<2 → 0
        score += np.where(
            cvd_positive_bars >= self.CVD_SLOPE_N * 0.66,
            self.CONF_W_CVD,
            np.where(cvd_positive_bars >= self.CVD_SLOPE_N * 0.33,
                     self.CONF_W_CVD * 0.5, 0)
        )

        # Price 联动加分：三向确认（多空对称）
        price_change_2h = (
            dataframe["close"] / dataframe["close"].shift(self.OI_WINDOW) - 1
        ).fillna(0)
        price_up = price_change_2h > 0.005
        price_down = price_change_2h < -0.005
        oi_up = dataframe["oi_change_pct"].fillna(0) > 0
        oi_down = dataframe["oi_change_pct"].fillna(0) < 0
        cvd_sustained_long = cvd_positive_bars >= self.CVD_SLOPE_N * 0.5
        cvd_sustained_short = cvd_negative_bars >= self.CVD_SLOPE_N * 0.5
        score += np.where(
            (price_up & oi_up & cvd_sustained_long) |
            (price_down & oi_down & cvd_sustained_short),
            self.CONF_W_PRICE_LINK, 0
        )

        # ═══ Layer 3: 健康度 (20) ═══
        # RSI：40-60 最优满分，32-68 半分，极端值 0
        score += np.where(
            (dataframe["rsi"] >= 40) & (dataframe["rsi"] <= 60),
            self.CONF_W_RSI,
            np.where(
                (dataframe["rsi"] >= 32) & (dataframe["rsi"] <= 68),
                self.CONF_W_RSI * 0.5, 0
            )
        )

        # Funding Rate：< 0.05% 满分，0.05-0.1% 半分，> 0.1% 零分
        fr_abs = dataframe["funding_rate"].fillna(0).abs()
        score += np.where(
            fr_abs <= self.FUNDING_MAX,
            self.CONF_W_FR,
            np.where(fr_abs <= self.FUNDING_MAX * 2,
                     self.CONF_W_FR * 0.5, 0)
        )

        # 成交量健康：高于 SMA20 成交量满分，高于一半半分，低于零分
        vol_sma20 = dataframe["volume"].rolling(20).mean()
        volume_ratio = dataframe["volume"] / vol_sma20.replace(0, np.nan)
        score += np.where(
            volume_ratio >= 1.0,
            self.CONF_W_VOL,
            np.where(volume_ratio >= 0.5,
                     self.CONF_W_VOL * 0.5, 0)
        )

        # 顶/底背离扣分
        score -= np.where(dataframe["top_diverge"].fillna(False), self.DIVERGE_PENALTY, 0)
        score -= np.where(dataframe["bot_diverge"].fillna(False), self.DIVERGE_PENALTY, 0)

        dataframe["confidence"] = score.clip(0, 100).astype(float)

        # t1_atr 供 custom_exit 在开仓时取入场 bar 的 ATR，固化 T1
        dataframe["t1_atr"] = dataframe["atr14"]

        # ── Market State Engine watchlist 过滤（阶段5）────────
        # 读取 market_state_engine 写入的 watchlist 表（需连续3次≥WATCH才进入）。
        # 若 DB 不存在或 market_state_engine 未运行，默认放行（不阻断回测和初始 dry run）。
        mse_in_watchlist, mse_rank, mse_score = self._query_watchlist(binance_symbol)
        dataframe["mse_ok"]   = mse_in_watchlist
        dataframe["mse_rank"] = mse_rank
        dataframe["mse_score"] = mse_score

        return dataframe

    # ── Market State Engine DB 查询 ──────────────────────
    _DB_PATH = Path(__file__).parent.parent / "market.db"

    @classmethod
    def _query_watchlist(cls, binance_symbol: str) -> tuple[bool, int, float]:
        """
        查询 MSE watchlist 表（需连续3次≥WATCH才进入）。
        Returns (in_watchlist: bool, rank: int, score: float).
        DB 不存在或引擎未运行时返回 (True, 0, 0)=放行。
        设置 HYBRID_NO_MSE=1 环境变量跳过 MSE 过滤（用于对照回测）。
        """
        import os
        if os.environ.get("HYBRID_NO_MSE") == "1":
            return True, 0, 0.0
        try:
            if not cls._DB_PATH.exists():
                return True, 0, 0.0
            with sqlite3.connect(str(cls._DB_PATH), timeout=2) as conn:
                row = conn.execute(
                    "SELECT score FROM watchlist WHERE symbol=? LIMIT 1",
                    (binance_symbol,),
                ).fetchone()
            if row:
                return True, 0, float(row[0])   # watchlist 无 rank 概念
            return False, 0, 0.0
        except Exception:
            return True, 0, 0.0   # fallback: don't block when engine not running

    # ── Risk State（日亏损追踪，借鉴 TradeCat fail-closed）──

    @classmethod
    def _load_risk_state(cls):
        """加载持久化的风险状态，跨日期自动重置。"""
        today = str(date.today())
        try:
            if cls._RISK_FILE.exists():
                state = json.loads(cls._RISK_FILE.read_text())
                if state.get("date") != today:
                    state = {"date": today, "realized_pnl": 0.0}
            else:
                state = {"date": today, "realized_pnl": 0.0}
        except Exception:
            state = {"date": today, "realized_pnl": 0.0}
        with cls._risk_lock:
            cls._risk_state = state

    @classmethod
    def _save_risk_state(cls):
        """持久化风险状态。"""
        with cls._risk_lock:
            cls._RISK_FILE.write_text(json.dumps(cls._risk_state))

    @classmethod
    def _check_daily_loss_limit(cls) -> bool:
        """
        检查是否触发日亏损上限。
        Returns True = 已触发（禁止开仓），False = 未触发。
        """
        cls._load_risk_state()
        with cls._risk_lock:
            return cls._risk_state["realized_pnl"] <= -cls.MAX_DAILY_LOSS_USDT

    @classmethod
    def _record_trade_pnl(cls, profit_usdt: float, symbol: str = ""):
        """记录一笔已平仓交易的 PnL 到日累计，同时更新币种亏损追踪。"""
        cls._load_risk_state()
        with cls._risk_lock:
            cls._risk_state["realized_pnl"] += profit_usdt
        cls._save_risk_state()
        # P1: 币种亏损追踪 → 自动拉黑
        if symbol and profit_usdt < 0:
            cls._track_symbol_loss(symbol, profit_usdt)

    # ── P1: 币种亏损自过滤 ───────────────────────────────

    @classmethod
    def _load_blocked_symbols(cls):
        """加载币种黑名单。"""
        try:
            if cls._BLOCK_FILE.exists():
                cls._blocked_symbols = set(json.loads(cls._BLOCK_FILE.read_text()))
        except Exception:
            cls._blocked_symbols = set()

    @classmethod
    def _save_blocked_symbols(cls):
        """持久化币种黑名单。"""
        cls._BLOCK_FILE.write_text(json.dumps(list(cls._blocked_symbols)))

    @classmethod
    def _track_symbol_loss(cls, symbol: str, loss_usdt: float):
        """
        追踪币种亏损。累计亏损超过阈值 → 自动加入黑名单。
        """
        cls._load_blocked_symbols()
        key = f"{symbol}_loss"
        state_file = cls._RISK_FILE  # 复用同一个状态文件
        try:
            state = json.loads(state_file.read_text()) if state_file.exists() else {}
        except Exception:
            state = {}
        total = state.get(key, 0.0) + loss_usdt
        state[key] = total
        state_file.write_text(json.dumps(state))
        if total <= cls.SYMBOL_BLOCK_LOSS:
            cls._blocked_symbols.add(symbol)
            cls._save_blocked_symbols()
            import logging
            logging.getLogger(__name__).warning(
                f"SYMBOL BLOCKED: {symbol} cumulative loss={total:.1f} USDT"
            )

    # ── P2: Kill Switch ──────────────────────────────────

    @classmethod
    def _is_kill_switch_active(cls) -> bool:
        """检查 kill switch 文件是否存在。"""
        return cls._KILL_SWITCH.exists()

    # ── Risk Gate：开仓前检查 ────────────────────────────

    # 已记录 PnL 的交易 ID 集合（避免重复计算）
    _pnl_recorded: set[int] = set()

    def confirm_trade_entry(
        self,
        pair: str,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        current_time,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> bool:
        """
        TradeCat-style deterministic risk gates（按优先级排序）：
        Gate 0: Kill Switch（P2）
        Gate 1: 币种黑名单（P1）
        Gate 2: 异常波动熔断（P1）
        Gate 3: 日亏损上限（P0）
        Gate 4: 最大同时持仓数（P0）
        任一触发 → 拒绝开仓。
        """
        # Auto-record PnL from recently closed trades (dry-run only; backtest skips)
        if hasattr(self, "trades"):
            for t in self.trades:
                tid = getattr(t, "id", id(t))
                if not t.is_open and tid not in self._pnl_recorded:
                    pnl = t.realized_profit if t.realized_profit else 0.0
                    self._record_trade_pnl(pnl, t.pair)
                    self._pnl_recorded.add(tid)

        # Gate 0: Kill Switch（P2 — 最高优先级）
        if self._is_kill_switch_active():
            self.log_once(
                f"RISK GATE: kill switch active ({self._KILL_SWITCH}), blocking all entries",
                log_level="critical",
            )
            return False

        # Gate 1: 币种黑名单（P1）
        self._load_blocked_symbols()
        # 提取裸币种名（不含 /USDT:USDT 后缀）
        base = pair.split("/")[0]
        if base in self._blocked_symbols:
            self.log_once(
                f"RISK GATE: {base} is blocked (cumulative loss), rejecting {pair}",
                log_level="warning",
            )
            return False

        # Gate 2: 异常波动熔断（P1）
        if self._abnormal_move_check(pair, rate):
            self.log_once(
                f"RISK GATE: abnormal price move detected for {pair}, blocking entry",
                log_level="warning",
            )
            return False

        # Gate 3: 日亏损上限
        if self._check_daily_loss_limit():
            self._load_risk_state()
            pnl = self._risk_state["realized_pnl"]
            self.log_once(
                f"RISK GATE: daily loss limit hit (PnL={pnl:.1f} <= -{self.MAX_DAILY_LOSS_USDT}), "
                f"blocking new entries for {pair}",
                log_level="warning",
            )
            return False

        # Gate 4: 最大同时持仓数（dry-run 时生效，回测由 max_open_trades 控制）
        if hasattr(self, "trades"):
            open_count = len([t for t in self.trades if t.is_open])
            if open_count >= self.MAX_OPEN_TRADES:
                self.log_once(
                    f"RISK GATE: max open trades ({self.MAX_OPEN_TRADES}) reached, "
                    f"blocking {pair}",
                    log_level="warning",
                )
                return False

        return True

    # ── P1: 异常波动检查（实例方法，可访问 dataprovider）───

    def _abnormal_move_check(self, pair: str, current_rate: float) -> bool:
        """
        P1: 检查最近是否存在异常价格跳变。
        用 5m K 线：若最新 bar 涨跌幅 ≥ ABNORMAL_MOVE_PCT → 熔断。
        回测时 5m 数据可能不可用，fallback=False（不阻断）。
        """
        try:
            dataframe, _ = self.dp.get_analyzed_dataframe(pair, "5m")
            if dataframe is None or dataframe.empty:
                return False
            recent = dataframe.tail(3)
            if recent.empty:
                return False
            for _, bar in recent.iterrows():
                move = abs(bar["close"] - bar["open"]) / bar["open"] if bar["open"] > 0 else 0
                if move >= self.ABNORMAL_MOVE_PCT:
                    return True
            return False
        except Exception:
            return False  # fallback: don't block when data unavailable

    # ── 入场 ─────────────────────────────────────────────

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        做多条件 — 三层评分架构：
        Layer 1 硬条件：ST 方向向上 + HMA 对齐（方向错了就不入场）
        Layer 2+3 由置信度评分统一衡量
        MSE watchlist 作为最外层门控
        """
        # Layer 1 硬条件：趋势方向必须正确
        trend_ok_long = (
            (dataframe["st_dir"] == 1) &
            (dataframe["hma_fast"] > dataframe["hma_slow"])
        )

        # MSE 门控
        mse_ok = dataframe["mse_ok"].fillna(True).astype(bool)

        conditions_long = [
            trend_ok_long,
            dataframe["confidence"] >= self.CONF_MIN,
            mse_ok,
            dataframe["volume"] > 0,
        ]
        dataframe.loc[
            reduce(lambda a, b: a & b, conditions_long),
            "enter_long"
        ] = 1

        # ── 做空条件（对称逻辑）───
        trend_ok_short = (
            (dataframe["st_dir"] == -1) &
            (dataframe["hma_fast"] < dataframe["hma_slow"])
        )
        conditions_short = [
            trend_ok_short,
            dataframe["confidence"] >= self.CONF_MIN,
            mse_ok,
            dataframe["volume"] > 0,
        ]
        dataframe.loc[
            reduce(lambda a, b: a & b, conditions_short),
            "enter_short"
        ] = 1

        return dataframe

    # ── 出场 ─────────────────────────────────────────────

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        平多条件（任一满足）：
        1. Supertrend 翻转为 -1
        2. HMA 死叉 + RSI > 65（高位死叉）

        平空条件（任一满足，做多镜像）：
        1. Supertrend 翻转为 +1
        2. HMA 金叉 + RSI < 35（低位金叉）
        """
        exit_long = (
            (dataframe["st_dir"] == -1) |
            (dataframe["hma_cross_down"] & (dataframe["rsi"] > 65))
        )
        dataframe.loc[exit_long & (dataframe["volume"] > 0), "exit_long"] = 1

        exit_short = (
            (dataframe["st_dir"] == 1) |
            (dataframe["hma_cross_up"] & (dataframe["rsi"] < 35))
        )
        dataframe.loc[exit_short & (dataframe["volume"] > 0), "exit_short"] = 1

        return dataframe

    # ── 自定义止损（Supertrend 线）────────────────────────

    def custom_stoploss(
        self,
        pair: str,
        trade,
        current_time,
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs,
    ) -> float:
        """
        动态止损 = Supertrend 线位置（多空通用）
        多头：止损在 Supertrend 下轨下方；空头：止损在上轨上方。
        Kill Switch 激活时 → 立即以 -0.1% 止损强制平仓。
        """
        # P2: Kill Switch → 强制退出
        if self._is_kill_switch_active():
            return -0.001
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe.empty:
            return self.stoploss

        last = dataframe.iloc[-1]
        st_line = last.get("st_line", np.nan)

        if pd.isna(st_line) or st_line <= 0:
            return self.stoploss

        stop_ratio = (st_line - current_rate) / current_rate
        # 多头：st_line 在下轨（< current_rate），stop_ratio 为负 ✓
        # 空头：st_line 在上轨（> current_rate），stop_ratio 为正 → 取负
        if stop_ratio > 0:
            stop_ratio = -abs(stop_ratio)
        return max(stop_ratio, self.stoploss)

    # ── Partial Close（T1 到达平 55%）────────────────────

    def custom_exit(
        self,
        pair: str,
        trade,
        current_time,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ):
        """
        T1 目标位到达时平仓 55%，剩余 45% 继续持有直到 Supertrend 翻转。
        T1 = 入场时 close + ATR14 × 2.0（在 populate_indicators 里预计算）
        Kill Switch 激活时 → 立即全平。
        """
        # P2: Kill Switch → 强制退出
        if self._is_kill_switch_active():
            return "kill_switch"

        # 已触发过直接跳过
        if trade.get_custom_data("t1_hit"):
            return None

        # 首次调用时在入场 bar 固化 T1 目标价（只算一次，不随行情漂移）
        if trade.get_custom_data("t1_target") is None:
            dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            if dataframe.empty:
                return None
            open_bar_mask = dataframe["date"] <= pd.Timestamp(trade.open_date_utc)
            if not open_bar_mask.any():
                return None
            open_bar = dataframe.loc[open_bar_mask].iloc[-1]
            atr_dist = open_bar["t1_atr"] * self.PARTIAL_T1_ATR_MUL
            if trade.is_short:
                trade.set_custom_data("t1_target", float(open_bar["close"] - atr_dist))
            else:
                trade.set_custom_data("t1_target", float(open_bar["close"] + atr_dist))

        t1 = trade.get_custom_data("t1_target")
        if (not trade.is_short and current_rate >= t1) or (trade.is_short and current_rate <= t1):
            return "t1_partial_close"

        # P2: 时间止损 — 持仓超过 MAX_HOLDING_HOURS 强制平仓
        from datetime import timezone
        hold_hours = (current_time.replace(tzinfo=timezone.utc) - trade.open_date_utc).total_seconds() / 3600
        if hold_hours >= self.MAX_HOLDING_HOURS:
            return "time_stop"

        return None

    def adjust_trade_position(
        self,
        trade,
        current_time,
        current_rate: float,
        current_profit: float,
        min_stake: float,
        max_stake: float,
        current_entry_rate: float,
        current_exit_rate: float,
        current_entry_profit: float,
        current_exit_profit: float,
        **kwargs,
    ):
        """
        T1 触发后减仓 55%，只执行一次。
        返回负数 stake = 减少仓位；None = 不操作。
        """
        # 只在 T1 触发且尚未执行减仓时操作
        if trade.get_custom_data("t1_hit"):
            return None

        # 检查是否已触发 T1 部分平仓
        triggered = any(
            getattr(o, "ft_order_tag", "") == "t1_partial_close"
            for o in (trade.orders or [])
        )
        if not triggered:
            return None

        # 标记已触发，避免重复
        trade.set_custom_data("t1_hit", True)

        # 减仓金额 = 当前仓位价值 × 55%
        reduce_stake = trade.stake_amount * self.PARTIAL_CLOSE_PCT
        reduce_stake = max(min(reduce_stake, max_stake), min_stake)
        return -reduce_stake
