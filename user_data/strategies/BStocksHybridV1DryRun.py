#!/usr/bin/env python3
"""Generate BStocksHybridV1DryRun with bot_start local-data injection."""
import logging
import requests
from datetime import datetime, time, timezone
from typing import Optional

from freqtrade.data.history import load_pair_history
from freqtrade.enums import CandleType
from freqtrade.util.datetime_helpers import dt_ts
from freqtrade.persistence import Trade

from BStocksHybridV1 import BStocksHybridV1

logger = logging.getLogger(__name__)

# 开仓前真实验证（TASK 124，2026-08-20）：币安 bStock 突破信号须经真实美股确认。
# 背景：MUB 2026-08-17 币安虚涨 +10%（真实纹丝不动）制造假突破 → 8/18 开仓即套。
# 反事实：真实 MUB 8/17 close 105.56 < 前20日高 106.40 → REJECT；真实 SNDK +8.9% → PASS。
REAL_TICKER_MAP = {"MUB": "MUB"}  # 真实代码本身含 B，strip 会错
REAL_VERIFY_TIMEOUT = 8  # 秒，超时降级放行
REAL_VERIFY_PROXY = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}
REAL_VERIFY_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# NYSE/Nasdaq 全天休市日（UTC 日期，含周末顺延观察日），需逐年维护
NYSE_HOLIDAYS = {
    # 2024
    "2024-01-01", "2024-01-15", "2024-02-19", "2024-03-29", "2024-05-27",
    "2024-06-19", "2024-07-04", "2024-09-02", "2024-11-28", "2024-12-25",
    # 2025
    "2025-01-01", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26",
    "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25",
    # 2026
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    # 2027
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
    "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
}

MARKET_OPEN_UTC = time(13, 30)
MARKET_CLOSE_UTC = time(20, 0)


class BStocksHybridV1DryRun(BStocksHybridV1):

    def bot_start(self, **kwargs) -> None:
        """
        预加载本地 nasdaq feather 数据到 exchange._klines 缓存。
        dry-run 模式 freqtrade 默认只从交易所 fetch_ohlcv 拉数据，不读本地文件；
        而 Binance bStocks 上架历史短（部分 pair 仅 10 根 K 线 < Donchian 20 窗口），
        导致 don_upper 无法计算、永远无信号。本钩子把本地完整历史注入缓存，
        后续 refresh 时 freqtrade 自动增量合并（concat([old, ohlcv_df])）。

        设计约束：
        - 用官方钩子 bot_start（ft_bot_start 内部调用），不修改 freqtrade 源码
        - 报错时被 strategy_safe_wrapper 吞掉，自动降级为纯交易所数据
        - 只对交易所数据 < 20 根的 pair 注入，已有足够历史的不覆盖
        """
        try:
            exchange = self.dp._exchange
            candle_type = self.config.get("candle_type_def", CandleType.SPOT)
            pairs = self.config.get("exchange", {}).get("pair_whitelist", [])
            datadir = self.config.get("datadir")
            for pair in pairs:
                key = (pair, self.timeframe, candle_type)
                existing = exchange._klines.get(key)
                if existing is not None and len(existing) >= 20:
                    continue
                df = load_pair_history(
                    datadir=datadir,
                    timeframe=self.timeframe,
                    pair=pair,
                    data_format=self.config.get("dataformat_ohlcv", "feather"),
                )
                if df is None or df.empty:
                    logger.warning(f"[bot_start] 本地无数据: {pair}")
                    continue
                exchange._klines[key] = df.reset_index(drop=True)
                # 关键：把最后刷新时间设为预加载数据的最后 K 线时间（而非 now）。
                # 设 now → _now_is_time_to_refresh 判定 plr(now)+1d > now → 永不 refresh，
                # bot 数据永远停在 feather 快照（2026-08-16 实证：停 8/6 超 10 天）。
                # 设最后 K 线时间 → 缓存保留（> min_ts 995天前，不触发 Time jump evict）
                # 且 refresh 立即触发（plr < now-1d）。
                last_ts = df["date"].iloc[-1]
                exchange._pairs_last_refresh_time[key] = dt_ts(last_ts)
                logger.info(f"[bot_start] 预加载本地历史: {pair} {len(df)} rows, last={last_ts}")
        except Exception as e:
            logger.warning(f"[bot_start] 预加载失败，降级为纯交易所数据: {e}")

    def _is_market_open(self, current_time: datetime) -> bool:
        if current_time.weekday() >= 5:  # 5=Sat, 6=Sun
            return False
        if current_time.strftime("%Y-%m-%d") in NYSE_HOLIDAYS:
            return False
        t = current_time.time()
        return MARKET_OPEN_UTC <= t < MARKET_CLOSE_UTC

    def _real_ticker(self, pair: str) -> str:
        """币安 bStock pair → 真实美股代码（Yahoo）。"""
        sym = pair.split("/")[0]
        if sym in REAL_TICKER_MAP:
            return REAL_TICKER_MAP[sym]
        return sym[:-1] if sym.endswith("B") else sym

    def _real_verify(self, pair: str, signal_date: str):
        """
        真实美股突破验证（TASK 124 级别 1）。
        返回 (passed, evidence)。任何异常 → (True, '降级放行: 原因')。
        """
        ticker = self._real_ticker(pair)
        try:
            end = int(datetime.now(timezone.utc).timestamp())
            start = end - 40 * 86400
            url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
                   f"?period1={start}&period2={end}&interval=1d")
            r = requests.get(url, timeout=REAL_VERIFY_TIMEOUT,
                             proxies=REAL_VERIFY_PROXY, headers=REAL_VERIFY_UA)
            res = r.json().get("chart", {}).get("result")
            if not res:
                return True, f"降级放行: Yahoo 无数据 ({ticker})"
            ts = res[0]["timestamp"]
            q = res[0]["indicators"]["quote"][0]
            closes = []  # (date_str, close)
            for i, t in enumerate(ts):
                if q["close"][i] is None:
                    continue
                d = datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")
                closes.append((d, float(q["close"][i])))
            if len(closes) < 21:
                return True, f"降级放行: 真实数据不足 ({ticker}, {len(closes)}根<21)"
            # 取 date <= signal_date 的最后一根完整 bar（美股休市日取最近交易日）
            idx = None
            for i, (d, c) in enumerate(closes):
                if d <= signal_date:
                    idx = i
            if idx is None or idx < 20:
                return True, f"降级放行: 信号日无真实数据 ({ticker}@{signal_date})"
            real_close = closes[idx][1]
            prev20_high = max(c for d, c in closes[max(0, idx - 20):idx])  # 不含当日
            passed = real_close > prev20_high
            ev = (f"真实验证 {ticker}: close={real_close:.2f} vs prev20H={prev20_high:.2f} "
                  f"({(real_close / prev20_high - 1) * 100:+.1f}%) "
                  f"{'PASS' if passed else 'REJECT'}")
            return passed, ev
        except Exception as e:
            return True, f"降级放行: {type(e).__name__}: {e}"

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float, rate: float,
                            time_in_force: str, current_time: datetime, entry_tag: Optional[str],
                            side: str, **kwargs) -> bool:
        # 第一道：美股交易时段（周末/节假日/盘外不开仓）
        if not self._is_market_open(current_time):
            return False
        # 第二道：真实美股突破验证（TASK 124）——只拦 don_break 波段腿入场
        if entry_tag != "don_break":
            return True
        df = self.dp.get_dataframe(pair, self.timeframe)
        if df is None or df.empty:
            return True
        signal_date = str(df["date"].iloc[-1])[:10]
        ok, evidence = self._real_verify(pair, signal_date)
        logger.info(f"[real_verify] {pair} {signal_date} → {evidence}")
        return ok

    # confirm_trade_exit 不覆盖，保持父类/框架默认行为（不限制退出时段），
    # 通道退出与 -50% 回撤止损必须能随时触发。
