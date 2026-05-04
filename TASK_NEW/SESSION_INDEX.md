# 会话索引 — 快速定位历史讨论

格式：每条记录用 `---` 分隔，最新在前。
涉及 freqtrade / OKX / Telegram / 策略状态 / 数据诊断 时，先来这里查。

---
## 2026-05-02（完整）

**主题**: Trade #9 退出确认 / P5数据底座修复(9笔vs1笔差异) / 历史数据Backfill / 模型切换 / 信号生态诊断准备

**关键结论**:
- Trade #9 (BTC/USDT, 04-19@74778.9) 于5月1日 pnf_target_1 退出，+4.15%，PnF目标位命中
- 全部9笔已平仓：胜率71.4%（5/7有完整数据），累计 +7.99%
- Exit分布：pnf_target_1（5笔）、axis_support_failed_exit（1笔 -6.10%）、sow_fast_exit（1笔 -1.77%）
- P5 trades表缺数据根因：当前run（run_20260502_133248）无 trades.jsonl 文件生成，StrategyEventBridge 的 emit_trade_open/close 链路待排查
- P5数据底座修复完成：backfill 64个 experiment_runs + 14条 trade 事件入库
- 修复 ingest 脚本孤儿 trade_close 处理（INSERT 占位行 → UPDATE 匹配）
- 最终 P5/p5.db：66 runs, 13 trades（7平仓+6孤儿/open）, 6269 signals, 364 detectors, 25355 regimes, 567 exit_decisions
- signals 表字段名为 candidate_enter_tag（非 enter_tag），信号生态诊断 prompt 需适配
- 周报生成已切换到读 P5/p5.db

**操作记录**:
- 批量 backfill 历史 run 和 trade 数据到 P5 SQLite
- 修复 ingest 脚本 schema 兼容（start_ts vs created_at 字段名）
- 准备信号生态 AI 诊断（signal_ecosystem_diagnostic_prompt.md，5维度），样本量仅7笔 → 仅可观察，禁止调参提案
- 模型即时切换：deepseek V4 pro → V4 flash（/model 命令）

**待关注**:
- 当前 run trades.jsonl 未生成，StrategyEventBridge emit_trade_close 链路需排查
- AI诊断 V1-C-2 门槛：7笔已平仓仅可观察，禁止调参提案
- enter_tag 字段名不一致（signals 表 candidate_enter_tag），诊断 prompt 需适配

**相关文件**: P5/p5.db | user_data/ingest/ingest_jsonl.py | user_data/ai/prompts/signal_ecosystem_diagnostic_prompt.md | weekly_report_20260502.md

---
## 2026-04-30（完整）

**主题**: Dry_run 运行状态确认 / OKX 行情资金费率查询 / 情绪指标可行性评估

**关键结论**:
- DryRun 正常：PID 3923，运行15h（自04-29 21:12启动），策略 WyckoffV7_TrueMethod，仅监控 BTC 不开新仓
- 持仓1笔：trade_9（04-19@74778.9，持仓11天），浮盈 +1.24% → 半月后重估 +1.96%
- 已平仓8笔：胜率75%，总损益 +10.06%，均值 +1.26%/笔
- Exit分布：pnf_target_1(4笔 +2.95%)、roi(2笔 +3.06%)、sow_fast_exit(1笔 -1.77%)、axis_support_failed_exit(1笔 -6.1%)
- 仅 BTC/USDT:USDT 在 whitelist，策略专注监控退出不判断新入场
- BTC $76,246.7，资金费率 -0.0043%（近8期 ±0.01% 内震荡，无极端偏向），OI 31,132 BTC（~$23.7亿）
- OKX 不提供情绪指数公开API（无多空比/taker成交量/大户持仓）— 唯一可合成代理指标为 oi-anomaly-detection
- PerformanceWarning（DataFrame碎片化）不影响逻辑，无 regimes.jsonl（威科夫相位记录未启用）

**操作记录**:
- 确认 dry_run 进程状态（service + PID）
- 查询 OKX 行情、资金费率、持仓量
- 盘点已有 OKX skill（okx-market-data, oi-anomaly-detection, wyckoffv7-live-diagnostics）
- 讨论建议：新建 Composite Sentiment Score skill（整合资金费率趋势 + OI方向 + 订单簿买卖比）

**待关注**:
- trade_9 浮盈 +1.96%，止损 65,874.7（-11.9%），用户未明确减仓计划
- 是否要新建 Composite Sentiment Score skill 未定

**相关文件**: TASK_NEW/TASKS_with_checklist.md | user_data/tradesv3.dryrun.sqlite

---
## 2026-04-27（完整）

**主题**: Gbrain 模块 3 + 模块 4 完成

**模块 4（Skill 扩充）**:
- `freqtrade-strategy-diagnostics/references/decision_tree.md` — 7 支诊断分叉，强制预检流程，常见陷阱速查表
- `wyckoff-trading-knowledge/references/wyckoff_graph.md` — 节点定义（signal/regime/module）+ 跨层关系边 + 快速定位表

**模块 3（知识图谱层完善）**:
- 新建 entities/regime: markup, markdown, transition, spring, sos, sow
- 新建 entities/signal: sos_bar, sow_bar, down_wave_exit, sow_fast_exit, wave_start, phase_d_signal
- 新建 entities/market: btc, eth
- 补全: axis_support, trading_range, gbrain, trade_9, okx
- 新建 concepts: entry-optimization, exit-optimization, rm3-position-manager, p5-intelligence, regime-routing, wyckoff-three-laws, book-analysis
- 图谱统计: originals:7 | concepts:7 | entities:28

**关键结论**:
- graph_updated: false 是认知误差（实际 cron 正常，index.json 时间戳已更新）
- gbrain 全部 5 个模块已完成

---
## 2026-04-26（完整）

**主题**: Gbrain 进化 — 威科夫记忆系统 2.0

**关键结论**:
- Signal Detector 完成：后台并行捕获想法 + typed 实体，零 LLM 消耗
- Typed 关系提取：8 类实体（trade/exchange/regime/signal/market/file/command/axis），正则匹配优先级去重
- 知识图谱层：建立 originals/ / concepts/ / entities/{type}/ / ideas/ 四层结构
- 夜间优化 cron 挂载：每天 03:00 AM 自动修复引用、合并重复、归档旧 session、更新 graph index

**实现文件**:
- `~/.hermes/skills/signal-detector/SKILL.md` — 技能定义
- `~/.hermes/skills/signal-detector/references/capture.py` — 核心捕获逻辑
- `~/.hermes/skills/signal-detector/references/extractors/entity_patterns.py` — typed 实体提取
- `~/.hermes/skills/signal-detector/references/extractors/idea_detector.py` — 想法检测
- `~/.hermes/skills/nightly-maintenance/SKILL.md` — 夜间维护定义
- `~/.hermes/skills/nightly-maintenance/scripts/nightly_maintenance.py` — 维护脚本
- `TASK_NEW/GBRAIN_EVOLUTION_SPEC.md` — 设计文档

**待完成**:
- 模块 3（知识图谱层完善）— 依附 1+2，目录结构已建
- 模块 4（Skill 扩充）— freqtrade-strategy-diagnostics 扩充 pitfalls + decision tree

**Cron Jobs**:
- `nightly-graph-maintenance` (ID: fc3046163f4f) — 每天 03:00 AM

---
## 2026-04-25（完整）

**主题**: OKX连接诊断 / Telegram断连根因 / Freqtrade dry_run状态 / 数据无需重载 / SESSION_INDEX机制建立

**关键结论**:
- Telegram 断连根因：4/24 21:24 Hermes Gateway 崩溃后重启循环（约2小时离线），非每日限额
- fetch_l2_order_book 偶发 NetworkError：ccxt 4.5.40 不走 proxies 配置，但 aiohttp 走 aiohttp_proxy 正常，不影响策略
- Phase D 信号为 0：市场条件问题（trading_range 无 SOS Bar），非 bug，数据不需要重载
- 每日 00:00 cron + session_search 组合不可靠（cron 在独立会话，无法搜到正在进行的对话）
- SESSION_INDEX.md 正确用法：用户说「索引一下」触发，而非定时自动

**操作记录**:
- 诊断 OKX API：curl 正常（BTC 77463），ccxt 直连超时（不走代理）
- 检查 Freqtrade dry_run：持仓 1 笔（trade_9，04-19@74778，持6天），累计 +12.02%
- 检查 market.db：signals 4/21 后停止（trading_range 无入场信号，符合预期）
- 创建每日 00:00 cronjob（后发现不work，改为用户触发模式）
- 更新 AGENTS.md：加入「每次启动必读 SESSION_INDEX」规则
- 手动触发 cron 测试，确认 session_search 在 cron 独立会话中无法搜到当前对话

**待关注**:
- trade_9 持仓已达 6 天，关注是否有 exit 信号
- OKX orderbook 偶发 NetworkError 是否影响 exit 逻辑
- 新机制：用户说「索引一下」→ agent 主动写 SESSION_INDEX.md

**相关文件**: TASK_NEW/TASKS_with_checklist.md | TASK_NEW/SESSION_INDEX.md | user_data/market.db | user_data/tradesv3.dryrun.sqlite
