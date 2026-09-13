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
| 6 | `b300_collector --breakout` 连续两天无产物（9/11、9/12） | **00:05 三任务撞车**（b300 --once + b300 --breakout + macd_m8 同时挤 7890 代理）→ breakout 拉币安 24hr 全市场大响应被挤掉（SSL handshake timeout） | 调度错峰：breakout `5 0` → **`20 0`**（只改 crontab，脚本未动）。验证点：9/13 00:20 首跑 |
| 7 | M8 采集器**时钟/窗口错位**（首帧实证）：ts 写 18:29:45，但 15m 数据窗口已是 18:30 后 | `fetch_klines` 每次调用各取 `time.time()`，脚本逐币逐周期耗时跨过 K 线边界 → ts 与实际数据窗口差 1 根（EMA seeding 起点不同 → 15m power ±1；XRP 4→3、LINK 3→4） | ① **硬化**（3 处）：`scan()` 每帧锚定 `anchor_ms` 并作为 `endTime` 下发、逐周期复用同一锚 → ts 与窗口同源自洽（调用方不传锚时行为不变）；② **停采**（9/11 起，crontab 注释可逆；转离线重建，见 §八-5） |
| 8 | **vergex.trade 全部端点被 Cloudflare 拦**（9/12 起）→ B3 采集停 2 天、V3 的 score/net_flow 采集全断 | vergex 上线 WAF，触发条件是**请求指纹**：旧脚本 `UA = "Mozilla/5.0"`（短 UA）→ 403；且 **requests 即使带完整头仍 403**（客户端指纹差异），必须走 urllib | **根因修复（9/13 12:14 验收）**：新增统一客户端 `scripts/vergex_client.py`（完整浏览器头 + urllib）+ V3 策略加 `VERGEX_HEADERS`/`_vergex_get_json` → **B3 全恢复**（含 signals/门槛判定/信号触发）+ **V3 全恢复**。期间曾临时迁 Open API（`vergex_api.py`）作跳板，现保留为备用通道。详见 `vergex-trading-data` skill §32 |
| 9 | **V3 dry-run 停用 → 当日恢复**（停 11:56 / 恢复 12:11 CST） | score 榜 HTTP 端点 9/12 14:45 起被 CF 拦 → `_entry_state` 抓取失败走 `keeping previous` 沿用旧值 → 产出污染样本 | 停 15 分钟后根因修复（见 #8）→ 12:11 重启，12:12:53 首轮 score 抓取正常（LSK 78.346 / ZEC 77.278 / USELESS 61.505），三个 CSV 恢复写入、无新 403 |
| 10 | **爆发线 `--breakout` 整轮崩溃（exit 1，零产物）**（9/13 12:28 发现，修复后 12:30 验收） | 币安涨幅榜含中文名 meme **「牛来」** → symbol 未 URL 编码 → `urlopen` 抛 `UnicodeEncodeError`（position 60-61）；且 `breakout_scan` 只捕 `RuntimeError` → **单币异常崩掉整轮** | ① `vergex_client.signals()` 对 symbol 加 `urllib.parse.quote`；② `breakout_scan` 非 ASCII 符号直接记 `no_market(non_ascii_symbol)` + `except Exception`（per-coin 容错）；③ `scan_once` 同样放宽为 `except Exception`。**验收**：20 币完整跑完（watch=10 / no_market=10），`牛来` 正确归类 |

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
| 系统 crontab | 7 | b300 signals(`5 0,4,8,12,16,20`) / b300 breakout(**`20 0`**，9/12 由 00:05 错峰) / ~~macd_m8~~（9/11 起停采，见 §八-5） / OI 归档(6h) / oi_data_trigger(08:30) / fake_breakdown(6h) / download_recent_data(每小时) |
| Bot 内置 | 3 | V3: score_history_v3 / zombie_check / netflow_multiwin（30s 写） |
| Hermes cron | 1 | 十老板交易者监控(09:00) |
| 已停（产物保留） | 4 | yss-signal / aggTrades / V1-V2 三件套 / AI500 live |

时区注意：`download_recent_data.log`/`fake_breakdown_detector.log` 打 UTC(-8h)；`oi_hist/` 文件名 UTC 日切；jsonl 全部 +08:00。

## 六、待验证（裁决清单，见 `macd-m8-analysis` skill）

| # | 假设 | 数据源 | 目标样本 | 状态 |
|---|------|--------|---------|------|
| A | V3 阴线出场时 4h zone=🔥 的后续漂移为正 → 加平仓豁免 | V3 × macd_m8.jsonl | 两周 | 待积累 |
| B | B3 入场 +4h power≥2 门的前向收益差 | b300_signals × macd_m8.jsonl | 两周 | 待积累 |
| C | 💎黄金一击后 24h/48h 正期望 → 爆发线回踩触发 | b300_breakout × macd_m8.jsonl | 两周 | **❌ 放弃（9/11，见 §八）** |
| D | OI 高位回落期入场 vs 上升期收益差 | V3 × oi_*.csv.gz | **5-10 笔 V3 交易** | 待积累 |
| D2 | 入场时价格 N 小时涨幅超阈值（+50%）负期望 | V3 × 币安 klines | 同上 | 待积累（已知常态见 §八 Q1） |

**当前纪律：策略只做"数据真不真"层面的修复（§七），入场时机/拥挤度层面不动，继续积累。**

---

## 七、V3 僵尸过滤器口径修复（9/11，已本机验证）

**问题**：`_check_tradable` stage ② 的旧判据比的是**派生百分比**——把"用 `start_time` 所在那根 **1h** K 开盘价复算的累计涨幅"和"接口报的累计涨幅"相减，差 >3pp 判僵尸。

**根因**（两层叠加）：
1. 口径差：接口 `startPrice` 记的是信号触发**那一刻**的价，1h 开盘最多早 59 分钟；能上 score 榜的标的按构造正在快拉，这 59 分钟本身就是几个点。
2. 放大效应：`diff_pp ≈ 参考价相对误差 × (1 + 累计涨幅)`——同样的口径差，标的涨得越多被放得越大，**过滤器系统性掐掉涨幅最大的那批**。

**证据**（`scripts/audit_zombie_ref_price.py`，5095 行 zombie_check）：
- 83.4% 被拒 / 15.7% tradable；`recompute_mismatch` 占全部行的 82.9%
- 该组中**实时价过期 >1pp 的占 0.0%**（中位 0.043pp）→ 拒的不是"死数据"
- `corr(diff_pp, 参考价差) = 0.955` → 拒的就是参考价口径差本身
- 改用 1m 开盘做参考：偏差中位 1.03→0.64pp、p90 10.45→1.94pp

**改法**：stage ② 改成**直接比价格**——`|接口 startPrice / start_time 那根 1m K 开盘 - 1| > 3%` 判僵尸（`ZOMBIE_REF_PP`）。旧的 1h 口径 `start_open/recomputed_pct/diff_pp` **降级为纯观测**，仍算仍落 CSV 但不再参与判定（`ZOMBIE_DIFF_PP` 保留只为审计对比）。stage ①（币安最新价 vs 接口 price >1pp）不变。

**逐 episode 改判**（16 个）：ARB 3.81→0.09、BULLA 19.38→0.58、FF 10.28→2.30、SOPH 10.62→1.14 —— 四个确认误杀；**ZEC 10.26→10.70，换 1m 口径后偏差仍在 → ZEC 是真阳性**，过滤器该拦的还是拦住了。

**影响**：按评估次数加权 tradable 率 **16.0% → 70.8%**（约 4.4 倍入场流量）。

**本机端到端验证**（9/11，实时接口 + 1m K，非回测）：
```
ZEC     tradable=0 zombie=1 reason=ref_price_mismatch  接口起点612.52 1m基准553.32 差10.699pp  [旧观测 diff_pp=19.85]
FF      tradable=1 zombie=0 reason=ok                  接口起点0.14447 1m基准0.14122 差 2.301pp  [旧观测 diff_pp=11.25]
USELESS tradable=1 zombie=0 reason=ok                  接口起点0.08844 1m基准0.08985 差 1.569pp  [旧观测 diff_pp=6.10]
```
与离线预测逐个吻合；CSV 表头变更触发 `_append_csv` 自动轮转（旧文件改名 `.bak`），验证用的临时数据目录已删除。

> ⚠️ 上线后 zombie_check 的 tradable 率会从 ~16% 跳到 ~70%，**这是预期内的**，不要当故障。同时意味着 9/11 之前与之后的入场样本**不可混池统计**。

## 八、本期关闭的方向

### 1. 涨幅榜 top3 + 短持有 —— ❌ 证伪
预注册全文与结果见 `AI500_PRICEBOARD_PREREG.md`（§7 数据 / §8 结论）。528 标的 × 一年 1h K 自建横截面榜，18 格全负、9 主格中 6 格 CI 上界 <0、17/18 格中位收益恰等于止损价 -5.100%。**该榜是 K 线的纯函数 → 完全离线可重建，一年全样本一次跑完，不需要等采集。**

### 2. 裁决 C —— ❌ 放弃立项
两条独立理由，任一条都足够：
- **底座本身负期望**：b300 的底座就是"币安 24h 涨幅榜 top20 + qv≥500万"，同属可重建类。`scripts/audit_c_base_gainers.py`：n=43460/腿、522 标的、363 天，24h/48h × 有无止损**四格全负且 CI 上界全部 <0**。
- **事件数死穴**：110 行 b300_breakout 里 `candidate` 级 **0** 个、`candidate_minor` **3** 个且全是 ZEC、只落在 4 天 6 个时间戳上。再等两周也凑不出样本。

### 3. 信号年龄门 —— 不加
`scripts/audit_score_vs_priceboard.py` Q2：41 episode / 11 天，MDE 5.01~10.63%，且方向与提案相反（<24h 的 fwd24 均值 **-7.88%**，>7d **+3.76%**），>7d 那格还被榜内幸存者偏差污染。另外僵尸修复刚把入场流量放大 4.4 倍，此刻加门会与该变更混淆。**等 ≥5-10 笔真实交易后连同裁决 D/D2 一起冻结判据再测。**

### 4. score 榜 ≠ 涨幅榜（Q1，重要）
score 榜标的在全市场涨幅横截面上的中位名次 1h 193/528、4h 138/528、24h 70/526，落 top3 仅 5.6%/7.0%/12.7% → **涨幅榜的证伪不传导到 V3**，V3 不下线。
但同一份数据暴露 V3 的真实毛病：接口累计涨幅中位 **+55.1%**（>100% 占 31.1%），而同时刻真实 trailing-24h 涨幅中位仅 **+3.9%**、29.1% 在跌 —— **V3 买的是"已经走完、正在衰减"的行情**。9/10 IOST 那笔 +91.58% 只排第 65 分位，不是异常值是常态。这正是裁决 D/D2 要回答的问题，需要真实交易样本。

### 5. MACD M8 采集器 —— 停采（产物保留），已产出离线重建器
M8 的输入**只有**币安 K 线（15m/1h/4h/1d），K 线永久可查 → 完全可离线重建，停采零损失。对比 score 榜：只挂当前 1-4 个标的、无历史接口，断一小时永久缺一小时，**必须继续采**。

**重建器** `scripts/macd_m8_offline.py`。关键性质是**精确重放**而非近似重建：采集器在时刻 T 取 `limit=500` 的最近 500 根、用 `closeTime < now` 剔未收盘；重建改用 `endTime=T`，币安返回 openTime ≤ T 的最后 500 根，再用同一句 `closeTime < T` 剔除——**两边 K 线逐根相同**，`ema` 的 seeding 起点也相同（不是"预热够长误差可忽略"，是同一窗口算同一个数）。计算逻辑直接 `import macd_m8` 的 `ema/macd_state/classify`，不复制实现（教训 6）。

三模式：`--selfcheck`（机制等价性）/ `--validate`（对齐留存 jsonl）/ `--at --pool-from`（重建任意历史时刻，池子 = MAJORS + 从 score 榜历史还原的 top3）。

本机验证：selfcheck BTC/ETH/SOL × 4 周期 **12/12 逐根一致**；重建 9/10 04:00 的 IOST = 🚀拉升，与 §四记录的入场读数一致；`--pool-from` 还原出当时 top3 = ZEC/IOST/USELESS，与同期 zombie_check 标的吻合。

> **停采前置条件（bot 机器）**：先跑 `--validate scripts/macd_m8.jsonl --source spot`，`powers` 一致率须 **100%** 才停采。M8 是确定性函数，同一窗口同一公式不存在"大致相同"，不得按比例四舍五入放行。`macd_m8.jsonl` 不要删——它是校验重建结果的唯一对照物。
> `macd-m8-analysis` skill 若继续使用，必须改指向离线重建。

**对齐校验结果（9/11 于 bot 执行，337 条）**：

| 口径 | powers 一致率 | state 一致率 |
|---|---|---|
| cron 帧（335 条） | **100%** | **100%** |
| 全量（337 条） | 99.41% | 100% |

**2 条差异已阳性归因到采集器，非重建器偏差**——XRP 15m power 与 LINK 15m power 各差 1。决定性证据：按落盘 `ts`（18:29:45）重放得 XRP 3 / LINK 4，与采集值不符；**改按 18:31:00 重放，逐值复现采集值 XRP 4 / LINK 3** ✅。这是可证伪预测被验证，不是事后找解释。

**根因：采集器的时钟/窗口错位。** `macd_m8.scan()` 在开头戳一次 `ts`，而 `fetch_klines` 剔除未收盘 K 用的是每次请求时现取的 `time.time()`——两个时钟之间隔着整轮逐币逐周期拉取的耗时。9/6 18:29:45 手动首帧启动时距 15m 收盘（18:30）仅 15 秒，拉取必然跨过边界 → 后半段标的的 15m 窗口多含一根新收盘 K，但落盘 `ts` 仍写 18:29:45。差一根 K → EMA seeding 起点不同 → 最敏感的 15m power ±1。

> ⚠️ **首帧须整帧排除，不是只剔那 2 行。** 按 MAJORS 顺序，XRP 之后的约 7-8 条记录**都**多吃了一根 15m K；只是 `power` 是符号比较得到的小整数，多一根 K 通常翻不动阈值，所以只有 XRP/LINK 显形。**没显形 ≠ 没错位**，按行剔除等于拿"碰巧没翻转"当清白证明。
> cron 帧全部落在 `*:05`，距 K 线边界 5 分钟，拉取耗时跨不过去 → 335/335 一致。**手动帧与 cron 帧不同质**，混在一起统计前必须先分开。

**该发现反过来支持停采**：缺陷在采集器（`ts` 与数据窗口可不一致），重建器用 `endTime=T` 锚定、给定 T 完全确定，结构上没有这个竞态。停采是消除缺陷源。
> 明确不采纳的两个"修法"：①修采集器时钟——给即将淘汰的组件投资，且它停采后只作为 `ema/macd_state/classify` 的真相源被 import，那部分不碰时钟；②改校验脚本"对齐时钟口径"——等于造一个专门掩盖这类 bug 的装置，而这次正是靠校验器**不**宽容才挖出来的（教训 6）。

### 6. M8 K 线口径：现货 → 合约（9/11 拍板，口径变更）
**缺陷**：`macd_m8.py` 的 `MAJORS` 含 HYPE，但数据源是现货 `api.binance.com/api/v3`，而 **HYPE 现货无交易对**（`-1121 Invalid symbol`），合约有。自 9/6 部署起 HYPE 每次扫描都在静默记 error。

不止 HYPE——score 榜历史出现过的 34 个标的中 **8 个现货无对**（`1000BONK / BR / BTW / BULLA / MAGMA / UAI / USELESS / XMR`），即 M8 对约 **24% 的 AI500 标的从来没有数据**。根因是口径错配：score 榜与 V3 交易的都是 `core_perp`，M8 却查现货。（`1000BONK` 另属符号映射问题——现货叫 `BONK`。）

**决策：换合约 fapi**。理由是 M8 的用途就是给 V3/B3 的**合约**交易做择时，用现货价算动量本身是错配。合约侧实测覆盖 score 榜 **34/34 + MAJORS 9/9，零缺口**，符号命名也与 score 榜一致。

**实现**：`macd_m8.py` 新增 `KLINE_BASE` / `KLINE_SOURCE`（env `M8_KLINE_SOURCE`，默认 futures），落盘记录新增 `src` 字段；`macd_m8_offline.py` 加 `--source futures|spot`。切换后 HYPE selfcheck 通过（1d 仅 470 根，仍过 120 根下限）。

**口径变更副作用**（CLAUDE.md 副作用条款）：9/11 之前落盘的行是现货口径（无 `src` 字段），与之后**不得混池统计**；重放旧数据必须 `--source spot`。
> 探索性旁证——非结论：10 币 × 3 时刻共 30 条两口径对比，`powers` 与 `state` **30/30 完全一致**。M8 的 power 是符号比较得到的小整数，对现货/永续的价差不敏感。但 30 条证明不了"两口径永远等价"（基差剧烈波动时仍可能分叉），故混池禁令与 `src` 标注照旧保留。
> **换口径不削弱对齐校验的价值**：校验验的是"重放机制对不对"，在旧口径下用旧数据验完，机制正确性即已确立，再把同一机制套到新口径上。
