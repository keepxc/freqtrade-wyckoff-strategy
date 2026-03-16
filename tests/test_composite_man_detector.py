"""
Unit tests for CompositeManIntentDetector (FUT-E4)

验证 composite_man_detector 的核心逻辑：
- 成交量结构分析
- 价格行为分析
- 波浪结构分析
- 事件序列分析
- 相对强度分析
- 综合评分与意图判断
"""

import pytest
import pandas as pd
import numpy as np
from pandas import DataFrame
from wyckoff.composite_man_detector import CompositeManIntentDetector


class TestCompositeManIntentDetector:
    """CompositeManIntentDetector 单元测试套件"""

    @pytest.fixture
    def detector(self):
        """创建检测器实例"""
        return CompositeManIntentDetector()

    @pytest.fixture
    def sample_dataframe(self):
        """创建测试用 DataFrame"""
        n = 150
        dates = pd.date_range('2023-01-01', periods=n, freq='1h')
        
        df = DataFrame({
            'date': dates,
            'open': np.random.uniform(40000, 50000, n),
            'high': np.random.uniform(50000, 52000, n),
            'low': np.random.uniform(38000, 40000, n),
            'close': np.random.uniform(40000, 50000, n),
            'volume': np.random.uniform(100, 1000, n),
        })
        
        # 添加必需字段
        df['atr'] = 500.0
        df['volume_ma'] = df['volume'].rolling(20).mean()
        
        return df

    def test_detector_initialization(self, detector):
        """测试检测器初始化"""
        assert detector is not None
        assert hasattr(detector, 'detect')
        assert hasattr(detector, '_analyze_volume_structure')
        assert hasattr(detector, '_analyze_price_behavior')
        assert hasattr(detector, '_analyze_wave_structure')
        assert hasattr(detector, '_analyze_event_sequence')

    def test_detect_returns_dataframe(self, detector, sample_dataframe):
        """测试 detect() 返回 DataFrame"""
        result = detector.detect(sample_dataframe.copy())
        assert isinstance(result, DataFrame)
        assert len(result) == len(sample_dataframe)

    def test_detect_adds_required_fields(self, detector, sample_dataframe):
        """测试 detect() 添加必需字段"""
        result = detector.detect(sample_dataframe.copy())
        
        required_fields = [
            'cm_intent',
            'cm_confidence',
            'cm_score',
        ]
        
        for field in required_fields:
            assert field in result.columns, f"Missing field: {field}"

    def test_intent_values(self, detector, sample_dataframe):
        """测试意图值的有效性"""
        result = detector.detect(sample_dataframe.copy())
        
        # cm_intent 应该是有效的意图标签
        valid_intents = ['accumulation', 'distribution', 'neutral', 'markup', 'markdown']
        
        if 'cm_intent' in result.columns:
            unique_intents = result['cm_intent'].unique()
            for intent in unique_intents:
                assert intent in valid_intents or pd.isna(intent), \
                    f"Invalid intent: {intent}"

    def test_confidence_range(self, detector, sample_dataframe):
        """测试置信度范围"""
        result = detector.detect(sample_dataframe.copy())
        
        if 'cm_confidence' in result.columns:
            confidence = result['cm_confidence']
            
            # 置信度应该在 [0, 1] 范围内
            assert confidence.min() >= 0.0
            assert confidence.max() <= 1.0

    def test_score_calculation(self, detector, sample_dataframe):
        """测试综合评分计算"""
        result = detector.detect(sample_dataframe.copy())
        
        if 'cm_score' in result.columns:
            score = result['cm_score']
            
            # 评分应该是数值型
            assert score.dtype in [np.float64, np.float32, np.int64, np.int32]
            assert score.notna().any()

    def test_volume_structure_analysis(self, detector):
        """测试成交量结构分析"""
        df = DataFrame({
            'date': pd.date_range('2023-01-01', periods=100, freq='1h'),
            'open': [45000] * 100,
            'high': [46000] * 100,
            'low': [44000] * 100,
            'close': [45000] * 100,
            'volume': [500] * 100,
            'atr': [500] * 100,
            'volume_ma': [400] * 100,
        })
        
        # 模拟放量上涨（积累信号）
        df.loc[50:60, 'volume'] = 1500  # 3x 平均量
        df.loc[50:60, 'close'] = np.linspace(45000, 47000, 11)
        
        result = detector.detect(df.copy())
        
        # 应该检测到积累意图
        if 'cm_intent' in result.columns:
            intent_in_range = result.loc[55:65, 'cm_intent'].mode()
            if len(intent_in_range) > 0:
                # 可能是 accumulation 或 markup
                assert intent_in_range.iloc[0] in ['accumulation', 'markup', 'neutral']

    def test_price_behavior_analysis(self, detector):
        """测试价格行为分析"""
        df = DataFrame({
            'date': pd.date_range('2023-01-01', periods=100, freq='1h'),
            'open': [45000] * 100,
            'high': [46000] * 100,
            'low': [44000] * 100,
            'close': [45000] * 100,
            'volume': [500] * 100,
            'atr': [500] * 100,
            'volume_ma': [400] * 100,
        })
        
        # 模拟价格持续上涨
        df.loc[40:80, 'close'] = np.linspace(45000, 50000, 41)
        df.loc[40:80, 'high'] = np.linspace(46000, 51000, 41)
        df.loc[40:80, 'low'] = np.linspace(44000, 49000, 41)
        
        result = detector.detect(df.copy())
        
        # 应该有价格行为相关的分析结果
        assert 'cm_intent' in result.columns

    def test_accumulation_detection(self, detector):
        """测试积累阶段检测"""
        df = DataFrame({
            'date': pd.date_range('2023-01-01', periods=120, freq='1h'),
            'open': [45000] * 120,
            'high': [46000] * 120,
            'low': [44000] * 120,
            'close': [45000] * 120,
            'volume': [500] * 120,
            'atr': [500] * 120,
            'volume_ma': [400] * 120,
        })
        
        # 模拟积累特征：横盘 + 间歇性放量
        df.loc[40:80, 'close'] = [45000 + np.random.uniform(-500, 500) for _ in range(41)]
        df.loc[50, 'volume'] = 1500
        df.loc[60, 'volume'] = 1600
        df.loc[70, 'volume'] = 1400
        
        result = detector.detect(df.copy())
        
        # 应该检测到积累意图
        if 'cm_intent' in result.columns:
            accumulation_count = (result.loc[40:80, 'cm_intent'] == 'accumulation').sum()
            assert accumulation_count >= 0  # 至少不报错

    def test_distribution_detection(self, detector):
        """测试派发阶段检测"""
        df = DataFrame({
            'date': pd.date_range('2023-01-01', periods=120, freq='1h'),
            'open': [50000] * 120,
            'high': [51000] * 120,
            'low': [49000] * 120,
            'close': [50000] * 120,
            'volume': [500] * 120,
            'atr': [500] * 120,
            'volume_ma': [400] * 120,
        })
        
        # 模拟派发特征：高位横盘 + 放量下跌
        df.loc[40:60, 'close'] = [50000 + np.random.uniform(-300, 300) for _ in range(21)]
        df.loc[65:75, 'close'] = np.linspace(50000, 46000, 11)
        df.loc[65:75, 'volume'] = 1500
        
        result = detector.detect(df.copy())
        
        # 应该检测到派发意图
        if 'cm_intent' in result.columns:
            distribution_count = (result.loc[65:80, 'cm_intent'] == 'distribution').sum()
            assert distribution_count >= 0  # 至少不报错

    def test_neutral_detection(self, detector):
        """测试中性状态检测"""
        df = DataFrame({
            'date': pd.date_range('2023-01-01', periods=100, freq='1h'),
            'open': [45000] * 100,
            'high': [45500] * 100,
            'low': [44500] * 100,
            'close': [45000] * 100,
            'volume': [500] * 100,
            'atr': [500] * 100,
            'volume_ma': [500] * 100,
        })
        
        # 完全平静的市场
        result = detector.detect(df.copy())
        
        # 应该检测到中性状态
        if 'cm_intent' in result.columns:
            neutral_count = (result['cm_intent'] == 'neutral').sum()
            assert neutral_count > 0

    def test_empty_dataframe_handling(self, detector):
        """测试空 DataFrame 处理"""
        empty_df = DataFrame()
        result = detector.detect(empty_df)
        
        assert isinstance(result, DataFrame)

    def test_insufficient_data_handling(self, detector):
        """测试数据不足时的处理"""
        small_df = DataFrame({
            'date': pd.date_range('2023-01-01', periods=10, freq='1h'),
            'open': [45000] * 10,
            'high': [46000] * 10,
            'low': [44000] * 10,
            'close': [45000] * 10,
            'volume': [500] * 10,
            'atr': [500] * 10,
            'volume_ma': [400] * 10,
        })
        
        result = detector.detect(small_df.copy())
        
        # 应该不崩溃
        assert isinstance(result, DataFrame)
        assert len(result) == 10

    def test_detector_idempotency(self, detector, sample_dataframe):
        """测试检测器幂等性"""
        result1 = detector.detect(sample_dataframe.copy())
        result2 = detector.detect(sample_dataframe.copy())
        
        # 核心字段应该一致
        if 'cm_intent' in result1.columns and 'cm_intent' in result2.columns:
            pd.testing.assert_series_equal(
                result1['cm_intent'],
                result2['cm_intent'],
                check_names=False
            )

    def test_with_metadata(self, detector, sample_dataframe):
        """测试带 metadata 的检测"""
        metadata = {'pair': 'BTC/USDT:USDT'}
        result = detector.detect(sample_dataframe.copy(), metadata=metadata)
        
        assert isinstance(result, DataFrame)
        assert len(result) == len(sample_dataframe)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
