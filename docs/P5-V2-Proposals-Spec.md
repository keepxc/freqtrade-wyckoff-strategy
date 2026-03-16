# P5 V2 Proposals 使用规范

> 文档版本：v1.0（2026-03-15）

## 1. 概述

`proposals` 表是 P5 V2 的核心治理对象，记录"从诊断观察到参数变更提案"的完整审计链。
任何参数修改意图必须先成为 proposal，经过审查，才能对应生成 `experiment_run`。

### 硬性约束
- **AI 不能直接修改生产参数**
- **AI 不能绕过 proposal → experiment → review 流程**
- **样本 < 30 笔的 proposal 不得进入 approved 状态**
- **L1 / L2 风险等级时，禁止提交 proposal**

---

## 2. Proposal 状态机

```
[pending]
    │
    ▼  (人工审查或 AI 初审)
[in_review]
    │
    ├──→ [rejected]   (不满足条件 / 风险过高 / 归因不充分)
    │
    └──→ [approved]   (满足门槛，可以生成 experiment_run)
              │
              ▼
         [in_experiment]  ← 对应一个或多个 experiment_run 创建
              │
              ├──→ [validated]   (实验表现优于 baseline，可晋升)
              │
              └──→ [rejected]    (实验表现不达预期，退回)
```

---

## 3. Proposal 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `proposal_id` | TEXT PK | 唯一标识，格式：`prop_YYYYMMDD_NNN` |
| `created_at` | TEXT | ISO 8601 时间戳 |
| `proposal_type` | TEXT | `parameter_change` / `regime_fix` / `detector_fix` / `exit_logic` |
| `problem_statement` | TEXT | 观测到的问题（1-3 句描述） |
| `evidence_json` | TEXT | JSON：样本数、统计数据、漂移量等支撑数据 |
| `risk_level` | TEXT | `L1` / `L2` / `L3` / `L4` |
| `sample_sufficiency` | TEXT | `insufficient(<30)` / `low_threshold(30-99)` / `significant(>=100)` |
| `recommended_action` | TEXT | 具体行动建议（调整哪个参数、范围） |
| `experiment_matrix_json` | TEXT | JSON：最小实验矩阵（候选参数值列表） |
| `status` | TEXT | 状态机：见第 2 节 |

---

## 4. 什么时候创建 Proposal

### 允许创建的条件（全部满足）
1. 已完成显著性检查（`significance_check_prompt.md`）
2. 已完成归因分析（`attribution_analysis_prompt.md`）
3. 归因类型为 **B（局部）** 或 **C（Regime环境）**
4. 风险等级为 **L3** 或 **L4**
5. 样本量 ≥ 30 笔（相关 enter_tag 或全局）
6. 跨阶段一致性有证据

### 禁止创建的条件（任一满足则禁止）
- 样本量 < 30 笔（任何情况）
- 风险等级 L1 或 L2
- 归因类型为 A（统计噪声）
- 证据不充分（缺少 evidence_json 中的关键字段）

---

## 5. Proposal 与 Experiment 的联动

```
Proposal [approved]
    ↓ 通过 scripts/proposal_workflow.py create-experiment
ExperimentRun [running]
    - run_type: "candidate"
    - parent_baseline_run_id: 当前 baseline run_id
    - notes: 关联 proposal_id
    ↓ 实验完成
ExperimentRun [completed]
    ↓ 通过 scripts/manage_experiment_status.py review
Proposal [validated] 或 [rejected]
```

---

## 6. 常用操作命令

```powershell
# 列出所有 pending proposals
python scripts/proposal_workflow.py list --status pending

# 审查一个 proposal（设为 in_review）
python scripts/proposal_workflow.py review --proposal-id prop_20260315_001

# 批准一个 proposal（进入 approved）
python scripts/proposal_workflow.py approve --proposal-id prop_20260315_001

# 拒绝一个 proposal
python scripts/proposal_workflow.py reject --proposal-id prop_20260315_001 --reason "样本不足"

# 从 approved proposal 创建 experiment run
python scripts/proposal_workflow.py create-experiment --proposal-id prop_20260315_001 --timerange 20260101-20260315

# 查看 proposal 详情
python scripts/proposal_workflow.py show --proposal-id prop_20260315_001
```

---

## 7. Proposal 质量门禁

所有 proposal 在 approved 之前必须通过以下检查（由 `proposal_workflow.py approve` 自动执行）：

- [ ] `evidence_json` 包含 `trade_count`（≥ 30）
- [ ] `evidence_json` 包含 `baseline_win_rate` 和 `recent_win_rate`
- [ ] `risk_level` 不为 L1 或 L2
- [ ] `experiment_matrix_json` 包含至少 1 组候选参数值
- [ ] `sample_sufficiency` 不为 `insufficient`
- [ ] `status` 当前为 `in_review`（不可跳步）
