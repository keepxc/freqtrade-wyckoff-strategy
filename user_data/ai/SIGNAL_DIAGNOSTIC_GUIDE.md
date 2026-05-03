# P5 信号生态 AI 诊断系统使用指南

## 概述
这套系统用于对 P5 数据库中的信号生态进行全景诊断，识别信号质量、拒绝原因、相位效率、Regime 适配等问题，并生成可操作的优化建议。

## 系统组成
1. **数据提取脚本**：`extract_signal_diagnostic_data.py`
   - 从 SQLite 数据库提取结构化数据
   - 输出 JSON 格式的诊断数据包

2. **AI 分析 Prompt**：`prompts/signal_ecosystem_diagnostic_prompt.md`
   - 定义诊断框架和输出要求
   - 提供给 AI 作为分析指令

3. **输出报告**：AI 生成的 Markdown 诊断报告
   - 包含执行摘要、分项诊断、优先级建议

---

## 使用流程

### Step 1: 提取诊断数据
```bash
python user_data/ai/extract_signal_diagnostic_data.py \
    --db user_data/market.db \
    --recent-days 30 \
    --baseline-start 2023-01-01 \
    --baseline-end 2024-01-01 \
    --output signal_diagnostic_data.json
```

**参数说明：**
- `--db`：P5 SQLite 数据库路径
- `--recent-days`：最近窗口天数（用于漂移检测）
- `--baseline-start` / `--baseline-end`：基线窗口（可选，用于对比）
- `--output`：输出 JSON 文件路径

**输出示例：**
```
[OK] 诊断数据已写入：signal_diagnostic_data.json
[INFO] 最近窗口：2026-04-02T00:00:00+00:00 ~ 2026-05-02T00:00:00+00:00
[INFO] 基线窗口：2023-01-01T00:00:00+00:00 ~ 2024-01-01T00:00:00+00:00
[INFO] 总信号数：1247
[INFO] 已执行信号数：312
```

---

### Step 2: 准备 AI 输入
将以下内容提交给 AI（Claude / GPT-4 / 其他）：

1. **Prompt 文件内容**：
   ```
   [粘贴 prompts/signal_ecosystem_diagnostic_prompt.md 的完整内容]
   ```

2. **数据文件内容**：
   ```json
   [粘贴 signal_diagnostic_data.json 的完整内容]
   ```

3. **指令**：
   ```
   请基于上述 Prompt 和数据，生成信号生态诊断报告。
   ```

---

### Step 3: 审核 AI 输出
AI 将生成类似以下结构的报告：

```markdown
# P5 信号生态诊断报告

## 执行摘要
当前信号生态整体健康度：**中等偏弱**。主要问题：
1. 拒绝过度保守：rr_blocked 占 42%，但被拒绝信号质量中位数 1.05
2. Phase E 低效：胜率 38%，平均收益 -0.8%
3. Spring 在 Markdown 错配：胜率 32% vs Markup 58%

## A. 信号质量诊断
...

## B. 拒绝原因诊断
...

## C. 相位效率诊断
...

## D. Regime 适配诊断
...

## E. 漂移初筛诊断
...

## 优先级建议
1. [高优先级] 调整 RR 阈值：从 1.5 降至 1.3
2. [中优先级] 收紧 Phase E 授权条件
3. [低优先级] 禁用 Spring 在 Markdown 环境
```

---

### Step 4: 决策与行动
根据 AI 报告：
1. **识别高优先级问题**：样本充分 + 影响面大 + 可操作性强
2. **设计实验矩阵**：使用 P5 experiment 框架
3. **回测验证**：candidate run vs baseline
4. **Dry Run 观察**：至少 7 天
5. **决策晋升或回滚**

---

## 诊断维度说明

### A. 信号质量诊断
**目标：** 识别信号质量分布是否健康，是否存在"高质量未执行"或"低质量执行了"的异常

**关键指标：**
- 质量分布（0-0.5, 0.5-0.8, 0.8-1.0, 1.0-1.2, >1.2）
- 各区间的执行率、胜率、平均收益
- 质量阈值卡点

**典型问题：**
- 质量 >1.2 的信号执行率低于 50%（过度保守）
- 质量 <0.8 的信号执行率高于 20%（门槛过低）

---

### B. 拒绝原因诊断
**目标：** 识别哪个拒绝规则卡得最死，是否合理

**关键指标：**
- 拒绝原因频率排名
- 各拒绝原因对应的信号质量中位数
- 被拒绝信号的事后表现（模拟）

**典型问题：**
- `rr_blocked` 占比 >40% 且被拒绝信号质量 >1.0（RR 阈值过高）
- `regime_blocked` 占比 >30%（P2 过于严格）
- `max_open_trades_blocked` 频繁（仓位管理过保守）

---

### C. 相位效率诊断
**目标：** 识别不同 Wyckoff 相位的信号表现差异

**关键指标：**
- 各 phase 的信号数、执行率、胜率、平均收益
- Phase C / D / E 的表现对比

**典型问题：**
- Phase E 信号占比 >15% 但胜率 <40%（过晚入场）
- Phase C 信号执行率 <20%（过早被拒）
- Phase D 信号数过少（错过最佳窗口）

---

### D. Regime 适配诊断
**目标：** 识别"标签-环境错配"

**关键指标：**
- Enter Tag × Macro Phase 交叉表
- 各组合的胜率、平均收益
- 高风险组合（样本 ≥5 且胜率 <40%）

**典型问题：**
- `wyckoff_spring` 在 `markdown` 下胜率 <35%
- `wyckoff_sow` 在 `accumulation` 下样本过少
- `bear_market_mode` 开启时整体胜率下降 >15%

---

### E. 漂移初筛诊断
**目标：** 识别最近窗口是否存在系统性漂移

**关键指标：**
- 最近窗口 vs 基线窗口的质量均值、胜率、平均收益
- 漂移幅度（绝对值 + 百分比）

**典型问题：**
- 最近 30 天胜率下降 >10%（触发 drift alert）
- 最近 30 天平均收益下降 >0.5%（需回滚或调参）
- 信号质量均值下降 >0.1（detector 失真）

---

## 样本充分性判断标准

| 维度 | 最小样本数 | 可靠样本数 | 说明 |
|------|-----------|-----------|------|
| 整体信号 | 50 | 200+ | 少于 50 条仅供观察 |
| 单个 enter_tag | 5 | 20+ | 少于 5 笔不可靠 |
| 单个 phase | 10 | 30+ | 少于 10 笔谨慎解读 |
| Tag × Regime 组合 | 3 | 10+ | 少于 3 笔不纳入分析 |
| 漂移检测 | 30 | 100+ | 少于 30 笔不做漂移判断 |

---

## 风险等级定义

| 等级 | 定义 | 建议行动 |
|------|------|---------|
| **高** | 影响面 >30% 信号 + 样本充分 + 表现显著劣化 | 立即设计实验，7 天内验证 |
| **中** | 影响面 10-30% + 样本充分 + 表现中等劣化 | 2 周内验证，可与其他优化合并 |
| **低** | 影响面 <10% 或样本不足 | 持续观察，暂不行动 |

---

## 常见问题

### Q1: 数据库中没有足够的历史数据怎么办？
**A:** 
- 如果总信号数 <50，报告会标注"样本不足，仅供观察"
- 建议先积累至少 1 个月的 Dry Run 数据再做诊断
- 可以用回测数据（backtest）作为基线窗口

### Q2: AI 给出的建议是否可以直接执行？
**A:** 
- **不可以**。AI 建议必须经过：
  1. 人工审核（是否符合策略逻辑）
  2. 回测验证（candidate vs baseline）
  3. Dry Run 观察（至少 7 天）
  4. 决策闸门（P5 experiment 流程）

### Q3: 如何判断 AI 建议的优先级？
**A:** 
- 优先级 = 预期收益 × 样本充分性 × 可操作性
- 高优先级：影响面大 + 样本 >50 + 有明确参数调整方向
- 低优先级：影响面小 或 样本 <10 或 需要大规模重构

### Q4: 多久运行一次诊断？
**A:** 
- **Dry Run / Live**：每周一次
- **Backtest**：每次参数调整后
- **Drift Alert**：当周报显示胜率下降 >10% 时立即运行

### Q5: 诊断报告如何归档？
**A:** 
- 建议存入 `user_data/ai/reports/` 目录
- 文件名格式：`signal_diagnostic_YYYYMMDD.md`
- 关键报告可存入 `proposals` 表

---

## 示例命令

### 场景 1：最近 30 天 vs 2023 全年基线
```bash
python user_data/ai/extract_signal_diagnostic_data.py \
    --db user_data/market.db \
    --recent-days 30 \
    --baseline-start 2023-01-01 \
    --baseline-end 2024-01-01 \
    --output signal_diagnostic_recent30d.json
```

### 场景 2：仅分析最近 7 天（无基线对比）
```bash
python user_data/ai/extract_signal_diagnostic_data.py \
    --db user_data/market.db \
    --recent-days 7 \
    --output signal_diagnostic_recent7d.json
```

### 场景 3：对比两个实验窗口
```bash
# 提取 candidate 窗口
python user_data/ai/extract_signal_diagnostic_data.py \
    --db user_data/market.db \
    --recent-days 14 \
    --baseline-start 2025-01-01 \
    --baseline-end 2025-12-31 \
    --output signal_diagnostic_candidate.json
```

---

## 后续扩展方向

### V1.1 计划
- [ ] 自动化 AI 调用（通过 API）
- [ ] 诊断报告自动存入 `proposals` 表
- [ ] 集成到周报流程（每周自动生成）

### V2 计划
- [ ] 支持 Phase-aware exit 诊断
- [ ] 支持 P&F target 达成率分析
- [ ] 支持 Regime 授权规则诊断
- [ ] 支持多 run 对比（baseline vs candidate）

---

## 联系与反馈
如有问题或建议，请在项目 issue 中提出，或联系策略维护团队。
