"""
AI500V3ScoreHold — AI500 score 榜 top3 入场 + 4h 阴线出场
==========================================================
探索性部署——**不是已验证的策略**。判据未预注册，禁止引用本策略的实盘
数字为结论，除非事先冻结判据并跑独立 OOS 窗口。

设计依据（2026-09-05 用户拍板）：
  - ai500.txt 原文：「只买 AI500 评分最高的前 3 个币」+「4h 收盘阴线止盈」
  - AI500_EXIT_4H_PREREG.md（出场读法 (a) 字面 + MAX_HOLD 48h 兜底）
  - 与 V2（AI500V2FlowHold）的差异：V2 用 net_flow 榜作**代理**，本策略
    用 AI500 本尊 **score 榜**（/trending-category?key=ai500）作信号源。

为什么长这样
------------
V2 已证明「net_flow 榜 = 大市值择时代理」（前三被 BTC/ETH/SOL 占满）。
V3 回归 ai500.txt 的本尊信号：score 榜无参调用、按 score 降序 top3，
且 **score >= 80 才允许开新仓（60~80 只观察不进场）**。
其余全部沿用「最少自由参数」原则：
  - 出场 = 首根「收盘时刻严格晚于入场」的 4h 阴线平仓（close<open），
    MAX_HOLD 48h 强制离场兜底，5% 单笔止损。不加止盈/移动止损/信号叠加。
  - 止损是盘中实时触发的（freqtrade dry-run 每 process_throttle_secs=30s
    用实时 ticker 价检查 stoploss，非 4h K 收盘粒度；4h 收盘粒度只是
    backtesting 的行为，本策略不可回测故不适用）。
  - 杠杆 1x（等权，与 V2 口径一致才能比）。
  - 订单簿定价关闭（use_order_book:false，429 真凶是 fetch_l2_order_book）。

僵尸行过滤（用户拍板口径）
--------------------------
score 榜只返回「当前还活着」的信号，但其中混着「还挂着的死信号」
（startTime 陈旧、change_pct_value 卡住不再随价格更新）。

**当前只有一道判据在岗**：币安最新价 vs 接口 price 差 > 1pp → 判僵尸。
即只校验「接口报的**当前价**是不是还活着」。

曾经的两道主判据已先后降级为纯观测（继续算、继续落盘，不参与判定）：
  · diff_pp     —— 比涨幅百分点，2026-09-11 降级（误差被累计涨幅放大，误杀 83.4%）
  · ref_diff_pp —— 比起始价本身，2026-09-18 降级（见 §门③方案C）

门③方案C（2026-09-18 用户拍板，**B 类改动，🔒 未封板**）
-----------------------------------------------------------
把「起始价偏离」这道门取消，只留实时价校验。

依据（离线重放 ai500_score_history_v3 + ai500_zombie_check，3721 行 / 1481 轮）：
  · 入场漏斗 门①100% → 门②(score>=80) 53.1% → **门③ 8.0%**，门③是唯一瓶颈。
  · ref_diff_pp 在同一 (symbol, start_time) episode 内**恒为常数**
    （ZEC 10.70 / BR 9.22 / LSK 1.87 / AIN 2.47）——它不是持续复核的实时判据，
    而是 episode 诞生时的一次性判决，判完把该币整段封禁。
  · 占全部评估 57.9% 的 ZEC(1411 次)+BR(737 次) 通过率 **0.0%**，而这两个恰是
    分数最高的（峰值 92.61 / 89.36）。ZEC 的预筛是过的（币安 1182.05 vs
    接口 1180.28 = 0.15pp），实时数据完全新鲜——拒它的唯一理由是一个
    **122 天前的历史起始价**。
  · 放行率 39.3% → 96.2%。原速率 13 天成交 2 笔，30 笔判据需 ~195 天，
    实验结构上跑不完；改后约 5 周可达 30 笔。

**冻结判据（先于数据，禁止事后调整）**：
  主判据 = 本改动生效后**前 30 笔已平仓交易的累计净收益（含手续费）**
    > 0      → 门③方案C 通过，可转入封板讨论
    <= 0     → 否决，回滚门③
  提前中止闸 = 累计净收益 <= -20% 时不等满 30 笔直接停（≈4 笔满额止损）
  只记录不作判据：胜率、持仓时长、出场原因分布、MAE 分布
  功效边界：单笔净收益 sd ≈ 2.6~5%，n=30 的 MDE ≈ +1.2~2.3%/笔。
    **30 笔够否决，不够确认小 edge。** 若结果落在零附近，只能写
    「无 ≥1.2%/笔 量级的效应」，不得写成「策略有效/无效」。

本轮**只改门③**。止损档与出场周期一律不动——离线网格显示放宽止损单调变差
（4h/-5% 总净 -17.36% → 无止损 -36.45%），缩短周期唯一 CI 下界>0 的 1h
是 9 次比较里的预期假阳性；更重要的是同时改两处会让 30 笔前向样本无法归因。
（该网格 n=8 episode、4 个是 ZEC，**探索性，非结论**。）

top3 里有币安没有的（ACX/HFT/SCRT）跳过、不回补第 4 名，miss 记日志。
top3 中的僵尸行同样**不回补**（顺位递补是 ai500_v3_复刻设计.md 的备选，
用户 2026-09-05 拍板不递补——递补会悄悄改规则，miss 记日志事后量化）。

回测限制
--------
同 V2：score 榜是实时快照，populate_indicators 把当前快照广播到整段
dataframe，freqtrade backtesting 出来的任何数字都无意义。验证只能靠
dry-run 实跑 + 事后离线重放（僵尸行过滤结果逐次落盘，事后可量化 miss 率）。

落盘（全部在 user_data/，路径经 AI500_DATA_DIR 可覆盖）
-------------------------------------------------------
  ai500_score_history_v3.csv   —— score 榜快照（独立于 V2 的同名文件，避免
                                 两进程并行写同一 CSV 交错）
  ai500_netflow_multiwin.csv   —— net_flow 五档（无参默认 + 5m/15m/30m/1h），
                                 纯资金流辅助验证，**不参与入场**
  ai500_zombie_check.csv       —— 每次 top3 过滤结果（判据、复算涨幅、是否剔除）
"""
import csv
import json
import logging
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

import ccxt
import pandas as pd
import requests
from pandas import DataFrame

from freqtrade.strategy import IStrategy

logger = logging.getLogger(__name__)

# 东八区北京时间（用户约定：所有对话/数据时间均为 CST/UTC+8）
CST = timezone(timedelta(hours=8))

# 数据落盘目录 = 本策略文件所在的 user_data/（strategies/ 的上一级）。
# 与 V2 同款推导：bot 原生环境（.../user_data/strategies/）与容器
# （/freqtrade/user_data/strategies/）下都指向同一个 user_data。
_AI500_UD = os.environ.get(
    "AI500_DATA_DIR",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

CATEGORY_URL = "https://vergex.trade/trending-category"   # AI500 score 榜（本尊）

# ⚠️ 2026-09-13: vergex 启用 Cloudflare 托管挑战——**短 UA（"Mozilla/5.0" / python-requests 等）一律 403**
# （响应体 "Just a moment..."，实测旧头 0/12 通过）。必须带完整浏览器头指纹
# （UA + Accept + Accept-Language + Origin + Referer + Sec-Fetch-*），实测 20/20 通过。
# 本常量替换原 headers={"User-Agent": "Mozilla/5.0"}——两处调用（score 榜 + net_flow）共用。
VERGEX_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Origin": "https://vergex.trade",
    "Referer": "https://vergex.trade/explore",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}
TRENDING_URL = "https://vergex.trade/trending-crypto"      # net_flow 榜（五档落盘用）
# 容器内 127.0.0.1 指向容器自身，拿不到宿主机代理；用 AI500_PROXY 覆盖
# （docker 下设 http://host.docker.internal:7890）。不设时与原行为一致。
_AI500_PROXY = os.environ.get("AI500_PROXY", "http://127.0.0.1:7890")
TRENDING_PROXY = {"http": _AI500_PROXY, "https": _AI500_PROXY}
FETCH_TIMEOUT = 10


def _vergex_get_json(url: str, params: dict | None = None) -> dict:
    """GET vergex JSON —— **必须用 urllib**。

    2026-09-13 实测：相同完整浏览器头下，requests（含默认 / Session / identity-encoding
    三种变体）一律 403 挑战页（6007B "Just a moment..."），urllib 稳定 200（1470B JSON）。
    CF 判的是**客户端指纹**（非 header 内容）——所以外层换 requests→urllib 是修复的关键，
    只补 header 不够。改回 requests 会重新撞 403。
    """
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=VERGEX_HEADERS)
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": _AI500_PROXY, "https": _AI500_PROXY}))
    with opener.open(req, timeout=FETCH_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))
LIST_LIMIT = 100    # net_flow 榜深度（API 实测上限 100；score 榜不受此参数影响）

# score 榜历史。注意：V2 已写 ai500_score_history.csv，V3 与 V2 可能并行运行，
# 两进程 append 同一文件会交错损坏；且两进程采集内容完全相同（同一接口），
# 重复写无意义。故 V3 用独立文件，字段与 V2 完全一致，事后可合并分析。
SCORE_HISTORY_PATH = os.path.join(_AI500_UD, "ai500_score_history_v3.csv")
SCORE_HISTORY_FIELDS = [
    "fetched_at_utc", "symbol", "pair", "score", "signal_peak", "price",
    "start_price", "start_time", "change_pct_value",
]
SCORE_FETCH_INTERVAL_S = 300   # score 每小时刷新（generatedAt ~30s 变但 score 低频），5 分钟采样足够

# net_flow 五档落盘（纯辅助验证，不参与入场）。
# 「五档」= 无参默认档 + 5m/15m/30m/1h（4h 以上实测会整池换币，不采）。
NETFLOW_MULTIWIN_PATH = os.path.join(_AI500_UD, "ai500_netflow_multiwin.csv")
NETFLOW_MULTIWIN_FIELDS = [
    "fetched_at_utc", "duration", "rank", "symbol", "amount", "price", "price_delta_percent",
]
NETFLOW_DURATIONS = [None, "5m", "15m", "30m", "1h"]   # None = 无参默认档
NETFLOW_POLL_SEC = 60

# 僵尸行过滤结果逐次落盘（事后量化 miss 率 / 判据是否稳健）
ZOMBIE_PATH = os.path.join(_AI500_UD, "ai500_zombie_check.csv")
ZOMBIE_FIELDS = [
    "fetched_at_utc", "symbol", "pair", "score", "rank",
    "interface_price", "interface_change_pct", "start_time",
    "binance_price", "interface_start_price", "ref_1m", "ref_diff_pp",
    "start_open", "recomputed_pct", "diff_pp",
    "prefilter_hit", "is_zombie", "tradable", "reason",
]

# 僵尸行过滤阈值
ZOMBIE_PREFILTER_PP = 1.0    # 【唯一在岗的判据】币安最新价 vs 接口 price 差 >1pp 判僵尸（2026-09-05 用户拍板）
ZOMBIE_REF_PP = 3.0          # 【2026-09-18 降级为纯观测】接口 start_price vs start_time 那根 1m K 开盘
ZOMBIE_DIFF_PP = 3.0         # 【2026-09-11 降级为纯观测】更旧的「比涨幅百分点」口径
# ↑ 两个降级阈值都**保留常量与计算**：列继续落盘，供日后回答「当初那道门该不该关」。
#   删掉计算就永久失去审计能力——30 笔前向样本跑完也说不清放行的里面有多少真僵尸。

# 门③ 口径变更时点。**副作用条款：此刻前后的样本不得混池统计。**
GATE3_C_EFFECTIVE_UTC = "2026-09-18"


class AI500V3ScoreHold(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = '4h'
    can_short = False
    process_only_new_candles = True
    startup_candle_count = 100

    # 5% 单笔止损（与 V2、ai500.txt 的 stop_loss_pct=5 对齐）。纯风控，不是收益手段。
    stoploss = -0.05
    # 2880 分钟 = 48h 强制离场兜底。这是数据可得性约束（离线预注册同款），
    # 不是策略原意——原 prompt 无持有上限。
    minimal_roi = {"0": 100, "2880": -1}
    use_exit_signal = True        # 4h 阴线出场走 exit signal（populate_exit_trend）
    exit_profit_only = False
    ignore_roi_if_entry_signal = False
    trailing_stop = False         # 不加移动止损（V2 已证明三个出场机制配对差全跨零）
    use_custom_stoploss = False
    max_open_trades = 3           # score top3 全取；不再用 V2 的 8（那是 net_flow 全取口径）

    TOP_N = 3                     # 只买 AI500 评分最高的前 3 个币（ai500.txt 原文）
    MIN_SCORE = 80                # 入场门槛：score >= 80 才允许开新仓，60~80 只观察不进场
                                  #   （2026-09-05 用户拍板；ai500_v3_复刻设计.md §1 消融项 A 建议 V3 直接带上）
    RANK_ABSENT = 99              # 不在 score top3 时的 rank 占位值

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.ai500_scores: dict[str, dict] = {}   # symbol -> 最新 score 榜行（无 USDT 后缀）
        # symbol -> 入场决策状态（top3 过滤后：rank/score/tradable/is_zombie/reason/...）
        self._entry_state: dict[str, dict] = {}
        self._last_score_fetch: float = 0.0
        self._last_netflow_fetch: float = 0.0
        # 币安客户端（fetch_ticker/fetch_ohlcv 用于僵尸行过滤 + 合约存在性判断）
        # 懒加载：启动时可能因代理繁忙失败，运行中 _ensure_binance 会重试
        self.binance = None
        # 代理配置实际在 exchange.ccxt_config.proxies，不在 exchange.proxies（None）
        self.binance_proxies = (config.get('exchange', {}).get('ccxt_config', {})
                                .get('proxies', {}))
        if not self.binance_proxies:
            self.binance_proxies = {"http": "http://127.0.0.1:7890",
                                    "https": "http://127.0.0.1:7890"}
        self._init_binance()

    # ------------------------------------------------------------------ #
    # 币安客户端
    # ------------------------------------------------------------------ #
    def _init_binance(self) -> None:
        if self.binance is not None:
            return
        try:
            self.binance = ccxt.binanceusdm({
                'proxies': self.binance_proxies,
                'timeout': 30000,
                'enableRateLimit': True,
                'options': {'defaultType': 'future'}
            })
            last_err = None
            for attempt in range(3):
                try:
                    self.binance.fetch_time()
                    logger.info("Binance client initialized for zombie-row check")
                    return
                except Exception as e:
                    last_err = e
                    logger.warning(f"Binance client init attempt {attempt+1}/3 failed: {e}")
                    time.sleep(5)
            raise last_err if last_err else Exception("init failed")
        except Exception as e:
            logger.warning(f"Binance client init failed (will retry on next use): {e}")
            self.binance = None

    def _ensure_binance(self) -> bool:
        if self.binance is None:
            self._init_binance()
        return self.binance is not None

    # ------------------------------------------------------------------ #
    # 值解析工具
    # ------------------------------------------------------------------ #
    @staticmethod
    def _parse_price(v) -> float | None:
        """'$4.63' / '$80,956.9' / 4.63 -> float；无法解析返回 None。"""
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v).replace("$", "").replace(",", "").strip()
        try:
            return float(s)
        except ValueError:
            return None

    @staticmethod
    def _to_float(v) -> float | None:
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_int(v) -> int | None:
        if v is None:
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _pair_to_base(pair: str) -> str:
        # "BTC/USDT:USDT" -> "BTC"（与 score 接口 symbol 口径一致，无 USDT 后缀）
        return pair.split('/')[0]

    # ------------------------------------------------------------------ #
    # score 榜采集（本尊信号）
    # ------------------------------------------------------------------ #
    def _fetch_ai500_scores(self) -> list[dict] | None:
        """抓取 AI500 score 榜（/trending-category?key=ai500），**无参**调用。

        返回 assets 列表，每项含 score/price/start_price/start_time/change_pct_value，
        数值字段已解析为 float/int。失败返回 None（调用方保留上次状态）。
        """
        try:
            data = _vergex_get_json(CATEGORY_URL, {"lang": "zh", "key": "ai500"})
            assets = (data.get("category") or {}).get("assets") or []
            out = []
            for a in assets:
                sym = a.get("symbol")
                if not sym:
                    continue
                out.append({
                    "symbol": sym,
                    "pair": f"{sym}/USDT:USDT",
                    "score": self._to_float(a.get("score")),
                    "signal_peak": a.get("signal"),
                    "price": self._parse_price(a.get("price")),
                    "start_price": self._to_float(a.get("startPrice")),
                    "start_time": self._to_int(a.get("startTime")),
                    "change_pct_value": self._to_float(a.get("changePctValue")),
                })
            return out if out else None
        except Exception as e:
            logger.warning(f"[AI500V3] fetch score failed, keeping previous: {e}")
            return None

    # ------------------------------------------------------------------ #
    # net_flow 五档落盘（纯辅助验证，不参与入场）
    # ------------------------------------------------------------------ #
    def _fetch_netflow_multiwin(self) -> list[dict]:
        """拉 net_flow 五档（无参默认 + 5m/15m/30m/1h）。任一档失败跳过该档。"""
        rows = []
        for dur in NETFLOW_DURATIONS:
            params = {"tab": "net_flow", "limit": LIST_LIMIT}
            if dur is not None:
                params["duration"] = dur
            try:
                data = _vergex_get_json(TRENDING_URL, params)
            except Exception as e:
                logger.warning(f"[AI500V3] net_flow fetch duration={dur or 'default'} failed: {e}")
                continue
            label = dur or "default"
            for item in data.get("top") or []:
                sym = item.get("symbol")
                if not sym:
                    continue
                rows.append({
                    "duration": label,
                    "rank": item.get("rank"),
                    "symbol": sym,
                    "amount": item.get("amount"),
                    "price": item.get("price"),
                    "price_delta_percent": item.get("price_delta_percent"),
                })
        return rows

    # ------------------------------------------------------------------ #
    # 僵尸行过滤
    # ------------------------------------------------------------------ #
    def _start_open_1h(self, pair: str, start_time: int) -> float | None:
        """找「覆盖 start_time（秒级 epoch）那根 1h K」的 open。

        start_time 早于币安最早可用 1h K 时返回 None（无法复算 → 保守拦截）。
        """
        since_ms = (start_time - 3600) * 1000
        candles = self.binance.fetch_ohlcv(pair, "1h", since=since_ms, limit=2)
        target_ms = start_time * 1000
        for c in candles:
            open_ts = int(c[0])
            if open_ts <= target_ms < open_ts + 3600_000:
                return float(c[1])
        return None

    def _start_price_1m(self, pair: str, start_time: int) -> float | None:
        """找「覆盖 start_time（秒级 epoch）那根 1m K」的 open —— 信号起点的真实市价。

        为什么必须用 1m 而不是 1h（2026-09-11 实测，见下）：
        接口的 startPrice 记的是信号触发**那一刻**的价，1h 开盘价则最多早 59 分钟。
        能上 score 榜的标的按构造就是在快速拉升的，这段时间差被直接读成「对不上」。
        16 个 episode 的实测偏差中位：用 1h 开盘 1.03pp / p90 10.45pp，
        改用 1m 开盘后降到 0.64pp / p90 1.94pp；ARB 3.81→0.09、BULLA 19.38→0.58、
        FF 10.28→2.30、SOPH 10.62→1.14 —— 四个都是被口径差误杀的。
        """
        since_ms = (start_time - 60) * 1000
        candles = self.binance.fetch_ohlcv(pair, "1m", since=since_ms, limit=3)
        target_ms = start_time * 1000
        for c in candles:
            open_ts = int(c[0])
            if open_ts <= target_ms < open_ts + 60_000:
                return float(c[1])
        return None

    def _check_tradable(self, sym: str, row: dict) -> dict:
        """对 score 榜单行做「币安合约存在性 + 僵尸行」过滤，返回入场决策状态。

        顺序：fetch_ticker（顺带拿币安最新价）→ 预筛 1pp → **定判**；
        其后的起始价核算全部只落盘、不参与判定。

        **判定只看一件事**（2026-09-18 方案C，见模块 docstring §门③方案C）：
          接口报的**当前价**是不是还活着（与币安脱节 >1pp = 整行数据死了）。

        为什么起始价那道门被取消：`ref_diff_pp` 在同一 episode 内恒为常数，
        是「诞生即判决、判完整段封禁」的静态量，而不是实时健康度；它把占
        全部评估 57.9% 且分数最高的 ZEC/BR 以 0.0% 通过率全数拒掉，而这两个
        的**实时价校验是过的**。拒绝理由实为一个 122 天前的历史数字。

        拿不到实时价仍然拒（ticker_error / no_contract / no_binance_client）——
        **没法校验不等于可以放行**，这是方案C 与「完全取消门③」的唯一区别。

        反过来，起始价相关的取数失败（no_start_time / no_start_price /
        ref_1m_unavailable / ohlcv_error）现在**放行**：那些只是取不到一个
        已经不参与判定的观测量，不构成风险信号（2026-09-18 用户拍板）。
        """
        pair = f"{sym}/USDT:USDT"
        st = {
            "symbol": sym, "pair": pair, "score": row.get("score"),
            "interface_price": row.get("price"),
            "interface_change_pct": row.get("change_pct_value"),
            "start_time": row.get("start_time"),
            "binance_price": None,
            "interface_start_price": None, "ref_1m": None, "ref_diff_pp": None,
            "start_open": None, "recomputed_pct": None, "diff_pp": None,
            "prefilter_hit": 0, "is_zombie": 0, "tradable": 0, "reason": "",
        }
        if not self._ensure_binance():
            st["reason"] = "no_binance_client"
            return st
        # 1) 币安最新价。BadSymbol = 币安无此合约（ACX/HFT/SCRT 类）→ 跳过。
        try:
            ticker = self.binance.fetch_ticker(pair)
            last = ticker.get("last") or ticker.get("close")
            if last is None:
                st["reason"] = "ticker_no_last"
                return st
            last = float(last)
        except ccxt.BadSymbol:
            st["reason"] = "no_contract"
            return st
        except ccxt.RateLimitExceeded:
            st["reason"] = "rate_limit"
            return st
        except Exception as e:
            st["reason"] = f"ticker_error:{type(e).__name__}"
            return st
        st["binance_price"] = last
        # 2) 预筛：币安最新价 vs 接口 price 差 >1pp 直接判僵尸。
        iface_price = st["interface_price"]
        if iface_price and iface_price > 0:
            pp = abs(last - iface_price) / iface_price * 100.0
            if pp > ZOMBIE_PREFILTER_PP:
                st["prefilter_hit"] = 1
                st["is_zombie"] = 1
                st["reason"] = "prefilter_price_mismatch"
                return st
        # 3) 定判。预筛过 = 接口当前价还活着 → 可交易。
        #    判定到此为止，下面一律只落盘不改判。
        st["tradable"] = 1
        st["reason"] = "ok"
        self._record_ref_diag(st, pair, row)
        return st

    def _record_ref_diag(self, st: dict, pair: str, row: dict) -> None:
        """纯观测：算起始价相关的两代旧判据并落盘，**绝不改 tradable / is_zombie**。

        为什么取消判定资格后还要继续算：没有 ref_diff_pp / diff_pp 这两列，
        30 笔前向样本跑完后就无法回答「被方案C 放行的那些里，究竟有多少是真僵尸」——
        那正是评估门③该不该关的唯一证据来源。删计算 = 永久失去审计能力。

        失败一律吞掉，只在 reason 上加 `ok:<原因>` 后缀（tradable 保持 1）。
        观测量取不到不是风险信号。
        """
        st_time = st["start_time"]
        chg = st["interface_change_pct"]
        iface_price = st["interface_price"]
        if st_time is None:
            st["reason"] = "ok:no_start_time"
            return
        # 接口 startPrice 优先；缺失时用 price / (1 + change_pct) 反推。
        iface_start = row.get("start_price")
        if not iface_start or iface_start <= 0:
            if iface_price and chg is not None and (1.0 + float(chg) / 100.0) > 0:
                iface_start = float(iface_price) / (1.0 + float(chg) / 100.0)
            else:
                st["reason"] = "ok:no_start_price"
                return
        st["interface_start_price"] = iface_start

        # 第二代判据 ref_diff_pp：接口 startPrice vs「start_time 那根 1m K 的开盘」直比价。
        # 2026-09-18 降级为纯观测（原门槛 ZOMBIE_REF_PP=3.0）。
        try:
            ref = self._start_price_1m(pair, int(st_time))
        except Exception as e:
            st["reason"] = f"ok:ohlcv_error:{type(e).__name__}"
            ref = None
        if ref and ref > 0:
            st["ref_1m"] = ref
            st["ref_diff_pp"] = abs(float(iface_start) / ref - 1.0) * 100.0
        elif st["reason"] == "ok":
            st["reason"] = "ok:ref_1m_unavailable"

        # 第一代判据 diff_pp：比涨幅百分点。2026-09-11 已降级（误差被累计涨幅放大，
        # corr(diff_pp, 参考价口径差)=0.955，误杀 83.4% 的活信号）。
        try:
            start_open = self._start_open_1h(pair, int(st_time))
            if start_open and start_open > 0:
                st["start_open"] = start_open
                st["recomputed_pct"] = ((st["binance_price"] - start_open)
                                        / start_open * 100.0)
                if chg is not None:
                    st["diff_pp"] = abs(st["recomputed_pct"] - float(chg))
        except Exception:
            pass

    def _refresh_entry_state(self, rows: list[dict], fetched_at: str) -> None:
        """score 榜按 score 降序取 top3，逐个做僵尸行过滤，写 self._entry_state + 落盘。"""
        scored = [r for r in rows if r.get("score") is not None]
        scored.sort(key=lambda r: r["score"], reverse=True)
        top = scored[: self.TOP_N]
        self._entry_state = {}
        z_rows = []
        for rank, r in enumerate(top, 1):
            sym = r["symbol"]
            st = self._check_tradable(sym, r)
            st["rank"] = rank
            self._entry_state[sym] = st
            z_rows.append({
                "fetched_at_utc": fetched_at,
                "symbol": sym, "pair": st["pair"], "score": st["score"], "rank": st["rank"],
                "interface_price": st["interface_price"],
                "interface_change_pct": st["interface_change_pct"],
                "start_time": st["start_time"],
                "binance_price": st["binance_price"],
                "interface_start_price": st["interface_start_price"],
                "ref_1m": st["ref_1m"], "ref_diff_pp": st["ref_diff_pp"],
                "start_open": st["start_open"],
                "recomputed_pct": st["recomputed_pct"], "diff_pp": st["diff_pp"],
                "prefilter_hit": st["prefilter_hit"], "is_zombie": st["is_zombie"],
                "tradable": st["tradable"], "reason": st["reason"],
            })
        self._append_csv(ZOMBIE_PATH, ZOMBIE_FIELDS, z_rows)
        for sym, st in self._entry_state.items():
            logger.info(
                f"[AI500V3] top{st['rank']} {sym} score={st['score']} "
                f"tradable={st['tradable']} zombie={st['is_zombie']} reason={st['reason']}"
            )

    # ------------------------------------------------------------------ #
    # CSV 落盘（通用，带字段轮转）
    # ------------------------------------------------------------------ #
    @staticmethod
    def _append_csv(path: str, fields: list[str], rows: list[dict]) -> None:
        if not rows:
            return
        write_header = not os.path.exists(path)
        try:
            if not write_header:
                with open(path, "r", encoding="utf-8") as f:
                    old = (f.readline() or "").strip().split(",")
                if old != fields:
                    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                    os.rename(path, f"{path}.{ts}.bak")
                    write_header = True
            with open(path, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fields)
                if write_header:
                    writer.writeheader()
                writer.writerows(rows)
        except OSError:
            pass

    # ------------------------------------------------------------------ #
    # 主循环：采集 + 决策状态刷新
    # ------------------------------------------------------------------ #
    def bot_loop_start(self, current_time: datetime, **kwargs) -> None:
        now_ts = current_time.timestamp()
        fetched_at = current_time.astimezone(CST).isoformat()

        # score 榜采集（5 分钟节流）→ 刷新 top3 入场状态
        if now_ts - self._last_score_fetch >= SCORE_FETCH_INTERVAL_S:
            self._last_score_fetch = now_ts
            rows = self._fetch_ai500_scores()
            if rows:
                for r in rows:
                    r["fetched_at_utc"] = fetched_at
                self._append_csv(SCORE_HISTORY_PATH, SCORE_HISTORY_FIELDS, rows)
                self.ai500_scores = {r["symbol"]: r for r in rows}
                self._refresh_entry_state(rows, fetched_at)

        # net_flow 五档落盘（60s 节流，纯辅助验证，不参与入场）
        if now_ts - self._last_netflow_fetch >= NETFLOW_POLL_SEC:
            self._last_netflow_fetch = now_ts
            nf_rows = self._fetch_netflow_multiwin()
            if nf_rows:
                for r in nf_rows:
                    r["fetched_at_utc"] = fetched_at
                self._append_csv(NETFLOW_MULTIWIN_PATH, NETFLOW_MULTIWIN_FIELDS, nf_rows)

    # ------------------------------------------------------------------ #
    # freqtrade 钩子
    # ------------------------------------------------------------------ #
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # score 榜是当前时点的实时快照（无历史回放），只对最新一根 bar 有意义，
        # 不可用于回测（见文件头说明）。与 V2 同一模式。
        sym = self._pair_to_base(metadata['pair'])
        st = self._entry_state.get(sym)
        if st is None:
            dataframe['score_rank'] = self.RANK_ABSENT
            dataframe['score'] = -1.0     # 哨兵值，保证下方 >= 比较安全（避开 None 比较）
            dataframe['tradable'] = 0
            dataframe['is_zombie'] = 0
        else:
            dataframe['score_rank'] = st.get('rank', self.RANK_ABSENT)
            dataframe['score'] = st.get('score')
            dataframe['tradable'] = st.get('tradable', 0)
            dataframe['is_zombie'] = st.get('is_zombie', 0)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """入场闸门：score 榜 top3 且 score >= 80 且非僵尸且币安有合约。

        - score >= 80 才开新仓；60~80 只观察（score 榜快照 + 僵尸行落盘已记录，不进场）。
        - 无 BTC 方向闸门（2026-09-05 用户拍板：ai500.txt 原文没有，不加自由参数）。
        """
        dataframe.loc[
            (dataframe['score_rank'] <= self.TOP_N)
            & (dataframe['score'] >= self.MIN_SCORE)
            & (dataframe['tradable'] == 1)
            & (dataframe['is_zombie'] == 0),
            ['enter_long', 'enter_tag'],
        ] = (1, 'ai500_score_top3')
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """出场：4h 收阴线（close < open）即平。

        timeframe=4h 下 dataframe 本身就是 4h K；「入场后首根晚于入场的 4h K」
        由 freqtrade 时序自然保证（出场评估先于入场评估，新开仓不会在同一根 K 被平）。
        48h 强制离场兜底走 minimal_roi；5% 止损走 stoploss。三条路径互不冲突。
        """
        dataframe.loc[
            (dataframe['close'] < dataframe['open']),
            ['exit_long', 'exit_tag'],
        ] = (1, '4h_bearish_candle')
        return dataframe
