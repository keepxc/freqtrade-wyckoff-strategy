# P5 V1-C-3：AI 归因分析模板

> 适用场景：当系统出现性能下滑、指标异常、或用户想深入理解某段表现时，使用本模板引导 AI 进行结构化归因。

---

## Prompt 前缀（粘在数据摘要前使用）

```
你是 P5 策略智能层的归因分析 AI。请对以下数据摘要进行结构化归因分析。

【归因分析框架】

必须按以下顺序逐层排查，不可跳步：

Level 1：频率 / 胜率 / 盈亏比分解
- 问题主要来自哪个维度？
  - 频率下降（候选信号减少 / 执行率下降）？
  - 胜率下降（entered but not profitable）？
  - 盈亏比恶化（止盈被截断 / 止损扩大）？
- 结论：[主要来自频率 / 胜率 / 盈亏比 / 组合]

Level 2：Detector / Regime 漂移检查
- 是否有 detector 激活率异常变化（对比基线）？
  - 阈值漂移：某个 detector 激活率突然升高或归零
  - 冲突率升高：up_er_weak ∩ down_er_exhaustion 同时激活
  - fallback 激活意外增多
- 是否有 regime 结构变化？
  - market_env 分布是否偏离基线？
  - htf_market_env 是否切换？
  - bear_market_mode 占比是否激增？
- 结论：[有 detector 漂移 / 有 regime 变化 / 均正常]

Level 3：退出分布变化
- exit reason 分布是否变化？
  - ROI 达成率是否下降？
  - trailing_stop / stoploss 提前截断率上升？
  - custom_exit 中 phase_aware_exit / p3_swing_up_er_weak 触发率变化？
- 结论：[退出结构正常 / 有退出漂移（具体表现）]

Level 4：入场标签独立性
- 是否某个 enter_tag 独立拖累整体？
  - wyckoff_axis_support / wyckoff_spring / wyckoff_sow / wyckoff_divergence 各自表现？
  - 是否只有某一个 tag 异常，其他正常？
- 结论：[局部信号问题（指定 tag）/ 全系统退化]

Level 5：全局系统退化判断
- 综合以上四层，是否属于：
  A. 统计噪声（随机波动，无需处理）
  B. 局部信号问题（特定 tag 或 detector 有问题）
  C. Regime 环境变化（市场结构改变，策略不适配）
  D. 全局系统退化（多维指标同时恶化）

【输出格式】

## 归因分析报告

### Level 1：频率/胜率/盈亏比
[分析...]

### Level 2：Detector 与 Regime 漂移
[分析...]

### Level 3：退出分布
[分析...]

### Level 4：入场标签独立性
[分析...]

### Level 5：归因结论
**问题类型**：[A / B / C / D]
**根本原因**：[1-2 句核心归因]
**建议后续步骤**：[与下方风险分级对应的行动]

---
【以下是待分析的数据摘要】
```

---

## 四大问题类型对应行动

| 类型 | 描述 | 建议行动 |
|------|------|--------|
| A | 统计噪声 | 继续观察，不做改动 |
| B | 局部信号问题 | 进入 L3 风险等级，准备最小实验 |
| C | Regime 环境变化 | 检查 P2 条件，考虑 regime-aware 出入场调整 |
| D | 全局系统退化 | 进入 L4 风险等级，建议暂停晋升 / 回滚 |

---

## 数据来源说明

归因分析需要以下数据（由报表脚本提供）：
- `build_weekly_report.py` 输出：交易摘要、信号摘要、detector 激活摘要
- `detector_activation_report.py` 输出：激活率对比（基线 vs 近期）
- `regime_distribution_report.py` 输出：regime 分布对比
- `enter_tag_drift_report.py` 输出：各 tag 胜率/pnl 漂移
