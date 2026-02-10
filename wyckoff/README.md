# 威科夫交易系统模块

这个文件夹包含威科夫交易方法的所有核心组件。

## 模块结构

### 核心模块
- `data_structures.py` - 威科夫数据结构定义
- `utils.py` - 威科夫工具函数和计算方法

### 扩展模块 (待实现)
- `weis_wave.py` - Weis Wave波段分析
- `axis_system.py` - 动态轴线系统
- `state_machine.py` - 威科夫状态机
- `signals.py` - 信号检测逻辑
- `exit_logic.py` - 退出决策系统

## 使用方法

```python
# 导入所有威科夫组件
from wyckoff import *

# 或者导入特定组件
from wyckoff.data_structures import WyckoffWave, AxisLine
from wyckoff.utils import calculate_wave_quality
```

## 版本历史

- v1.0.0 - 初始模块化版本，从单体文件重构而来
- 性能基准: 288.17%收益率，75.9%胜率，6.47%回撤

## 维护说明

- 所有模块修改都要经过回测验证
- 保持与原始性能基准的一致性
- 遵循威科夫理论的物理本质
