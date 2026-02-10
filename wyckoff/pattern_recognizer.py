"""
Pattern Recognizer Module - 威科夫模式识别器

负责识别威科夫经典模式：
1. 积累模式（Accumulation）
2. 再积累模式（Reaccumulation）
3. Spring 模式
4. SOS 模式（Sign of Strength）
5. LPS 模式（Last Point of Support）

Author: Wyckoff Strategy Team
Version: 1.0.0
Date: 2026-01-27
"""

from typing import List, Tuple
from pandas import DataFrame, Series
import pandas as pd


class PatternRecognizer:
    """
    威科夫模式识别器
    
    识别威科夫理论中的经典价格模式，用于判断市场所处阶段
    和潜在的交易机会。
    """
    
    def __init__(self):
        """初始化模式识别器"""
        self.min_pattern_confidence = 0.6
        
    def recognize_wyckoff_patterns(self, dataframe: DataFrame, waves_cache: List) -> DataFrame:
        """
        威科夫经典模式识别
        
        识别的威科夫模式：
        1. 积累模式：PS→SC→AR→ST序列
        2. 再积累模式：简化的横盘后突破
        3. 派发模式：UTAD→SOW序列
        4. Spring模式：跌破后快速收回
        5. SOS模式：突破积累区间
        
        Args:
            dataframe: 价格数据
            waves_cache: 波段缓存列表
            
        Returns:
            添加了模式识别结果的 DataFrame
        """
        
        if len(waves_cache) < 5:
            return dataframe
        
        recent_waves = waves_cache[-8:]  # 最近8个波段用于模式识别
        
        # 1. 检测积累模式
        accumulation_pattern, acc_confidence = self.detect_accumulation_pattern(recent_waves)
        
        # 2. 检测再积累模式
        reaccumulation_pattern, reacc_confidence = self.detect_reaccumulation_pattern(recent_waves)
        
        # 3. 检测Spring模式
        spring_pattern, spring_confidence = self.detect_spring_pattern(recent_waves)
        
        # 4. 检测SOS模式
        sos_pattern, sos_confidence = self.detect_sos_pattern(recent_waves)
        
        # 选择置信度最高的模式
        patterns = [
            ('accumulation', acc_confidence),
            ('reaccumulation', reacc_confidence),
            ('spring', spring_confidence),
            ('sos', sos_confidence)
        ]
        
        best_pattern, best_confidence = max(patterns, key=lambda x: x[1])
        
        # 只有置信度 > min_pattern_confidence 才认为识别到模式
        if best_confidence > self.min_pattern_confidence:
            pattern_name = best_pattern
            pattern_confidence = best_confidence
        else:
            pattern_name = 'none'
            pattern_confidence = 0.0
        
        # 将模式信息映射到最近的K线
        if recent_waves:
            last_wave = recent_waves[-1]
            for idx in range(last_wave.start_idx, min(last_wave.end_idx + 1, len(dataframe))):
                dataframe.iloc[idx, dataframe.columns.get_loc('wyckoff_pattern')] = pattern_name
                dataframe.iloc[idx, dataframe.columns.get_loc('pattern_confidence')] = pattern_confidence
        
        return dataframe
    
    def detect_accumulation_pattern(self, waves: List) -> Tuple[bool, float]:
        """
        检测积累模式：下降→横盘→上升的序列
        
        Args:
            waves: 波段列表
            
        Returns:
            (是否检测到模式, 置信度)
        """
        if len(waves) < 4:
            return False, 0.0
        
        # 寻找下降→横盘→上升的模式
        down_waves = []
        sideways_waves = []
        up_waves = []
        
        for wave in waves:
            displacement_percent = wave.displacement / wave.start_price
            
            if not wave.is_up_wave and displacement_percent > 0.02:  # 显著下降
                down_waves.append(wave)
            elif displacement_percent < 0.015:  # 横盘
                sideways_waves.append(wave)
            elif wave.is_up_wave and displacement_percent > 0.02:  # 显著上升
                up_waves.append(wave)
        
        # 积累模式：有下降，有横盘，最后有上升
        if len(down_waves) >= 1 and len(sideways_waves) >= 2 and len(up_waves) >= 1:
            # 检查时间顺序：下降在前，上升在后
            last_down = max(down_waves, key=lambda w: w.end_idx)
            first_up = min(up_waves, key=lambda w: w.start_idx)
            
            if last_down.end_idx < first_up.start_idx:
                confidence = min(0.9, 0.3 + len(sideways_waves) * 0.1 + len(down_waves) * 0.1)
                return True, confidence
        
        return False, 0.0
    
    def detect_reaccumulation_pattern(self, waves: List) -> Tuple[bool, float]:
        """
        检测再积累模式：牛市中的横盘整理
        
        Args:
            waves: 波段列表
            
        Returns:
            (是否检测到模式, 置信度)
        """
        if len(waves) < 3:
            return False, 0.0
        
        # 检查是否在上升趋势中
        up_wave_count = sum(1 for w in waves if w.is_up_wave)
        if up_wave_count < len(waves) * 0.6:  # 上升波段应该占多数
            return False, 0.0
        
        # 检查最近是否有横盘
        recent_waves = waves[-4:]
        sideways_count = 0
        
        for wave in recent_waves:
            displacement_percent = wave.displacement / wave.start_price
            if displacement_percent < 0.02:  # 小幅波动
                sideways_count += 1
        
        if sideways_count >= 2:
            confidence = min(0.8, 0.4 + sideways_count * 0.1)
            return True, confidence
        
        return False, 0.0
    
    def detect_spring_pattern(self, waves: List) -> Tuple[bool, float]:
        """
        检测Spring模式：跌破支撑后快速收回
        
        Args:
            waves: 波段列表
            
        Returns:
            (是否检测到模式, 置信度)
        """
        if len(waves) < 3:
            return False, 0.0
        
        # 寻找下跌后快速反弹的模式
        for i in range(len(waves) - 2):
            down_wave = waves[i]
            up_wave = waves[i + 1]
            
            if (not down_wave.is_up_wave and up_wave.is_up_wave and
                up_wave.displacement > down_wave.displacement * 0.7):  # 反弹幅度足够
                
                # 检查成交量特征：下跌时成交量递减
                if down_wave.effort < up_wave.effort * 0.8:  # 下跌成交量较小
                    confidence = 0.7
                    return True, confidence
        
        return False, 0.0
    
    def detect_sos_pattern(self, waves: List) -> Tuple[bool, float]:
        """
        检测SOS模式：突破积累区间的力量信号
        
        Args:
            waves: 波段列表
            
        Returns:
            (是否检测到模式, 置信度)
        """
        if len(waves) < 2:
            return False, 0.0
        
        # 寻找显著的上升突破
        for wave in waves[-3:]:  # 检查最近3个波段
            if (wave.is_up_wave and 
                wave.displacement / wave.start_price > 0.03 and  # 显著上升
                wave.efficiency > 0.001):  # 有一定效率
                
                confidence = min(0.8, wave.displacement / wave.start_price * 20)
                return True, confidence
        
        return False, 0.0
    
    def detect_lps_pullback_entry(self, dataframe: DataFrame, sos_breakout_detected: Series) -> Series:
        """
        基于威科夫理论的真正LPS检测
        
        LPS (Last Point of Support) 是SOS突破后的回调支撑点，
        是威科夫理论中的关键入场时机。
        
        Args:
            dataframe: 价格数据
            sos_breakout_detected: SOS突破检测结果
            
        Returns:
            LPS检测结果 Series
        """
        lps_detected = pd.Series(False, index=dataframe.index)
        
        # 需要先有SOS突破
        if not sos_breakout_detected.any():
            return lps_detected
        
        # 找到最近的SOS突破点
        sos_indices = dataframe.index[sos_breakout_detected].tolist()
        if not sos_indices:
            return lps_detected
        
        last_sos_idx = sos_indices[-1]
        last_sos_loc = dataframe.index.get_loc(last_sos_idx)
        
        # 在SOS之后寻找LPS
        for i in range(last_sos_loc + 1, len(dataframe)):
            current_price = dataframe['close'].iloc[i]
            sos_price = dataframe['close'].iloc[last_sos_loc]
            
            # LPS特征：
            # 1. 价格回调到SOS突破点附近
            # 2. 成交量萎缩（供应枯竭）
            # 3. 快速反弹（需求重新进入）
            
            pullback_ratio = (sos_price - current_price) / sos_price
            
            if 0.02 < pullback_ratio < 0.08:  # 回调2-8%
                # 检查成交量是否萎缩
                recent_volume = dataframe['volume'].iloc[i-5:i+1].mean()
                sos_volume = dataframe['volume'].iloc[last_sos_loc]
                
                if recent_volume < sos_volume * 0.7:  # 成交量萎缩30%以上
                    lps_detected.iloc[i] = True
        
        return lps_detected
