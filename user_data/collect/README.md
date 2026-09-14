# 数据采集产物索引（汇聚目录）

> 本目录 symlink 指向各采集器真实产物（物理文件不动，避免破坏运行中策略/脚本路径）。
> 排查数据先看这里。时间口径：jsonl 均带 +08:00（北京时间）；日志类注明 UTC 的需 -8h。

## 采集任务全景（2026-09-09 确认）

### 系统 crontab（crontab -l）
| 任务 | 调度(CST) | 产物 | 状态 |
|------|----------|------|------|
| b300_collector --once | 5 0,4,8,12,16,20 | `b300_snapshots.jsonl` + `b300_signals.jsonl` | ✅ **9/13 12:14 全恢复**——CF 指纹修复（`scripts/vergex_client.py`：完整浏览器头 + urllib）；signals/门槛判定/信号触发全部恢复 |
| b300_collector --breakout | 20 0（每日；9/12 由 00:05 错峰） | `b300_breakout.jsonl` | ✅ signals 恢复 → 分类链恢复（待 00:20 下轮验证） |
| ~~macd_m8 --once~~ | ~~5 0,4,8,12,16,20~~ | `macd_m8.jsonl` | 🛑 **已停采**（标记 9/11、9/12 落地；crontab 注释可逆）——转离线重建；jsonl 保留为**重建器历史对照物**，不删。采集器已硬化（帧锚 endTime），将来手动 `--once` 不会再错位 |
| ai500_oi_archiver | 0 */6 | `oi_hist/oi_YYYYMMDD.csv.gz`（UTC 日切） | ✅ |
| oi_data_trigger | 30 8（每日） | `oi_feather/` + market_data.db | ✅（9/9 修代理静默失败） |
| fake_breakdown | 0 */6 | 日志（market.db 检测） | ✅（9/7 修权限） |
| download_recent_data | 2 *（每小时） | freqtrade data 目录 | ✅ |
| P5 ingest | 0 23 | market.db | ✅ |

### Bot 内置（V3 策略循环写，路径写死在策略代码——勿动）

✅ **V3 运行中**（9/13 11:56 因 vergex 断链暂停止 → 根因修复后 12:11 恢复；见 `TASK_NEW/DRYRUN_STATUS_20260911.md` §一 #8/#9）

| 产物 | 内容 |
|------|------|
| `ai500_score_history_v3.csv` | V3 score 榜历史 |
| `ai500_zombie_check.csv` | V3 僵尸过滤检查 |
| `ai500_netflow_multiwin.csv` | V3 netflow 五档（vergex 源，间歇失败属正常抖动） |

### Hermes cron（cronjob list）
| 任务 | 调度 | 备注 |
|------|------|------|
| 十老板数据监控 trader_daily_scraper | 0 9 | X/Twitter 交易者抓取 |
| AI500 大单周回测 | 0 21 周日 | 周回测非采集 |
| telegram-proxy-guard | 5m | 代理守护非采集 |

### 已停（产物保留不删）
yss-signal（8/24 停）、aggTrades（**断点 09-06 17:52**，**9/13 拍板不恢复**，见下）、V1/V2 采集三件套、AI500 live

> **aggTrades 结案（9/13 拍板，9/14 订正断点）**：停用原因当时无人记录，9/11 才补记状态。
> 9/14 在 bot 上实测：`agg_trades/` 里 `agg_20260831`~`agg_20260906` 七个文件齐全，
> `last_id.txt` 与 `agg_20260906.csv` 同在 **9/6 17:52** 停更 → **断点 = 2026-09-06 17:52**，
> 9/11 记的"9/6 停"是对的。（9/13 曾误判成"只剩 agg_20260831.csv → 断点 8/31"，已推翻，勿引用。）
> 复盘的两条可能（消费方 V1/V2 三件套 9/5 随 V3 上线退役、V3 不读该目录；或无守护常驻进程静默死亡）
> 中，新时间线更贴合前者，但确切原因已不可考。**不恢复**——BTCUSDT 合约 aggTrades 在币安官方归档有逐日全量可补，
> 属采集冗余（教训 7）。补档地址与列映射见 `scripts/ai500_agg_collector.py` 头部；
> 注意归档当日文件次日才发布，只能补历史、喂不了实时。

## 排查速查
- 看数据是否新鲜：`ls -lt user_data/collect/`（symlink 显示真实 mtime）
- 看日志尾部：`tail -5 logs/{b300_collector,macd_m8}.log` 等
- 时区坑：`download_recent_data.log`/`fake_breakdown_detector.log` 打 UTC（-8h）；`oi_hist/` 文件名 UTC 日切；jsonl 全部 +08:00
