"""
DonchianV1AtrMaExit — DonchianV1 对照实验：出场机制叠加 ATR 硬止损 + 20MA 收盘跟踪止损
=============================================================
背景：外部资料（Rayner Teo《价格行为交易法》+ Al Brooks 趋势分析）提出的
"M-A-B-E" 框架，经逐条比对，形态识别部分(M/A/B)与本项目已证伪的威科夫
Spring/Test/窄幅整理同源（TASK60-71, W1-c/W3），不采纳。唯一未测过的自由度
是"出场机制"——用户确认(2026-08-11)独立立项验证。

信号层与 DonchianV1 逐行一致（Donchian 20/10 纯多头，入场不变）。
仓位层沿用 DonchianV1 的波动率目标仓位（本实验只测出场，不动仓位算法，
避免和 DonchianV1AtrSizing 的仓位实验混淆变量）。

出场机制（三者取先触发）：
1. 现有 10 日低点通道退出（don_exit，不变）
2. 20MA 收盘价确认跌破（close < sma20，非盘中触及，过滤插针噪声——用户确认）
3. ATR(20) 硬止损：入场K线最低价 - 1倍ATR(20)，入场时定死，本版本不做
   "向上移动"处理（用户方案里"除非向上移动"未给出具体上移规则，为避免
   引入未经确认的新参数，本版本先测纯静态地板价，不隐含发明规则）

用户确认的关键参数：ATR周期=20（非常见14，用户明确要求与20MA/20日vol-lookback
统一），ATR倍数=1，MA周期=20，三者同时生效互不排斥。
"""
import numpy as np
import pandas as pd
from pandas import DataFrame
from datetime import datetime
from typing import Optional

from freqtrade.strategy import IStrategy
from freqtrade.persistence import Trade


class DonchianV1AtrMaExit(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = '1d'
    can_short = False
    process_only_new_candles = True
    startup_candle_count = 50

    # 框架层 stoploss 仍占位为 -0.99；真正的 ATR 止损通过 custom_stoploss 实现
    stoploss = -0.99
    minimal_roi = {"0": 100}
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = True
    trailing_stop = False
    use_custom_stoploss = True

    N_ENTRY = 20
    N_EXIT = 10
    MA_EXIT = 20
    ATR_PERIOD = 20
    ATR_MULTIPLIER = 1.0

    VOL_LOOKBACK = 20
    TARGET_VOL = 0.20
    MAX_LEV = 1.0

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['don_upper'] = dataframe['high'].rolling(self.N_ENTRY).max().shift(1)
        dataframe['don_exit'] = dataframe['low'].rolling(self.N_EXIT).min().shift(1)
        dataframe['sma_exit'] = dataframe['close'].rolling(self.MA_EXIT).mean()
        dataframe['atr'] = self._compute_atr(dataframe, self.ATR_PERIOD)
        ret = dataframe['close'].pct_change()
        dataframe['don_realized_vol'] = ret.rolling(self.VOL_LOOKBACK).std().shift(1) * np.sqrt(365)
        return dataframe

    @staticmethod
    def _compute_atr(df: DataFrame, period: int) -> pd.Series:
        high, low, close = df['high'], df['low'], df['close']
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)
        return tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        entry_mask = dataframe['don_upper'].notna() & (dataframe['close'] > dataframe['don_upper'])
        dataframe.loc[entry_mask, 'enter_long'] = 1
        dataframe.loc[entry_mask, 'enter_tag'] = 'don_break'
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        exit_mask = dataframe['don_exit'].notna() & (dataframe['close'] < dataframe['don_exit'])
        dataframe.loc[exit_mask, 'exit_long'] = 1
        dataframe.loc[exit_mask, 'exit_tag'] = 'don_exit'
        ma_exit_mask = (
            dataframe['sma_exit'].notna()
            & (dataframe['close'] < dataframe['sma_exit'])
            & (dataframe['exit_long'] != 1)
        )
        dataframe.loc[ma_exit_mask, 'exit_long'] = 1
        dataframe.loc[ma_exit_mask, 'exit_tag'] = 'ma20_exit'
        return dataframe

    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                         current_rate: float, current_profit: float, **kwargs) -> Optional[float]:
        """ATR硬止损：突破信号K线(收盘确认突破、次日开盘成交的那根)的 low - 1xATR(20)，
        入场时定死的绝对地板价，通过每次回调重算相对当前价的距离来固定同一个绝对价位。
        用严格 '<' 只取信号K线收盘后已知的数据，避免用到成交当天尚未走完的K线（未来函数）。"""
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        entry_hist = df.loc[df['date'] < trade.open_date_utc]
        if entry_hist.empty:
            return None
        entry_row = entry_hist.iloc[-1]
        atr_at_entry = entry_row['atr']
        if pd.isna(atr_at_entry) or atr_at_entry <= 0:
            return None
        floor_price = entry_row['low'] - self.ATR_MULTIPLIER * atr_at_entry
        if floor_price <= 0:
            return None
        return (floor_price / current_rate) - 1

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: Optional[float], max_stake: float,
                            leverage: float, entry_tag: Optional[str], side: str,
                            **kwargs) -> float:
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        hist = df.loc[df['date'] < current_time]
        if hist.empty:
            return proposed_stake
        rv = hist['don_realized_vol'].iloc[-1]
        if pd.isna(rv) or rv <= 0:
            lev = 1.0
        else:
            lev = min(self.MAX_LEV, self.TARGET_VOL / rv)
        stake = proposed_stake * lev
        if min_stake is not None and stake < min_stake:
            return min_stake
        return stake
