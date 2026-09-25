# AI500 H1/H2 —— 服务器端使用方法

**全部在远端服务器上跑，本机不跑。** 所有命令都在仓库根目录执行（`/home/gyan/freqtrade`）。

## 0. 拉代码

```bash
cd /home/gyan/freqtrade && git pull origin main
```

## 1. 持续采集 score 榜（V3 关停后的唯一数据源）

V3 bot 停掉后，`user_data/ai500_score_history_v3.csv` 不再增长，score 榜改由独立采集器写入：

```bash
# 前台试跑一轮，确认有 score_rows > 0
LOCAL_PROXY=http://127.0.0.1:7890 python3 scripts/ai500_local_collector.py --once

# 后台常驻
LOCAL_PROXY=http://127.0.0.1:7890 nohup python3 scripts/ai500_local_collector.py \
  >> user_data/ai500_local/collector.log 2>&1 &

# 检查
tail -f user_data/ai500_local/collector.log          # 每 30 轮打印一次，score_rows 应 > 0
ls -la user_data/ai500_local/score_$(date +%Y%m%d).csv
```

- 输出：`user_data/ai500_local/score_YYYYMMDD.csv`（每天一个文件，永不自动清理），另有 `netflow_multiwin_*.csv`（保留 60 天）。
- 直连 vergex 能通的话，可以不设 `LOCAL_PROXY`。
- 停止：`pkill -f ai500_local_collector.py`

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
