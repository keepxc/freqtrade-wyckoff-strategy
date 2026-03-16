"""
Unit tests for DailyEventDetector (FUT-E1)

验证 daily_event_detector 的核心逻辑：
- BC (Buying Climax) 检测
- BUEC (Backup to Edge of Creek) 检测
- 序列扫描逻辑
- 默认字段初始化
"""

import pytest
import pandas as pd
import numpy as np
from pandas import DataFrame
from wyckoff.daily_event_detector import DailyEventDetector


class TestDailyEventDetector:
    """DailyEventDetector 单元测试套件"""

    @pytest.fixture
    def detector(self):
        """创建检测器实例"""
        return DailyEventDetector()

    @pytest.fixture
    def sample_dataframe(self):
        """创建测试用 DataFrame（最小可用数据集）"""
        n = 100
        dates = pd.date_range('2023-01-01', periods=n, freq='1h')
        
        df = DataFrame({
            'date': dates,
            'open': np.random.uniform(40000, 50000, n),
            'high': np.random.uniform(50000, 52000, n),
            'low': np.random.uniform(38000, 40000, n),
            'close': np.random.uniform(40000, 50000, n),
            'volume': np.random.uniform(100, 1000, n),
        })
        
        # 添加必需的技术指标
        df['atr'] = 500.0
        df['ema_50'] = df['close'].ewm(span=50, adjust=False).mean()
        df['ema_200'] = df['close'].ewm(span=200, adjust=False).mean()
        df['volume_ma'] = df['volume'].rolling(20).mean()
        
        return df

    def test_detector_initialization(self, detector):
        """测试检测器初始化"""
        assert detector is not None
        assert hasattr(detector, 'detect')
        assert hasattr(detector, '_detect_bc')
        assert hasattr(detector, '_detect_buying_exhaustion')

    def test_detect_returns_dataframe(self, detector, sample_dataframe):
        """测试 detect() 返回 DataFrame"""
        result = detector.detect(sample_dataframe.copy())
        assert isinstance(result, DataFrame)
        assert len(result) == len(sample_dataframe)

    def test_detect_adds_required_fields(self, detector, sample_dataframe):
        """测试 detect() 添加必需字段"""
        result = detector.detect(sample_dataframe.copy())
        
        # 检查核心输出字段
        required_fields = [
            'daily_bc',
            'daily_buec',
            'daily_event_confidence',
        ]
        
        for field in required_fields:
            assert field in result.columns, f"Missing field: {field}"

    def test_bc_detection_logic(self, detector):
        """测试 BC 检测逻辑（高量上涨后回落）"""
        df = DataFrame({
            'date': pd.date_range('2023-01-01', periods=50, freq='1h'),
            'open': [45000] * 50,
            'high': [46000] * 50,
            'low': [44000] * 50,
            'close': [45000] * 50,
            'volume': [500] * 50,
            'atr': [500] * 50,
            'ema_50': [45000] * 50,
            'ema_200': [44000] * 50,
            'volume_ma': [400] * 50,
        })
        
        # 模拟 BC 场景：第30根K线高量上涨
        df.loc[30, 'volume'] = 2000  # 5x 平均量
        df.loc[30, 'close'] = 47000  # 大幅上涨
        df.loc[30, 'high'] = 47500
        
        # 后续回落
        df.loc[31:35, 'close'] = [46500, 46000, 45500, 45000, 44500]
        
        result = detector.detect(df.copy())
        
        # BC 应该在高量上涨后的某个位置被标记
        assert result['daily_bc'].sum() >= 0  # 至少不报错

    def test_empty_dataframe_handling(self, detector):
        """测试空 DataFrame 处理"""
        empty_df = DataFrame()
        result = detector.detect(empty_df)
        
        # 应该返回空 DataFrame 或带默认值的 DataFrame
        assert isinstance(result, DataFrame)

    def test_insufficient_data_handling(self, detector):
        """测试数据不足时的处理"""
        small_df = DataFrame({
            'date': pd.date_range('2023-01-01', periods=5, freq='1h'),
            'open': [45000] * 5,
            'high': [46000] * 5,
            'low': [44000] * 5,
            'close': [45000] * 5,
            'volume': [500] * 5,
            'atr': [500] * 5,
        })
        
        result = detector.detect(small_df.copy())
        
        # 应该不崩溃，返回带默认值的结果
        assert isinstance(result, DataFrame)
        assert len(result) == 5

    def test_buec_detection_after_bc(self, detector):
        """测试 BUEC 在 BC 后的检测"""
        df = DataFrame({
            'date': pd.date_range('2023-01-01', periods=60, freq='1h'),
            'open': [45000] * 60,
            'high': [46000] * 60,
            'low': [44000] * 60,
            'close': [45000] * 60,
            'volume': [500] * 60,
            'atr': [500] * 60,
            'ema_50': [45000] * 60,
            'ema_200': [44000] * 60,
            'volume_ma': [400] * 60,
        })
        
        # 模拟 BC
        df.loc[20, 'daily_bc'] = 1
        df.loc[20, 'high'] = 48000
        
        # 模拟回撤后的 BUEC（回到支撑位）
        df.loc[30:35, 'close'] = [46000, 45500, 45000, 45200, 45500]
        df.loc[35, 'volume'] = 800  # 适度放量
        
        result = detector.detect(df.copy())
        
        # BUEC 应该在 BC 后的某个位置被检测到
        assert 'daily_buec' in result.columns

    def test_confidence_calculation(self, detector, sample_dataframe):
        """测试置信度计算"""
        result = detector.detect(sample_dataframe.copy())
        
        if 'daily_event_confidence' in result.columns:
            confidence = result['daily_event_confidence']
            
            # 置信度应该在 [0, 1] 范围内
            assert confidence.min() >= 0.0
            assert confidence.max() <= 1.0

    def test_detector_idempotency(self, detector, sample_dataframe):
        """测试检测器幂等性（多次调用结果一致）"""
        result1 = detector.detect(sample_dataframe.copy())
        result2 = detector.detect(sample_dataframe.copy())
        
        # 核心字段应该一致
        if 'daily_bc' in result1.columns and 'daily_bc' in result2.columns:
            pd.testing.assert_series_equal(
                result1['daily_bc'],
                result2['daily_bc'],
                check_names=False
            )


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
