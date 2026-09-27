# -*- coding: utf-8 -*-
"""集中配置。所有路径都相对本文件所在目录，便于整体搬迁。

环境变量优先于这里的默认值，见 .env.example。
"""
import datetime as _datetime
import os

# ---------------------------------------------------------------- 兼容层
# datetime.UTC 是 Python 3.11 才加进来的常量，早期版本只有 datetime.timezone.utc。
# 这个补丁放在这里而不是各脚本里：config.py 被每个脚本在顶部 import，
# 而所有对 datetime.UTC 的使用都在函数体内（import 之后才会执行），
# 所以补丁一定先于使用生效。目标机若是 Ubuntu 22.04（Python 3.10）也不会挂。
if not hasattr(_datetime, "UTC"):
    _datetime.UTC = _datetime.timezone.utc

HERE = os.path.dirname(os.path.abspath(__file__))


def _env(key, default):
    v = os.environ.get(key)
    return v if v not in (None, "") else default


# ---------------------------------------------------------------- 路径
DATA_DIR = os.path.join(HERE, "data")
OUT_DIR = _env("OUT_DIR", os.path.join(HERE, "out"))
LOG_DIR = os.path.join(HERE, "logs")
CSV = os.path.join(DATA_DIR, _env("CSV_NAME", "BTCUSDT_1h.csv"))
TOKEN_FILE = os.path.join(HERE, ".token")      # JWT，约 7 天有效；已 .gitignore
# 剩余有效期不足这么多小时就提前续期。每天跑两次，24h 余量保证不会在两次之间过期。
TOKEN_RENEW_H = float(_env("TOKEN_RENEW_H", "24"))

# ---------------------------------------------------------------- 数据源
SYMBOL = _env("SYMBOL", "BTCUSDT")
INTERVAL = _env("INTERVAL", "1h")
# 币安公开数据镜像（不需要 API key）。中国大陆访问可能需要代理。
BINANCE_KLINES = _env("BINANCE_KLINES",
                      "https://data-api.binance.vision/api/v3/klines")
# 代理：留空则直连。例：http://127.0.0.1:7890 或 socks5://127.0.0.1:1080
PROXY = _env("HTTPS_PROXY", _env("PROXY_URL", ""))

# ---------------------------------------------------------------- 结构分析
THETA = float(_env("THETA", "0.30"))          # ZigZag 主阈值
THETA_ALT = float(_env("THETA_ALT", "0.20"))  # 稳健性阈值
HORIZONS = [int(x) for x in
            _env("HORIZONS", "7,15,30,60,90,180,365").split(",")]
MIN_INDEP = int(_env("MIN_INDEP", "15"))      # 独立样本下限

# ---------------------------------------------------------------- 外部 AI
AI_BASE = _env("AI_BASE", "https://ai.yss-signal.com")
AI_LOGIN = AI_BASE + "/login"
AI_CHART = AI_BASE + "/api/chart-agent"
AI_QUOTA = AI_BASE + "/api/chart-agent/quota"
AI_ORIGIN = _env("AI_ORIGIN", "https://www.yss-signal.com")
AI_UA = _env("AI_UA",
             "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
# 账号（登录用）。也可放在 .env 里，避免写进代码。
AI_EMAIL = _env("AI_EMAIL", "")
AI_PASSWORD = _env("AI_PASSWORD", "")
# 是否把程序算出的结构状态拼进 AI 提示词。前向观察期默认 0（关）：
# 要测的是「程序与 AI 各自独立时是否一致」，喂了就变成 AI 复述程序，一致率虚高。
AI_PROMPT_STATE = _env("AI_PROMPT_STATE", "0") == "1"

# ---------------------------------------------------------------- 前向观察（判据已冻结，见 FORWARD_PROTOCOL.md）
# 改这里的任何一个值 = 换了判据。冻结日 2026-09-27，冻结后不得改。
FWD_PRIMARY_H = 7            # 主判据兑现期限（天）
FWD_SECONDARY_H = 30         # 副判据（只记录，不参与决策）
FWD_BASE_RATE = 0.55         # 实盘门槛对照的基准率（7 天无条件 P(涨) 历史值）
FWD_MIN_INDEP = 30           # 实盘门槛：三者一致的独立窗口数下限
FWD_CHECKPOINT_DAYS = 14     # 管线检查点（两周）
FWD_PIPE_OK = 0.90           # 检查点：管线成功率下限
FWD_PARSE_OK = 0.90          # 检查点：AI 结构化解析成功率下限

# ---------------------------------------------------------------- 排班（UTC）
# 依据 session_vol.py 的实测：欧美重叠 13:00-16:00 UTC 波动最高（归一 1.309），
# 峰值在 14:00 UTC；亚盘 00:00-08:00 UTC 最低（0.924）。
# 额度每天 08:00 北京时间（= 00:00 UTC）重置，因此第一次必须排在其后。
SLOT_ASIA = _env("SLOT_ASIA", "00:30")   # 亚盘：东京开盘后，且额度刚重置
SLOT_EUUS = _env("SLOT_EUUS", "14:00")   # 欧美：重叠区峰值小时

# ---------------------------------------------------------------- 图表
CHART_W = int(_env("CHART_W", "1560"))
# 日线展示窗口（天）与 4H 展示窗口（天）
DAILY_DAYS = int(_env("DAILY_DAYS", "430"))
H4_DAYS = int(_env("H4_DAYS", "60"))
