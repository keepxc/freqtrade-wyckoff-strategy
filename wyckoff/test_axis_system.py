"""
威科夫轴线系统单元测试 - Phase 2.1.1

测试目标：
1. 验证轴线识别逻辑正确性
2. 验证成交量密度计算准确性
3. 验证轴线强度分级合理性
4. 验证轴线合并逻辑
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from dataclasses import dataclass

from axis_system import (
    AxisLineManager,
    AxisLine,
    AxisLineType,
    AxisLineStrength
)


# 模拟Wave数据结构（与weis_wave_foundation.py兼容）
@dataclass
class MockWave:
    """模拟波段数据"""
    start_idx: int
    end_idx: int
    start_price: float
    end_price: float
    high_price: float
    low_price: float
    total_volume: float
    bar_count: int
    direction: str  # 'up' or 'down'


def create_test_dataframe(n_bars: int = 100) -> pd.DataFrame:
    """
    创建测试用的价格数据
    
    Args:
        n_bars: K线数量
        
    Returns:
        pd.DataFrame: 测试数据
    """
    dates = pd.date_range(start='2024-01-01', periods=n_bars, freq='1H')
    
    # 创建模拟价格数据
    base_price = 50000.0
    price_changes = np.random.randn(n_bars) * 100
    close_prices = base_price + np.cumsum(price_changes)
    
    df = pd.DataFrame({
        'open': close_prices + np.random.randn(n_bars) * 50,
        'high': close_prices + np.abs(np.random.randn(n_bars)) * 100,
        'low': close_prices - np.abs(np.random.randn(n_bars)) * 100,
        'close': close_prices,
        'volume': np.random.uniform(1000000, 5000000, n_bars)
    }, index=dates)
    
    return df


def create_test_waves() -> list:
    """
    创建测试用的波段数据
    
    Returns:
        list: 波段列表
    """
    waves = [
        # 上升波段1
        MockWave(
            start_idx=0, end_idx=10,
            start_price=50000, end_price=51000,
            high_price=51000, low_price=50000,
            total_volume=10000000, bar_count=10,
            direction='up'
        ),
        # 下降波段1
        MockWave(
            start_idx=10, end_idx=20,
            start_price=51000, end_price=50500,
            high_price=51000, low_price=50500,
            total_volume=8000000, bar_count=10,
            direction='down'
        ),
        # 上升波段2
        MockWave(
            start_idx=20, end_idx=30,
            start_price=50500, end_price=51500,
            high_price=51500, low_price=50500,
            total_volume=12000000, bar_count=10,
            direction='up'
        ),
        # 下降波段2
        MockWave(
            start_idx=30, end_idx=40,
            start_price=51500, end_price=51000,
            high_price=51500, low_price=51000,
            total_volume=9000000, bar_count=10,
            direction='down'
        ),
    ]
    
    return waves


class TestAxisLineManager:
    """轴线管理器测试类"""
    
    def test_initialization(self):
        """测试初始化"""
        manager = AxisLineManager()
        
        assert manager.min_test_count == 2
        assert manager.volume_density_window == 20
        assert manager.price_tolerance == 0.005
        assert manager.max_axis_age_hours == 168
        assert len(manager.axis_lines) == 0
    
    def test_identify_axis_lines_from_waves(self):
        """测试从波段识别轴线"""
        manager = AxisLineManager()
        df = create_test_dataframe(50)
        waves = create_test_waves()
        
        axis_lines = manager.identify_axis_lines_from_waves(waves, df)
        
        # 应该识别出轴线
        assert len(axis_lines) > 0
        
        # 检查轴线类型
        resistance_lines = [a for a in axis_lines if a.line_type == AxisLineType.RESISTANCE]
        support_lines = [a for a in axis_lines if a.line_type == AxisLineType.SUPPORT]
        
        assert len(resistance_lines) > 0  # 应该有阻力线
        assert len(support_lines) > 0     # 应该有支撑线
    
    def test_volume_density_calculation(self):
        """测试成交量密度计算"""
        manager = AxisLineManager()
        df = create_test_dataframe(50)
        
        # 计算某个价格水平的成交量密度
        price_level = 50500.0
        center_idx = 25
        
        volume_density = manager._calculate_volume_density(
            price_level, df, center_idx
        )
        
        # 成交量密度应该大于0
        assert volume_density > 0
        
        # 成交量密度应该是合理的数值
        assert volume_density < df['volume'].sum()
    
    def test_axis_strength_calculation(self):
        """测试轴线强度计算"""
        manager = AxisLineManager()
        df = create_test_dataframe(50)
        
        # 创建测试轴线
        axis = AxisLine(
            price_level=50500.0,
            formation_time=df.index[10],
            formation_idx=10,
            line_type=AxisLineType.SUPPORT,
            strength=AxisLineStrength.WEAK,
            test_count=3,
            last_test_time=df.index[10],
            last_test_idx=10,
            volume_density=5000000.0,
            avg_test_volume=2000000.0,
            strength_score=0.0,
            wave_high=51000.0,
            wave_low=50500.0,
            wave_volume=10000000.0
        )
        
        # 计算强度
        strength_score = manager._calculate_axis_strength(axis, df)
        
        # 强度评分应该在0-1之间
        assert 0.0 <= strength_score <= 1.0
    
    def test_axis_strength_classification(self):
        """测试轴线强度分类"""
        manager = AxisLineManager()
        
        # 测试强轴线
        assert manager._classify_axis_strength(0.8) == AxisLineStrength.STRONG
        
        # 测试中等轴线
        assert manager._classify_axis_strength(0.5) == AxisLineStrength.MEDIUM
        
        # 测试弱轴线
        assert manager._classify_axis_strength(0.2) == AxisLineStrength.WEAK
    
    def test_merge_nearby_axis_lines(self):
        """测试合并相近轴线"""
        manager = AxisLineManager()
        df = create_test_dataframe(50)
        
        # 创建两条相近的轴线
        axis1 = AxisLine(
            price_level=50500.0,
            formation_time=df.index[10],
            formation_idx=10,
            line_type=AxisLineType.SUPPORT,
            strength=AxisLineStrength.WEAK,
            test_count=1,
            last_test_time=df.index[10],
            last_test_idx=10,
            volume_density=3000000.0,
            avg_test_volume=2000000.0,
            strength_score=0.5,
            wave_high=51000.0,
            wave_low=50500.0,
            wave_volume=10000000.0
        )
        
        axis2 = AxisLine(
            price_level=50520.0,  # 相近价格（0.04%差距）
            formation_time=df.index[20],
            formation_idx=20,
            line_type=AxisLineType.SUPPORT,
            strength=AxisLineStrength.WEAK,
            test_count=1,
            last_test_time=df.index[20],
            last_test_idx=20,
            volume_density=5000000.0,  # 更高的成交量密度
            avg_test_volume=2500000.0,
            strength_score=0.6,
            wave_high=51000.0,
            wave_low=50520.0,
            wave_volume=12000000.0
        )
        
        # 合并
        merged = manager._merge_nearby_axis_lines([axis1, axis2])
        
        # 应该合并为一条
        assert len(merged) == 1
        
        # 应该保留成交量密度更高的
        assert merged[0].volume_density == 5000000.0
        
        # 测试次数应该累加
        assert merged[0].test_count == 2
    
    def test_get_nearest_axis(self):
        """测试获取最近轴线"""
        manager = AxisLineManager()
        df = create_test_dataframe(50)
        
        # 创建轴线
        axis1 = AxisLine(
            price_level=50500.0,
            formation_time=df.index[10],
            formation_idx=10,
            line_type=AxisLineType.SUPPORT,
            strength=AxisLineStrength.MEDIUM,
            test_count=2,
            last_test_time=df.index[10],
            last_test_idx=10,
            volume_density=5000000.0,
            avg_test_volume=2000000.0,
            strength_score=0.6,
            wave_high=51000.0,
            wave_low=50500.0,
            wave_volume=10000000.0
        )
        
        manager.axis_lines = [axis1]
        
        # 测试获取最近轴线
        nearest = manager.get_nearest_axis(50600.0)  # 距离约0.2%
        
        assert nearest is not None
        assert nearest.price_level == 50500.0
    
    def test_axis_line_validity(self):
        """测试轴线有效性"""
        df = create_test_dataframe(50)
        
        # 创建新轴线
        axis = AxisLine(
            price_level=50500.0,
            formation_time=df.index[10],
            formation_idx=10,
            line_type=AxisLineType.SUPPORT,
            strength=AxisLineStrength.MEDIUM,
            test_count=3,
            last_test_time=df.index[10],
            last_test_idx=10,
            volume_density=5000000.0,
            avg_test_volume=2000000.0,
            strength_score=0.6,
            wave_high=51000.0,
            wave_low=50500.0,
            wave_volume=10000000.0
        )
        
        # 测试有效性（刚创建，应该有效）
        current_time = df.index[20]
        assert axis.is_valid(current_time, max_age_hours=168)
        
        # 测试过期（超过7天）
        future_time = df.index[10] + timedelta(days=8)
        assert not axis.is_valid(future_time, max_age_hours=168)
    
    def test_axis_line_distance(self):
        """测试轴线距离计算"""
        df = create_test_dataframe(50)
        
        axis = AxisLine(
            price_level=50000.0,
            formation_time=df.index[10],
            formation_idx=10,
            line_type=AxisLineType.SUPPORT,
            strength=AxisLineStrength.MEDIUM,
            test_count=2,
            last_test_time=df.index[10],
            last_test_idx=10,
            volume_density=5000000.0,
            avg_test_volume=2000000.0,
            strength_score=0.6,
            wave_high=51000.0,
            wave_low=50000.0,
            wave_volume=10000000.0
        )
        
        # 测试距离计算
        distance = axis.distance_from_price(50500.0)
        expected_distance = 500.0 / 50000.0  # 1%
        
        assert abs(distance - expected_distance) < 0.0001
        
        # 测试是否接近
        assert axis.is_near_price(50250.0, threshold=0.01)  # 0.5%距离，阈值1%
        assert not axis.is_near_price(51000.0, threshold=0.01)  # 2%距离，阈值1%


def test_integration_workflow():
    """集成测试：完整工作流程"""
    # 1. 创建管理器
    manager = AxisLineManager(
        min_test_count=2,
        volume_density_window=20,
        price_tolerance=0.005,
        max_axis_age_hours=168
    )
    
    # 2. 创建测试数据
    df = create_test_dataframe(100)
    waves = create_test_waves()
    
    # 3. 识别轴线
    axis_lines = manager.identify_axis_lines_from_waves(waves, df)
    
    assert len(axis_lines) > 0
    print(f"✅ 识别出 {len(axis_lines)} 条轴线")
    
    # 4. 检查轴线属性
    for axis in axis_lines:
        assert axis.price_level > 0
        assert axis.strength_score >= 0.0
        assert axis.strength_score <= 1.0
        assert axis.test_count >= 1
        assert axis.volume_density > 0
        
        print(f"  - {axis.line_type.value} @ {axis.price_level:.2f}, "
              f"强度: {axis.strength.value}, 评分: {axis.strength_score:.2f}")
    
    # 5. 获取强轴线
    manager.axis_lines = axis_lines
    strong_lines = manager.get_strong_axis_lines()
    
    print(f"✅ 其中 {len(strong_lines)} 条为强轴线")
    
    # 6. 测试最近轴线查询
    test_price = 50800.0
    nearest = manager.get_nearest_axis(test_price, max_distance=0.02)
    
    if nearest:
        print(f"✅ 价格 {test_price} 最近的轴线: {nearest.line_type.value} @ {nearest.price_level:.2f}")
    
    print("\n✅ 集成测试通过！")


if __name__ == '__main__':
    print("=" * 60)
    print("威科夫轴线系统单元测试 - Phase 2.1.1")
    print("=" * 60)
    print()
    
    # 运行集成测试
    test_integration_workflow()
    
    print()
    print("=" * 60)
    print("运行完整测试套件...")
    print("=" * 60)
    
    # 运行pytest
    pytest.main([__file__, '-v'])
