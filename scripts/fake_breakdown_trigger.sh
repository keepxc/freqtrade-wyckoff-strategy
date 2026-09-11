#!/usr/bin/env bash
# P5 假跌破检测器 — 生产定时触发（每 6h 一次，对齐设计文档窗口步长）
# 由 cron 触发：0 */6 * * * /path/to/fake_breakdown_trigger.sh
#
# 依赖前提（未在开发机验证，上线前必须现场确认）：
#   1. market.db 的 trades 表已通过 ingest 管线持续写入 dry_run/live 事件
#      （backtest run_id 已知有 trade_close 匹配 bug，dry_run/live 路径逻辑不同，
#       但未实测——上线第一步应先查 SELECT COUNT(*) FROM trades WHERE run_id LIKE 'run_%dry_run%'
#       确认非零且 exit_time 有值）
#   2. user_data/config_dryrun.json 的 telegram.token/chat_id 在生产机上有效
#
# 只做软告警：写 proposals 表 + Telegram 通知，不修改任何策略代码/参数。

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="${ROOT}/logs/fake_breakdown_detector.log"
mkdir -p "$(dirname "$LOG")"

cd "$ROOT"
echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') 触发假跌破检测（market_db / live）" >> "$LOG"

python3 scripts/detect_fake_breakdown.py \
  --source market_db \
  --db user_data/market.db \
  --mode live \
  --notify \
  --telegram-config user_data/config_dryrun.json \
  >> "$LOG" 2>&1

echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') 检测完成" >> "$LOG"
