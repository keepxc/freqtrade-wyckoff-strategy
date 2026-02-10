"""
威科夫轴线测试分类器

基于威科夫物理本质的测试分类：
- Normal Test（正常测试）：缺乏兴趣
- Absorption（吸收）：大努力、小结果
- Spring #3（枯竭型弹簧）：轻轻跌破、缩量、快速收回
- Spring #1（震仓型弹簧）：放量、深穿
- Genuine Break（真实突破）：大量、大幅

优化原则：
- 量价本质 > 形态准确度
- 物理特征 > 复杂条件
- 动态置信度计算
"""

from typing import Tuple, Optional, TYPE_CHECKING
from pandas import DataFrame
import pandas as pd
from datetime import datetime

from .data_structures import AxisTestResult

if TYPE_CHECKING:
    from .axis_system import AxisLine


class AxisTestClassifier:
    """
    轴线测试分类器
    
    负责识别和分类轴线测试类型
    """
    
    def __init__(self):
        """初始化分类器"""
        pass
    
    def classify_test(self, dataframe: DataFrame, test_idx: int, 
                     axis: 'AxisLine') -> Optional[AxisTestResult]:
        """
        分类单次轴线测试
        
        Args:
            dataframe: 价格数据
            test_idx: 测试K线索引
            axis: 轴线对象
            
        Returns:
            AxisTestResult: 测试结果，如果无法分类则返回None
        """
        if test_idx < 5 or test_idx >= len(dataframe):
            return None
        
        test_bar = dataframe.iloc[test_idx]
        
        # 计算测试特征
        volume_ratio = test_bar['volume'] / (test_bar['volume_sma_20'] + 1e-9)
        range_ratio = test_bar['bar_range'] / (test_bar['atr_20'] + 1e-9)
        close_position = test_bar['close_position']
        
        # 历史数据
        prev_1 = dataframe.iloc[test_idx - 1] if test_idx >= 1 else None
        prev_2 = dataframe.iloc[test_idx - 2] if test_idx >= 2 else None
        
        # 测试分类（按优先级）
        test_type = 'unclear'
        confidence = 0.3
        
        # 1. Spring #3 检测（最高优先级 - 最强信号）
        is_spring3, spring3_conf = self._is_spring_type3(
            dataframe, test_idx, axis, prev_1, prev_2
        )
        if is_spring3:
            test_type = 'spring'
            confidence = spring3_conf
            return self._create_test_result(test_bar, test_idx, axis, test_type, confidence,
                                           volume_ratio, range_ratio, close_position)
        
        # 2. Absorption 检测（高优先级 - 机构接盘）
        is_absorption, absorption_conf = self._is_absorption_test(
            test_bar, axis, volume_ratio, range_ratio, close_position
        )
        if is_absorption:
            test_type = 'absorption'
            confidence = absorption_conf
            return self._create_test_result(test_bar, test_idx, axis, test_type, confidence,
                                           volume_ratio, range_ratio, close_position)
        
        # 3. Normal Test 检测（简化版 - 物理本质）
        is_normal, normal_conf = self._is_normal_test_simplified(
            test_bar, prev_1, prev_2, axis, volume_ratio, range_ratio, close_position
        )
        if is_normal:
            test_type = 'normal'
            confidence = normal_conf
            return self._create_test_result(test_bar, test_idx, axis, test_type, confidence,
                                           volume_ratio, range_ratio, close_position)
        
        # 4. Genuine Break 检测
        is_break, break_conf = self._is_genuine_break(
            test_bar, axis, volume_ratio, range_ratio, close_position
        )
        if is_break:
            test_type = 'genuine_break'
            confidence = break_conf
            return self._create_test_result(test_bar, test_idx, axis, test_type, confidence,
                                           volume_ratio, range_ratio, close_position)
        
        # 5. Spring #1 检测（传统Spring）
        is_spring1, spring1_conf = self._is_spring_type1(
            test_bar, axis, volume_ratio, range_ratio, close_position
        )
        if is_spring1:
            test_type = 'spring'
            confidence = spring1_conf
            return self._create_test_result(test_bar, test_idx, axis, test_type, confidence,
                                           volume_ratio, range_ratio, close_position)
        
        # 6. 不明确
        return self._create_test_result(test_bar, test_idx, axis, 'unclear', 0.3,
                                       volume_ratio, range_ratio, close_position)
    
    def _is_normal_test_simplified(self, test_bar, prev_1, prev_2, axis,
                                   volume_ratio, range_ratio, close_position) -> Tuple[bool, float]:
        """
        简化的正常测试识别 - 基于威科夫物理本质
        
        核心：缺乏兴趣（Lack of Interest）
        - 成交量低于前2根K线（放宽：or条件）
        - 价差收窄
        
        阶段2优化：放宽条件，提高识别率
        
        Returns:
            (是否为Normal Test, 置信度)
        """
        if prev_1 is None or prev_2 is None:
            return False, 0.0
        
        # 1. 成交量缺乏兴趣：低于前2根K线之一即可（放宽条件）
        volume_declining = (
            test_bar['volume'] < prev_1['volume'] or  # ✅ 改为or
            test_bar['volume'] < prev_2['volume']
        )
        
        # 2. 价差收窄
        current_range = test_bar['high'] - test_bar['low']
        prev_1_range = prev_1['high'] - prev_1['low']
        prev_2_range = prev_2['high'] - prev_2['low']
        avg_prev_range = (prev_1_range + prev_2_range) / 2
        
        spread_narrowing = current_range < avg_prev_range
        
        # 3. 满足其中一个条件即可（放宽条件）
        if not (volume_declining or spread_narrowing):  # ✅ 改为or
            return False, 0.0
        
        # 4. 动态置信度计算
        # 计算缺乏兴趣的程度
        avg_prev_volume = (prev_1['volume'] + prev_2['volume']) / 2
        volume_decline_ratio = test_bar['volume'] / (avg_prev_volume + 1e-9)
        spread_ratio = current_range / (avg_prev_range + 1e-9)
        
        # 基础置信度
        base_confidence = 0.4
        
        # 成交量缺乏兴趣加分
        if volume_declining:
            base_confidence += (1.0 - volume_decline_ratio) * 0.3
        
        # 价差收窄加分
        if spread_narrowing:
            base_confidence += (1.0 - spread_ratio) * 0.2
        
        # 两个条件都满足，额外加分
        if volume_declining and spread_narrowing:
            base_confidence += 0.1
        
        confidence = min(0.95, max(0.4, base_confidence))
        
        return True, confidence
    
    def _is_absorption_test(self, test_bar, axis, volume_ratio,
                           range_ratio, close_position) -> Tuple[bool, float]:
        """
        识别吸收型测试 - 大努力、小结果（Phase 2.1 优化版）
        
        威科夫理论：机构在接盘（Bag-holding）
        - 成交量大（努力大）
        - 价格跌不动（结果小）
        - 极佳的入场信号
        
        Phase 2.1 优化：
        - 放宽阈值提高识别率
        - 目标：从2个提升到15-20个
        
        Returns:
            (是否为Absorption, 置信度)
        """
        # 1. 大努力：成交量明显放大（放宽：1.3→1.2）
        high_volume = volume_ratio > 1.2  # ✅ 降低到20%以上
        
        # 2. 小结果：价格区间相对较小（放宽：1.2→1.3）
        small_result = range_ratio < 1.3  # ✅ 放宽到1.3倍ATR
        
        # 3. 支撑有效：收盘位置较高（放宽：0.5→0.45）
        line_type_str = axis.line_type.value if hasattr(axis.line_type, 'value') else str(axis.line_type)
        if line_type_str == 'support':
            support_holding = close_position > 0.45  # ✅ 放宽到45%
        else:
            support_holding = True
        
        # 吸收特征
        if high_volume and small_result and support_holding:
            # 努力/结果比率越大，吸收越明显
            effort_result_ratio = volume_ratio / (range_ratio + 0.1)
            
            # 动态置信度：基于努力/结果比率
            base_confidence = 0.55  # 提高基础置信度
            ratio_bonus = min(0.35, effort_result_ratio * 0.08)  # 比率加成
            confidence = min(0.9, base_confidence + ratio_bonus)
            
            return True, confidence
        
        return False, 0.0
    
    def _is_spring_type3(self, dataframe, test_idx, axis, prev_1, prev_2) -> Tuple[bool, float]:
        """
        识别 Spring #3 - 枯竭型弹簧
        
        特征：
        - 轻轻跌破（浅度）
        - 缩量（枯竭）
        - 窄价差
        - 快速收回（当前K线）
        
        这是最强力的入场点！
        
        Returns:
            (是否为Spring #3, 置信度)
        """
        line_type_str = axis.line_type.value if hasattr(axis.line_type, 'value') else str(axis.line_type)
        if line_type_str != 'support' or prev_1 is None or prev_2 is None:
            return False, 0.0
        
        current = dataframe.iloc[test_idx]
        
        # 1. 轻轻跌破：低点略低于支撑
        break_depth = (axis.price_level - current['low']) / axis.price_level
        shallow_break = 0.001 < break_depth < 0.03  # 0.1%-3%
        
        # 2. 缩量：成交量低于前期
        avg_prev_volume = (prev_1['volume'] + prev_2['volume']) / 2
        volume_decline_ratio = current['volume'] / avg_prev_volume
        low_volume = volume_decline_ratio < 0.8  # 缩量20%以上
        
        # 3. 窄价差：价格区间收窄
        current_range = current['high'] - current['low']
        prev_1_range = prev_1['high'] - prev_1['low']
        prev_2_range = prev_2['high'] - prev_2['low']
        avg_prev_range = (prev_1_range + prev_2_range) / 2
        narrow_spread = current_range < avg_prev_range * 0.9
        
        # 4. 收盘收回：收盘价回到支撑之上或接近
        close_recovery = current['close'] >= axis.price_level * 0.998  # 允许0.2%容差
        
        # Spring #3 特征
        if shallow_break and low_volume and narrow_spread and close_recovery:
            # 越枯竭，置信度越高
            exhaustion_score = (1.0 - volume_decline_ratio) * 0.4 + (1.0 - current_range / avg_prev_range) * 0.3
            confidence = min(0.95, 0.6 + exhaustion_score)
            
            return True, confidence
        
        return False, 0.0
    
    def _is_spring_type1(self, test_bar, axis, volume_ratio,
                        range_ratio, close_position) -> Tuple[bool, float]:
        """
        识别 Spring #1 - 震仓型弹簧
        
        特征：
        - 放量
        - 深穿
        - 需等待放量回升
        
        Returns:
            (是否为Spring #1, 置信度)
        """
        line_type_str = axis.line_type.value if hasattr(axis.line_type, 'value') else str(axis.line_type)
        if line_type_str != 'support':
            return False, 0.0
        
        # 1. 中等成交量
        moderate_volume = 0.8 < volume_ratio < 1.5
        
        # 2. 跌破确认：收盘在支撑之下
        break_below = close_position < 0.4
        
        # 3. 跌破深度：比Spring #3更深
        break_depth = (axis.price_level - test_bar['low']) / axis.price_level
        deeper_break = 0.01 < break_depth < 0.05  # 1%-5%
        
        if moderate_volume and break_below and deeper_break:
            # 基础置信度
            confidence = 0.7
            return True, confidence
        
        return False, 0.0
    
    def _is_genuine_break(self, test_bar, axis, volume_ratio,
                         range_ratio, close_position) -> Tuple[bool, float]:
        """
        识别真实突破 - 优化版
        
        Returns:
            (是否为Genuine Break, 置信度)
        """
        # 1. 成交量放大
        volume_expansion = volume_ratio > 1.5
        
        # 2. 价格区间明显扩张
        wide_range = range_ratio > 1.5
        
        # 3. 突破确认
        line_type_str = axis.line_type.value if hasattr(axis.line_type, 'value') else str(axis.line_type)
        if line_type_str == 'support':
            break_confirmed = close_position < 0.3
        else:
            break_confirmed = close_position > 0.7
        
        if volume_expansion and wide_range and break_confirmed:
            # 动态置信度
            strength = (volume_ratio + range_ratio) / 3.0
            confidence = min(0.95, 0.7 + strength * 0.1)
            return True, confidence
        
        return False, 0.0
    
    def _create_test_result(self, test_bar, test_idx, axis, test_type, confidence,
                           volume_ratio, range_ratio, close_position) -> AxisTestResult:
        """创建测试结果对象"""
        return AxisTestResult(
            axis_line=axis,
            test_type=test_type,
            test_idx=test_idx,
            test_price=test_bar['close'],
            test_time=datetime.now(),
            volume_ratio=volume_ratio,
            range_ratio=range_ratio,
            close_position=close_position,
            confidence_score=confidence,
            confirmation_status='pending'
        )
