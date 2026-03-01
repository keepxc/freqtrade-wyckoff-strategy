"""
Task 2.7: 气候行动检测器（Climatic Action Detector）

威科夫理论：气候行动（SC/BC）是 Phase A 的核心入口事件。
没有识别到 SC/BC，就不允许系统进入 Phase B/C 的积累逻辑。

核心输出字段：
    climatic_action_detected : bool   — 当前K线是否为气候行动柱
    climatic_type            : str    — 'SC' / 'BC' / 'none'
    climatic_level           : float  — 气候行动发生时的收盘价位（供 Task 2.9 位置约束使用）
    post_climax_bars         : int    — 距上次气候行动已过K线数（Phase B 时间参照）
    in_post_climax_window    : bool   — 是否处于气候行动后的有效积累窗口（60根K线内）

物理依据：
    SC (Selling Climax):
        - 价格创 N 根K线新低（至少60根），供应端最后一次极端释放
        - 极高成交量（>2.0倍20周期均量）：大量供应被专业资金吸收
        - K线收盘位置适中或偏高（>30%）：表明买方开始接管
        - 宽幅K线（>1.5倍ATR）：供需剧烈碰撞

    BC (Buying Climax):
        - 价格创 N 根K线新高（至少60根），需求端最后一次极端释放
        - 极高成交量（>2.0倍20周期均量）：大量买盘涌入后专业资金开始派发
        - K线收盘位置适中或偏低（<70%）：表明卖方开始接管
        - 宽幅K线（>1.5倍ATR）：供需剧烈碰撞

防误判设计：
    - 要求在气候行动之前存在明显的单边趋势（N根K线方向一致）
    - SC 前提：价格比20根K线前跌幅 > 5%（确认下跌趋势存在）
    - BC 前提：价格比20根K线前涨幅 > 5%（确认上涨趋势存在）

与系统集成：
    - 在 populate_indicators 中调用，产出上述字段
    - _update_trading_permission 中：检测到 Climax 时触发 no_physical_reset（Task 2.5 已实现）
    - Task 2.9 No Supply：位置约束使用 climatic_level 字段
    - Task 3.2 状态机：SC/BC 作为 Phase A 入口触发器
"""

import numpy as np
import pandas as pd
import logging
from pandas import DataFrame

logger = logging.getLogger(__name__)


class ClimaticActionDetector:
    """气候行动（SC/BC）检测器"""

    def __init__(self, strategy=None):
        self.strategy = strategy

        # SC 检测参数
        self.sc_lookback = 60          # 新低判断回看窗口
        self.sc_trend_bars = 20        # 趋势确认窗口
        self.sc_trend_min_drop = 0.05  # 趋势确认最小跌幅（5%）
        self.sc_volume_mult = 2.0      # 成交量倍数门槛
        self.sc_atr_mult = 1.5         # ATR宽幅门槛
        self.sc_close_pos_min = 0.30   # 最低收盘位置（至少在30%以上表明买方接管）

        # BC 检测参数
        self.bc_lookback = 60          # 新高判断回看窗口
        self.bc_trend_bars = 20        # 趋势确认窗口
        self.bc_trend_min_rise = 0.05  # 趋势确认最小涨幅（5%）
        self.bc_volume_mult = 2.0      # 成交量倍数门槛
        self.bc_atr_mult = 1.5         # ATR宽幅门槛
        self.bc_close_pos_max = 0.70   # 最高收盘位置（至多在70%以下表明卖方接管）

        # 积累窗口参数
        self.post_climax_window = 60   # 气候行动后的有效积累窗口（60根K线）

    def detect(self, dataframe: DataFrame) -> DataFrame:
        """
        主检测入口：计算所有气候行动相关字段

        Args:
            dataframe: 包含 OHLCV + atr_20 + volume_sma_20 + close_position + bar_range 的 DataFrame

        Returns:
            添加了气候行动字段的 DataFrame
        """
        # 初始化输出字段
        dataframe['climatic_action_detected'] = False
        dataframe['climatic_type'] = 'none'
        dataframe['climatic_level'] = 0.0
        dataframe['post_climax_bars'] = 999   # 999 = 从未检测到
        dataframe['in_post_climax_window'] = False

        try:
            dataframe = self._detect_sc(dataframe)
            dataframe = self._detect_bc(dataframe)
            dataframe = self._calculate_post_climax_fields(dataframe)
            self._log_stats(dataframe)
        except Exception as e:
            logger.warning(f'ClimaticActionDetector.detect() 异常: {e}')

        return dataframe

    def _detect_sc(self, dataframe: DataFrame) -> DataFrame:
        """
        卖方气候行动（Selling Climax）检测

        SC 物理逻辑：
            1. 趋势前提：过去20根K线价格跌幅 > 5%（确认下跌趋势）
            2. 新低突破：当前低点创60根K线新低（非渐进下跌，是加速跌破）
            3. 极高成交量：volume > 20周期均量 * 2.0（供应极端释放）
            4. 宽幅K线：bar_range > ATR * 1.5（供需激烈碰撞）
            5. 买方接管证据：收盘位置 > 30%（虽然低点很低，但收盘被拉回）
        """
        # 条件1：趋势前提（过去N根K线存在跌幅）
        prior_close = dataframe['close'].shift(self.sc_trend_bars)
        downtrend_exists = (prior_close - dataframe['close']) / prior_close > self.sc_trend_min_drop

        # 条件2：价格创60根K线新低
        rolling_low = dataframe['low'].rolling(self.sc_lookback).min().shift(1)
        new_n_bar_low = dataframe['low'] <= rolling_low

        # 条件3：极高成交量
        high_volume = dataframe['volume'] > dataframe['volume_sma_20'] * self.sc_volume_mult

        # 条件4：宽幅K线
        wide_range = dataframe['bar_range'] > dataframe['atr_20'] * self.sc_atr_mult

        # 条件5：买方接管（收盘位置 > 30%）
        buyer_takeover = dataframe['close_position'] >= self.sc_close_pos_min

        # 综合 SC 判断
        sc_detected = (
            downtrend_exists &
            new_n_bar_low &
            high_volume &
            wide_range &
            buyer_takeover
        )

        # 写入结果（SC 优先于 BC，不覆盖已检测的 BC）
        sc_mask = sc_detected & (dataframe['climatic_type'] == 'none')
        dataframe.loc[sc_mask, 'climatic_action_detected'] = True
        dataframe.loc[sc_mask, 'climatic_type'] = 'SC'
        dataframe.loc[sc_mask, 'climatic_level'] = dataframe.loc[sc_mask, 'close']

        return dataframe

    def _detect_bc(self, dataframe: DataFrame) -> DataFrame:
        """
        买方气候行动（Buying Climax）检测

        BC 物理逻辑：
            1. 趋势前提：过去20根K线价格涨幅 > 5%（确认上涨趋势）
            2. 新高突破：当前高点创60根K线新高（加速冲高）
            3. 极高成交量：volume > 20周期均量 * 2.0（需求极端释放 / 派发开始）
            4. 宽幅K线：bar_range > ATR * 1.5（供需激烈碰撞）
            5. 卖方接管证据：收盘位置 < 70%（虽然冲高，但收盘被压低）
        """
        # 条件1：趋势前提
        prior_close = dataframe['close'].shift(self.bc_trend_bars)
        uptrend_exists = (dataframe['close'] - prior_close) / prior_close > self.bc_trend_min_rise

        # 条件2：价格创60根K线新高
        rolling_high = dataframe['high'].rolling(self.bc_lookback).max().shift(1)
        new_n_bar_high = dataframe['high'] >= rolling_high

        # 条件3：极高成交量
        high_volume = dataframe['volume'] > dataframe['volume_sma_20'] * self.bc_volume_mult

        # 条件4：宽幅K线
        wide_range = dataframe['bar_range'] > dataframe['atr_20'] * self.bc_atr_mult

        # 条件5：卖方接管（收盘位置 < 70%）
        seller_takeover = dataframe['close_position'] <= self.bc_close_pos_max

        # 综合 BC 判断
        bc_detected = (
            uptrend_exists &
            new_n_bar_high &
            high_volume &
            wide_range &
            seller_takeover
        )

        # 写入结果（只在未标记 SC 时写入）
        bc_mask = bc_detected & (dataframe['climatic_type'] == 'none')
        dataframe.loc[bc_mask, 'climatic_action_detected'] = True
        dataframe.loc[bc_mask, 'climatic_type'] = 'BC'
        dataframe.loc[bc_mask, 'climatic_level'] = dataframe.loc[bc_mask, 'close']

        return dataframe

    def _calculate_post_climax_fields(self, dataframe: DataFrame) -> DataFrame:
        """
        计算气候行动后的时序字段

        post_climax_bars: 距上次气候行动已过K线数
            - 0 = 当前K线就是气候行动
            - 1-59 = 气候行动后有效窗口内
            - 60+ = 超出有效积累窗口
            - 999 = 回测时间范围内从未检测到气候行动

        in_post_climax_window: True = 处于气候行动后 1-60 根K线内
            物理含义：Phase A 完成，系统处于 Phase B（横盘积累）有效期内
        """
        bars = len(dataframe)
        post_climax = np.full(bars, 999, dtype=np.int32)
        climax_mask = dataframe['climatic_action_detected'].values

        # 向前传播：记录每根K线距上次气候行动的距离
        last_climax_idx = -999
        for i in range(bars):
            if climax_mask[i]:
                last_climax_idx = i
            if last_climax_idx >= 0:
                post_climax[i] = i - last_climax_idx

        dataframe['post_climax_bars'] = post_climax

        # 有效积累窗口：气候行动后 1-60 根K线（不包括气候行动当根）
        dataframe['in_post_climax_window'] = (
            (dataframe['post_climax_bars'] >= 1) &
            (dataframe['post_climax_bars'] <= self.post_climax_window)
        )

        # 传播 climatic_level：窗口内每根K线都记录最近一次气候行动的价位
        # 供 Task 2.9 位置约束使用
        climatic_level_propagated = dataframe['climatic_level'].copy()
        last_level = 0.0
        for i in range(bars):
            if climax_mask[i]:
                last_level = dataframe['climatic_level'].iloc[i]
            if post_climax[i] <= self.post_climax_window and last_level > 0:
                climatic_level_propagated.iloc[i] = last_level
        dataframe['climatic_level'] = climatic_level_propagated

        return dataframe

    def _log_stats(self, dataframe: DataFrame) -> None:
        """输出检测统计"""
        total = len(dataframe)
        sc_count = (dataframe['climatic_type'] == 'SC').sum()
        bc_count = (dataframe['climatic_type'] == 'BC').sum()
        in_window = dataframe['in_post_climax_window'].sum()

        logger.info(
            f'Task 2.7 ClimaticAction: SC={sc_count}, BC={bc_count}, '
            f'气候后窗口K线={in_window} ({in_window / total * 100:.1f}%)'
        )
