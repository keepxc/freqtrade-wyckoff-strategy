# 协议修订 · 2026-09-28 — AI 源由「站点图表 agent」改为「OpenRouter 免费视觉模型」

> `FORWARD_PROTOCOL.md` 正文（判据、期限、门槛、去重规则）**一字未改**，本文件只记录
> **前提条件的一次变更**，按协议 §5「模型变更视同前提条件变化，需在报告中标注变更时点，
> 前后样本不得混池」执行。

## 1. 变更内容

| 项 | 变更前 | 变更后 |
|---|---|---|
| 实现 | `ask_ai.py` → `ai.yss-signal.com/api/chart-agent` | **`ask_vision.py` → `openrouter.ai/api/v1/chat/completions`** |
| 每日调用上限 | 2 次/账号（北京时间 08:00 重置） | 免费模型档位上限（1000 次/天；实测瓶颈是上游 provider 瞬时限流，非日配额） |
| 单次成本 | 免费（受额度约束） | **`cost: 0`（免费模型）** |
| 提示词 | `ask_ai.build_prompt()` | **同一函数，逐字一致**（`AI_PROMPT_STATE=0` 不变） |
| 图 | `out/chart_dual.png` | 同一张，不变 |
| 方向口径 | 末行 `DIRECTION_JSON: {"dir_7d","dir_30d"}` | 同一格式，解析器 `forward_log.parse_ai_dirs` 不变 |
| 切换方式 | — | `AI_SOURCE=site` 可切回站点源（`run_session.sh`） |

## 2. 混池问题：不存在

**切源之前，站点源的成功样本数 = 0。** 01:29–02:10 UTC 共 7 次站点调用全部
`UPSTREAM_STATUS`（上游模型不可用）/ `USER_BUSY`，失败不扣次，`forward_log.csv` 里那唯一
一行是 `ai_status=fail`，已被 2026-09-28 05:42 的 OpenRouter 成功样本**按 run_key 覆盖**。
⇒ 现有样本集 100% 来自新源，无需拆分、无需重新累计。

## 3. 每次调用必须记录（协议 §5 的可执行化）

`out/ai_<日期>_<槽位>.json` 已写入：`model`、`provider`、`usage.cost`。
**`provider` 是 OpenRouter 会变的那一项**（同一个 model id 可能落到不同 provider），
报告里两个字段都要给；任一变化都算前提条件变化，需追加一份本文件的修订。

## 4. 候选模型与失败判据（实现约束）

- 候选顺序：`nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free` → `dots-studio/dots-3-note-preview:free`
  （可用 `.env` 的 `VISION_MODELS` 覆盖；两个都实测能逐字读对随机图）
- **失败即换候选**（不原地重试）：HTTP 429 / 403 / 5xx / 结构异常 / **HTTP 200 但 reply 为空**
- `max_tokens ≥ 4000`（默认 4000）：多个免费候选是推理模型，给少了会把预算烧在 reasoning 上，
  HTTP 200 但回复为空——**这必须算调用失败，不能记成 `ai_parse_ok=0`**（否则失败率算错维度）
- 幂等：`out/ai_index.json` 按 `(日期, 槽位, 源)` 记录，站点源与本地源可各自留一条读数

## 5. 已知局限（追加声明）

- 免费模型的可用性是**概率性**的：已实测 `qwen/qwen3.8-27b:free`、`google/gemma-4-31b-it:free`
  在同一时刻返回 429「temporarily rate-limited」，`thinkingmachines/inkling:free` 返回 403。
  ⇒ 候选列表必须保留备胎；报告里要给出「各候选实际胜出次数」。
- `deepseek-flash` 亦经实测可直接读图（同一张随机图逐字读对），但**本管线不使用它**：
  与主 agent 同族，且用户指定走 OpenRouter。
