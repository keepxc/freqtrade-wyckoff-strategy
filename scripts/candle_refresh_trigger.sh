#!/usr/bin/env bash
# 1H 蜡烛收盘后强制刷新 freqtrade 数据
# 由 cron 在每小时 :01 触发（蜡烛收盘后 ~1 分钟）
# SIGHUP 触发 freqtrade config reload，重置 _pairs_last_refresh_time，
# 下一个 process_throttle tick（30s）立即拉最新蜡烛。
# 持仓不受影响（open trades 存 SQLite，reload 不清除）。

set -euo pipefail

SERVICE="freqtrade-dryrun"
LOG="/home/gyan/freqtrade/logs/candle_refresh.log"

if systemctl --user is-active --quiet "${SERVICE}.service"; then
    systemctl --user kill --signal=HUP "${SERVICE}.service"
    echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') SIGHUP sent to ${SERVICE}" >> "$LOG"
else
    echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') ${SERVICE} not running, skip" >> "$LOG"
fi
