# TR 边界检测优化方案
## 威科夫"因果律"物理归因与修复

**问题根源**：Trade #10/11 过早退出（+1.23%/+1.79%），错失后续涨至 80065 的利润空间

**物理归因**：
- **刻舟求剑的 TR 检测**：120 根 K 线（5 天）只捕捉到局部微观波动
- **因果错位**：识别的是"局部小因"（TR Height ~2000），只能得到"局部小果"（target ~79000）
- **真实的因**：4月底开始的更大规模积累结构（TR Height ~5000，target 应为 ~80000）

---

## P1: 添加 TR 边界诊断日志（立即实施）

### 目标
让系统输出 TR 边界的完整来源链，便于法医鉴定

### 改动位置
1. `wyckoff/pnf_target_calculator.py` - `_calculate_long_side()` 方法
2. `user_data/strategies/WyckoffV7_TrueMethod.py` - `leverage()` 函数

### 实施方案

#### 1.1 在 pnf_target_calculator.py 中添加来源标记

```python
# 在 _calculate_long_side() 中添加 tr_source 列
def _init_columns(self, dataframe: DataFrame) -> None:
    # ... 现有代码 ...
    dataframe['tr_source'] = 'unknown'  # 新增：记录 TR 来源
    dataframe['tr_source_detail'] = ''  # 新增：详细信息

# 在 TR 计算逻辑中标记来源
for i in range(bars):
    if bool(sc_mask.iloc[i]):
        last_sc_low = float(dataframe['low'].iloc[i])
        running_high_from_sc = float(dataframe['high'].iloc[i])

    if last_sc_low > 0:
        running_high_from_sc = max(running_high_from_sc, float(dataframe['high'].iloc[i]))
        tr_low[i] = last_sc_low
        tr_high[i] = running_high_from_sc
        tr_source[i] = 'SC_anchor'  # 🆕 标记来源
        tr_source_detail[i] = f'SC@{last_sc_low:.0f}'
    elif phase_tr_valid_vals[i] and phase_tr_low_vals[i] > 0:
        tr_low[i] = float(phase_tr_low_vals[i])
        tr_high[i] = float(phase_tr_high_vals[i])
        tr_source[i] = 'phase_context'  # 🆕 标记来源
        tr_source_detail[i] = f'Phase[{phase_tr_low_vals[i]:.0f}-{phase_tr_high_vals[i]:.0f}]'
    else:
        # Fallback 1: 滚动窗口
        tr_low[i] = float(fallback_low[i])
        tr_high[i] = float(fallback_high[i])
        tr_source[i] = 'rolling_window'  # 🆕 标记来源
        tr_source_detail[i] = f'Window{self.lookback_fallback}'
        
        # Fallback 2: Swing high/low
        atr_val = dataframe.get('atr_20', ...).iloc[i]
        if atr_val > 0 and (tr_high[i] - tr_low[i]) < (atr_val * 2.0):
            swing_window = 20
            start_idx = max(0, i - swing_window)
            swing_low = float(dataframe['low'].iloc[start_idx:i+1].min())
            swing_high = float(dataframe['high'].iloc[start_idx:i+1].max())
            
            if (swing_high - swing_low) > (tr_high[i] - tr_low[i]):
                tr_low[i] = swing_low
                tr_high[i] = swing_high
                tr_source[i] = 'swing_fallback'  # 🆕 标记来源
                tr_source_detail[i] = f'Swing{swing_window}[{swing_low:.0f}-{swing_high:.0f}]'

# 存储到 dataframe
dataframe['tr_source'] = tr_source
dataframe['tr_source_detail'] = tr_source_detail
```

#### 1.2 在 leverage() 中输出诊断日志

```python
# 在 leverage() 的 P&F 目标位锁定日志中添加 TR 边界信息
if side == 'long':
    tr_source = last.get('tr_source', 'unknown')
    tr_source_detail = last.get('tr_source_detail', '')
    
    logger.info(
        f'✅ P&F 目标位已锁定 [{pair}] (long): '
        f'conservative={pnf_meta["target_conservative"]:.2f}, '
        f'standard={pnf_meta["target_standard"]:.2f}, '
        f'open_date={trade.open_date_utc} | '
        f'🔍 TR边界: low={pnf_meta["tr_low"]:.0f}, '
        f'high={pnf_meta["tr_high"]:.0f}, '
        f'height={pnf_meta["tr_height"]:.0f}, '
        f'source={tr_source}, detail={tr_source_detail}'
    )
```

### 预期输出

```
✅ P&F 目标位已锁定 [BTC/USDT:USDT] (long): conservative=79000.00, standard=81500.00, open_date=2026-05-01 12:00:00 | 🔍 TR边界: low=77000, high=79000, height=2000, source=rolling_window, detail=Window120
```

**诊断价值**：
- 立即看出 TR 来源（SC 锚点 vs Phase Context vs 滚动窗口）
- 发现 TR Height 过小（2000 vs 预期 5000）
- 为 P2 优化提供数据支撑

---

## P2: 优化 TR 检测窗口（验证后实施）

### 目标
扩大 TR 识别范围，捕捉更深厚的"底座"（威科夫吸筹区间）

### 改动位置
`wyckoff/pnf_target_calculator.py` - `__init__()` 和 `_calculate_long_side()`

### 实施方案

#### 2.1 扩大滚动窗口：120 → 240 根

```python
def __init__(self, strategy=None):
    self.strategy = strategy
    self.lookback_fallback = 240  # 🔧 从 120 增加到 240（10 天）
    self.utad_pre_high_lookback = 20
```

**物理依据**：
- 威科夫指出，吸筹需要充足的时间完成筹码交换
- 从图中看，真正的 TR 形成周期是 7-10 天（4月28日 - 5月4日）
- 240 根 1h K 线 = 10 天，能覆盖完整的积累结构

#### 2.2 改进 Swing High/Low 检测周期

```python
# 当前：swing_window = 20（不足以捕捉大周期 swing）
# 优化：根据 ATR 动态调整 swing 窗口

# 在 Fallback 2 逻辑中
atr_val = dataframe.get('atr_20', ...).iloc[i]
if atr_val > 0 and (tr_high[i] - tr_low[i]) < (atr_val * 2.0):
    # 🔧 动态 swing 窗口：ATR 越大，窗口越大
    # 小波动市场：20 根，大波动市场：最多 168 根（7 天）
    swing_window = min(168, max(20, int(60 * (atr_val / dataframe['close'].iloc[i]))))
    
    start_idx = max(0, i - swing_window)
    swing_low = float(dataframe['low'].iloc[start_idx:i+1].min())
    swing_high = float(dataframe['high'].iloc[start_idx:i+1].max())
    
    if (swing_high - swing_low) > (tr_high[i] - tr_low[i]):
        tr_low[i] = swing_low
        tr_high[i] = swing_high
        tr_source[i] = 'swing_fallback'
        tr_source_detail[i] = f'DynamicSwing{swing_window}[{swing_low:.0f}-{swing_high:.0f}]'
```

#### 2.3 添加成交量验证（威科夫"努力与结果"）

```python
# 🆕 在 TR 确定后，验证 SC-AR 段的成交量
# 如果"努力"很大（高成交量），说明吸筹充分，目标位应该更激进

def _validate_tr_with_volume(self, dataframe: DataFrame, i: int, tr_low: float, tr_high: float) -> dict:
    """
    验证 TR 边界的成交量特征
    返回：{'effort_score': float, 'should_boost': bool}
    """
    # 查找 SC 到当前的成交量
    sc_idx = None
    for j in range(max(0, i - 240), i):
        if dataframe['climatic_type'].iloc[j] == 'SC':
            sc_idx = j
            break
    
    if sc_idx is None:
        return {'effort_score': 0.0, 'should_boost': False}
    
    # 计算 SC-AR 段的平均成交量 vs 全局平均
    sc_ar_volume = dataframe['volume'].iloc[sc_idx:i+1].mean()
    global_volume = dataframe['volume'].iloc[max(0, i-240):i+1].mean()
    
    effort_score = sc_ar_volume / global_volume if global_volume > 0 else 1.0
    
    # 如果 effort_score > 1.5（成交量显著放大），建议提升目标位
    should_boost = effort_score > 1.5
    
    return {
        'effort_score': effort_score,
        'should_boost': should_boost,
        'sc_ar_volume': sc_ar_volume,
        'global_volume': global_volume
    }

# 在 TR 计算后调用
volume_validation = self._validate_tr_with_volume(dataframe, i, tr_low[i], tr_high[i])
if volume_validation['should_boost']:
    # 记录到 dataframe，供策略层决策
    dataframe.loc[i, 'tr_volume_boost'] = True
    dataframe.loc[i, 'tr_effort_score'] = volume_validation['effort_score']
```

---

## P3: 动态 axis_mult（长期优化）

### 目标
根据市场环境和成交量特征，动态调整 P&F 目标位乘数

### 实施方案

```python
# 在 custom_exit() 的 P&F 退出逻辑中
if entry_tag == 'wyckoff_axis_support' and tr_low > 0 and tr_height > 0:
    # 🆕 动态 axis_mult
    base_mult = self.pnf_axis_mult  # 默认 1.0
    
    # 因素 1：成交量验证
    tr_volume_boost = last.get('tr_volume_boost', False)
    effort_score = last.get('tr_effort_score', 1.0)
    if tr_volume_boost and effort_score > 2.0:
        # 强势突破（需求完全压倒供应）
        mult_adjustment = 0.5  # 1.0 → 1.5
    elif tr_volume_boost and effort_score > 1.5:
        # 中等强度突破
        mult_adjustment = 0.2  # 1.0 → 1.2
    else:
        mult_adjustment = 0.0
    
    # 因素 2：TR 来源（SC 锚点更可靠）
    tr_source = last.get('tr_source', 'unknown')
    if tr_source == 'SC_anchor':
        source_boost = 0.0  # SC 锚点已经很可靠
    elif tr_source == 'phase_context':
        source_boost = 0.0
    else:
        source_boost = -0.2  # 滚动窗口/swing fallback 更保守
    
    # 最终 mult
    final_mult = base_mult + mult_adjustment + source_boost
    final_mult = max(0.5, min(2.0, final_mult))  # 限制在 0.5-2.0 之间
    
    adjusted = tr_low + tr_height * final_mult
    
    logger.info(
        f'🎯 动态 P&F 目标位 [{pair}]: '
        f'base_mult={base_mult:.1f}, '
        f'effort_adj={mult_adjustment:.1f}, '
        f'source_adj={source_boost:.1f}, '
        f'final_mult={final_mult:.1f}, '
        f'target={adjusted:.0f}'
    )
```

---

## 实施优先级

### 立即执行（今天）
- ✅ **P0 验证**：运行 `diagnose_trade10_metadata.sh` 确认元数据链是否生效
- ✅ **P1 实施**：添加 TR 边界诊断日志（30 分钟工作量）

### 验证后执行（1-2 天内）
- ⏳ **P2.1 实施**：扩大滚动窗口 120 → 240（5 分钟）
- ⏳ **P2.2 实施**：改进 Swing 检测（15 分钟）
- ⏳ **P2.3 实施**：添加成交量验证（30 分钟）

### 长期优化（1-2 周内）
- 📋 **P3 实施**：动态 axis_mult（需要累积 30+ 笔样本验证）

---

## 预期效果

### Trade #10 场景重现
**修复前**：
```
TR边界: low=77000, high=79000, height=2000, source=rolling_window
target_conservative = 77000 + 2000 * 1.0 = 79000
退出价格: 79288 (+1.23%)
错失利润: 80065 - 79288 = 777 点 (+1%)
```

**修复后（P1+P2）**：
```
TR边界: low=75000, high=80000, height=5000, source=rolling_window(240)
target_conservative = 75000 + 5000 * 1.0 = 80000
预期退出: ~80000 (+2.3%)
额外利润: +1%
```

**修复后（P1+P2+P3）**：
```
TR边界: low=75000, high=80000, height=5000, source=rolling_window(240)
成交量验证: effort_score=2.1, should_boost=True
动态 mult: 1.0 + 0.5 = 1.5
target_standard = 75000 + 5000 * 1.5 = 82500
预期退出: ~82000+ (+5%+)
额外利润: +3%+
```

---

## 风险评估

### P1（诊断日志）
- ✅ **零风险**：只添加日志，不改变逻辑
- ✅ **高价值**：立即可见 TR 检测问题

### P2.1（扩大窗口）
- ⚠️ **中等风险**：可能在震荡市中识别过大的 TR
- ✅ **缓解措施**：保留 Swing fallback 作为二级验证
- ✅ **回测验证**：在 20220101-20260504 全周期回测验证

### P2.2（动态 Swing）
- ⚠️ **低风险**：只在滚动窗口失效时触发
- ✅ **缓解措施**：限制最大窗口 168 根（7 天）

### P2.3（成交量验证）
- ✅ **零风险**：只记录标记，不直接改变目标位
- ✅ **为 P3 提供数据支撑**

### P3（动态 mult）
- ⚠️ **高风险**：直接改变退出逻辑
- ✅ **缓解措施**：
  - 限制 mult 范围 0.5-2.0
  - 需要 30+ 笔样本验证
  - 默认关闭，通过环境变量启用

---

## 验收标准

### P1 验收
- [ ] 每笔入场日志包含 TR 边界信息
- [ ] 能清晰看出 TR 来源（SC/Phase/Rolling/Swing）
- [ ] TR Height 值合理（不再出现 2000 vs 5000 的错位）

### P2 验收
- [ ] 回测 20220101-20260504，总利润不低于当前 283K USDT
- [ ] axis_support 平均退出利润提升 0.5-1%
- [ ] 无因 TR 过大导致的长期持仓（>7 天）

### P3 验收
- [ ] 累积 30+ 笔 axis_support 样本
- [ ] 动态 mult 样本中，至少 50% 退出利润 > 静态 mult
- [ ] 无因过度激进导致的错失退出（价格回撤未达目标）

---

## 下一步行动

1. **立即**：用户在 dry-run 服务器运行 `diagnose_trade10_metadata.sh`
2. **确认 P0**：验证元数据链是否生效
3. **实施 P1**：添加 TR 边界诊断日志
4. **部署测试**：在 dry-run 观察 2-3 笔新交易
5. **决策 P2**：根据 P1 日志决定是否需要扩大窗口
6. **规划 P3**：设计动态 mult 的实验矩阵
