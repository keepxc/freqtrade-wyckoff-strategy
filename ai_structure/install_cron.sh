#!/usr/bin/env bash
# ============================================================================
# 注册定时任务：每天两次
#   亚盘槽位  00:30 UTC  —— 东京开盘后，且刚好在 AI 额度重置（00:00 UTC）之后
#   欧美槽位  14:00 UTC  —— 欧美重叠时段波动的峰值小时（实测归一波动 1.40）
#
# 用法：
#   ./install_cron.sh              # 安装/更新（幂等，重复执行只替换自己那一段）
#   ./install_cron.sh --dry-run    # 只打印将要写入的 crontab，不落盘
#   ./install_cron.sh --local      # 不用 CRON_TZ，按本机时区换算成等价的本地时间
#   ./install_cron.sh --remove     # 只删除本包注册的那一段
#
# 为什么默认用 CRON_TZ=UTC：
#   服务器时区可能不是 UTC，而且有些时区有夏令时。写死本地时间会在换季时
#   悄悄漂移一小时。CRON_TZ 让 cron 条目直接按 UTC 解释，逻辑最稳。
#   不支持 CRON_TZ 的 crond 会自动退回 --local 模式并在输出里说明：
#   busybox（Docker alpine 等）和 Debian/Ubuntu 的 cron 3.0pl1（vixie）都不支持，
#   后者是最常见的一种，且它是 `cron` 而不是 `crond`，光按进程名认不出来。
# ============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

MARK_BEGIN="# >>> btc-swing-watch >>>"
MARK_END="# <<< btc-swing-watch <<<"

DRY=0
FORCE_LOCAL=0
REMOVE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --local)   FORCE_LOCAL=1 ;;
    --remove)  REMOVE=1 ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
  shift
done

# 读 .env 里的槽位（脚本内部记录用，这里也要用同一份值，避免两处不一致）
if [ -f "$HERE/.env" ]; then
  set -a; . "$HERE/.env"; set +a
fi
SLOT_ASIA="${SLOT_ASIA:-00:30}"
SLOT_EUUS="${SLOT_EUUS:-14:00}"

say()  { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }

command -v crontab >/dev/null 2>&1 || {
  echo "[中止] 没有 crontab 命令。请先安装 cron："
  echo "  Debian/Ubuntu: sudo apt install -y cron && sudo systemctl enable --now cron"
  echo "  RHEL/CentOS:   sudo yum install -y cronie && sudo systemctl enable --now crond"
  exit 2
}

# ---------------------------------------------------------------- 时区
# 本机相对 UTC 的偏移（分钟）
Z="$(date +%z)"                       # 形如 +0800 / -0430 / +0000
SIGN="${Z:0:1}"; HH="${Z:1:2}"; MM="${Z:3:2}"
OFF=$(( (10#$HH * 60 + 10#$MM) ))
[ "$SIGN" = "-" ] && OFF=$(( -OFF ))
TZ_NAME="$(cat /etc/timezone 2>/dev/null || true)"
[ -z "$TZ_NAME" ] && TZ_NAME="$(date +%Z 2>/dev/null || true)"
[ -z "$TZ_NAME" ] && TZ_NAME="未知时区"

# CRON_TZ 支持性检测。不支持就必须退回本地时间换算 —— 否则条目会按本地时区
# **静默**执行：时段整体偏移，且没有任何东西会报错（额度还在同一 UTC 日内，
# 所以连额度耗尽这种可观测症状都不会出现）。
#   · busybox crond（Docker alpine 等）           → 不支持
#   · Debian/Ubuntu 的 cron 3.0pl1（vixie）        → 不支持，且进程名是 `cron`
#                                                    不是 `crond`，光看名字认不出
#   · cronie（RHEL / CentOS 系）                   → 支持
# 检测不出来时一律退回本地时间（保守方向：对没有夏令时的时区，本地换算永远正确）。
USE_TZ_UTC=1
TZ_REASON=""
[ "$FORCE_LOCAL" -eq 1 ] && { USE_TZ_UTC=0; TZ_REASON="用户指定 --local"; }
if [ "$USE_TZ_UTC" -eq 1 ]; then
  if readlink -f "$(command -v crond 2>/dev/null || echo /nonexistent)" 2>/dev/null | grep -q busybox; then
    USE_TZ_UTC=0
    TZ_REASON="busybox crond 不支持 CRON_TZ"
  fi
fi
if [ "$USE_TZ_UTC" -eq 1 ]; then
  # 找 crond 二进制（它常在 /usr/sbin，普通用户 PATH 里可能没有）
  CRONBIN="$(command -v cron 2>/dev/null || command -v crond 2>/dev/null || true)"
  if [ -z "$CRONBIN" ]; then
    for p in /usr/sbin/cron /usr/sbin/crond /sbin/cron /sbin/crond; do
      [ -x "$p" ] && { CRONBIN="$p"; break; }
    done
  fi
  SUPPORTS=0
  if [ -n "$CRONBIN" ] && grep -qa CRON_TZ "$CRONBIN" 2>/dev/null; then
    SUPPORTS=1
  elif man 5 crontab 2>/dev/null | grep -qi CRON_TZ; then
    SUPPORTS=1
  fi
  if [ "$SUPPORTS" -eq 0 ]; then
    USE_TZ_UTC=0
    TZ_REASON="未在本机 crond（${CRONBIN:-未找到}）与 crontab(5) 里找到 CRON_TZ 支持"
  fi
fi

# UTC 的 HH:MM → 本机时区的 HH:MM
to_local() {
  local h="${1%%:*}" m="${1##*:}"
  local t=$(( (10#$h * 60 + 10#$m + OFF) % 1440 ))
  [ "$t" -lt 0 ] && t=$(( t + 1440 ))
  printf '%02d:%02d' $(( t / 60 )) $(( t % 60 ))
}

if [ "$USE_TZ_UTC" -eq 1 ]; then
  say "时区：本机 $TZ_NAME (UTC${Z})  → 使用 CRON_TZ=UTC，条目按 UTC 解释"
  CRON_ASIA="$SLOT_ASIA"
  CRON_EUUS="$SLOT_EUUS"
else
  CRON_ASIA="$(to_local "$SLOT_ASIA")"
  CRON_EUUS="$(to_local "$SLOT_EUUS")"
  say "时区：本机 $TZ_NAME (UTC${Z})  → 不支持 CRON_TZ，换算成本地时间"
  [ -n "$TZ_REASON" ] && warn "原因：$TZ_REASON"
  warn "注意：若本机时区有夏令时，换季时需重新执行本脚本"
fi
# 给验收用：crontab 里的时刻到底按谁的时区解释
if [ "$USE_TZ_UTC" -eq 1 ]; then
  TZMODE="cron 按 UTC 解释"
else
  TZMODE="本机 $TZ_NAME (UTC${Z})"
fi

A_H="${CRON_ASIA%%:*}"; A_M="${CRON_ASIA##*:}"
E_H="${CRON_EUUS%%:*}"; E_M="${CRON_EUUS##*:}"

# ---------------------------------------------------------------- 组装
# cron 的分钟字段：为 0 时写 "0 H" 而不是 "00 H"，两种都合法，前者更常见。
A_MIN="$A_M"; [ "$A_MIN" = "00" ] && A_MIN="0"
E_MIN="$E_M"; [ "$E_MIN" = "00" ] && E_MIN="0"

LINE_ASIA="$A_MIN $A_H * * * cd $HERE && ./run_session.sh asia >> logs/cron.log 2>&1"
LINE_EUUS="$E_MIN $E_H * * * cd $HERE && ./run_session.sh euus >> logs/cron.log 2>&1"

BLOCK="$MARK_BEGIN
# BTC 波段结构 + 外部 AI 对比  每天两次
# 排班依据见 README.md（session_vol.py 对 3315 个配对日的实测结论）"
if [ "$USE_TZ_UTC" -eq 1 ]; then
  BLOCK="$BLOCK
CRON_TZ=UTC"
fi
BLOCK="$BLOCK
$LINE_ASIA
$LINE_EUUS
$MARK_END"

# ---------------------------------------------------------------- 读旧表并摘掉本包的段
CUR="$(crontab -l 2>/dev/null || true)"
TMP="$(mktemp)"
NEW="$(mktemp)"
trap 'rm -f "$TMP" "$TMP.clean" "$NEW"' EXIT

printf '%s\n' "$CUR" | awk -v b="$MARK_BEGIN" -v e="$MARK_END" '
  $0 == b { skip = 1; next }
  $0 == e { skip = 0; next }
  !skip   { print }
' > "$TMP"

if [ "$REMOVE" -eq 0 ]; then
  # 保留用户原有内容，末尾补**恰好一个**空行再接本包的段。
  # 这里必须确定化，否则连续执行两次会多出一个空行（幂等性被破坏）：
  #   ① 去掉行尾空白
  #   ② 折叠连续空行（最多保留一个）
  #   ③ 去掉尾部空行
  #   ④ 统一补一个空行 + 本包段
  {
    sed -e 's/[[:space:]]*$//' "$TMP" \
      | awk 'NF || prev { print } { prev = NF }' \
      | awk '{ line[NR] = $0; if ($0 ~ /[^[:space:]]/) last = NR }
             END { for (i = 1; i <= last; i++) print line[i] }' > "$TMP.clean"
    if [ -s "$TMP.clean" ]; then
      cat "$TMP.clean"
      printf '\n%s\n' "$BLOCK"
    else
      printf '%s\n' "$BLOCK"
    fi
  } > "$NEW"
  rm -f "$TMP.clean"
else
  cp "$TMP" "$NEW"
fi

say "将写入的 crontab 片段："
echo "----------------------------------------"
if [ "$REMOVE" -eq 1 ]; then
  echo "(仅删除本包注册的段落，其余内容保留)"
else
  printf '%s\n' "$BLOCK"
fi
echo "----------------------------------------"

if [ "$DRY" -eq 1 ]; then
  say "dry-run：未写入。去掉 --dry-run 即生效。"
  exit 0
fi

crontab "$NEW" || { echo "[失败] crontab 写入失败" >&2; exit 1; }

if [ "$REMOVE" -eq 1 ]; then
  ok "已移除本包的定时任务"
else
  ok "已注册定时任务"
  echo
  echo "排班对照（验收时照抄，不要凭记忆换算）："
  printf '  asia  crontab 写 %s   ← 按 %s ← 目标 %s UTC\n' \
         "$CRON_ASIA" "$TZMODE" "$SLOT_ASIA"
  printf '  euus  crontab 写 %s   ← 按 %s ← 目标 %s UTC\n' \
         "$CRON_EUUS" "$TZMODE" "$SLOT_EUUS"
  echo "  （实例：本机 Asia/Shanghai 且不支持 CRON_TZ → 写 30 08 / 0 22）"
  echo
  say "当前 crontab："
  crontab -l | sed 's/^/  /'
  echo
  echo "验证方式："
  echo "  date -u                      # 当前 UTC 时间"
  echo "  ls -l $HERE/logs/             # 跑过之后会有 run_YYYYMMDD.log"
  echo "  tail -f $HERE/logs/cron.log   # 实时看 cron 输出"
fi
