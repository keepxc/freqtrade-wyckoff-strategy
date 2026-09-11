# Dry-run / 采集状态记录 — 2026-09-11

> 本期覆盖 2026-09-07 ~ 09-11 的调整、测试与 dry-run 情况。
> 时间口径：**北京时间（CST）**。数据产物 jsonl 均带 `+08:00`。

---

## 一、本期调整（采集链路修复）

| # | 问题 | 根因 | 修复 |
|---|------|------|------|
| 1 | `fake_breakdown_trigger.sh` 每 6h 失败 | 缺执行权限（rw-rw-r--） | chmod +x |
| 2 | `oi_data_trigger.sh` 每日 08:30 失败 | 缺执行权限（rw-------） | chmod +x |
| 3 | `oi_data_trigger.sh` 修权限后**仍静默失败** | download_oi.py 用 requests 直连 fapi，**cron 环境无代理 env** → 超时被容错成"无新数据"→ exit 0 不告警 | sh 内显式注入 `HTTPS_PROXY/http_proxy` 四件套 |
| 4 | `bstock-whitelist-sync` cron error | Hermes no_agent 的 script 字段塞了整条 `cd && python` 命令（被当路径查找失败） | 建 wrapper `~/.hermes/scripts/bstock_whitelist_sync.sh` |
| 5 | `macd_m8 collector`（Hermes 侧）持续失败 | 同 #4 script 字段问题 + LLM 超时；真正执行者是**系统 crontab** | 删除 Hermes 僵尸 job（d75f15b36b26），保留系统 crontab |

**教训**：cron 健康不能只看 last_status/exit 0——必须核对产物时间戳与内容增长（#3 就是典型静默失败）。

## 二、网络层加固（proxy_guard 扩展）

1. **OKX 三域验证**：`telegram_proxy_guard.py` 的交易所验证从"币安现货+合约"扩展为**三域 200**（+OKX order book）——9 月 DonchianV1 order book 每天失败而 TG+币安正常的盲区根因
2. **GLOBAL 组自动修复**：watchdog 每次检查 `GLOBAL now == DIRECT` 则 PUT 回「一分机场」（8/30 + 9/7 两次事故模式）
3. **freqtrade 日志扫描**：watchdog 顺带扫活跃 bot 日志最近 12min 的 NetworkError/Unable to exit/Order failed（30min 冷却防刷屏）
4. **systemd unit 护栏**：`freqtrade-donchian.service` 恢复 env 代理清空设计（9/7 曾被误注入 `HTTP_PROXY=`）+ 文件内注释说明为什么不能改

## 三、新增内容

1. **MACD M8 采集器**（`scripts/macd_m8.py`，9/6 部署）：每 4h 采 12 币（MAJORS 9 + AI500 top3）的 15m/1h/4h/1d MACD 8 态，落盘 `macd_m8.jsonl`
   - 定位：动量状态机 = 择时精化器，**不是 alpha 源**；先记录后裁决
2. **《MACD M8 × V3/B3.0 结合使用指南》**：存档于 AITrader 知识库 `concepts/`（三条结合线 + 裁决清单）
3. **数据汇聚目录** `user_data/collect/`：symlink 汇聚全部采集产物（B3/M8/V3 CSV/OI），README 含调度/时区/状态速查

## 四、Dry-run 情况

### V3 ScoreHold（`tradesv3.ai500_v3_dryrun.sqlite`）
**首笔实盘交易**（此前 0 笔）：
| 项 | 值 |
|----|-----|
| 开仓 | 9/10 04:01 IOST/USDT:USDT @0.0019854，enter_tag=ai500_score_top3 |
| 平仓 | 9/10 06:20 @0.0018798，**stop_loss（-5.41%，-14.44U）**，持仓 2h18m |
| 入场合规性 | score=80.479 tradable=1 zombie=0 reason=ok —— **合规**（非僵尸） |
| 后续 | IOST 当晚崩 **-42%**（止损避免了 37pp 的下跌） |

**五重信号互验结论**（详见 `macd-m8-analysis` skill 案例库）：
| 信号源 | 入场时读数 | 性质 |
|--------|-----------|------|
| V3 score | 80.479 刚过门槛 | 合规但滞后 |
| B3 爆发线 | 已涨 **+91.58%** | 暴涨已完成 |
| MACD M8 | 🚀拉升（00:05 曾🚀🚀超级拉升） | 暴涨后确认态 |
| OI | 前 12h +29.1% → 入场前 03:15 从峰值 **-7.3% 急降** | 持仓拥挤 + 获利了结 |
| OI 后续 | 价格跌 + OI 升 | 空头建仓/踩踏前兆 |

**判定**：入场合规但**追高**（+91% 后入场），五重信息一致指向非理想入场点。**1 笔样本不改策略**，继续积累。

### 其他
- DonchianV1：BTC 多头（7/16 @65130）仍持仓，浮盈约 +20%；出场线 = 前 10 日最低 **76,264**（未触及，无卖出信号）
- BStocks：15 笔持仓，白名单 67 支（9/7 sync 新增 4 支）
- V3：IOST 平仓后空仓等信号

## 五、采集任务全景（13 个在跑）

| 类型 | 数量 | 清单 |
|------|:---:|------|
| 系统 crontab | 7 | b300 signals / b300 breakout / macd_m8 / OI 归档(6h) / oi_data_trigger(08:30) / fake_breakdown(6h) / download_recent_data(每小时) |
| Bot 内置 | 3 | V3: score_history_v3 / zombie_check / netflow_multiwin（30s 写） |
| Hermes cron | 1 | 十老板交易者监控(09:00) |
| 已停（产物保留） | 4 | yss-signal / aggTrades / V1-V2 三件套 / AI500 live |

时区注意：`download_recent_data.log`/`fake_breakdown_detector.log` 打 UTC(-8h)；`oi_hist/` 文件名 UTC 日切；jsonl 全部 +08:00。

## 六、待验证（裁决清单，见 `macd-m8-analysis` skill）

| # | 假设 | 数据源 | 目标样本 |
|---|------|--------|---------|
| A | V3 阴线出场时 4h zone=🔥 的后续漂移为正 → 加平仓豁免 | V3 × macd_m8.jsonl | 两周 |
| B | B3 入场 +4h power≥2 门的前向收益差 | b300_signals × macd_m8.jsonl | 两周 |
| C | 💎黄金一击后 24h/48h 正期望 → 爆发线回踩触发 | b300_breakout × macd_m8.jsonl | 两周 |
| D | OI 高位回落期入场 vs 上升期收益差 | V3 × oi_*.csv.gz | **5-10 笔 V3 交易** |
| D2 | 入场时价格 N 小时涨幅超阈值（+50%）负期望 | V3 × 币安 klines | 同上 |

**当前纪律：不改策略，继续跑数据积累。**
