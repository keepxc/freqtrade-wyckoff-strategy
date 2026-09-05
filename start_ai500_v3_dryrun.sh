#!/usr/bin/env bash
set -euo pipefail

# AI500 V3 ScoreHold dry-run 启动脚本
# 用法：先按 deploy/freqtrade-ai500-v3-dryrun.service 模板在
#   $HOME/.config/systemd/user/freqtrade-ai500-v3-dryrun.service
# 建好 service 文件（路径按你机器实际 FREQTRADE_DIR 调整），再跑本脚本。

SERVICE_NAME="freqtrade-ai500-v3-dryrun"
SERVICE_FILE="$HOME/.config/systemd/user/${SERVICE_NAME}.service"
FREQTRADE_DIR="/home/gyan/freqtrade"

# ═══════════════════════════════════════════════════
# 清理缓存 — 防止旧 bytecode 覆盖新源码
# ═══════════════════════════════════════════════════
echo "[cache] Clearing Python bytecode..."
find "$FREQTRADE_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "$FREQTRADE_DIR" -type f -name "*.pyc" -delete 2>/dev/null || true
echo "[cache] Bytecode cleared"

# Ensure service file exists
if [ ! -f "$SERVICE_FILE" ]; then
  echo "[ERROR] Service file not found: $SERVICE_FILE"
  echo "        Copy deploy/freqtrade-ai500-v3-dryrun.service there first."
  exit 1
fi

# Reload systemd in case service file was updated
systemctl --user daemon-reload

# Enable (boot auto-start + linger)
systemctl --user enable "$SERVICE_NAME.service" 2>/dev/null
loginctl enable-linger "$(whoami)" 2>/dev/null

# Start or restart
if systemctl --user is-active --quiet "$SERVICE_NAME.service"; then
  echo "[start_ai500_v3] $SERVICE_NAME is already running. Restarting..."
  systemctl --user restart "$SERVICE_NAME.service"
else
  echo "[start_ai500_v3] Starting $SERVICE_NAME..."
  systemctl --user start "$SERVICE_NAME.service"
fi

sleep 2

# Status
echo ""
systemctl --user status "$SERVICE_NAME.service" --no-pager
