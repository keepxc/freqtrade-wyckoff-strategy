"""
Weis Wave Foundation - 威科夫波段分析基础模块
Phase 1 实现：将K线噪音转化为具有物理意义的波段

核心原理：
1. 市场按"波"运动，而非固定时间
2. 每个波段包含累积成交量（Effort）和价格位移（Result）
3. 努力结果和谐性是趋势健康的关键指标

参数设置（基于BTC 1小时图）：
- min_wave_size = 0.005 (0.5%)
- ATR倍数：1.5x - 2.0x
- 算法：改进型ZigZag（基于High/Low极值）
"""

from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple
from datetime import datetime
import pandas as pd
import numpy as np
from enum import Enum

try:
    from .data_structures import WyckoffWave
except ImportError:
    from data_structures import WyckoffWave


class DivergenceLevel(Enum):
    """背离严重程度分级"""
    NONE = 0           # 无背离
    LEVEL_1_SOT = 1    # 一级：预警-SOT（位移缩短，缺乏兴趣）
    LEVEL_2_ANOMALY = 2  # 二级：异常（努力增加，结果减小）
    LEVEL_3_CLIMAX = 3   # 三级：危险-气候行动（超高努力，极小结果）


@dataclass
class HarmonyAnalysis:
    """和谐性分析结果"""
    harmony_type: str  # 'harmony', 'divergence', 'neutral'
    effort_level: str  # 'low', 'normal', 'high', 'extreme'
    result_level: str  # 'low', 'normal', 'high', 'extreme'
    harmony_score: float  # -1 to 1, negative = divergence
    divergence_level: DivergenceLevel
    warning_message: str


@dataclass
class SOTAnalysis:
    """冲力缩短分析结果"""
    sot_detected: bool
    wave_displacements: List[float]
    momentum_ratio: float  # current / previous
    volume_trend: str  # 'increasing', 'stable', 'decreasing'
    confidence_score: float
    warning_bars_ahead: int  # 预警提前量


class WeisWaveSegmentation:
    """
    Weis Wave 波段识别引擎
    将价格数据转换为有意义的波段，过滤K线噪音
    """
    
    def __init__(self, min_wave_size: float = 0.005, atr_multiplier: float = 1.75):
        """
        初始化波段识别引擎
        
        Args:
            min_wave_size: 最小波段幅度（默认0.5%）
            atr_multiplier: ATR倍数阈值（默认1.75，范围1.5-2.0）
        """
        self.min_wave_size = min_wave_size
        self.atr_multiplier = atr_multiplier
        self.current_wave: Optional[WyckoffWave] = None
        self.waves: List[WyckoffWave] = []
    
    def segment_waves(self, dataframe: pd.DataFrame) -> List[WyckoffWave]:
        """
        识别价格波段的起点和终点
        
        算法步骤：
        1. 计算动态ATR阈值
        2. 使用改进型ZigZag识别转折点
        3. 过滤小幅震荡
        4. 确保每个波段都有物理意义
        
        Args:
            dataframe: OHLCV数据框
            
        Returns:
            波段列表
        """
        if len(dataframe) < 20:
            return []
        
        # 计算ATR作为动态阈值
        atr = self._calculate_atr(dataframe)
        threshold = atr * self.atr_multiplier
        
        # 识别转折点
        turning_points = self._identify_turning_points(dataframe, threshold)
        
        # 构建波段
        waves = self._build_waves(dataframe, turning_points)
        
        # 验证波段物理意义
        valid_waves = [w for w in waves if self._validate_wave_significance(w, dataframe)]
        
        return valid_waves
    
    def _calculate_atr(self, dataframe: pd.DataFrame, period: int = 14) -> float:
        """
        计算平均真实波幅（ATR）
        
        Args:
            dataframe: OHLCV数据框
            period: ATR周期
            
        Returns:
            ATR值
        """
        if 'atr' in dataframe.columns:
            return dataframe['atr'].iloc[-1]
        
        # 手动计算ATR
        high = dataframe['high']
        low = dataframe['low']
        close = dataframe['close']
        
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean().iloc[-1]
        
        return atr if not pd.isna(atr) else dataframe['close'].iloc[-1] * 0.02

    
    def _identify_turning_points(self, dataframe: pd.DataFrame, threshold: float) -> List[int]:
        """
        识别价格转折点
        使用改进的ZigZag算法，基于ATR而非固定百分比
        
        核心逻辑：
        - 基于极值High/Low触发
        - 捕捉高低点物理全距
        - 位移小于阈值时归为当前波段延伸
        
        Args:
            dataframe: OHLCV数据框
            threshold: 动态阈值（ATR * multiplier）
            
        Returns:
            转折点索引列表
        """
        turning_points = []
        
        if len(dataframe) < 3:
            return turning_points
        
        # 初始化：找到第一个显著的高点或低点
        current_trend = None  # 'up' or 'down'
        last_extreme_idx = 0
        last_extreme_price = dataframe['close'].iloc[0]
        
        for i in range(1, len(dataframe)):
            high = dataframe['high'].iloc[i]
            low = dataframe['low'].iloc[i]
            close = dataframe['close'].iloc[i]
            
            # 计算当前价格与上一个极值的位移
            displacement_from_high = abs(high - last_extreme_price)
            displacement_from_low = abs(low - last_extreme_price)
            
            # 检测上升趋势中的转折（高点后的显著下跌）
            if current_trend == 'up' or current_trend is None:
                if displacement_from_low > threshold:
                    # 确认转折点
                    turning_points.append(last_extreme_idx)
                    current_trend = 'down'
                    last_extreme_idx = i
                    last_extreme_price = low
                elif high > last_extreme_price:
                    # 更新高点
                    last_extreme_idx = i
                    last_extreme_price = high
                    current_trend = 'up'
            
            # 检测下降趋势中的转折（低点后的显著上涨）
            elif current_trend == 'down':
                if displacement_from_high > threshold:
                    # 确认转折点
                    turning_points.append(last_extreme_idx)
                    current_trend = 'up'
                    last_extreme_idx = i
                    last_extreme_price = high
                elif low < last_extreme_price:
                    # 更新低点
                    last_extreme_idx = i
                    last_extreme_price = low
        
        # 添加最后一个极值点
        if last_extreme_idx not in turning_points:
            turning_points.append(last_extreme_idx)
        
        return turning_points
    
    def _build_waves(self, dataframe: pd.DataFrame, turning_points: List[int]) -> List[WyckoffWave]:
        """
        根据转折点构建波段
        
        Args:
            dataframe: OHLCV数据框
            turning_points: 转折点索引列表
            
        Returns:
            波段列表
        """
        waves = []
        
        for i in range(len(turning_points) - 1):
            start_idx = turning_points[i]
            end_idx = turning_points[i + 1]
            
            # 提取波段数据
            wave_data = dataframe.iloc[start_idx:end_idx + 1]
            
            start_price = dataframe['close'].iloc[start_idx]
            end_price = dataframe['close'].iloc[end_idx]
            high_price = wave_data['high'].max()
            low_price = wave_data['low'].min()
            total_volume = wave_data['volume'].sum()
            bar_count = len(wave_data)
            
            # 确定方向
            direction = 'up' if end_price > start_price else 'down'
            
            wave = WyckoffWave(
                start_idx=start_idx,
                end_idx=end_idx,
                start_price=start_price,
                end_price=end_price,
                high_price=high_price,
                low_price=low_price,
                total_volume=total_volume,
                bar_count=bar_count,
                direction=direction
            )
            
            waves.append(wave)
        
        return waves
    
    def _validate_wave_significance(self, wave: WyckoffWave, dataframe: pd.DataFrame) -> bool:
        """
        验证波段的物理意义
        过滤"无专业利益参与的随机波动"
        
        验证标准：
        1. 最小价格移动（min_wave_size）
        2. 最小时间跨度（至少2根K线）
        3. 成交量支持（非零成交量）
        
        Args:
            wave: 波段对象
            dataframe: OHLCV数据框
            
        Returns:
            是否有效
        """
        # 检查最小价格移动
        price_change_pct = abs(wave.end_price - wave.start_price) / wave.start_price
        if price_change_pct < self.min_wave_size:
            return False
        
        # 检查最小时间跨度
        if wave.bar_count < 2:
            return False
        
        # 检查成交量支持
        if wave.total_volume <= 0:
            return False
        
        return True


class EffortResultAnalyzer:
    """
    努力结果和谐性分析器
    实现威科夫第三定律：努力结果定律
    
    量化标准：
    - 高努力：当前波段累积成交量 > 前5个同向波段平均成交量的1.5倍
    - 高结果：当前波段位移 > 前5个同向波段平均位移
    """
    
    def __init__(self, lookback_waves: int = 5, effort_multiplier: float = 1.5):
        """
        初始化分析器
        
        Args:
            lookback_waves: 回看波段数量（默认5）
            effort_multiplier: 努力倍数阈值（默认1.5）
        """
        self.lookback_waves = lookback_waves
        self.effort_multiplier = effort_multiplier
    
    def analyze_wave_harmony(self, wave: WyckoffWave, previous_waves: List[WyckoffWave]) -> HarmonyAnalysis:
        """
        分析波段的努力结果关系
        
        和谐模式：
        - 高努力高结果：健康趋势
        - 低努力低结果：正常整理
        
        背离模式：
        - 高努力低结果：遇到阻力（二级异常）
        - 低努力高结果：供应枯竭
        
        Args:
            wave: 当前波段
            previous_waves: 历史波段列表
            
        Returns:
            和谐性分析结果
        """
        # 获取同向波段
        same_direction_waves = [w for w in previous_waves if w.direction == wave.direction]
        
        if len(same_direction_waves) < self.lookback_waves:
            return HarmonyAnalysis(
                harmony_type='neutral',
                effort_level='normal',
                result_level='normal',
                harmony_score=0.0,
                divergence_level=DivergenceLevel.NONE,
                warning_message='数据不足，无法分析'
            )
        
        # 取最近的N个同向波段
        recent_waves = same_direction_waves[-self.lookback_waves:]
        
        # 计算平均努力和结果
        avg_effort = np.mean([w.effort for w in recent_waves])
        avg_result = np.mean([w.displacement for w in recent_waves])
        
        # 判断当前波段的努力和结果水平
        effort_level = self._classify_level(wave.effort, avg_effort, self.effort_multiplier)
        result_level = self._classify_level(wave.displacement, avg_result, 1.0)
        
        # 计算和谐性得分
        harmony_score, harmony_type = self._calculate_harmony_score(effort_level, result_level)
        
        # 判断背离严重程度
        divergence_level = self._classify_divergence_level(
            effort_level, result_level, wave, recent_waves
        )
        
        # 生成警告信息
        warning_message = self._generate_warning_message(
            harmony_type, effort_level, result_level, divergence_level
        )
        
        return HarmonyAnalysis(
            harmony_type=harmony_type,
            effort_level=effort_level,
            result_level=result_level,
            harmony_score=harmony_score,
            divergence_level=divergence_level,
            warning_message=warning_message
        )

    
    def _classify_level(self, value: float, avg_value: float, multiplier: float) -> str:
        """
        分类水平（low/normal/high/extreme）
        
        Args:
            value: 当前值
            avg_value: 平均值
            multiplier: 倍数阈值
            
        Returns:
            水平分类
        """
        if value > avg_value * (multiplier + 0.5):
            return 'extreme'
        elif value > avg_value * multiplier:
            return 'high'
        elif value > avg_value * 0.7:
            return 'normal'
        else:
            return 'low'
    
    def _calculate_harmony_score(self, effort_level: str, result_level: str) -> Tuple[float, str]:
        """
        计算和谐性得分
        
        Args:
            effort_level: 努力水平
            result_level: 结果水平
            
        Returns:
            (得分, 类型)
        """
        level_scores = {'low': 0, 'normal': 1, 'high': 2, 'extreme': 3}
        
        effort_score = level_scores.get(effort_level, 1)
        result_score = level_scores.get(result_level, 1)
        
        # 和谐：努力和结果匹配
        if abs(effort_score - result_score) <= 1:
            harmony_score = 0.5 + (min(effort_score, result_score) * 0.1)
            harmony_type = 'harmony'
        # 背离：努力和结果不匹配
        else:
            harmony_score = -(abs(effort_score - result_score) * 0.3)
            harmony_type = 'divergence'
        
        return harmony_score, harmony_type
    
    def _classify_divergence_level(
        self, 
        effort_level: str, 
        result_level: str,
        wave: WyckoffWave,
        recent_waves: List[WyckoffWave]
    ) -> DivergenceLevel:
        """
        分类背离严重程度
        
        一级（预警-SOT）：结果减弱，努力减小
        二级（异常）：努力增加，结果减小
        三级（危险-气候行动）：超高努力，极小结果
        
        Args:
            effort_level: 努力水平
            result_level: 结果水平
            wave: 当前波段
            recent_waves: 最近波段列表
            
        Returns:
            背离等级
        """
        # 三级：气候行动（超高努力，极小结果）
        if effort_level in ['extreme', 'high'] and result_level == 'low':
            # 检查效率是否显著下降
            avg_efficiency = np.mean([w.efficiency for w in recent_waves])
            if wave.efficiency < avg_efficiency * 0.5:
                return DivergenceLevel.LEVEL_3_CLIMAX
        
        # 二级：异常（努力增加，结果减小）
        if effort_level in ['high', 'extreme'] and result_level in ['low', 'normal']:
            return DivergenceLevel.LEVEL_2_ANOMALY
        
        # 一级：SOT预警（结果减弱）
        if result_level == 'low' and effort_level in ['low', 'normal']:
            return DivergenceLevel.LEVEL_1_SOT
        
        return DivergenceLevel.NONE
    
    def _generate_warning_message(
        self,
        harmony_type: str,
        effort_level: str,
        result_level: str,
        divergence_level: DivergenceLevel
    ) -> str:
        """
        生成警告信息
        
        Args:
            harmony_type: 和谐类型
            effort_level: 努力水平
            result_level: 结果水平
            divergence_level: 背离等级
            
        Returns:
            警告信息
        """
        if divergence_level == DivergenceLevel.LEVEL_3_CLIMAX:
            return f"⚠️ 三级警告：气候行动！超高努力({effort_level})但结果极小({result_level})，趋势可能反转"
        elif divergence_level == DivergenceLevel.LEVEL_2_ANOMALY:
            return f"⚠️ 二级警告：努力结果异常！努力{effort_level}但结果{result_level}，机构可能在吸收"
        elif divergence_level == DivergenceLevel.LEVEL_1_SOT:
            return f"⚠️ 一级警告：SOT预警！位移缩短({result_level})，缺乏兴趣，动能衰减"
        elif harmony_type == 'harmony':
            return f"✓ 和谐波段：努力{effort_level}，结果{result_level}，趋势健康"
        else:
            return f"中性：努力{effort_level}，结果{result_level}"
    
    def calculate_ease_of_movement(self, wave: WyckoffWave) -> float:
        """
        计算移动轻松感
        轻松感 = 价格位移 / 成交量努力
        
        Args:
            wave: 波段对象
            
        Returns:
            轻松感指标
        """
        return wave.efficiency
    
    def detect_effort_result_divergence(self, waves: List[WyckoffWave]) -> bool:
        """
        检测努力结果背离
        连续背离是趋势转折的重要信号
        
        Args:
            waves: 波段列表
            
        Returns:
            是否存在背离
        """
        if len(waves) < 3:
            return False
        
        # 检查最近3个波段
        recent_waves = waves[-3:]
        divergence_count = 0
        
        for i in range(1, len(recent_waves)):
            current = recent_waves[i]
            previous = recent_waves[i-1]
            
            # 检查效率下降
            if current.efficiency < previous.efficiency * 0.7:
                divergence_count += 1
        
        # 连续2次以上效率下降视为背离
        return divergence_count >= 2


class SOTDetector:
    """
    冲力缩短检测器（Signs of Trouble）
    识别连续波段位移递减的动能枯竭信号
    
    SOT条件：
    1. 连续3个同向波段
    2. 位移递减：D3 < D2 < D1
    3. 成交量维持或增加
    """
    
    def __init__(self, wave_count: int = 3, momentum_threshold: float = 0.8):
        """
        初始化SOT检测器
        
        Args:
            wave_count: 检测波段数量（默认3）
            momentum_threshold: 动量阈值（默认0.8）
        """
        self.wave_count = wave_count
        self.momentum_threshold = momentum_threshold
    
    def detect_shortening_of_thrust(self, waves: List[WyckoffWave]) -> SOTAnalysis:
        """
        检测冲力缩短
        
        Args:
            waves: 波段列表
            
        Returns:
            SOT分析结果
        """
        if len(waves) < self.wave_count:
            return SOTAnalysis(
                sot_detected=False,
                wave_displacements=[],
                momentum_ratio=1.0,
                volume_trend='unknown',
                confidence_score=0.0,
                warning_bars_ahead=0
            )
        
        # 获取最近N个同向波段
        recent_waves = self._get_recent_same_direction_waves(waves, self.wave_count)
        
        if len(recent_waves) < self.wave_count:
            return SOTAnalysis(
                sot_detected=False,
                wave_displacements=[],
                momentum_ratio=1.0,
                volume_trend='unknown',
                confidence_score=0.0,
                warning_bars_ahead=0
            )
        
        # 提取位移
        displacements = [w.displacement for w in recent_waves]
        
        # 检查位移递减：D3 < D2 < D1
        sot_detected = self._check_displacement_decrease(displacements)
        
        # 计算动量比率
        momentum_ratio = self._calculate_momentum_ratio(recent_waves)
        
        # 分析成交量趋势
        volume_trend = self._analyze_volume_trend(recent_waves)
        
        # 计算置信度
        confidence_score = self._calculate_confidence(
            sot_detected, momentum_ratio, volume_trend
        )
        
        # 预警提前量
        warning_bars_ahead = self._estimate_warning_bars(confidence_score)
        
        return SOTAnalysis(
            sot_detected=sot_detected,
            wave_displacements=displacements,
            momentum_ratio=momentum_ratio,
            volume_trend=volume_trend,
            confidence_score=confidence_score,
            warning_bars_ahead=warning_bars_ahead
        )

    
    def _get_recent_same_direction_waves(
        self, 
        waves: List[WyckoffWave], 
        count: int
    ) -> List[WyckoffWave]:
        """
        获取最近的同向波段
        
        Args:
            waves: 波段列表
            count: 需要的波段数量
            
        Returns:
            同向波段列表
        """
        if not waves:
            return []
        
        # 获取最后一个波段的方向
        last_direction = waves[-1].direction
        
        # 从后向前收集同向波段
        same_direction = []
        for wave in reversed(waves):
            if wave.direction == last_direction:
                same_direction.insert(0, wave)
                if len(same_direction) >= count:
                    break
        
        return same_direction
    
    def _check_displacement_decrease(self, displacements: List[float]) -> bool:
        """
        检查位移是否递减
        
        Args:
            displacements: 位移列表
            
        Returns:
            是否递减
        """
        if len(displacements) < 3:
            return False
        
        # 检查 D3 < D2 < D1
        return displacements[-1] < displacements[-2] < displacements[-3]
    
    def _calculate_momentum_ratio(self, waves: List[WyckoffWave]) -> float:
        """
        计算动量比率
        动量 = 位移 / 时间 / 平均成交量
        
        Args:
            waves: 波段列表
            
        Returns:
            当前动量 / 前一个动量
        """
        if len(waves) < 2:
            return 1.0
        
        def calc_momentum(wave: WyckoffWave) -> float:
            avg_volume = wave.total_volume / wave.bar_count
            return wave.displacement / wave.bar_count / (avg_volume + 1e-9)
        
        current_momentum = calc_momentum(waves[-1])
        previous_momentum = calc_momentum(waves[-2])
        
        if previous_momentum == 0:
            return 1.0
        
        return current_momentum / previous_momentum
    
    def _analyze_volume_trend(self, waves: List[WyckoffWave]) -> str:
        """
        分析成交量趋势
        
        Args:
            waves: 波段列表
            
        Returns:
            'increasing', 'stable', 'decreasing'
        """
        if len(waves) < 2:
            return 'unknown'
        
        volumes = [w.total_volume for w in waves]
        
        # 计算成交量变化
        volume_changes = []
        for i in range(1, len(volumes)):
            change = (volumes[i] - volumes[i-1]) / volumes[i-1]
            volume_changes.append(change)
        
        avg_change = np.mean(volume_changes)
        
        if avg_change > 0.1:
            return 'increasing'
        elif avg_change < -0.1:
            return 'decreasing'
        else:
            return 'stable'
    
    def _calculate_confidence(
        self, 
        sot_detected: bool, 
        momentum_ratio: float, 
        volume_trend: str
    ) -> float:
        """
        计算SOT检测置信度
        
        Args:
            sot_detected: 是否检测到SOT
            momentum_ratio: 动量比率
            volume_trend: 成交量趋势
            
        Returns:
            置信度（0-1）
        """
        if not sot_detected:
            return 0.0
        
        confidence = 0.5  # 基础置信度
        
        # 动量下降增加置信度
        if momentum_ratio < self.momentum_threshold:
            confidence += 0.2
        
        # 成交量增加但位移减少（更危险）
        if volume_trend == 'increasing':
            confidence += 0.2
        elif volume_trend == 'stable':
            confidence += 0.1
        
        return min(confidence, 1.0)
    
    def _estimate_warning_bars(self, confidence_score: float) -> int:
        """
        估算预警提前量（K线数）
        
        Args:
            confidence_score: 置信度
            
        Returns:
            预警K线数
        """
        if confidence_score >= 0.8:
            return 3  # 高置信度，3根K线预警
        elif confidence_score >= 0.6:
            return 5  # 中等置信度，5根K线预警
        elif confidence_score >= 0.4:
            return 8  # 低置信度，8根K线预警
        else:
            return 0  # 置信度太低，不预警
    
    def validate_sot_conditions(self, waves: List[WyckoffWave]) -> bool:
        """
        验证SOT的威科夫条件
        
        验证标准：
        - 必须在上升趋势中
        - 必须有足够的历史数据
        - 必须排除正常的整理模式
        
        Args:
            waves: 波段列表
            
        Returns:
            是否满足条件
        """
        if len(waves) < 5:
            return False
        
        # 检查是否在上升趋势中
        recent_waves = waves[-5:]
        up_waves = [w for w in recent_waves if w.direction == 'up']
        
        if len(up_waves) < 3:
            return False  # 不在明显的上升趋势中
        
        # 检查是否是正常整理（横盘）
        price_range = max(w.high_price for w in recent_waves) - min(w.low_price for w in recent_waves)
        avg_price = np.mean([w.end_price for w in recent_waves])
        
        if price_range / avg_price < 0.03:
            return False  # 横盘整理，不是SOT
        
        return True


# 辅助函数

def detect_exhaustion_reversal(
    waves: List[WyckoffWave],
    dataframe: pd.DataFrame
) -> bool:
    """
    检测枯竭型转折
    特征：极小成交量 + 窄价差
    
    这种转折不发生在成交量最大时，而是在"缺乏兴趣"时
    
    Args:
        waves: 波段列表
        dataframe: OHLCV数据框
        
    Returns:
        是否检测到枯竭型转折
    """
    if len(waves) < 2:
        return False
    
    last_wave = waves[-1]
    
    # 计算平均成交量
    avg_volume = np.mean([w.total_volume for w in waves[-5:]])
    
    # 检查极小成交量（低于平均的50%）
    low_volume = last_wave.total_volume < avg_volume * 0.5
    
    # 检查窄价差（低于平均位移的50%）
    avg_displacement = np.mean([w.displacement for w in waves[-5:]])
    narrow_range = last_wave.displacement < avg_displacement * 0.5
    
    return low_volume and narrow_range


def calculate_wave_quality_score(wave: WyckoffWave, previous_waves: List[WyckoffWave]) -> float:
    """
    计算波段质量得分
    
    质量因素：
    1. 效率（轻松感）
    2. 持续时间
    3. 成交量支持
    
    Args:
        wave: 当前波段
        previous_waves: 历史波段
        
    Returns:
        质量得分（0-1）
    """
    if not previous_waves:
        return 0.5
    
    score = 0.0
    
    # 效率得分（40%权重）
    avg_efficiency = np.mean([w.efficiency for w in previous_waves[-5:]])
    if wave.efficiency > avg_efficiency * 1.2:
        score += 0.4
    elif wave.efficiency > avg_efficiency * 0.8:
        score += 0.2
    
    # 持续时间得分（30%权重）
    avg_bars = np.mean([w.bar_count for w in previous_waves[-5:]])
    if wave.bar_count >= avg_bars:
        score += 0.3
    elif wave.bar_count >= avg_bars * 0.7:
        score += 0.15
    
    # 成交量支持得分（30%权重）
    avg_volume = np.mean([w.total_volume for w in previous_waves[-5:]])
    if wave.total_volume > avg_volume * 1.2:
        score += 0.3
    elif wave.total_volume > avg_volume * 0.8:
        score += 0.15
    
    return min(score, 1.0)



# ============================================================================
# Weis Wave 信号质量过滤器
# ============================================================================

@dataclass
class QualityFilterResult:
    """质量过滤结果"""
    floor_filter: pd.Series      # 地板线过滤（基础过滤）
    high_quality: pd.Series      # 高质量信号
    risk_warning: pd.Series      # 风险警告
    final_filter: pd.Series      # 最终过滤结果
    statistics: Dict[str, int]   # 统计信息


class WeisWaveQualityFilter:
    """
    Weis Wave 质量过滤器
    
    威科夫两层过滤体系：
    1. 静态地板线（Floor）- 排除明显低质量信号
    2. 积极过滤（AND逻辑）- 只保留高质量信号
    3. 预警机制 - 风险检查
    
    设计原则：
    - 威科夫强调"寻找阻力最小的路径"
    - 高质量信号 = 多种力量的共振
    - 移动轻松感（高效率）+ 显著努力（高成交量）+ 无疲劳迹象（无SOT）
    """
    
    def __init__(
        self,
        floor_quality_threshold: float = 0.4,
        floor_harmony_threshold: float = 0.0,
        high_quality_threshold: float = 0.6,
        high_harmony_threshold: float = 0.3,
        min_wave_count: int = 6
    ):
        """
        初始化过滤器
        
        Args:
            floor_quality_threshold: 地板线质量阈值（默认0.4）
            floor_harmony_threshold: 地板线和谐性阈值（默认0.0）
            high_quality_threshold: 高质量阈值（默认0.6）
            high_harmony_threshold: 高和谐性阈值（默认0.3）
            min_wave_count: 最小波段数量（默认6，因果定律）
        """
        self.floor_quality_threshold = floor_quality_threshold
        self.floor_harmony_threshold = floor_harmony_threshold
        self.high_quality_threshold = high_quality_threshold
        self.high_harmony_threshold = high_harmony_threshold
        self.min_wave_count = min_wave_count
    
    def apply_filter(self, dataframe: pd.DataFrame) -> QualityFilterResult:
        """
        应用威科夫两层过滤体系
        
        Args:
            dataframe: 包含 Weis Wave 指标的数据框
            
        Returns:
            QualityFilterResult: 过滤结果
        """
        # 检查必需的列
        required_cols = [
            'weis_wave_quality',
            'weis_harmony_score',
            'weis_sot_detected',
            'weis_divergence_level',
            'weis_local_wave_count'  # ✅ 改为局部计数
        ]
        
        for col in required_cols:
            if col not in dataframe.columns:
                # 返回默认结果（全部通过）
                return self._create_default_result(dataframe)
        
        # ============================================================
        # 第一层：静态地板线（Floor）- 排除明显低质量信号
        # ============================================================
        # 威科夫原则：排除"缺乏兴趣"（Lack of Interest）的波段
        
        floor_filter = (
            (dataframe['weis_wave_quality'] >= self.floor_quality_threshold) &
            (dataframe['weis_harmony_score'] >= self.floor_harmony_threshold) &
            (dataframe['weis_local_wave_count'] >= self.min_wave_count)  # ✅ 作为必要条件
        )
        
        # ✅ 移除 insufficient_data 逻辑（数据不足时不应放宽要求）
        
        # ============================================================
        # 第二层：积极过滤（AND逻辑）- 只保留高质量信号
        # ============================================================
        # 威科夫原则："寻找阻力最小的路径"
        # 高质量信号 = 多种力量的共振
        
        high_quality = (
            (dataframe['weis_wave_quality'] > self.high_quality_threshold) &
            (dataframe['weis_harmony_score'] > self.high_harmony_threshold) &
            (dataframe['weis_sot_detected'] == False)
        )
        
        # ✅ 移除 insufficient_data 引用（已删除该逻辑）
        
        # ============================================================
        # 第三层：预警机制 - 风险检查
        # ============================================================
        # 威科夫原则：一旦出现二级异常，强制触发风险检查
        
        risk_warning = (
            (dataframe['weis_divergence_level'] >= 2) |
            (dataframe['weis_sot_detected'] == True)
        )
        
        # ============================================================
        # 最终过滤器组合
        # ============================================================
        
        final_filter = floor_filter & ~risk_warning
        
        # ============================================================
        # 统计信息
        # ============================================================
        
        statistics = {
            'floor_passed': floor_filter.sum(),
            'high_quality_count': high_quality.sum(),
            'risk_warning_count': risk_warning.sum(),
            'final_passed': final_filter.sum()
        }
        
        return QualityFilterResult(
            floor_filter=floor_filter,
            high_quality=high_quality,
            risk_warning=risk_warning,
            final_filter=final_filter,
            statistics=statistics
        )
    
    def _create_default_result(self, dataframe: pd.DataFrame) -> QualityFilterResult:
        """
        创建默认结果（数据不足或列缺失时）
        
        Args:
            dataframe: 数据框
            
        Returns:
            QualityFilterResult: 默认结果（全部通过）
        """
        default_series = pd.Series(True, index=dataframe.index)
        false_series = pd.Series(False, index=dataframe.index)
        
        statistics = {
            'floor_passed': len(dataframe),
            'high_quality_count': 0,
            'risk_warning_count': 0,
            'final_passed': len(dataframe)
        }
        
        return QualityFilterResult(
            floor_filter=default_series,
            high_quality=false_series,
            risk_warning=false_series,
            final_filter=default_series,
            statistics=statistics
        )
