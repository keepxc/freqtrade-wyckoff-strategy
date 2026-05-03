#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="freqtrade-dryrun"
SERVICE_FILE="$HOME/.config/systemd/user/${SERVICE_NAME}.service"
FREQTRADE_DIR="/home/gyan/freqtrade"

# ═══════════════════════════════════════════════════
# 清理缓存 — 防止旧 bytecode 覆盖新源码
# ═══════════════════════════════════════════════════
echo "[cache] Clearing Python bytecode..."
find "$FREQTRADE_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "$FREQTRADE_DIR" -type f -name "*.pyc" -delete 2>/dev/null || true
echo "[cache] Bytecode cleared"

echo "[cache] Clearing freqtrade OHLCV data cache..."
rm -rf "$FREQTRADE_DIR/user_data/data/"* 2>/dev/null || true
echo "[cache] OHLCV cache cleared"

# Ensure service file exists
if [ ! -f "$SERVICE_FILE" ]; then
  echo "[ERROR] Service file not found: $SERVICE_FILE"
  exit 1
fi

# Reload systemd in case service file was updated
systemctl --user daemon-reload

# Enable (boot auto-start + linger)
systemctl --user enable "$SERVICE_NAME.service" 2>/dev/null
loginctl enable-linger "$(whoami)" 2>/dev/null

# Start or restart
if systemctl --user is-active --quiet "$SERVICE_NAME.service"; then
  echo "[start_dryrun] $SERVICE_NAME is already running. Restarting..."
  systemctl --user restart "$SERVICE_NAME.service"
else
  echo "[start_dryrun] Starting $SERVICE_NAME..."
  systemctl --user start "$SERVICE_NAME.service"
fi

sleep 2

# Status
echo ""
systemctl --user status "$SERVICE_NAME.service" --no-pager
