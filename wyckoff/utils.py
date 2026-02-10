"""
威科夫工具函数模块
核心计算和分析函数
"""

import numpy as np
import pandas as pd
from typing import List, Dict, Optional, Tuple

def calculate_wave_quality(displacement: float, effort: float, bar_count: int) -> float:
    """计算波段质量"""
    if effort == 0 or bar_count == 0:
        return 0.0
    
    efficiency = displacement / effort
    time_factor = 1.0 / bar_count
    
    return efficiency * time_factor

def detect_effort_result_divergence(waves: list, threshold: float = 0.3) -> bool:
    """检测努力结果背离"""
    if len(waves) < 2:
        return False
    
    recent_wave = waves[-1]
    previous_wave = waves[-2]
    
    # 努力增加但结果减少
    effort_increase = recent_wave.effort > previous_wave.effort * (1 + threshold)
    result_decrease = recent_wave.displacement < previous_wave.displacement * (1 - threshold)
    
    return effort_increase and result_decrease

def calculate_atr_normalized_displacement(high: float, low: float, atr: float) -> float:
    """计算ATR标准化位移"""
    if atr == 0:
        return 0.0
    
    displacement = abs(high - low)
    return displacement / atr

def calculate_volume_effort(volume_series, window: int = 20) -> float:
    """计算成交量努力"""
    if len(volume_series) < window:
        return volume_series.sum()
    
    return volume_series.tail(window).sum()

def detect_spring_action(price_low: float, support_level: float, volume: float, avg_volume: float) -> bool:
    """检测Spring行动"""
    # 价格略微跌破支撑
    price_break = price_low < support_level * 0.998
    
    # 成交量相对较小
    volume_condition = volume < avg_volume * 1.2
    
    return price_break and volume_condition
