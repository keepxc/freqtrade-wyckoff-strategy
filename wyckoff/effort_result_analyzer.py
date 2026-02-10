"""
Effort-Result Analyzer - 威科夫努力结果分析器

实现威科夫第三定律：努力结果定律
- 努力（成交量）与结果（价格运动）应该和谐
- 背离表示供需关系发生变化
- 基于市场行为的物理分析，而非统计学方法

Milestone 1.2: Effort-Result Calculator
"""

import numpy as np
import pandas as pd
from pandas import DataFrame
from typing import List, Optional
import logging

logger = logging.getLogger(__name__)


class EffortResultAnalyzer:
    """
    努力结果分析器
    
    职责：
    1. 计算努力结果和谐性
    2. 检测威科夫背离
    3. 识别供应/需求出现证据
    4. 生成背离预警信号
    """
    
    def __init__(self):
        """初始化分析器"""
        pass
    
    def _calculate_effort_result_harmony(self, dataframe: DataFrame) -> DataFrame:
        """
        努力结果和谐性分析 - Milestone 1.2 基础实现
        
        实现威科夫第三定律：努力结果定律
        
        和谐模式：
        - 高努力高结果：健康趋势
        - 低努力低结果：正常整理
        
        背离模式：
        - 高努力低结果：遇到阻力
        - 低努力高结果：供应枯竭
        
        验收标准：
        - 正确识别和谐与背离波段
        - 轻松感计算准确反映市场阻力
        - 背离信号准确率 > 75%
        - 实时计算延迟 < 10ms
        """
        
        # 初始化列
        dataframe['effort_result_harmony'] = True
        dataframe['wyckoff_warning'] = False
        dataframe['ease_of_movement'] = 0.0
        dataframe['harmony_score'] = 0.0
        
        if len(dataframe) < 20:
            return dataframe
        
        # 基于K线的努力结果分析（波段级分析在后续版本实现）
        dataframe['bar_range'] = dataframe['high'] - dataframe['low']
        dataframe['close_position'] = (
            (dataframe['close'] - dataframe['low']) / (dataframe['bar_range'] + 1e-9)
        )
        
        # 计算努力（成交量相对强度）
        dataframe['effort'] = dataframe['volume'] / (dataframe['volume_sma_20'] + 1e-9)
        
        # 计算结果（价格运动幅度）
        dataframe['result'] = dataframe['bar_range'] / (dataframe['atr_20'] + 1e-9)
        
        # 和谐性判断：努力与结果应该匹配
        dataframe['effort_result_harmony'] = (
            (dataframe['effort'] > 1.2) & (dataframe['result'] > 1.0) |  # 高努力高结果：健康
            (dataframe['effort'] < 1.2) & (dataframe['result'] < 1.0)    # 低努力低结果：正常
        )
        
        # 威科夫警告信号：高努力低结果
        dataframe['wyckoff_warning'] = (
            (dataframe['effort'] > 1.5) &  # 高成交量
            (dataframe['result'] < 0.8) &  # 但价格区间小
            (dataframe['close_position'] < 0.6)  # 且收盘位置不佳
        )
        
        # 移动轻松感计算
        dataframe['ease_of_movement'] = dataframe['result'] / (dataframe['effort'] + 1e-9)
        
        # 和谐性评分 (-1 到 1，负值表示背离)
        effort_normalized = np.clip((dataframe['effort'] - 1.0) / 2.0, -1, 1)
        result_normalized = np.clip((dataframe['result'] - 1.0) / 2.0, -1, 1)
        
        # 和谐性：努力和结果方向一致时为正，背离时为负
        dataframe['harmony_score'] = np.where(
            (effort_normalized * result_normalized) >= 0,
            np.minimum(np.abs(effort_normalized), np.abs(result_normalized)),  # 和谐：取较小值
            -np.maximum(np.abs(effort_normalized), np.abs(result_normalized))  # 背离：取较大值的负数
        )
        
        return dataframe
    
    def _calculate_enhanced_effort_result_harmony(self, dataframe: DataFrame) -> DataFrame:
        """
        增强的努力结果和谐性计算
        
        Priority 3优化：集成真正的威科夫背离检测
        """
        
        # 先执行基础计算
        dataframe = self._calculate_effort_result_harmony(dataframe)
        
        # 添加增强的威科夫背离检测
        dataframe['true_wyckoff_divergence'] = self._detect_true_wyckoff_divergence(dataframe)
        dataframe['enhanced_down_wave'] = self._enhanced_down_wave_detection(dataframe)
        
        # 更新威科夫警告：集成真正的背离检测
        enhanced_warning = (
            dataframe['wyckoff_warning'] |  # 原有警告
            dataframe['true_wyckoff_divergence']  # 新增：真正的威科夫背离
        )
        
        dataframe['wyckoff_warning_enhanced'] = enhanced_warning
        
        # 更新和谐性：考虑增强的背离检测
        dataframe['effort_result_harmony_enhanced'] = (
            dataframe['effort_result_harmony'] &  # 原有和谐性
            ~dataframe['true_wyckoff_divergence']  # 排除真正的背离
        )
        
        return dataframe
    
    def _detect_true_wyckoff_divergence(self, dataframe: DataFrame) -> pd.Series:
        """
        基于威科夫努力结果定律的物理背离检测
        
        威科夫原理：
        - 努力（成交量）与结果（价格运动）应该和谐
        - 背离表示供需关系发生变化
        - 不是统计学的Z-Score，而是市场行为的物理分析
        
        替换Priority 3的统计学方法，用威科夫物理分析
        """
        
        # 确保有足够的数据
        if len(dataframe) < 30:
            return pd.Series(False, index=dataframe.index)
        
        # 威科夫背离检测：基于相对强度分析，不是统计学
        wyckoff_divergence = pd.Series(False, index=dataframe.index)
        
        for i in range(30, len(dataframe)):
            # 1. 威科夫努力分析（成交量相对强度）
            current_effort = dataframe['volume'].iloc[i]
            recent_avg_effort = dataframe['volume'].iloc[i-10:i].mean()
            effort_increasing = current_effort > recent_avg_effort * 1.8  # 努力显著增加
            
            # 2. 威科夫结果分析（价格运动相对强度）
            current_result = dataframe['high'].iloc[i] - dataframe['low'].iloc[i]
            recent_avg_result = (dataframe['high'].iloc[i-10:i] - dataframe['low'].iloc[i-10:i]).mean()
            result_decreasing = current_result < recent_avg_result * 0.6  # 结果显著减少
            
            # 3. 威科夫阶段确认：必须在上升趋势中才有意义
            price_trend = (dataframe['close'].iloc[i] - dataframe['close'].iloc[i-20]) / dataframe['close'].iloc[i-20]
            in_uptrend = price_trend > 0.01  # 在上升趋势中
            
            # 4. 威科夫供应出现证据
            supply_evidence = self._detect_supply_emergence_evidence(dataframe, i)
            
            # 威科夫背离确认：高努力 + 低结果 + 上升趋势 + 供应证据
            if effort_increasing and result_decreasing and in_uptrend and supply_evidence:
                wyckoff_divergence.iloc[i] = True
        
        return wyckoff_divergence
    
    def _detect_supply_emergence_evidence(self, dataframe: DataFrame, idx: int) -> bool:
        """
        检测供应出现的威科夫证据
        
        供应出现的物理特征：
        1. 上影线增长（遇到阻力）
        2. 收盘位置下移（卖压增加）
        3. 连续几根K线显示卖压
        """
        
        if idx < 5:
            return False
        
        # 1. 上影线增长（遇到阻力）
        current_upper_shadow = dataframe['high'].iloc[idx] - max(dataframe['open'].iloc[idx], dataframe['close'].iloc[idx])
        current_body = abs(dataframe['close'].iloc[idx] - dataframe['open'].iloc[idx])
        long_upper_shadow = current_upper_shadow > current_body * 0.5  # 上影线较长
        
        # 2. 收盘位置恶化
        if (dataframe['high'].iloc[idx] - dataframe['low'].iloc[idx]) > 0:
            close_position = (dataframe['close'].iloc[idx] - dataframe['low'].iloc[idx]) / (dataframe['high'].iloc[idx] - dataframe['low'].iloc[idx])
            poor_close = close_position < 0.4  # 收盘在下半部
        else:
            poor_close = False
        
        # 3. 连续卖压证据（最近3根K线中至少2根显示卖压）
        selling_pressure_count = 0
        for i in range(max(0, idx-2), idx+1):
            if i < len(dataframe):
                # 卖压特征：收盘低于开盘，或上影线长
                close_below_open = dataframe['close'].iloc[i] < dataframe['open'].iloc[i]
                upper_shadow = dataframe['high'].iloc[i] - max(dataframe['open'].iloc[i], dataframe['close'].iloc[i])
                body = abs(dataframe['close'].iloc[i] - dataframe['open'].iloc[i])
                long_shadow = upper_shadow > body * 0.3
                
                if close_below_open or long_shadow:
                    selling_pressure_count += 1
        
        continuous_selling = selling_pressure_count >= 2
        
        return long_upper_shadow and poor_close and continuous_selling
    
    def _enhanced_down_wave_detection(self, dataframe: DataFrame) -> pd.Series:
        """
        增强的下降波段检测：区分回调 vs 反转
        
        Priority 3目标：减少wyckoff_down_wave误触发
        原理：量化"显著增加"标准，区分普通回调和真正反转
        """
        
        # 基础下降波段
        is_wave_start = dataframe.get('is_wave_start', pd.Series(False, index=dataframe.index))
        wave_direction = dataframe.get('wave_direction', pd.Series('', index=dataframe.index))
        basic_down_wave = is_wave_start & (wave_direction == 'down')
        
        if basic_down_wave.sum() == 0:
            return pd.Series(False, index=dataframe.index)
        
        # 获取波段数据
        wave_displacement = dataframe.get('wave_displacement', pd.Series(0.0, index=dataframe.index))
        wave_effort = dataframe.get('wave_effort', dataframe.get('volume', pd.Series(0.0, index=dataframe.index)))
        
        # 1. 长度比较：当前 vs 前2-3个下降波段平均
        recent_down_displacements = []
        
        # 简化实现：使用滚动窗口计算历史下降波段的平均位移
        down_wave_mask = basic_down_wave
        historical_down_displacement = np.where(
            down_wave_mask,
            wave_displacement,
            np.nan
        )
        
        # 计算历史下降波段平均位移（向前填充）
        historical_avg_displacement = pd.Series(historical_down_displacement, index=dataframe.index).rolling(
            window=20, min_periods=3
        ).mean().fillna(method='ffill')
        
        # 当前下降波段位移显著增加
        displacement_increase = wave_displacement > historical_avg_displacement * 1.5
        
        # 2. 强度比较：当前下降波段成交量 vs 前几个上升波段平均
        up_wave_mask = is_wave_start & (wave_direction == 'up')
        historical_up_effort = np.where(
            up_wave_mask,
            wave_effort,
            np.nan
        )
        
        # 计算历史上升波段平均成交量
        historical_avg_up_effort = pd.Series(historical_up_effort, index=dataframe.index).rolling(
            window=20, min_periods=3
        ).mean().fillna(method='ffill')
        
        # 当前下降波段成交量超过上升波段
        effort_increase = wave_effort > historical_avg_up_effort * 1.2
        
        # 真正的反转信号：长度增加 + 强度增加
        true_reversal = basic_down_wave & displacement_increase & effort_increase
        
        return true_reversal
    
    def _detect_effort_result_divergence_warning(self, dataframe: DataFrame) -> pd.Series:
        """检测努力结果背离预警"""
        
        if len(dataframe) < 20:
            return pd.Series([False] * len(dataframe), index=dataframe.index)
        
        # 计算努力（成交量相对强度）
        effort_ratio = dataframe['volume'] / (dataframe['volume'].rolling(20).mean() + 1e-9)
        
        # 计算结果（价格运动效率）
        price_range = dataframe['high'] - dataframe['low']
        atr_20 = price_range.rolling(20).mean()
        result_ratio = price_range / (atr_20 + 1e-9)
        
        # 背离检测：高努力低结果
        high_effort = effort_ratio > 2.0  # 放宽条件：2.5 → 2.0
        low_result = result_ratio < 0.8   # 放宽条件：0.6 → 0.8
        
        # 收盘位置疲软
        close_position = (dataframe['close'] - dataframe['low']) / (price_range + 1e-9)
        weak_close = close_position < 0.4
        
        # 综合背离预警
        divergence_warning = high_effort & low_result & weak_close
        
        return divergence_warning
