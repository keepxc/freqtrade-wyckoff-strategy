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

## 如何解除归档

```bash
# 1) 服务：先只启需要的那一个，别一次性全启
systemctl --user enable --now freqtrade-mse.service
# 2) crontab：把要恢复的行去掉 `# [2026-10-18 归档] … 原行：` 前缀后重新 crontab 安装
crontab -l > /tmp/cron.txt && vi /tmp/cron.txt && crontab /tmp/cron.txt && crontab -l
# 3) 跑之前先确认策略真有仓：查 trades DB 的 MAX(open_date)，别看文件在不在（AGENTS.md 的老规矩）
```

## 相关

- 方法论（策略无关，仍有效）：`CLAUDE.md` / `RESEARCH_FRAMEWORK.md` / `TASK_NEW/quant_system_tasks.md`
- 归档项目在 harness 里**没有登记**（不设门禁、不设 hook）—— 后续要启用时，先按 `harness-pipeline`
  的三步登记（manifest + deploy + hooks），别让它以「无门禁的活代码」形态复活。
