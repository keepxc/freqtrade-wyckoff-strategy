#!/usr/bin/env bash
# ============================================================================
# 单次分析任务：更新数据 → 结构分析 → 出图 → 刷新 token → 调 AI → 归档
#
# 用法：
#   ./run_session.sh asia            # 亚盘槽位（默认 00:30 UTC）
#   ./run_session.sh euus            # 欧美槽位（默认 14:00 UTC）
#   ./run_session.sh manual          # 手动跑
#   ./run_session.sh asia --no-ai    # 只做本地分析与出图，不联网调 AI
#
# 退出码：0 正常 / 1 AI 调用失败 / 2 前置缺失 / 3 额度为 0
#
# 设计取舍：
#   · 用 set -uo pipefail 但**不加 -e**：拉数据失败不应导致整轮中断，
#     沿用本地已有 CSV 出图比什么都不产出更有用。
#   · 每步都把 stdout/stderr 追加进 logs/run_YYYYMMDD.log，cron 下无终端也能回溯。
# ============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE" || exit 2

SLOT="${1:-manual}"
shift || true
NO_AI=0
for a in "$@"; do
  [ "$a" = "--no-ai" ] && NO_AI=1
done

# 载入 .env（若存在）。set -a 让里面定义的变量自动导出给子进程。
if [ -f "$HERE/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$HERE/.env"
  set +a
fi

PY="$HERE/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
if [ -z "$PY" ]; then
  echo "[中止] 找不到 python3，请先跑 ./setup.sh" >&2
  exit 2
fi

mkdir -p "$HERE/logs" "$HERE/out"
STAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
LOG="$HERE/logs/run_$(date -u +%Y%m%d).log"

log() { echo "[$STAMP] $*" | tee -a "$LOG"; }

log "===== slot=$SLOT start ====="

# 各步退出码。前向记录（第 5 步）无论前面成败都要跑——
# 「这一轮没跑成」本身就是要统计的管线失败，不能让它从分母里消失。
SW_RC=0; CHART_RC=0; AI_RC=0; SKIP=0

# 1) 增量拉取最新 1h 数据（失败不中断，沿用本地数据）
log "--- 1/6 更新数据"
"$PY" swing_watch.py --update --quiet >>"$LOG" 2>&1
SW_RC=$?
if [ $SW_RC -eq 2 ]; then
  log "数据不足以确认枢轴，跳过出图与 AI"
  SKIP=1
elif [ $SW_RC -ne 0 ]; then
  log "警告：结构分析返回 $SW_RC，继续尝试后续步骤"
fi

# 2) 出图
if [ $SKIP -eq 0 ]; then
  log "--- 2/6 生成图表"
  "$PY" make_chart.py >>"$LOG" 2>&1
  CHART_RC=$?
  [ $CHART_RC -ne 0 ] && log "警告：出图失败（$CHART_RC）"
fi

# 3) 刷新 token（幂等：未过期则直接跳过，不消耗额度）
if [ $SKIP -eq 0 ] && [ "$NO_AI" -eq 0 ]; then
  log "--- 3/6 检查 AI token"
  "$PY" ai_login.py >>"$LOG" 2>&1
  rc=$?
  [ $rc -ne 0 ] && log "警告：token 刷新失败（$rc），继续尝试调用"
else
  log "--- 3/6 跳过 token"
fi

# 4) 调外部 AI
if [ $SKIP -eq 0 ] && [ "$NO_AI" -eq 0 ]; then
  log "--- 4/6 调用外部 AI（槽位 $SLOT）"
  "$PY" ask_ai.py --slot "$SLOT" >>"$LOG" 2>&1
  AI_RC=$?
  case $AI_RC in
    0) log "AI 调用成功" ;;
    3) log "额度为 0，跳过（等重置）" ;;
    2) log "前置缺失（token 或图）" ;;
    *) log "AI 调用失败，退出码 $AI_RC" ;;
  esac
else
  log "--- 4/6 跳过 AI"
  AI_RC=9
fi

# 5) 前向记录 + 打分（只读，不联网）
log "--- 5/6 前向记录"
"$PY" forward_log.py --slot "$SLOT" --sw-rc "$SW_RC" --chart-rc "$CHART_RC" \
  --ai-rc "$AI_RC" 2>&1 | tee -a "$LOG" | grep '^\[前向\]' || log "警告：前向记录失败"
"$PY" score.py >>"$LOG" 2>&1 || log "警告：打分失败"

rc=$AI_RC
[ $AI_RC -eq 9 ] && rc=0
[ $SKIP -eq 1 ] && rc=2

# 6) 归档：把本轮所有产物复制一份到 out/archive/YYYYMMDD/，避免下一轮覆盖
log "--- 6/6 归档"
"$PY" - "$SLOT" <<'PYEOF' >>"$LOG" 2>&1
import datetime, json, os, shutil, sys
import config as C

slot = sys.argv[1]
day = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d")
arc = os.path.join(C.OUT_DIR, "archive", day)
os.makedirs(arc, exist_ok=True)

files = ("watch_latest.json", "report_watch.html", "chart_dual.png",
         "watch_history.csv", "forward_log.csv", "forward_score.md")
for f in files:
    p = os.path.join(C.OUT_DIR, f)
    if os.path.exists(p):
        shutil.copy2(p, os.path.join(arc, f"{slot}_{f}"))

# AI 结果按 out/ai_<日期>_<槽位>.json 命名，连同索引一起归档
# （ai_index.json 是索引，不是单次结果，单独处理）
for f in (f"ai_{day}_{slot}.json", f"ai_raw_response.txt"):
    p = os.path.join(C.OUT_DIR, f)
    if os.path.exists(p):
        shutil.copy2(p, os.path.join(arc, f))

idx = os.path.join(C.OUT_DIR, "ai_index.json")
if os.path.exists(idx):
    shutil.copy2(idx, os.path.join(arc, "ai_index.json"))

n = len(os.listdir(arc))
print(f"归档到 {arc}（{n} 个文件）")
PYEOF

log "===== slot=$SLOT done (rc=$rc) ====="
exit $rc
