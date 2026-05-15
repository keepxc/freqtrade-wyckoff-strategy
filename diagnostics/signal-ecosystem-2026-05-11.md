# Signal Ecosystem Diagnostic Report
**窗口期**: 2026-05-04 ~ 2026-05-11
**生成时间**: 2026-05-11
**分析师**: 自动诊断脚本

---

## 执行摘要

**健康度评级**: 🔴 **数据不足 / 系统级缺陷**

本报告覆盖最近 7 天数据，但发现**三个阻断性问题**，导致大部分诊断维度无法输出有效结论：

1. **Trade 表 phase/macro/env 全 NULL**：策略层未在 trade entry 时回写 `phase_context_tag_enter`、`macro_phase_enter`、`market_env_enter` 字段，导致所有 5 个 phase/trade 交叉分析表均为空。无法进行相位效率、Regime 适配、Enter Tag×Regime 矩阵诊断。
2. **质量分数极度离散**：全部 20,046 条信号仅呈现 3 个离散值（1.0、1.1、3.0），非连续分布，无法做区间分析和阈值卡点诊断。
3. **信号→交易转化率 0.06%**（20,046 信号 / 12 笔成交），但 rejection 记录为零。说明信号出口与仓位执行之间存在未被记录的过滤层。

---

## A. 信号质量诊断

### 数据事实

| Quality Bucket | 信号数 | 占比 | 执行数 | 平均 Q |
|---|---|---|---|---|
| 1.0-1.2 | 19,882 | 99.2% | 19,882 | 1.015 |
| >1.2 | 164 | 0.8% | 164 | 3.0 |

**全量历史**（非仅最近 7 天）仅存在 3 个离散 quality 值：
- `q=1.0`: 16,944 条（84.5%）
- `q=1.1`: 2,938 条（14.7%）
- `q=3.0`: 164 条（0.8%）

**Enter Tag 分布（全量）**：
| Tag | 信号数 | 平均 Q | 范围 |
|---|---|---|---|
| wyckoff_axis_support | 18,853 | 1.014 | 1.0–3.0 |
| wyckoff_wave_start | 494 | 1.042 | 1.0–1.1 |
| wyckoff_sos | 364 | 1.874 | 1.0–3.0 |
| wyckoff_spring | 300 | 1.076 | 1.0–1.1 |
| v8_wave_start | 22 | 1.0 | 1.0 |
| wyckoff_efficiency | 13 | 1.069 | 1.0–1.1 |

### 诊断结论

- **质量计算非连续**：当前信号质量分数被强制量化为 3 档，这不符合"质量评分"的语义。可能是策略内部 `min()` 取了固定下界，或者多因子评分被简化为条件分支。正常分布应接近正态或右偏。
- **wyckoff_sos 质量显著更高**（avg 1.874），说明 SOS 路径有独立的强化逻辑；但 `wyckoff_axis_support` 占信号量 94% 且平均质量仅 1.014，几乎贴着门槛。
- 质量 >1.2 的信号（164 条）全部带有 `wyckoff_sos` 或 `wyckoff_axis_support` 标签，且 avg_q=3.0 说明存在某个质量上限强制设置为 3.0 的逻辑。

### 风险等级：🔴 高（数据质量）

### 建议行动
1. **排查信号质量计算逻辑**：检查策略中 `signal_quality_after` 的计算路径，确认是否存在硬截断或离散化逻辑。
2. **保留 5 档质量分布**：当前直方图只有 2 档无法分析，建议质量公式输出至少 5 个有效区间。

---

## B. 拒绝原因诊断

### 数据事实

**最近 7 天拒绝原因**：空（`rejection_reasons: []`）

**全量历史拒绝**：0 条
- 20,046 条信号，`was_executed = 1` for ALL（100% 通过信号层）
- 但 trades 表仅 12 条记录

### 诊断结论

- **假阳性陷阱**：`was_executed = 1` 的语义混淆。当前数据中该字段表示"信号通过质量/授权门"，而非"实际成交"。真实拒绝发生在仓位管理 / 资金费率 / 滑点层，但未被记录到 signals 表。
- **无法区分"正确防守"和"过度保守"**：因为拒绝原因记录为空，无法判断是否有信号被不该拒绝的层拦截。

### 风险等级：🔴 高（数据完整性缺失）

### 建议行动
1. 在策略的仓位管理拒绝逻辑处（如 `can_enter()` 返回 False 时）补充写回 `rejection_reason` 到 signals 表，而不是仅在信号层记录。

---

## C. 相位效率诊断

### 数据事实

**Trades 表字段填充状态**：
```
phase_context_tag_enter: 12/12 NULL
macro_phase_enter: 12/12 NULL
market_env_enter: 12/12 NULL
```

信号表中有 `phase_context_tag` 字段（如 `none`, `dist_c_utad`, `accum_e`），但该字段**未被回写到 trades 表**。

**Phase Context 分布（信号层，仅最近 7 天）**：
| Phase | 信号数 | 执行数 | 平均 Q | Trade 数 |
|---|---|---|---|---|
| none | 729 | 729 | 1.0 | 0 |
| dist_c_utad | 1,519 | 1,519 | 1.077 | 0 |
| accum_e | 97 | 97 | 1.1 | 0 |
| E | 8 | 8 | 1.0 | 0 |
| unknown | 0 | 0 | — | 3 |

**全量交易（12 笔）**：
- 所有 trade 的 `phase_context_tag_enter = NULL`
- Enter tags: `wyckoff_axis_support` (8), `wyckoff_sos` (1, open), `wyckoff_lps` (1), None (2)
- Exit reasons: `pnf_target_1` (9), `sow_fast_exit` (1), `axis_support_failed_exit` (1)

### 诊断结论

- **相位诊断不可执行**：trades 表缺失 phase_context 字段，无法计算 Phase C/D/E 效率对比。
- 9/12 交易以 `pnf_target_1` 退出，说明退出逻辑健康（PnF 模块有效），但无法判断是哪类相位信号触发的。

### 风险等级：🔴 阻断性（数据缺失）

### 建议行动
1. **立即修复**：`_on_trade_entry()` 中添加 `self.custom_info['trade_db_id']` 的 phase/macro/env 回写逻辑。
2. 修复后，下期报告方可执行相位效率诊断。

---

## D. Regime 适配诊断

### 数据事实

**Regime Snapshots（97,227 条，覆盖 ~全量历史）**：
| market_env | macro_phase | Bar 数 | 占比 |
|---|---|---|---|
| markup | markdown | 36,098 | 37.1% |
| trading_range | markdown | 39,552 | 40.7% |
| markdown | markdown | 20,378 | 21.0% |
| null | null | 1,199 | 1.2% |

**信号层 market_env 分布**（最近 7 天 2,353 条信号）：
- `trading_range`: majority
- `markup`: present
- `markdown`: minority
- `null`: present

### 诊断结论

- **数据写入不一致**：trades 表 `market_env_enter` / `macro_phase_enter` 全为 NULL，无法构建 Enter Tag × Regime 热力图。
- **信号层 regime 分布**与 regime_snapshots 口径不完全匹配：snapshot 显示 37% markup 但 macro_phase=markdown（语义冲突？），而信号层以 `trading_range` 为主。
- 12 笔交易中 8 笔 enter_tag=`wyckoff_axis_support`，但 regime 信息丢失，无法判断这些是在哪种环境下触发的。

### 风险等级：🔴 阻断性（数据缺失）

### 建议行动
1. 同 C.1，确认 trade entry 时写入 regime 上下文。
2. 审查 `macro_phase=markdown` 但 `market_env=markup` 的 regime 判定逻辑——这在语义上是矛盾的。

---

## E. 漂移初筛诊断

### 数据事实

**最近 7 天 vs 全量基线对比**：
| 指标 | 最近 7 天 | 全量历史 | 变化 |
|---|---|---|---|
| 信号数 | 2,353 | 20,046 | — |
| 质量均值（全量）| 1.03 | 1.03 | 无漂移 |
| 质量分布 | 仅 1.0/1.1/3.0 | 仅 1.0/1.1/3.0 | 无漂移 |
| 已平仓交易 | 0 (7d) | 10 (全量) | — |
| 胜率（10 笔全量）| — | 75% | 样本不足 |
| 平均收益（10 笔）| — | +1.43% | 样本不足 |

**Baseline window**: `null` —上期报告未指定基线窗口

**Detector 激活（7 天）**：
- phase/phase_context_tag: 513 次

### 诊断结论

- **无法做漂移分析**：7 天内仅 6 条新信号有交易关联（trade_count=0 in extraction output），无已平仓交易，无法计算胜率/收益漂移。
- 质量分布无漂移（恒定为 3 值离散）。
- 全量 10 笔交易（trade_id 非 null 且有 exit）胜率 75%、平均 PnL +1.43% — 需注意样本仅 10 笔，置信区间极宽。

### 风险等级：🟡 中（样本不足）

### 建议行动
1. 拉长回测窗口至 30+ 天以获得 ≥30 笔交易样本后再做漂移分析。
2. 当前 12 笔交易数据点不足以支撑"胜率下降 >10%"的漂移判据。

---

## 优先级排序（Top 3 优化方向）

| 优先级 | 问题 | 预期收益 | 实施难度 | 行动 |
|---|---|---|---|---|
| **P0** | Trade entry 缺失 phase/macro/env 字段回写 | 诊断能力恢复 | 低（代码修复） | 在 `_on_trade_entry()` 补充写入 |
| **P0** | Rejection reason 记录缺失 | 拒绝效率诊断 | 中（需在多处补充写入） | 在所有 `can_enter()` False 分支写回 reason |
| **P1** | 信号质量极度离散（3 值） | 质量分层能力 | 高（需改质量公式） | 审查 signal_quality 计算，移除硬截断 |

---

## 样本充分性声明

| 维度 | 样本量 | 状态 |
|---|---|---|
| 总信号（7 天）| 2,353 | ✅ 充足 |
| 总信号（全量）| 20,046 | ✅ 充足 |
| 已平仓交易（全量）| 10 | ⚠️ 不足（<30） |
| 已平仓交易（7 天）| 0 | ❌ 不足 |
| Phase × Trade 交叉 | 0（字段全 NULL）| ❌ 阻断 |
| Regime × Trade 交叉 | 0（字段全 NULL）| ❌ 阻断 |
| Enter Tag × Regime 矩阵 | 0（字段全 NULL）| ❌ 阻断 |

---

## 上期基线

无基线报告文件（`signal-ecosystem-baseline.md` 未找到）。KN-001 / KN-002 状态：**持续监控**（见下）。

---

## 已知监控项（KN）

| ID | 描述 | 状态 |
|---|---|---|
| KN-001 | 信号→交易转化率极低（0.06%）| 持续监控 — 下层过滤逻辑未记录，待补充 rejection 记录 |
| KN-002 | Wyckoff phase context 未写入 trade 表 | 持续监控 — P0 修复项，阻塞所有相位诊断 |

---

## KN-001 / KN-002 详细说明

**KN-001 — 信号→交易转化黑洞**
- 20,046 信号 / 12 笔成交 = 0.0598% 转化率
- 但 `rejection_reasons` 全为空，说明过滤发生在信号层之后（可能是 freqtrade 自身的 `max_open_trades`、`stake_amount` 检查、`can_enter` 检查等）
- 这些拒绝未被写回 signals 表
- **影响**：无法判断是否过度保守

**KN-002 — Trade metadata 缺失（最优先）**
- 策略产生 signals 时记录了 `phase_context_tag`、`macro_phase`、`market_env`
- 但 trade entry 时这些字段未被回写到 trades 表
- 导致所有相位效率、Regime 适配诊断**完全不可执行**
- **影响**：阻塞 B/C/D/E 四个诊断维度

---

*报告生成：`/home/gyan/freqtrade/user_data/ai/extract_signal_diagnostic_data.py` + 手动 DB 补充查询*