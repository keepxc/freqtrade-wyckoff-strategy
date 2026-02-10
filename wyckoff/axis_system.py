"""
威科夫轴线系统模块 - 从主策略提取

包含所有轴线相关的数据结构和方法：
1. AxisLine, TestResult, ValidationResult 数据结构
2. _identify_axis_lines 及其辅助方法
3. _classify_axis_tests 及其辅助方法
4. _integrate_multi_timeframe_validation 及其辅助方法

从 WyckoffV7_TrueMethod.py 提取
日期：2026-02-08
"""

from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict, Optional, Tuple
import pandas as pd
import numpy as np
from pandas import DataFrame
import logging

logger = logging.getLogger(__name__)


# ============================================================================
# Phase 2: Axis/Ice Line System - 核心数据结构
# ============================================================================

@dataclass
class AxisLine:
    """
    威科夫轴线 - 动态支撑阻力线
    替代EMA技术指标，基于真实的市场结构
    
    Milestone 3.1: Dynamic Axis Line Identification
    """
    price_level: float
    formation_time: datetime
    formation_idx: int
    test_count: int
    volume_density: float
    line_type: str  # 'support', 'resistance', 'ice'
    importance: str  # 'major', 'minor'
    last_test_time: datetime
    last_test_idx: int
    strength_score: float  # 0-1, 轴线强度评分
    
    def is_valid(self, current_time: datetime, max_age_hours: int = 168) -> bool:
        """轴线是否仍然有效（默认7天内）"""
        age_hours = (current_time - self.formation_time).total_seconds() / 3600
        return age_hours < max_age_hours and self.test_count >= 2
    
    def distance_from_price(self, price: float) -> float:
        """价格距离轴线的百分比距离"""
        return abs(price - self.price_level) / (self.price_level + 1e-9)

@dataclass
class TestResult:
    """
    轴线测试结果
    
    Milestone 3.2: Support/Resistance Test Classification
    """
    axis_line: AxisLine
    test_type: str  # 'normal', 'genuine_break', 'spring', 'ice_formation'
    test_time: datetime
    test_idx: int
    test_price: float
    volume_ratio: float  # 相对平均成交量的倍数
    range_ratio: float   # 相对ATR的倍数
    close_position: float  # 收盘位置 0-1
    confirmation_status: str  # 'pending', 'confirmed', 'rejected'
    confidence_score: float  # 0-1

@dataclass
class ValidationResult:
    """
    多时间框架验证结果
    
    Milestone 3.3: Multi-Timeframe Integration
    """
    is_valid: bool
    confidence_score: float
    conflicting_timeframes: List[str]
    recommended_action: str  # 'buy', 'sell', 'hold', 'exit'
    risk_level: str  # 'low', 'medium', 'high'
    supporting_evidence: List[str]


# ============================================================================
# 轴线系统类 - 包含所有轴线相关方法
# ============================================================================

class AxisSystem:
    """
    威科夫轴线系统
    
    包含从主策略提取的所有轴线相关方法
    """
    
    def __init__(self, strategy_instance):
        """
        初始化轴线系统
        
        Args:
            strategy_instance: 主策略实例，用于访问参数和状态
        """
        self.strategy = strategy_instance
        self.axis_lines: List[AxisLine] = []
        self.test_results: List[TestResult] = []
    
    # ========================================================================
    # Milestone 3.1: Dynamic Axis Line Identification
    # ========================================================================
    
    def identify_axis_lines(self, dataframe: DataFrame) -> DataFrame:
        """
        动态轴线识别 - Milestone 3.1 实现
        
        替代EMA技术指标，使用真正的威科夫支撑阻力：
        1. 识别重要的前期高低点
        2. 基于成交量密度确认轴线
        3. 分类轴线重要性（Major/Minor）
        4. 动态更新轴线状态
        
        验收标准：
        - 识别准确率 > 85%
        - 轴线数量合理（5-15条活跃轴线）
        - 动态更新延迟 < 50ms
        - 与历史重要点位一致性 > 90%
        """
        
        # 初始化轴线相关列
        dataframe = self._initialize_axis_columns(dataframe)
        
        if len(dataframe) < 50:
            return dataframe
        
        # 1. 识别潜在轴线价位
        potential_levels = self._find_potential_axis_levels(dataframe)
        
        # 调试输出
        if len(potential_levels) > 0:
            logger.info(f"Phase 2 轴线识别: 发现 {len(potential_levels)} 个潜在轴线")
        
        # 2. 验证轴线有效性
        validated_axes = self._validate_axis_significance(potential_levels, dataframe)
        
        # 3. 分类轴线重要性
        classified_axes = self._classify_axis_importance(validated_axes, dataframe)
        
        # 4. 更新轴线缓存
        self._update_axis_cache(classified_axes)
        
        # 调试输出
        if len(self.axis_lines) > 0:
            logger.info(f"Phase 2 轴线系统: 活跃轴线 {len(self.axis_lines)} 条")
        
        # 5. 将轴线信息映射到dataframe
        dataframe = self._map_axes_to_dataframe(dataframe)
        
        return dataframe
    
    def _initialize_axis_columns(self, dataframe: DataFrame) -> DataFrame:
        """
        初始化轴线相关列
        
        重要修复（2026-02-09）：
        使用50周期低点作为默认支撑位（兼容模式后备）
        这确保即使轴线识别失败，Spring 检测仍然可以工作
        """
        # 使用50周期低点作为默认支撑位（shift(1)避免未来数据泄露）
        dataframe['nearest_support'] = dataframe['low'].rolling(50).min().shift(1)
        dataframe['nearest_resistance'] = dataframe['high'].rolling(50).max().shift(1)
        
        # 计算默认距离
        dataframe['support_distance'] = abs(dataframe['close'] - dataframe['nearest_support']) / (dataframe['close'] + 1e-9)
        dataframe['resistance_distance'] = abs(dataframe['close'] - dataframe['nearest_resistance']) / (dataframe['close'] + 1e-9)
        
        # 默认强度
        dataframe['support_strength'] = 0.5
        dataframe['resistance_strength'] = 0.5
        
        # 其他默认值
        dataframe['axis_test_type'] = 'none'
        dataframe['test_confidence'] = 0.0
        dataframe['dynamic_distance_threshold'] = 0.02  # 默认2% ATR
        dataframe['ice_line_active'] = False
        dataframe['axis_break_confirmed'] = False
        
        return dataframe

    # ========================================================================
    # Phase 2.1: Axis Strength Tier System
    # ========================================================================
    
    def calculate_axis_strength_tier(self, dataframe: DataFrame) -> DataFrame:
        """
        计算轴线强度分级 - Phase 2.1 新增
        
        威科夫理论：测试次数 = 因果积累 = 市场共识强度
        
        分级标准：
        - Strong: test_count >= 3 或 strength_score >= 0.7 或 absorption测试
        - Medium: test_count == 2 且 strength_score >= 0.4
        - Weak: 其他情况
        
        Args:
            dataframe: 包含轴线信息的DataFrame
            
        Returns:
            DataFrame: 添加了 axis_strength_tier 列
        """
        # 初始化列
        dataframe['axis_strength_tier'] = 'weak'
        dataframe['support_test_count'] = 0
        dataframe['resistance_test_count'] = 0
        
        if len(dataframe) == 0 or not self.axis_lines:
            return dataframe
        
        # 从 axis_lines 中提取 test_count 并映射到 dataframe
        for i in range(len(dataframe)):
            current_price = dataframe.iloc[i]['close']
            
            support_test_count = 0
            resistance_test_count = 0
            
            # 找到最近的支撑和阻力的 test_count
            for axis in self.axis_lines:
                distance = axis.distance_from_price(current_price)
                
                if axis.line_type == 'support':
                    if distance < dataframe.iloc[i]['support_distance']:
                        support_test_count = axis.test_count
                
                elif axis.line_type == 'resistance':
                    if distance < dataframe.iloc[i]['resistance_distance']:
                        resistance_test_count = axis.test_count
            
            # 更新 dataframe
            dataframe.iloc[i, dataframe.columns.get_loc('support_test_count')] = support_test_count
            dataframe.iloc[i, dataframe.columns.get_loc('resistance_test_count')] = resistance_test_count
        
        # 强轴线条件（任一满足即可）
        strong_conditions = (
            (dataframe['support_test_count'] >= 3) |
            (dataframe['resistance_test_count'] >= 3) |
            (dataframe['support_strength'] >= 0.7) |
            (dataframe['resistance_strength'] >= 0.7) |
            (dataframe['axis_test_type'] == 'absorption')
        )
        dataframe.loc[strong_conditions, 'axis_strength_tier'] = 'strong'
        
        # 中等轴线条件（必须同时满足）
        medium_conditions = (
            (
                (dataframe['support_test_count'] == 2) |
                (dataframe['resistance_test_count'] == 2)
            ) &
            (
                (dataframe['support_strength'] >= 0.4) |
                (dataframe['resistance_strength'] >= 0.4)
            ) &
            ~strong_conditions  # 排除已经是强轴线的
        )
        dataframe.loc[medium_conditions, 'axis_strength_tier'] = 'medium'
        
        # 其他情况保持默认值 'weak'（已在初始化时设置）
        
        # 调试输出
        strong_count = (dataframe['axis_strength_tier'] == 'strong').sum()
        medium_count = (dataframe['axis_strength_tier'] == 'medium').sum()
        weak_count = (dataframe['axis_strength_tier'] == 'weak').sum()
        
        if strong_count > 0 or medium_count > 0:
            logger.info(f"Phase 2.1 轴线分级: Strong={strong_count}, Medium={medium_count}, Weak={weak_count}")
        
        return dataframe

    def _find_potential_axis_levels(self, dataframe: DataFrame) -> List[Dict]:
        """
        寻找潜在的轴线价位 - Milestone 3.1 完整实现
        
        基于以下条件识别：
        1. 重要的高低点（基于波段分析）
        2. 成交量密集区域
        3. 多次测试的价位
        4. 时间跨度足够的价位
        """
        potential_levels = []
        
        if len(dataframe) < 50:
            return potential_levels
        
        # 1. 基于波段高低点识别轴线
        if hasattr(self.strategy, 'waves_cache') and self.strategy.waves_cache:
            # 收集所有波段的高低点
            wave_highs = []
            wave_lows = []
            
            for wave in self.strategy.waves_cache[-20:]:  # 最近20个波段
                wave_highs.append({
                    'price': wave.high_price,
                    'idx': wave.end_idx,
                    'volume': wave.effort,
                    'type': 'resistance'
                })
                wave_lows.append({
                    'price': wave.low_price,
                    'idx': wave.start_idx,
                    'volume': wave.effort,
                    'type': 'support'
                })
            
            # 合并所有价位
            all_levels = wave_highs + wave_lows
            
            # 按价格聚类，识别重复测试的价位
            clustered_levels = self._cluster_price_levels(all_levels)
            potential_levels.extend(clustered_levels)
        
        # 2. 基于成交量密集区域识别轴线
        volume_levels = self._identify_volume_density_levels(dataframe)
        potential_levels.extend(volume_levels)
        
        # 3. 基于历史高低点识别轴线
        historical_levels = self._identify_historical_levels(dataframe)
        potential_levels.extend(historical_levels)
        
        # 4. 去重和排序
        potential_levels = self._deduplicate_levels(potential_levels)
        
        return potential_levels
    
    def _cluster_price_levels(self, levels: List[Dict]) -> List[Dict]:
        """
        将相近的价位聚类，识别重复测试的轴线
        """
        if not levels:
            return []
        
        clustered = []
        tolerance = 0.02  # 2%的价格容差
        
        # 按价格排序
        sorted_levels = sorted(levels, key=lambda x: x['price'])
        
        current_cluster = [sorted_levels[0]]
        
        for level in sorted_levels[1:]:
            # 检查是否属于当前聚类
            cluster_center = np.mean([l['price'] for l in current_cluster])
            price_diff = abs(level['price'] - cluster_center) / cluster_center
            
            if price_diff <= tolerance:
                current_cluster.append(level)
            else:
                # 完成当前聚类
                if len(current_cluster) >= 2:  # 至少2次测试才形成轴线
                    clustered_level = self._create_clustered_level(current_cluster)
                    clustered.append(clustered_level)
                
                # 开始新聚类
                current_cluster = [level]
        
        # 处理最后一个聚类
        if len(current_cluster) >= 2:
            clustered_level = self._create_clustered_level(current_cluster)
            clustered.append(clustered_level)
        
        return clustered
    
    def _create_clustered_level(self, cluster: List[Dict]) -> Dict:
        """
        从聚类创建轴线
        """
        prices = [l['price'] for l in cluster]
        volumes = [l['volume'] for l in cluster]
        indices = [l['idx'] for l in cluster]
        
        # 计算轴线属性
        avg_price = np.mean(prices)
        total_volume = np.sum(volumes)
        test_count = len(cluster)
        formation_idx = min(indices)
        last_test_idx = max(indices)
        
        # 确定轴线类型（支撑或阻力）
        support_count = sum(1 for l in cluster if l['type'] == 'support')
        resistance_count = sum(1 for l in cluster if l['type'] == 'resistance')
        line_type = 'support' if support_count > resistance_count else 'resistance'
        
        # 计算重要性
        importance = 'major' if test_count >= 3 and total_volume > 1000000 else 'minor'
        
        # 计算强度评分
        strength_score = min(1.0, (test_count * 0.2 + total_volume / 5000000) * 0.5)
        
        return {
            'price': avg_price,
            'formation_idx': formation_idx,
            'last_test_idx': last_test_idx,
            'test_count': test_count,
            'volume_density': total_volume,
            'line_type': line_type,
            'importance': importance,
            'strength_score': strength_score
        }
    
    def _identify_volume_density_levels(self, dataframe: DataFrame) -> List[Dict]:
        """
        基于成交量密集区域识别轴线
        """
        levels = []
        
        if len(dataframe) < 100:
            return levels
        
        # 计算价格区间的成交量密度
        recent_data = dataframe.tail(200)  # 最近200根K线
        
        # 将价格分成若干区间
        price_min = recent_data['low'].min()
        price_max = recent_data['high'].max()
        price_range = price_max - price_min
        
        if price_range <= 0:
            return levels
        
        # 分成20个价格区间
        num_bins = 20
        bin_size = price_range / num_bins
        
        volume_density = {}
        
        for _, row in recent_data.iterrows():
            # 计算每个K线在各价格区间的成交量分布
            bar_low = row['low']
            bar_high = row['high']
            bar_volume = row['volume']
            
            # 简化：假设成交量在K线范围内均匀分布
            bar_range = bar_high - bar_low
            if bar_range > 0:
                volume_per_price = bar_volume / bar_range
                
                # 分配到相应的价格区间
                start_bin = int((bar_low - price_min) / bin_size)
                end_bin = int((bar_high - price_min) / bin_size)
                
                for bin_idx in range(max(0, start_bin), min(num_bins, end_bin + 1)):
                    bin_price = price_min + (bin_idx + 0.5) * bin_size
                    if bin_price not in volume_density:
                        volume_density[bin_price] = 0
                    volume_density[bin_price] += volume_per_price * bin_size
        
        # 识别成交量密集区域
        if volume_density:
            avg_volume = np.mean(list(volume_density.values()))
            
            for price, volume in volume_density.items():
                if volume > avg_volume * 2.0:  # 成交量超过平均值2倍
                    levels.append({
                        'price': price,
                        'formation_idx': len(dataframe) - 200,
                        'last_test_idx': len(dataframe) - 1,
                        'test_count': 1,
                        'volume_density': volume,
                        'line_type': 'support',  # 默认为支撑
                        'importance': 'minor',
                        'strength_score': min(1.0, volume / (avg_volume * 5))
                    })
        
        return levels
    
    def _identify_historical_levels(self, dataframe: DataFrame) -> List[Dict]:
        """
        基于历史重要高低点识别轴线
        """
        levels = []
        
        if len(dataframe) < 50:
            return levels
        
        # 识别显著的高低点
        lookback = 50
        recent_data = dataframe.tail(lookback)
        
        # 寻找局部高点
        for i in range(5, len(recent_data) - 5):
            current_high = recent_data.iloc[i]['high']
            
            # 检查是否为局部高点
            is_local_high = True
            for j in range(i - 5, i + 6):
                if j != i and recent_data.iloc[j]['high'] >= current_high:
                    is_local_high = False
                    break
            
            if is_local_high:
                levels.append({
                    'price': current_high,
                    'formation_idx': len(dataframe) - lookback + i,
                    'last_test_idx': len(dataframe) - lookback + i,
                    'test_count': 1,
                    'volume_density': recent_data.iloc[i]['volume'],
                    'line_type': 'resistance',
                    'importance': 'minor',
                    'strength_score': 0.3
                })
        
        # 寻找局部低点
        for i in range(5, len(recent_data) - 5):
            current_low = recent_data.iloc[i]['low']
            
            # 检查是否为局部低点
            is_local_low = True
            for j in range(i - 5, i + 6):
                if j != i and recent_data.iloc[j]['low'] <= current_low:
                    is_local_low = False
                    break
            
            if is_local_low:
                levels.append({
                    'price': current_low,
                    'formation_idx': len(dataframe) - lookback + i,
                    'last_test_idx': len(dataframe) - lookback + i,
                    'test_count': 1,
                    'volume_density': recent_data.iloc[i]['volume'],
                    'line_type': 'support',
                    'importance': 'minor',
                    'strength_score': 0.3
                })
        
        return levels
    
    def _deduplicate_levels(self, levels: List[Dict]) -> List[Dict]:
        """
        去除重复的轴线
        """
        if not levels:
            return []
        
        # 按价格排序
        sorted_levels = sorted(levels, key=lambda x: x['price'])
        
        deduplicated = []
        tolerance = 0.015  # 1.5%的价格容差
        
        for level in sorted_levels:
            # 检查是否与已有轴线重复
            is_duplicate = False
            
            for existing in deduplicated:
                price_diff = abs(level['price'] - existing['price']) / existing['price']
                if price_diff <= tolerance:
                    # 合并到现有轴线（选择强度更高的）
                    if level['strength_score'] > existing['strength_score']:
                        deduplicated.remove(existing)
                        deduplicated.append(level)
                    is_duplicate = True
                    break
            
            if not is_duplicate:
                deduplicated.append(level)
        
        # 按强度评分排序，保留最重要的轴线
        deduplicated.sort(key=lambda x: x['strength_score'], reverse=True)
        
        # 最多保留10条轴线
        return deduplicated[:10]
    
    def _validate_axis_significance(self, potential_levels: List[Dict], dataframe: DataFrame) -> List[Dict]:
        """
        验证轴线的重要性
        """
        validated = []
        
        for level in potential_levels:
            # 验证条件：
            # 1. 测试次数 >= 2
            # 2. 成交量密度足够
            # 3. 时间跨度合理
            
            if level['test_count'] >= 2:
                validated.append(level)
            elif level['volume_density'] > 1000000:  # 高成交量密度
                validated.append(level)
        
        return validated
    
    def _classify_axis_importance(self, validated_axes: List[Dict], dataframe: DataFrame) -> List[AxisLine]:
        """
        分类轴线重要性
        """
        classified = []
        
        for axis_dict in validated_axes:
            # 创建 AxisLine 对象
            # 使用 pd.Timestamp 确保时区一致性
            if 'date' in dataframe.columns:
                formation_time = pd.Timestamp(dataframe.iloc[axis_dict['formation_idx']]['date'])
                last_test_time = pd.Timestamp(dataframe.iloc[axis_dict['last_test_idx']]['date'])
            else:
                formation_time = pd.Timestamp.now(tz='UTC')
                last_test_time = pd.Timestamp.now(tz='UTC')
            
            # 转换为 naive datetime（移除时区信息）
            if hasattr(formation_time, 'tz_localize'):
                formation_time = formation_time.tz_localize(None) if formation_time.tz is None else formation_time.tz_convert(None).replace(tzinfo=None)
            if hasattr(last_test_time, 'tz_localize'):
                last_test_time = last_test_time.tz_localize(None) if last_test_time.tz is None else last_test_time.tz_convert(None).replace(tzinfo=None)
            
            axis_line = AxisLine(
                price_level=axis_dict['price'],
                formation_time=formation_time.to_pydatetime() if hasattr(formation_time, 'to_pydatetime') else formation_time,
                formation_idx=axis_dict['formation_idx'],
                test_count=axis_dict['test_count'],
                volume_density=axis_dict['volume_density'],
                line_type=axis_dict['line_type'],
                importance=axis_dict['importance'],
                last_test_time=last_test_time.to_pydatetime() if hasattr(last_test_time, 'to_pydatetime') else last_test_time,
                last_test_idx=axis_dict['last_test_idx'],
                strength_score=axis_dict['strength_score']
            )
            
            classified.append(axis_line)
        
        return classified
    
    def _update_axis_cache(self, classified_axes: List[AxisLine]):
        """
        更新轴线缓存
        """
        # 合并新轴线到现有缓存
        for new_axis in classified_axes:
            # 检查是否已存在相似轴线
            found_existing = False
            
            for i, existing_axis in enumerate(self.axis_lines):
                if existing_axis.distance_from_price(new_axis.price_level) < 0.015:  # 1.5%容差
                    # 更新现有轴线
                    if new_axis.strength_score > existing_axis.strength_score:
                        self.axis_lines[i] = new_axis
                    found_existing = True
                    break
            
            if not found_existing:
                self.axis_lines.append(new_axis)
        
        # 清理过期轴线
        current_time = datetime.now()
        self.axis_lines = [axis for axis in self.axis_lines if axis.is_valid(current_time)]
        
        # 限制轴线数量
        self.axis_lines.sort(key=lambda x: x.strength_score, reverse=True)
        self.axis_lines = self.axis_lines[:15]
    
    def _map_axes_to_dataframe(self, dataframe: DataFrame) -> DataFrame:
        """
        将轴线信息映射到dataframe
        
        重要修复（2026-02-09）：
        1. 支撑位不再要求必须在价格下方（允许检测 Spring）
        2. 使用前一根K线的价格（shift(1)）来避免未来数据泄露
        """
        if not self.axis_lines:
            return dataframe
        
        # 使用前一根K线的收盘价（避免未来数据泄露）
        prev_close = dataframe['close'].shift(1)
        
        for i in range(len(dataframe)):
            if i == 0:
                # 第一根K线没有前一根，跳过
                continue
                
            current_price = prev_close.iloc[i]
            
            if pd.isna(current_price):
                continue
            
            # 找到最近的支撑和阻力
            nearest_support = None
            nearest_resistance = None
            min_support_distance = float('inf')
            min_resistance_distance = float('inf')
            
            for axis in self.axis_lines:
                # 只考虑在当前K线之前形成的轴线
                if axis.last_test_idx >= i:
                    continue
                
                distance = axis.distance_from_price(current_price)
                
                # 修复：支撑位不再要求必须在价格下方
                # 这样可以检测到 Spring（价格跌破支撑后的情况）
                if axis.line_type == 'support':
                    if distance < min_support_distance:
                        min_support_distance = distance
                        nearest_support = axis
                
                # 阻力位不再要求必须在价格上方
                elif axis.line_type == 'resistance':
                    if distance < min_resistance_distance:
                        min_resistance_distance = distance
                        nearest_resistance = axis
            
            # 更新dataframe
            if nearest_support:
                dataframe.iloc[i, dataframe.columns.get_loc('nearest_support')] = nearest_support.price_level
                dataframe.iloc[i, dataframe.columns.get_loc('support_distance')] = min_support_distance
                dataframe.iloc[i, dataframe.columns.get_loc('support_strength')] = nearest_support.strength_score
            
            if nearest_resistance:
                dataframe.iloc[i, dataframe.columns.get_loc('nearest_resistance')] = nearest_resistance.price_level
                dataframe.iloc[i, dataframe.columns.get_loc('resistance_distance')] = min_resistance_distance
                dataframe.iloc[i, dataframe.columns.get_loc('resistance_strength')] = nearest_resistance.strength_score
        
        return dataframe

    # ========================================================================
    # Milestone 3.2: Support/Resistance Test Classification
    # ========================================================================
    
    def _classify_axis_tests(self, dataframe: DataFrame) -> DataFrame:
            """
            轴线测试分类 - Milestone 3.2 实现

            根据威科夫理论，将轴线测试分为以下类型：
            1. Normal Test（正常测试）：成交量萎缩 + 价差收窄 = 缺乏兴趣
            2. Absorption（吸收）：高努力 + 小结果 = 大资金接盘
            3. Spring（弹簧）：跌破后快速收回 = 终端震仓
            4. Genuine Break（真实突破）：放量 + 宽幅 = 移动轻松感

            理论依据：
            - Normal Test: 成交量低于前2根K线，价差收窄，代表供应枯竭
            - Absorption: 成交量大但价格跌不动，收盘位在中位以上，代表机构接盘
            - Spring: 跌破支撑后快速收回，代表最后的洗盘

            参考：威科夫交易方法完整学习指南.md (3598-4400行)
            """

            # 初始化测试分类列
            dataframe['axis_test_type'] = 'none'
            dataframe['test_confidence'] = 0.0
            dataframe['ice_line_formed'] = False
            dataframe['spring_detected'] = False

            if len(dataframe) < 3:
                return dataframe

            # 计算必要的辅助指标
            # 价差（Spread）= 高低点差
            dataframe['spread'] = dataframe['high'] - dataframe['low']
            dataframe['spread_avg'] = dataframe['spread'].rolling(20).mean()

            # 收盘位置（Position of Close）：0-1，0=最低点，1=最高点
            dataframe['close_position'] = (dataframe['close'] - dataframe['low']) / (dataframe['spread'] + 1e-9)

            # 成交量比率
            dataframe['volume_ratio'] = dataframe['volume'] / (dataframe['volume'].rolling(20).mean() + 1e-9)

            # 前2根K线的平均成交量
            dataframe['prev_2_volume_avg'] = (dataframe['volume'].shift(1) + dataframe['volume'].shift(2)) / 2

            # 遍历每根K线，分类轴线测试
            for i in range(2, len(dataframe)):
                # 跳过没有支撑/阻力的K线
                if dataframe.iloc[i]['nearest_support'] <= 0 and dataframe.iloc[i]['nearest_resistance'] <= 0:
                    continue

                current_close = dataframe.iloc[i]['close']
                current_low = dataframe.iloc[i]['low']
                current_high = dataframe.iloc[i]['high']
                current_volume = dataframe.iloc[i]['volume']
                current_spread = dataframe.iloc[i]['spread']
                current_close_pos = dataframe.iloc[i]['close_position']

                support = dataframe.iloc[i]['nearest_support']
                resistance = dataframe.iloc[i]['nearest_resistance']
                support_dist = dataframe.iloc[i]['support_distance']
                resistance_dist = dataframe.iloc[i]['resistance_distance']

                prev_2_vol_avg = dataframe.iloc[i]['prev_2_volume_avg']
                spread_avg = dataframe.iloc[i]['spread_avg']
                volume_ratio = dataframe.iloc[i]['volume_ratio']

                # === 1. 检测 Normal Test（正常测试）===
                # 条件：接近轴线 + 成交量萎缩 + 价差收窄
                # 回滚到更宽松的条件以提高信号数量

                # 接近支撑位的测试
                if support > 0 and support_dist < 0.03:  # 距离支撑3%以内
                    # Normal Test 条件（宽松版本 - 接近310%简化逻辑）
                    # 优先判断：如果成交量萎缩 + 价差收窄，则为 Normal Test
                    is_normal_test = (
                        current_volume < prev_2_vol_avg and  # 成交量低于前2根K线
                        current_spread < spread_avg * 1.2 and  # 价差不超过平均值的120%
                        current_close_pos > 0.4  # 收盘位在中位以上（40%以上）
                    )

                    if is_normal_test:
                        dataframe.iloc[i, dataframe.columns.get_loc('axis_test_type')] = 'normal'
                        # 置信度计算修正：使用相对萎缩程度，而不是绝对比率
                        # 成交量萎缩程度：如果是平均值的80%，萎缩了20%，置信度应该是0.2
                        # 但我们希望萎缩越多，置信度越高，所以需要反向映射
                        vol_shrink_ratio = max(0, 1.0 - current_volume / prev_2_vol_avg)  # 0-1，萎缩程度
                        vol_confidence = 0.5 + vol_shrink_ratio * 0.5  # 映射到0.5-1.0
                        
                        # 价差收窄程度：如果是平均值的100%，没有收窄，置信度应该较低
                        spread_shrink_ratio = max(0, 1.0 - current_spread / spread_avg)  # 0-1，收窄程度
                        spread_confidence = 0.5 + spread_shrink_ratio * 0.5  # 映射到0.5-1.0
                        
                        confidence = (vol_confidence * 0.6 + spread_confidence * 0.4)
                        dataframe.iloc[i, dataframe.columns.get_loc('test_confidence')] = confidence
                        continue
                    
                    # 如果不满足 Normal Test 条件，但接近支撑位，也标记为 normal（兼容简化逻辑）
                    # 这样可以捕获更多信号，类似310%版本
                    if dataframe.iloc[i]['axis_test_type'] == 'none':
                        dataframe.iloc[i, dataframe.columns.get_loc('axis_test_type')] = 'normal'
                        dataframe.iloc[i, dataframe.columns.get_loc('test_confidence')] = 0.5  # 固定置信度

                # 接近阻力位的测试
                if resistance > 0 and resistance_dist < 0.03:  # 距离阻力3%以内
                    # Normal Test 条件（宽松版本 - 接近310%简化逻辑）
                    is_normal_test = (
                        current_volume < prev_2_vol_avg and  # 成交量低于前2根K线
                        current_spread < spread_avg * 1.2 and  # 价差不超过平均值的120%
                        current_close_pos < 0.6  # 收盘位在中位以下（60%以下）
                    )

                    if is_normal_test:
                        dataframe.iloc[i, dataframe.columns.get_loc('axis_test_type')] = 'normal'
                        # 置信度计算修正：使用相对萎缩程度（阻力位版本）
                        vol_shrink_ratio = max(0, 1.0 - current_volume / prev_2_vol_avg)
                        vol_confidence = 0.5 + vol_shrink_ratio * 0.5
                        
                        spread_shrink_ratio = max(0, 1.0 - current_spread / spread_avg)
                        spread_confidence = 0.5 + spread_shrink_ratio * 0.5
                        
                        confidence = (vol_confidence * 0.6 + spread_confidence * 0.4)
                        dataframe.iloc[i, dataframe.columns.get_loc('test_confidence')] = confidence
                        continue
                    
                    # 如果不满足 Normal Test 条件，但接近阻力位，也标记为 normal（兼容简化逻辑）
                    if dataframe.iloc[i]['axis_test_type'] == 'none':
                        dataframe.iloc[i, dataframe.columns.get_loc('axis_test_type')] = 'normal'
                        dataframe.iloc[i, dataframe.columns.get_loc('test_confidence')] = 0.5  # 固定置信度

                # === 2. 检测 Absorption（吸收）===
                # 条件：高努力（大成交量）+ 小结果（价格跌不动）+ 收盘位在中位以上
                # 回滚到更宽松的条件

                # 支撑位的吸收
                if support > 0 and support_dist < 0.03:
                    is_absorption = (
                        volume_ratio > 1.2 and  # 成交量大于平均值的120%
                        current_spread < spread_avg * 1.5 and  # 价差不超过平均值的150%（小结果）
                        current_close_pos > 0.5 and  # 收盘位在中位以上
                        current_close >= support * 0.998  # 价格守住支撑
                    )

                    if is_absorption:
                        dataframe.iloc[i, dataframe.columns.get_loc('axis_test_type')] = 'absorption'
                        # 置信度计算修正：Absorption 需要高成交量 + 高收盘位
                        # 成交量比率：1.2-2.0 映射到 0.5-1.0
                        vol_confidence = 0.5 + min(0.5, (volume_ratio - 1.2) / 1.6)
                        # 收盘位置：0.5-1.0 映射到 0.5-1.0
                        close_pos_confidence = 0.5 + (current_close_pos - 0.5)
                        confidence = (vol_confidence * 0.5 + close_pos_confidence * 0.5)
                        dataframe.iloc[i, dataframe.columns.get_loc('test_confidence')] = confidence
                        continue

                # 阻力位的吸收
                if resistance > 0 and resistance_dist < 0.03:
                    is_absorption = (
                        volume_ratio > 1.2 and  # 成交量大于平均值的120%
                        current_spread < spread_avg * 1.5 and  # 价差不超过平均值的150%（小结果）
                        current_close_pos < 0.5 and  # 收盘位在中位以下
                        current_close <= resistance * 1.002  # 价格守住阻力
                    )

                    if is_absorption:
                        dataframe.iloc[i, dataframe.columns.get_loc('axis_test_type')] = 'absorption'
                        # 置信度计算修正：阻力位的 Absorption（收盘位应该低）
                        vol_confidence = 0.5 + min(0.5, (volume_ratio - 1.2) / 1.6)
                        # 收盘位置：0.0-0.5 映射到 1.0-0.5（越低越好）
                        close_pos_confidence = 1.0 - current_close_pos
                        confidence = (vol_confidence * 0.5 + close_pos_confidence * 0.5)
                        dataframe.iloc[i, dataframe.columns.get_loc('test_confidence')] = confidence
                        continue

                # === 3. 检测 Spring（弹簧）===
                # 条件：跌破支撑 + 快速收回
                # 注意：Spring 检测已经在 SpringClassifier 中实现，这里只做标记

                if support > 0:
                    # 检测跌破
                    broke_support = current_low < support
                    # 检测收回
                    recovered = current_close >= support * 0.998

                    if broke_support and recovered:
                        dataframe.iloc[i, dataframe.columns.get_loc('spring_detected')] = True
                        # Spring 也算作一种特殊的测试
                        if dataframe.iloc[i]['axis_test_type'] == 'none':
                            dataframe.iloc[i, dataframe.columns.get_loc('axis_test_type')] = 'spring'
                            # Spring 的置信度取决于收回速度和成交量
                            recovery_strength = (current_close - support) / (support + 1e-9)
                            vol_factor = 1.0 - min(1.0, volume_ratio / 2.0)  # 成交量越小越好
                            confidence = min(1.0, recovery_strength * 10) * 0.6 + vol_factor * 0.4
                            dataframe.iloc[i, dataframe.columns.get_loc('test_confidence')] = max(0.6, confidence)

            # 清理临时列（保留 close_position，因为后续方法需要使用）
            dataframe.drop(columns=['spread', 'spread_avg', 'volume_ratio', 'prev_2_volume_avg'], 
                          inplace=True, errors='ignore')

            # 统计输出
            normal_count = (dataframe['axis_test_type'] == 'normal').sum()
            absorption_count = (dataframe['axis_test_type'] == 'absorption').sum()
            spring_count = (dataframe['axis_test_type'] == 'spring').sum()

            if normal_count > 0 or absorption_count > 0 or spring_count > 0:
                logger.info(f"Phase 2 轴线测试分类: Normal={normal_count}, Absorption={absorption_count}, Spring={spring_count}")

            return dataframe

    
    # ========================================================================
    # Milestone 3.3: Multi-Timeframe Integration
    # ========================================================================
    
    def _integrate_multi_timeframe_validation(self, dataframe: DataFrame) -> DataFrame:
        """
        多时间框架集成验证 - Milestone 3.3 实现（修复版）
        
        修复问题：
        1. 验证条件过于严格导致信号过滤过多
        2. 只更新最后一行的问题
        3. 在数据不足时的处理逻辑
        
        新的策略：更宽松的验证，重点是避免明显的冲突
        """
        
        # 初始化多时间框架验证列（默认通过验证）
        dataframe['mtf_validation'] = True
        dataframe['mtf_confidence'] = 0.8  # 降低默认置信度要求
        dataframe['mtf_risk_level'] = 'low'
        dataframe['mtf_conflicts'] = ''
        dataframe['higher_tf_context'] = 'neutral'
        
        if len(dataframe) < 100:
            # 数据不足时，保持宽松验证
            return dataframe
        
        # 简化的多时间框架验证：只检查明显的冲突
        dataframe = self._simplified_mtf_validation(dataframe)
        
        return dataframe
    
    def _simplified_mtf_validation(self, dataframe: DataFrame) -> DataFrame:
        """
        简化的多时间框架验证
        
        只检查明显的冲突情况：
        1. 明显的下降趋势中避免买入
        2. 极端的派发信号时谨慎
        3. 其他情况保持宽松
        """
        
        # 计算简化的趋势指标
        price_change_96h = (dataframe['close'] - dataframe['close'].shift(96)) / dataframe['close'].shift(96)  # 4天变化
        price_change_168h = (dataframe['close'] - dataframe['close'].shift(168)) / dataframe['close'].shift(168)  # 7天变化
        
        # 成交量趋势
        volume_ratio_24h = dataframe['volume'] / dataframe['volume'].rolling(24).mean()
        
        # 识别明显的下降趋势（需要避免的情况）
        strong_downtrend = (
            (price_change_96h < -0.15) &  # 4天下跌超过15%
            (price_change_168h < -0.20) &  # 7天下跌超过20%
            (dataframe['close'] < dataframe['ema_200'])  # 在长期均线下方
        )
        
        # 识别可能的派发信号（需要谨慎的情况）
        potential_distribution = (
            (volume_ratio_24h > 2.0) &  # 成交量异常放大
            (price_change_96h.abs() < 0.05) &  # 但价格变化不大
            (dataframe['close'] > dataframe['close'].rolling(168).max() * 0.95)  # 在高位
        )
        
        # 更新验证结果
        # 只在明显冲突时设置为False，其他情况保持True
        dataframe.loc[strong_downtrend, 'mtf_validation'] = False
        dataframe.loc[strong_downtrend, 'mtf_confidence'] = 0.2
        dataframe.loc[strong_downtrend, 'mtf_risk_level'] = 'high'
        dataframe.loc[strong_downtrend, 'mtf_conflicts'] = 'strong_downtrend'
        dataframe.loc[strong_downtrend, 'higher_tf_context'] = 'bearish'
        
        # 派发信号时降低置信度但不完全阻止
        dataframe.loc[potential_distribution, 'mtf_confidence'] = 0.5
        dataframe.loc[potential_distribution, 'mtf_risk_level'] = 'medium'
        dataframe.loc[potential_distribution, 'higher_tf_context'] = 'distribution'
        
        return dataframe
