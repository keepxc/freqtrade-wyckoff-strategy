"""
DonchianV1 — 经典 Donchian 通道突破，纯多头，波动率目标仓位
=============================================================
重建战役最终存活策略（TASK 72-75b 审计链）：
- 信号层：Donchian 20/10（零调参经典参数，TASK 74 D1 参数高原 88-94% 通过，
  D2 成本 0.30%/边仍正）
- 仓位层：入场时波动率目标 lb=20d / target_vol=20% / 封顶 1.0x（只降不加杠杆），
  持仓期间不再平衡（TASK 75b 入场定杠杆变体：spot Calmar 0.72 / futures 0.74，
  双数据集 F1/F2 通过）
- 无 Wyckoff 依赖，无自定义结构检测器（TASK 73 C2 判定检测器零增量）

风险控制说明：本策略不设价格止损（stoploss=-0.99 仅为框架占位），
风险控制 = 通道退出（10 日低点）+ 波动率目标仓位。审计口径 MaxDD ~26%。
"""
import numpy as np
import pandas as pd
from pandas import DataFrame
from datetime import datetime
from typing import Optional

from freqtrade.strategy import IStrategy


class DonchianV1(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = '1d'
    can_short = False
    process_only_new_candles = True
    startup_candle_count = 50

    # 风险控制在通道退出与仓位层，不用价格止损（见模块 docstring）
    stoploss = -0.99
    minimal_roi = {"0": 100}
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = True
    trailing_stop = False

    # 信号参数（零调参经典值，TASK 74 封板，禁止 hyperopt）
    N_ENTRY = 20
    N_EXIT = 10
    # 仓位参数（TASK 75b 选型）
    VOL_LOOKBACK = 20
    TARGET_VOL = 0.20
    MAX_LEV = 1.0

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # shift(1)：通道只用截至前一日的数据，当日收盘决策 → 次日开盘执行，因果
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
        """入场时按波动率目标缩放名义仓位：lev = min(1, target_vol / realized_vol)。"""
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        # 只用 current_time 之前的已收盘K线（信号K线），防回测框架下的未来读取
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
