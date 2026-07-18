"""
DonchianV1Lev20 — TASK 102 预注册变体：MAX_LEV 1.0 → 2.0
=========================================================
与 DonchianV1.py 逐行一致，唯一差异是 MAX_LEV=2.0。见 DonchianV1Lev15.py
docstring 与 wyckoff_v8_redesign.md TASK 102（判据冻结未跑数）。
"""
import numpy as np
import pandas as pd
from pandas import DataFrame
from datetime import datetime
from typing import Optional

from freqtrade.strategy import IStrategy


class DonchianV1Lev20(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = '1d'
    can_short = False
    process_only_new_candles = True
    startup_candle_count = 50

    stoploss = -0.99
    minimal_roi = {"0": 100}
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = True
    trailing_stop = False

    N_ENTRY = 20
    N_EXIT = 10
    VOL_LOOKBACK = 20
    TARGET_VOL = 0.20
    MAX_LEV = 2.0  # TASK 102 变体：唯一与 DonchianV1 不同之处

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['don_upper'] = dataframe['high'].rolling(self.N_ENTRY).max().shift(1)
        dataframe['don_exit'] = dataframe['low'].rolling(self.N_EXIT).min().shift(1)
        ret = dataframe['close'].pct_change()
        dataframe['don_realized_vol'] = ret.rolling(self.VOL_LOOKBACK).std().shift(1) * np.sqrt(365)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            dataframe['don_upper'].notna() & (dataframe['close'] > dataframe['don_upper']),
            ['enter_long', 'enter_tag']
        ] = (1, 'don_break')
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            dataframe['don_exit'].notna() & (dataframe['close'] < dataframe['don_exit']),
            ['exit_long', 'exit_tag']
        ] = (1, 'don_exit')
        return dataframe

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
