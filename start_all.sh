#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# 启动所有服务 — 按依赖顺序
# 1. Market State Engine（写 market.db watchlist）
# 2. HybridTraderV1 dry run（读 market.db watchlist）
# 3. WyckoffV8 dry run（独立，无依赖）
# ═══════════════════════════════════════════════════════════════════
set -euo pipefail

FREQTRADE_DIR="/home/gyan/freqtrade"

echo "═══════════════════════════════════════"
echo "  Freqtrade 服务启动"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "═══════════════════════════════════════"

# ── 清理 bytecache ────────────────────────
echo ""
echo "[1/4] Clearing Python bytecode..."
find "$FREQTRADE_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "$FREQTRADE_DIR" -type f -name "*.pyc" -delete 2>/dev/null || true
echo "  Done"

# ── 1. Market State Engine ─────────────────
echo ""
echo "[2/4] Market State Engine..."
systemctl --user daemon-reload
systemctl --user enable freqtrade-mse.service 2>/dev/null
if systemctl --user is-active --quiet freqtrade-mse.service; then
  systemctl --user restart freqtrade-mse.service
  echo "  Restarted"
else
  systemctl --user start freqtrade-mse.service
  echo "  Started"
fi

# ── 2. HybridTraderV1 ─────────────────────
echo ""
echo "[3/4] HybridTraderV1..."
bash "$FREQTRADE_DIR/start_hybrid_dryrun.sh"

# ── 3. WyckoffV8（如未运行）──────────────
echo ""
echo "[4/4] WyckoffV8..."
if ! systemctl --user is-active --quiet freqtrade-dryrun.service; then
  bash "$FREQTRADE_DIR/start_dryrun.sh"
else
  echo "  Already running"
fi

echo ""
echo "═══════════════════════════════════════"
echo "  服务状态"
systemctl --user list-units 'freqtrade*' --no-pager
echo ""
echo "  日志"
echo "  MSE:      tail -f $FREQTRADE_DIR/logs/market_state_engine.log"
echo "  Hybrid:   tail -f $FREQTRADE_DIR/logs/hybrid-dryrun.log"
echo "  V8:       tail -f $FREQTRADE_DIR/logs/dryrun.log"
echo "═══════════════════════════════════════"
