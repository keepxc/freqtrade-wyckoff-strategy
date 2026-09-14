# AI500 数据采集 —— bot 机器操作说明

**目的**：为 [AI500_OI_GATE_PREREG.md](./AI500_OI_GATE_PREREG.md) 的确认窗口积累数据。
预注册的四个决策点（D1–D4）**还没定**，但**采集不依赖它们**——先把数据攒上，
决策定了之后直接用。反过来，如果等决策定完再开始采集，就白等一段时间。

**核心约束**：OI 数据事后无法重建，采集断档 = 该时段永久丢失。
本项目 2026-08-18~08-25 就是这么丢的，直接导致可用样本从 16 天缩到 9 天。

---

## 1. 拉代码

```bash
cd /home/gyan/freqtrade
git pull origin main
```

新增两个文件：
- `scripts/ai500_oi_archiver.py` —— 币安官方 OI 归档器（新，需要挂 cron）
- `scripts/ai500_history_extract.py` —— 历史抽取器（已有，本次加了 OI 列）

### ⚠️ 拉之前先看这条：代理端口默认值变了

本次同时提交了一批代理配置补丁，把 5 个脚本的硬编码 `127.0.0.1:7890`
改成了 `os.environ.get("LOCAL_PROXY", "http://127.0.0.1:10809")`。
其中 **`scripts/ai500_agg_collector.py` 的 `DATA_DIR` 指向 `/home/gyan/...`，
说明它也在 bot 机器上跑**。

如果 bot 机器的代理不是 10809，`git pull` 之后这个采集器会连不上。
两种处理，选一个：

```bash
# 方案 A：bot 机器用别的端口（比如还是 7890）—— 用环境变量顶掉默认值
export LOCAL_PROXY=http://127.0.0.1:7890          # 写进 ~/.bashrc 或 cron 行里

# 方案 B：bot 机器直连币安，不需要代理 —— 设成空会让 requests 走直连
#   注意：这 5 个脚本的 PROXY 是 dict 形式，空字符串不等于不用代理，
#   最省事的是设成一个明确无效值会报错，所以直连的话改用方案 A 指向真实代理，
#   或者告诉我，我把这 5 个脚本也改成「LOCAL_PROXY 未设则不走代理」的写法
#   （新的 ai500_oi_archiver.py 已经是这个写法）
```

~~**先确认一下 bot 机器上 aggTrades 采集器现在的代理是什么**~~
> ✅ **已结案（2026-09-13）：这个待办作废——aggTrades 采集器 2026-09-06 17:52 停采
> （9/14 在 bot 上实测坐实），已拍板不恢复。**
> 它的代理默认值是什么不再重要。原因复盘、不恢复的依据（币安官方归档可逐日补档）
> 与补档口径见 `TASK_NEW/quant_system_tasks.md` 文末 09-13 条，以及
> `scripts/ai500_agg_collector.py` 头部的 ⛔ 说明。**不要顺手把它重启起来。**
>
> 顺带订正上面第 27-28 行的推断：`DATA_DIR` 是个 Windows 绝对路径，当时据此断定
> "它也在 bot 机器上跑"。这条**没被独立验证过**，别再当事实引用。

```bash
# 若仍要确认它的历史运行痕迹（只为读懂旧数据，不为重启）：
ls -lt user_data/agg_trades/ | head   # 末个 agg_*.csv 的日期 = 真实断点
ps aux | grep ai500_agg               # 若居然还活着 → 按拍板停掉
```

新增的 `ai500_oi_archiver.py` 用的是「`LOCAL_PROXY` 没设就直连」的写法，
不受这个问题影响。

---

## 2. 挂上 OI 归档器（**这一步最要紧**）

### 为什么需要它

现在的 OI 只有一个来源：策略 `bot_loop_start` 顺手写进
`user_data/ai500_netflow_oi_history.csv` 的快照。这个来源有两个问题：

1. bot 一停，OI 就断（本会话内已发生过）
2. 它记的是策略当时看到的值，含 `vergex_fallback`，不是币安官方口径

币安 `/futures/data/openInterestHist` 提供 5 分钟粒度的官方 OI，
**但只保留 30 天**。所以它能救火（补最近 30 天的洞），
也只能救火——超过 30 天没归档就再也拿不回来了。

### 先手动跑一次，确认能通

```bash
cd /home/gyan/freqtrade
python3 scripts/ai500_oi_archiver.py --dry-run     # 先看能不能拿到标的清单
python3 scripts/ai500_oi_archiver.py               # 正式跑，约 3–5 分钟
```

预期输出结尾类似：

```
[ok] 20260902: +150,000 行 (本次抓到 150,000)
[ok] 20260903: +100,000 行 (本次抓到 100,000)
[done] 净新增 250,000 行 | 失败标的 0 | 耗时 3.2 min
```

**如果 `失败标的` 超过几十个**，多半是限流，把脚本里的 `SLEEP = 0.2` 调到 `0.5` 再跑。
脚本幂等，重复跑不会重复计数。

### 挂 cron

```bash
crontab -e
```

加一行（**每 6 小时**，不是每天）：

```cron
0 */6 * * * cd /home/gyan/freqtrade && /usr/bin/python3 scripts/ai500_oi_archiver.py >> user_data/ai500_oi_hist/archiver.log 2>&1
```

> 为什么是 6 小时不是 24 小时：每次拉最近 500 个点 ≈ **41 小时**窗口。
> 6 小时一跑，即使机器宕了一天半，下次跑还能把空洞补回来。
> 24 小时一跑只剩 17 小时余量，机器一宕就开始丢数据。

挂完验证 cron 生效：

```bash
crontab -l | grep oi_archiver
sleep 21600 && tail -20 user_data/ai500_oi_hist/archiver.log   # 或者六小时后再看
```

---

## 3. 确认 bot 自己的 netflow 快照没断

这个是策略进程在写，不需要额外挂什么，但要**定期确认它还在长**：

```bash
ls -la user_data/ai500_netflow_oi_history.csv
tail -2 user_data/ai500_netflow_oi_history.csv | cut -c1-120
```

最后一行的时间戳应该在几分钟以内。如果落后超过一小时，说明 bot 挂了或
`bot_loop_start` 抛异常了，需要查 freqtrade 日志。

**建议加个每日巡检**（可选，但机器已宕过两次）：

```cron
30 8 * * * cd /home/gyan/freqtrade && [ $(( $(date +\%s) - $(stat -c \%Y user_data/ai500_netflow_oi_history.csv) )) -gt 3600 ] && echo "netflow 采集疑似停止" | mail -s "AI500 采集告警" you@example.com
```

（没配 mail 的话，改成写进一个 `ALERT.txt`，你自己看。）

---

## 4. 定期把数据传回来

每隔一两周跑一次抽取，把产物传回本地分析机：

```bash
cd /home/gyan/freqtrade
python3 scripts/ai500_history_extract.py
# 产物: user_data/ai500_netflow_hist_compact.csv.gz （约 13 MB / 16 天）
```

先看**时间戳口径诊断**那一段输出。正常应该是：

```
  ✓ 全程单一口径，可直接换算。
```

如果出现「⚠ 出现多于一种时区口径」，**先别传，把那段输出发回来**——
仓库历史上改过时间写法（UTC ↔ 北京时间），跨变更点混用会整段错位 8 小时。

OI 归档器的产物按天分文件，传最近的几天即可：

```bash
ls -la user_data/ai500_oi_hist/
```

---

## 5. ⚠️ 确认窗口期间的禁止事项

预注册冻结之后（D1–D4 定了才冻结），在确认窗口结束前：

- **不修改** `user_data/strategies/AI500V1MacdExitDryRun.py` 的**入场逻辑**
  （`ENTRY_MEDIAN` / `OI_MIN` / BTC 闸门 / `RANK_WINDOW_SEC`）
  —— 改了实盘与重建口径就分叉，整个实验作废
- **不修改出场逻辑** —— 会影响 H1′ 的附带报告
- **不跑**任何确认窗口内、涉及 OI 闸门收益的数字
  —— 中途偷看会污染「只跑一次」的纪律

要改别的（日志、告警、非 AI500 的策略）没问题。

---

## 6. 现在的状态与下一步

| 项 | 状态 |
|---|---|
| netflow + OI 快照（bot 自写） | ✅ 在跑，但断过两次 |
| 币安官方 OI 归档 | ⬜ **本次新增，待你挂上** |
| K 线（Vision 日包） | ✅ 本地脚本，可断点续传 |
| 预注册 D1–D4 | ⬜ 待拍板 |
| 确认窗口 | ⬜ 待冻结后起算，预计需 42 天 |

挂完 cron 之后，把第 2 步手动跑那次的输出发回来，我确认归档器工作正常
（尤其是 `失败标的` 的数量和覆盖的日期范围）。

D1–D4 的四个决策点在预注册文档 §0.5，不急，采集先跑着。
