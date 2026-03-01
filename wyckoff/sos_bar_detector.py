"""
SOS Bar 检测器 - 基于威科夫四大硬指标

Task 2.2 — SOS Bar 检测重构
实现日期: 2026-02-26

理论依据：
SOS (Sign of Strength) Bar 是威科夫理论中标志性的强势信号，必须满足四大硬指标：
1. 宽价差 (Wide Spread): 价格区间显著大于平均波动
2. 高成交量 (High Volume): 成交量显著高于平均水平
3. 高收盘 (Close Near High): 收盘价接近K线最高点
4. 价格承诺 (Price Commitment): 突破前期阻力位，显示真实的买盘力量

这四个指标缺一不可，共同确认买方主导地位。
"""

import logging
import numpy as np
import pandas as pd
from pandas import DataFrame

logger = logging.getLogger(__name__)


class SOSBarDetector:
    """
    SOS Bar 检测器
    
    基于威科夫四大硬指标识别 Sign of Strength (SOS) Bar
    """
    
    def __init__(self, strategy):
        """
        初始化 SOS Bar 检测器
        
        Args:
            strategy: 策略实例，用于访问参数
        """
        self.strategy = strategy
        
        # 四大硬指标阈值（可通过 Hyperopt 优化）
        # 这些是威科夫理论推荐的物理边界
        self.bar_range_multiplier = 1.5      # 宽价差：1.5x ATR
        self.volume_multiplier = 1.5         # 高成交量：1.5x SMA
        self.close_position_threshold = 0.7  # 高收盘：收在上30%
        
        # 质量评分权重（优化后）
        # 价格承诺和价差是最关键的指标，提高权重
        # 收盘位置虽然重要，但相对次要，降低权重
        self.weight_spread = 0.30               # 价差得分权重（+5%）
        self.weight_volume = 0.25               # 成交量得分权重（不变）
        self.weight_close_position = 0.15       # 收盘位置得分权重（-10%）
        self.weight_price_commitment = 0.30     # 价格承诺得分权重（+5%）
        
        logger.info(f"SOSBarDetector 初始化完成 (四大硬指标: spread={self.bar_range_multiplier}x, "
                   f"volume={self.volume_multiplier}x, close_pos={self.close_position_threshold}, "
                   f"price_commitment=突破阻力)")
        logger.info(f"质量评分权重: spread={self.weight_spread:.0%}, volume={self.weight_volume:.0%}, "
                   f"close_pos={self.weight_close_position:.0%}, commitment={self.weight_price_commitment:.0%}")
    
    def detect_sos_bars(self, dataframe: DataFrame) -> DataFrame:
        """
        检测 SOS Bar 并计算质量评分
        
        Args:
            dataframe: 包含价格和成交量数据的 DataFrame
            
        Returns:
            添加了 SOS 检测字段的 DataFrame
            
        新增字段:
            - sos_bar_detected: bool，是否为 SOS Bar
            - sos_quality_score: float (0-100)，SOS 质量评分
            - sos_spread_score: float (0-1)，价差得分
            - sos_volume_score: float (0-1)，成交量得分
            - sos_close_position_score: float (0-1)，收盘位置得分
            - sos_price_commitment_score: float (0-1)，价格承诺得分
        """
        
        # 初始化字段
        dataframe['sos_bar_detected'] = False
        dataframe['sos_quality_score'] = 0.0
        dataframe['sos_spread_score'] = 0.0
        dataframe['sos_volume_score'] = 0.0
        dataframe['sos_close_position_score'] = 0.0
        dataframe['sos_price_commitment_score'] = 0.0
        
        # 确保必要字段存在
        if 'atr' not in dataframe.columns or 'volume_sma_20' not in dataframe.columns:
            logger.warning("⚠️ 缺少必要字段 (atr 或 volume_sma_20)，跳过 SOS 检测")
            return dataframe
        
        if 'nearest_resistance' not in dataframe.columns:
            logger.warning("⚠️ 缺少 nearest_resistance 字段，跳过 SOS 检测")
            return dataframe
        
        # === 1. 计算基础指标 ===
        
        # K线价格区间
        dataframe['bar_range'] = dataframe['high'] - dataframe['low']
        
        # 收盘位置（0=最低点，1=最高点）
        dataframe['close_position'] = np.where(
            dataframe['bar_range'] > 0,
            (dataframe['close'] - dataframe['low']) / dataframe['bar_range'],
            0.5  # 如果区间为0，默认中间位置
        )
        
        # === 2. 四大硬指标验证 ===
        
        # 指标1: 宽价差 (Wide Spread)
        spread_condition = dataframe['bar_range'] > (dataframe['atr'] * self.bar_range_multiplier)
        
        # 指标2: 高成交量 (High Volume)
        volume_condition = dataframe['volume'] > (dataframe['volume_sma_20'] * self.volume_multiplier)
        
        # 指标3: 高收盘 (Close Near High)
        close_position_condition = dataframe['close_position'] > self.close_position_threshold
        
        # 指标4: 价格承诺 (Price Commitment) - 突破前期阻力
        price_commitment_condition = dataframe['close'] > dataframe['nearest_resistance']
        
        # === 3. SOS Bar 识别（四大指标必须全部满足）===
        
        sos_detected = (
            spread_condition &
            volume_condition &
            close_position_condition &
            price_commitment_condition
        )
        
        dataframe.loc[sos_detected, 'sos_bar_detected'] = True
        
        # === 4. 质量评分计算（0-100）===
        
        # 只对检测到的 SOS Bar 计算评分
        sos_indices = dataframe[sos_detected].index
        
        if len(sos_indices) > 0:
            # 4.1 价差得分（归一化到 0-1）
            # 超过阈值越多，得分越高，最高1.0
            spread_ratio = dataframe.loc[sos_indices, 'bar_range'] / (dataframe.loc[sos_indices, 'atr'] * self.bar_range_multiplier)
            dataframe.loc[sos_indices, 'sos_spread_score'] = np.minimum(
                (spread_ratio - 1.0) / 1.0 + 0.5,  # 1.0x=0.5分，2.0x=1.0分
                1.0
            )
            
            # 4.2 成交量得分（归一化到 0-1）
            volume_ratio = dataframe.loc[sos_indices, 'volume'] / (dataframe.loc[sos_indices, 'volume_sma_20'] * self.volume_multiplier)
            dataframe.loc[sos_indices, 'sos_volume_score'] = np.minimum(
                (volume_ratio - 1.0) / 1.0 + 0.5,  # 1.0x=0.5分，2.0x=1.0分
                1.0
            )
            
            # 4.3 收盘位置得分（归一化到 0-1）
            # 0.7=0.5分，1.0=1.0分
            close_pos_normalized = (dataframe.loc[sos_indices, 'close_position'] - self.close_position_threshold) / (1.0 - self.close_position_threshold)
            dataframe.loc[sos_indices, 'sos_close_position_score'] = np.minimum(
                close_pos_normalized * 0.5 + 0.5,
                1.0
            )
            
            # 4.4 价格承诺得分（归一化到 0-1）
            # 突破阻力位越多，得分越高
            resistance_breakthrough = (dataframe.loc[sos_indices, 'close'] - dataframe.loc[sos_indices, 'nearest_resistance']) / dataframe.loc[sos_indices, 'nearest_resistance']
            dataframe.loc[sos_indices, 'sos_price_commitment_score'] = np.minimum(
                resistance_breakthrough * 10 + 0.5,  # 突破0%=0.5分，突破5%=1.0分
                1.0
            )
            
            # 4.5 综合质量评分（加权平均，转换为 0-100）
            dataframe.loc[sos_indices, 'sos_quality_score'] = (
                dataframe.loc[sos_indices, 'sos_spread_score'] * self.weight_spread +
                dataframe.loc[sos_indices, 'sos_volume_score'] * self.weight_volume +
                dataframe.loc[sos_indices, 'sos_close_position_score'] * self.weight_close_position +
                dataframe.loc[sos_indices, 'sos_price_commitment_score'] * self.weight_price_commitment
            ) * 100
        
        # === 5. 统计日志 ===
        
        sos_count = dataframe['sos_bar_detected'].sum()
        if sos_count > 0:
            avg_quality = dataframe.loc[dataframe['sos_bar_detected'], 'sos_quality_score'].mean()
            logger.info(f"SOS Bar 检测完成: 发现 {sos_count} 个 SOS Bar，平均质量评分 {avg_quality:.1f}")
        else:
            logger.debug("SOS Bar 检测完成: 未发现符合四大硬指标的 SOS Bar")
        
        return dataframe
    
    def get_sos_signal_conditions(self, dataframe: DataFrame) -> pd.Series:
        """
        获取 SOS 入场信号条件
        
        用于策略的 populate_entry_trend 方法
        
        Args:
            dataframe: 包含 SOS 检测字段的 DataFrame
            
        Returns:
            bool Series，True 表示满足 SOS 入场条件
        """
        
        # 基础条件：检测到 SOS Bar
        sos_condition = dataframe['sos_bar_detected']
        
        # 质量过滤：只接受高质量 SOS（评分 > 60）
        quality_condition = dataframe['sos_quality_score'] > 60
        
        # 组合条件
        return sos_condition & quality_condition
    
    def validate_lps_with_sos(self, dataframe: DataFrame, lookback: int = 5) -> DataFrame:
        """
        使用 SOS 验证 LPS (Last Point of Support)
        
        LPS 应该在 SOS 之后出现，作为回测确认
        
        Args:
            dataframe: DataFrame
            lookback: 向前查找 SOS 的K线数量
            
        Returns:
            添加了 lps_sos_confirmed 字段的 DataFrame
        """
        
        dataframe['lps_sos_confirmed'] = False
        
        # 检查前 lookback 根K线内是否有 SOS
        for i in range(1, lookback + 1):
            sos_in_past = dataframe['sos_bar_detected'].shift(i).fillna(False)
            dataframe['lps_sos_confirmed'] = dataframe['lps_sos_confirmed'] | sos_in_past
        
        lps_confirmed_count = dataframe['lps_sos_confirmed'].sum()
        logger.debug(f"LPS-SOS 确认: {lps_confirmed_count} 个 LPS 在 SOS 后出现")
        
        return dataframe
