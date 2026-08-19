#!/usr/bin/env bash
# OI 数据定时下载 — 每天 08:30 CST（日线收盘后）
set -euo pipefail

ROOT="/home/gyan/freqtrade"
LOG="$ROOT/logs/oi_download.log"
mkdir -p "$(dirname "$LOG")"

cd "$ROOT"
echo "$(date '+%Y-%m-%d %H:%M:%S CST') OI download start" >> "$LOG"

# 下载 OI 数据（增量）
.venv/bin/python user_data/scripts/download_oi.py >> "$LOG" 2>&1

# 迁移到 market_data.db（如果 download 成功）
if [ -f "user_data/data/binance/futures/BTC_USDT_USDT-1d-open_interest.feather" ]; then
    .venv/bin/python user_data/scripts/migrate_oi_to_db.py >> "$LOG" 2>&1
fi

echo "$(date '+%Y-%m-%d %H:%M:%S CST') OI download complete" >> "$LOG"
