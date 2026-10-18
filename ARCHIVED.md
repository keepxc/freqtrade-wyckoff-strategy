# 归档记录 — freqtrade（2026-10-18 深层归档）

> 本文件是**操作记录**，不是运行说明。仓库早已归档（2026-10-04，全部 bot 停），
> 本文件记录 2026-10-18 把**最后残留的活接线**也停掉的那次操作。
> 用户决定：**后续如果要启用，再说。**

## 这次停掉了什么

| 对象 | 动作 | 备注 |
|---|---|---|
| `freqtrade-mse.service`（user unit） | `stop` + `disable` | Market State Engine，`Restart=always`，靠 linger 一直活着 |
| `ai500-local-collector.service`（user unit） | `stop` + `disable` | AI500 score/netflow 采集，V3 停用后仅作数据源 |
| 系统 crontab 4 条活接线 | 注释归档（行首加 `# [2026-10-18 归档] … 原行：`） | 数据下载 / fake_breakdown / oi_data_trigger / ai500_oi_archiver+oi_hist_guard |

- 其余 freqtrade 单元（donchian / ai500 / ai500-v3 / bstocks / dryrun / agg-collector）
  与三个 timer 早就是 `disabled`，未动。
- **why 现在才停**：那两条 service 是 `Restart=always` + 用户级 linger，
  所以「bot 全停」之后它们仍在跑（进程 `market_state_engine.py` / `ai500_local_collector.py`）——
  属于「以为停了其实没停」这一类，归档要连它们一起处理。
- **为什么可以直接停**：产物只喂 freqtrade 自己的 dry-run 策略（`user_data/market.db`、
  `user_data/ai500_local/`），本机活链路（币安跟单 / ETF 定投 / vault 日报）都不读它们。
  `oi_hist_guard.py` 与 `ai500_oi_archiver.py` 在同一条 cron 行里，一起注释 ⇒ 不会留下「源停了、守卫还在报警」的假告警。

## 验证过的事实（归档后）

```bash
systemctl --user is-active freqtrade-mse.service ai500-local-collector.service   # → inactive inactive
systemctl --user is-enabled freqtrade-mse.service ai500-local-collector.service  # → disabled disabled
crontab -l | grep -vE '^\s*#|^\s*$'                                              # → 只剩 SHELL / BASH_ENV
pgrep -af 'market_state_engine|ai500_local_collector|freqtrade/scripts'           # → 无
```

## 磁盘

- `~/freqtrade` ≈ **6.4G**，其中 `user_data/` ≈ **4.8G**（trades DB / 历史 K 线 / OI 归档）。
  这次**没有删也没有搬**任何数据 —— 归档 = 停接线，不是清盘。
  要腾空间得单独决定（哪些目录是「可再生缓存」、哪些是研究资产，见各 skill）。

## 监视侧的最后一条接线（2026-10-18 补）

归档当天漏了一条**下游守卫**：`scripts/collect_watchdog.py`（断采告警，每小时，Hermes cron job `watchdog-cron`）
仍在监控被停掉的采集端，于是从 13:05 起每小时报「score_ai500 / netflow_ai500 断采」并 `exit 1`。

- `TARGETS` 里最后三条（`score_ai500` / `netflow_ai500` / `oi_hist`）已移除，注释写明恢复方法；
  移空后 `watchdog-cron` 已 **pause**（Hermes cron，job `dc5a1e0a07fa`）。
- `user_data/WATCHDOG_ALERT.txt` 已清除（脚本在「无 red」时会自己删）。
- 规则：**归档一个采集端，要顺着「产物 → 守卫 → 通知」三层一起停**，否则留下的是
  「源停了、守卫还在响」的永久噪声（本文件开头那批 service 停掉了，这一层是同类遗漏）。
- 数据本身没动：`user_data/ai500_local/`（score 到 10-17 完整 + 10-18 半天，netflow_multiwin ≈ 511MB）
  与 `user_data/ai500_oi_hist/` 全部保留 —— 只剩**离线**消费（`research/ai500_h1/scripts/*` 用历史数据）。
- **2026-10-18 用户决定：不续采**（采集端保持 stop+disable）。理由：无活体消费者；数据作为**研究资产**留存，
  后续要复盘 AI500 方向（V3 是终止、非证伪）用这份已冻结样本即可，需要的是「当时那段」，不是持续录像。
  用法见 skill `ai500-shadow-analysis` 的「冻结数据集」一节。

## 如何解除归档

```bash
# 1) 服务：先只启需要的那一个，别一次性全启
systemctl --user enable --now freqtrade-mse.service
# 2) crontab：把要恢复的行去掉 `# [2026-10-18 归档] … 原行：` 前缀后重新 crontab 安装
crontab -l > /tmp/cron.txt && vi /tmp/cron.txt && crontab /tmp/cron.txt && crontab -l
# 3) 跑之前先确认策略真有仓：查 trades DB 的 MAX(open_date)，别看文件在不在（AGENTS.md 的老规矩）
# 4) 断采守卫：先把要恢复的目标加回 scripts/collect_watchdog.py 的 TARGETS，
#    再 resume 那个 Hermes cron job（watchdog-cron, dc5a1e0a07fa）—— 顺序反了会先被空守卫骗一轮
```

## 相关

- 方法论（策略无关，仍有效）：`CLAUDE.md` / `RESEARCH_FRAMEWORK.md` / `TASK_NEW/quant_system_tasks.md`
- 归档项目在 harness 里**没有登记**（不设门禁、不设 hook）—— 后续要启用时，先按 `harness-pipeline`
  的三步登记（manifest + deploy + hooks），别让它以「无门禁的活代码」形态复活。
