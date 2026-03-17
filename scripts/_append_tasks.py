#!/usr/bin/env python3
"""追加 Fix Pack A 到 TASKS.md"""

fix_pack_a = """

---

## Fix Pack A — 当前优先修复包（先修真钱问题）

### Goal
先处理当前已经被报告明确证实、且**直接影响收益**的问题。  
不扩新 detector，不开新大方向，先把最亏钱、最容易少赚的地方修掉。

> 当前优先级不是"让系统更完整"。  
> 当前优先级是：**先减少明显亏损源，再减少明显利润回吐。**

---

## A-1：空头体系限流 / 关闭

### 目标
先阻断当前已经确认的空头负贡献，避免继续稳定亏钱。

### 依据
- `wyckoff_sow` / `wyckoff_utad` / `sos_reverse_short` 整体负收益
- BTC 2023-2026 牛市阶段，空头体系稳定亏损

### 实现方案
**仅在 `bear_market_mode=True` 时允许空头入场**
- 在 `final_short_entry` 增加 `bear_market_mode` 门控
- 保留代码，不删逻辑，最小变更

### 验收标准
- 空头入口被明确限流：非熊市（`bear_market_mode=False`）时空头不成交
- 回测验证减少空头负贡献

---

## A-2：`wyckoff_down_wave` 误杀样本拆解

### 目标
确认 `wyckoff_down_wave` 为什么会成为最大利润回吐源。  
找共性，再决定怎么改。

### 依据
- `wyckoff_down_wave` 占退出 82.3%，均收益仅 1.21%
- `roi` 均收益 7.99%，说明大量单"本可继续跑"

### 输出
- `wyckoff_down_wave` 误杀样本分布
- 前 2~3 类最常见误杀模式
- 每类模式候选修正方向

### 验收标准
- 能明确指出主要误杀模式
- 能区分"该退"和"不该退"的 down_wave 样本
- 为 A-3 最小修正提供依据

---

## A-3：`wyckoff_down_wave` 最小修正规则

### 前置
仅在 A-2 完成后启动。

### 实现方案
在当前规则基础上加一条波幅约束：
- 原条件：`is_wave_start & wave_direction==down & wave_efficiency>0.001`
- 新增：`wave_displacement > close * 0.008`（最小 0.8% 价格位移）
- 效果：过滤掉"效率够高但位移极小"的微幅下降段，防止正常回踩被误判

### 验收标准
- `wyckoff_down_wave` 退出笔数合理减少
- 不引入新的大规模回撤
- 回测总收益不下降（允许微幅浮动）

---

## A-4：`wyckoff_climax_exit` 收紧

### 目标
减少 8 笔 climax 退出中的 5 笔误杀（当前 37.5% 胜率）。

### 依据
- `wyckoff_climax` 8 笔，胜率 37.5%，均收益 -0.90%
- `change_of_character` 路径中 `close < rolling(20).min()` 条件在牛市中过于宽松

### 实现方案
将 `change_of_character` 中的新低判断窗口从 20 根收紧至 5 根：
- 原：`close < close.rolling(20).min()`
- 新：`close < close.rolling(5).min()`（仍需打破近期低点，但不再以 20H 为参照）
- 物理含义：要求价格明确打破近期关键低点，而不是仅仅比 20H 低

### 验收标准
- `wyckoff_climax` 退出笔数减少，或胜率改善
- 不引入大量新的未捕获利润回吐

---

## A-5：暂停开发清单（Fix Pack A 执行期间）

### 当前暂停
- Spring / Spring 相关增强
- Phase D / confluence 扩展
- 新 detector 支线
- 动态 quality / gate 类深扫
- P2 regime-aware 决策接线
- 大规模退出参数扫参
- 治理层 / 文档层扩展

---

## Fix Pack A 完成标准

1. 空头体系是否已被有效限流 → 非熊市时 `enter_short=0`
2. `wyckoff_down_wave` 主要误杀模式已识别
3. `wyckoff_down_wave` 已完成一次最小有效修正
4. `wyckoff_climax_exit` 已被收紧，减少过敏退出
5. 当前最直接亏钱的问题已优先处理
"""

content = open('TASK_NEW/TASKS.md', encoding='utf-8').read()
content = content + fix_pack_a
open('TASK_NEW/TASKS.md', 'w', encoding='utf-8').write(content)
print('Done, new length:', len(content))
