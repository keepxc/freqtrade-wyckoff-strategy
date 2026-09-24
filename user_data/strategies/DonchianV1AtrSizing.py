"""
DonchianV1AtrSizing — DonchianV1 对照实验：仅替换仓位算法（TASK 待归档）
=============================================================
信号层与 DonchianV1 逐行一致（Donchian 20/10 纯多头，10 日低点通道退出，
无价格止损）。唯一改动：custom_stake_amount 从"波动率目标仓位"换成
"ATR 固定分数风险仓位"（van Tharp / Turtle 经典法）：
  stake = (账户总额 × 1% 风险) / (2×ATR / 入场价)
ATR 只用来反推仓位大小，不作为实际止损——出场仍然只看 10 日低点通道，
不引入双重出场。

对照目的：验证该仓位算法相对 DonchianV1 的 vol-target 仓位（TASK75b 封板）
是否有可采纳的改善。参数 RISK_PER_TRADE=1%、ATR_MULTIPLIER=2.0 未经寻优，
直接沿用来源文件给出的值（用户确认，不做网格搜索）。
"""
import numpy as np
import pandas as pd
from pandas import DataFrame
from datetime import datetime
from typing import Optional

from freqtrade.strategy import IStrategy


class DonchianV1AtrSizing(IStrategy):
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

    # 信号参数 —— 与 DonchianV1 完全一致（零调参经典值，禁止 hyperopt）
    N_ENTRY = 20
    N_EXIT = 10

    # 仓位参数 —— ATR 固定分数风险法（未寻优，直接采用来源值）
    ATR_PERIOD = 14
    ATR_MULTIPLIER = 2.0
    RISK_PER_TRADE = 0.01

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # shift(1)：通道只用截至前一日的数据，当日收盘决策 -> 次日开盘执行，因果
        dataframe['don_upper'] = dataframe['high'].rolling(self.N_ENTRY).max().shift(1)
        dataframe['don_exit'] = dataframe['low'].rolling(self.N_EXIT).min().shift(1)
        dataframe['atr'] = self._compute_atr(dataframe, self.ATR_PERIOD)
        return dataframe

    @staticmethod
    def _compute_atr(df: DataFrame, period: int) -> pd.Series:
        """Wilder's ATR：True Range 的 RMA 平滑。"""
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
        return dataframe

    def custom_stake_amount(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_stake: float,
        min_stake: Optional[float],
        max_stake: float,
        leverage: float,
        entry_tag: Optional[str],
        side: str,
        **kwargs
    ) -> float:
        """ATR 固定分数风险仓位：stake = 风险金额 / (2xATR/入场价)。
        ATR 只用于反推仓位，不作为实际止损（出场仍只用 10 日低点通道）。"""
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        # 只用 current_time 之前的已收盘K线，防回测框架下 get_analyzed_dataframe
        # 返回全量 df 导致的未来函数（与 DonchianV1 同一处坑）
        hist = df.loc[df['date'] < current_time]
        if hist.empty:
            return proposed_stake
        atr_val = hist['atr'].iloc[-1]
        if pd.isna(atr_val) or atr_val <= 0:
            return proposed_stake

        stop_dist_pct = (self.ATR_MULTIPLIER * atr_val) / current_rate
        if stop_dist_pct <= 0:
            return proposed_stake

        # 固定名义本金基数（config stake_amount），非当前账户权益 ——
        # 与 DonchianV1 基线的非复利口径对齐（方案A，用户确认 2026-08-06）：
        # 避免"风险按当前权益滚动放大"引入复利效应，混淆仓位算法与复利的对比
        fixed_capital_base = self.config.get('stake_amount', proposed_stake)
        risk_amount = fixed_capital_base * self.RISK_PER_TRADE
        stake = risk_amount / stop_dist_pct

        if max_stake is not None and max_stake > 0:
            stake = min(stake, max_stake)
        if min_stake is not None and stake < min_stake:
            stake = min_stake
        return stake
