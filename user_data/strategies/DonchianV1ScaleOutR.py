"""
DonchianV1ScaleOutR — DonchianV1 对照实验：1R减仓50% + 保本止损 + 剩余仓位10日通道持有
=============================================================
背景：TASK112 证伪了"20MA收盘跟踪出场+ATR硬止损"叠加方案（20MA出场系统性早于
10日通道离场，砍掉肥尾）。用户前置门控确认(2026-08-15)提出脱离该失效骨架的
独立新命题：不改变10日通道退出这一已验证有效的出场骨架，只在盈利路径中段做
一次性减仓，测试是否能在不牺牲趋势跟踪本质的前提下改善盈亏曲线平滑度。

信号层与 DonchianV1 逐行一致（Donchian 20/10 纯多头，入场/主出场不变）。
仓位层沿用 DonchianV1 的波动率目标仓位（custom_stake_amount 不变）。

出场机制（用户确认，2026-08-15）：
1. 风险单位 R = |入场成交价 - 入场信号K线(突破确认那根)的low|（价格原语，
   非ATR统计量——延续TASK112"突破K线低点被跌破=动量瞬时失效"的价格行为逻辑，
   用严格'<'取信号K线，避免用到成交当天未走完的K线，无未来函数）
2. 浮盈达到 1R 时，一次性减仓50%（用 trade.nr_of_successful_exits==0 作为
   "尚未减仓"判断，减仓单实际成交后该计数器才+1，避免下单未成交时被误判为
   已减仓——复用 TASK109 记录过的同类竞态修复模式）
3. 减仓成交后，剩余50%仓位止损上移至"入场价+手续费缓冲"（FEE_BUFFER，覆盖
   双边0.06%手续费），实现名义无风险
4. 剩余仓位出场规则不变：严格沿用10日通道下轨（don_exit），不引入20MA
   （TASK112已证伪，本实验唯一变量是"是否1R处减仓"，不改变趋势跟踪骨架）
5. 范围限定 BTC/ETH/SOL

已知风险（用户与我一致同意的评估指标，需在结果中报告）：
- Profit Leakage Ratio = 实验组总利润 / 基线组总利润：过低说明减仓心理溢价代价过高
- 剩余仓位被保本止损扫出、但若不减仓本可吃到don_exit离场的订单占比：
  过高说明1R保本止损在本品类波动下太容易被插针扫损
"""
import numpy as np
import pandas as pd
from pandas import DataFrame
from datetime import datetime
from typing import Optional

from freqtrade.strategy import IStrategy
from freqtrade.persistence import Trade


class DonchianV1ScaleOutR(IStrategy):
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
    use_custom_stoploss = True
    position_adjustment_enable = True

    N_ENTRY = 20
    N_EXIT = 10
    FEE_BUFFER = 0.0012  # 双边手续费(0.06%x2)缓冲，保证保本止损名义无亏损
    SCALE_OUT_FRACTION = 0.5

    VOL_LOOKBACK = 20
    TARGET_VOL = 0.20
    MAX_LEV = 1.0

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

    def _entry_signal_row(self, pair: str, open_date_utc):
        """入场信号K线(突破确认、次日开盘成交那根之前的收盘K线)。严格'<'避免未来函数。"""
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        entry_hist = df.loc[df['date'] < open_date_utc]
        if entry_hist.empty:
            return None
        return entry_hist.iloc[-1]

    def adjust_trade_position(self, trade: Trade, current_time: datetime, current_rate: float,
                               current_profit: float, min_stake: Optional[float], max_stake: float,
                               current_entry_rate: float, current_exit_rate: float,
                               current_entry_profit: float, current_exit_profit: float, **kwargs):
        """浮盈达到1R时一次性减仓50%。用nr_of_successful_exits(减仓单真正成交后才+1)
        判断是否已减过仓，避免下单未成交时被误判为已减仓（同TASK109竞态修复）。"""
        if trade.nr_of_successful_exits != 0:
            return None
        entry_row = self._entry_signal_row(trade.pair, trade.open_date_utc)
        if entry_row is None:
            return None
        r = trade.open_rate - entry_row['low']
        if r <= 0:
            return None
        target_price = trade.open_rate + r
        if current_rate < target_price:
            return None
        sell_stake = trade.stake_amount * self.SCALE_OUT_FRACTION
        if min_stake is not None and sell_stake < min_stake:
            return None
        return -sell_stake, 'scale_out_1r'

    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                         current_rate: float, current_profit: float, **kwargs) -> Optional[float]:
        """减仓前不设硬止损(只靠don_exit信号出场，与基线一致)。减仓成交后，
        剩余仓位止损上移至入场价+手续费缓冲，实现名义保本。"""
        if trade.nr_of_successful_exits == 0:
            return None
        floor_price = trade.open_rate * (1 + self.FEE_BUFFER)
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
