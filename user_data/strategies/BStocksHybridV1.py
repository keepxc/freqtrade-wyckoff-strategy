"""
BStocksHybridV1 — 半仓买入持有 + 半仓 Donchian 波段混合策略（bStocks 正股）
=============================================================
用户确认设计（2026-08-07）：入场仓位沿用 BStocksDonchianV1 的波动率目标
算法（TARGET_VOL=0.45, MAX_LEV=1.0, 公式不变）算出总仓位；Donchian 10日
通道退出信号首次触发时，只平掉这笔仓位的 50%（波段腿），剩余 50% 转为
纯买入持有腿，此后不再对波段信号做任何反应，也不做定期再平衡（仓位权重
自然漂移，理由：强制再平衡会隐含均值回归择时假设，与"纯买入持有"的
本意矛盾）。买入持有腿唯一的退出条件是相对其自身持仓期最高价的 -50%
回撤止损（灾难性/黑天鹅保护，非战术止损，阈值取整数量级，参考口径为
本策略族回测中已观察到的单笔最差跌幅 -30.34% 的数量级放大，未做精细
优化）。

实现方式：freqtrade 原生不支持"一个 pair 一笔仓位里一半波段一半永久
持有"，用 position_adjustment_enable + adjust_trade_position 做部分
平仓模拟：
  1. 入场：与 BStocksDonchianV1 相同，波动率目标决定总仓位。
  2. 首次 don_exit 信号触发时：通过 adjust_trade_position 返回
     (-stake_amount*0.5, 'tactical_exit')，平掉一半（战术腿退出）。是否
     已平仓用 trade.nr_of_successful_exits（框架维护、退出单真正成交后
     才 +1）判断，不用提前写的 custom_data 标记，避免退出单未成交时误判
     （2026-08-08 修正）。
  3. 此后每根 bar 更新 custom_data 中的 peak_price（相对现价的历史最高
     价，仅在 nr_of_successful_exits ≥ 1 后开始追踪，因为买入持有腿是从
     这一刻才"独立"存在的）。
  4. 当 current_rate 相对 peak_price 回撤 ≤ -50% 时，返回
     -trade.stake_amount（此时应等于剩余的一半仓位），全部平仓，退出
     标记 'bh_dd_stop'。

费率假设沿用 0.1%（币安现货标准 taker，见 BStocksDonchianV1 说明）。
资产范围、数据代理方式与 BStocksDonchianV1 相同，不重复说明。
"""
import numpy as np
import pandas as pd
from pandas import DataFrame
from datetime import datetime
from typing import Optional

from freqtrade.strategy import IStrategy
from freqtrade.persistence import Trade


class BStocksHybridV1(IStrategy):
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

    position_adjustment_enable = True
    max_entry_position_adjustment = -1  # 不限制调整次数(用负值做部分/全部平仓)

    N_ENTRY = 20
    N_EXIT = 10
    VOL_LOOKBACK = 20
    TARGET_VOL = 0.45
    MAX_LEV = 1.0

    TACTICAL_FRACTION = 0.5   # 波段腿占比(触发通道退出时平掉的比例)
    BH_DD_STOP = -0.50        # 买入持有腿相对其自身峰值的极端回撤止损

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['don_upper'] = dataframe['high'].rolling(self.N_ENTRY).max().shift(1)
        dataframe['don_exit'] = dataframe['low'].rolling(self.N_EXIT).min().shift(1)
        ret = dataframe['close'].pct_change()
        dataframe['don_realized_vol'] = ret.rolling(self.VOL_LOOKBACK).std().shift(1) * np.sqrt(365)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        entry_mask = dataframe['don_upper'].notna() & (dataframe['close'] > dataframe['don_upper'])
        dataframe.loc[entry_mask, 'enter_long'] = 1
        dataframe.loc[entry_mask, 'enter_tag'] = 'don_break'
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 退出完全通过 adjust_trade_position 处理(部分平仓+回撤止损),
        # 这里不产生 exit_long 信号,避免框架把整笔仓位一次性平掉。
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

    def _get_don_exit_signal(self, pair: str, current_time: datetime) -> bool:
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        hist = df.loc[df['date'] < current_time]
        if hist.empty:
            return False
        last = hist.iloc[-1]
        if pd.isna(last['don_exit']):
            return False
        return bool(last['close'] < last['don_exit'])

    def adjust_trade_position(self, trade: Trade, current_time: datetime, current_rate: float,
                              current_profit: float, min_stake: Optional[float], max_stake: float,
                              current_entry_rate: float, current_exit_rate: float, **kwargs):
        # 用 nr_of_successful_exits(框架维护、只在退出单真正成交后才 +1)判断战术腿
        # 是否已平仓，而不是提前写 custom_data 标记——避免"标记已写但退出单实际
        # 失败/未成交"时错误地把仓位当成已进入买入持有腿(2026-08-08 修正，见
        # TASK109 code review)。
        if trade.nr_of_successful_exits == 0:
            if self._get_don_exit_signal(trade.pair, current_time):
                sell_stake = trade.stake_amount * self.TACTICAL_FRACTION
                if min_stake is not None and (trade.stake_amount - sell_stake) < min_stake:
                    # 剩余部分不足最小下单量时,直接全平,不强行拆出买入持有腿
                    return -trade.stake_amount, 'tactical_exit'
                return -sell_stake, 'tactical_exit'
            return None

        # 已进入买入持有腿：只做峰值追踪 + 极端回撤止损，不响应波段信号
        peak = trade.get_custom_data('bh_peak_price', default=current_rate)
        peak = max(peak, current_rate)
        trade.set_custom_data('bh_peak_price', peak)

        dd = (current_rate - peak) / peak
        if dd <= self.BH_DD_STOP:
            return -trade.stake_amount, 'bh_dd_stop'

        return None
