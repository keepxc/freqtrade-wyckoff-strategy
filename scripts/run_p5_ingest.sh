#!/usr/bin/env bash
# ============================================================
# P5 ingest runner — 自动发现最新 dry_run 事件目录并入库
# ============================================================
set -euo pipefail

FREQTRADE_DIR="/home/gyan/freqtrade"
PYTHON="$FREQTRADE_DIR/.venv/bin/python"
INGEST_SCRIPT="$FREQTRADE_DIR/user_data/ingest/ingest_jsonl.py"
P5_DB="$FREQTRADE_DIR/P5/p5.db"
SCHEMA_SQL="$FREQTRADE_DIR/P5/db/schema.sql"
VIEWS_SQL="$FREQTRADE_DIR/P5/db/views.sql"
EVENTS_BASE="$FREQTRADE_DIR/user_data/events/dry_run"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] P5 ingest starting..."

# 找最新 dry_run 事件目录
LATEST_DIR=$(ls -dt "$EVENTS_BASE"/run_*/ 2>/dev/null | head -1 || true)
if [ -z "$LATEST_DIR" ]; then
    echo "[ERROR] No dry_run event directories found under $EVENTS_BASE"
    exit 1
fi

echo "[INFO] Latest run dir: $LATEST_DIR"

# 收集所有 JSONL 文件（run.jsonl 放最前保证外键）
EVENT_FILES=()
for ext in run trades signals detectors regimes exit_decisions; do
    for f in "$LATEST_DIR/$ext.jsonl" "$LATEST_DIR/${ext}s.jsonl"; do
        if [ -f "$f" ]; then
            EVENT_FILES+=("$f")
        fi
    done
done

if [ ${#EVENT_FILES[@]} -eq 0 ]; then
    echo "[ERROR] No JSONL files found in $LATEST_DIR"
    exit 1
fi

echo "[INFO] Found ${#EVENT_FILES[@]} JSONL files"

# 执行 ingest
"$PYTHON" "$INGEST_SCRIPT" \
    --db "$P5_DB" \
    --schema "$SCHEMA_SQL" \
    --views "$VIEWS_SQL" \
    --events "${EVENT_FILES[@]}"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] P5 ingest done"
