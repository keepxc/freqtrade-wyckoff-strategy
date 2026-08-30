"""
AI500V1DryRun — VergeX trending-crypto 资金流+OI 驱动的探索性 dry-run 策略
=============================================================
探索性——非结论，禁止引用为证据。本策略无法回测（数据源为实时榜单，
无历史回放能力，见 TASK111 尽调）。本文件只用于 dry-run 观察，运行结果
不构成 CLAUDE.md 三层终止校验意义上的"结论"，不得据此判定策略✅/❌，
也不写入任何策略基线文档。

数据源替换（2026-08-18 用户确认）：原 trending-category?key=ai500 免费
端点仅恒返 3 币（GPS/BTW/XPL）、22 小时零变化，已证伪。替换为逆向发现的
完整端点 trending-crypto：
  - tab=net_flow：资金净流入榜（amount / rank / price_delta_percent）
  - tab=oi：OI 榜（current_oi / oi_delta_percent / oi_delta_value ...）

用户确认口径（2026-08-18）：
  1. 入场：过去 30m net_flow 排名中位数 ≤ 3 且 当前 oi_delta_percent > OI_MIN
     （中位数=50%分位，等价于"超过一半时间在前 3"，无需另设占比阈值）
  2. 退出：当前 net_flow rank 跌出前 10 立即退（灵敏退出，不经 30m 中位数平滑；
     dry-run 观察发现对称版中位数退出滞后、退出即顶点）
  3. 执行粒度 30m K 线：所有时间窗口（rank 中位数 / OI / BTC bias）均对齐 30m
  4. 弃用 net_long / net_short（语义不明：net_long 可为负、之和≠current_oi、
     与页面 Bias 不对应）；OI 过滤只用当前 oi_delta_percent，不对其要求 30m 持续度
  5. max_open_trades=3 保留；ATR 硬止损 + 波动率目标仓位沿用原实现（窗口按
     30m 换算保持时间语义不变）
  6. 每次抓取写入历史记录（netflow_rank + median_rank + oi_delta_percent 等），
     为未来可能的前瞻验证积累数据（同 TASK110 discovery_daily_snapshot 先例）

榜单变化频率实测（2026-08-18，每 60s × 30 次采样）：net_flow 前 5 每分钟
55% 概率成员变化（约 1.5 分钟换 1 个）、oi 前 5 每分钟 32%；核心前 3 稳定、
边缘第 4-5 名频繁进出。故信号用 30m 排名中位数平滑分钟级噪声，而非瞬时排名。

架构限制（技术验证，非用户判据决定）：freqtrade 该版本的
PairListResolver.user_subdir 为 None，不支持从 user_data/pairlists 加载
自定义 pairlist 插件，无法做"动态白名单"。改为内置 VolumePairList 提供
候选池（成交量活跃的 USDT-M 合约），策略内部按 net_flow+OI 条件过滤实际
入场，白名单里其余 pair 不会被交易。

=============================================================
决策历史（2026-08-27 追加）
=============================================================
- 08-27 回退到 16eee16（netflow_oi_exit 退出）+ 币安 OI 主源 + VolumePairList
  200 动态池（修复"新趋势币进不了候选池"缺陷）。数据验证：
  cbb29ce 四象限退出期 26 笔 -12.15 vs 回退后 19 笔 +1.38。
- 08-27 滑点分析：限价单执行无滑点（64 笔开仓 60 笔 0-10s 成交），
  "滑点"实际是 30m 信号 vs 成交的时间差；平仓超时 1min 改 5min 减少
  市价兜底频率（unfilledtimeout.exit=5）。
- 08-27 BTC 方向过滤（本版实现）：开仓要求 BTC 前 30min 涨幅 > 0。
  66 笔历史验证：BTC_30m 微跌 26 笔 -10.63（最大亏损源）vs 微涨 31 笔
  +1.34；加过滤后策略从 -11.59 → +2.38。
- 【TASK-待办】做空方向：等策略稳定后实现。当前 can_short=False，
  net_flow 是流入榜（多头信号），做空需另一套信号（net_short 或
  BTC 跌时反向），2026-08-27 用户确认"等策略稳定再实现"。

=============================================================
决策历史（2026-08-28 追加）
=============================================================
- 08-28 移动止损阶梯（custom_stoploss）：持仓峰值盈利触发止损上移。
  档位 0.5%→保本(+0.1%手续费) / 1.0%→锁0.4% / 1.5%→锁0.9% /
  2.0%→锁1.4% / 2.5%→锁1.9% / 3.0%→锁2.4%。不主动卖，netflow_oi_exit
  仍管 rank 退出。66 笔验证：阶梯止盈 37.28% vs 实际 33.92%。
- 08-28 BTC 1h 拦截【已证伪】: 试图加"BTC 前 1h 涨幅 < X 才开仓"避免
  追高。数据否决：BTC 1h 涨 >0.3% 占 23% 时间（常态噪声非过热信号），
  且误杀 #74 TRUMP 这类"BTC 涨但山寨独立启动"的好单。维持 30m 过滤。
- 08-28 开仓后快速止损【已证伪】: 5min/10min 浮亏超阈值即砍。
  数据否决：所有阈值都是负改善（0.5% 阈值 -3.01U），因 AI500 盈利单
  普遍"先浮亏后盈利"（#52 BTR 浮亏-1.74%后涨到+34.6%峰值），
  快速止损=在洗盘最低点割肉，杀掉最赚钱的单。
- 08-28 BTC 方向过滤 bug 修复【重要】: _get_btc_bias 失败返回 None，
  原逻辑 btc_ok=None 时放行 → #77 ETH 在 BTC 跌 -0.535% 时误开仓。
  修复：失败保留上次成功值；btc_ok 仅当 btc_pct>0 才为 1（None 拦截）。
- 08-28 观察结论：昨天 7 笔 -1.12U，#76 BTC -0.74U 是"BTC 涨但开仓后
  反转"的固有成本（方向过滤救不了），#77 -0.07U 是 bug 已修复。

=============================================================
决策历史（2026-08-29 追加）
=============================================================
- 08-29 BTC 大单净流信号【数据验证强信号，暂观察】: 糖哥 stock.tanggestock.com
  大单追溯接口（币安 aggTrade 落库 57 天，免鉴权）。89 笔回溯：开仓时
  BTC 大单净流<0 的 45 笔 -11.98U vs 净流>=0 的 44 笔 -0.12U——亏损几乎
  全部集中在"大单资金流出时开仓"。拦截 30 笔亏损 -15.88U，误杀 15 笔
  盈利 +3.89U，净改善 +11.98U（-12.10 → -0.12）。
- 08-29 决策：按用户"CSV同步积累数据"，只记录不改入场——CSV 新增
  btc_large_net_30m（与策略周期对齐，滞后~30min）+ btc_large_net_15m
  （更灵敏，滞后~15min）两列，每轮 bot_loop_start 拉取写入（失败保留
  上次值）。积累 3-5 天真实数据后再决定是否作为入场条件（AND 大单
  净流>0）。曾用 5m/1h 两列，实测 30m 接口存在后改为 30m/15m。
- 08-29 数据源备注：接口只对主流币（BTC/ETH spot+perp、SOL 仅 spot）
  有数据，山寨币（TRUMP）无——但对 BTC 方向过滤应用足够。
- 08-29 OI_MIN 0→2【上线】：数据验证 OI 阈值与做多收益单调正相关，
  89 笔实盘回测 OI>=2% 保留 8 笔全赚 +0.71U vs 当前 68 笔 -12.51U；
  代价开仓频率 -83%（一天可能 1-2 单）。只影响做多；做空 TASK 待研。
  大单净流分析结论：-50~-10M 中度流出是最大毒区（16 笔 -9.49U），
  OI>0 + 大单>-10M 组合 -12.51 → -1.61U（改善未转正），暂不作为
  入场条件，继续 CSV 观察积累。
- 08-30 BTC 15m MTF 评分【观察版上线】：复刻 MACD v2.4 MTF 矩阵单周期
  逻辑（MACD>Signal / MACD 升 / Signal 升 / Hist 区域 四项各 ±1 → -4~+4），
  拉币安 15m 合约 K 线算 BTC 评分。只写独立 CSV
  ai500_btc_mtf_history.csv（含 bias/大单净流对照列），不进入场。
  与 AI500 开仓/收益对照 2-4 周后决定是否转正为入场过滤。
"""
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
]


class AI500V1DryRun(IStrategy):
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
    RANK_WINDOW_SEC = 1800  # 中位数回看窗口 = 30m（与 30m K 线粒度对齐，原 1h）
    MIN_WINDOW_SEC = 900    # 中位数最少覆盖 15 分钟，样本不足视为不在前 3
    RANK_ABSENT = 99        # 不在 net_flow 榜时的 rank 占位值
    OI_MIN = 1.0            # OI 过滤：当前 oi_delta_percent > 1% 才可入场（30m 窗口等价阈值）
    # 08-30 数据验证（71 笔实盘双窗口回测）：
    #   1h OI>2% 保留 12 笔 -2.60U（含 #12 ENA -0.71U / #71 PROM -0.42U 等 1h 强但 30m 已熄火陷阱）
    #   30m OI>1.0% 保留 7 笔 -0.87U（拦截 5 笔 1h 假强单，净改善 +1.73U）
    #   30m OI>0.8% 9 笔 -1.17U | 30m OI>1.5% 5 笔 -0.89U → 1.0 为最优档
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
        self.oi_delta_pct: dict[str, float] = {}     # symbol -> oi_delta_percent
        self.btc_bias_pct: float | None = None       # BTC 前 30min 涨幅 %（方向过滤）
        self.btc_mtf_15m: int | None = None          # BTC 15m MACD 共振评分 -4~+4（观察版）
        self.btc_large_net_30m: float | None = None  # BTC 大单净流 USD（最近 30 分钟，与策略周期对齐）
        self.btc_large_net_15m: float | None = None  # BTC 大单净流 USD（最近 15 分钟，更灵敏）
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

        接口：/api/realtime-events/large-trades?symbol=BTC&interval=<5m|15m|30m|1h>&anchor=<now>&market=perp
        返回周期桶：buyUsd/sellUsd（主动买/卖金额），净流 = buyUsd - sellUsd。
        实测刷新：后端流式实时落库，5m 桶滞后约 5min，30m 桶滞后约 30min。
        数据源：币安现货逐笔成交流（aggTrade），2026-07 起落库，免费免鉴权。
        失败返回 None（调用方处理：保留上次值）。
        """
        try:
            anchor = int(time.time() * 1000)
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

    def bot_loop_start(self, current_time: datetime, **kwargs) -> None:
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
            # OI 主源：币安 30m 累计变化率（与 30m K 线粒度对齐）
            # Fallback：币安失败时退回 Vergex（数据可能更旧但总比没有强）
            # 不一致告警：双源都拿到且差 > 2pp 时 log warning（捕捉 RVN/ONT 这类异常）
            oi_delta_value = o.get("oi_delta_value")
            vergex_pct = o.get("oi_delta_percent")
            binance_pct = self._get_binance_oi_change(sym, window_min=30) if self.binance else None
            oi_delta_percent = None
            oi_source = None
            if binance_pct is not None and vergex_pct is not None:
                # 双源都有：取币安（交易所一手），但不一致时告警
                oi_delta_percent = binance_pct
                oi_source = "binance"
                if abs(binance_pct - vergex_pct) > 2.0:
                    logger.warning(
                        f"OI mismatch {sym}: binance={binance_pct:+.2f}% vs "
                        f"vergex={vergex_pct:+.2f}% (diff={binance_pct-vergex_pct:+.2f}pp)"
                    )
            elif binance_pct is not None:
                # 只有币安有：填币安
                oi_delta_percent = binance_pct
                oi_source = "binance"
            elif vergex_pct is not None:
                # 币安失败：退回 Vergex
                oi_delta_percent = vergex_pct
                oi_source = "vergex_fallback"
                logger.debug(f"OI fallback to vergex for {sym}: {vergex_pct:+.3f}%")
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
        cur_rank = self.current_rank.get(sym, self.RANK_ABSENT)
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

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe['median_rank'] <= self.ENTRY_MEDIAN) & (dataframe['oi_ok'] == 1)
            & (dataframe['btc_ok'] == 1),
            ['enter_long', 'enter_tag']
        ] = (1, 'netflow_oi_entry')
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            dataframe['current_rank'] > self.EXIT_RANK,
            ['exit_long', 'exit_tag']
        ] = (1, 'netflow_oi_exit')
        return dataframe

    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                         current_rate: float, current_profit: float, **kwargs) -> Optional[float]:
        """移动止损阶梯 + ATR 地板。

        - ATR 硬止损：入场信号K线 low - 2xATR，绝对地板价（永不比这更宽松）
        - 移动止损：持仓峰值盈利达到档位后，止损逐级上移到开仓价+X%
          回吐到止损位自动卖出（保护利润，避免"盈利变亏"）
        - 不主动卖（netflow_oi_exit 仍管 rank 退出，让大趋势奔跑）
        """
        # ATR 地板
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

        # 移动止损阶梯：用持仓期间峰值盈利触发
        # trade.max_rate = 持仓期间最高价（freqtrade 维护）
        open_rate = trade.open_rate
        if open_rate and trade.max_rate and trade.max_rate > open_rate:
            peak_profit_pct = (trade.max_rate - open_rate) / open_rate * 100
            # 找到已触发的最高档
            trail_pct = None
            for trigger, lock_pct in self.TRAIL_STAGES:
                if peak_profit_pct >= trigger:
                    trail_pct = lock_pct  # 触发后锁 X% 利润
            if trail_pct is not None:
                # 止损价 = 开仓价 × (1 + 锁利%)
                stop_price = open_rate * (1 + trail_pct / 100)
                trail_stop_pct = (stop_price / current_rate) - 1
                # 取更紧的（max 因为 pct 是负的亏损比例，越接近 0 越紧）
                if atr_floor_pct is None or trail_stop_pct > atr_floor_pct:
                    return trail_stop_pct

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
