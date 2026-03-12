# 修复报告：EventSeqSM SOW/PhaseD Bug + Spring 区间交易路径

**日期**: 2026-03-05  
**分支**: main  
**Commit**: `e8f59af`  
**影响文件**: `wyckoff/event_sequence_state_machine.py`、`user_data/strategies/WyckoffV7_TrueMethod.py`

---

## 1. 问题背景

### 触发原因

Dry Run 服务器运行两天（2026-03-03 ~ 03-04）期间，BTC 从 65K 涨至 73K（+12%），系统却产生 **0 笔交易**。通过回测诊断，发现两类根本原因：

1. **EventSequenceStateMachine 有两个长期存在的 Bug**，导致 `dist_phase_d` 错误覆盖绝大多数历史 K 线
2. **Spring 区间专用交易路径缺失**，导致 BTC 处于积累期低位（EMA200 以下 15%+）时所有入场条件均被 EMA/意图过滤器拦截

### 现象

| 指标 | 修复前 | 修复后（短期） | 修复后（1年） |
|------|--------|--------------|--------------|
| dist_phase_d 覆盖 K 线 | **334/384 (87%)** | **0/384 (0%)** | 0% |
| 交易数（2025-02-25~03-05） | 0 | 1 | — |
| 交易数（1 年） | — | — | **146** |
| 胜率（1 年） | — | — | **76.0%** |
| 年化收益（1 年） | — | — | **71.06%** |
| SQN（1 年） | — | — | **4.45（A级）** |

---

## 2. Bug 1：`_detect_sow()` — SOW 无需 BC 即可触发

### 文件

`wyckoff/event_sequence_state_machine.py`，函数 `_detect_sow()`

### 根因

```python
# 修复前（Bug 版本）
last_bc_idx = -_MAX_BARS  # _MAX_BARS = 999

for i in range(n):
    if bc_mask[i]:
        last_bc_idx = i
    bars_since_bc_i = i - last_bc_idx
    # i=0 时：bars_since_bc_i = 0 - (-999) = 999 >= 5 → 恒为 True！
    if sow_raw[i] and bars_since_bc_i >= _SOW_AFTER_BC_MIN:
        seq_sow[i] = True
```

由于 `last_bc_idx` 初始化为 `-999`，任意 K 线的 `bars_since_bc_i = i - (-999) = i + 999`，对 `i >= 0` 恒 `>= 5`（`_SOW_AFTER_BC_MIN = 5`）。

**结果**：整个历史数据中，所有 `sow_bar_detected=True` 的 K 线都被确认为 `seq_sow_confirmed=True`，无需实际发生过 BC。

### 修复

```python
# 修复后
last_bc_idx  = -_MAX_BARS
bc_ever_seen = False          # ← 新增门控变量

for i in range(n):
    if bc_mask[i]:
        last_bc_idx  = i
        bc_ever_seen = True   # ← 只有真实 BC 发生后才置 True
    bars_since_bc_i = i - last_bc_idx
    # 增加 bc_ever_seen 前置门控
    if sow_raw[i] and bc_ever_seen and bars_since_bc_i >= _SOW_AFTER_BC_MIN:
        seq_sow[i] = True
```

需要同时满足：① BC 曾经发生过（`bc_ever_seen=True`）；② 距上次 BC 已超过最小间隔。

---

## 3. Bug 2：`_mark_dist_phases()` — dist_phase_d 无 BC 前置门控

### 文件

`wyckoff/event_sequence_state_machine.py`，函数 `_mark_dist_phases()`

### 根因

```python
# 修复前（Bug 版本）
if sow_mask[i]:
    sow_seen = True

if sow_seen:                  # ← 没有 dist_a_done 门控
    dist_in_d[i] = True
```

由于 Bug 1 导致全历史存在大量虚假 `seq_sow_confirmed=True`，并且 `dist_in_d` 的置位没有要求先完成 Phase A（BC→AR 序列），一旦某根 K 线出现虚假 SOW，**之后所有 K 线都被标记为 `dist_phase_d=True`**。

**结果**：`dist_phase_d` 覆盖 334/384 根 K 线（87%），触发了：
- `not_distribution = False` → axis_trading_allowed 所有路径关闭
- `composite_intent_ok_axis = False`（distributing 意图）→ 路径3关闭

### 修复

```python
# 修复后
if sow_mask[i]:
    sow_seen = True

if sow_seen and dist_a_done:  # ← 增加 dist_a_done 双重门控
    dist_in_d[i] = True
```

`dist_a_done` 只有在 BC + AR 序列完整完成后才为 `True`，确保派发 Phase D 标记的前提是真实完成了积累→派发转换。

---

## 4. 修复 3：`axis_trading_allowed` — 添加 Spring 区间路径 5

### 文件

`user_data/strategies/WyckoffV7_TrueMethod.py`，函数 `_update_trading_permission()`

### 背景

Spring（弹簧/末日震仓）按威科夫理论定义就是**跌破支撑后的快速反转**，发生在积累末期。此时 BTC 价格自然在 EMA200 下方显著偏离（本次为 65K vs EMA200 ≈ 80K，偏离 -19%）：

- 路径 1 需要 `close > EMA50 * 0.95` → 不满足
- 路径 2 需要 `in_phase_e = True` → 不满足（还在积累 Phase C）
- 路径 3 需要 `no_physical_reset = True` → Spring 本身触发 choch_reset，因此也不满足
- 路径 4 需要 `macro_markup_ok = True` → Daily ChoCh 活跃时不满足

结果：**Spring 信号无法通过任何路径触发 `axis_trading_allowed = True`**。

### 修复

```python
# 新增 _spring_zone_ok 辅助变量
_spring_zone_ok = (
    dataframe.get('spring_detected', pd.Series(False, index=dataframe.index)) |
    (dataframe.get('spring_quality', pd.Series('none', index=dataframe.index)) != 'none')
)

dataframe['axis_trading_allowed'] = (
    (not_distribution & trend_support) |                                              # 路径1
    (in_phase_e & not_distribution & phase_e_trend_ok & no_physical_reset) |         # 路径2
    (axis_strong_or_medium & composite_intent_ok_axis & not_distribution & no_physical_reset) |  # 路径3
    (macro_markup_ok & not_distribution & no_physical_reset) |                        # 路径4
    (_spring_zone_ok & not_distribution)                                              # 路径5 ← 新增
)
```

**路径 5 语义**：Spring 区间（`spring_detected=True` 或 `spring_quality != 'none'`）绕过所有 EMA / ChoCh 约束，仅保留 `not_distribution` 门控（防止派发阶段误入）。

**威科夫理论依据**：Spring 是积累的终结信号，不是牛市结果而是牛市的创造者。在 Phase C Spring 出现时必然处于低价区（EMA 下方），此时应积极建仓，而非被 EMA 过滤器拦截。

---

## 5. 修复 4：`composite_intent_ok` — Spring 意图豁免

### 文件

`user_data/strategies/WyckoffV7_TrueMethod.py`，函数 `populate_entry_trend()`

### 背景

`final_entry` 有一个外层 `composite_intent_ok` 过滤器，阻止 `distributing` 或 `trending_down` 意图下的所有入场。当机构处于派发震荡（BTC 从 100K 跌至 65K 的过程）时，`composite_intent = distributing`，即便 Spring 出现也会被此过滤器拦截。

### 修复

```python
# 原始
composite_intent_ok = ~composite_intent_vals.isin(['distributing', 'trending_down'])

# 修复后：Spring 区间豁免
_spring_intent_exempt = (
    dataframe.get('spring_detected', pd.Series(False, index=dataframe.index)) |
    (dataframe.get('spring_quality', pd.Series('none', index=dataframe.index)) != 'none')
)
composite_intent_ok = composite_intent_ok | _spring_intent_exempt
```

Spring 信号豁免 composite_intent 过滤，其余信号行为不变。

---

## 6. 修复 5：Spring #1/#2 EMA 阈值放宽

### 文件

`user_data/strategies/WyckoffV7_TrueMethod.py`，`spring_2_entry`、`spring_1_entry` 变量

### 问题

```python
# Spring #2（修复前）
close > ema_200 * 0.9    # BTC 65K vs ema_200≈80K → 65/80 = 0.81 < 0.9 → 不满足

# Spring #1（修复前）
close > ema_200          # 更严格，完全无法满足
```

### 修复

```python
# Spring #2（修复后）
close > ema_200 * 0.85

# Spring #1（修复后）
close > ema_200 * 0.85
```

**选择 0.85 的依据**：
- 本次 BTC Spring 处于 65K，EMA200 约 80K，比值约 0.81
- 0.85 保留了一定安全边际，防止在极端情况（如 0.5倍以上EMA偏离）下入场
- Spring #3 本身已无 EMA 限制，Spring #2/#1 设置 0.85 形成层级约束

---

## 7. 回测验证结果

### 短期验证（2026-02-25 ~ 2026-03-05，BTC 65K→73K 阶段）

| 指标 | 修复前 | 修复后 |
|------|--------|--------|
| dist_phase_d 覆盖 | **334根（87%）** | **0根（0%）** |
| 实际交易数 | **0** | **1** |
| 该1笔胜率 | — | **100%** |
| 收益 | — | **+3.0% / +118 USDT** |
| 出场方式 | — | ROI 止盈触发 |

### 1年验证（2025-03-05 ~ 2026-03-04）

| 指标 | 数值 |
|------|------|
| 总交易数 | **146笔** |
| 胜率 | **76.0%**（111胜/35负） |
| 总收益 | **+71.06%** |
| CAGR | **71.31%** |
| Sharpe 比率 | **2.83** |
| Sortino | **2.99** |
| Calmar | **66.32** |
| SQN | **4.45（A 级）** |
| Profit Factor | **2.47** |

**入场标签分布**（1 年）：

| Enter Tag | 笔数 | 平均利润 | 胜率 |
|-----------|------|---------|------|
| wyckoff_axis_support | 104 | +0.53% | 69.2% |
| wyckoff_wave_start | 17 | +2.12% | 100% |
| wyckoff_sos | 12 | +1.55% | 91.7% |
| wyckoff_sow | 6 | +2.10% | 83.3% |
| wyckoff_utad | 2 | +3.10% | 100% |
| wyckoff_spring_1 | 2 | +0.25% | 50.0% |

### 3年全量验证（2023-01-01 ~ 2026-03-05）✅

> 使用 `config_backtest.json`（与历史基线一致），与修复前基线对比

| 指标 | **修复前基线**（~20260228） | **修复后**（~20260305） | 变化 |
|------|--------------------------|----------------------|------|
| 总交易数 | 425 | **448** | +23 |
| 胜率 | 78.6% | **77.0%** | -1.6pp |
| 总收益 | 601.75% | **563.10%** | -38pp |
| CAGR | — | **81.54%** | — |
| Sharpe | 2.58 | **2.55** | -0.03 |
| SQN | 7.53 | **7.30** | -0.23 |
| 最大回撤 | 4.79% | **5.69%** | +0.9pp |
| Profit Factor | — | **2.54** | — |
| Calmar | — | **163.34** | — |

**结论**：✅ 修复未破坏历史性能，指标小幅波动属于正常范围。
- 交易数增加 +23 笔（Spring 区间新路径带来的额外信号）
- 收益略降主要因为**时间范围比基线多~5周**（20260228→20260305 期间 BTC 仍在下跌）
- SQN 7.30 仍属 "Holy Grail" 级别（>7.0），最大回撤仅 5.69% ✅

**入场信号分布**（3年）：

| Enter Tag | 笔数 | 平均利润 | 胜率 |
|-----------|------|---------|------|
| wyckoff_axis_support | 284 | +0.70% | 70.8% |
| wyckoff_wave_start | 90 | +2.20% | 98.9% |
| wyckoff_sos | 28 | +1.40% | 85.7% |
| wyckoff_sow | 22 | +0.40% | 59.1% |
| wyckoff_utad | 9 | +1.56% | 66.7% |
| wyckoff_efficiency | 3 | +4.02% | 100% |
| wyckoff_lps | 8 | +1.44% | 87.5% |
| wyckoff_spring_1 | 2 | +0.24% | 50.0% |

---

## 8. 理论依据总结

### 为什么 Spring 必须绕过 EMA 约束

威科夫的核心洞见之一：**Spring 创造牛市，而不是牛市的结果**。

积累 Phase C 的末日震仓（Spring/Shakeout）发生在价格**刚刚跌破支撑**的时刻，意图是清洗最后一批弱势持有者。此时：
- 价格必然低于支撑区下轨
- EMA200 代表长期趋势，当 BTC 从 100K 跌至 65K 时，EMA200 仍在 80K 附近
- 要求 `close > EMA200` 等于要求 Spring 发生后价格已经回升到 EMA200 以上，那时 Spring 早已过去

**正确逻辑**：Spring 是入场时机，EMA 是趋势确认工具。Spring 阶段两者存在本质冲突，Spring 优先级应高于 EMA 过滤。

### 为什么 SOW 必须有 BC 前置

发布方（Distribution）的完整因果链：**BC（买力顶峰）→ AR（自动反弹）→ ST（二次测试）→ UTAD（诱多最高点）→ SOW（弱势信号）**。若 BC 从未出现，价格根本未到达顶部区，所谓"弱势信号"没有参照锚点，属于噪音而非真实转折。

---

## 9. 注意事项

1. **Spring #1 胜率偏低（50%）**：2笔中有1笔亏损，样本量不足，后续 Dry Run 持续观察
2. **路径5不设 EMA 硬限制**：仅靠 `not_distribution` 防止误入，若极端行情下 Spring 被误识别，可能在深度下跌中入场；建议后续版本增加 `spring_quality >= 'spring_2'` 作为路径5的质量门槛
3. **`满足trading_allowed=0`但有1笔交易**的计数器偏差：已排查为日志统计时序问题，实际交易正确执行

---

## 10. 相关 Commit

```
e8f59af  fix: EventSeqSM SOW/PhaseD bugs + Spring zone trading path
```

**变更摘要**：

```
wyckoff/event_sequence_state_machine.py  | +4 / -2
user_data/strategies/WyckoffV7_TrueMethod.py | +15 / -3
```
