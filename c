# RM-3 动态仓位管理 - 快速启用指南

**状态**: ✅ 已验证，建议启用  
**模式**: defensive（保守）  
**风险**: 低（仅 -15% 调整，不放大）

---

## 一、验证结果摘要

### 质量分层表现（20230101-20260228）

| 分层 | 数量 | 胜率 | PF | 仓位倍数 |
|------|------|------|-----|---------|
| High | 88 | 96.6% | 39.14 | 1.0x |
| Medium | 263 | 68.4% | 2.25 | 0.85x |

**结论**: high/medium 分层稳定可靠（占 93.9%），建议启用。

---

## 二、启用步骤（3步）

### 步骤 1：集成到策略

在 `user_data/strategies/WyckoffV7_TrueMethod.py` 的 `__init__` 方法中添加：

```python
# RM-3: 动态仓位管理器（在所有 detector 初始化之后）
from wyckoff.position_manager import create_position_manager

self.position_manager = create_position_manager(
    self,
    mode=os.getenv('WY_POSITION_MODE', 'off'),  # 默认关闭
)
```

### 步骤 2：添加仓位计算方法

在策略中添加 `custom_stake_amount` 方法：

```python
def custom_stake_amount(
    self,
    pair: str,
    current_time: datetime,
    current_rate: float,
    proposed_stake: float,
    min_stake: Optional[float],
    max_stake: float,
    leverage: float,
    entry_tag: Optional[str],
    side: str,
    **kwargs
) -> float:
    """
    RM-3: 动态仓位计算
    
    根据信号质量调整仓位大小：
    - High 信号（wave_start, lps）: 1.0x
    - Medium 信号（axis_support, sos）: 0.85x
    """
    # 如果未启用，返回默认仓位
    if self.position_manager is None:
        return proposed_stake
    
    # 从 entry_tag 推断信号类型
    signal_type = entry_tag or 'unknown'
    
    # 获取当前数据
    dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
    if dataframe.empty:
        return proposed_stake
    
    current_idx = len(dataframe) - 1
    
    # 计算动态仓位
    position_multiplier, metadata = self.position_manager.calculate_position_size(
        dataframe,
        current_idx,
        signal_type,
        trade_history=None  # 可选：传入交易历史
    )
    
    # 应用倍数
    adjusted_stake = proposed_stake * position_multiplier
    
    # 限制范围
    adjusted_stake = max(min_stake or 0, min(adjusted_stake, max_stake))
    
    # 记录日志（可选）
    if position_multiplier != 1.0:
        logger.info(
            f"RM-3 Position adjustment: {pair} {signal_type} "
            f"quality={metadata['quality_score']:.1f} "
            f"tier={metadata['quality_tier']} "
            f"multiplier={position_multiplier:.2f}x"
        )
    
    return adjusted_stake
```

### 步骤 3：运行对比回测

```bash
# 1. 基线回测（固定仓位）
docker compose run --rm freqtrade backtesting \
  --config user_data/config_backtest.json \
  --strategy WyckoffV7_TrueMethod \
  --timerange 20230101-20260228 \
  --cache none

# 记录结果：总收益、最大回撤、Sharpe Ratio

# 2. 动态仓位回测
export WY_POSITION_MODE=defensive
docker compose run --rm freqtrade backtesting \
  --config user_data/config_backtest.json \
  --strategy WyckoffV7_TrueMethod \
  --timerange 20230101-20260228 \
  --cache none

# 3. 对比结果
# - 收益改善 > 0 ✅
# - 回撤增幅 < 2% ✅
# - Sharpe Ratio ≥ 基线 ✅
```

---

## 三、验证标准

### 必须满足（3项）

1. ✅ **收益改善**: 总收益 > 基线
2. ✅ **风险可控**: 最大回撤增幅 < 2%
3. ✅ **稳定性**: Sharpe Ratio ≥ 基线

### 如果不满足

```bash
# 立即关闭
export WY_POSITION_MODE=off

# 或调整参数
# 在 position_manager.py 中修改 PositionConfig
```

---

## 四、启用生产

### 如果回测验证通过

```bash
# 1. 设置环境变量
export WY_POSITION_MODE=defensive

# 2. 启动 dry-run 测试
docker compose run --rm freqtrade trade \
  --config user_data/config_dryrun.json \
  --strategy WyckoffV7_TrueMethod

# 3. 监控 24 小时
# - 检查仓位调整日志
# - 验证 high/medium 信号分布
# - 确认无异常

# 4. 如果正常，启用实盘
docker compose up -d freqtrade
```

---

## 五、监控指标

### 日常监控

1. **仓位分布**
   - High 信号: ~23%（88/374）
   - Medium 信号: ~70%（263/374）

2. **调整频率**
   - Medium 信号降低 15%
   - 环境调整触发次数

3. **绩效对比**
   - 总收益 vs 基线
   - 最大回撤 vs 基线
   - Sharpe Ratio vs 基线

### 异常处理

```bash
# 如果发现异常
export WY_POSITION_MODE=off

# 重启服务
docker compose restart freqtrade
```

---

## 六、回滚方案

### 立即回滚

```bash
# 1. 关闭动态仓位
export WY_POSITION_MODE=off

# 2. 重启服务
docker compose restart freqtrade

# 3. 验证恢复
# 检查日志确认使用固定仓位
```

### 代码回滚

```python
# 注释掉 position_manager 初始化
# self.position_manager = create_position_manager(...)
self.position_manager = None

# 或删除 custom_stake_amount 方法
```

---

## 七、常见问题

### Q1: 为什么只调整 medium 信号？

**A**: 保守策略，先验证效果。High 信号表现优异（96.6%），保持不变。

### Q2: 为什么不放大高质量信号？

**A**: 当前阶段优先风险控制，不追求激进收益。验证稳定后可考虑。

### Q3: 如果效果不好怎么办？

**A**: 立即关闭（`export WY_POSITION_MODE=off`），系统自动回到固定仓位。

### Q4: 需要修改配置文件吗？

**A**: 不需要。仅通过环境变量控制，不修改任何配置文件。

---

## 八、技术支持

### 相关文档

- `wyckoff/RM3_POSITION_MANAGER_GUIDE.md` - 完整使用指南
- `RM3_FINAL_VALIDATION_REPORT.md` - 验证报告
- `TASK_NEW/TASKS_with_checklist.md` - RM-3 任务详情

### 日志位置

```bash
# 查看仓位调整日志
docker compose logs freqtrade | grep "RM-3 Position"

# 查看统计信息
docker compose logs freqtrade | grep "PositionManager"
```

---

**最后更新**: 2026-03-14  
**状态**: ✅ 已验证，建议启用  
**风险等级**: 低  
**预期收益**: 风险收益比改善
