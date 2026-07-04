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

## 当前基线（固定仓口径，2026-07-04，TASK 44 起）

| 指标 | 数值 |
|------|------|
| 总利润 | **218,370 USDT（21.84%）** |
| 交易数 | 783 |
| 胜率 | 90.9% |
| 平均利润/笔 | 2.80% |
| 最大 DD | 0.08% |

**基线口径（三要素，对比实验必须完全一致）**：
- `max_open_trades=1`（config_backtest.json，commit ff08dd9）
- `stake_amount=10000` 固定 + `dry_run_wallet=1,000,000` + `tradable_balance_ratio=1.0`（config_v8_backtest.json，TASK 44 起）
- `--timerange 20220101-20260704 --cache none`

> ⚠️ **口径变更史（三代基线互不可比）**：
> 1. **mot=2 复利口径**（~TASK 33b）：473,905%，每笔部署 40%
> 2. **mot=1 复利口径**（TASK 38~43）：TASK 38 = 9,235,899%（20260602）；**TASK 41 封板 = 9,355,121%**（+1.29%，35笔 wsdown_reversal_bar 100% WR）。复利终值被小本金早期复利 + 后期 1000 BTC cap 双重扭曲，序列敏感（插入/删除少数早期交易即扰动整条终值），已弃用作为采纳/证伪判据
> 3. **stake=10000 固定仓口径**（TASK 44 起，现行）：消除复利序列敏感性，per-trade edge 可直接对比。历史 pp delta（TASK 1~43）均在复利口径下测得，与现行口径数值不可比
>
> 策略文件：`user_data/strategies/WyckoffV8.py`（代码状态 = TASK 41 封板 + TASK 44/45 证伪回退）

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
| TASK 24 | 25,491% | 33 | FTI冰层厚度确认税（persist_days≥20，清除persist=1/2的5笔侥幸交易）+ Code Review修正 |
| TASK 27 | 66,188% | 108 | wave_breakdown 趋势跟随空头第八路径（EMA200 persist≥15 + ChoCh下行，零新参数，+160%） |
| TASK 30 | 76,522% | 126 | wave_breakdown persist_days 0→15 敏感度审计，去除无依据等待税（+10,334pp，+15.6%） |
| TASK 31 | 96,509% | 150 | daily_ema200_persist 激活窗口 rolling(15→5) 审计，提前10天激活（+19,987pp，+26.1%） |
| TASK 31b | 119,473% | 185 | daily_ema200_persist 激活窗口 rolling(5→1) 法医审计，P5 接管 V 形反弹保护（+22,964pp，+23.8%） |
| TASK 32 | 134,485% | 216 | wave_breakdown 入场窗口 rolling(1→8)，趋势跟随多点入场（+15,012pp，+12.6%） |
| TASK 33 | 451,917% | 257 | wave_breakdown 止损锚点结构化（8h 窗口 MAX，修复 ffill 反弹扫单缺陷）（+317,432pp，+236%，DD 2.06%→0.59%） |
| TASK 33b | **473,905%** | **269** | wave_breakdown custom_exit 双锚一致（_wb_structural_high 私有方法，修复 structure_fail 用 ar_high 提前触发）（+21,988pp，timerange 延长至 20260612） |
| TASK 34 | ❌ 证伪 | — | phase_b_min_days 20→7 + TR High 追踪：473,905%→372,863%（-101,042pp）；Phase D/E 提前激活阻断空头复利链；Phase B 早期突破 TR High 历史 100% UTAD；已回退 |
| — | mot 2→1 | — | config 对齐 dry run 满油门（ff08dd9）；基线口径切换为 mot=1，下方数值不再与上方 mot=2 时代可比 |
| TASK 38 | 9,235,899%（mot=1） | 274 | wave_breakdown 阴跌延续窗口 rolling(8→24)：阴跌空头 80.4% WR 审计支撑；多头笔数不变(468)未被挤；+1.48%，DD 0.66%→0.26% |
| TASK 41 | **9,355,121%**（mot=1，复利口径封板值） | 297 | wsdown 强反转K线提前退出（body≥0.8% + CIR≥0.87，Hyperopt 200 epochs）：35笔 reversal_bar 100% WR，+1.29%，DD 持平 0.26%。✅ OOS 复核通过（2026-07-04，futures 2021：10笔 100% WR / +5.41%，见 redesign 文档 P0-2b） |
| — | stake unlimited→10000 | — | TASK 44 起切固定仓口径：新基线 **218,370 USDT / 21.84% / 783笔 / 90.9% WR / DD 0.08%**（timerange 20220101-20260704）。复利口径终值弃用作为决策指标 |

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

### 空头入场路径（八路）

```
1. SOW / fallback_sow：超卖 + 20日新低
2. UTAD：上冲吸筹失败
3. BE（Buying Exhaustion）：买盘耗尽
4. LPSY（Last Point of Supply）：BE 周期失败反弹后 ChoCh（TASK 16）
5. ICE_LPSY（EMA200冰线LPSY）：再派发确权，persist_days≥30（TASK 20）
6. REDIST（再派发结构）：横盘+高点递降+量萎缩，距EMA200≥7%，7日动量下行（TASK 22）
7. FTI（Fall Through Ice）：冰线跌破，横盘15%+EMA200+MSOW日线门控（TASK 23）
8. wave_breakdown：EMA200 persist≥15天 + 1H ChoCh下行（趋势跟随，TASK 27）
门控：1~4：dist_cause_sufficient（含 BE 双轨）| daily_sm_v2 | daily_ema200_persist
      5：daily_ema200_persist + persist_days≥30 + EMA200触碰失败；豁免 pnf_valid_short
      6：daily_redist_ok（7项日线结构）；豁免 pnf_valid_short 和 EMA200触碰；止损 EMA200×1.02
      7：daily_fti_msow_active（横盘15%+EMA200熊市+persist≥20天+MSOW）+ 1H收盘位置≤40%；止损 ice×1.02
      8：daily_ema200_persist（单日收盘低于EMA200即激活，rolling=1）+ is_wave_start down（8h窗口内有效）；豁免 pnf/rr；止损 swing_high×1.01
```

### 退出优先级

```
1. 结构失败：current_rate < TR Low → v8_structure_fail（紧急）
2. 1H down_wave 开始             → v8_down_wave（主要，95.3% 占比）
3. PNF T2 到达                   → v8_pnf_t2（辅助）
空头 wsdown 专属：单根强反转K（body≥0.8% + CIR≥0.87 + 浮盈>0.5%）→ wsdown_reversal_bar（TASK 41）
custom_stoploss: TR Low 结构止损（统一）+ T1 到达移保本
⚠️ 注意：Phase E 多头 TR Low 距入场 30-70%，实际起约束的是 -12% 硬止损（TASK 29 审计）
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
| TASK 27 | wave_breakdown 趋势跟随空头第八路径（+40,697pp，579笔，108空头，DD 1.04%，零新参数） | ✅ |
| TASK 28 | 多空槽位交互验证（双槽同时占用0%、零碰撞、置换有利、利用率+7.8pp）+ 九大检验覆盖评估 | ✅ |
| TASK 29 | 九大检验三方向预研（RR门控/HH-HL序列/空头量价背离）— 三项全部证伪 | ❌ |
| TASK 30 | wave_breakdown persist_days 15→0 敏感度审计（+10,334pp） | ✅ |
| TASK 31/31b | daily_ema200_persist 激活窗口 rolling 15→5→1（+19,987pp / +22,964pp） | ✅ |
| TASK 32 | wave_breakdown 入场窗口 rolling(1→8)（+15,012pp） | ✅ |
| TASK 33/33b | wb 止损锚点结构化（8h 窗口 MAX）+ custom_exit 双锚一致（+317,432pp / +21,988pp） | ✅ |
| TASK 34 | phase_b_min_days 20→7 + TR High 追踪 — 证伪（-101,042pp），已回退 | ❌ |
| TASK 35 | lps_signal 置信度标注列（零侵入）+ bot_loop_start 守卫 | ✅ |
| TASK 36 | VSA 前向信号四路径（绕过 ZigZag）— 全线证伪，胜率收敛 50-53% | ❌ |
| TASK 37 | Phase C Spring 检测预研 — 预研未动代码 | 📋 |
| TASK 38 | wave_breakdown 阴跌延续窗口 rolling(8→24)（+1.48%，DD 腰斩） | ✅ |
| TASK 39 | confirm_trade_entry PNF 豁免扩展 — 证伪（PNF 检查是有益二次过滤），已回退 | ❌ |
| TASK 40/40b | wsdown 追踪止损 + 冷却门控 — 双证伪（与 rolling(24h) 结构冲突），方向关闭 | ❌ |
| TASK 41 | wsdown 强反转K线提前退出（CIR≥0.87）— 采纳封板（+1.29%） | ✅ |
| TASK 42~42d | rolling(24h) 量价结构替代四方案 — 全部证伪，rolling(24h) 为有效新鲜度约束 | ❌ |
| TASK 43 | ZigZag min_bars 3→2/1 — 证伪，wave_min_percent 才是主导过滤器 | ❌ |
| TASK 44 | ar_min_pct 10%→7% — 无改善（-0.15%，4笔差异，噪声级），不采纳，可重试 | ⭕ |
| TASK 45 | Phase B/C→D SOS 放量门控恢复 — 网格单调劣化，维持 v8.4 无门控 | ❌ |
| TASK 47 | TR 持续时间→PNF 目标位乘数（时间因子）预研，未动代码 | 📋 |
| TASK 48 | down_wave↔威科夫SOW 对应关系文档澄清，待补充 | 📋 |

---

## 常用命令

```bash
# V8 回测（全量）
docker compose run --rm "freqtrade" backtesting \
  --config user_data/config_backtest.json \
  --config user_data/config_v8_backtest.json \
  --strategy WyckoffV8 \
  --timerange 20220101-20260704 --cache none

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
