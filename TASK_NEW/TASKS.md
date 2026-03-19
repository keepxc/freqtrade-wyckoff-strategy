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

----------------------------------------------------------------

## Core Audit — 收益导向版（当前版本收尾前）

> ✅ **Core Audit 已完成 — 2026-03-17**  
> 报告：[TASK_NEW/CORE_AUDIT_REPORT.md](CORE_AUDIT_REPORT.md)  
> 触发条件：进入重大接线前（P2 regime-aware / Phase-aware exit）  
> 5 个必答问题全部答出，Core Audit 结束。

### Goal
在进入下一阶段开发前，先查清当前核心交易链里：

- 哪些模块**真改变成交集合**
- 哪些退出逻辑**真改变最终收益**
- 哪些字段/模块只是看起来有用，实际上**不产出收益**
- 下一步最值得开发的点，到底是**入场、退出，还是观测**

> 目标不是写审计报告。  
> 目标是：**找出真钱问题，避免继续在低 ROI 方向浪费开发时间。**

### 触发条件（满足任一条即启动 Core Audit）
- [ ] Dry Run 连续运行 `>= 14 天`
- [ ] 或累计真实交易 `>= 30 笔`
- [ ] 或 AI 周报 / 监控系统给出 `L3 / L4` 风险信号
- [x] 或进入重大接线前：
  - `Phase-aware exit`
  - `P2 regime-aware 授权系统`
  - 其他会直接影响交易行为层的关键模块

> 说明：  
> 这里的 `P2 regime-aware 授权系统`，**专指真正进入交易决策面的 regime 授权接线**。  
> 不包括以下内容：
> - `market_env / htf_market_env / htf_market_env_ok` 等 regime 字段本身
> - `regime_snapshot`
> - regime distribution drift
> - regime 维度绩效复盘
> - 其他观测 / 审计 / 诊断层 regime 分析  
>
> 上述内容属于**观测层 / 审计层 / 诊断层**，不是这里说的 `P2 regime-aware 授权系统`。

---

## CA-1：找出“真影响成交”的链路 ✅

### 目标
确认当前成交集合到底主要由哪些条件决定。  
把“会变但不改交易”的东西和“真改交易”的东西分开。

### 重点排查
- detector → phase/context → signal_quality → entry gate → enter_tag
- `sos_bar_confirmed` 是否真实挡掉交易
- `lps_bar_detected` 是否真实新增交易
- `phase_quality_mult` / gate / context 是否只是改分数，不改成交
- 哪些模块输出很多字段，但最终不影响 entry

### 必答问题
- 当前最主要的进场路径是哪几条
- 哪些字段只是“看起来高级”，其实不改变成交集合
- 哪些改动会像 `P1-A` 一样，只改数字不改交易

### 完成标准
必须产出：
- 真影响成交的字段清单
- 只改中间值、不改成交的字段清单
- 当前主导成交的前 `3` 条路径

---

## CA-2：找出“真影响收益”的退出链路 ✅

### 目标
确认当前谁在真正主导退出，以及钱主要死在哪。

### 重点排查
- `ROI`
- `stoploss`
- `custom_exit`
- `structure invalidation`
- `swing E/R`
- future hooks（`phase-aware exit` / `P&F exit`）当前到底有没有生效

### 必答问题
- 当前实际退出主裁判是谁
- 好单利润是被谁拿回去的
- 烂单是被谁拖死的
- 哪些退出逻辑只是名义存在，实际不生效
- 下一步最可能提高收益的是哪一类退出改动

### 完成标准
必须产出：
- 当前真实退出链顺序
- 利润回吐最严重的 `2~3` 类模式
- 亏损拖延最明显的 `2~3` 类模式
- 下一步退出优化最值得先做的点

---

## CA-3：找出“浪费开发时间”的模块和方向 ✅

### 目标
避免继续投入在不改变收益的地方。

### 重点排查
- 哪些模块有输出但未消费
- 哪些模块只做标签/描述，不影响交易
- 哪些 future hooks 容易被误以为已经有能力
- 哪些方向已经被证明边际低（例如只改 quality 不改成交）

### 必答问题
- 哪些模块现在不值得继续投入
- 哪些 future hook 应继续保持 `off`
- 哪些方向当前 ROI 最低
- 哪些方向不该在当前阶段进入主线

### 完成标准
必须产出：
- 暂停开发清单
- 保留观察但不接线清单
- 当前低 ROI 方向清单

---

## CA-4：确定“下一步最值得做的开发项” ✅

### 目标
不是列很多建议，而是只定下一步最赚钱的 `1~2` 个开发动作。

### 候选范围（只能从这里面选）
1. 退出层优化
2. 运行时观测补强
3. 少量入场链修正

### 判断原则
必须同时满足至少两条：
- 明确影响成交集合
- 明确影响退出收益
- 已有样本支持
- 不是 future hook 幻觉
- 不是“看起来合理但尚未改变交易行为”的方向

### 完成标准
最后只能收口成：
- `Next #1`
- `Next #2`
- `暂不做`

---

## 执行顺序

1. `CA-1`：真影响成交的链路
2. `CA-2`：真影响收益的退出链路
3. `CA-3`：开发时间止损
4. `CA-4`：只定下一步最值得做的开发项

---

## 输出要求（务实版）

每个 `CA` 子项都不要追求长报告。  
只输出下面三样：

1. 发现了什么真问题
2. 这个问题是否影响成交或收益
3. 建议：继续做 / 暂停 / 进入下一开发项

---

## 明确不做

在 Core Audit 阶段，先不做这些：

- 大规模参数重扫
- 新 detector 支线
- 自动 proposal 闭环
- `phase-aware exit` 直接接生产
- 大篇幅总结型报告

---

## Core Audit 完成标准

只有当下面 `5` 个问题都能答出来，Core Audit 才算完成：

1. 当前到底是哪几条链路在真实决定成交
2. `sos_bar_confirmed` 和 `lps_bar_detected` 到底有没有真实改变交易
3. 当前退出到底是谁在主导，钱主要死在哪
4. 哪些模块/方向现在继续做大概率不挣钱
5. 下一步最值得做的开发项到底是什么

---

## P7-min — Audit 运行时证据补丁

### Goal
只补 Audit 真需要的运行时证据。  
不另开大主线，不铺大观测系统。

> 作用：  
> 给 `Core Audit` 提供**运行时真证据**，补足“静态代码看起来接了，但到底有没有改变成交/退出”的缺口。

---

## P7-1：signal_decision_trace

### 目标
记录每个候选信号最终为什么进、为什么没进。  
确认关键字段是否真实参与交易决策。

### 修改位置
- `populate_entry_trend()`
- enter_tag 生成处
- `signal_quality / quality multiplier` 接线处
- regime 授权接线处（如已存在）

### 记录字段
- `timestamp`
- `pair`
- `enter_candidate`
- `enter_approved`
- `enter_tag`
- `blocked_reason`
- `sos_bar_detected`
- `sos_bar_confirmed`
- `lps_bar_detected`
- `phase_d_confirmed`
- `accum_phase_a_complete`
- `tr_upper`
- `phase_quality_mult`
- `market_env`
- `htf_market_env_ok`

### 输出
- JSONL 或 SQLite 表：`signal_decision_trace`

### 启动条件
仅当 `CA-1 / CA-4` 证明：
- 静态代码审查仍无法确认谁真影响成交
- 需要运行时证据来收口

### 验收标准
- 能看到每笔候选信号的放行/拦截原因
- 能确认 `sos_bar_confirmed / lps_bar_detected` 是否真实影响成交集合
- 能区分“真决策字段”与“仅描述字段”

---

## P7-2：exit_decision_event

### 目标
把退出层从黑盒变成可诊断事件流。

### 修改位置
- `custom_exit()`
- `wyckoff/exit_logic.py`
- event bridge / event emitter
- 策略主文件里的退出决策调用点

### 记录字段
- `timestamp`
- `pair`
- `trade_id`
- `current_profit`
- `max_profit_seen`
- `holding_hours`
- `enter_tag`
- `current_phase`
- `market_env`
- `exit_reason_candidate`
- `final_exit_reason`
- `blocked_reason`
- `fallback_used`
- `structure_invalidation_flag`
- `phase_exit_flag`
- `effort_result_exit_flag`

### 输出
- SQLite 表：`exit_decision_event`

### 启动条件
仅当 `CA-2 / CA-4` 证明：
- 当前退出链仍是黑盒
- 需要运行时证据来确认谁真主导收益

### 验收标准
- 每次退出判断都有事件记录
- 能看出“原本想怎么退、最后怎么退”
- 能区分结构退出 / ROI / stop / fallback

---

## P7-3：P6 trade diff（条件启动）

### 目标
确认 `P6` 到底删掉了哪些烂单、增加了哪些好单。

### 输入
- `baseline` 回测结果
- `baseline + P6` 回测结果

### 输出表
1. 新增交易
2. 删除交易
3. 标签变化交易
4. 同交易但质量状态变化交易

### 每笔比较字段
- `entry_time`
- `exit_time`
- `entry_price`
- `pnl`
- `enter_tag`
- `sos_bar_confirmed`
- `lps_bar_detected`
- phase / detector 状态

### 输出文件
- `scripts/diff_trades_p6.py`
- `reports/p6_trade_diff.md`

### 启动条件
仅当 `CA-1` 结束后仍无法明确回答：
- `sos_bar_confirmed` 到底挡掉了哪几笔交易
- `lps_bar_detected` 到底新增了哪几笔交易

### 验收标准
- 明确列出 `P6` 新增/删除的交易
- 能指出 `sos_bar_confirmed` 挡掉的具体单
- 能指出 `lps_bar_detected` 新增的具体单

---

## P7-min 执行顺序

1. `P7-1 signal_decision_trace`
2. **`P7-2 exit_decision_event` ← 当前主线（2026-03-19）**
3. `P7-3 P6 trade diff`（条件启动）

---

## P7-min 明确不做

当前阶段，先不做这些：

- `detector_event` 全量扩张
- `regime_snapshot` 全量扩张
- `enter_tag drift` 全量扩张
- 大而全的行为观测平台
- 与当前 Audit 收口无关的事件化铺设

---

## P7-min 完成标准

只有当下面问题能被回答时，P7-min 才算完成：

1. 哪些字段真实改变了成交集合
2. `sos_bar_confirmed` 和 `lps_bar_detected` 到底改变了哪些具体交易
3. 当前退出链到底是谁在主导
4. 当前最值得优化的退出问题是哪一个
5. 是否已经有足够运行时证据进入退出层优化

---

## P0 — 退出层优化（收益兑现主战场）

### Goal
把退出层从“单一 ROI / fallback 驱动”升级为“多层退出栈”，优先解决：

- 好单利润被回吐
- 烂单退出过慢
- 不同 enter_tag 缺乏差异化退出节奏
- 结构类交易没有被结构类退出接住

> 目标不是先做复杂功能。  
> 目标是：**先找出最容易多赚、少回吐、少拖损的退出改动。**

---

## P0-1：确认当前真实退出主裁判

### 目标
先确认当前到底是谁在决定退出，不要在错误理解上继续开发。

### 重点排查
- `ROI`
- `stoploss`
- `custom_exit`
- `structure invalidation`
- `swing E/R`
- future hooks（`phase-aware exit / P&F exit`）当前是否只是挂着

### 必答问题
- 当前真实退出主裁判是谁
- `ROI` 现在是主裁判，还是已经只是 `fallback`
- `custom_exit` 到底有没有真实改变最终收益
- 哪些退出逻辑只是“存在”，但当前不生效

### 完成标准
必须产出：
- 当前真实退出链顺序
- 当前退出主裁判结论
- 名义存在但未生效的退出逻辑清单

### 2026-03-17 结果（已完成）

- 样本来源：`backtest-result-2026-03-17_09-41-03.zip`（393 笔）
- 退出贡献（profit_ratio 合计）
  - `wyckoff_down_wave`: 336 笔，`+4.0113`
  - `roi`: 34 笔，`+2.7070`
  - `wyckoff_climax`: 9 笔，`-0.0871`
  - `trailing_stop_loss`: 5 笔，`-0.6003`
- 当前真实退出主裁判结论：
  - 频次主裁判：`wyckoff_down_wave`
  - 收益兑现主裁判：`wyckoff_down_wave + roi`
  - 亏损主裁判：`trailing_stop_loss`（单项亏损最大）
- 名义存在但当前未成为主裁判的退出方向：`phase-aware exit / P&F exit / swing E-R`（当前以基础设施或辅助信号为主）

---

## P0-2：找出最亏的退出缺陷

### 目标
先找“最丢钱”的退出模式，而不是先写新规则。

### 重点排查
- 峰值利润回吐
- 亏损拖延
- 持仓过久
- 不同 enter_tag 的退出失配
- 不同 regime / phase 下的退出失配

### 必答问题
- 哪类交易最容易“本来赚得到，最后拿回去”
- 哪类交易最容易“早该走，却一直拖”
- 哪些 enter_tag 最不适合继续靠统一 `ROI`
- 哪些样本最值得优先接 `phase-aware exit`

### 完成标准
必须产出：
- 利润回吐最严重的 `2~3` 类模式
- 亏损拖延最严重的 `2~3` 类模式
- 最值得优先优化的 enter_tag / phase 样本类型

### 2026-03-17 结果（已完成）

- 利润回吐最严重模式：
  - `trailing_stop_loss`：5 笔全亏，平均 `-12.006%`，合计 `-0.6003`（全策略最大亏损来源）
  - `wyckoff_climax`：9 笔，平均 `-0.967%`，合计 `-0.0871`
- 亏损拖延最严重模式：
  - `trailing_stop_loss` 平均持仓 `6036` 分钟（约 `4.19` 天），显著长于 `wyckoff_climax` 的 `1593` 分钟（约 `1.11` 天）
- 样本标签集中度：
  - `trailing_stop_loss`：`5/5` 来自 `wyckoff_axis_weak`
  - `wyckoff_climax`：`7/9` 来自 `wyckoff_axis_weak`
- 优先优化样本类型：`wyckoff_axis_weak` 持仓在不利波动中的风险保护

---

## P0-3：设计退出层四层栈（只定结构，不急着全接）

### 目标
把未来退出系统结构定清楚，避免后续越改越乱。

### 四层退出栈
1. **硬风险退出**
   - `stoploss`
   - 极端风险保护
   - 熔断 / 异常退出

2. **结构失效退出**
   - Wyckoff 结构破坏
   - phase 失效
   - `SOS / LPS` 前提被破坏

3. **阶段 / 过程退出**
   - `phase-aware exit`
   - `swing E/R` 衰竭
   - 持仓寿命调节
   - 分批止盈节奏
   - trailing 强度调节

4. **目标 / 时间退出**
   - `P&F target`
   - `ROI fallback`
   - 最大持仓时长
   - 时间止盈 / 止损

### 必答问题
- 哪一层是主裁判
- `ROI` 应降级到哪一层
- `P&F` 应该做目标坐标还是直接做裁判
- `swing E/R` 最适合作为减仓、止盈还是退出确认

### 完成标准
必须产出：
- 退出层四层栈的职责分配
- 每一层“该做 / 不该做”边界
- `ROI / P&F / phase-aware / E/R` 的正确定位

---

## P0-4：Phase-aware exit 的最小接线方案

### 目标
不直接全量上线，只设计最小灰度接线方案。

### 允许先做的能力
- 持仓寿命调节
- 分批止盈节奏
- trailing / stop 保护强度调节

### 暂不做
- 直接替代主退出裁判
- 大规模动态退出切换
- 未验证就覆盖 `ROI / custom_exit` 主逻辑

### 接线原则
- 默认 `off`
- 独立 commit
- 最小验证矩阵
- 三段回测
- Dry Run 灰度

### 完成标准
必须产出：
- `phase-aware exit` 最小接线点
- 默认参数与开关
- 灰度验证方案

---

## P0-5：P&F exit 的定位与接线原则

### 目标
明确 `P&F` 在退出层里到底扮演什么角色，防止误用。

### 原则
- `P&F` 优先作为**结构目标坐标**
- 不直接当唯一退出裁判
- 不应替代风险控制
- 不应孤立脱离 `phase / structure` 使用

### 必答问题
- `P&F` 更适合全平、分批止盈，还是目标参考
- 哪些 enter_tag 最适合接 `P&F target`
- 与 `ROI / phase-aware exit` 怎么协同

### 完成标准
必须产出：
- `P&F exit` 定位结论
- 与 `ROI / phase-aware` 的协同原则
- 暂不直接接线的边界

---

## P0-6：确定下一步只做哪个退出优化点

### 目标
不同时开多个退出方向，只选一个最有收益潜力的点先做。

### 候选
- `A`：phase-aware 持仓寿命调节
- `B`：按 enter_tag 差异化止盈节奏
- `C`：swing E/R 提前减仓 / 退出确认
- `D`：ROI 降级为 fallback / 护栏

### 判断原则
必须满足至少两条：
- 已有样本支持
- 明确对应利润回吐或亏损拖延问题
- 不依赖大量新基础设施
- 可默认 `off` 灰度验证
- 不会破坏当前核心退出链

### 完成标准
最后只能收口成：
- `Next Exit #1`
- `暂不做`

### 2026-03-17 决策（已收口）

- `Next Exit #1`：仅做 `trailing_stop_loss` 最小优化实验（不同时改 climax）
  - 实验目标：判断这 5 笔是“可接受黑天鹅”还是“可被更聪明保护降低损失”
  - 实验边界：只做一处最小改动，默认可回退，不改主退出链结构
  - 优先样本：`wyckoff_axis_weak` 持仓
- `暂不做`：
  - `wyckoff_climax` 规则改造（先不动，避免双变量）
  - phase-aware/P&F 全面接线（留在后续 P0-4/P0-5）

---

## Next Exit #1 实验结果（2026-03-17）— FAIL

### 实验设计
- 参数：`WY_AXIS_WEAK_TIGHT_SL=1`，`axis_weak_stoploss=-0.08`
- 逻辑：对 `wyckoff_axis_support` 持仓收紧止损至 -8%（原 axis_weak 全量皆为 weak tier）
- 目的：阻止 5 笔 TSL 直跌触达 -12%

### 静态分析预判（基于 min_rate 离线分析）
| 止损阈值 | 牺牲盈利交易数 | 节省损失总量 |
|---------|------------|------------|
| -8%（选定）| 1 笔(+0.03%)  | 0.2019     |

### 实际回测结果对比

| 指标          | Baseline    | 实验(-8%)   | 变化           |
|---------------|-------------|------------|----------------|
| 总收益         | 965.76%     | 956.24%    | **-9.52pp**    |
| 最大回撤       | 8.13%       | 10.57%     | **+2.44pp 更差** |
| TSL 笔数       | 5 笔 -12%   | 17 笔 -8%  | 激增 +12 笔    |
| TSL 损失贡献   | -128.04pp   | -347.61pp  | 更差 -219pp    |
| Sharpe        | 1.94        | 1.83       | 下降           |
| SQN           | 5.91        | 5.49       | 下降           |

### 失败根因  
静态分析（基于 `min_rate`）假设交易相互独立。实际回测中：
- -8% 止损触发 12 笔原本可以恢复的交易提前止损
- 提前止损 → 更早开仓下一笔 → 可能在更差市场窗口入场 → 连锁损失
- `min_rate` 分析在顺序回测中不等于实际影响

### 结论
- 5 笔黑天鹅（-12%）无法与其他交易在入场前区分
- 任何针对 axis_support 的统一收紧止损都会"误伤"可恢复的良性交易
- **接受这 5 笔为系统内含风险，不以止损手段消除**
- 代码已保留（`use_axis_weak_tight_sl` 默认 `0`，不激活）

---

## P0 执行顺序

1. `P0-1`：确认当前真实退出主裁判
2. `P0-2`：找出最亏的退出缺陷
3. `P0-3`：设计退出层四层栈
4. `P0-4`：Phase-aware exit 最小接线方案
5. `P0-5`：P&F exit 定位与接线原则
6. `P0-6`：只选一个退出优化点先做

---

## P0 明确不做

在当前阶段，先不做这些：

- 同时上线多个退出新逻辑
- 让 `phase-aware exit` 直接替代主退出裁判
- 把 `P&F` 直接当唯一退出规则
- 未验证就覆盖现有 `ROI / stop / custom_exit`
- 大规模参数扫退出组合

---

## P0 完成标准

只有当下面 `5` 个问题都能答出来，P0 才算进入可开发状态：

1. 当前谁在真实主导退出
2. 钱主要死在哪几类退出缺陷上
3. `ROI` 在未来退出层里应降级到什么位置
4. `phase-aware / P&F / swing E/R` 各自该扮演什么角色
5. 下一步只做哪个退出优化点最值得

---

## ▶ 当前主线任务：P0-Next（2026-03-17 更新）

### Core Audit + 实验数据综合结论

**背景**：P0 Next Exit #1（TSL 收紧）失败后，用最新 baseline 数据重新对齐。

**决定性矛盾**（最新数据）：

| 入场 × 退出 | 笔数 | WR | avg |
|------------|------|-----|-----|
| `axis_support × down_wave` | 248 | 65.7% | +0.80% |
| `axis_support × roi` | 24 | 100% | +7.92% |
| `wave_start × down_wave` | 74 | 98.6% | +2.40% |

同一 down_wave 机制：axis_support WR=65.7% vs wave_start WR=98.6%。  
axis_support 只有 8.8% 的交易走到 roi（收益 7.92% avg），91.2% 被 down_wave 以 0.80% avg 截断。

### Next #1：按 enter_tag 差异化 down_wave 出场确认

**目标**：减少 axis_support × down_wave 的错误出场（提升 WR → 向 roi 路径引导更多交易）

**实验设计**：
- 对 `axis_support` 入场的持仓，在 down_wave 退出条件上要求更多确认（提高阈值 或 增加 K 线确认数）
- 对 `wave_start` 入场的持仓：**维持现有条件不变**（98.6% WR 不动）
- 实现位置：`custom_exit()` 中 `_custom_exit_impl()` 的 down_wave 判断，按 `trade.enter_tag` 分支
- 控制开关：`WY_AXIS_WEAK_DW_CONFIRM`（默认 off）
- 对比基线：965.76% / 8.13% DD / WR=74.0%

**预期效果**：如果 248 笔中有 20-30 笔原本可以继续持有走到 roi（7.92% avg），对应 ~(7.92%-0.80%) × 20 = +142pp 潜在收益。

**约束**：
- 不改 wave_start 出场条件（98.6% WR 保护）
- 不同时改其他退出规则（单变量）
- 一次只调一个参数（确认 K 线数 or 效率阈值）

### 暂不做（已确认）
- TSL 收紧（已验证 FAIL）
- Spring 系列激活（0 样本）
- Regime 参数路由（backlog）
- wyckoff_climax 收紧（样本 9 笔太少，二期再做）



---

## ▶ 下一主线方向：双通道分流（2026-03-17 更新）

### 背景与决策依据

微调路线已穷尽：trailing 收紧 / down_wave 门控 / EMA 过滤 / confidence 过滤 / axis tier / 高量低量过滤 —— 全部失败或边际极低。

**根本原因**：同一条退出主裁判（down_wave）不适合同时服务两种完全不同语义的入场。

| 入场通道 | 交易语义 | down_wave WR | 适配性 |
|---------|---------|-------------|--------|
| `wave_start` | 强趋势 / 快速兑现 | **98.6%** | 极强 |
| `axis_support` | 支撑承接 / 持仓等待 | **65.7%** | 弱 |

**最优下一步**：拆分两条赚钱子系统，分别量化独立贡献，为差异化退出层/ROI/风险管理提供数据支撑。

---

## C-1：wave_start-only 通道实验 ✅ 已完成 2026-03-17

### 实现方式
- 环境变量：`WY_CHANNEL=wave_start`，默认关闭
- 屏蔽块：`channel_mode` 在 `final_entry` 前将非 wave_start 信号全部归零

### 回测结果（backtest-result-2026-03-17_15-03-43）

| 指标 | wave_start-only | Baseline (全通道) |
|------|--------|---------|
| 总收益 | **440.71%** | 965.76% |
| CAGR | 70.54% | 未记录 |
| 总笔数 | **161** | 393 |
| 平均卫 | 2.65% | 未记录 |
| 平均持仓 | 1d 07:53 | 未记录 |
| **WR** | **96.3%** (155/0/6) | 74.0% |
| **最大回撤** | **1.23%** | 8.13% |
| Sharpe | **2.70** | 1.94 |
| SQN | **12.79** | 5.91 |
| Calmar | 592.14 | 未记录 |
| Profit Factor | 112.85 | 未记录 |

退出分布：
| 退出原因 | 笔数 | avg | WR |
|---------|-----|-----|-----|
| wyckoff_down_wave | 139 | +2.13% | 97.8% |
| roi | 16 | +8.01% | 100% |
| wyckoff_efficiency_decline | 3 | +0.64% | 33.3% |
| wyckoff_divergence | 1 | +0.48% | 100% |
| wyckoff_climax | 2 | +0.04% | 50% |

### 结论
- wave_start 单独运行：**几乎完美的交易系统**
- 1.23% DD 远好于 baseline（全部回撤来自 axis_support）
- SQN 12.79 ＋Sharpe 2.70：质量极高
- down_wave 退出 WR=97.8%：语义完全适配 wave_start 语义

---

## C-2：axis_support-only 通道实验 ✅ 已完成 2026-03-17

### 实现方式
- 环境变量：`WY_CHANNEL=axis_support`

### 回测结果（backtest-result-2026-03-17_15-09-xx）

| 指标 | axis_support-only | Baseline (全通道) |
|------|-----------|---------|
| 总收益 | **531.70%** | 965.76% |
| CAGR | 79.14% | 未记录 |
| 总笔数 | **364** | 393 |
| 平均卫 | 1.30% | 未记录 |
| 平均持仓 | 2d 00:34 | 未记录 |
| **WR** | **71.4%** (260/0/104) | 74.0% |
| **最大回撤** | **8.13%** | 8.13% |
| Sharpe | 1.71 | 1.94 |
| SQN | 5.41 | 5.91 |
| Calmar | 108.27 | 未记录 |
| Profit Factor | 2.27 | 未记录 |

退出分布：
| 退出原因 | 笔数 | avg | WR |
|---------|-----|-----|-----|
| wyckoff_down_wave | 318 | +1.09% | 71.4% |
| roi | 24 | +7.92% | 100% |
| wyckoff_divergence | 3 | +0.31% | 100% |
| force_exit | 1 | +0.12% | 100% |
| wyckoff_efficiency_decline | 4 | -0.64% | 25% |
| wyckoff_climax | 9 | -0.57% | 44.4% |
| trailing_stop_loss | 5 | -12.01% | 0% |

### 结论
- axis_support 是 **所有 DD 的来源**：baseline DD=8.13% 完全由 axis_support 贡献
- down_wave 退出 WR 只有 71.4%：退出语义与 axis_support 进场语义完全不配
- roi 路径 WR=100%, avg=+7.92%：这 24 笔才是 axis_support 的 sweet spot
- TSL 5 笔全部不兌，-12% avg：-83.8pp 直接损失

---

## C-3：双通道综合结论 ✅ 已收口 2026-03-17

### 关键发现

| | wave_start-only | axis_support-only | 全通道 baseline |
|--|--------|------------|---------|
| 总收益 | 440.71% | 531.70% | 965.76% |
| WR | **96.3%** | 71.4% | 74.0% |
| 最大DD | **1.23%** | 8.13% | 8.13% |
| Sharpe | **2.70** | 1.71 | 1.94 |
| SQN | **12.79** | 5.41 | 5.91 |
| 笔数 | 161 | 364 | 393 |
| down_wave WR | **97.8%** | 71.4% | 混合 |

**核心论断**：
1. **wave_start 是高纯度系统**：SQN 12.79 / Sharpe 2.70 / DD 1.23%，不需动，只需保护
2. **axis_support 是全部 DD 来源**： baseline DD 8.13% 完全是 axis_support 的贡献
3. **axis_support 的真实电价在 roi**：24 笔 roi (avg +7.92%, WR=100%) 是其核心价值
4. **down_wave 这个退出语义对 axis_support 根本不适用**：318 笔中 91 笔被错误带出
5. **两个通道运行时不是简单叠加**：因为 max_open_trades=1，两个通道互相竞争资金，整体结果面目大于单个但质量被抹平

### 决策方向

**选定：路线 A —— 为 axis_support 设计独立 ROI 梯度**

王牌数据：
- axis_support 中 24 笔走到 roi 的：WR=100%, avg=+7.92%
- 它们被拦截的 318 笔 down_wave 中有多少笔原本应该加入 roi 组？
- 目标：把 axis_support 的 roi 占比从 6.6%（=24/364）提到 20-30%，这唯一需要的拤手就是调整 ROI 梯度

**暂不做**：
- 路线 B（拆独立子策略）：需要 Freqtrade 多实例架构，工程量过大
- 路线 C（关闭 axis_support）：axis_support 单独 531.7%，有独立价值，射杯了主老

### 下一步：C-4 —— axis_support 部分的独立 ROI 梯度实验

详见 C-4 节点。

---

## C-4：axis_support 退出优化实验系列

### C-4-v1：严格连续确认（CONFIRM flag）❌ FAIL 2026-03-17
**实验**：WY_AXIS_SUPPORT_DW_CONFIRM=1，axis_support持仓要求2根K线连续确认+效率阈值×2。  
**结果**：965.76%，393笔，与baseline完全一致。  
**根因**：BTC 2023-2026强趋势市场中，大多数down_wave效率远高于0.002（双倍阈值仍被轻易满足）。  
**结论**：阈值调节路径无效。

### C-4-v2：完全禁用 axis_support down_wave ❌ FAIL 2026-03-18
**实验**：WY_AXIS_NO_DW=1，axis_support持仓完全跳过down_wave退出，依赖ROI+TSL。  
**实现细节**：
- 第一次尝试（class variable + custom_exit pass）→ 完全无效  
- 根因诊断：`populate_exit_trend` 向量化写入 exit_tag='wyckoff_down_wave'，  
  `custom_exit` 返回 None 无法阻止向量化信号触发  
- 修复：改用 `confirm_trade_exit` 返回 False 拦截（唯一能取消任何退出的钩子）  
- 确认 flag 生效后，结果如下：

**C-4-v2 结果（confirm_trade_exit 拦截生效后）**：
| 指标 | baseline | C-4-v2（禁用DW） |
|------|----------|-----------------|
| 总收益 | 965.76% | **86.70%** |
| 笔数 | 393 | 137 |
| WR | 74.0% | 70.1% |
| Sharpe | 1.94 | 0.31 |
| SQN | 5.91 | 1.60 |
| Max DD | 8.13% | **18.17%** |
| TSL亏损笔 | 5笔 avg-12% | **18笔 avg-12%** |
| axis_support×roi | 24笔 | 38笔 |

**根本结论**：down_wave 对 axis_support 是**保护性退出机制**，不是拖累。  
- 原先 axis_support×down_wave 85笔亏损（avg约-1~-2%）如不通过DW及时止出  
  → 拖成TSL -12%的深度亏损（净增13笔×12% ≈ -156%级别损失）  
- ROI增加从24→38笔（+14笔×+7.75%），但远不足以弥补TSL增加损失  
- **禁用DW路径已证伪，axis_support必须保留down_wave作为限损机制**

### C-4 系列总结
**两条路径均已失败**：
1. 调节DW阈值（CONFIRM）→ BTC趋势市场下无鉴别力
2. 禁用DW（NO_DW）→ 失去限损功能，灾难性增加TSL

**关键认知升级**：
- axis_support×down_wave WR=65.7%：这不是"DW选错方向"，而是"DW在合适时机止损"
- axis_support×down_wave的85笔亏损：它们本就是正确的限损退出，平均亏损可控
- axis_support的真正问题是**入场信号质量**，而非退出机制

---

## T-1/T-2/T-3 双通道架构分析框架（已收口 2026-03-19）

### 背景
C-4系列结束后，转入数据驱动的架构分析：量化两个通道的机会成本、独立贡献和优先级关系。

---

### T-1：axis_support 机会成本分析 ✅ 完成 2026-03-19

**问题**：axis_support 持仓期间是否挤占了本可入场的 wave_start？

**分析方法**：交叉比对全量回测（393笔）与 wave_start-only 回测（161笔），识别被 axis_support 占位时错过的 wave_start 信号。

**结果**：
- axis_support 窗口覆盖 64 个 wave_start 信号（共292个axis_support交易窗口中有冲突）
- 这 64 个被阻断的 wave_start 潜在收益（C-1基准）：WR 95.3%，avg +2.49%，理论贡献 ≈ 160pp
- axis_support 实际产出：+318pp（292笔 × avg +1.09%）
- 机会成本覆盖率：**318% / 160% = 199%**

**结论**：axis_support 以 **199%** 的比例覆盖了它的机会成本。双通道并存在整体收益上是正当的。

---

### T-2：双通道固定账户台账 ✅ 完成 2026-03-19

**全量 baseline（393笔）账户分拆**：

| 通道 | 交易数 | 总收益贡献 | WR | avg盈亏 | avg持仓 | TSL笔数 | 大亏笔数 |
|------|--------|-----------|-----|---------|---------|--------|--------|
| `wave_start` | 86 | **+253.22%** | **97.7%** | +2.94% | 31.5h | **0笔** | **0笔** |
| `axis_support` | 292 | **+318.32%** | 66.4% | +1.09% | 56.8h | **5笔(-12%)** | **6笔** |
| others (sos/lps/spring) | 15 | +33%左右 | — | — | — | — | — |

**关键事实**：
- wave_start 贡献全策略总收益的 **41.9%**，用 86 笔
- axis_support 贡献 **52.7%**，用 292 笔（3.4倍交易数）
- **全部 5 笔 TSL（-12%）来自 axis_support，wave_start 零 TSL**
- wave_start 是高效率、低风险通道；axis_support 是高容量、高噪声通道

---

### T-3：wave_start 优先级实验 ❌ FAIL 2026-03-19

**实验设计**：`WY_WS_PRIORITY=1` — 当同一K线同时触发 wave_start 和 axis_support 入场时，清除 axis_support 信号，wave_start 优先。

**实际清除量**：69个冲突K线（日志确认）

**结果**：

| 指标 | baseline | T-3 (WY_WS_PRIORITY=1) | 变化 |
|------|----------|----------------------|------|
| 总收益 | **965.76%** | 773.69% | **-192pp ↓** |
| 交易数 | 393 | 429 | +36 |
| WR | 74.0% | 75.5% | +1.5pp |
| Sharpe | 1.94 | 1.94 | = |
| SQN | 5.91 | 5.64 | -0.27 |
| 最大回撤 | 8.13% | 8.21% | +0.08pp |
| **TSL 笔数** | **5笔** | **48笔** | **+43 爆炸** |

**失败根因**：
- 清除冲突K线上的 axis_support 信号后，后续 axis_support 在更差位置入场（TSL 5→37笔）
- wave_start 自身新增 TSL 11笔（持仓期间资金被抢占导致入场时机变差）
- max_open_trades=1/2 限制下，优先级调整引发连锁时序错位

**结论**：T-1 发现的 64 个冲突窗口中，wave_start 缺席不是被 axis_support"抢占席位"造成的，有更复杂的市场时序原因。强行重排优先级只会破坏 axis_support 的入场时机。

---

### 双通道改造线最终收口（2026-03-19）

**已证伪的全部路径**：

| 实验 | 结果 | 核心发现 |
|------|------|---------|
| C-4-v1 (CONFIRM flag) | ❌ FAIL | BTC趋势下DW效率远高于双倍阈值，无鉴别力 |
| C-4-v2 (完全禁用DW) | ❌ 灾难性 | 965%→87%，TSL 5→18，DW是保护机制 |
| Route 1 (利润锁定) | ❌ 数据关闭 | 仅7/85笔曾到达+2%，样本不足 |
| Route 2 (入场质量过滤A-2系列) | ❌ 6轮穷尽 | 6轮A-2实验全部失败 |
| T-3 (wave_start优先) | ❌ FAIL | TSL 5→48，收益-192pp |

**决策：关闭双通道改造线**

- 双通道**可继续作为分析框架**（解释系统为何赚钱、为何回撤）
- 但**不再作为交易逻辑改造主线**
- Baseline 965.76% 是当前最优配置，axis_support×down_wave 结构维持原样

**明确不再开的方向**：
- axis_support ROI 梯度
- axis_support down_wave 延迟/降权
- wave_start 优先级二次修补
- 新一轮 signal filter
- 完整 regime 参数系统
- P5-V3 / P5-WS（当前阶段）

---

## P7-2 增强版：exit_decision_event 证据层（当前主线 2026-03-19）

### 背景与优先原因

本轮 T-3 失败再次暴露了核心缺口：**能看到数字，但看不到每笔退出发生时的结构状态、候选队列、浮亏深度**。

当前 P5-V1B-3 中的 `exit_decision_event` 只覆盖了 `phase-aware exit` 和 `p3_swing_up_er_weak`。需要扩充到真实退出黑盒。

**为什么现在做**：不是为了立刻重开 P0，而是为未来再次出现以下问题时，能直接定位证据：
- 某笔为什么被 down_wave 带出而不是 ROI
- 退出前后结构是否已恶化
- TSL 触发前的浮亏/持仓状态是什么
- 是否存在"本可继续持有但被保护机制提前带出"的样本簇

### 目标

在实际退出发生时，记录完整的决策上下文，覆盖：
1. 所有候选退出理由（不只是最终理由）
2. 每个候选是否触发、为何未触发
3. 退出时的浮动收益、持仓时长、结构状态

### 扩充字段（在 V1B-3 基础上新增）

| 新增字段 | 作用 |
|---------|------|
| `exit_candidate_list` | 退出时刻所有候选原因（ROI / TSL / down_wave / climax / divergence） |
| `triggered_reason` | 实际触发的退出原因 |
| `float_profit_at_exit` | 退出时浮动收益率 |
| `max_profit_seen` | 持仓期峰值浮动收益（已有字段，确认接入） |
| `trade_hold_bars` | 持仓时长（bar数） |
| `structure_deteriorated` | 退出前是否存在结构恶化信号（bool） |
| `tsl_dist_atr_mult` | TSL触发时：价格距触发线距离（ATR倍数）|
| `down_wave_concurrent_roi` | down_wave触发时：ROI候选是否同时存在（bool）|

### 诊断能力目标

补齐后，以下 5 个诊断问题变为可回答：

1. 某笔为什么被 down_wave 带出 → `exit_candidate_list` + `triggered_reason`
2. 退出前结构是否已恶化 → `structure_deteriorated`
3. 是否存在"可继续持有但被保护机制提前带出"样本簇 → `float_profit_at_exit` + `down_wave_concurrent_roi`
4. TSL 触发前的形态/收益/波动状态 → `tsl_dist_atr_mult` + `max_profit_seen` + `trade_hold_bars`
5. ROI 和 down_wave 是否存在竞争冲突 → `down_wave_concurrent_roi`

### 修改位置
- `custom_exit()` / `_custom_exit_impl()`：捕获候选队列和最终决策
- `confirm_trade_exit()`：记录被阻断的退出候选
- `event_emitter.py` / `strategy_event_bridge.py`：新增 `emit_exit_decision()` 方法
- SQLite：`exit_decision_events` 表（V1B-3已建，扩充字段）

### 验收标准
- 每次退出判断均有对应事件记录
- 能看出"原本想怎么退、最后怎么退、当时状态是什么"
- 能区分：结构退出 / ROI / TSL / down_wave fallback
- dry-run 运行 7 天后，能回答上述 5 个诊断问题

### 启用条件
- 默认 off，通过 `WY_EXIT_TRACE=1` 启用
- 独立 commit，不影响主退出链逻辑
- 先在回测模式验证字段完整性，再接 dry-run

---

### ✅ 实施完成（2026-03-19）

**代码变更**（`WyckoffV7_TrueMethod.py`）：

1. `custom_exit` 末尾：添加 `WY_EXIT_TRACE` 门控，调用 `_emit_exit_trace`
2. 新方法 `_emit_exit_trace`：每次 `custom_exit` 被调用时记录完整候选状态
3. `confirm_trade_exit`：扩展为三段捕获：
   - `C-4-v2 blocked`：`WY_AXIS_NO_DW` 阻断的 down_wave（`triggered=False`）
   - `confirm_trade_exit_tsl`：TSL 触发（含 max_profit_seen / hold_hours）
   - `confirm_trade_exit_vectorized`：所有向量化退出（down_wave / roi / climax / divergence 等）

**事件结构（存入 `metadata_json`，无需 schema 迁移）**：

```json
{
  "enter_tag": "wyckoff_axis_support",
  "float_profit": 0.025998,
  "max_profit_seen": 0.034447,
  "hold_hours": 39.0,
  "exit_candidates": ["wyckoff_down_wave"],
  "structure_deteriorated": false,
  "down_wave_concurrent_roi": false,
  "market_env": null,
  "current_rate": 87654.3
}
```

**回测验证（20260101-20260128）**：
- 总事件：489 条
- `custom_exit`：480 条（每 bar 评估记录）
- `confirm_trade_exit_vectorized`：9 条（实际向量化退出，含完整字段）
- 样本验证：`wyckoff_down_wave` 退出，profit=0.026，max_seen=0.034，hold=39h ✓

**5 个诊断问题 → 现已可回答**：
1. 某笔为何被 down_wave 带出 → `candidate_reason` + `exit_logic_module`
2. 退出前结构是否恶化 → `structure_deteriorated`（custom_exit 路径）
3. "可继续持有但被保护机制带出" → `float_profit` + `max_profit_seen` 对比
4. TSL 触发前状态 → `confirm_trade_exit_tsl` 的 `max_profit_seen` + `hold_hours`
5. ROI 与 down_wave 竞争 → `down_wave_concurrent_roi`

**运行命令**：
```bash
# 回测模式启用跟踪
docker compose run --rm -e WY_EXIT_TRACE=1 freqtrade backtesting \
  --config user_data/config_backtest.json \
  --strategy WyckoffV7_TrueMethod \
  --timerange 20230101-20260228 --cache none

# dry-run 模式启用跟踪（加入 docker-compose 或 .env）
WY_EXIT_TRACE=1
```

---## P5-V3：受控进化闭环（未来治理层）

### 状态
当前暂不进入主线开发。  
前提是：
- `Core Audit` 已完成
- `P7-min` 已补足必要运行时证据
- `P0` 已明确下一步退出主线
- `P5 V1-B / V1-C / V2` 的观测、诊断、proposal 数据基础已足够稳定

### 组成
- 闸门
- rollback 建议
- baseline 晋升制度

### 当前定位
属于未来治理层，不是当前收益主线。  
当前只保留方法与结构，不进入实现优先级。

---

## P5-WS：策略研究治理层（未来制度层）

### 定位
`P5-WS = 方法论与治理层 / 制度`

作用是：
- 约束研究流程
- 固化样本纪律
- 定义 `proposal / experiment / baseline / rollback` 制度
- 限制自动优化越权

### 当前状态
已有方法论框架，但当前不作为主线实现项。

### 当前原则
- 不抢在 `Core Audit / P7-min / P0` 前面
- 不替代当前收益主线
- 不在样本不足时推动自动化进化
- 不把治理框架误当成收益来源

### 进入条件
只有当下面条件基本满足时，再考虑推进：
- 当前核心交易链已审清
- 退出层优化主线已明确
- 关键运行时证据已补足
- `P5` 数据底座已足以支撑 proposal 质量控制

### 当前结论
`P5-WS` 保留，但降级为未来制度层。  
当前不进入执行主线。

---

## 从这里往后的主线顺序

### 当前顺序
1. `Core Audit — 收益导向版` ✅ 已完成
2. `P0 — 退出层微调` ✅ 已穷尽（FAIL）
3. **`C-1 / C-2 / C-3 — 双通道分流实验`** ← **当前主线**
4. `P5-V3`
5. `P5-WS`

### 当前最重要的一句话
**微调路线已穷尽。下一步是拆分两条赊錢子系统，让 wave_start 和 axis_support 用不同的退出哲学独立运行，用数据决定架构。**
---

## Fix Pack A — 当前优先修复包（先修真钱问题）

### Goal
先处理当前已经被报告明确证实、且**直接影响收益**的问题。  
不扩新 detector，不开新大方向，先把最亏钱、最容易少赚的地方修掉。

> 当前优先级不是"让系统更完整"。  
> 当前优先级是：**先减少明显亏损源，再减少明显利润回吐。**

---

## ✅ A-1：空头体系限流 / 关闭

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

## ✅ A-2：`wyckoff_down_wave` 误杀样本拆解

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

## ✅ A-3：`wyckoff_down_wave` 最小修正规则

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

## ✅ A-4：`wyckoff_climax_exit` 收紧

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

---

## ✅ Fix Pack A — 回测验证结果（2026-03-17）

| 指标 | 基线 (03-16) | Fix Pack A (03-17) | 变化 |
|------|-------------|-------------------|------|
| 交易笔数 | 378 | 388 | +10 |
| 均利润 | 1.53% | 1.55% | +0.02% |
| 总收益率 | 858.38% | 947.68% | **+89.3%** ✅ |
| 胜率 | 73.3% | 74.5% | +1.2% |
| 最大回撤 | 8.48% | 8.47% | ≈不变 |
| Sharpe | ~1.8 | 1.91 | ↑ |
| SQN | ~5.5 | 5.86 | ↑ |

A-1 (bear_gate) 是主要贡献：sos_reverse_short 从 9 笔亏损 → 0 笔。
A-4 rolling20→5 已回退（无效），climax 胜率 37.5%→33.3% 略差。

---

## Fix Pack A — 认知更新补丁（2026-03-17）

---

## ✅ A-1a（归档）：`axis_support × trailing_stop_loss`

### 验证结论（2026-03-17）

**5 笔 trailing_stop_loss 亏单复盘：**

| # | 入场时间 | 入场价 | 最大浮盈 | 类型 | 根因 |
|---|---------|-------|---------|------|------|
| 1 | 2023-03-06 | 22515 | +0.15% | A类 | 进场后立即急跌（SVB危机） |
| 2 | 2023-08-03 | 29255 | +3.40% | B类 | 有浮盈后14天缓慢回撤 |
| 3 | 2024-08-02 | 63394 | +0.33% | A类 | Black Monday急跌 |
| 4 | 2025-10-08 | 122785 | +0.78% | A类 | 48h闪崩 |
| 5 | 2026-02-04 | 73398 | +1.01% | A类 | 25h急崩 |

**已尝试方案对比（vs Fix Pack A 基准 947.68%）：**

| 方案 | 总收益 | 最大回撤 | trailing 笔数 | 结论 |
|------|-------|---------|--------------|------|
| Fix Pack A 基准 | **947.68%** | **8.47%** | 5笔 × -12% = -12,669 | 当前最优 |
| break-even +1% → -0.5% | 592.52% | 11.58% | 142笔 | ❌ 灾难性失败 |
| 统一 -9% stoploss | 932.46% | 10.92% | 13笔 × -9% = -28,460 | ❌ 反而更差 |

### 归档结论
- 4/5 笔最大浮盈从未超过 +2%（SVB 危机、Black Monday、闪崩）
- 任何统一 stoploss 收紧都会误伤 axis_support 正常持仓（P95 回撤 5.12%，P99 回撤 7.65%）
- 附带损伤显著大于修复收益
- **这 5 笔属于宏观黑天鹅，系统层面可接受成本，不再优化**
- **归档：已验证结束，非当前优先收益修复项**

### 明确不做
- `-8% / -9% / -10%` 一类统一 stoploss 调整
- 更复杂的统一 trailing 参数微调
- 为修复少量黑天鹅亏单而牺牲大量正常盈利单

---

## ✅ A-1b（归档）：`axis_support × down_wave` 出口降权

### 深度分析结论（2026-03-17，脚本：`scripts/_a1b_downwave_postmove_analysis.py`）

**down_wave 退出质量分析（244笔 axis_support × down_wave）：**

| 退出后观察窗口 | 正确退出（继续跌） | 误退（继续涨） | 中性 |
|--------------|------------------|--------------|------|
| 退出后 5h    | 127笔 / 52.0%    | 9笔 / 3.7%   | 108笔 |
| 退出后 10h   | 154笔 / 63.1%    | 8笔 / 3.3%   | 82笔 |
| 退出后 20h   | 166笔 / 68.0%    | 20笔 / 8.2%  | 58笔 |

**A-1b-1 实验结果**：wave_efficiency 阈值 0.001→0.003，backtest 完全相同（388笔、947.68%、8.47% DD），零效果。代码已回退。

### 归档结论
- **down_wave 退出质量本来就很好（63% 正确率），不是问题所在**
- "242笔 +0.8% vs 25笔 +7.92%"的差距来自：244笔中有 **82笔（33.6%）是负利润止亏退出**（均 -3.26%）
- 问题根源是**入场质量**，不是退出逻辑
- **归档：理论前提已被证伪，退出侧优化空间极小**

### 明确不做
- `axis_support` 专属 `down_wave` 延迟退出
- 继续提高 `wave_efficiency` 阈值
- 以"down_wave 提前退出"作为当前主线问题继续投入

---

## 🚀 A-2（当前唯一主线）：`axis_support` 入场质量过滤

### Goal
过滤掉那些 **入场后很快走弱、最终由 `down_wave` 正确止亏退出** 的低质量 `axis_support` 交易。

### 当前依据
- `axis_support × down_wave` 中存在约 **82 笔负利润退出**，平均亏损约 **-3.26%**
- 这些退出大多数是正确止亏——说明问题在入场侧，不在退出侧
- 过滤掉这批低质量进场，比任何退出层修改都有更直接的收益杠杆

---

## A-2a：拆解 82 笔负利润 `axis_support` 样本

### 目标
找出低质量 `axis_support` 在入场时的共同弱特征。

### 重点排查字段
- 入场时价格是否在 `ema_50` 下方或贴近下方
- `phase_e_active / phase_d_confirmed / accum_phase_a_complete` 状态
- `sos_bar_confirmed` 是否缺失
- `lps_bar_detected` 是否缺失
- 相对 `tr_upper` 的位置是否偏弱
- `volume / spread / effort-result` 是否偏弱
- 是否处于弱环境、假反弹、震荡失败段
- 入场前 3~5 根 K 线走势是否已有弱化迹象

### 验收标准
- 能总结出 82 笔负利润 axis_support 的主要坏样本模式（2~3 类）
- 能提出 1~2 个最值得先试的入场过滤候选条件

---

## A-2b：盈利 vs 亏损 `axis_support` 对照分析

### 目标
找出负利润 axis_support 和优质 axis_support 在入场时最可操作的区别。

### 对比维度
- 入场时价格相对 `ema_50`
- `phase_d_confirmed / phase_e_active / accum_phase_a_complete`
- 是否有 `sos_bar_confirmed`
- 是否有 `lps_bar_detected`
- 相对 `tr_upper` 的位置
- `volume / spread / effort-result`
- 入场前 3~5 根 K 线走势质量

### 验收标准
- 对照分析能落到具体字段 / 条件
- 能明确排序 1~2 个最值得先试的过滤条件
- 不停留在抽象叙述层

---

## A-2c：只做一个最小入场过滤实验

### 前置
仅在 A-2a / A-2b 完成后启动。

### ✅ A-2c-1：volume >= 0.5x（过滤极低量）— 完成

**实验内容**：在 `_axis_base` 末尾加 `(dataframe['volume'] >= dataframe['volume_sma_20'] * 0.5)`

**回测结果（2023-01-01 → 2026-02-28）**：
- 总交易：393 笔（vs 基准 388）
- 总收益：**965.76%**（vs 基准 947.68%，+18.08%pts ✅）
- 胜率：74.0%（vs 基准 74.5%）
- 最大回撤：**8.13%**（vs 基准 8.47%，降低 ✅）
- axis_support 入场：292 笔；× down_wave 248 笔，65.7% WR

**结论**：轻微正向。收益微升、回撤下降，但效果不显著。  
**代码状态**：已还原（实验后回退）

---

### ✅ A-2c-2：volume <= 2.0x（过滤极高量）— 完成（失败）

**实验内容**：在 `_axis_base` 末尾加 `(dataframe['volume'] <= dataframe['volume_sma_20'] * 2.0)`

**回测结果（2023-01-01 → 2026-02-28）**：
- 总交易：421 笔（vs 基准 388，+33 笔 ⚠️）
- 总收益：948.35%（vs 基准 947.68%，+0.67%pts 可忽略）
- 胜率：72.0%（vs 基准 74.5%，下降 ⚠️）
- 最大回撤：**10.40%**（vs 基准 8.47%，超 9% 阈值 ❌）
- axis_support × climax：28 笔，14.3% WR（质量极低）

**失败原因**：`max_open_trades=1` 交互效应，过滤高量 axis_support 信号后，
系统补进了更多低质量替代入场（climax 退出激增 31 笔），导致 DD 超限。

**代码状态**：已还原（实验后回退）

---

### 下一步方向（待决策）

**选项1**：采纳 A-2c-1（`volume >= 0.5x`）作为新基准（947.68% → 965.76%，轻微正向）  
**选项2**：A-2c-3 组合实验 — 同时加两个条件 `volume >= 0.5x AND volume <= 2.0x`（中间量区）  
**选项3**：放弃量能维度，转向其他特征（test_confidence 阈值、axis_strength_tier 分级、EMA 相对位置等）

### 原则
- 一次只试一个条件
- 不重做整套 quality 系统
- 不同时叠多个过滤器
- 不做复杂加权打分
- 优先可解释、可单独回测、可明确归因

### 验收标准
- 被过滤掉的交易中，坏样本比例明显高于好样本
- 总收益 / 风险结构方向改善
- 不因过滤而显著误杀主要盈利来源
- 能清楚解释"这次到底是哪个条件在起作用"

---

## A-2d：提高 test_confidence 阈值

### 背景
- **新基准（2026-03-17）**：A-2c-1 已永久写入策略
  - 393 笔 / **965.76%** / 74.0% WR / **8.13% DD**
  - `_axis_base` 末尾已有 `volume >= volume_sma_20 * 0.5`

### test_confidence 分布说明
axis_system.py 中的计算逻辑：
- **真实 Normal 测试**（量能萎缩 + 价差收窄 + 收盘在中位以上）：confidence = vol_shrink × 0.6 + spread_shrink × 0.4 → **0.5 ~ 1.0**
- **兜底 fallback**（接近支撑/阻力但未满足 Normal Test 条件）：固定 **0.5**
- **Absorption**：vol_confidence × 0.5 + close_pos_confidence × 0.5 → **0.5 ~ 1.0**
- 当前阈值 `> 0.4`：**所有信号全部放行**（无效过滤）

### 实验设计

#### ✅ A-2d-1：`test_confidence > 0.5` — 完成（失败）

**背景发现**：约 57% 的 axis_support 信号 confidence 恰好 = 0.5（fallback 路径）。  
`> 0.5` 将轴线支撑信号从 9543 → 4155（-57%），影响极大。

**回测结果（基于 A-2c-1 新基准）**：
- 总交易：409 笔（vs 基准 393）
- 总收益：**888.70%**（vs 基准 965.76%，**-77%pts** ❌）
- 胜率：72.9%（vs 74.0%）
- 最大回撤：**6.20%**（vs 8.13%，大幅降低 ✅）

**失败原因**：fallback confidence=0.5 信号虽然质量较低，但绝对量大、整体贡献净正收益。  
强制过滤后，`wave_start` 因 `max_open_trades=1` 占位效应增加（86→109），  
但 axis_support ROI 退出从 24→15，核心利润来源被切断。

**代码状态**：已还原新基准（A-2c-1）

#### ❌ A-2d-2：`test_confidence >= 0.55` — 跳过

A-2d-1 已证明 `> 0.5` 切割 57% 信号，A-2d-2 更激进（还会滤 0.50~0.54），  
方向确认无效，直接跳过。

### 结论

**`test_confidence` 不是有效过滤维度**——fallback=0.5 的信号整体是盈利的，不能简单通过阈值提升来过滤。  
如需改进 confidence 过滤，需要修改 axis_system.py 中的置信度计算逻辑（属于不同范畴）。

### 验收标准（同 A-2c）
- 总收益不低于新基准 965.76%，或 DD 显著降低
- 不因过滤而误杀主要盈利来源（wave_start 不受影响）

---

## ✅ A-4（冻结）：`wyckoff_climax` 暂停主线优化

- `rolling(20).min()` 版本保留（rolling5 已回退）
- `wyckoff_climax` 不进主线优化
- 原因：样本小，修法已验证无效，axis_support 入场质量过滤收益杠杆更高

---

## 执行顺序（2026-03-17 更新）

1. ✅ `A-1a`：归档（不再继续）
2. ✅ `A-1b`：归档（不再继续）
3. ✅ `A-2a/A-2b`：拆解 82 笔 + 对照分析完成
4. ✅ `A-2c`：两个量能实验完成（A-2c-1 采纳为新基准 965.76%）
5. ✅ `A-2d`：test_confidence 阈值实验（❌ 失败，confidence=0.5 fallback 信号整体盈利，不可过滤）
6. ✅ `A-2f`：axis_strength_tier 过滤实验（❌ 失败，见下）
7. `A-4`：继续冻结

---

## A-2f：axis_strength_tier 过滤（已结束）

**假设**：weak tier（test_count ≤ 1 或 support_strength < 0.4）是亏损的主要来源，过滤可提高 WR。

**预检方案**：临时将 enter_tag 分为 `wyckoff_axis_weak` / `wyckoff_axis_medium` / `wyckoff_axis_strong`，回测一次观察分布。

**预检结果（2026-03-17 v1 回测）**：

| Tier | 交易数 | Avg Profit | WR |
|------|--------|-----------|-----|
| `wyckoff_axis_weak` | **292** | 1.09% | **66.4%** |
| `wyckoff_axis_medium` | **0** | — | — |
| `wyckoff_axis_strong` | **0** | — | — |

**发现 bug（axis_system.py test_count 取值逻辑错误）**：  
原代码用 `distance < support_distance`（最小距离与自身比较，永远为 False），导致 `support_test_count` 始终为 0。  
→ 修复为追踪最小距离对应的 test_count（`min_sup_dist` 模式）。

**预检 v2（bug 修复后重跑，2026-03-17）**：

| Tier | 交易数 | Avg Profit | WR |
|------|--------|-----------|-----|
| `wyckoff_axis_weak` | **292** | 1.09% | **66.4%** |
| `wyckoff_axis_medium` | **0** | — | — |
| `wyckoff_axis_strong` | **0** | — | — |

**v1 = v2，结果完全相同。**

**根因（最终）**：  
- 在整个 3 年回测周期内，axis_lines 中支撑线的 `test_count` 几乎全部为 1（轴线刚被建立时触发入场信号）  
- 升到 medium 需要同一轴线被测试 2 次，升到 strong 需要 3 次 —— 到达该时间点时入场窗口已关闭或轴线已被打破  
- `support_strength >= 0.7` / `absorption` 条件同样极少满足  
- **tier 分级系统的设计假设（多次测试产生更可靠的支撑）与实际入场时间窗口不匹配，根本无法产生有效的 medium/strong 信号**
- axis_system.py 的 bug fix 已保留（逻辑正确），但不影响分级结论

**结论**：A-2f 完全不可行。axis_support 优化线 **正式关闭**。

---

## 完成标准（2026-03-17 更新）

1. ✅ A-1a / A-1b 已正式证伪并停止
2. ✅ A-2a/A-2b 已完成（量能 U 型分布发现）
3. ✅ A-2c-1 已采纳为新基准（965.76% / 8.13% DD）
4. ✅ A-2d：test_confidence 阈值无效（57% 信号为 fallback=0.5，整体盈利不可过滤）
5. ✅ A-2f：axis_strength_tier 过滤无效（全部信号均为 weak 级别，medium/strong = 0 笔）

**当前一句话主线定义：**  
**新基准 965.76%（A-2c-1）。axis_support 优化线全线证伪关闭。量能/置信度/分级三维度已穷尽。下一步：返回主任务线（P0 出场层 / Core Audit / P7-min）。**

---

## 后置事项（主线完成后再启动）

### 参数路由改造（Regime-Aware Parameter Routing）

- 目标：将关键参数从主策略中解耦到独立配置层，由主策略按行情状态动态取值（而非全周期固定同参）
- 启动时机：仅在当前主线任务（P0 出场层 / Core Audit / P7-min）完成后启动
- 最小落地范围（MVP）：
  - 入场阈值（entry thresholds）
  - 止损参数（stoploss）
  - ROI 结构（minimal_roi / routing）
  - RR 门槛（pnf_min_rr / 熊市附加门槛）
- 路由维度：`markup` / `markdown` / `trading_range`（保留 `transition` 回退默认）
- 原则：先做“配置外置 + 取值路由”，不改变现有交易哲学与信号定义
- 验收：分行情分桶回测结果可解释，且全周期收益/回撤不劣于当前基准

