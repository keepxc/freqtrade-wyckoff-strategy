#!/usr/bin/env python3
"""Generate BStocksHybridV1DryRun with bot_start local-data injection."""
import logging
from datetime import datetime, time, UTC
from typing import Optional

from freqtrade.data.history import load_pair_history
from freqtrade.enums import CandleType
from freqtrade.util.datetime_helpers import dt_ts
from freqtrade.persistence import Trade

from BStocksHybridV1 import BStocksHybridV1

logger = logging.getLogger(__name__)

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
                # 关键：同时设置最后刷新时间，否则 _build_coroutine 判定
                # min_ts(995天前) > _pairs_last_refresh_time(0) → "Time jump" 清缓存
                exchange._pairs_last_refresh_time[key] = dt_ts(datetime.now(UTC))
                logger.info(f"[bot_start] 预加载本地历史: {pair} {len(df)} rows")
        except Exception as e:
            logger.warning(f"[bot_start] 预加载失败，降级为纯交易所数据: {e}")

    def _is_market_open(self, current_time: datetime) -> bool:
        if current_time.weekday() >= 5:  # 5=Sat, 6=Sun
            return False
        if current_time.strftime("%Y-%m-%d") in NYSE_HOLIDAYS:
            return False
        t = current_time.time()
        return MARKET_OPEN_UTC <= t < MARKET_CLOSE_UTC

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float, rate: float,
                            time_in_force: str, current_time: datetime, entry_tag: Optional[str],
                            side: str, **kwargs) -> bool:
        return self._is_market_open(current_time)

    # confirm_trade_exit 不覆盖，保持父类/框架默认行为（不限制退出时段），
    # 通道退出与 -50% 回撤止损必须能随时触发。
