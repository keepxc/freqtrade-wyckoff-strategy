"""
DonchianV1MaExitOnly — DonchianV1AtrMaExit 的诊断性拆分变体（探索性——非结论，禁止引用为证据）
=============================================================
背景：DonchianV1AtrMaExit（ATR硬止损+20MA跟踪出场叠加）confirm结果 delta=-1.60%，
按退出原因拆分发现亏损集中在 ATR 止损分支（30笔，均利润-10.72%，胜率0%，
平均持仓仅4天），20MA分支单独看是正贡献（73笔，均利润+20.89%，胜率68.5%）。
本变体去掉 ATR 硬止损（custom_stoploss 直接返回 None），只保留 10日通道退出
+ 20MA跟踪出场两条，用于拆分验证"是否是 ATR 止损这一条拖累了整体表现"。
仅诊断用途，不构成独立TASK立项，不写入采纳/证伪表。

信号层与 DonchianV1 逐行一致（Donchian 20/10 纯多头，入场不变）。
仓位层沿用 DonchianV1 的波动率目标仓位（不变，避免混淆变量）。
"""
import numpy as np
import pandas as pd
from pandas import DataFrame
from datetime import datetime
from typing import Optional

from freqtrade.strategy import IStrategy
from freqtrade.persistence import Trade


class DonchianV1MaExitOnly(IStrategy):
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
        """诊断变体：不设置 ATR 硬止损，只保留 don_exit + ma20_exit 两条出场，
        用于拆分验证 ATR 止损分支对整体表现的贡献。"""
        return None

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
