"""
AI500V1MacdExitDryRun — AI500 入场 + MACD whisper 出场的 dry-run 观察版
=============================================================
探索性——非结论，禁止引用为证据。观察 2-4 周后用真实交易数据判定。

理念（2026-09-02 用户拍板）：
  - 入场：AI500 原逻辑（net_flow rank 中位数≤3 + 币安 OI 30m >3% + BTC 30m bias>0）
    —— AI500 负责「发现暴涨币」（资金流是领先指标，能从 200 币池筛出热点）
  - 出场：MACD whisper 真卖出信号（karisik/bicak，连续 N 根确认）
    —— MACD 负责「吃主升段」，信号转空才离场，不做 rank 秒退
  - 数据背景（08-26 BTR 案例）：AI500 net_flow 榜滞后约 9h（资金流入后才上榜），
    而 MACD 在暴涨前 3h 就给 super_ralli。组合思路 = AI500 选币 + MACD 定离场时机。

与 AI500V1DryRun 的差异：
  1. populate_exit_trend 不再用 rank 连续>10 秒退（原 netflow_oi_exit），
     退出改由 custom_exit + MACD whisper 判定
  2. 保留 ATR 地板止损作为灾难保护；去掉移动止损阶梯（避免锁死主升段利润）
  3. max_open_trades 保持 3（观察期内与 AI500 原始版并跑对比）

数据依赖：MACD whisper 需 15m/1h/4h/1d 四周期 K 线，用策略内 ccxt binance
懒加载拉取（复用 _get_btc_mtf_15m 的 EMA/MACD 实现，扩展到 4 周期）。

风险提示：MACD whisper 在 Top50 全市场验证中「去最大1笔后为负」
（8-类型拆分无单一类型有独立 edge），本策略仅验证「AI500入场+MACD出场」
组合是否优于 AI500 原始出场——不是结论，是观察。
"""
from freqtrade.strategy import IStrategy
from freqtrade.persistence import Trade

import csv
import logging
import os
import statistics
import time
from collections import deque
from datetime import datetime, timezone, timedelta
from typing import Optional

# 东八区北京时间（用户约定：所有对话/数据时间均为 CST/UTC+8）
CST = timezone(timedelta(hours=8))

import ccxt
import numpy as np
import pandas as pd
import requests
from pandas import DataFrame

from freqtrade.strategy import IStrategy
from freqtrade.persistence import Trade

logger = logging.getLogger(__name__)

TRENDING_URL = "https://vergex.trade/trending-crypto"
TRENDING_PROXY = {
    "http": "http://127.0.0.1:7890",
    "https": "http://127.0.0.1:7890",
}
FETCH_TIMEOUT = 10
LIST_LIMIT = 50

HISTORY_PATH = "/home/gyan/freqtrade/user_data/ai500_netflow_oi_history.csv"
HISTORY_FIELDS = [
    "fetched_at_utc", "symbol", "pair", "netflow_rank", "median_rank",
    "netflow_amount", "oi_delta_percent", "oi_source", "oi_delta_value", "price", "price_delta_percent",
    "btc_large_net_30m", "btc_large_net_15m",
]
# BTC MTF 评分独立 CSV（BTC 全局量，避免污染 215 万行主 CSV 的列结构）
MTF_HISTORY_PATH = "/home/gyan/freqtrade/user_data/ai500_btc_mtf_history.csv"
MTF_HISTORY_FIELDS = [
    "fetched_at_utc", "btc_mtf_15m", "btc_bias_pct", "btc_large_net_30m", "btc_large_net_15m",
    "btc_imb_buy_max", "btc_imb_sell_max",
]
# AI500 本尊 score 榜历史（2026-09-02 恢复采集：TASK 121 曾判"恒3币无价值"弃用，
# 但端点已改版为动态 4 币 + compositeZ 综合评分。真 AI500 用法：score≥80 入场 / ≥67 条件
# / <60 平仓（公开策略 prompt 规则），与 net_flow 榜代理不是一回事——需独立积累验证）
SCORE_HISTORY_PATH = "/home/gyan/freqtrade/user_data/ai500_score_history.csv"
SCORE_HISTORY_FIELDS = [
    "fetched_at_utc", "symbol", "pair", "score", "signal_peak", "price",
    "start_price", "start_time", "change_pct_value",
]
SCORE_FETCH_INTERVAL_S = 300  # score 每小时刷新（generatedAt ~30s 变但 score 低频），5 分钟采样足够
# 币安 aggTrades 逐笔目录（ai500_agg_collector.py 常驻采集）
AGG_TRADES_DIR = "/home/gyan/freqtrade/user_data/agg_trades"
IMB_LOOKBACK_MIN = 30   # imbalance 观察窗口 = 30m（与策略周期对齐）
IMB_PRICE_STEP = 78.0   # 价格分层步长 ≈ 0.1%（78 USDT）
IMB_RATIO_THRESH = 3.0  # 失衡阈值 300%


class AI500V1MacdExitDryRun(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = '30m'
    can_short = False
    process_only_new_candles = True
    startup_candle_count = 100

    # 框架层 stoploss 占位；真正止损通过 custom_stoploss（ATR）实现
    stoploss = -0.99
    minimal_roi = {"0": 100}
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = True
    trailing_stop = False
    use_custom_stoploss = True

    max_open_trades = 3

    ENTRY_MEDIAN = 3        # 入场：过去 30m net_flow 排名中位数 ≤ 3（与 30m K 线粒度对齐）
    EXIT_RANK = 10          # 退出：当前 rank 跌出前 10 立即退（灵敏，不经中位数平滑）
    RANK_EXIT_CONFIRM = 3   # 退出平滑（09-01 方案A）：连续 3 次采样 rank>10 才平（≈1.5min 确认）
                            #   数据验证（16 笔 rank 轨迹重放）：N=2 与 N=3 效果相同（30s 采样粒度），
                            #   真掉队持续 >10 不受影响，单次抖动被过滤；N=5 多保 2 笔但增加真掉队拖尾风险
    RANK_WINDOW_SEC = 1800  # 中位数回看窗口 = 30m（与 30m K 线粒度对齐，原 1h）
    MIN_WINDOW_SEC = 900    # 中位数最少覆盖 15 分钟，样本不足视为不在前 3
    RANK_ABSENT = 99        # 不在 net_flow 榜时的 rank 占位值
    OI_MIN = 3.0            # OI 过滤：当前 oi_delta_percent > 3% 才可入场（30m 窗口等价阈值）
    # 09-01 反推验证（977,016 采样点，8-20~8-26 与 8-27~9-01 两独立时段一致）：
    #   "低点"=未来 2h 涨≥5%，命中率随 OI 单调上升（跨时段稳健，非过拟合）：
    #     OI<1%: 5% | 1~2%: 10-11% | 2~3%: 14-16% | 3~5%: 17-20% | 5~10%: 22-26% | >10%: 28-30%
    #   旧 1.0% 阈值命中率 ~10%，提到 3.0% → ~17-20%（+80%），代价是入场笔数减少
    #   历史依据（08-30 71 笔实盘双窗口回测，留档）：
    #     1h OI>2% 保留 12 笔 -2.60U（含 #12 ENA -0.71U / #71 PROM -0.42U 等 1h 强但 30m 已熄火陷阱）
    #     30m OI>1.0% 保留 7 笔 -0.87U（拦截 5 笔 1h 假强单，净改善 +1.73U）
    #   阈值随窗口缩放：1h 2.0% ≈ 30m 1.0%（OI 增长是慢变量，窗口减半阈值约减半）
    #   只作用于做多（can_short=False）；做空信号待后续单独研究
    BTC_BIAS_WINDOW_MIN = 30  # BTC 方向过滤：前 30 分钟涨幅 > 0 才允许做多

    ATR_PERIOD = 40         # 30m K 线 ×40 = 20h 窗口（原 1h ×20）
    ATR_MULTIPLIER = 2.0    # 30m 粒度 ATR 绝对值约 1h 的 1/√2，×2 补偿止损距离缩水

    # 移动止损阶梯（2026-08-27 用户方案2：止损逐级上移，不主动卖）
    # 触发条件 = 持仓期间峰值盈利（trade.max_rate vs open_rate）
    # 每档把止损移到对应位置，回吐到止损位自动卖出（保护利润）
    # 66 笔验证：阶梯止盈 37.28% vs 实际 33.92%，救回 4 笔盈利变亏
    # 但为避免砍大单翅膀，用「移动止损」而非「主动分批卖」——netflow_oi_exit 仍管 rank 退出
    # 手续费已计入：币安合约 0.05%×2（开+平）= 0.1%，保本档 = 开仓价+0.1%
    # 后续每档锁定 X% 均为「扣除手续费后的净锁定」——锁 0.3% 实际锁 0.2% 净利
    TRAIL_STAGES = [
        # (触发峰值盈利%, 止损移到开仓价+%)
        (0.5, 0.1),    # 盈利 0.5% → 止损到开仓价+0.1%（真保本，含手续费）
        (1.0, 0.4),    # 盈利 1.0% → 锁 0.4%（净 0.3%）
        (1.5, 0.9),    # 盈利 1.5% → 锁 0.9%（净 0.8%）
        (2.0, 1.4),    # 盈利 2.0% → 锁 1.4%（净 1.3%）
        (2.5, 1.9),    # 盈利 2.5% → 锁 1.9%（净 1.8%）
        (3.0, 2.4),    # 盈利 3.0% → 锁 2.4%（净 2.3%）
    ]

    VOL_LOOKBACK = 40       # 30m K 线 ×40 = 20h 窗口（原 1h ×20）
    TARGET_VOL = 0.20
    MAX_LEV = 1.0

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.rank_history: dict[str, deque] = {}     # symbol -> deque[(ts, rank)]
        self.median_rank: dict[str, float] = {}      # symbol -> 30m 中位数排名
        self.current_rank: dict[str, int] = {}       # symbol -> 最近一次采样 rank（退出用）
        self.last_rank: dict[str, int] = {}          # symbol -> 上一次成功采样 rank（接口失败兜底，修复09-01）
        self.oi_delta_pct: dict[str, float | None] = {}     # symbol -> oi_delta_percent（None=币安取不到，保守拦截）
        self.btc_bias_pct: float | None = None       # BTC 前 30min 涨幅 %（方向过滤）
        self.btc_mtf_15m: int | None = None          # BTC 15m MACD 共振评分 -4~+4（观察版）
        self.btc_large_net_30m: float | None = None  # BTC 大单净流 USD（最近 30 分钟，与策略周期对齐）
        self.btc_large_net_15m: float | None = None  # BTC 大单净流 USD（最近 15 分钟，更灵敏）
        self.btc_imb_buy_max: float | None = None    # BTC 价格分层 buy imbalance 最大比（观察）
        self.btc_imb_sell_max: float | None = None   # BTC 价格分层 sell imbalance 最大比（观察）
        # MACD whisper 出场状态：symbol -> {'bar': int, 'count': int}
        # 连续 MACD_CONFIRM_BARS 根 30m bar whisper 在卖出集才触发 custom_exit
        self._macd_exit_state: dict[str, dict] = {}
        # AI500 score 采集节流状态
        self._last_score_fetch: float = 0.0
        self.ai500_scores: dict[str, dict] = {}  # symbol -> {score, signal_peak, ...} 观察用
        # 币安客户端（OI 数据来源，用于四象限/资金流判断的 fallback）
        # 懒加载：启动时可能因代理繁忙失败，运行中 _get_binance_oi_change 会重试
        self.binance = None
        # 代理配置实际在 exchange.ccxt_config.proxies，不在 exchange.proxies（None）
        # 直接从 ccxt_config 拿，避免策略 new 的 ccxt 无代理直连被墙导致超时
        self.binance_proxies = (config.get('exchange', {}).get('ccxt_config', {})
                                .get('proxies', {}))
        if not self.binance_proxies:
            self.binance_proxies = {"http": "http://127.0.0.1:7890",
                                    "https": "http://127.0.0.1:7890"}
        self._init_binance()

    def _init_binance(self) -> None:
        """初始化币安客户端（带重试）。启动瞬间代理繁忙时首次 fetch_time 可能超时，
        重试 3 次（间隔 5s）避免 self.binance 永久为 None 导致 OI fallback 失效。"""
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
                    logger.info("Binance client initialized for OI history fallback")
                    return
                except Exception as e:
                    last_err = e
                    logger.warning(f"Binance client init attempt {attempt+1}/3 failed: {e}")
                    time.sleep(5)
            raise last_err if last_err else Exception("init failed")
        except Exception as e:
            logger.warning(f"Binance client init failed (will retry on next use): {e}")
            self.binance = None

    def _get_binance_oi_change(self, symbol: str, window_min: int = 30) -> float:
        """从币安获取 OI 累计变化率（主源）。

        返回 (now - past) / past * 100 格式的累计百分比。
        window_min 默认 30（与 30m K 线粒度对齐，原 1h）。
        08-30 数据验证（71 笔实盘双窗口回测）：30m OI>1% 优于 1h OI>2%
        （-0.87U vs -2.60U），且能拦截 1h 假强但 30m 已熄火的陷阱单。
        其他可选: 5, 15, 60, 240。

        合约名规则：币安给 <1美元 的小币加 1000 前缀（PEPE/BONK/FLOKI），
        BCH/SOL/AAOI 不加。双尝试：先原名，失败再试 1000 前缀。
        """
        if not self.binance:
            # 懒加载：启动时可能失败，运行中首次使用 OI 数据时重试初始化
            self._init_binance()
        if not self.binance:
            return None
        # 待尝试的 symbol 列表
        candidates = [symbol]
        if symbol.endswith('USDT') and not symbol.startswith('1000'):
            candidates.append('1000' + symbol)
        # 选 period + limit：根据窗口长度（沿用 5m 粒度，最长拉 1d）
        if window_min <= 5:
            period, limit = '5m', 2
        elif window_min <= 60:
            period = '5m'; limit = max(2, window_min // 5)
        elif window_min <= 240:
            period = '15m'; limit = max(2, window_min // 15)
        elif window_min <= 1440:
            period = '1h'; limit = max(2, window_min // 60)
        else:
            period = '4h'; limit = max(2, window_min // 240)
        for bs in candidates:
            try:
                oi_hist = self.binance.fapiDataGetOpenInterestHist({
                    'symbol': bs,
                    'period': period,
                    'limit': limit,
                })
                if len(oi_hist) >= 2:
                    # 时间跨度校验（数据可靠性，2026-08-30 修复 #93 PROM 假 OI bug）：
                    # 币安按 limit 返回最近 N 根，若期间有数据空洞（新币/停更），
                    # oi_hist[0] 可能远早于预期窗口 → 变化率被错误放大（#93 真实 OI 横盘
                    # 却算出 +6.12% 导致误开仓）。要求首末时间跨度在预期窗口 ±20% 内，
                    # 否则拒绝采信返回 None（保守拦截，宁可不入场也不吃假数据）。
                    period_minutes = {'5m': 5, '15m': 15, '1h': 60, '4h': 240}.get(period, 5)
                    span_ms = int(oi_hist[-1]['timestamp']) - int(oi_hist[0]['timestamp'])
                    span_min = span_ms / 60000.0
                    expected_min = (limit - 1) * period_minutes
                    if span_min < expected_min * 0.8 or span_min > expected_min * 1.2:
                        logger.warning(
                            f"Binance OI span check FAIL for {bs} window={window_min}m: "
                            f"got {len(oi_hist)} rows spanning {span_min:.0f}m "
                            f"(expected ~{expected_min}m) -> reject"
                        )
                        continue
                    oi_now = float(oi_hist[-1]['sumOpenInterest'])
                    oi_past = float(oi_hist[0]['sumOpenInterest'])
                    if oi_past > 0:
                        return (oi_now - oi_past) / oi_past * 100.0
            except Exception as e:
                logger.debug(f"Binance OI fetch failed for {bs} window={window_min}m: {e}")
                continue
        return None

    def _get_btc_bias(self) -> float | None:
        """获取 BTC 前 30 分钟涨幅 %（方向过滤用）。

        用币安 5m K 线：取最近 6 根（30 分钟），对比第一根开盘 vs 最后一根收盘。
        失败返回 None（调用方处理：保留上次值或保守拦截，不因瞬时失败放行）。
        """
        if not self.binance:
            self._init_binance()
        if not self.binance:
            return None
        try:
            k = self.binance.fetch_ohlcv("BTC/USDT:USDT", "5m", limit=7)
            if not k or len(k) < 2:
                return None
            # k = [[ts, open, high, low, close, vol], ...]
            start_price = k[0][1]
            end_price = k[-1][4]
            if start_price <= 0:
                return None
            return (end_price - start_price) / start_price * 100.0
        except Exception as e:
            logger.debug(f"BTC bias fetch failed: {e}")
            return None

    def _get_btc_mtf_15m(self) -> int | None:
        """BTC 15m 单周期 MACD 共振评分（-4 ~ +4，观察版，不进入场）。

        复刻 MACD v2.4 的 MTF 矩阵评分逻辑（单周期版）：
          1. MACD > Signal            -> +1（否则 -1）
          2. MACD 较前一根上升        -> +1（否则 -1）
          3. Signal 较前一根上升      -> +1（否则 -1）
          4. Histogram > 0            -> +1（否则 -1）
        标准 MACD(12,26,9)。用币安 15m 合约 K 线，60 根足够 EMA 预热。

        用途（观察版）：只写 CSV + 日志积累，与 AI500 开仓/收益对照 2-4 周，
        评分 <0 时开仓的单 vs >=0 时开仓的单盈亏差足够大才转正为入场过滤。
        失败返回 None（调用方保留上次值）。
        """
        if not self.binance:
            self._init_binance()
        if not self.binance:
            return None
        try:
            k = self.binance.fetch_ohlcv("BTC/USDT:USDT", "15m", limit=60)
            if not k or len(k) < 35:  # EMA26 + EMA9 预热需要至少 35 根
                return None
            closes = [float(x[4]) for x in k]

            def ema(values: list[float], period: int) -> list[float]:
                alpha = 2.0 / (period + 1)
                out = [values[0]]
                for v in values[1:]:
                    out.append(alpha * v + (1 - alpha) * out[-1])
                return out

            ema12 = ema(closes, 12)
            ema26 = ema(closes, 26)
            macd = [a - b for a, b in zip(ema12, ema26)]
            signal = ema(macd, 9)
            hist = [m - s for m, s in zip(macd, signal)]

            m, s, h = macd[-1], signal[-1], hist[-1]
            m_prev, s_prev = macd[-2], signal[-2]

            score = 0
            score += 1 if m > s else -1
            score += 1 if m > m_prev else -1
            score += 1 if s > s_prev else -1
            score += 1 if h > 0 else -1
            return score
        except Exception as e:
            logger.debug(f"BTC MTF 15m fetch failed: {e}")
            return None

    def _get_btc_large_net(self, interval: str = "30m") -> float | None:
        """获取 BTC 大单净流 USD（糖哥 stock.tanggestock.com 大单追溯）。

        接口：/api/realtime-events/large-trades?symbol=BTC&interval=<5m|15m|30m|1h>&anchor=<周期末根ms>&market=perp
        返回周期桶：buyUsd/sellUsd（主动买/卖金额），净流 = buyUsd - sellUsd。
        实测刷新：后端流式实时落库，5m 桶滞后约 5min，30m 桶滞后约 30min。
        数据源：币安合约逐笔成交流（perp 池，非 aggTrade 现货），2026-07 起落库，免费免鉴权。
        anchor 对齐：必须用「当前周期末根 K 线时间戳」而非实时时间，否则 rows[-1] 会错位
        ~27min(30m)/~12min(15m)，与 BTC 30m bias / K 线周期对不齐（2026-08-31 实测确认）。
        失败返回 None（调用方处理：保留上次值）。
        """
        try:
            # anchor = 当前周期末根时间戳（整除对齐到整点桶边界）
            if interval == "15m":
                bucket_ms = 900_000
            elif interval == "1h":
                bucket_ms = 3_600_000
            else:  # 30m 默认
                bucket_ms = 1_800_000
            anchor = (int(time.time() * 1000) // bucket_ms) * bucket_ms
            url = ("https://stock.tanggestock.com/api/realtime-events/large-trades"
                   f"?symbol=BTC&interval={interval}&anchor={anchor}&market=perp")
            resp = requests.get(url, proxies=TRENDING_PROXY,
                                headers={"User-Agent": "Mozilla/5.0"}, timeout=FETCH_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            rows = data.get("rows") or []
            if not rows:
                return None
            last = rows[-1]
            if "buyUsd" not in last or "sellUsd" not in last:
                return None
            return float(last["buyUsd"]) - float(last["sellUsd"])
        except Exception as e:
            logger.debug(f"BTC large net fetch failed ({interval}): {e}")
            return None

    @staticmethod
    def _strip_usdt(symbol: str) -> str:
        # "ACEUSDT" -> "ACE"
        return symbol[:-4] if symbol.endswith("USDT") else symbol

    @staticmethod
    def _pair_to_symbol(pair: str) -> str:
        # "ACE/USDT:USDT" -> "ACEUSDT"
        return f"{pair.split('/')[0]}USDT"

    def _fetch_list(self, tab: str):
        """抓取 trending-crypto 某 tab 的 top 榜，返回 {symbol: item}。失败返回 None。"""
        try:
            resp = requests.get(
                TRENDING_URL, params={"tab": tab, "limit": LIST_LIMIT},
                proxies=TRENDING_PROXY,
                headers={"User-Agent": "Mozilla/5.0"}, timeout=FETCH_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"[AI500] fetch tab={tab} failed, keeping previous state: {e}")
            return None
        out = {}
        for item in data.get("top") or []:
            sym = item.get("symbol")
            if sym:
                out[sym] = item
        return out

    def _fetch_ai500_scores(self) -> list[dict] | None:
        """抓取 AI500 本尊 score 榜（trending-category?key=ai500）。

        返回 assets 列表（每项含 score/signal/startPrice/startTime/changePctValue）。
        2026-09-02 端点已改版为动态 4 币（TASK 121 曾判恒 3 币弃用）。
        失败返回 None（调用方保留上次值）。
        """
        try:
            resp = requests.get(
                "https://vergex.trade/trending-category",
                params={"lang": "zh", "key": "ai500"},
                proxies=TRENDING_PROXY,
                headers={"User-Agent": "Mozilla/5.0"}, timeout=FETCH_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            assets = (data.get("category") or {}).get("assets") or []
            out = []
            for a in assets:
                sym = a.get("symbol")
                if not sym:
                    continue
                out.append({
                    "symbol": sym,
                    "pair": f"{sym}/USDT:USDT",
                    "score": a.get("score"),
                    "signal_peak": a.get("signal"),
                    "price": a.get("price"),
                    "start_price": a.get("startPrice"),
                    "start_time": a.get("startTime"),
                    "change_pct_value": a.get("changePctValue"),
                })
            return out if out else None
        except Exception as e:
            logger.warning(f"[AI500] fetch score failed, keeping previous: {e}")
            return None

    @staticmethod
    def _append_score_history(rows: list[dict]) -> None:
        if not rows:
            return
        write_header = not os.path.exists(SCORE_HISTORY_PATH)
        try:
            with open(SCORE_HISTORY_PATH, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=SCORE_HISTORY_FIELDS)
                if write_header:
                    writer.writeheader()
                writer.writerows(rows)
        except OSError:
            pass

    def bot_loop_start(self, current_time: datetime, **kwargs) -> None:
        # AI500 score 本尊采集（独立于 netflow/oi，5 分钟节流；纯观察积累，不进当前入场逻辑）
        now_ts = current_time.timestamp()
        if now_ts - self._last_score_fetch >= SCORE_FETCH_INTERVAL_S:
            self._last_score_fetch = now_ts
            score_rows = self._fetch_ai500_scores()
            if score_rows:
                for s in score_rows:
                    s["fetched_at_utc"] = current_time.astimezone(CST).isoformat()
                self._append_score_history(score_rows)
                self.ai500_scores = {s["symbol"]: s for s in score_rows}

        netflow = self._fetch_list("net_flow")
        oi = self._fetch_list("oi")
        if netflow is None or oi is None:
            return

        now = current_time.timestamp()

        # 更新 OI 状态（只用当前值，不要求持续度）
        self.oi_delta_pct = {sym: it.get("oi_delta_percent") for sym, it in oi.items()}

        # 更新 BTC 方向过滤（前 30min 涨幅）
        # 失败时保留上次成功值（避免瞬时 API 失败导致过滤失效放行）
        new_bias = self._get_btc_bias()
        if new_bias is not None:
            self.btc_bias_pct = new_bias

        # 更新 BTC 大单净流（糖哥大单数据，观察积累用，暂不影响入场）
        # 30m 桶=与策略周期对齐（滞后~30min），15m 桶=更灵敏（滞后~15min）
        # 失败时保留上次成功值
        new_large_net_30m = self._get_btc_large_net(interval="30m")
        if new_large_net_30m is not None:
            self.btc_large_net_30m = new_large_net_30m
        new_large_net_15m = self._get_btc_large_net(interval="15m")
        if new_large_net_15m is not None:
            self.btc_large_net_15m = new_large_net_15m

        # 更新 BTC 15m MTF 共振评分（观察版，不进入场；失败保留上次值）
        new_mtf = self._get_btc_mtf_15m()
        if new_mtf is not None:
            self.btc_mtf_15m = new_mtf
        # 更新 BTC 价格分层 imbalance（币安 aggTrades，观察积累用，不进入场）
        new_imb = self._get_btc_imb()
        if new_imb[0] is not None:
            self.btc_imb_buy_max = new_imb[0]
        if new_imb[1] is not None:
            self.btc_imb_sell_max = new_imb[1]
        self._append_mtf_history(current_time.astimezone(CST).isoformat())

        # 更新排名历史：net_flow 榜内的记真实 rank，其余记 RANK_ABSENT（不在榜）
        netflow_syms = set(netflow.keys())
        tracked = set(self.rank_history.keys()) | netflow_syms
        try:
            for t in Trade.get_open_trades():
                tracked.add(self._pair_to_symbol(t.pair))
        except Exception:
            pass
        for sym in tracked:
            hist = self.rank_history.setdefault(sym, deque())
            rank = netflow[sym].get("rank") if sym in netflow_syms else self.RANK_ABSENT
            hist.append((now, rank))
            self.current_rank[sym] = rank
            self.last_rank[sym] = rank   # 成功采样才更新兜底值（修复09-01：接口失败时保留上次）

        # 清理超过 30m 的旧采样，并计算中位数（窗口覆盖 < 15 分钟视为样本不足）
        cutoff = now - self.RANK_WINDOW_SEC
        self.median_rank = {}
        for sym in list(self.rank_history.keys()):
            hist = self.rank_history[sym]
            while hist and hist[0][0] < cutoff:
                hist.popleft()
            if not hist:
                del self.rank_history[sym]
                continue
            if (now - hist[0][0]) < self.MIN_WINDOW_SEC:
                continue  # 样本不足，不产生有效中位数（视为不在前 3）
            self.median_rank[sym] = statistics.median([r for _, r in hist])

        # 写历史记录（含当前 rank 与 30m 中位数）
        # 时间统一为东八区北京时间（CST），方便与用户对话/Telegram 通知核对
        # 2026-08-31 用户要求：CSV 记录时间全部改北京时间，避免与 DB 的 UTC 混淆
        fetched_at = current_time.astimezone(CST).isoformat()
        rows = []
        for sym in sorted(tracked):
            nf = netflow.get(sym) or {}
            o = oi.get(sym) or {}
            pair = f"{self._strip_usdt(sym)}/USDT:USDT"
            # OI 唯一源：币安 30m 累计变化率（与 30m K 线粒度对齐）
            # 2026-08-31 用户拍板：去掉 Vergex fallback——vergex oi_delta_percent 是 1h 窗口
            # （price_delta 验证 +0.727%≈0.785%），与币安 30m 窗口错配，fallback 会用错窗口
            # 阈值（30m OI_MIN=1.0% vs 1h 波动天然更大）放行 30m 已熄火的单。
            # 币安取不到 OI → 留 None → populate_indicators 里 oi_ok=0 保守拦截，宁可不入场。
            oi_delta_value = o.get("oi_delta_value")
            oi_delta_percent = self._get_binance_oi_change(sym, window_min=30) if self.binance else None
            oi_source = "binance" if oi_delta_percent is not None else None
            rows.append({
                "fetched_at_utc": fetched_at,
                "symbol": sym,
                "pair": pair,
                "netflow_rank": nf.get("rank"),
                "median_rank": self.median_rank.get(sym),
                "netflow_amount": nf.get("amount"),
                "oi_delta_percent": oi_delta_percent,
                "oi_source": oi_source,
                "oi_delta_value": oi_delta_value,
                "price": nf.get("price") if nf.get("price") is not None else o.get("price"),
                "price_delta_percent": nf.get("price_delta_percent")
                    if nf.get("price_delta_percent") is not None else o.get("price_delta_percent"),
                "btc_large_net_30m": self.btc_large_net_30m,
                "btc_large_net_15m": self.btc_large_net_15m,
            })
            # 过滤用 OI 源写回（币安优先，失败回退 Vergex）：确保入场过滤与 CSV 记录一致
            # 08-30 修复：原 self.oi_delta_pct 直接来自 Vergex（oi tab），币安 30m 算了只进 CSV
            # 导致 OI_MIN/window_min 改动形同虚设。现统一为币安源（自主可控，不依赖 Vergex 稳定性）
            self.oi_delta_pct[sym] = oi_delta_percent
        self._append_history(rows)

    @staticmethod
    def _append_history(rows: list[dict]) -> None:
        if not rows:
            return
        write_header = not os.path.exists(HISTORY_PATH)
        try:
            with open(HISTORY_PATH, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=HISTORY_FIELDS)
                if write_header:
                    writer.writeheader()
                writer.writerows(rows)
        except OSError:
            pass

    def _get_btc_imb(self, lookback_min: int = IMB_LOOKBACK_MIN) -> tuple[float | None, float | None]:
        """读取币安 aggTrades 逐笔落盘，计算最近 lookback_min 窗口的价格分层 imbalance。

        返回 (buy_imb_max_ratio, sell_imb_max_ratio)。数据缺失/文件不存在返回 (None, None)。
        分层方法: 价格按 0.1% 步长分组，某层主动买量 > 下层主动卖量 ×3 = buy imbalance。
        """
        try:
            cutoff_ms = int(time.time() * 1000) - lookback_min * 60_000
            # 只读最近 1-2 天的文件（采集器每天一个文件；UTC 日期从时间戳推导）
            import datetime as _dt
            days = []
            for off in (0, -1):
                d = _dt.datetime.fromtimestamp(
                    int(time.time()) + off * 86400, _dt.timezone.utc)
                days.append(d)
            files = []
            for d in days:
                fp = os.path.join(AGG_TRADES_DIR, f"agg_{d.strftime('%Y%m%d')}.csv")
                if os.path.exists(fp):
                    files.append(fp)
            if not files:
                return None, None

            wbq: dict[int, float] = {}
            wsq: dict[int, float] = {}
            for fp in files:
                with open(fp, newline="", encoding="utf-8") as f:
                    reader = csv.reader(f)
                    next(reader, None)  # header
                    for row in reader:
                        if len(row) < 5:
                            continue
                        ts_ms = int(row[0])
                        if ts_ms < cutoff_ms:
                            continue
                        price = float(row[2])
                        qty = float(row[3])
                        maker = row[4].strip() == "1"
                        lvl = round(price / IMB_PRICE_STEP)
                        if maker:
                            wsq[lvl] = wsq.get(lvl, 0.0) + qty
                        else:
                            wbq[lvl] = wbq.get(lvl, 0.0) + qty
            if not wbq or not wsq:
                return None, None

            lvls = sorted(set(list(wbq.keys()) + list(wsq.keys())))
            buy_max = sell_max = 0.0
            for i, l in enumerate(lvls):
                b = wbq.get(l, 0.0)
                s = wsq.get(l, 0.0)
                s_below = wsq.get(lvls[i - 1], 0.0) if i > 0 else 0.0
                b_above = wbq.get(lvls[i + 1], 0.0) if i < len(lvls) - 1 else 0.0
                if b > 0 and s_below > 0:
                    buy_max = max(buy_max, b / s_below)
                if s > 0 and b_above > 0:
                    sell_max = max(sell_max, s / b_above)
            return buy_max, sell_max
        except Exception as e:
            logger.debug(f"BTC imbalance fetch failed: {e}")
            return None, None

    def _append_mtf_history(self, fetched_at_utc: str) -> None:
        """写 BTC MTF 评分快照（独立小 CSV，观察积累用）。"""
        if self.btc_mtf_15m is None:
            return
        write_header = not os.path.exists(MTF_HISTORY_PATH)
        row = {
            "fetched_at_utc": fetched_at_utc,
            "btc_mtf_15m": self.btc_mtf_15m,
            "btc_bias_pct": self.btc_bias_pct,
            "btc_large_net_30m": self.btc_large_net_30m,
            "btc_large_net_15m": self.btc_large_net_15m,
            "btc_imb_buy_max": self.btc_imb_buy_max,
            "btc_imb_sell_max": self.btc_imb_sell_max,
        }
        try:
            with open(MTF_HISTORY_PATH, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=MTF_HISTORY_FIELDS)
                if write_header:
                    writer.writeheader()
                writer.writerow(row)
        except OSError:
            pass

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        pair = metadata['pair']
        dataframe['atr'] = self._compute_atr(dataframe, self.ATR_PERIOD)
        ret = dataframe['close'].pct_change()
        dataframe['realized_vol'] = ret.rolling(self.VOL_LOOKBACK).std().shift(1) * np.sqrt(365 * 24 * 2)
        # net_flow/OI 榜单是当前时点的实时快照（无历史回放），只对最新一根 bar
        # 有意义，不可用于回测（详见文件头说明）。
        sym = self._pair_to_symbol(pair)
        median = self.median_rank.get(sym, self.RANK_ABSENT)
        # 修复 09-01：current_rank 缺失时保留上次值，而不是默认 RANK_ABSENT(99)
        #   根因：bot_loop_start 接口失败直接 return，current_rank 不更新；
        #   若持仓币 sym 恰不在 dict（接口失败期间新开仓/数据未刷新），
        #   get() 返回 99 → rank>10 → 立即误平仓（#16 ETH rank=2 / #46 ETH rank=1 / #76 BTC rank=1 均被误杀）
        #   现在：缺失时用 last_rank 兜底（None 才 99，即从未有过值）
        cur_rank = self.current_rank.get(sym, self.last_rank.get(sym, self.RANK_ABSENT))
        oi_pct = self.oi_delta_pct.get(sym, None)
        dataframe['median_rank'] = median
        dataframe['current_rank'] = cur_rank
        dataframe['oi_ok'] = 1 if (oi_pct is not None and oi_pct > self.OI_MIN) else 0
        # BTC 方向过滤：前 30min 涨幅 > 0 才允许做多（数据验证 66 笔：
        # BTC_30m 微跌 26 笔 -10.63 vs 微涨 31 笔 +1.34）
        # BTC 涨幅从未成功获取（初始 None）时保守拦截（btc_ok=0），
        # 避免"API 瞬时失败 → 过滤失效放行"（修复 #77 ETH 漏网 bug）
        btc_pct = self.btc_bias_pct
        dataframe['btc_ok'] = 1 if (btc_pct is not None and btc_pct > 0) else 0
        return dataframe

    @staticmethod
    def _compute_atr(df: DataFrame, period: int) -> pd.Series:
        high, low, close = df['high'], df['low'], df['close']
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)
        return tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    # ============================================================
    # MACD whisper 出场（核心改动）：4 周期 power + whisper 分类
    # 数据源：ccxt binance 合约 K 线（与 _get_btc_mtf_15m 同实现，扩 4 周期）
    # ============================================================
    MACD_TF_PARAMS = {
        '15m': (12, 26, 9), '1h': (16, 34, 12),
        '4h': (20, 40, 15), '1d': (12, 26, 9),
    }
    MACD_CONFIRM_BARS = 2  # 连续 2 根 30m bar 转卖出才确认出场（防抖动）
    MACD_SELL_SET = {'karisik', 'bicak'}  # 真卖出（不含中性 kararsiz）

    def _ema(self, values: list[float], period: int) -> list[float]:
        alpha = 2.0 / (period + 1)
        out = [values[0]]
        for v in values[1:]:
            out.append(alpha * v + (1 - alpha) * out[-1])
        return out

    def _macd_series(self, closes: list[float], fast: int, slow: int, sig: int):
        e_fast = self._ema(closes, fast)
        e_slow = self._ema(closes, slow)
        m = [a - b for a, b in zip(e_fast, e_slow)]
        s = self._ema(m, sig)
        h = [x - y for x, y in zip(m, s)]
        return m, s, h

    def _power_from_series(self, m: list[float], s: list[float], h: list[float]) -> Optional[int]:
        """计算 MTF power：用最后两根已收盘值"""
        if len(m) < 2:
            return None
        m_i, s_i, h_i = m[-1], s[-1], h[-1]
        mp, sp, hp = m[-2], s[-2], h[-2]
        macdAbove = 1 if m_i > s_i else -1
        macdUp = 1 if m_i > mp else 0
        signalUp = 1 if s_i > sp else 0
        hUp = h_i > hp
        histZone = 2 if (h_i > 0 and hUp) else 1 if (h_i > 0 and not hUp) else -1 if (h_i < 0 and hUp) else -2
        return macdAbove + macdUp + signalUp + (1 if histZone > 0 else -1 if histZone < 0 else 0)

    def _whisper_for_symbol(self, symbol: str) -> Optional[str]:
        """对单个币拉 4 周期 K 线算 whisper（None=数据不足/失败）"""
        if not self.binance:
            self._init_binance()
        if not self.binance:
            return None
        # 合约名处理：小币 1000 前缀（与 OI 拉取一致）
        candidates = [symbol]
        if symbol.endswith('USDT') and not symbol.startswith('1000'):
            candidates.append('1000' + symbol)
        # 各周期需要足够 bar：EMA26 + EMA9 预热 → 至少 40 根
        closes_by_tf = {}
        for bs in candidates:
            ok = True
            for tf, (f, sl, sg) in self.MACD_TF_PARAMS.items():
                try:
                    k = self.binance.fetch_ohlcv(f"{bs.split('USDT')[0]}/USDT:USDT", tf, limit=50)
                    if not k or len(k) < 40:
                        ok = False
                        break
                    closes_by_tf[tf] = [float(x[4]) for x in k]
                except Exception as e:
                    logger.debug(f"MACD whisper fetch {bs} {tf} failed: {e}")
                    ok = False
                    break
            if ok:
                break
        if len(closes_by_tf) < 4:
            return None
        powers = []
        for tf in ['15m', '1h', '4h', '1d']:
            if tf not in closes_by_tf:
                return None
            f, sl, sg = self.MACD_TF_PARAMS[tf]
            m, s, h = self._macd_series(closes_by_tf[tf], f, sl, sg)
            p = self._power_from_series(m, s, h)
            if p is None:
                return None
            powers.append(p)
        p1, p2, p3, p4 = powers
        if p1 >= 2 and p2 >= 2 and p3 >= 2 and p4 >= 2: return 'super_ralli'
        if p3 >= 2 and p4 >= 2 and (p1 >= 1 or p2 >= 1): return 'ralli'
        if p3 >= 2 and p4 >= 2 and p1 <= -1: return 'altin_vurus'
        if p4 >= 3 and p1 <= -2: return 'duzeltme'
        if (p1 >= 2 and p4 <= -1) or (p4 >= 2 and p1 <= -1): return 'karisik'
        if p4 <= -2 and p1 >= 2: return 'tepki'
        if p1 <= -2 and p2 <= -2 and p3 <= -2 and p4 <= -2: return 'bicak'
        return 'kararsiz'

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime,
                    current_rate: float, current_profit: float, **kwargs) -> Optional[str]:
        """MACD whisper 真卖出信号出场（连续 MACD_CONFIRM_BARS 根确认）。

        理念：AI500 负责选币入场，这里用 MACD 4 周期共振转空判定主升段结束。
        不做 rank 秒退——rank 跌出前 10 只是短期热度退潮，不是趋势结束。
        失败（None/数据不足）→ 保守不卖（趋势中宁可拿着，ATR 地板兜底）。
        """
        sym = self._pair_to_symbol(pair)
        w = self._whisper_for_symbol(sym)
        if w is None:
            return None
        # 连续计数：同一 30m bar 内多次调用只算一次（用 current_time 对齐 bar）
        bar_key = int(current_time.timestamp() // 1800)
        state = self._macd_exit_state.setdefault(sym, {'bar': None, 'count': 0})
        if state['bar'] != bar_key:
            state['bar'] = bar_key
            if w in self.MACD_SELL_SET:
                state['count'] += 1
            else:
                state['count'] = 0
        if state['count'] >= self.MACD_CONFIRM_BARS:
            return f'macd_whisper_exit({w})'
        return None

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe['median_rank'] <= self.ENTRY_MEDIAN) & (dataframe['oi_ok'] == 1)
            & (dataframe['btc_ok'] == 1),
            ['enter_long', 'enter_tag']
        ] = (1, 'netflow_oi_entry')
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # MACD whisper 出场版：不做 rank 秒退。
        # 出场完全由 custom_exit（MACD whisper 真卖出信号）判定，
        # 这里只保留一个"跌出榜很远"的灾难兜底（rank > 50 且持续确认）
        # ——避免极端情况下 MACD 一直不转空、币却已暴跌/退榜无人管。
        sym = self._pair_to_symbol(metadata['pair'])
        hist = self.rank_history.get(sym)
        confirmed = False
        if hist and len(hist) >= self.RANK_EXIT_CONFIRM:
            recent = [r for _, r in hist][-self.RANK_EXIT_CONFIRM:]
            confirmed = all(r > 50 for r in recent)  # 灾难兜底：连续3次 rank>50
        if confirmed:
            # 只在最后一根（当前）bar 标记退出。confirmed 是标量 bool，
            # 不能直接 dataframe.loc[confirmed]（pandas 拒绝单 bool 行索引）
            dataframe.loc[dataframe.index[-1], ['exit_long', 'exit_tag']] = (1, 'rank_disaster_exit')
        return dataframe

    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                         current_rate: float, current_profit: float, **kwargs) -> Optional[float]:
        """ATR 地板止损（灾难保护）。

        - ATR 硬止损：入场信号K线 low - 2xATR，绝对地板价
        - 不做移动止损阶梯（会锁死主升段利润，违背"吃主升段"理念）
        - 主动卖出由 custom_exit（MACD whisper 转空）触发
        """
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        entry_hist = df.loc[df['date'] < trade.open_date_utc]
        atr_floor_pct = None
        if not entry_hist.empty:
            entry_row = entry_hist.iloc[-1]
            atr_at_entry = entry_row['atr']
            if not pd.isna(atr_at_entry) and atr_at_entry > 0:
                floor_price = entry_row['low'] - self.ATR_MULTIPLIER * atr_at_entry
                if floor_price > 0:
                    atr_floor_pct = (floor_price / current_rate) - 1
        return atr_floor_pct

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: Optional[float], max_stake: float,
                            leverage: float, entry_tag: Optional[str], side: str,
                            **kwargs) -> float:
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        hist = df.loc[df['date'] < current_time]
        if hist.empty:
            return proposed_stake
        rv = hist['realized_vol'].iloc[-1]
        if pd.isna(rv) or rv <= 0:
            lev = 1.0
        else:
            lev = min(self.MAX_LEV, self.TARGET_VOL / rv)
        stake = proposed_stake * lev
        if min_stake is not None and stake < min_stake:
            return min_stake
        return stake
