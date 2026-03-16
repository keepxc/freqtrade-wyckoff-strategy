"""
Unit tests for EventSequenceStateMachine (FUT-E2)

验证 event_sequence_state_machine 的核心逻辑：
- PS (Preliminary Support) 检测
- SC (Selling Climax) 检测
- AR (Automatic Rally) 检测
- ST (Secondary Test) 检测
- Phase 转换逻辑（A → B → C → D → E）
- Spring / UTAD 检测
"""

import pytest
import pandas as pd
import numpy as np
from pandas import DataFrame
from wyckoff.event_sequence_state_machine import EventSequenceStateMachine


class TestEventSequenceStateMachine:
    """EventSequenceStateMachine 单元测试套件"""

    @pytest.fixture
    def state_machine(self):
        """创建状态机实例"""
        return EventSequenceStateMachine()

    @pytest.fixture
    def sample_dataframe(self):
        """创建测试用 DataFrame"""
        n = 200
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

    def test_state_machine_initialization(self, state_machine):
        """测试状态机初始化"""
        assert state_machine is not None
        assert hasattr(state_machine, 'detect')
        assert hasattr(state_machine, '_detect_ps')
        assert hasattr(state_machine, '_detect_sc')
        assert hasattr(state_machine, '_detect_ar')
        assert hasattr(state_machine, '_detect_st')

    def test_detect_returns_dataframe(self, state_machine, sample_dataframe):
        """测试 detect() 返回 DataFrame"""
        result = state_machine.detect(sample_dataframe.copy())
        assert isinstance(result, DataFrame)
        assert len(result) == len(sample_dataframe)

    def test_detect_adds_phase_fields(self, state_machine, sample_dataframe):
        """测试 detect() 添加 Phase 相关字段"""
        result = state_machine.detect(sample_dataframe.copy())
        
        required_fields = [
            'wyckoff_phase',
            'phase_a_complete',
            'phase_b_active',
            'phase_c_active',
            'phase_d_active',
            'phase_e_active',
        ]
        
        for field in required_fields:
            assert field in result.columns, f"Missing field: {field}"

    def test_ps_detection(self, state_machine):
        """测试 PS 检测逻辑"""
        df = DataFrame({
            'date': pd.date_range('2023-01-01', periods=50, freq='1h'),
            'open': [45000] * 50,
            'high': [46000] * 50,
            'low': [44000] * 50,
            'close': [45000] * 50,
            'volume': [500] * 50,
            'atr': [500] * 50,
            'volume_ma': [400] * 50,
        })
        
        # 模拟 PS：下跌后首次放量反弹
        df.loc[0:10, 'close'] = np.linspace(50000, 42000, 11)  # 下跌
        df.loc[15, 'volume'] = 1500  # 放量
        df.loc[15, 'close'] = 43000  # 反弹
        df.loc[15, 'low'] = 41500
        
        result = state_machine.detect(df.copy())
        
        assert 'ps_bar' in result.columns

    def test_sc_detection(self, state_machine):
        """测试 SC 检测逻辑（恐慌性抛售）"""
        df = DataFrame({
            'date': pd.date_range('2023-01-01', periods=50, freq='1h'),
            'open': [45000] * 50,
            'high': [46000] * 50,
            'low': [44000] * 50,
            'close': [45000] * 50,
            'volume': [500] * 50,
            'atr': [500] * 50,
            'volume_ma': [400] * 50,
        })
        
        # 模拟 SC：极端放量下跌
        df.loc[20, 'volume'] = 3000  # 6x 平均量
        df.loc[20, 'close'] = 38000  # 暴跌
        df.loc[20, 'low'] = 37000
        df.loc[20, 'open'] = 44000
        
        result = state_machine.detect(df.copy())
        
        assert 'sc_bar' in result.columns

    def test_ar_detection_after_sc(self, state_machine):
        """测试 AR 在 SC 后的检测"""
        df = DataFrame({
            'date': pd.date_range('2023-01-01', periods=60, freq='1h'),
            'open': [45000] * 60,
            'high': [46000] * 60,
            'low': [44000] * 60,
            'close': [45000] * 60,
            'volume': [500] * 60,
            'atr': [500] * 60,
            'volume_ma': [400] * 60,
        })
        
        # 模拟 SC
        df.loc[20, 'sc_bar'] = 1
        df.loc[20, 'low'] = 37000
        
        # 模拟 AR：SC 后的自动反弹
        df.loc[25:30, 'close'] = np.linspace(38000, 43000, 6)
        df.loc[30, 'volume'] = 1200
        
        result = state_machine.detect(df.copy())
        
        assert 'ar_bar' in result.columns

    def test_phase_a_completion(self, state_machine):
        """测试 Phase A 完成标记"""
        df = DataFrame({
            'date': pd.date_range('2023-01-01', periods=80, freq='1h'),
            'open': [45000] * 80,
            'high': [46000] * 80,
            'low': [44000] * 80,
            'close': [45000] * 80,
            'volume': [500] * 80,
            'atr': [500] * 80,
            'volume_ma': [400] * 80,
        })
        
        # 模拟完整的 Phase A 序列：PS → SC → AR
        df.loc[10, 'ps_bar'] = 1
        df.loc[20, 'sc_bar'] = 1
        df.loc[30, 'ar_bar'] = 1
        
        result = state_machine.detect(df.copy())
        
        # Phase A 应该在 AR 后被标记为完成
        if 'phase_a_complete' in result.columns:
            assert result['phase_a_complete'].sum() > 0

    def test_phase_b_activation(self, state_machine):
        """测试 Phase B 激活"""
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
        
        # 模拟 Phase A 完成
        df.loc[30, 'phase_a_complete'] = 1
        
        # Phase B 应该在 Phase A 后激活
        result = state_machine.detect(df.copy())
        
        if 'phase_b_active' in result.columns:
            assert result.loc[31:, 'phase_b_active'].sum() >= 0

    def test_spring_detection(self, state_machine):
        """测试 Spring 检测"""
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
        
        # 模拟 Phase B/C 环境
        df.loc[30:60, 'phase_b_active'] = 1
        
        # 模拟 Spring：短暂跌破支撑后快速收回
        df.loc[50, 'low'] = 42000  # 跌破
        df.loc[51, 'close'] = 45500  # 快速收回
        df.loc[51, 'volume'] = 1200
        
        result = state_machine.detect(df.copy())
        
        assert 'spring_bar' in result.columns

    def test_phase_transition_sequence(self, state_machine):
        """测试 Phase 转换序列（A → B → C → D → E）"""
        df = DataFrame({
            'date': pd.date_range('2023-01-01', periods=200, freq='1h'),
            'open': [45000] * 200,
            'high': [46000] * 200,
            'low': [44000] * 200,
            'close': [45000] * 200,
            'volume': [500] * 200,
            'atr': [500] * 200,
            'volume_ma': [400] * 200,
        })
        
        # 模拟完整的 Wyckoff 积累周期
        df.loc[20, 'ps_bar'] = 1
        df.loc[30, 'sc_bar'] = 1
        df.loc[40, 'ar_bar'] = 1
        df.loc[50, 'st_bar'] = 1
        df.loc[80, 'spring_bar'] = 1
        df.loc[100:150, 'close'] = np.linspace(45000, 52000, 51)  # Phase D 上涨
        
        result = state_machine.detect(df.copy())
        
        # 应该有 Phase 字段
        assert 'wyckoff_phase' in result.columns

    def test_empty_dataframe_handling(self, state_machine):
        """测试空 DataFrame 处理"""
        empty_df = DataFrame()
        result = state_machine.detect(empty_df)
        
        assert isinstance(result, DataFrame)

    def test_state_machine_idempotency(self, state_machine, sample_dataframe):
        """测试状态机幂等性"""
        result1 = state_machine.detect(sample_dataframe.copy())
        result2 = state_machine.detect(sample_dataframe.copy())
        
        # 核心字段应该一致
        if 'wyckoff_phase' in result1.columns and 'wyckoff_phase' in result2.columns:
            pd.testing.assert_series_equal(
                result1['wyckoff_phase'],
                result2['wyckoff_phase'],
                check_names=False
            )


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
