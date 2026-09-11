#!/usr/bin/env bash
# OI 数据定时下载 — 每天 08:30 CST（日线收盘后）
set -euo pipefail

ROOT="/home/gyan/freqtrade"
LOG="$ROOT/logs/oi_download.log"
mkdir -p "$(dirname "$LOG")"

cd "$ROOT"
echo "$(date '+%Y-%m-%d %H:%M:%S CST') OI download start" >> "$LOG"

# 下载 OI 数据（增量）——requests 读 env 代理，cron 环境无代理会直连 fapi 超时（9/9 实证）
# ⚠️ 必须显式给代理，否则静默失败（超时后被容错为"无新数据"退出 0）
LOCAL_PROXY="http://127.0.0.1:7890"
HTTPS_PROXY="$LOCAL_PROXY" https_proxy="$LOCAL_PROXY" HTTP_PROXY="$LOCAL_PROXY" http_proxy="$LOCAL_PROXY" \
  .venv/bin/python user_data/scripts/download_oi.py >> "$LOG" 2>&1

# 迁移到 market_data.db（如果 download 成功）
if [ -f "user_data/data/binance/futures/BTC_USDT_USDT-1d-open_interest.feather" ]; then
    .venv/bin/python user_data/scripts/migrate_oi_to_db.py >> "$LOG" 2>&1
fi

echo "$(date '+%Y-%m-%d %H:%M:%S CST') OI download complete" >> "$LOG"
