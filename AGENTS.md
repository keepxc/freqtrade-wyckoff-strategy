# AGENTS.md — 项目状态与架构

> 约束与教训见 `CLAUDE.md`。

---

## 项目身份

这是一个基于 **Freqtrade** 的 **威科夫交易系统**，包含两大子系统：

| 子系统 | 定位 | 状态 |
|--------|------|:---:|
| **Wyckoff 策略** | 交易执行引擎 | 🟢 V8 封板，Dry Run 运行中 |
| **P5 智能层** | 统计、诊断、实验、治理底座（SQLite + JSONL） | 🟢 V1 已运行 |

---

## 开始前必读

1. `TASK_NEW/wyckoff_v8_redesign.md` — V8 设计主文档（最重要，含所有 TASK 记录和实验证伪结论）
2. `docs/P5-Data-Design.md` / `docs/P5-Event-Spec.md` — P5 数据底座设计

> `TASK_NEW/TASKS.md` 和 `TASK_NEW/TASKS_with_checklist.md` 为 V7 时代遗留文档，V8 阶段不再维护。

---

## 当前基线（封板，2026-06-05，TASK 24）

| 指标 | 数值 |
|------|------|
| 收益率 | **25,491%**（20220101-20260602） |
| 交易数 | 509（多头 471 / 空头 38） |
| 胜率 | 92.5% |
| 最大 DD | 0.32% |
| Sharpe | — |
| Sortino | — |
| Profit Factor | — |

> 策略文件：`user_data/strategies/WyckoffV8.py`

### 基线演进历史

| TASK | 收益率 | 空头数 | 说明 |
|------|--------|:------:|------|
| TASK 7 | 19,449% | 0 | V8 初版集成 |
| TASK S-1~S-7 | — | 3 | 做空完整集成（SOW/UTAD/BE） |
| TASK 13 | 19,615% | 5 | sm_v2 + fallback_sow，熊市路径激活 |
| TASK 14 | 19,615% | 5 | EMA200 persist 第四路径（修复死代码，数值不变） |
| TASK 15 | 19,615% | 5 | sm_v2 权重 20% 封板（无统计差异） |
| TASK 16 | 20,272% | 9 | LPSY 路径，捕获 BE 周期 78k→72k 零空单缺口 |
| TASK 17 | 21,553% | 15 | BE路径分层门控，结构化重置替换时间阈值清零 |
| TASK 20 | 21,934% | 20 | EMA200 冰线 LPSY（再派发确权），persist_days≥30 时间税 |
| TASK 22 | 25,200% | 36 | 再派发结构第六路径（横盘+高点递降+量萎缩+深度+动量过滤） |
| TASK 23 | 26,693% | 38 | FTI 冰线跌破第七路径（横盘15%+EMA200+MSOW日线门控，1H收盘位置确认） |
| TASK 24 | 25,491% | 38 | FTI冰层厚度确认税（persist_days≥20，清除persist=1/2的5笔侥幸交易）+ Code Review修正 |

### V7 历史基线（已归档）

| 版本 | 收益率 | 交易数 | 胜率 | DD | 说明 |
|------|--------|:------:|------|-----|------|
| V7.1 | 8,374% | — | — | — | 移除 EMA200 独裁者 |
| V7.1 + 实验A | 9,749% | 554 | 81.4% | 6.28% | wave_start 豁免 distributing |
| V7.1 + A+B（修复后） | 6,284% | 583 | 76.8% | 6.25% | ZigZag lookahead 修复后真实值 |

---

## V8 架构设计

### 核心设计原则

```
日线层 = 趋势背景：Phase D/E 过滤 + PNF 因果结构 + TR Low 止损参考
1H 层  = 交易执行：wave_start 入场（v8_ws_d/v8_ws_e）+ down_wave 退出（主导）
PNF 目标位 = 辅助参考：T1→移保本止损，T2→辅助退出
止损架构 = TR Low 统一（Phase D/E/unknown 全部使用，奥卡姆最优解）
```

### 多头入场路径

```
v8_ws_d / v8_ws_e：Phase D/E 中 1H wave_start（up）确认后入场
条件：Phase D/E + wave_displacement > 阈值 + bear_gate=False
```

### 空头入场路径（六路）

```
1. SOW / fallback_sow：超卖 + 20日新低
2. UTAD：上冲吸筹失败
3. BE（Buying Exhaustion）：买盘耗尽
4. LPSY（Last Point of Supply）：BE 周期失败反弹后 ChoCh（TASK 16）
5. ICE_LPSY（EMA200冰线LPSY）：再派发确权，persist_days≥30（TASK 20）
6. REDIST（再派发结构）：横盘+高点递降+量萎缩，距EMA200≥7%，7日动量下行（TASK 22）
门控：1~4：dist_cause_sufficient（含 BE 双轨）| daily_sm_v2 | daily_ema200_persist
      5：daily_ema200_persist + persist_days≥30 + EMA200触碰失败；豁免 pnf_valid_short
      6：daily_redist_ok（7项日线结构）；豁免 pnf_valid_short 和 EMA200触碰；止损 EMA200×1.02
      7：daily_fti_msow_active（横盘15%+EMA200熊市+persist≥20天+MSOW）+ 1H收盘位置≤40%；止损 ice×1.02
```

### 退出优先级

```
1. 结构失败：current_rate < TR Low → v8_structure_fail（紧急）
2. 1H down_wave 开始             → v8_down_wave（主要，95.3% 占比）
3. PNF T2 到达                   → v8_pnf_t2（辅助）
custom_stoploss: TR Low 结构止损（统一）+ T1 到达移保本
```

---

## TASK 完成状态

| TASK | 内容 | 状态 |
|------|------|:---:|
| TASK 1 | `sc_detector.py` SC/BC 识别 | ✅ |
| TASK 2 | `phase_detector.py` Phase A-E | ✅ |
| TASK 3 | `pnf_calculator.py` PNF 目标位 | ✅ |
| TASK 4 | 1H 执行层（entry_detector.py） | ✅ |
| TASK 5 | 退出层（down_wave 主退出 + PNF 辅助） | ✅ |
| TASK 6 | `WyckoffV8.py` 集成 | ✅ |
| TASK 7 | 回测验证 | ✅ 19,449% / 93.6% WR |
| TASK S-1~S-7 | 做空完整集成（SOW/UTAD/BE） | ✅ |
| TASK 11 | 全链路延迟法医审计（51.5h 确认税） | ✅ |
| TASK 12 | 空头三路径审计 + LPSY 决策 | ✅ |
| TASK 13 | 熊市空头路径激活（sm_v2 + fallback_sow） | ✅ |
| TASK 14 | EMA200 persist 第四路径 + 做空门控深度修复 | ✅ |
| TASK 15 | sm_v2 权重调参审计（20% 封板） | ✅ |
| TASK 16 | LPSY 路径实现（BE 周期做空缺口修复） | ✅ |
| TASK 17 | BE路径分层门控，结构化重置替换时间阈值清零 | ✅ |
| TASK 18 | DCS多头阻断（-58%）+ 趋势配仓（-45%）— 两项证伪 | ❌ |
| TASK 19 | BE条件②宽松化（4/5日递减）— 证伪，DD 6.8×恶化 | ❌ |
| TASK 19b | EMA200熔断补救 — 零效果，代码回退T17 | ❌ |
| TASK 20 | EMA200 冰线 LPSY 第五路径（再派发确权，阴跌型熊市盲区补全）| ✅ |
| TASK 20b | persist_days 15天 vs 30天敏感度测试 — 等价，30天维持 | ❌ |
| TASK 21 | 量价指纹早窗口绕过30天税 — 证伪（假信号指纹强于真信号）| ❌ |
| TASK 22 | 再派发结构第六路径（+3,266pp，DD持平0.32%） | ✅ |
| TASK 23 | FTI 冰线跌破第七路径（+1,493pp，DD持平0.32%，Sharpe 3.32） | ✅ |
| TASK 24 | FTI冰层厚度确认税 persist≥20（-1,181pp，清除persist=1/2侥幸交易，物理逻辑闭环） | ✅ |
| TASK 25 | P&F T2命中率审计 — T2精度99.2%（11/11 100%命中），动态Box Size证伪，不立项 | ❌ |
| TASK 26 | ETH零参数压力测试 — 3笔全亏（-22%），Phase检测器BTC专项，RS前置条件不满足 | ❌ |

---

## 常用命令

```bash
# V8 回测（全量）
docker compose run --rm "freqtrade" backtesting \
  --config user_data/config_backtest.json \
  --config user_data/config_v8_backtest.json \
  --strategy WyckoffV8 \
  --timerange 20220101-20260602 --cache none

# 数据更新（1H + 1D）
docker compose run --rm "freqtrade" download-data \
  --config user_data/config_backtest.json \
  --config user_data/config_v8_backtest.json \
  --timerange 20260101- --timeframe 1h 1d

# Dry Run 日志
docker compose logs -f --tail=100 "freqtrade"

# 确认税审计脚本
docker compose run --rm --entrypoint "" "freqtrade" \
  sh -c "cd /freqtrade && python user_data/scripts/audit_confirmation_tax.py"

# P5 全流程一键：回测 + 摄入 + 报表
python scripts/run_backtest_pipeline.py --timerange 20260101-20260114

# 快速查数据库行数
python -c "import sqlite3; conn=sqlite3.connect('user_data/market.db'); \
  [print(f'{t}: {conn.execute(f\"SELECT COUNT(*) FROM {t}\").fetchone()[0]}') \
  for t in ['experiment_runs','signals','trades','detector_events',\
  'regime_snapshots','exit_decision_events','weekly_reports']]"
```

---

## P5 智能层说明

- 实现文件：`ingest/ingest_jsonl.py`、`reports/build_weekly_report.py`
- 数据库：`user_data/market.db`（SQLite）
- 设计文档：`docs/P5-Data-Design.md` / `docs/P5-Event-Spec.md`
- 架构原则：交易执行保留在 Freqtrade，P5 只读取结构化事件，不直接修改策略逻辑
