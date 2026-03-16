## P5 — 策略智能层（Strategy Intelligence Layer）

> 目标：将当前系统从“能交易的策略代码”，升级为“可统计、可诊断、可提案、可受控进化”的系统。
>
> 当前定位：
> - P5 不是新信号模块
> - P5 是未来系统的统计、分析、实验、治理底座
> - 执行仍留在 Freqtrade 内，研究与进化能力独立在 Freqtrade 外部实现

---

### P5 当前状态（V1 + V1-B 基础设施）
- [x] 数据库 schema 已创建（9 张表，含 `exit_decision_events`）
- [x] `EventEmitter` 事件发射器已实现（schema version 1.1.0）
- [x] `StrategyEventBridge` 桥接层已实现
- [x] 策略侧最小侵入集成已完成（含 V1B-1/V1B-2 调用点）
- [x] JSONL → SQLite 数据摄取链路已打通
- [x] 周报生成功能已跑通（含 detector / regime / enter_tag 章节）
- [x] 最小闭环验证完成：
  - 策略运行 → JSONL 事件 → SQLite 数据库 → 周报分析
- [x] V1 回测验证通过（2026-01）：
  - 152 条事件（run + signals + trades）
  - 3 笔交易，100% 胜率，4.07% 收益
- [x] V1-B 回测验证通过（2026-01-01 ~ 2026-01-14）：
  - 783 条事件（含 516 条 regime 快照、18 条 detector 事件）
  - 5 笔交易，80% 胜率，+4.83%
- [x] 全流程自动化脚本已完成：`scripts/run_backtest_pipeline.py`

### P5 当前结论
- [x] P5 V1 数据底座已打通
- [x] P5 已从“设计轮廓”进入“可运行基础设施”阶段
- [x] 当前完成的是“能记录 / 能入库 / 能出基础周报”
- [x] 下一阶段进入"能观察 / 能诊断 / 能提案"（V1-B 已完成，2026-03-14）

---

## P5 V1-B：观测增强（✅ 已完成 2026-03-14）

### 目标
在当前 V1 基础设施之上，补齐关键观测维度，使系统从“有数据”升级为“有观察能力”。
### 完成记录（2026-03-14）
- 全部 9 项任务已实现并通过 Docker 回测验证
- 关键修复：`emit_regime_snapshot` / `emit_detector_events_from_dataframe` 改为遍历全量 dataframe rows，覆盖 516 条 regime 快照 / 18 条 detector 状态变化事件
- `ingest_jsonl.py` 修复外键约束（`run.jsonl` 自动排序优先摄入）
- 新增 `scripts/run_backtest_pipeline.py`：一键完成 回测 → 摄入 → 报表 全流程自动化
- 回测验证结果（20260101-20260114）：5 笔交易，80% 胜率，+4.83%，783 条事件入库

### 常用命令

```powershell
# ── 回测模式（mode=backtest，默认）──────────────────────────────
# 1. 全流程一键：Docker 回测 + 摄入 + 出报表
python scripts/run_backtest_pipeline.py --timerange 20260101-20260114

# 2. 跳过回测，摄入最新目录 + 出报表
python scripts/run_backtest_pipeline.py --skip-backtest --week-start 2026-01-01 --week-end 2026-01-14

# 3. 仅重新生成报表（不摄入）
python scripts/run_backtest_pipeline.py --skip-backtest --skip-ingest --week-start 2026-01-01 --week-end 2026-01-14

# ── 定时维护模式（dry_run / live）────────────────────────────────
# 4. 摄入所有 dry_run 目录 + 生成上周周报
python scripts/run_backtest_pipeline.py --mode dry_run --all-runs --weekly

# 5. 摄入所有 live 目录（不出报表）
python scripts/run_backtest_pipeline.py --mode live --all-runs

# 6. 处理回测 trade_close 补丁 + 批量摄入 backtest 目录
python scripts/run_backtest_pipeline.py --mode backtest --all-runs --fix-trades

# ── 单独运行各报表（指定时间窗口）──────────────────────────────
python user_data/reports/detector_activation_report.py --db user_data/market.db --baseline-start 2026-01-01 --baseline-end 2026-01-14
python user_data/reports/regime_distribution_report.py --db user_data/market.db --baseline-start 2026-01-01 --baseline-end 2026-01-14
python user_data/reports/enter_tag_drift_report.py   --db user_data/market.db --baseline-start 2026-01-01 --baseline-end 2026-01-14 --recent-start 2026-01-01 --recent-end 2026-01-14
python user_data/reports/build_weekly_report.py      --db user_data/market.db --week-start 2026-01-01 --week-end 2026-01-14 --store

# ── 快速查数据库行数 ──────────────────────────────────────────
python -c "import sqlite3; conn=sqlite3.connect('user_data/market.db'); [print(f'{t}: {conn.execute(f\"SELECT COUNT(*) FROM {t}\").fetchone()[0]}') for t in ['experiment_runs','signals','trades','detector_events','regime_snapshots','exit_decision_events','weekly_reports']]"
```

### 任务

#### 事件覆盖增强
- [x] P5-V1B-1：补 `detector_event`
  - 覆盖：
    - `daily_choch_confirmed`
    - `dist_cause_sufficient`
    - `bear_market_mode`
    - `phase_context_tag`
    - `swing_up_er_weak`
    - `swing_down_er_exhaustion`
  - 要求：
    - 优先记录状态变化，不全量刷每根 bar
    - 事件格式遵循 `P5-Event-Spec.md`

- [x] P5-V1B-2：补 `regime_snapshot`
  - 覆盖：
    - `market_env`
    - `htf_market_env`
    - `macro_phase`
    - `bear_market_mode`
    - `daily_tr_exists`
    - `daily_gate_score`
  - 要求：
    - 先按主执行周期 bar 记录
    - 作为 P2 触发条件的数据来源

- [x] P5-V1B-3：补 `exit_decision_event`
  - 覆盖：
    - `phase-aware exit`
    - `p3_swing_up_er_weak`
    - future `ExitLogic` 相关候选退出
  - 字段要求：
    - `trade_id`
    - `phase_context_tag`
    - `exit_logic_module`
    - `candidate_reason`
    - `triggered`
    - `blocked_reason`

#### 统计与报表增强
- [x] P5-V1B-4：建立 `enter_tag drift` 报表
  - 输出：
    - 最近窗口 vs 基线窗口
    - 胜率差
    - avg pnl 差
    - PF 差
    - 样本数
  - 优先覆盖：
    - `wyckoff_axis_support`
    - `wyckoff_spring`
    - `wyckoff_sow`
    - `wyckoff_divergence`

- [x] P5-V1B-5：建立 detector 激活率报表
  - 输出：
    - 激活率
    - 冲突率
    - fallback 率
  - 要求：
    - 支持最近 7 天 / 30 天
    - 支持回测基线对照

- [x] P5-V1B-6：建立 regime 分布变化报表
  - 输出：
    - `market_env`
    - `htf_market_env`
    - `macro_phase`
    - `bear_market_mode`
  - 目标：
    - 为 P2 regime mismatch 提供自动观察来源

- [x] P5-V1B-7：扩展周报内容
  - 在现有周报基础上新增：
    - detector 激活率摘要
    - regime 分布摘要
    - enter_tag drift 摘要
    - 风险提示
    - 样本不足提醒

#### 数据治理增强
- [x] P5-V1B-8：为事件流增加版本字段
  - 必须补：
    - `event_schema_version`
    - `strategy_version`
    - `git_commit`
  - 目标：
    - 为历史兼容和 schema 演化做准备

- [x] P5-V1B-9：严格区分 `run_type`
  - 枚举：
    - `baseline`
    - `candidate`
    - `dry_run`
    - `live`
    - `analysis`
  - 要求：
    - 后续 proposal / rollback / baseline 晋升都依赖该字段

---

## P5 V1-C：AI 诊断层（✅ 已完成 2026-03-15）

### 目标
让 AI 先学会"读系统、解释系统、识别异常"，而不是直接调参。

### 完成记录（2026-03-15）
- 全部 4 项 AI Prompt 模板已实现，存放在 `user_data/ai/prompts/`
- `weekly_report_prompt.md`：周报解读 Prompt，含评估标准和标准输出格式
- `significance_check_prompt.md`：4 步显著性门禁（样本量 / 跨阶段 / 市场环境 / 声明）
- `attribution_analysis_prompt.md`：5 层归因框架（频率/胜率 → Detector → 退出 → Tag → 全局）
- `risk_classification_prompt.md`：L1~L4 风险分级规则与判断流程图

### 任务
- [x] P5-V1C-1：建立 AI 周报模板
  - 输出：
    - 系统健康度
    - 本周信号表现
    - detector 激活摘要
    - regime 分布变化
    - 风险提示

- [x] P5-V1C-2：建立 AI 显著性判断模板
  - 必须判断：
    - 样本是否足够
    - 是否低于 30 / 100 笔门槛
    - 是否属于小样本噪声
    - 是否跨阶段一致

- [x] P5-V1C-3：建立 AI 归因分析模板
  - 优先回答：
    - 问题来自频率、胜率还是盈亏比
    - 来自 detector 漂移、regime 变化，还是退出分布变化
    - 是局部信号问题，还是全局系统退化

- [x] P5-V1C-4：建立 AI 风险分级
  - L1：统计波动，无需动作
  - L2：继续观察
  - L3：建议进入最小实验矩阵
  - L4：建议暂停升级 / 回滚 / 人工审查

---

## P5 V2：Proposal / Experiment Registry（✅ 已完成 2026-03-15）

### 目标
让系统从"会观察"升级为"会提案"。

### 完成记录（2026-03-15）
- `docs/P5-V2-Proposals-Spec.md`：proposals 完整使用规范（状态机 / 字量门禁 / 命令手册）
- `user_data/ai/prompts/proposal_generation_prompt.md`：AI 生成提案模板（JSON 格式 + 示例）
- `user_data/reports/experiment_status_report.py`：experiment_runs + parameter_versions 联动报告
- `scripts/manage_experiment_status.py`：baseline / candidate / rollback 状态管理 CLI
- `scripts/proposal_workflow.py`：proposal → experiment → review 全状态流转 CLI（含质量门禁）

### 任务
- [x] P5-V2-1：建立 `proposals` 使用规范
- [x] P5-V2-2：建立 AI 候选提案格式
  - 问题定义
  - 证据
  - 风险等级
  - 样本充分性
  - 候选参数
  - 最小实验矩阵
  - 是否建议进入实验队列
- [x] P5-V2-3：建立 `experiment_runs` 与 `parameter_versions` 联动
- [x] P5-V2-4：建立 baseline / candidate / rollback 状态管理
- [x] P5-V2-5：建立 proposal → experiment → review 的状态流转

---

## P6 — 探测器质量修复（Detector Quality Fixes）

> **来源：** 2026-03-16 BTC/USDT 1H 行情分析中，通过威科夫理论对照发现的代码级别结构性缺陷。
> **优先级：** 高（直接影响信号质量，可导致 UTAD 被误判为 SOS，Phase 标签失真）
> **当前状态：** ✅ 代码实现完成（2026-03-16），待回测验证

### 问题背景

在对 BTC/USDT 1H 图（2026-03-13 急拉急跌结构、2026-03-16 二次测试）进行威科夫理论验证时，
发现现有探测器存在以下 3 个结构性缺陷，使策略倾向于输出过于乐观的 Phase 标签，
实盘中可能导致在 UTAD（分配陷阱）处建立多单。

### 任务

- [x] **P6-0：显式输出 TR 结构边界字段（`tr_upper` / `tr_lower`）**
  - **问题：** `ar_high`（TR 上边界）和 `sc_low`（TR 下边界）仅作为局部变量存在于 `_mark_tr_midpoint()` 中，只输出了 `tr_midpoint`，上下边界本身被丢弃
  - **为什么不能用 `nearest_resistance` 代替：**
    - `nearest_resistance` 来自轴线系统（`high.rolling(50).max().shift(1)` + 轴线聚类），随价格滚动漂移，Phase 无感知
    - `tr_upper` 是 SC/AR 序列结构定义的 TR 上沿，Phase A 完成后语义锁定，是 TR boundary semantics，不是 event label semantics
  - **修改文件：** `wyckoff/event_sequence_state_machine.py`（`_mark_tr_midpoint()` 方法）
  - **语义决策（2026-03-16 理论沟通确认）：**
    - **`tr_upper` = Phase A 完成后锁定为 AR 高点的名义 TR 上边界，不随 Phase B 新高更新**
    - **锁定时机：** `accum_phase_a_complete` 第一次变为 True 的那根 bar（即 ST 确认后），之前为 NaN，之后锁定
    - **Phase B 上探（UT/UTAD）不改写 `tr_upper`**，上探极值另存为 `phase_b_high`（诊断用）
    - 理由：P6-1 要识别"突破后快速失守回原区间"，必须有一个先于突破行为存在、且不被突破本身改写的稳定边界；若 UTAD 上探可抬升 `tr_upper`，则 P6-1 的"快速失守"判断会被钝化
    - `tr_lower` = SC 低点，Phase A 内 SC 更新时同步更新，Phase A 完成后锁定
    - **BC 重置规则（2026-03-16 确认）：值保留，权限失效**
      - BC 出现时，**不清零** 已锁定的 `tr_upper` / `tr_lower`，历史数值保留供回溯和分配侧诊断使用
      - 停止其作为当前 accumulation 结构边界向前生效（由 `accum_phase_a_complete` / `accum_structure_valid` 控制下游权限）
      - **规范：`tr_upper`/`tr_lower` 是历史结构字段，不是当前状态授权字段**
      - 下游使用规则：P6-1 / P6-3 必须同时检查 `accum_phase_a_complete == True`；distribution / UT / UTAD 诊断允许引用历史 `tr_upper`
      - 理由：Wyckoff 图式分析高度依赖"相对既有 TR 边界的历史语境"；清零会使 BC/UT/UTAD 诊断失去"它是相对哪条原结构边界发生的"这层证据
  - **实现方案：**
    - `tr_upper`：当前 bar 属于 accumulation 且 `accum_phase_a_complete=True`，保持为第一次锁定时的 `ar_high`；否则 NaN
    - `tr_lower`：对应 `sc_low`，与 `tr_upper` 同步锁定
    - `tr_midpoint`：继续由 `(tr_lower + tr_upper) / 2` 计算，保持现有行为
    - `phase_b_high`：accumulation Phase B 期间已见最高价（滚动，不锁定），供诊断 UT/UTAD 幅度使用
  - **验证标准：** `tr_lower < tr_midpoint < tr_upper` 在 Phase B/C/D 区间内恒成立；Phase B 上探不引起 `tr_upper` 变化；`phase_b_high >= tr_upper` 在有 UT/UTAD 的结构中成立
  - **后续依赖方：** P6-1（用 `tr_upper`）、P6-3（用 `tr_upper`），P6-0 必须优先完成

- [x] **P6-1：SOS Bar 增加后续确认窗口（`sos_bar_confirmed` 字段）**
  - **前置依赖：** P6-0（需要 `tr_upper` 列已存在）
  - **问题：** `sos_bar_detected` 仅验证当根 K 线四大指标（宽价差 / 高成交量 / 高收盘 / 价格承诺），完全无后续确认机制
  - **后果：** UTAD（Phase B/C 的假突破）与真实 SOS（Phase D 突破）使用完全相同的检测逻辑，无法区分
  - **理论依据：** 威科夫 7 大事件 §SOS："突破后不立即回到区间内"；§假突破："突破后成交量迅速萎缩并回到区间内"
  - **具体场景：** 2026-03-13 73,500 急拉急跌，`sos_bar_detected=True`，但随后 4 小时即回落至 70,400，明显 UTAD
  - **修改文件：** `wyckoff/sos_bar_detector.py`
  - **判据定稿（2026-03-16 全部收口）：**
    - **前置条件（四项，缺一不触发）：**
      - `sos_bar_detected == True`
      - `accum_phase_a_complete == True`
      - `tr_upper` 非 NaN（P6-0 完成后有效）
      - `atr` 非 NaN
    - **参数：**
      - `_SOS_CONFIRM_WINDOW = 3`（默认 3 根，保守模式可调至 5）
      - `_SOS_CONFIRM_EPSILON_ATR_MULT = 0.10`（容忍带 = 0.10 × ATR_14；0.05 偏敏感，0.15 偏迟钝，0.10 先作为中性默认值落地）
    - **快速失守否决条件（方案 A：相邻连续，不是窗口累计）：**
      - 在 `(i+1, i+2)` 或 `(i+2, i+3)` 中，若任一**相邻对**同时满足 `close < tr_upper - epsilon`，视为快速失守，`sos_bar_confirmed = False`
      - 选方案 A 的原因：UTAD 的典型失守是"迅速、持续"地被打回，不是散点式偶发回踩；方案 B（窗口内累计 2 根）会误杀突破后的正常震荡确认
    - **确认结果：** 未触发快速失守否决 → `sos_bar_confirmed = True`
    - **增强条件（不作硬门槛，作质量加分）：** `close[i+3] >= tr_upper - epsilon`，满足则后续可对 `sos_quality_score` 加权
    - **实现注意：** 需要前视窗口，不适合纯向量化；用 `shift(-1/-2/-3)` 构造未来收盘列后做布尔组合，或局部按行循环；清晰优先于极致向量化

- [x] **P6-2：Phase B 增加最小持续时间约束（`_PHASE_B_MIN_BARS` 常量）**
  - **问题：** 只有 `_PHASE_B_MAX_BARS = 300`，无最小值；`_mark_phase_b()` 无最小时长检查，Phase B 可压缩至数根 K 线
  - **后果：** 在 1H 图上，理论上 11 小时内即可完成 Phase A→B→C→D→E 全序列，违反因果定律
  - **理论依据：** 威科夫因果定律（Law of Cause and Effect）："积累时间越长，后续趋势越强；不充分的积累无法支撑有效突破"
  - **具体场景：** 若 SC→AR（Phase A）仅发生在数根 K 线内，系统随即可进入 Phase D，完全缺乏可信度
  - **修改文件：** `wyckoff/event_sequence_state_machine.py`
  - **共识实现方案（2026-03-16 理论沟通确认）：**
    - 不要做成跨时间框架普适常量；先静态落地，后续参数化到 timeframe 配置层
    - **1H 默认值 `_PHASE_B_MIN_BARS = 20`（约 20 小时）**，经验参考值：
      - 15m: 24~32 bars；1H: 20 bars；4H: 10~12 bars；1D: 6~10 bars
    - 实现位置：在 `_mark_phase_c()` 起始处加入前置校验，**同时所有 Phase B→C 出口路径共享同一前置检查**，防止未来逻辑绕过

- [x] **P6-3：建立独立 LPS Bar 检测器（`lps_detector.py`）**
  - **问题：** 无 `lps_detector.py`，无 `lps_bar_detected` 字段；Phase D 内所有 K 线均被视为 LPS 区域
  - **后果：** 不满足"窄区间 + 极低成交量"条件的普通回调也被隐式视为 LPS，入场信号精度偏低
  - **理论依据：** 威科夫 7 大事件 §LPS："Last Point of Support — 回测时区间狭窄、成交量极低、不重入 TR 内部"
  - **修改文件：** 新建 `wyckoff/lps_detector.py`
  - **判据定稿（2026-03-16 全部收口）：**
    - **两阶段结构：**
      - `lps_pullback_active`：候选回踩过程（过程标志）
      - `lps_bar_detected`：反转确认 bar（事件标志，锚点是"重新转强的确认 bar"，不是最低点 bar）
    - **`lps_pullback_active` 触发条件：**
      - 最近 `m=15` 根内存在 `sos_bar_confirmed == True`
      - 当前价格回踩 `tr_upper` 附近
      - 当前 bar 已出现至少一类候选特征：窄价差 或 缩量 或 位置有效
    - **`lps_pullback_active` 终止条件（三类，语义不同）：**
      - **作废（B）：** 出现明显供给回归（放量大阴线），本次候选作废
      - **超时作废（C）：** 超过 `m=15` 根未出现确认 bar，作废
      - **进入确认观察（A）：** 价格重新回到 `tr_upper` 附近并开始转强，允许寻找 `lps_bar_detected`；此时不终止 active，而是从"候选回踩"进入"允许触发确认"
    - **默认阈值（2026-03-16 确认）：**
      - `m = 15`（1H 图约 15 小时，足容纳正常回踩，不至于松成普通震荡）
      - `atr_n = 14`（与 P6-1 统一 ATR 基准）
      - `vol_ma_n = 20`（成交量均值用 20，比 14 更平稳）
      - 窄价差：`true_range / ATR_14 <= 0.8`（diminished spread，不要求极端压缩）
      - 缩量：`volume / vol_ma_20 <= 0.7`（diminished volume，不要求极低量）
      - 位置约束：`close >= tr_upper - 0.25 * tr_height`（**alpha = 0.25**，允许贴边回踩，不允许深度重入 TR 腹地）
      - 确认 bar 位置：`close >= tr_upper - 0.10 * ATR_14`（epsilon_lps，与 P6-1 保持同一 ATR 比例）
    - **`lps_bar_detected` 硬门槛（仅 3 条）：**
      - `lps_pullback_active == True`
      - 反转方向：`close > prev_close` 或 `high > prev_high`（二选一满足即可）
      - 位置确认：`close >= tr_upper - 0.10 * ATR_14`
    - **`lps_bar_detected` 加分项（不作硬门槛）：**
      - `close` 在本根上半部（`close_position > 0.5`）
      - `volume` 无明显供给扩张
      - 当前实体为阳线
      - 当前 `low` 未创回踩新低
    - **理由：** LPS 确认 bar 是柔性行为事件，Wyckoff 强调回踩时 diminished spread/volume、其后恢复上行，未要求确认 bar 满足一串强动量条件；全硬化会导致样本极少，漏掉正常 LPS

### 理论沟通结论（2026-03-16 已确认）

| 问题 | 结论 |
|------|------|
| SOS 确认窗口：3 根还是 5 根？ | **默认 3 根，可配置**。目标是过滤快速失守，3 根已足够；5 根作为保守模式，Dry Run 发现仍放行过多假突破时再调 |
| Phase B 最小时长 20H 是否合适？是否需跨周期动态调整？ | **1H 默认 20 合理，但不宣称为跨周期常量**。先静态落地，后续参数化到 timeframe 配置，不做线性小时换算 |
| LPS"窄区间"如何量化？ | **以 ATR 相对比例为主（TR/ATR_n ≤ 0.8），TR 高度位置约束为辅**；TR 高度比例不能作为主条件，否则大区间误判、小区间误杀 |

---

## P5 V3：受控进化层

### 目标
建立“AI 可辅助进化，但不能失控演化”的制度闭环。

### 任务
- [ ] P5-V3-1：建立实验闸门
  - 样本量闸门
  - 零回归闸门
  - 单参数敏感性闸门
  - 三段回测闸门
  - 最大回撤闸门
  - 小样本信号审查闸门

- [ ] P5-V3-2：建立自动回滚建议逻辑
- [ ] P5-V3-3：建立 baseline 晋升规则
- [ ] P5-V3-4：建立 candidate 审批与归档机制

---

## P5 与当前主线的关系
- [x] P5 不替代当前策略逻辑
- [x] P5 不替代 Freqtrade 执行层
- [x] P5 是当前版本收尾后的下一阶段主平台任务
- [x] P5 为 P2 / P3 扩展 / P4 / Phase-aware exit / proposal 系统提供底座

---

## P5 约束
- 不允许 AI 直接改生产参数
- 不允许数据库故障影响交易执行
- 不允许在样本不足时推进参数优化
- 不允许跳过 Layer 7 / 三段回测 / Dry Run 观察
- 所有新增分析与提案必须可追溯、可解释、可回滚


## P5-WS — 华尔街参考版策略研究与进化操作系统

> 目标：参考华尔街量化研究的方法论，但做成适合个人系统规模、由 AI 辅助分析与受控进化的研究操作系统。
>
> 一句话定位：
> **P5 是机器，P5-WS 是制度。**

---

### P5-WS 当前状态
- [x] 方法论框架已确定
- [x] 五层架构已定义
- [x] AI 角色分工已定义
- [x] 分阶段落地顺序已定义
- [x] 与 P5 数据底座已完成对齐

### P5-WS 当前结论
- [x] 当前系统已从“策略开发期”进入“策略治理期”
- [x] P5-WS 不直接产生信号收益
- [x] P5-WS 的作用是：
  - 约束研究流程
  - 固化样本纪律
  - 定义 proposal / experiment / baseline / rollback 制度
  - 使 AI 分析具备边界和责任分工

---

## P5-WS 五层制度结构

### Layer 1 — Alpha Research Layer
- [x] 已定义
- [ ] 后续细化：
  - 新信号进入条件
  - 老信号退场标准
  - 最小实验矩阵模板

### Layer 2 — Risk & Regime Layer
- [x] 已定义
- [ ] 后续细化：
  - P2 触发条件对接 P5 周报
  - regime mismatch 自动识别
  - future P4 / regime-aware 授权只走 multiplier / 授权路径

### Layer 3 — Execution & Cost Layer
- [x] 已定义
- [ ] 后续细化：
  - 退出路径诊断
  - `custom_exit` 时序差异监控
  - funding 暴露代理统计

### Layer 4 — Observation & Intelligence Layer
- [x] 已定义
- [x] 已由 P5 V1 部分实现
- [ ] 后续细化：
  - detector / regime / exit decision 事件补全
  - 周报模板固化
  - drift 诊断自动化

### Layer 5 — Governance & Release Layer
- [x] 已定义
- [ ] 后续细化：
  - baseline / candidate / rollback 状态机
  - proposal 审批流
  - 回滚建议制度
  - 实验闸门自动检查

---

## P5-WS AI 角色分工

### AI 研究员
- [x] 角色已定义
- [ ] 后续实现：
  - 读取 ledger
  - 生成问题定义
  - 生成候选实验矩阵

### AI 审计员
- [x] 角色已定义
- [ ] 后续实现：
  - 样本充分性检查
  - 是否违反单参数原则
  - 是否通过三段/零回归闸门

### AI 运营员
- [x] 角色已定义
- [ ] 后续实现：
  - 周报输出
  - 状态面板
  - baseline / candidate / rollback 台账维护

### 人工角色（最终批准）
- [x] 保留
- [x] AI 不可替代
- [x] 任何 baseline 晋升必须人工批准

---

## P5-WS 分阶段推进顺序

### Phase 0 — Dry Run 稳定化
- [ ] 持续累积真实样本
- [ ] 确保事件链路在 Dry Run 中稳定运行
- [ ] 不再出现 0 交易 / 字段失真 / 容器异常

### Phase 1 — Ledger 完整化
- [x] run / signal / trade 基础链路已通
- [ ] detector / regime / exit decision 补齐
- [ ] baseline / candidate / rollback 元数据补齐

### Phase 2 — 周报与漂移
- [ ] detector 激活率
- [ ] regime 分布变化
- [ ] enter_tag drift
- [ ] 风险提示 / 样本不足提醒

### Phase 3 — Proposal 系统
- [ ] proposal 模板
- [ ] experiment registry
- [ ] 诊断 → 提案 → 实验的闭环

### Phase 4 — 受控进化
- [ ] 审批流
- [ ] baseline 晋升规则
- [ ] rollback 建议
- [ ] 自动实验的闸门控制

---

## P5-WS 与 future hooks 的关系

### 已预埋未接线内容
- [~] Phase-aware 退出（`wyckoff/exit_logic.py` 已实现，策略未接线）
- [~] future regime-aware authorization（P4 预埋点）
- [~] RR / 持仓寿命 / 信号路由授权层

### P5-WS 对它们的要求
- [x] 先观察，再接线
- [x] 先入 ledger，再入策略
- [x] 必须有独立开关、独立 commit、独立验证矩阵
- [x] 默认不得直接进入生产逻辑

---

## P5-WS 约束
- 不做华尔街式大规模自动参数搜索
- 不做 AI 直接写生产策略
- 不做样本不足下的激进自动优化
- 不做绕过人工批准的 baseline 晋升
- 所有新制度必须满足：
  - 可解释
  - 可回测
  - 可关闭
  - 可回滚

---

## P5 / P5-WS 下一步总优先级
1. [ ] 补齐 `detector_event / regime_snapshot / exit_decision_event`
2. [ ] 做 drift 报表（enter_tag / detector / regime）
3. [ ] 做 AI 周报模板
4. [ ] 做 proposal / experiment registry
5. [ ] 做 baseline / candidate / rollback 状态机