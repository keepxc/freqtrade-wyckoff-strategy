#!/usr/bin/env bash
# ============================================================================
# 密集观测（每 4h，由 cron 调）：程序方向 + AI 方向的密集记录
#
# ⚠️ 与前向样本完全隔离：只写 out/dense_log.csv，**不写** out/forward_log.csv，
#    不影响 n、检查点分母、实盘门槛。用途是测量相位覆盖度 / AI 翻转率 / provider 分歧。
#    也不做归档（省磁盘）。
#
# 用法： ./run_dense.sh            # 正常
#        DENSE_NO_AI=1 ./run_dense.sh   # 只更新数据与图，不调 AI
# ============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE" || exit 2

if [ -f "$HERE/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$HERE/.env"
  set +a
fi

PY="$HERE/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
mkdir -p "$HERE/logs" "$HERE/out"

LOG="$HERE/logs/dense.log"
STAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
SLOT="dense_$(date -u +%H)"
log() { echo "[$STAMP] $*" | tee -a "$LOG"; }

log "===== $SLOT start ====="

SW_RC=0; CHART_RC=0; AI_RC=0
"$PY" swing_watch.py --update --quiet >>"$LOG" 2>&1
SW_RC=$?
[ $SW_RC -ne 0 ] && log "警告：swing_watch 返回 $SW_RC"

if [ $SW_RC -ne 2 ]; then
  "$PY" make_chart.py >>"$LOG" 2>&1
  CHART_RC=$?
  [ $CHART_RC -ne 0 ] && log "警告：出图失败（$CHART_RC）"
fi

if [ $SW_RC -ne 2 ] && [ "${DENSE_NO_AI:-0}" != "1" ]; then
  "$PY" ask_vision.py --slot "$SLOT" >>"$LOG" 2>&1
  AI_RC=$?
  [ $AI_RC -ne 0 ] && log "AI 调用未成功（rc=$AI_RC）"
fi

"$PY" dense_log.py --slot "$SLOT" --sw-rc "$SW_RC" --chart-rc "$CHART_RC" \
  --ai-rc "$AI_RC" 2>&1 | tee -a "$LOG"

log "===== $SLOT done (ai_rc=$AI_RC) ====="
exit 0
