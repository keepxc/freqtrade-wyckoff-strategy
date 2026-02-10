"""
波段分析模块单元测试
"""

import unittest
import pandas as pd
import numpy as np
from wave_analyzer import WaveAnalyzer, WyckoffWave

class TestWaveAnalyzer(unittest.TestCase):
    
    def setUp(self):
        """测试设置"""
        self.analyzer = WaveAnalyzer()
        
        # 创建测试数据
        self.test_data = pd.DataFrame({
            'open': [100, 101, 102, 103, 104],
            'high': [101, 102, 103, 104, 105],
            'low': [99, 100, 101, 102, 103],
            'close': [100.5, 101.5, 102.5, 103.5, 104.5],
            'volume': [1000, 1100, 1200, 1300, 1400]
        })
    
    def test_wave_analyzer_initialization(self):
        """测试波段分析器初始化"""
        self.assertIsInstance(self.analyzer, WaveAnalyzer)
        self.assertEqual(len(self.analyzer.waves_cache), 0)
    
    def test_analyze_waves_empty_data(self):
        """测试空数据的波段分析"""
        empty_df = pd.DataFrame()
        waves = self.analyzer.analyze_waves(empty_df)
        self.assertEqual(len(waves), 0)
    
    def test_analyze_waves_basic(self):
        """测试基础波段分析"""
        waves = self.analyzer.analyze_waves(self.test_data)
        self.assertIsInstance(waves, list)
    
    def test_sot_detection_insufficient_waves(self):
        """测试SOT检测 - 波段不足"""
        result = self.analyzer.detect_sot_signals([])
        self.assertFalse(result['detected'])
        self.assertEqual(result['confidence'], 0.0)
    
    def test_effort_result_harmony_insufficient_waves(self):
        """测试努力结果和谐性 - 波段不足"""
        harmony = self.analyzer.calculate_effort_result_harmony([])
        self.assertEqual(harmony, 0.5)

if __name__ == '__main__':
    unittest.main()
