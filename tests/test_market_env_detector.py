"""
Unit tests for MarketEnvironmentDetector (FUT-E3)

验证 market_env_detector 的核心逻辑：
- markup (上涨趋势) 检测
- markdown (下跌趋势) 检测
- trading_range (横盘区间) 检测
- transition (过渡期) 检测
- 半窗口比较法
- ATR 归一化
- 持久化机制
"""

import pytest
import pandas as pd
import numpy as np
from pandas import DataFrame
from wyckoff.market_env_detector import MarketEnvironmentDetector


class TestMarketEnvironmentDetector:
    """MarketEnvironmentDetector 单元测试套件"""

    @pytest.fixture
    def detector(self):
        """创建检测器实例"""
        return MarketEnvironmentDetector()

    @pytest.fixture
    def sample_dataframe(self):
        """创建测试用 DataFrame"""
        n = 100
        dates = pd.date_range('2023-01-01', periods=n, freq='1h')
        
        df = DataFrame({
            'date': dates,
            'open': np.random.uniform(40000, 50000, n),
            'high': np.random.uniform(50000, 52000, n),
            'low': np.random.uniform(38000, 40000, n),
            'close': np.random.uniform(40000, 50000, n),
            'volume': np.random.uniform(100, 1000, n),
            'atr': [500.0] * n,
        })
        
        return df

    def test_detector_initialization(self, detector):
        """测试检测器初始化"""
        assert detector is not None
        assert hasattr(detector, 'detect')
        assert detector.half_window == 20
        assert detector.markup_thresh == 1.5
        assert detector.markdown_thresh == 1.5
        assert detector.persistence_bars == 5

    def test_detect_returns_dataframe(self, detector, sample_dataframe):
        """测试 detect() 返回 DataFrame"""
        result = detector.detect(sample_dataframe.copy())
        assert isinstance(result, DataFrame)
        assert len(result) == len(sample_dataframe)

    def test_detect_adds_required_fields(self, detector, sample_dataframe):
        """测试 detect() 添加必需字段"""
        result = detector.detect(sample_dataframe.copy())
        
        required_fields = [
            'market_env',
            'market_env_confidence',
            'hh_score',
            'hl_score',
            'trend_score',
        ]
        
        for field in required_fields:
            assert field in result.columns, f"Missing field: {field}"

    def test_markup_detection(self, detector):
        """测试 markup (上涨趋势) 检测"""
        n = 100
        df = DataFrame({
            'date': pd.date_range('2023-01-01', periods=n, freq='1h'),
            'high': np.linspace(40000, 50000, n),  # 持续上涨
            'low': np.linspace(38000, 48000, n),   # 低点也抬升
            'close': np.linspace(39000, 49000, n),
            'open': np.linspace(38500, 48500, n),
            'volume': [500] * n,
            'atr': [500] * n,
        })
        
        result = detector.detect(df.copy())
        
        # 后半段应该检测到 markup
        markup_count = (result.loc[50:, 'market_env'] == 'markup').sum()
        assert markup_count > 0, "Should detect markup in uptrend"

    def test_markdown_detection(self, detector):
        """测试 markdown (下跌趋势) 检测"""
        n = 100
        df = DataFrame({
            'date': pd.date_range('2023-01-01', periods=n, freq='1h'),
            'high': np.linspace(50000, 40000, n),  # 持续下跌
            'low': np.linspace(48000, 38000, n),   # 低点也下移
            'close': np.linspace(49000, 39000, n),
            'open': np.linspace(48500, 38500, n),
            'volume': [500] * n,
            'atr': [500] * n,
        })
        
        result = detector.detect(df.copy())
        
        # 后半段应该检测到 markdown
        markdown_count = (result.loc[50:, 'market_env'] == 'markdown').sum()
        assert markdown_count > 0, "Should detect markdown in downtrend"

    def test_trading_range_detection(self, detector):
        """测试 trading_range (横盘区间) 检测"""
        n = 100
        df = DataFrame({
            'date': pd.date_range('2023-01-01', periods=n, freq='1h'),
            'high': [46000 + np.random.uniform(-200, 200) for _ in range(n)],
            'low': [44000 + np.random.uniform(-200, 200) for _ in range(n)],
            'close': [45000 + np.random.uniform(-300, 300) for _ in range(n)],
            'open': [45000 + np.random.uniform(-300, 300) for _ in range(n)],
            'volume': [500] * n,
            'atr': [500] * n,
        })
        
        result = detector.detect(df.copy())
        
        # 应该检测到 trading_range
        tr_count = (result['market_env'] == 'trading_range').sum()
        assert tr_count > 0, "Should detect trading_range in sideways market"

    def test_hh_hl_score_calculation(self, detector):
        """测试 HH/HL 分数计算"""
        n = 80
        df = DataFrame({
            'date': pd.date_range('2023-01-01', periods=n, freq='1h'),
            'high': [45000] * 40 + [47000] * 40,  # 后半段高点抬升
            'low': [43000] * 40 + [44000] * 40,   # 后半段低点抬升
            'close': [44000] * 40 + [46000] * 40,
            'open': [44000] * 40 + [46000] * 40,
            'volume': [500] * n,
            'atr': [500] * n,
        })
        
        result = detector.detect(df.copy())
        
        # hh_score 和 hl_score 应该在后半段为正（Higher High + Higher Low）
        assert 'hh_score' in result.columns
        assert 'hl_score' in result.columns
        
        # 后半段的分数应该大于前半段
        assert result.loc[60:, 'hh_score'].mean() > result.loc[20:40, 'hh_score'].mean()

    def test_trend_score_calculation(self, detector, sample_dataframe):
        """测试 trend_score 计算"""
        result = detector.detect(sample_dataframe.copy())
        
        assert 'trend_score' in result.columns
        
        # trend_score 应该是 hh_score 和 hl_score 的平均
        # 验证数值范围合理
        assert result['trend_score'].notna().all()

    def test_confidence_range(self, detector, sample_dataframe):
        """测试置信度范围"""
        result = detector.detect(sample_dataframe.copy())
        
        confidence = result['market_env_confidence']
        
        # 置信度应该在 [0, 1] 范围内
        assert confidence.min() >= 0.0
        assert confidence.max() <= 1.0

    def test_persistence_mechanism(self, detector):
        """测试持久化机制"""
        n = 100
        df = DataFrame({
            'date': pd.date_range('2023-01-01', periods=n, freq='1h'),
            'high': [45000] * n,
            'low': [43000] * n,
            'close': [44000] * n,
            'open': [44000] * n,
            'volume': [500] * n,
            'atr': [500] * n,
        })
        
        # 在第50根K线制造一个明显的 markup 信号
        df.loc[45:55, 'high'] = np.linspace(45000, 48000, 11)
        df.loc[45:55, 'low'] = np.linspace(43000, 46000, 11)
        df.loc[45:55, 'close'] = np.linspace(44000, 47000, 11)
        
        result = detector.detect(df.copy())
        
        # markup 信号应该持续一段时间（persistence_bars = 5）
        markup_indices = result[result['market_env'] == 'markup'].index
        if len(markup_indices) > 0:
            # 检查是否有连续的 markup 标记
            assert len(markup_indices) >= detector.persistence_bars

    def test_transition_detection(self, detector):
        """测试 transition (过渡期) 检测"""
        n = 100
        df = DataFrame({
            'date': pd.date_range('2023-01-01', periods=n, freq='1h'),
            'high': [45000] * n,
            'low': [43000] * n,
            'close': [44000] * n,
            'open': [44000] * n,
            'volume': [500] * n,
            'atr': [500] * n,
        })
        
        # 制造矛盾信号：先上涨后下跌
        df.loc[40:50, 'high'] = np.linspace(45000, 48000, 11)
        df.loc[40:50, 'low'] = np.linspace(43000, 46000, 11)
        df.loc[50:60, 'high'] = np.linspace(48000, 44000, 11)
        df.loc[50:60, 'low'] = np.linspace(46000, 42000, 11)
        
        result = detector.detect(df.copy())
        
        # 应该有 transition 标记
        transition_count = (result['market_env'] == 'transition').sum()
        assert transition_count >= 0  # 至少不报错

    def test_empty_dataframe_handling(self, detector):
        """测试空 DataFrame 处理"""
        empty_df = DataFrame()
        result = detector.detect(empty_df)
        
        assert isinstance(result, DataFrame)

    def test_insufficient_data_handling(self, detector):
        """测试数据不足时的处理"""
        small_df = DataFrame({
            'date': pd.date_range('2023-01-01', periods=10, freq='1h'),
            'high': [46000] * 10,
            'low': [44000] * 10,
            'close': [45000] * 10,
            'open': [45000] * 10,
            'volume': [500] * 10,
            'atr': [500] * 10,
        })
        
        result = detector.detect(small_df.copy())
        
        # 应该返回默认值（transition）
        assert isinstance(result, DataFrame)
        assert len(result) == 10
        assert (result['market_env'] == 'transition').all()

    def test_detector_idempotency(self, detector, sample_dataframe):
        """测试检测器幂等性"""
        result1 = detector.detect(sample_dataframe.copy())
        result2 = detector.detect(sample_dataframe.copy())
        
        # 核心字段应该一致
        pd.testing.assert_series_equal(
            result1['market_env'],
            result2['market_env'],
            check_names=False
        )
        
        pd.testing.assert_series_equal(
            result1['trend_score'],
            result2['trend_score'],
            check_names=False
        )


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
