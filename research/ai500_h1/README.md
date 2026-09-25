# AI500 H1/H2 —— 服务器端使用方法

**全部在远端服务器上跑，本机不跑。** 所有命令都在仓库根目录执行（`/home/gyan/freqtrade`）。

## 0. 拉代码

```bash
cd /home/gyan/freqtrade && git pull origin main
```

## 1. 持续采集 score 榜（V3 关停后的唯一数据源）

V3 bot 停掉后，`user_data/ai500_score_history_v3.csv` 不再增长，score 榜改由独立采集器写入：

```bash
# ⚠️ 2026-09-25 起本机已改为 **systemd 常驻**（不要再用 nohup 起第二份）：
systemctl --user status ai500-local-collector.service
systemctl --user restart ai500-local-collector.service
# 前台试跑一轮用（验证传输层/凭据，不写调度）
LOCAL_PROXY=http://127.0.0.1:7890 python3 scripts/ai500_local_collector.py --once
```

**检查（判活只看产物，不要看日志的 score_rows）**：

```bash
# ✅ 正确：看产物文件的行数与末轮时间（score 应每 5 分钟一行组、netflow 每 1 分钟一组）
ls -la user_data/ai500_local/score_$(date +%Y%m%d).csv
tail -1 user_data/ai500_local/score_$(date +%Y%m%d).csv
tail -3 user_data/ai500_local/collector.log     # 日志只看异常/崩溃重启
```

- ⚠️ **日志里的 `score_rows` 是「本轮」计数，不是区间累计**：score 每 5 轮才拉一次，
  故该字段绝大多数轮次为 0 —— **它恒为 0 不代表采集坏了**。
  2026-09-25 已把汇总行改成 `report(N轮累计): netflow_rows=.. score_rows=.. | 本轮 nf=.. sc=..`
  （区间累计 + 本轮两个口径并列）。**判活一律以产物文件为准。**
  这条纪律的来由：09-04→09-25 的真实断采正是**只看 `score_rows=0` 而没查产物**才被漏了 5 周。
- 输出：`user_data/ai500_local/score_YYYYMMDD.csv`（每天一个文件，永不自动清理），
  另有 `netflow_multiwin_*.csv`（保留 60 天）。
- 直连 vergex 能通的话可以不设 `LOCAL_PROXY`；systemd 单元里已显式设为本机 7890。
- 停止：`systemctl --user stop ai500-local-collector.service`（旧的 `pkill -f ai500_local_collector.py` 仅适用于手工 nohup 起的进程）。


## 2. 复跑 H1/H2（已冻结的检验）

```bash
python3 research/ai500_h1/fetch_klines.py      # 拉币安 1h K 线 → data/klines_1h.parquet（约 5 分钟）
python3 research/ai500_h1/analyze_h1_h2.py | tee research/ai500_h1/output.txt
```

- 代理：`fetch_klines.py` 默认 `http://127.0.0.1:7890`，可用 `AI500_PROXY=...` 覆盖。
- 输入：`user_data/ai500_score_history_v3.csv` + `data/exchangeInfo_20260925.json`（宇宙快照，已冻结，**不要替换**）。
- 输出：`output.txt`、`h1_events.csv`、`h2_events.csv`。
- 依赖：`pandas numpy pyarrow`。

## 3. 纪律（必读）

- 判据见 `PREREG_H1_H2.md`，结果见 `RESULTS_H1_H2.md`（4d 中位超额 −1.15%，CI [−6.19, +2.40] → 无 alpha 证据，⭕）。
- **在同一批数据上重跑只是复现，不是新证据。** 事件窗口截止在 09-24，重新拉 K 线只会让 7d 窗口的删失变少，**不得**据此改写结论。
- 要用新采集的数据（第 1 节）重测 H1，必须**先写一份新的预注册**再跑，并且只用预注册之后采集的数据。那时要把 `analyze_h1_h2.py` 的输入换成 `user_data/ai500_local/score_*.csv`：时间列名是 `fetched_at_cst`（值同样带 +08:00），另外 `price` 列带 `$` 和千分位逗号。
