"""
DonchianV1RSize — DonchianV1 对照实验：信号K线R锚点动态定仓（替换已实现波动率仓位）
=============================================================
背景：TASK112(20MA出场)、TASK113(1R减仓)相继因"截断肥尾利润"证伪，说明"让交易更
舒服"的出场端改造系统性有害。用户前置门控确认(2026-08-15)转向仓位口径本身——不碰
出场骨架（沿用10日通道退出），只改初始仓位的计算锚点，测试"用突破信号K线的止损
距离（R）做等风险定仓"能否优于现有的已实现波动率定仓。

与 TASK113 共享同一个 R 定义（价格原语，非ATR统计量）："入场价 - 信号K线(突破确认
那根)的low"，延续"突破K线低点被跌破=动量瞬时失效"的价格行为逻辑。

前置门控确认的5个决策点（2026-08-15）：
1. 替换（非叠加）DonchianV1现有的已实现波动率定仓——避免双重保守性过滤导致测不出
   R锚点定仓本身的效果（同TASK107教训：多重过滤会把仓位压得过小，拖累总收益）
2. Risk_Pct = 1%（每笔以基础仓位资本的1%作为风险预算）
3. MAX_LEV = 1.0（R过小时名义仓位不得超过基础仓位资本的1.0倍，防止极端放大）
4. 范围仅限初始定仓公式，不含金字塔加码（Scaling In）——保持单一变量
5. 判据沿用CLAUDE.md现有三档分级（利润delta为主），不为本TASK另立新判据

信号层与 DonchianV1 逐行一致（Donchian 20/10 纯多头，入场/出场不变）。
"""
import numpy as np
import pandas as pd
from pandas import DataFrame
from datetime import datetime
from typing import Optional

from freqtrade.strategy import IStrategy


class DonchianV1RSize(IStrategy):
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

    # 仓位参数（本实验唯一变量：R锚点定仓，替换 DonchianV1 的已实现波动率定仓）
    # TASK119(2026-08-17)：按TASK115重开条件单点测试 RISK_PCT=0.02 → 总利润5.57%
    # vs 基线4.61%，delta +0.96% < +2% 门槛，⭕不采纳，按规则回退至1%。
    # 1%与2%两个预先声明取值均⭕，R锚点定仓方向收口，禁止再试3%/4%（参数钓鱼）。
    RISK_PCT = 0.01
    MAX_LEV = 1.0

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['don_upper'] = dataframe['high'].rolling(self.N_ENTRY).max().shift(1)
        dataframe['don_exit'] = dataframe['low'].rolling(self.N_EXIT).min().shift(1)
        return dataframe

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

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: Optional[float], max_stake: float,
                            leverage: float, entry_tag: Optional[str], side: str,
                            **kwargs) -> float:
        """R锚点等风险定仓：notional = (proposed_stake*RISK_PCT) * current_rate / R，
        R = current_rate - 信号K线(严格早于current_time的最后一根)的low。
        R越窄仓位越大，R越宽仓位越小；封顶 proposed_stake*MAX_LEV 防止R过小时仓位失控。
        """
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        # 严格 '<'：只用 current_time 之前已收盘的信号K线，避免未来函数
        hist = df.loc[df['date'] < current_time]
        if hist.empty:
            return proposed_stake
        signal_low = hist['low'].iloc[-1]
        r = current_rate - signal_low
        if r <= 0:
            # 结构上不应发生（信号K线的low理应低于突破价），保守退化为基础仓位
            return proposed_stake
        risk_budget = proposed_stake * self.RISK_PCT
        notional = risk_budget * current_rate / r
        capped = min(notional, proposed_stake * self.MAX_LEV)
        if min_stake is not None and capped < min_stake:
            return min_stake
        return capped
