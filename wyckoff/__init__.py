"""
威科夫模块包 - 简化版本
包含威科夫交易方法的核心组件，避免复杂依赖
"""

# 导入核心数据结构和工具函数
try:
    # 使用相对导入避免路径问题
    import sys
    import os
    
    # 添加当前目录到路径
    current_dir = os.path.dirname(__file__)
    parent_dir = os.path.dirname(current_dir)
    if parent_dir not in sys.path:
        sys.path.append(parent_dir)
    
    # 导入核心模块
    from .wyckoff_dataclasses import *
    from .wyckoff_utils import *
    
    # 导入ExitLogic模块
    try:
        from .exit_logic import ExitLogic
    except ImportError:
        from exit_logic import ExitLogic
    
    # 导入Weis Wave Foundation模块
    try:
        from .weis_wave_foundation import (
            WeisWaveQualityFilter,
            QualityFilterResult,
            DivergenceLevel
        )
        WEIS_WAVE_FILTER_AVAILABLE = True
    except ImportError as e:
        print(f"Weis Wave Filter导入失败: {e}")
        WEIS_WAVE_FILTER_AVAILABLE = False
        # 提供回退定义
        class WeisWaveQualityFilter:
            def __init__(self, **kwargs):
                pass
            def apply_filter(self, dataframe):
                return None
        
        class QualityFilterResult:
            pass
        
        class DivergenceLevel:
            NONE = 0
            LEVEL_1_SOT = 1
            LEVEL_2_ANOMALY = 2
            LEVEL_3_CLIMAX = 3
    
    # 导入Axis Test Classifier模块
    try:
        from .axis_test_classifier import AxisTestClassifier
        AXIS_TEST_CLASSIFIER_AVAILABLE = True
    except ImportError as e:
        print(f"Axis Test Classifier导入失败: {e}")
        AXIS_TEST_CLASSIFIER_AVAILABLE = False
        # 提供回退定义
        class AxisTestClassifier:
            def __init__(self):
                pass
            def classify_test(self, dataframe, test_idx, axis):
                return None
    
    # 版本信息
    __version__ = "1.0.0"
    __author__ = "Wyckoff Trading System"
    
    print("威科夫模块包加载成功")
    
except ImportError as e:
    print(f"威科夫模块包加载失败: {e}")
    # 提供基本的回退定义
    from dataclasses import dataclass
    from enum import Enum
    
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
    
    @dataclass
    class AxisLine:
        price: float
        timestamp: int
        strength: float
        test_count: int
        line_type: str
    
    class WyckoffPhase(Enum):
        PHASE_A = "accumulation_phase_a"
        PHASE_B = "accumulation_phase_b" 
        PHASE_C = "accumulation_phase_c"
        PHASE_D = "accumulation_phase_d"
        PHASE_E = "markup_phase_e"
        UNKNOWN = "unknown"
    
    def calculate_wave_quality(displacement: float, effort: float, bar_count: int) -> float:
        if effort <= 0 or bar_count <= 0:
            return 0.0
        efficiency = displacement / effort
        time_factor = max(0.1, 1.0 - (bar_count / 100.0))
        return min(1.0, efficiency * time_factor * 10)
    
    # 提供ExitLogic回退定义
    class ExitLogic:
        def __init__(self):
            pass
    
    print("使用威科夫模块回退定义")

# 导出的符号
__all__ = [
    'WyckoffWave',
    'AxisLine', 
    'WyckoffPhase',
    'calculate_wave_quality',
    'detect_effort_result_divergence',
    'calculate_atr_normalized_displacement',
    'calculate_volume_effort',
    'detect_spring_action',
    'ExitLogic',  # ExitLogic导出
    'WeisWaveQualityFilter',  # Weis Wave过滤器
    'QualityFilterResult',  # 过滤结果
    'DivergenceLevel',  # 背离等级
    'AxisTestClassifier'  # 轴线测试分类器
]