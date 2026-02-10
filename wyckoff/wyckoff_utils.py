"""
威科夫交易方法 - 工具函数 (简化版本)

包含威科夫分析的核心工具函数，避免复杂依赖
基于288.17%基准性能
"""

from typing import List, Dict, Optional, Tuple
import logging

# 简化的导入，避免循环依赖
try:
    from wyckoff_dataclasses import WyckoffWave, AxisLine, WyckoffPhase
except ImportError:
    # 如果导入失败，使用基本定义
    pass

logger = logging.getLogger(__name__)

def calculate_wave_quality(displacement: float, effort: float, bar_count: int) -> float:
    """
    计算波段质量评分
    
    Args:
        displacement: 位移
        effort: 努力（成交量）
        bar_count: K线数量
        
    Returns:
        质量评分 (0-1)
    """
    if effort <= 0 or bar_count <= 0:
        return 0.0
    
    # 基础效率评分
    efficiency = displacement / effort
    
    # 时间因子 - 较短的波段质量更高
    time_factor = max(0.1, 1.0 - (bar_count / 100.0))
    
    # 综合评分
    quality = min(1.0, efficiency * time_factor * 10)
    return quality

def detect_effort_result_divergence(waves: list, threshold: float = 0.3) -> bool:
    """
    检测努力结果背离
    
    Args:
        waves: 波段列表
        threshold: 背离阈值
        
    Returns:
        是否存在背离
    """
    if len(waves) < 2:
        return False
    
    recent_wave = waves[-1]
    previous_wave = waves[-2]
    
    # 检查同方向波段的背离
    if hasattr(recent_wave, 'direction') and hasattr(previous_wave, 'direction'):
        if recent_wave.direction == previous_wave.direction:
            effort_change = (recent_wave.effort - previous_wave.effort) / (previous_wave.effort + 1e-9)
            result_change = (recent_wave.displacement - previous_wave.displacement) / (previous_wave.displacement + 1e-9)
            
            # 努力增加但结果减少，或努力减少但结果增加
            if (effort_change > threshold and result_change < -threshold) or \
               (effort_change < -threshold and result_change > threshold):
                return True
    
    return False

def calculate_atr_normalized_displacement(high_prices: list, low_prices: list, atr_value: float) -> float:
    """
    计算ATR标准化的位移
    
    Args:
        high_prices: 高价列表
        low_prices: 低价列表
        atr_value: ATR值
        
    Returns:
        标准化位移
    """
    if not high_prices or not low_prices or atr_value <= 0:
        return 0.0
    
    displacement = abs(max(high_prices) - min(low_prices))
    return displacement / atr_value

def calculate_volume_effort(volumes: list) -> float:
    """
    计算成交量努力
    
    Args:
        volumes: 成交量列表
        
    Returns:
        总努力值
    """
    if not volumes:
        return 0.0
    
    return sum(volumes)

def detect_spring_action(price: float, support_level: float, tolerance: float = 0.02) -> bool:
    """
    检测Spring行动
    
    Args:
        price: 当前价格
        support_level: 支撑位
        tolerance: 容差
        
    Returns:
        是否为Spring行动
    """
    if support_level <= 0:
        return False
    
    # 价格略微跌破支撑位
    break_percentage = (support_level - price) / support_level
    return 0 < break_percentage <= tolerance