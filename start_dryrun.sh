#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="freqtrade-dryrun"
SERVICE_FILE="$HOME/.config/systemd/user/${SERVICE_NAME}.service"
FREQTRADE_DIR="/home/gyan/freqtrade"

# ═══════════════════════════════════════════════════
# Preflight 检查 — 启动前验证数据 + 配置 + 门控状态
# ═══════════════════════════════════════════════════
echo ""
echo "========================================================"
echo "  [preflight] Dry Run 启动前检查..."
echo "========================================================"
source "$FREQTRADE_DIR/env/dryrun.env" 2>/dev/null || true
PYTHONPATH="${PYTHONPATH:-}:$FREQTRADE_DIR" \
  "$FREQTRADE_DIR/.venv/bin/python" "$FREQTRADE_DIR/scripts/local_preflight.py"
PREFLIGHT_EXIT=$?
if [ $PREFLIGHT_EXIT -ne 0 ]; then
  echo "[preflight] ❌ 检查未通过，启动已取消。修复后重新运行 start_dryrun.sh"
  exit 1
fi
echo "[preflight] ✅ 检查通过，继续启动..."
echo ""

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
