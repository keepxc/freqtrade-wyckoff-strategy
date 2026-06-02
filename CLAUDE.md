# CLAUDE.md

## 项目身份

这是一个基于 **Freqtrade** 的 **威科夫交易系统**，包含两大子系统：

| 子系统 | 定位 | 状态 |
|--------|------|:---:|
| **Wyckoff 策略** | 交易执行引擎（V8 Dry Run 运行中） | 🟢 V8 封板，Dry Run |
| **P5 智能层** | 统计、诊断、实验、治理底座（SQLite + JSONL） | 🟢 V1 已运行 |

---

## ⚠️ 核心工作纪律：改代码必须有依据

**在动任何策略代码之前，必须能回答这个问题：**

> "我有什么理论或数据依据证明这个改动会改善系统，而不是基于直觉或'感觉合理'？"

合法依据的示例：
- **回测数据**：对照组 vs 实验组，样本 >30 笔，数值可对比
- **Wyckoff 物理理论**：引用威科夫原典结构（SC/AR/SOS/BC/UTAD 等定义的行为规律）
- **法医审计结论**：从 TASK 11/12 等历史审计中提炼的量化结论
- **已知缺陷的根因**：能定位到具体 bar/逻辑路径，而不是"感觉信号太少"

**不合法的依据**（这些理由不能触发代码改动）：
- "这样更灵活" / "更能捕捉机会"
- "理论上应该更好"
- "其他系统也这么做"
- 观察到某几天没开仓就认为系统有问题

> 教训：15m/30m 切换提案 — 直觉上"更灵活"，但 Wyckoff 物理要求结构确认需要 1H+ 粒度，假突破率在低时框架指数级上升，无数据支撑，直接否决。

---

## ⚠️ 写代码必读：未来函数（Lookahead Bias）检查清单

**每次修改策略代码，必须检查以下几点，否则回测结果不可信：**

1. **ZigZag / 波段检测**：转折点确认时会回溯标记过去的 bar，必须 shift 延迟
   - 修复方式：`is_wave_start.shift(min_bars)`

2. **日线数据 merge 到 1H**：当天日线 close/phase/PNF 在收盘后才可知
   - 修复方式：merge 前 `df_1d['date'] += pd.Timedelta(days=1)`

3. **`populate_indicators` 中任何用整段 dataframe 计算的指标**：
   - 滚动窗口（rolling）必须确认只用过去数据
   - 禁止用 `df.iloc[i+n]`（向后看）

4. **验证方法**：回测胜率 > 90% 或 DD < 1% 时，**必须怀疑存在未来函数**，立即排查

> 教训：V7 因 ZigZag 回溯导致基线虚高 11,313% → 真实 6,284%（虚高 80%）

---

## 开始前必读

1. `TASK_NEW/wyckoff_v8_redesign.md` — V8 设计主文档（最重要，含所有 TASK 记录和实验证伪结论）
2. `AGENTS.md` — P5 架构说明（P5 智能层仍在运行）
3. `docs/P5-Data-Design.md` / `docs/P5-Event-Spec.md` — P5 数据底座设计

> `TASK_NEW/TASKS.md` 和 `TASK_NEW/TASKS_with_checklist.md` 为 V7 时代遗留文档，V8 阶段不再维护。

---

## 当前基线

### V7 历史基线（已归档）

| 版本 | 收益率 | 交易数 | 胜率 | DD | 说明 |
|------|--------|:------:|------|-----|------|
| V7.1 | 8,374% | — | — | — | 移除 EMA200 独裁者 |
| V7.1 + 实验A | 9,749% | 554 | 81.4% | 6.28% | wave_start 豁免 distributing |
| V7.1 + A+B（修复后） | 6,284% | 583 | 76.8% | 6.25% | ZigZag lookahead 修复后真实值 |

### V8 当前基线（封板，2026-06-02，TASK 16 完成）

| 指标 | 数值 |
|------|------|
| 收益率 | **20,272%**（20220101-20260602） |
| 交易数 | 491（多头 482 / 空头 9） |
| 胜率 | 93.5% |
| 最大 DD | 0.32% |
| Sharpe | 3.13 |
| Sortino | 36.12 |
| Profit Factor | 157.4 |

> 回测范围：`20220101-20260602`（随数据更新延伸）
> 策略文件：`user_data/strategies/WyckoffV8.py`

#### V8 基线演进历史
| TASK | 收益率 | 空头数 | 说明 |
|------|--------|:------:|------|
| TASK 13 | 19,615% | 5 | sm_v2 + fallback_sow，熊市路径激活 |
| TASK 14 | 19,615% | 5 | EMA200 persist 第四路径，数值不变（修复死代码） |
| TASK 15 | 19,615% | 5 | sm_v2 权重调至 20%（与 13 持平，接受） |
| TASK 16 | 20,272% | 9 | LPSY 路径，捕获 BE 周期 78k→72k 零空单缺口 |

---

## V8 当前状态（2026-06-02，封板版本 TASK 16）

**V8 架构已完成并封板**：`WyckoffV8.py` 从零实现，Dry Run 运行中。

### 核心设计原则
```
日线层 = 趋势背景：Phase D/E 过滤 + PNF 因果结构 + TR Low 止损参考
1H 层  = 交易执行：wave_start 入场（v8_ws_d/v8_ws_e）+ down_wave 退出（主导）
PNF 目标位 = 辅助参考：T1→移保本止损，T2→辅助退出
止损架构 = TR Low 统一（Phase D/E/unknown 全部使用，奥卡姆最优解）
```

### 退出优先级
```
1. 结构失败：current_rate < TR Low → v8_structure_fail（紧急）
2. 1H down_wave 开始             → v8_down_wave（主要，95.3% 占比）
3. PNF T2 到达                   → v8_pnf_t2（辅助）
custom_stoploss: TR Low 结构止损（统一）+ T1到达移保本
```

### TASK 完成状态
| TASK | 内容 | 状态 |
|------|------|:---:|
| TASK 1 | `sc_detector.py` SC/BC 识别 | ✅ |
| TASK 2 | `phase_detector.py` Phase A-E | ✅ |
| TASK 3 | `pnf_calculator.py` PNF 目标位 | ✅ |
| TASK 4 | 1H 执行层（entry_detector.py） | ✅ |
| TASK 5 | 退出层（down_wave主退出 + PNF辅助） | ✅ |
| TASK 6 | `WyckoffV8.py` 集成 | ✅ |
| TASK 7 | 回测验证 | ✅ 19,449% / 93.6% WR |
| TASK S-1~S-7 | 做空完整集成（SOW/UTAD/BE） | ✅ |
| TASK 11 | 全链路延迟法医审计（51.5h确认税） | ✅ |
| TASK 12 | 空头三路径审计 + LPSY 封板决策 | ✅ |
| TASK 13 | 熊市空头路径激活（sm_v2 + fallback_sow）| ✅ |
| TASK 14 | EMA200 persist 第四路径 + 做空门控深度修复 | ✅ |
| TASK 15 | sm_v2 权重调参审计（20% 封板） | ✅ |
| TASK 16 | LPSY 路径实现（BE 周期做空缺口修复）| ✅ |

### 关键审计结论（已固化）
| 结论 | 数据支撑 |
|------|------|
| Phase E 止损必须用 TR Low（不能用 ATR/Swing Low） | 复利悖论：0.175pp/笔差距 → 109,750× 复利差 |
| down_wave 退出门槛（0.8% displacement）不需要调整 | 449笔 93.5% WR，6.5% 误触发为正常摩擦 |
| 全链路入场延迟 51.5h，确认税 3.6% | 389个转折点统计，Alpha 物理真实性确认 |
| 短窗口回测（<30天）无法触发交易 | PNF 门控需要长期历史预热，非系统缺陷 |
| LPSY 路径实现（TASK 16）捕获 BE 周期空头缺口 | 根因：fallback_sow 要求 20日新低，BE顶后反弹物理不可能触发，4笔新增空头，+657% |
| 确认税 Phase D vs E 无显著差异，min_bars 不需调整 | Phase D 中位 52h，Phase E 中位 45h；长尾延迟中负 price_gap 占多数，延迟≠错过，607笔统计 |
| 1H 时间框架是 Wyckoff 最低有效粒度，不下沉 15m/30m | 15m 假突破率指数级上升（无数据支撑的提案已否决）；Wyckoff 结构确认需要 1H+ |

---

## 关键约束 & 已证伪的实验

| 约束/结论 | 详情 |
|-----------|------|
| EMA200 移除 = +138% 利润 | V7→V7.1 唯一变量 |
| wave_start 豁免 distributing = +1375% | 实验 A，已固化，8374%→9749% |
| composite_intent ctx/harmony/sot/quality 对 wave_start 拦截率为 0 | 可删，不带来任何增益 |
| 1H 退出端已达物理极限 | 7 轮实验无一超越 V7.1 |
| 和谐保险必须分信号 | 统一和谐 -21%，分信号 -69% |
| Phase E 分层抑制无效 | Freqtrade 向量化 exit_signal 优先级冲突 |
| RP 参数路由已永久关闭 | `WY_REGIME_ROUTING=0`，信号层已覆盖市场自适应 |
| P3 波段 E/R 默认关闭 | `WY_P3_SWING_ER=off` |
| 仓位管理默认关闭 | `WY_POSITION_MODE=off` |
| sm_v2 + fallback_sow = BC独立空头路径 | TASK 13，$120k→$70k 信号真空修复，空头 3→5，收益 19,449%→19,615% |
| fallback_sow 20日新低条件不放宽 | 放宽会引入假 SOW，对满仓复利链造成非线性回撤，奥卡姆封板 |
| sm_v2 权重 20% 封板（TASK 15） | 5%/10%/15%/20%/25% 五档回测，各档与基线持平，无统计显著差异，保持原值 |
| LPSY 豁免 RR（对齐 BE 路径） | BE 路径已无 RR 要求，LPSY 同为 BE 周期衍生路径，统一处理；样本少不宜用 RR 过滤 |
| dist_cause_sufficient 包含 BE 激活（双轨并行） | daily_event_detector.py L577-579：`be_dcs_active=True` 当 BE 触发；DCS = dcs_active OR be_dcs_active |
| 当前 Dry Run DCS=False 是正确行为 | BTC 已连续 5 日收于 EMA200 上方，be_dcs_active 按设计重置，分发论题物理失效 |

---

## 常用命令

```bash
# V8 回测（全量）
docker compose run --rm "freqtrade" backtesting --config user_data/config_backtest.json --config user_data/config_v8_backtest.json --strategy WyckoffV8 --timerange 20220101-20260602 --cache none

# 数据更新（1H + 1D）
docker compose run --rm "freqtrade" download-data --config user_data/config_backtest.json --config user_data/config_v8_backtest.json --timerange 20260101- --timeframe 1h 1d

# P5 全流程一键：回测 + 摄入 + 报表
python scripts/run_backtest_pipeline.py --timerange 20260101-20260114

# Dry Run 日志
docker compose logs -f --tail=100 "freqtrade"

# 快速查数据库行数
python -c "import sqlite3; conn=sqlite3.connect('user_data/market.db'); [print(f'{t}: {conn.execute(f\"SELECT COUNT(*) FROM {t}\").fetchone()[0]}') for t in ['experiment_runs','signals','trades','detector_events','regime_snapshots','exit_decision_events','weekly_reports']]"
```

---

## 修改策略代码的流程

1. 先读 `TASK_NEW/wyckoff_v8_redesign.md` 确认改动是否与 V8 设计一致
2. 改代码 → Docker 回测验证（`--timerange 20220101-20260525 --cache none`）→ 对比基线
3. 记录实验结果到 `wyckoff_v8_redesign.md`
4. 如果结果劣化，回退并记录证伪结论
5. commit 后 `git push origin main`
