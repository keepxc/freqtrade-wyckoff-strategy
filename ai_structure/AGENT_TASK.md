# AGENT_TASK — bot 机器部署交接

> 给在 bot 那台 Linux 上执行的 Agent。按顺序做，每步有验收命令；
> 任何一步验收不过，**停下来报告**，不要自行改判据或绕过。

## 0. 边界（必须遵守）

- **只部署观察管线，不下单。** 本包不含、也不得添加任何交易执行逻辑。
- **不碰 freqtrade**：不改它的容器、配置、策略、crontab 里不属于本包的行。
  本包用独立目录、独立 venv、crontab 里只动 `# >>> btc-swing-watch >>>` 那一段。
- **不改判据**：`config.py` 里 `FWD_*` 常量、`forward_log.py` 的 `PROG_CODES`、
  `FORWARD_PROTOCOL.md` 都是冻结的。
- **不刷额度**：不用 `ask_ai.py --force`，不注册新账号。每天额度只有 2 次。
- **不手改** `out/forward_log.csv`。

## 1. 拉代码

本包位于 freqtrade 仓库的 `ai_structure/` 子目录（https://github.com/keepxc/freqtrade-wyckoff-strategy）。

```bash
cd <freqtrade 仓库目录>          # bot 机器上已有的 checkout
git pull origin main
cd ai_structure
chmod +x setup.sh install_cron.sh run_session.sh
```

下文所有命令都在 `ai_structure/` 目录下执行，记作 `$AS`（= 该目录绝对路径）。

验收：`ls` 能看到 `FORWARD_PROTOCOL.md`、`forward_log.py`、`score.py`、`selftest_forward.py`；
`file run_session.sh` 不含 `CRLF`（有则 `sed -i 's/\r$//' *.sh`）。

## 2. 环境与凭据

```bash
./setup.sh            # 国内网络加 --mirror；此时还没数据，会提示跳过自检，正常
cp .env.example .env
chmod 600 .env
```

`.env` 填 `AI_EMAIL` / `AI_PASSWORD`：**向用户要，不在仓库里，不要猜，不要写进任何会提交的文件**。
`.env`、`.token` 已被 `.gitignore`，提交前用 `git status` 确认它们不出现。
若机器访问币安或 AI 站点需要代理，填 `HTTPS_PROXY`（freqtrade 若已有代理配置，可复用同一个地址）。
**保持 `AI_PROMPT_STATE=0`。**

## 3. 数据（CSV 不入库，首次全量下载）

```bash
.venv/bin/python download_data.py     # 约 80 次请求，1~3 分钟
tail -2 data/BTCUSDT_1h.csv
date -u
```

验收：约 8 万行；末行时间 = 当前 UTC 整点 −1h（最后一根**已收盘** bar）。

然后跑自检：
```bash
.venv/bin/python selftest.py          # 期望：通过 47 项，失败 0 项
.venv/bin/python selftest_forward.py  # 期望：最后一行「全部通过」
```

## 4. AI 登录与 token 续期

```bash
set -a; . ./.env; set +a
.venv/bin/python ai_login.py
.venv/bin/python ai_login.py          # 第二次应显示「[跳过] 现有 token 有效期至 …」
.venv/bin/python ask_ai.py --slot manual --dry-run
```

验收：
- `.token` 已生成，权限 600；登录输出的有效期约为 7 天后
- dry-run 打印的提示词**不含**「当前客观状态」段落，末尾有 `DIRECTION_JSON` 格式要求

**token 续期不需要单独的定时任务**：`run_session.sh` 每次排班（每天 2 次）都先跑 `ai_login.py`，
剩余有效期不足 24h（`TOKEN_RENEW_H`）就用 `.env` 里的账号重新登录。

## 5. 管线试跑（不调 AI，不耗额度）

每天额度只有 2 次，正好给两个排班槽位用，**试跑不要消耗 AI 额度**：

```bash
./run_session.sh manual --no-ai
tail -30 logs/run_$(date -u +%Y%m%d).log
```

验收：
- 日志 1/6~6/6 都走到，`out/chart_dual.png` 与 `out/report_watch.html` 生成
- 日志里有 `[前向] 槽位 manual 不在前向样本内` ——manual 不进样本，正确

真实 AI 调用的验收放到第 7 步（首个排班槽位）。
若首个槽位 AI 返回的最后一行不是合法 `DIRECTION_JSON`：**记录下来报告给用户**，不要改解析器放宽规则。

## 6. 注册定时任务

```bash
./install_cron.sh --dry-run
./install_cron.sh
crontab -l
```

验收：crontab 里有 `CRON_TZ=UTC` + `30 0 * * *` 与 `0 14 * * *` 两行（或按本地时区换算后的等价时间），
且原有的非本包条目原样保留。

## 7. 首个排班槽位跑完后

```bash
cat out/forward_log.csv
cat out/forward_score.md
```

验收：`forward_log.csv` 有一行 `pipeline_ok=1`，`ai_parse_ok=1`，`data_fresh=1`。

## 8. 日常巡检（每天一次，只读）

```bash
cd "$AS"
tail -5 logs/cron.log
tail -3 out/forward_log.csv
sed -n '/① 管线检查点/,/② /p' out/forward_score.md
```

异常处置：
| 现象 | 处理 |
|---|---|
| `pipeline_ok=0` 且 `data_fresh=0` | 币安拉数失败，查代理 |
| `ai_status=quota` | 额度被别处用掉了，查是否有人手动跑过 |
| `ai_status=fail` 且日志有 401 | token 失效：`.venv/bin/python ai_login.py --force`；若登录也失败，检查 `.env` 凭据，报告用户 |
| `ai_status=fail`（其他） | 看 `out/ai_raw_response.txt`，接口可能改形态 |
| `ai_parse_ok=0` 但 `ai_status=ok` | AI 没按格式输出，**只记录不修改**，计入解析失败率 |
| cron 没触发 | `systemctl status cron`，看 `logs/cron.log` |

修管线代码（非判据）可以，但要记下修复时点，报告给用户。

## 9. 两周后（首次记录 +14 天）

```bash
.venv/bin/python score.py
```

把 `out/forward_score.md` 全文交给用户。**只报告数字，不给「可以上实盘」之类的结论**——
按协议，两周检查点只决定是否继续观察；实盘门槛是 n≥30 个独立 7 天窗口，最快约 30 周。
