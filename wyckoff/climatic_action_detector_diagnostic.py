"""
Task 2.7: 气候行动检测器（Climatic Action Detector）- 诊断版本

在原有逻辑基础上添加详细的诊断日志，用于排查为什么 SC/BC 从未触发。

使用方法：
1. 在策略中临时替换 from wyckoff.climatic_action_detector import ClimaticActionDetector
   为 from wyckoff.climatic_action_detector_diagnostic import ClimaticActionDetector
2. 运行回测
3. 查看日志输出，确认每个条件的通过率
"""

import numpy as np
import pandas as pd
import logging
from pandas import DataFrame

logger = logging.getLogger(__name__)


class ClimaticActionDetector:
    """气候行动（SC/BC）检测器 - 诊断版本"""

    def __init__(self, strategy=None):
        self.strategy = strategy

        # SC 检测参数
        self.sc_lookback = 60
        self.sc_trend_bars = 20
        self.sc_trend_min_drop = 0.05
        self.sc_volume_mult = 2.0
        self.sc_atr_mult = 1.5
        self.sc_close_pos_min = 0.30

        # BC 检测参数
        self.bc_lookback = 60
        self.bc_trend_bars = 20
        self.bc_trend_min_rise = 0.05
        self.bc_volume_mult = 2.0
        self.bc_atr_mult = 1.5
        self.bc_close_pos_max = 0.70

        # 积累窗口参数
        self.post_climax_window = 60
        
        # 诊断标志
        self._diagnostic_logged = False

    def detect(self, dataframe: DataFrame) -> DataFrame:
        """主检测入口：计算所有气候行动相关字段"""
        # 初始化输出字段
        dataframe['climatic_action_detected'] = False
        dataframe['climatic_type'] = 'none'
        dataframe['climatic_level'] = 0.0
        dataframe['post_climax_bars'] = 999
        dataframe['in_post_climax_window'] = False

        try:
            dataframe = self._detect_sc(dataframe)
            dataframe = self._detect_bc(dataframe)
            dataframe = self._calculate_post_climax_fields(dataframe)
            self._log_stats(dataframe)
            
            # 只在第一次运行时输出详细诊断
            if not self._diagnostic_logged:
                self._diagnostic_analysis(dataframe)
                self._diagnostic_logged = True
                
        except Exception as e:
            logger.warning(f'ClimaticActionDetector.detect() 异常: {e}')

        return dataframe

    def _detect_sc(self, dataframe: DataFrame) -> DataFrame:
        """卖方气候行动（Selling Climax）检测 - 带诊断日志"""
        
        # 条件1：趋势前提
        prior_close = dataframe['close'].shift(self.sc_trend_bars)
        price_drop_ratio = (prior_close - dataframe['close']) / prior_close
        downtrend_exists = price_drop_ratio > self.sc_trend_min_drop

        # 条件2：价格创60根K线新低
        rolling_low = dataframe['low'].rolling(self.sc_lookback).min().shift(1)
        new_n_bar_low = dataframe['low'] <= rolling_low

        # 条件3：极高成交量
        volume_ratio = dataframe['volume'] / dataframe['volume_sma_20']
        high_volume = volume_ratio > self.sc_volume_mult

        # 条件4：宽幅K线
        range_ratio = dataframe['bar_range'] / dataframe['atr_20']
        wide_range = range_ratio > self.sc_atr_mult

        # 条件5：买方接管
        buyer_takeover = dataframe['close_position'] >= self.sc_close_pos_min

        # 综合 SC 判断
        sc_detected = (
            downtrend_exists &
            new_n_bar_low &
            high_volume &
            wide_range &
            buyer_takeover
        )

        # 写入结果
        sc_mask = sc_detected & (dataframe['climatic_type'] == 'none')
        dataframe.loc[sc_mask, 'climatic_action_detected'] = True
        dataframe.loc[sc_mask, 'climatic_type'] = 'SC'
        dataframe.loc[sc_mask, 'climatic_level'] = dataframe.loc[sc_mask, 'close']

        return dataframe

    def _detect_bc(self, dataframe: DataFrame) -> DataFrame:
        """买方气候行动（Buying Climax）检测 - 带诊断日志"""
        
        # 条件1：趋势前提
        prior_close = dataframe['close'].shift(self.bc_trend_bars)
        price_rise_ratio = (dataframe['close'] - prior_close) / prior_close
        uptrend_exists = price_rise_ratio > self.bc_trend_min_rise

        # 条件2：价格创60根K线新高
        rolling_high = dataframe['high'].rolling(self.bc_lookback).max().shift(1)
        new_n_bar_high = dataframe['high'] >= rolling_high

        # 条件3：极高成交量
        volume_ratio = dataframe['volume'] / dataframe['volume_sma_20']
        high_volume = volume_ratio > self.bc_volume_mult

        # 条件4：宽幅K线
        range_ratio = dataframe['bar_range'] / dataframe['atr_20']
        wide_range = range_ratio > self.bc_atr_mult

        # 条件5：卖方接管
        seller_takeover = dataframe['close_position'] <= self.bc_close_pos_max

        # 综合 BC 判断
        bc_detected = (
            uptrend_exists &
            new_n_bar_high &
            high_volume &
            wide_range &
            seller_takeover
        )

        # 写入结果
        bc_mask = bc_detected & (dataframe['climatic_type'] == 'none')
        dataframe.loc[bc_mask, 'climatic_action_detected'] = True
        dataframe.loc[bc_mask, 'climatic_type'] = 'BC'
        dataframe.loc[bc_mask, 'climatic_level'] = dataframe.loc[bc_mask, 'close']

        return dataframe

    def _calculate_post_climax_fields(self, dataframe: DataFrame) -> DataFrame:
        """计算气候行动后的时序字段"""
        bars = len(dataframe)
        post_climax = np.full(bars, 999, dtype=np.int32)
        climax_mask = dataframe['climatic_action_detected'].values

        last_climax_idx = -999
        for i in range(bars):
            if climax_mask[i]:
                last_climax_idx = i
            if last_climax_idx >= 0:
                post_climax[i] = i - last_climax_idx

        dataframe['post_climax_bars'] = post_climax

        dataframe['in_post_climax_window'] = (
            (dataframe['post_climax_bars'] >= 1) &
            (dataframe['post_climax_bars'] <= self.post_climax_window)
        )

        climatic_level_propagated = dataframe['climatic_level'].copy()
        last_level = 0.0
        for i in range(bars):
            if climax_mask[i]:
                last_level = dataframe['climatic_level'].iloc[i]
            if post_climax[i] <= self.post_climax_window and last_level > 0:
                climatic_level_propagated.iloc[i] = last_level
        dataframe['climatic_level'] = climatic_level_propagated

        return dataframe

    def _diagnostic_analysis(self, dataframe: DataFrame) -> None:
        """详细诊断分析 - 只在第一次运行时执行"""
        
        logger.info("=" * 80)
        logger.info("🔍 Wyckoff 气候行动检测诊断报告")
        logger.info("=" * 80)
        
        total = len(dataframe)
        
        # ========================================================================
        # SC 诊断
        # ========================================================================
        logger.info("\n【SC (Selling Climax) 检测诊断】")
        
        # 重新计算各条件（与 _detect_sc 逻辑一致）
        prior_close = dataframe['close'].shift(self.sc_trend_bars)
        price_drop_ratio = (prior_close - dataframe['close']) / prior_close
        downtrend_exists = price_drop_ratio > self.sc_trend_min_drop
        
        rolling_low = dataframe['low'].rolling(self.sc_lookback).min().shift(1)
        new_n_bar_low = dataframe['low'] <= rolling_low
        
        volume_ratio = dataframe['volume'] / dataframe['volume_sma_20']
        high_volume = volume_ratio > self.sc_volume_mult
        
        range_ratio = dataframe['bar_range'] / dataframe['atr_20']
        wide_range = range_ratio > self.sc_atr_mult
        
        buyer_takeover = dataframe['close_position'] >= self.sc_close_pos_min
        
        # 统计各条件通过率
        cond1 = downtrend_exists.sum()
        cond2 = new_n_bar_low.sum()
        cond3 = high_volume.sum()
        cond4 = wide_range.sum()
        cond5 = buyer_takeover.sum()
        
        logger.info(f"  条件1 [趋势前提 跌幅>{self.sc_trend_min_drop*100:.0f}%]: {cond1}/{total} ({cond1/total*100:.2f}%)")
        logger.info(f"    └─ 实际跌幅范围: [{price_drop_ratio.min():.2%}, {price_drop_ratio.max():.2%}], 均值={price_drop_ratio.mean():.2%}")
        
        logger.info(f"  条件2 [新低突破 {self.sc_lookback}根]: {cond2}/{total} ({cond2/total*100:.2f}%)")
        
        logger.info(f"  条件3 [成交量>{self.sc_volume_mult}x均量]: {cond3}/{total} ({cond3/total*100:.2f}%)")
        logger.info(f"    └─ 实际倍数范围: [{volume_ratio.min():.2f}x, {volume_ratio.max():.2f}x], 均值={volume_ratio.mean():.2f}x")
        
        logger.info(f"  条件4 [宽幅>{self.sc_atr_mult}x ATR]: {cond4}/{total} ({cond4/total*100:.2f}%)")
        logger.info(f"    └─ 实际倍数范围: [{range_ratio.min():.2f}x, {range_ratio.max():.2f}x], 均值={range_ratio.mean():.2f}x")
        
        logger.info(f"  条件5 [收盘位置>={self.sc_close_pos_min*100:.0f}%]: {cond5}/{total} ({cond5/total*100:.2f}%)")
        
        # 逐步组合
        comb12 = (downtrend_exists & new_n_bar_low).sum()
        comb123 = (downtrend_exists & new_n_bar_low & high_volume).sum()
        comb1234 = (downtrend_exists & new_n_bar_low & high_volume & wide_range).sum()
        comb12345 = (downtrend_exists & new_n_bar_low & high_volume & wide_range & buyer_takeover).sum()
        
        logger.info(f"\n  逐步组合:")
        logger.info(f"    条件1∧2:         {comb12}/{total} ({comb12/total*100:.2f}%)")
        logger.info(f"    条件1∧2∧3:       {comb123}/{total} ({comb123/total*100:.2f}%)")
        logger.info(f"    条件1∧2∧3∧4:     {comb1234}/{total} ({comb1234/total*100:.2f}%)")
        logger.info(f"    条件1∧2∧3∧4∧5:   {comb12345}/{total} ({comb12345/total*100:.4f}%) ← SC最终结果")
        
        # ========================================================================
        # BC 诊断
        # ========================================================================
        logger.info("\n【BC (Buying Climax) 检测诊断】")
        
        prior_close_bc = dataframe['close'].shift(self.bc_trend_bars)
        price_rise_ratio = (dataframe['close'] - prior_close_bc) / prior_close_bc
        uptrend_exists = price_rise_ratio > self.bc_trend_min_rise
        
        rolling_high = dataframe['high'].rolling(self.bc_lookback).max().shift(1)
        new_n_bar_high = dataframe['high'] >= rolling_high
        
        seller_takeover = dataframe['close_position'] <= self.bc_close_pos_max
        
        bc_cond1 = uptrend_exists.sum()
        bc_cond2 = new_n_bar_high.sum()
        bc_cond3 = high_volume.sum()
        bc_cond4 = wide_range.sum()
        bc_cond5 = seller_takeover.sum()
        
        logger.info(f"  条件1 [趋势前提 涨幅>{self.bc_trend_min_rise*100:.0f}%]: {bc_cond1}/{total} ({bc_cond1/total*100:.2f}%)")
        logger.info(f"    └─ 实际涨幅范围: [{price_rise_ratio.min():.2%}, {price_rise_ratio.max():.2%}], 均值={price_rise_ratio.mean():.2%}")
        
        logger.info(f"  条件2 [新高突破 {self.bc_lookback}根]: {bc_cond2}/{total} ({bc_cond2/total*100:.2f}%)")
        logger.info(f"  条件3 [成交量>{self.bc_volume_mult}x均量]: {bc_cond3}/{total} ({bc_cond3/total*100:.2f}%)")
        logger.info(f"  条件4 [宽幅>{self.bc_atr_mult}x ATR]: {bc_cond4}/{total} ({bc_cond4/total*100:.2f}%)")
        logger.info(f"  条件5 [收盘位置<={self.bc_close_pos_max*100:.0f}%]: {bc_cond5}/{total} ({bc_cond5/total*100:.2f}%)")
        
        bc_comb12 = (uptrend_exists & new_n_bar_high).sum()
        bc_comb123 = (uptrend_exists & new_n_bar_high & high_volume).sum()
        bc_comb1234 = (uptrend_exists & new_n_bar_high & high_volume & wide_range).sum()
        bc_comb12345 = (uptrend_exists & new_n_bar_high & high_volume & wide_range & seller_takeover).sum()
        
        logger.info(f"\n  逐步组合:")
        logger.info(f"    条件1∧2:         {bc_comb12}/{total} ({bc_comb12/total*100:.2f}%)")
        logger.info(f"    条件1∧2∧3:       {bc_comb123}/{total} ({bc_comb123/total*100:.2f}%)")
        logger.info(f"    条件1∧2∧3∧4:     {bc_comb1234}/{total} ({bc_comb1234/total*100:.2f}%)")
        logger.info(f"    条件1∧2∧3∧4∧5:   {bc_comb12345}/{total} ({bc_comb12345/total*100:.4f}%) ← BC最终结果")
        
        logger.info("\n" + "=" * 80)
        logger.info("诊断报告结束")
        logger.info("=" * 80 + "\n")

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
