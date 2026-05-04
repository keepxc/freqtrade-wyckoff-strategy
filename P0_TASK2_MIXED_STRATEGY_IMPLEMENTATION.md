# P0-Task-2 混合策略实施总结

## 实施日期
2026-05-04

## 背景

基于 P0-Task-1.5 预分析结果，发现：
- ❌ **原假设错误**：90.8% 的 axis_support 在 TR 上半段
- ✅ **实际数据**：64.4% 在 TR 下半段，35.6% 在 TR 上半段
- 🔴 **关键发现**：全面切换到日线 swing 会导致 TR 上半段目标位过近（+0.37%）

因此采用**混合策略**而非全面切换。

## 实施内容

### 1. 策略文件修改（WyckoffV7_TrueMethod.py）

**文件**：`user_data/strategies/WyckoffV7_TrueMethod.py`

**修改位置**：`populate_indicators()` 中的日线数据处理部分

**新增代码**：
```python
# P0-Task-2: 计算日线 swing 点（20 根日线 = 约 20 天）
df_1d['daily_swing_high'] = df_1d['high'].rolling(20).max()
df_1d['daily_swing_low'] = df_1d['low'].rolling(20).min()
df_1d['daily_swing_height'] = df_1d['daily_swing_high'] - df_1d['daily_swing_low']
```

**merge 字段**：
- `daily_swing_high_1d`
- `daily_swing_low_1d`
- `daily_swing_height_1d`

### 2. PnF 目标位计算器修改（pnf_target_calculator.py）

**文件**：`wyckoff/pnf_target_calculator.py`

**修改位置**：`_calculate_long_side()` 中的 fallback 逻辑

**混合策略逻辑**：

```python
# 1. 计算 1H rolling window 的 TR 位置
tr_position_1h = (close - rolling_low) / (rolling_high - rolling_low)

# 2. 根据 TR 位置选择目标位来源
if has_daily_swing and tr_position_1h < 0.5:
    # TR 下半段：使用日线 swing（64.4%的交易）
    tr_low = daily_swing_low_1d
    tr_high = daily_swing_high_1d
    tr_source = 'daily_swing'
else:
    # TR 上半段：使用 1H rolling window（35.6%的交易）
    tr_low = rolling_low_120
    tr_high = rolling_high_120
    tr_source = 'rolling_window'
```

## 混合策略决策表

| TR 位置（基于 1H rolling） | 目标位选择 | 交易占比 | 预期改善 | 理由 |
|--------------------------|-----------|---------|---------|------|
| 0.0-0.3 | 日线 swing | 37.4% | +2.52pp | 目标位距离从 +4.19% 提升到 +6.71% |
| 0.3-0.5 | 日线 swing | 27.0% | +1.36pp | 目标位距离从 +1.49% 提升到 +2.85% |
| 0.5-0.7 | 1H rolling | 20.7% | 保持 | 日线目标仅 +1.36%，改善幅度小 |
| 0.7-1.0 | 1H rolling | 14.9% | 保持 | 日线目标位在入场价下方（-1.00%） |

## 预期效果

### TR < 0.5 区间（64.4%的交易）
- TR Height：从 ~7000 提升到 ~15000（+103-116%）
- 目标位距离：平均提升 +1.94pp
- 胜率：保持或提升（63.1-74.5%）

### TR ≥ 0.5 区间（35.6%的交易）
- TR Height：保持 ~7000
- 目标位距离：保持原有水平
- 胜率：不劣化

### 整体预期
- axis_support 胜率：从 62.6% 提升到 63-64%（+0.5-1pp）
- 总利润：从 +129.35% 提升到 +130-135%
- 最大回撤：保持 ≤ 7%
- wave_start：不受影响（主要在 TR 顶部，使用 1H rolling）

## 验收标准

### 基本指标
- [ ] 总利润 ≥ 125%
- [ ] 总胜率 ≥ 67%
- [ ] 最大回撤 ≤ 7%
- [ ] wave_start 胜率 ≥ 90%
- [ ] axis_support 胜率 ≥ 63%

### TR 分段验证
- [ ] TR 0.0-0.3：平均利润 ≥ +1.2%（从 +0.94% 提升）
- [ ] TR 0.3-0.5：平均利润 ≥ +1.3%（从 +1.14% 提升）
- [ ] TR 0.5-0.7：平均利润 ≥ +0.9%（不劣化）
- [ ] TR 0.7-1.0：平均利润 ≥ -0.5%（不显著劣化）

### 混合策略验证
- [ ] `tr_source=daily_swing` 占比约 60-65%
- [ ] `tr_source=rolling_window` 占比约 35-40%
- [ ] TR < 0.5 区间的 TR Height 均值 ≥ 15000
- [ ] TR < 0.5 区间的目标位距离平均提升 ≥ +1.5pp

## 回测命令

```bash
docker compose run --rm freqtrade backtesting \
  --config user_data/config_backtest.json \
  --strategy WyckoffV7_TrueMethod \
  --timerange 20250101-20260330 \
  --cache none \
  2>&1 | tee backtest_p0_mixed_strategy.log
```

## Phase 2 决策点

如果 P0 验收通过，TR 0.7-1.0 区间仍然是负期望（胜率 < 40%），考虑：

| 选项 | 实施难度 | 风险 | 预期效果 | 优先级 |
|------|---------|------|---------|--------|
| A. 直接过滤 TR ≥ 0.7 | ⭐ 最简单 | 低 | 减少 14.9% 负期望入场 | 🥇 第一优先 |
| B. 用 4H swing 替代 | ⭐⭐ 适中 | 中 | 逻辑更合理，增加复杂度 | 🥈 第二优先 |
| C. 压缩目标倍数 | ⭐ 简单 | 中 | 治标不治本 | 🥉 第三优先 |

## 相关文档

- 预分析脚本：`analyze_daily_swing_target_impact.py`
- 预分析数据：`daily_swing_target_impact_analysis.json`
- Roadmap：`WYCKOFF_FIX_ROADMAP.md`（已更新）
- 策略文件：`user_data/strategies/WyckoffV7_TrueMethod.py`（已修改）
- 目标位计算器：`wyckoff/pnf_target_calculator.py`（已修改）

## 下一步

1. ✅ 更新 roadmap 文档
2. ✅ 实施混合策略代码
3. ⏳ 运行回测验证
4. ⏳ 分析回测结果
5. ⏳ 决定是否进入 Phase 2（TR 0.7-1.0 处理）
