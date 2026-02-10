"""
威科夫波段分析模块 - 优化版本
核心波段识别和分析逻辑，精简到 < 300行

职责：
- Weis Wave波段识别
- 波段质量分析  
- 努力结果计算
- SOT检测逻辑
"""

import numpy as np
import pandas as pd
from pandas import DataFrame
from typing import List, Dict, Optional, Tuple
import logging

# 导入威科夫数据结构
try:
    from wyckoff_dataclasses import WyckoffWave
    from wyckoff_utils import calculate_wave_quality
except ImportError:
    from dataclasses import dataclass
    
    @dataclass
    class WyckoffWave:
        start_idx: int
        end_idx: int
        start_price: float
        end_price: float
        high_price: float
        low_price: float
        total_volume: float
        bar_count: int
        direction: str
        
        @property
        def displacement(self) -> float:
            return abs(self.high_price - self.low_price)
        
        @property
        def effort(self) -> float:
            return self.total_volume

logger = logging.getLogger(__name__)

class WaveAnalyzer:
    """威科夫波段分析器 - 核心功能"""
    
    def __init__(self, config: Dict = None):
        self.config = config or {}
        self.waves_cache: List[WyckoffWave] = []
        
        # 核心参数
        self.wave_min_percent = self.config.get('wave_min_percent', 0.008)
        self.wave_atr_multiplier = self.config.get('wave_atr_multiplier', 1.5)
        self.wave_min_bars = self.config.get('wave_min_bars', 3)
        self.sot_wave_count = self.config.get('sot_wave_count', 3)
        
    def segment_waves(self, dataframe: DataFrame) -> List[WyckoffWave]:
        """主要接口：波段分割"""
        if dataframe.empty or len(dataframe) < 10:
            return []
        
        # 计算ATR用于波段阈值
        atr = self._calculate_atr(dataframe)
        
        # 识别转折点
        turning_points = self._find_turning_points(dataframe, atr)
        
        # 构建波段
        waves = self._build_waves(dataframe, turning_points)
        
        # 验证波段有效性
        valid_waves = self._validate_waves(waves, dataframe)
        
        self.waves_cache = valid_waves
        return valid_waves
    
    def detect_sot_signals(self, waves: List[WyckoffWave] = None) -> Dict:
        """检测SOT (Signs of Trouble) 信号"""
        waves = waves or self.waves_cache
        
        if len(waves) < self.sot_wave_count:
            return {'detected': False, 'confidence': 0.0, 'reason': 'insufficient_waves'}
        
        # 获取最近的同方向波段
        recent_waves = self._get_recent_same_direction_waves(waves)
        
        if len(recent_waves) < 3:
            return {'detected': False, 'confidence': 0.0, 'reason': 'insufficient_same_direction'}
        
        # 检查位移递减模式
        displacements = [w.displacement for w in recent_waves[-3:]]
        
        # SOT条件：连续位移递减
        sot_detected = (displacements[0] > displacements[1] > displacements[2] and
                       displacements[2] < displacements[0] * 0.7)
        
        confidence = 0.8 if sot_detected else 0.0
        
        return {
            'detected': sot_detected,
            'confidence': confidence,
            'reason': 'displacement_decline' if sot_detected else 'no_pattern',
            'displacements': displacements
        }
    
    def calculate_effort_result_harmony(self, waves: List[WyckoffWave] = None) -> float:
        """计算努力结果和谐性"""
        waves = waves or self.waves_cache
        
        if len(waves) < 2:
            return 0.5
        
        # 计算最近波段的努力结果比率
        recent_wave = waves[-1]
        previous_wave = waves[-2]
        
        if recent_wave.direction != previous_wave.direction:
            return 0.5
        
        # 努力变化
        effort_ratio = recent_wave.effort / (previous_wave.effort + 1e-9)
        
        # 结果变化
        result_ratio = recent_wave.displacement / (previous_wave.displacement + 1e-9)
        
        # 和谐性：努力和结果变化方向一致
        harmony = min(effort_ratio / (result_ratio + 1e-9), result_ratio / (effort_ratio + 1e-9))
        
        return min(harmony, 1.0)
    
    def get_wave_quality_metrics(self, wave: WyckoffWave) -> Dict:
        """获取波段质量指标"""
        if not wave:
            return {'quality': 0.0, 'efficiency': 0.0, 'strength': 0.0}
        
        efficiency = wave.displacement / (wave.effort + 1e-9)
        time_factor = max(0.1, 1.0 - (wave.bar_count / 100.0))
        quality = min(1.0, efficiency * time_factor * 10)
        
        # 强度评分
        strength = min(wave.displacement / 100.0, 1.0)  # 简化强度计算
        
        return {
            'quality': quality,
            'efficiency': efficiency,
            'strength': strength,
            'displacement': wave.displacement,
            'effort': wave.effort,
            'duration': wave.bar_count
        }
    
    # 私有辅助方法
    def _calculate_atr(self, dataframe: DataFrame, period: int = 14) -> pd.Series:
        """计算ATR"""
        high_low = dataframe['high'] - dataframe['low']
        high_close = np.abs(dataframe['high'] - dataframe['close'].shift())
        low_close = np.abs(dataframe['low'] - dataframe['close'].shift())
        
        true_range = np.maximum(high_low, np.maximum(high_close, low_close))
        return true_range.rolling(period).mean()
    
    def _find_turning_points(self, dataframe: DataFrame, atr: pd.Series) -> List[int]:
        """识别转折点"""
        turning_points = []
        threshold = atr * self.wave_atr_multiplier
        
        # 简化的转折点识别
        for i in range(2, len(dataframe) - 2):
            current_high = dataframe.iloc[i]['high']
            current_low = dataframe.iloc[i]['low']
            
            # 检查是否为局部高点
            if (current_high > dataframe.iloc[i-1]['high'] and 
                current_high > dataframe.iloc[i+1]['high'] and
                current_high - dataframe.iloc[i-2:i+3]['low'].min() > threshold.iloc[i]):
                turning_points.append(i)
            
            # 检查是否为局部低点
            elif (current_low < dataframe.iloc[i-1]['low'] and 
                  current_low < dataframe.iloc[i+1]['low'] and
                  dataframe.iloc[i-2:i+3]['high'].max() - current_low > threshold.iloc[i]):
                turning_points.append(i)
        
        return turning_points
    
    def _build_waves(self, dataframe: DataFrame, turning_points: List[int]) -> List[WyckoffWave]:
        """从转折点构建波段"""
        waves = []
        
        for i in range(len(turning_points) - 1):
            start_idx = turning_points[i]
            end_idx = turning_points[i + 1]
            
            if end_idx - start_idx < self.wave_min_bars:
                continue
            
            start_price = dataframe.iloc[start_idx]['close']
            end_price = dataframe.iloc[end_idx]['close']
            
            # 获取波段范围内的高低点
            wave_data = dataframe.iloc[start_idx:end_idx+1]
            high_price = wave_data['high'].max()
            low_price = wave_data['low'].min()
            total_volume = wave_data['volume'].sum()
            
            direction = 'up' if end_price > start_price else 'down'
            
            wave = WyckoffWave(
                start_idx=start_idx,
                end_idx=end_idx,
                start_price=start_price,
                end_price=end_price,
                high_price=high_price,
                low_price=low_price,
                total_volume=total_volume,
                bar_count=end_idx - start_idx + 1,
                direction=direction
            )
            
            waves.append(wave)
        
        return waves
    
    def _validate_waves(self, waves: List[WyckoffWave], dataframe: DataFrame) -> List[WyckoffWave]:
        """验证波段有效性"""
        valid_waves = []
        
        for wave in waves:
            # 最小位移检查
            price_change_pct = abs(wave.end_price - wave.start_price) / wave.start_price
            
            if price_change_pct >= self.wave_min_percent:
                valid_waves.append(wave)
        
        return valid_waves
    
    def _get_recent_same_direction_waves(self, waves: List[WyckoffWave], count: int = 5) -> List[WyckoffWave]:
        """获取最近的同方向波段"""
        if not waves:
            return []
        
        last_direction = waves[-1].direction
        same_direction_waves = []
        
        # 从后往前查找同方向波段
        for wave in reversed(waves):
            if wave.direction == last_direction:
                same_direction_waves.insert(0, wave)
                if len(same_direction_waves) >= count:
                    break
            else:
                break
        
        return same_direction_waves