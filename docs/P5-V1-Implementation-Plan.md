# P5-V1-Implementation-Plan.md

## 0. 目标

P5 V1 的目标不是一次性做完完整研究平台，而是先把最小闭环搭起来：

- Freqtrade 输出结构化事件
- 事件可 ingest 到 SQLite
- 能生成周报和基础诊断
- 能为未来 Phase-aware exit 等预埋点留出观察与治理接口

本版额外要求：
- **把 Phase-aware 退出预埋进 V1 设计**
- 但**不在 V1 直接接线到生产策略**
- 先做“可观察、可统计、可审计”

---

## 1. V1 范围

### 1.1 V1 必做
- [ ] JSONL 事件输出基础设施
- [ ] SQLite 数据库
- [ ] `experiment_runs`
- [ ] `trades`
- [ ] `signals`
- [ ] `detector_events`
- [ ] `regime_snapshots`
- [ ] `parameter_versions`
- [ ] ingest 脚本
- [ ] 每周 summary 脚本
- [ ] AI 周报入口
- [ ] trades / signals / detector_events 对 Phase-aware exit 预埋字段兼容

### 1.2 V1.1 建议做
- [ ] `exit_decision_event`
- [ ] `exit_decisions` 表
- [ ] Exit Layer Diagnostics 周报小节
- [ ] shadow mode 下的 `ExitLogic` 观察输出

### 1.3 V1 不做
- [ ] PostgreSQL 生产迁移
- [ ] 自动晋升 baseline
- [ ] 自动改参数
- [ ] dashboard 前端
- [ ] 多用户并发
- [ ] 全自动实验队列
- [ ] 正式接线 Phase-aware exit 到生产策略

---

## 2. 实施顺序

### Step 1：落地 DB schema
目标：
- 先建立 SQLite schema
- 字段上先兼容未来扩展

任务：
- [ ] 创建 `schema.sql`
- [ ] 创建 `views.sql`
- [ ] 补入 Phase-aware exit 预埋字段：
  - `trades.phase_exit_*`
  - `signals.phase_exit_*`
  - `parameter_versions.parameter_class='exit'`
- [ ] 预留 `exit_decisions` 表（V1 可先创建，脚本可暂不写）

验收：
- [ ] schema 可在 SQLite 中成功初始化
- [ ] views 可成功创建

---

### Step 2：打通 JSONL ingest
目标：
- 任意 run 生成的 JSONL 事件都能导入数据库

任务：
- [ ] `ingest_jsonl.py`
- [ ] 识别 5 类基础事件：
  - `run_event`
  - `signal_event`
  - `trade_event`
  - `detector_event`
  - `regime_snapshot`
- [ ] V1.1 兼容第 6 类：
  - `exit_decision_event`
- [ ] 为未知字段保留 warning，不直接崩溃

验收：
- [ ] 样例事件文件可成功 ingest
- [ ] 多次 ingest 不产生重复主键污染
- [ ] 缺失可选字段时不报错

---

### Step 3：先接 Freqtrade 最小输出点
目标：
- 不改执行结构，先把最关键事实打出来

建议第一批输出点：
- [ ] run start / end
- [ ] candidate signal
- [ ] trade open
- [ ] trade close
- [ ] 关键 detector 变化
- [ ] regime snapshot

**Phase-aware 退出当前动作**
- [ ] 暂不改交易行为
- [ ] 只在设计层预留事件类型和表
- [ ] 若 `ExitLogic` 容易调用，可选做 shadow mode 输出：
  - `exit_decision_event(triggered=false)`
  - 不改变实际 exit

验收：
- [ ] 事件输出不影响策略正常执行
- [ ] 即使 JSONL 写失败，也不阻断交易流程

---

### Step 4：建立第一版周报
目标：
- 先做一个稳定、简单、可持续生成的 weekly report

V1 周报至少包括：
- [ ] run 摘要
- [ ] 交易数 / 胜率 / 平均收益
- [ ] enter_tag 分布
- [ ] detector 激活率
- [ ] regime 分布
- [ ] baseline vs 当前 run 的简单偏移

V1.1 新增：
- [ ] Exit Layer Diagnostics
  - phase exit candidate 次数
  - phase exit triggered 次数
  - blocked by ROI 次数
  - exit reason 分布变化

验收：
- [ ] 可从 DB 自动生成 markdown 周报
- [ ] 周报可写入 `weekly_reports` 表

---

### Step 5：AI 周报与诊断入口
目标：
- 让 AI 可以基于数据库摘要先做“读”和“解释”

V1 先做：
- [ ] 周报总结
- [ ] detector 异常提示
- [ ] regime 漂移提示
- [ ] enter_tag drift 提示
- [ ] 小样本提醒

V1.1 增加：
- [ ] exit layer 诊断
- [ ] 是否存在 ROI 抢先覆盖结构化退出的迹象
- [ ] 是否值得未来启动 Phase-aware exit wiring 预研

验收：
- [ ] AI 输出固定格式 markdown
- [ ] AI 不直接建议上线，只做诊断和候选提案

---

## 3. 当前与 Phase-aware 退出的衔接方案

### 当前状态
- `wyckoff/exit_logic.py` 的 ExitLogic 已实现
- 策略未接线
- 属于预埋点，不进入当前执行主线

### V1 的处理方式
V1 不直接接线，但要完成 3 件事：

#### 3.1 数据层预留
- [ ] `trades` 加 phase exit 字段
- [ ] `signals` 加 phase exit 观察字段
- [ ] `parameter_versions` 支持 `exit` 类型

#### 3.2 事件层预留
- [ ] `exit_decision_event` 加入规范
- [ ] `detector_event` 预留 `phase_exit_candidate` / `phase_exit_blocked`

#### 3.3 报告层预留
- [ ] 周报支持 Exit Layer Diagnostics 章节
- [ ] proposal 支持 `exit_enhancement` 分类

### V1.1 可选动作：shadow mode
如果实现成本低，可以加：
- [ ] 让 ExitLogic 在不影响交易行为的情况下输出 `exit_decision_event`
- [ ] 用于统计：
  - 候选次数
  - ROI 抢先次数
  - 持仓时长差异
  - 该拿没拿住的潜在证据

---

## 4. 未来正式接线 Phase-aware exit 的前置条件

V1 完成后，只有同时满足以下条件，才建议把它从预埋点变成实验项：

- [ ] P5 V1 已落地
- [ ] Dry Run / Ledger 已稳定
- [ ] Exit Layer Diagnostics 显示明确问题：
  - ROI 抢先覆盖率高
  - “该拿没拿住”证据明确
  - 持仓寿命在特定 phase 下显著不匹配
- [ ] 样本量足够
- [ ] 问题不更适合由 P3 / 常规 ROI / stoploss 调整解决

正式接线时要求：
- [ ] 默认 off
- [ ] 独立 commit
- [ ] 最小验证矩阵
- [ ] 三段回测
- [ ] Dry Run 灰度

---

## 5. 里程碑

### Milestone A：P5 V1 数据闭环
完成条件：
- schema 可用
- ingest 可用
- 周报可用
- AI 可读

### Milestone B：Phase-aware 退出已被“系统看见”
完成条件：
- 数据字段已预留
- 事件规范已预留
- 周报可容纳 Exit Layer Diagnostics
- proposal 可归类到 `exit_enhancement`

### Milestone C：ExitLogic shadow mode 可观察（可选）
完成条件：
- `exit_decision_event` 能输出
- 不改变交易行为
- 能统计 ROI 抢先覆盖率

---

## 6. 验收标准

### P5 V1 验收
- [ ] SQLite schema 初始化通过
- [ ] JSONL ingest 成功
- [ ] trades / signals / detector / regime 数据可查询
- [ ] weekly report 可生成
- [ ] AI 可基于周报做诊断摘要

### Phase-aware 退出预埋验收
- [ ] 数据层字段已补齐
- [ ] 事件规范已补齐
- [ ] 周报支持退出层诊断接口
- [ ] 不影响当前策略执行
- [ ] 不提前引入新的生产行为

### V1.1 验收（可选）
- [ ] shadow mode 的 `exit_decision_event` 可落库
- [ ] 可观察 candidate / blocked / triggered 分布
- [ ] 可统计 ROI 抢先覆盖率

---

## 7. 当前结论

P5 V1 不是为了今天就启用 Phase-aware exit，  
而是为了让未来的这类“已预埋但未接线”的模块不再孤立存在。

因此，本实施计划要求：

> 先把它们变成“系统看得见、AI读得懂、周报能解释、治理层能审计”的对象，  
> 再决定是否值得进入正式接线实验。