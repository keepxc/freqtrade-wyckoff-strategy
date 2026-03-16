# P5 V2-2：AI 候选提案生成模板

> 适用场景：AI 完成归因分析并确认风险等级 L3/L4 后，使用本模板生成标准格式的候选提案（Proposal）。

---

## Prompt 前缀（紧接归因报告之后使用）

```
你已完成归因分析并确认风险等级。现在请根据以下规则，生成一份标准格式的 P5 候选提案（Proposal）。

【Proposal 生成规则】

1. 仅当风险等级为 L3 或 L4 时才能生成 Proposal
2. 样本量必须 ≥ 30 笔（相关 enter_tag 或全局总体）
3. 问题描述必须来自实际观测数据，不可凭空猜测
4. 候选参数范围必须是守恒的（保守变更，不超过 ±20% 幅度）
5. 最小实验矩阵：通常 1 个基线 + 1-2 个候选值
6. 明确声明"不确定性"和"潜在风险"

【输出格式】

按以下 JSON 结构输出 Proposal（可供直接录入数据库）：

```json
{
  "proposal_id": "prop_YYYYMMDD_NNN",
  "proposal_type": "<parameter_change|regime_fix|detector_fix|exit_logic>",
  "problem_statement": "<用 1-3 句话描述观测到的问题，包含时间窗口和数量依据>",
  "evidence_json": {
    "trade_count": <整数，相关样本数>,
    "time_window": "<开始日期> ~ <结束日期>",
    "baseline_win_rate": <小数，基线胜率>,
    "recent_win_rate": <小数，近期胜率>,
    "win_rate_drift": <小数，漂移量>,
    "baseline_avg_pnl": <小数>,
    "recent_avg_pnl": <小数>,
    "affected_tags": ["<enter_tag1>", "..."],
    "detector_anomaly": "<描述，或 null>",
    "regime_anomaly": "<描述，或 null>",
    "cross_period_consistent": <true|false>
  },
  "risk_level": "<L3|L4>",
  "sample_sufficiency": "<low_threshold(30-99)|significant(>=100)>",
  "recommended_action": "<具体描述：调整哪个参数，从什么值到什么值，预期效果>",
  "experiment_matrix_json": {
    "parameter_name": "<参数名>",
    "current_value": "<当前值>",
    "candidates": [
      {"value": "<候选值1>", "rationale": "<改动理由>"},
      {"value": "<候选值2>", "rationale": "<改动理由>"}
    ],
    "evaluation_metric": "<主要评估指标：win_rate|avg_pnl|profit_factor>",
    "min_trades_required": <最少需要多少笔交易才能评估>
  },
  "uncertainties": "<列出 1-2 个主要不确定性>",
  "potential_risks": "<如果实验失败，可能的负面影响>",
  "status": "pending"
}
```

注意：
- 如果无法确定某个字段，填写 null 并说明原因
- 不要凭空填写统计数据，只使用实际数据
- `experiment_matrix_json.min_trades_required` 建议设为 30（最低）
```

---

## 示例 Proposal（仅供格式参考）

```json
{
  "proposal_id": "prop_20260315_001",
  "proposal_type": "parameter_change",
  "problem_statement": "wyckoff_spring enter_tag 在 2026-01-01 ~ 2026-03-15 期间胜率从基线 62% 下滑至 44%，共 35 笔，连续 3 周低于 45%，归因为 spring zone 判断阈值过于宽松导致假信号增多。",
  "evidence_json": {
    "trade_count": 35,
    "time_window": "2026-01-01 ~ 2026-03-15",
    "baseline_win_rate": 0.62,
    "recent_win_rate": 0.44,
    "win_rate_drift": -0.18,
    "baseline_avg_pnl": 0.0215,
    "recent_avg_pnl": 0.0082,
    "affected_tags": ["wyckoff_spring"],
    "detector_anomaly": null,
    "regime_anomaly": null,
    "cross_period_consistent": true
  },
  "risk_level": "L3",
  "sample_sufficiency": "low_threshold(30-99)",
  "recommended_action": "将 spring_zone_tolerance 从 0.015 调整为 0.010，减少宽松判断，预期提高精度但可能降低频率约 10%",
  "experiment_matrix_json": {
    "parameter_name": "spring_zone_tolerance",
    "current_value": "0.015",
    "candidates": [
      {"value": "0.010", "rationale": "收紧 33%，与原始设计意图对齐"},
      {"value": "0.012", "rationale": "温和收紧，降低误判但保留部分信号"}
    ],
    "evaluation_metric": "win_rate",
    "min_trades_required": 30
  },
  "uncertainties": "低频率环境下 35 笔样本可能仍有噪声；regime 切换可能是部分原因（未完全排除）",
  "potential_risks": "若收紧过度，wyckoff_spring 频率可能下降超过 30%，影响整体持仓数量",
  "status": "pending"
}
```

---

## Proposal 类型说明

| `proposal_type` | 适用场景 |
|-----------------|--------|
| `parameter_change` | 调整策略参数（阈值、窗口、比率等） |
| `regime_fix` | P2 regime 条件调整（如 bear_market_mode 触发条件） |
| `detector_fix` | Detector 逻辑修复或阈值调整 |
| `exit_logic` | CustomExit / PhaseAwareExit / P3 退出条件调整 |

---

## 提案录入数据库

生成提案 JSON 后，通过以下命令提交：

```powershell
# 从 JSON 文件提交 proposal
python scripts/proposal_workflow.py submit --file proposal_draft.json

# 或直接 CLI 提交（基础字段）
python scripts/proposal_workflow.py create \
  --type parameter_change \
  --problem "wyckoff_spring 胜率下滑" \
  --risk L3 \
  --action "收紧 spring_zone_tolerance 到 0.010"
```
