#!/usr/bin/env bash
set -euo pipefail

FREQTRADE_DIR="/home/gyan/freqtrade"
DB="$FREQTRADE_DIR/P5/p5.db"
OUTPUT="$FREQTRADE_DIR/weekly_report_$(date +%Y%m%d).md"
PYTHON="$FREQTRADE_DIR/.venv/bin/python"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting weekly report generation..."

"$PYTHON" "$FREQTRADE_DIR/user_data/reports/build_weekly_report.py" \
    --db "$DB" \
    --output "$OUTPUT" \
    --store

ln -sf "$OUTPUT" "$FREQTRADE_DIR/weekly_report_latest.md"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Done: $OUTPUT"
