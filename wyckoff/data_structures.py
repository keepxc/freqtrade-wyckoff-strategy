"""
威科夫数据结构模块
从原始策略文件中提取的核心数据结构
"""

from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple
from enum import Enum

@dataclass
class WyckoffWave:
    """威科夫波段数据结构"""
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
        """价格位移（Result）- 威科夫结果度量"""
        return abs(self.end_price - self.start_price)
    
    @property
    def effort(self) -> float:
        """成交量努力 - 威科夫努力度量"""
        return self.total_volume
    
    @property
    def efficiency(self) -> float:
        """移动效率（轻松感）- 威科夫核心指标"""
        return self.displacement / (self.effort + 1e-9)
    
    @property
    def is_up_wave(self) -> bool:
        return self.direction == 'up'

# AxisLine 在 axis_system.py 中定义，这里不重复定义

@dataclass
class AxisTestResult:
    """
    轴线测试结果 - 完整版
    
    包含测试分类、置信度、量价特征等
    """
    # 基本信息
    axis_line: 'AxisLine'
    test_type: str  # 'normal', 'absorption', 'spring', 'genuine_break', 'unclear'
    test_idx: int
    test_price: float
    test_time: 'datetime'
    
    # 量价特征
    volume_ratio: float
    range_ratio: float
    close_position: float
    
    # 置信度
    confidence_score: float  # 0.0-1.0
    
    # 状态
    confirmation_status: str  # 'pending', 'confirmed', 'rejected'

# 保留旧名称以兼容
TestResult = AxisTestResult
    
@dataclass
class ValidationResult:
    """验证结果数据结构"""
    is_valid: bool
    confidence: float
    supporting_evidence: List[str]

class WyckoffPhase(Enum):
    """威科夫阶段枚举"""
    PHASE_A = "accumulation_phase_a"
    PHASE_B = "accumulation_phase_b"
    PHASE_C = "accumulation_phase_c"
    PHASE_D = "accumulation_phase_d"
    PHASE_E = "markup_phase_e"
    UNKNOWN = "unknown"
