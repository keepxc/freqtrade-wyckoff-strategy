"""
威科夫数据结构 - 简化版本
只包含核心数据结构，避免复杂依赖
"""

from dataclasses import dataclass
from typing import Optional, List
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
        """位移 - 价格变化幅度"""
        return abs(self.high_price - self.low_price)
    
    @property
    def effort(self) -> float:
        """努力 - 成交量"""
        return self.total_volume
    
    @property
    def efficiency(self) -> float:
        """效率 - 位移/努力"""
        if self.effort == 0:
            return 0
        return self.displacement / self.effort
    
    @property
    def is_up_wave(self) -> bool:
        """是否为上升波段"""
        return self.direction == 'up'

@dataclass
class AxisLine:
    """轴线数据结构"""
    price: float
    timestamp: int
    strength: float
    test_count: int
    line_type: str  # 'support' or 'resistance'

class WyckoffPhase(Enum):
    """威科夫阶段枚举"""
    PHASE_A = "accumulation_phase_a"
    PHASE_B = "accumulation_phase_b" 
    PHASE_C = "accumulation_phase_c"
    PHASE_D = "accumulation_phase_d"
    PHASE_E = "markup_phase_e"
    UNKNOWN = "unknown"
