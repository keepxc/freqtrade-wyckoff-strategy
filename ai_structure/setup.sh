#!/usr/bin/env bash
# ============================================================================
# 一次性环境准备：建 venv、装依赖、检查字体与数据、跑一次自检。
#
#   ./setup.sh                     # 正常安装
#   ./setup.sh --mirror            # 用国内 PyPI 镜像（阿里云）加速
#   ./setup.sh --python python3.12 # 指定解释器
#
# 幂等：重复执行只会重建/更新 venv，不会动 data/ 与 out/ 里的东西。
# ============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE" || exit 2

MIRROR=""
WANT_PY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --mirror) MIRROR="https://mirrors.aliyun.com/pypi/simple/" ;;
    --python) shift; WANT_PY="${1:-}" ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
  shift
done

say()  { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; }

# ---------------------------------------------------------------- 1. 解释器
say "1/6  检查 Python 解释器"

# 为什么要求 3.9+：代码本身只用标准库与 numpy/pandas/Pillow 的稳定 API，
# 但 pandas 2.x 需要 3.9+，且我已在源码里清掉了 3.12 专有的 f-string 嵌套写法
# （PEP 701）并给 datetime.UTC（3.11+）加了兼容补丁。
MIN_MINOR=9
pick_py() {
  for c in "$WANT_PY" python3.13 python3.12 python3.11 python3.10 python3.9 python3; do
    [ -z "$c" ] && continue
    p="$(command -v "$c" 2>/dev/null)" || continue
    v="$("$p" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null)" || continue
    maj="${v%%.*}"; min="${v##*.}"
    if [ "$maj" -eq 3 ] && [ "$min" -ge "$MIN_MINOR" ]; then echo "$p"; return 0; fi
  done
  return 1
}

SYS_PY="$(pick_py)" || {
  bad "找不到 Python 3.${MIN_MINOR}+。"
  echo
  echo "  Ubuntu/Debian：  sudo apt update && sudo apt install -y python3 python3-venv python3-pip"
  echo "  CentOS/RHEL：    sudo yum install -y python3"
  echo "  若系统只有 3.8（如 CentOS 7 / Ubuntu 18.04），建议改用 pyenv 或 conda。"
  exit 2
}
ok "系统解释器 $SYS_PY ($("$SYS_PY" -c 'import sys;print(sys.version.split()[0])'))"

# ---------------------------------------------------------------- 2. venv
say "2/6  创建虚拟环境 .venv"
if [ -d "$HERE/.venv" ]; then
  ok "已存在，复用"
else
  "$SYS_PY" -m venv "$HERE/.venv" || {
    bad "venv 创建失败。多半是缺 python3-venv："
    echo "  sudo apt install -y python3-venv"
    exit 2
  }
  ok "已创建"
fi
PY="$HERE/.venv/bin/python"
[ -x "$PY" ] || { bad "$PY 不可执行"; exit 2; }

# ---------------------------------------------------------------- 3. 依赖
say "3/6  安装依赖"
"$PY" -m pip install -q --upgrade pip >/dev/null 2>&1 || warn "pip 自升级失败（不致命）"
PIPARGS=(-q -r "$HERE/requirements.txt")
[ -n "$MIRROR" ] && PIPARGS+=(-i "$MIRROR")
if "$PY" -m pip install "${PIPARGS[@]}"; then
  ok "numpy / pandas / Pillow 就绪"
else
  bad "依赖安装失败。若在墙内，重试： ./setup.sh --mirror"
  exit 2
fi

# ---------------------------------------------------------------- 4. 字体
say "4/6  检查中文字体（图表标题与栏目标签要用）"
FONT_HIT="$("$PY" - <<'PYEOF'
import os
cands = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
]
for p in cands:
    if os.path.exists(p):
        print(p); break
PYEOF
)"
if [ -n "$FONT_HIT" ]; then
  ok "$FONT_HIT"
else
  warn "未找到任何中文字体 → 图上中文会显示成方块（不影响数值与结构）。"
  echo "     Ubuntu/Debian：sudo apt install -y fonts-noto-cjk"
  echo "     CentOS/RHEL：  sudo yum install -y wqy-zenhei-fonts"
fi

# ---------------------------------------------------------------- 5. 目录与数据
say "5/6  目录与数据"
mkdir -p "$HERE/data" "$HERE/out" "$HERE/logs"
ok "data/ out/ logs/ 就绪"

CSV="${CSV_NAME:-BTCUSDT_1h.csv}"
if [ -s "$HERE/data/$CSV" ]; then
  LINES="$(wc -l < "$HERE/data/$CSV")"
  FIRST="$(sed -n '2p' "$HERE/data/$CSV" | cut -d, -f2)"
  LAST="$(tail -1 "$HERE/data/$CSV" | cut -d, -f2)"
  ok "data/$CSV  $((LINES - 1)) 根   $FIRST → $LAST"
else
  warn "data/$CSV 不存在。首次需要全量下载（约 80 次请求，1~3 分钟）："
  echo "     ./run_session.sh manual --no-ai     # 会自动增量补齐"
  echo "     或： .venv/bin/python download_data.py"
fi

if [ -f "$HERE/.env" ]; then
  ok ".env 已存在"
else
  warn ".env 不存在 → 无法调用外部 AI。要调 AI 就执行："
  echo "     cp .env.example .env  &&  \$EDITOR .env   # 填 AI_EMAIL / AI_PASSWORD"
fi

# ---------------------------------------------------------------- 6. 自检
say "6/6  自检：跑一次纯本地分析（不联网、不调 AI）"
if [ -s "$HERE/data/$CSV" ]; then
  if ( cd "$HERE" && "$PY" swing_watch.py --quiet >/dev/null 2>&1 ); then
    ok "结构分析通过"
  else
    warn "结构分析返回非 0（数据不足时正常）。单独看原因："
    echo "     .venv/bin/python swing_watch.py | tail -30"
  fi
  if ( cd "$HERE" && "$PY" make_chart.py >/dev/null 2>&1 ); then
    ok "出图通过  →  out/chart_dual.png"
  else
    warn "出图失败，检查 Pillow 与字体"
  fi
  echo
  echo "  ── 交付自检（47 项：数据完整性 / 算法不变量 / 判别力 / 边界夹具）"
  if ( cd "$HERE" && "$PY" selftest.py 2>&1 | tail -8 ); then
    :
  fi
  echo
  echo "  ── 前向观察模块自检（记录 / 打分口径）"
  ( cd "$HERE" && "$PY" selftest_forward.py 2>&1 | tail -3 ) || true
else
  warn "无数据，跳过自检"
fi

echo
say "完成。接下来："
echo "  1) 补数据（若上面提示不存在）：  ./run_session.sh manual --no-ai"
echo "  2) 登录 AI 换 token：           .venv/bin/python ai_login.py"
echo "  3) 手动试跑一次完整流程：        ./run_session.sh manual"
echo "     （manual 不进前向样本，但会消耗 1 次 AI 额度）"
echo "  4) 确认没问题后注册定时任务：    ./install_cron.sh"
echo
echo "前向观察的判据与流程见 FORWARD_PROTOCOL.md；部署交接见 AGENT_TASK.md"
echo
echo "详细说明（排班依据、额度机制、排错）见 README.md"
