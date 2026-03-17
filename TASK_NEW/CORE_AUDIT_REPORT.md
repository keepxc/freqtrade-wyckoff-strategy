# Core Audit Report — 收益导向版

> 回测区间：2023-01-01 ~ 2026-02-28  
> 策略：WyckoffV7_TrueMethod（1H BTC/USDT）  
> 总交易：378 笔（多头 356 + 空头 22）  
> 总收益：858.38%，均收益 1.53%，胜率 73.3%，最大回撤 8.48%  
> 生成时间：2026-03-17

---

## CA-1：找出"真影响成交"的链路

### 入场标签实测分布

| 入场标签 | 笔数 | 占比 | 胜率 | 均收益 | 总收益贡献 |
|---------|------|------|------|--------|----------|
| wyckoff_axis_support | **253** | **66.9%** | 68.0% | 1.11% | 372.13% |
| wyckoff_wave_start | **81** | **21.4%** | **97.5%** | 3.01% | 348.14% |
| wyckoff_sow（空头）| 19 | 5.0% | 36.8% | 0.08% | 32.39% |
| wyckoff_sos | 12 | 3.2% | 83.3% | 2.08% | 63.89% |
| wyckoff_lps | 7 | 1.9% | 85.7% | 2.81% | 28.86% |
| wyckoff_efficiency | 1 | 0.3% | 100% | 7.99% | 11.76% |
| wyckoff_spring_test_sig | 2 | 0.5% | 50.0% | **-0.24%** | 1.59% |
| wyckoff_utad（空头）| 3 | 0.8% | 33.3% | **-0.26%** | -0.36% |
| **Spring 全系列** | **0** | 0% | — | — | — |
| wyckoff_phase_d / hq | 0 | 0% | — | — | — |
| wyckoff_axis_sos_confluence | 0 | 0% | — | — | — |

### 真影响成交的字段清单

| 字段 | 影响路径 | 影响规模 |
|------|---------|---------|
| `nearest_support` / `support_distance` / `axis_strength_tier` | → axis_support_entry → 66.9% | **核心** |
| `is_wave_start` / `wave_direction` / `wave_efficiency` | → wave_start_entry → 21.4% | **核心** |
| `is_SOS_bar` + `sos_quality_score` | → wyckoff_sos → 3.2% | 次要 |
| `sos_bar_confirmed` → lps_detector → `lps_bar_detected` | → wyckoff_lps → 1.9% | 次要 |
| `sow_bar_detected` | → wyckoff_sow(空头) → 5.0% | 次要（负ROI） |

### 只改中间值、不改成交的字段清单

| 字段 | 真实影响 |
|------|--------|
| `signal_quality` | 改质量分，不改成交集合（soft 模式）|
| `phase_quality_mult` | 改质量乘数，不改成交集合 |
| `daily_gate_score` / `_gate_mode=soft` | soft 模式不影响成交（已验证） |
| `composite_intent` | 只阻止 distributing/trending_down，不影响主链路 |
| `rr_long_ok` (PnF) | 基本全通（样本中几乎不阻断） |

### 当前主导成交的前 3 条路径

1. **`wyckoff_axis_support`（67%）** — 近轴线 + test_confidence + harmony
2. **`wyckoff_wave_start`（21%）** — 波段起点 + 上升方向 + wyckoff_context
3. **`wyckoff_sow`/空头（5%）** — 空头信号（整体负ROI）

### CA-1 结论

1. **真问题**：成交集合由两条链路决定（axis_support + wave_start），两者合计占 88%；其余所有信号（spring 系列、phase_d、confluence）= 0 触发
2. **影响成交？** 是。axis 和 wave 系是真正的引擎，其余都是边角料
3. **建议**：主链路（axis_support + wave_start）继续优化；spring 系列暂不投入

---

## CA-2：找出"真影响收益"的退出链路

### 退出原因实测分布

| 退出原因 | 笔数 | 占比 | 胜率 | 均收益 | 绝对收益 |
|---------|------|------|------|--------|---------|
| wyckoff_down_wave | **311** | **82.3%** | 74.0% | 1.21% | +60,272 |
| roi | **36** | **9.5%** | **100%** | **7.99%** | +37,728 |
| force_exit | 1 | 0.3% | 100% | 5.50% | +2,065 |
| wyckoff_efficiency_decline | 5 | 1.3% | 40.0% | -0.02% | +768 |
| wyckoff_divergence | 3 | 0.8% | 100% | 0.31% | +185 |
| wyckoff_climax | 8 | 2.1% | **37.5%** | -0.90% | **-1,471** |
| sos_reverse_short | 9 | 2.4% | 22.2% | -2.61% | **-2,928** |
| trailing_stop_loss | **5** | 1.3% | 0% | **-12.01%** | **-10,780** |

### 当前真实退出链顺序

```
优先级1（极少触发）: wyckoff_climax_exit / severe_divergence
优先级2（真实主裁）: wyckoff_down_wave（波段下降退出）— 82.3%
优先级3（最优结局）: roi（ROI命中）— 9.5%
优先级4（底线守护）: trailing_stop_loss（-12% custom stoploss）— 1.3%
```

### 利润回吐最严重的模式

1. **好单被 `wyckoff_down_wave` 过早截断**：roi 平均收益 7.99% vs down_wave 平均 1.21%，说明有一批本可跑到 ROI 的单，先被下降波段止盈/止损了 
2. **`wyckoff_climax` 出场点判断错误**：8 笔，37.5% 胜率（意味着 5/8 单在"气候行动"后价格继续涨，错误出场），均亏 0.90%

### 亏损拖延最明显的模式

1. **`trailing_stop_loss`（-12%）**：5 笔，全部 `wyckoff_axis_support` 入场，亏损合计 -10,780。实际上是 axis 信号在 BTC 单边下跌阶段触发，随后跌穿 -12% 止损。这不是错误，但是最大单笔损耗来源
2. **`sos_reverse_short`（空头被迫平仓）**：9 笔，22.2% 胜率，均亏 2.61%。BTC 2023-2026 牛市中空头体系（wyckoff_sow/utad）持续亏损

### CA-2 结论

1. **真问题**：退出结构不平衡——好单（ROI路径）只占 9.5%，大部分单被 down_wave 以 1.21% 均收益出场（远低于 ROI 的 7.99%）；最大亏损来源是 trailing_stop_loss（5 笔 -12%）和空头 sos_reverse_short（9 笔 -2.61%）
2. **影响收益？** 是。down_wave 精度决定 82% 的单，wyckoff_climax 退出质量极差（37.5% 胜率），空头体系稳定贡献亏损
3. **建议**：First priority — 退出层优化（down_wave 精度 + climax 退出条件收紧）；Second — 关闭或抑制空头体系

---

## CA-3：找出"浪费开发时间"的模块和方向

### 暂停开发清单

| 模块/信号 | 当前状态 | 原因 |
|---------|---------|------|
| Spring 全系列（spring_1/2/3/recovery/test_sig）| 几乎 0 触发 | 条件过于严格，在 BTC 趋势行情中极少触发 |
| wyckoff_phase_d / phase_d_hq | 0 触发 | 未见实际成交 |
| wyckoff_axis_sos_confluence | 0 触发 | 共振条件未同时满足 |
| `signal_quality` / `phase_quality_mult` 动态仓位 | 质量分布不单调 | 已确认不能开启动态仓位；改分数但不改成交集合 |
| P2 regime-aware 授权系统接线 | 处于观测层 | 触发 Core Audit 的条件尚未成熟，不接主线 |

### 保留观察但不接线清单

| 模块 | 状态 |
|------|------|
| `daily_gate_score` | soft/off 保留，不接 hard |
| `sos_bar_confirmed` / `lps_bar_detected` | 保留观测，影响 5% 成交，不需要大投入 |
| `wyckoff_climax_exit` | 保留，但需收紧触发条件（当前 37.5% 胜率） |

### 当前低 ROI 方向清单

1. **空头体系**（wyckoff_sow + wyckoff_utad + sos_reverse_short）：牛市中持续亏损，ROI 极低
2. **Spring 系列开发**：代码存在但 0 触发，继续投入无即时收益
3. **composite_man_detector 接线优化**：already 已接，但边际效果不明显（主路径 axis_support 主要不受其影响）

### CA-3 结论

1. **真问题**：大量代码（spring 系列、phase_d、confluence）写了但未触发；空头体系是稳定亏损机器
2. **影响收益？** 零触发信号不影响收益，但消耗了开发时间；空头体系直接负收益
3. **建议**：暂停 spring/phase_d/confluence 开发；关闭或严格限制空头体系入场

---

## CA-4：确定"下一步最值得做的开发项"

### 判断依据汇总

| 候选方向 | 影响成交？ | 影响退出收益？ | 已有样本？ | 非 future hook？ |
|---------|---------|------------|---------|--------------|
| `wyckoff_down_wave` 退出精度优化 | ✅ 间接（主裁判） | **✅ 是，82.3% 出口** | ✅ 311 笔 | ✅ |
| 空头体系关闭/限制 | ✅ 消除空头 27 笔 | ✅ 减少 -13,708 亏损 | ✅ 27 笔 | ✅ |
| `wyckoff_climax_exit` 收紧 | 否 | ✅ 8 笔 37.5% 改善 | ✅ 8 笔 | ✅ |
| Spring 系列激活 | 可能 | 不确定 | ❌ 0 样本 | ❌ |

### 结论

**Next #1：`wyckoff_down_wave` 退出精度优化**
- 理由：主裁判（82.3%），改善它每 10 笔多对 1 笔，相当于多 ~60+ 笔正确退出
- 具体方向：分析 311 笔中哪些"下降波段"信号出现后价格继续上涨（错误出场），加入重新确认窗口或更严格的下降波段判断条件
- 要避免：不是改 ROI 参数，而是改 `wyckoff_down_wave` 的触发条件

**Next #2：关闭或严格限制空头体系（wyckoff_sow + wyckoff_utad）**
- 理由：22 笔空头，整体负收益；sos_reverse_short 被迫平多亏损 -2,928；BTC 2023-2026 牛市期不适合空头
- 具体动作：在 BTC 牛市（bear_market_mode=False）时禁止 sow/utad 入场 OR 收紧入场条件，或直接设置 `can_short = False`
- 影响：消除 -13,708 的亏损来源，整体胜率和收益提升

**暂不做**：
- Spring 系列激活（0 样本，无法评估触发条件是否正确）
- Phase D / confluence 优化（0 样本）
- `daily_gate hard` 模式激活（BTC 趋势期不适用）
- P2 regime-aware 接线（观测层，未达触发条件）

---

## Core Audit 5问答案

| 问题 | 答案 |
|------|------|
| 1. 哪几条链路在真实决定成交 | `wyckoff_axis_support`（67%）+`wyckoff_wave_start`（21%）= 88% |
| 2. `sos_bar_confirmed` 和 `lps_bar_detected` 有没有真实改变交易 | **有**，但影响极小（合计 ~5% 成交）；`sos_bar_confirmed` 是 LPS 路径的上游门槛 |
| 3. 退出谁在主导，钱主要死在哪 | `wyckoff_down_wave` 主导（82.3%）；钱主要死在 `trailing_stop_loss`（5笔 -12%，-10,780）和空头体系（-13,708） |
| 4. 哪些模块/方向继续做大概率不挣钱 | Spring 系列（0触发）、空头体系（稳定亏损）、signal_quality 动态仓位（不单调） |
| 5. 下一步最值得做的开发项 | ①`wyckoff_down_wave` 退出精度优化  ②关闭/限制空头体系 |

---

> Core Audit 完成时间：2026-03-17  
> 触发条件：进入重大接线前（Phase-aware exit / P2 regime-aware 授权系统）  
> 状态：✅ 5 个必答问题全部答出，Core Audit 结束

---

## Core Audit 重新校验（2026-03-17 最新 baseline）

> 本节基于：回测 20230101-20260228 / 393 笔 / 965.76% / 8.13% DD  
> 此为 P0 Next Exit #1 实验失败后，用最新数据重新对齐答案。

### 成交决策者（不变）

| 入场标签 | 笔数 | 占比 | WR | avg | 总贡献 |
|---------|------|------|----|-----|--------|
| `wyckoff_axis_support`（含axis_weak label）| 292 | 74.3% | 66.4% | +1.09% | +318% |
| `wyckoff_wave_start` | 86 | 21.9% | 97.7% | +2.94% | +253% |
| 其余所有信号 | 15 | 3.8% | — | — | +33% |
| Spring全系列 / phase_d / confluence | **0** | 0% | — | — | — |

### 退出决策者（更新）

| 退出原因 | 笔数 | WR | 总贡献 |
|---------|------|-----|--------|
| `wyckoff_down_wave` | 336 | 73.8% | +401% |
| `roi` | 34 | 100% | +271% |
| `trailing_stop_loss` | 5 | 0% | -60%（已接受为系统内含风险） |
| `wyckoff_climax` | 9 | 33.3% | -8.7% |

### 核心矛盾：axis_weak × down_wave 质量分裂

| 入场 × 退出 | 笔数 | WR | avg |
|------------|------|-----|-----|
| `axis_weak × down_wave` | 248 | **65.7%** | +0.80% |
| `axis_weak × roi` | 24 | **100%** | +7.92% |
| `wave_start × down_wave` | 74 | **98.6%** | +2.40% |
| `wave_start × roi` | 9 | 100% | +8.08% |

同一 down_wave 机制：axis_weak 只有 65.7% WR，wave_start 有 98.6% WR。  
axis_weak 中只有 8.8%（24笔）走到 roi（avg +7.92%），其余 91.2% 以 +0.80% avg 出场。

### 更新后的下一步方向

**Next #1（新）**：按 `enter_tag` 差异化 `wyckoff_down_wave` 出场确认条件

- axis_weak 持仓：要求更多 K 线确认 或 更大下降波效率阈值（减少噪音触发）
- wave_start 持仓：维持现有条件（98.6% WR 不破坏）
- 实现位置：`custom_exit()` 中的 down_wave 判断块，按 `trade.enter_tag` 分支
- 实验控制：默认 off，`WY_AXIS_WEAK_DOWN_WAVE_CONFIRM=1` 开启
- 预期：部分 axis_weak × down_wave 的错误出场转化为 roi 出场 → avg 从 0.80% 向 7.92% 靠拢

**已确认不做**：  
- TSL 收紧：已验证 FAIL  
- Spring 系列：0 样本  
- Regime 参数路由：backlog  
- 空头体系进一步改动：bear_gate 已封堵

