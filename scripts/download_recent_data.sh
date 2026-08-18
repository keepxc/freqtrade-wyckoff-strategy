#!/usr/bin/env bash
# Silent data download for V8 dry-run
# Runs every 30 min, only outputs on error (watchdog pattern)
set -euo pipefail

FREQTRADE_DIR="/home/gyan/freqtrade"
CONFIG1="$FREQTRADE_DIR/user_data/config_dryrun.json"
CONFIG2="$FREQTRADE_DIR/user_data/config_v8_dryrun.json"
LOG="$FREQTRADE_DIR/logs/download_recent_data.log"

cd "$FREQTRADE_DIR"

# Download 1H (last 2 days) — silent, capture errors
"$FREQTRADE_DIR/.venv/bin/python" -m freqtrade download-data \
  --config "$CONFIG1" --config "$CONFIG2" \
  --timerange 20260604- \
  --timeframe 1h \
  --candle-types futures \
  > /dev/null 2>> "$LOG"

# Download 1D (last 5 days)
"$FREQTRADE_DIR/.venv/bin/python" -m freqtrade download-data \
  --config "$CONFIG1" --config "$CONFIG2" \
  --timerange 20260601- \
  --timeframe 1d \
  --candle-types futures \
  > /dev/null 2>> "$LOG"

echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') data download OK" >> "$LOG"
