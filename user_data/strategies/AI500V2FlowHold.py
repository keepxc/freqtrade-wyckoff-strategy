"""
AI500V2FlowHold — net_flow 瞬时+中位双 rank 入场 + 固定 3h 持有
==============================================================
探索性部署——**不是已验证的策略**。判据未预注册，禁止引用本策略的实盘
数字为结论，除非事先冻结判据并跑独立 OOS 窗口。

设计依据：AI500_STRATEGY_DESIGN.md（2026-09-04，8 天 × 526 标的，样本内）

为什么长这样
------------
8 天数据做完留一日稳健性检验后，**只剩一条结论活下来**：
  「瞬时 rank>3 但中位 rank<=3」那批 4h 市场中性收益 -0.812%，
  90%CI [-1.509,-0.175]，8 折留一日只有 1 折折断。
  —— 30m 中位数窗口会放行已经掉出榜的过气信号，必须用瞬时 rank 卡掉。

其余全部不可判定（CI 跨零 + 留一日 7~8/8 折断）：入场信号自身有没有正边缘、
止盈/移动止损/止损三个出场机制孰优、冷却期长短、追高与否、top-N 排序规则。

所以设计原则不是「选最优」，而是**最少自由参数 + 能活到样本够为止**：
  - 入场加 entry_rank<=3（唯一有依据的改动）
  - 去掉 OI 闸门（用户 2026-09-04 拍板；条件于 rank 后 MDE 从 1.3% 膨胀到 14.3%）
  - 出场退化为「固定 3h + 5% 止损」两条路径，不加止盈、不加移动止损、不加信号出场
    —— 每多一个出场机制就是一个数据分辨不出的自由参数
  - 等权、全取，不做排序筛选

功效现实
--------
保留组 MDE ≈ 1.31%/笔，合理预期效应量级 0.3%/笔。按日区块自举 sd ∝ 1/√天数：
  N = 8 × (1.31/0.30)² ≈ 153 天 ≈ 5 个月
**这套配置的期望值在现有样本上无法与零区分，需要约 5 个月才能判定。**
仓位必须小到能承受这段时间的波动（8 天里逐日盈亏在 -33% ~ +67% 之间摆）。

收益分布是尾部主导的：4h 原始收益前 5% 的信号贡献 +681%，后 95% 合计 -528%。
因此任何"改善胜率/中位数"的直觉在这里都不适用，中位交易本来就是亏的（-0.12%）。

与 AI500V1MacdExitDryRun 的差异
-------------------------------
  1. 入场加 entry_rank<=3；去掉 OI 闸门
  2. 整个 MACD whisper 出场链路删除（已 ❌ 证伪：NEW 卖出集仅覆盖 0.93% 的 bar，
     48h 内 101 笔只触发 4 次，结构上不构成出场规则）
  3. 去掉 ATR 地板止损与 rank 灾难兜底，改固定 5% stoploss
  4. max_open_trades 3 -> 8（3 会硬截断并发，等于引入无依据的隐式排序）
  5. net_flow/OI/BTC 数据采集与历史落盘全部保留不变

回测限制
--------
本策略结构上**不可回测**：populate_indicators 把实时内存快照广播到整段
dataframe，freqtrade backtesting 出来的任何数字都无意义。验证只能靠
dry-run 实跑 + 事后用 ai500_netflow_hist_v2.csv.gz 离线重放。
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

# 数据落盘目录 = 本策略文件所在的 user_data/（strategies/ 的上一级）。
# 原来是硬编码绝对路径，在 docker 里挂载点不同会写不进去；
# 这样在 bot 原生环境（.../user_data/strategies/）与容器（/freqtrade/user_data/strategies/）
# 下都指向同一个 user_data，落盘文件与此前完全一致。
_AI500_UD = os.environ.get(
    "AI500_DATA_DIR",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

TRENDING_URL = "https://vergex.trade/trending-crypto"
# 容器内 127.0.0.1 指向容器自身，拿不到宿主机代理；用 AI500_PROXY 覆盖
# （docker 下设 http://host.docker.internal:7890）。不设时与原行为一致。
_AI500_PROXY = os.environ.get("AI500_PROXY", "http://127.0.0.1:7890")
TRENDING_PROXY = {"http": _AI500_PROXY, "https": _AI500_PROXY}
FETCH_TIMEOUT = 10
# 2026-09-05 用户拍板 50 -> 100：实测 API 上限为 100（limit=200 仍只返回 100）。
# 可见宇宙翻倍，对未来数据积累是纯增益。注意这改变了数据生成过程：原先排 51~100 的
# 币记为 RANK_ABSENT=99，现在记真实名次，median_rank 的数值语义随之变化（对 <=3
# 闸门几乎无影响，但两段数据在 rank 分布上不再同质）。故每行落盘 list_limit 标注
# 切换时点，禁止把两段当同质样本混用。
LIST_LIMIT = 100

HISTORY_PATH = os.path.join(_AI500_UD, "ai500_netflow_oi_history.csv")
HISTORY_FIELDS = [
    "fetched_at_utc", "symbol", "pair", "netflow_rank", "median_rank",
    "netflow_amount", "oi_delta_percent", "oi_source", "oi_delta_value", "price", "price_delta_percent",
    "btc_large_net_30m", "btc_large_net_15m", "list_limit",
]
# BTC MTF 评分独立 CSV（BTC 全局量，避免污染 215 万行主 CSV 的列结构）
MTF_HISTORY_PATH = os.path.join(_AI500_UD, "ai500_btc_mtf_history.csv")
MTF_HISTORY_FIELDS = [
    "fetched_at_utc", "btc_mtf_15m", "btc_bias_pct", "btc_large_net_30m", "btc_large_net_15m",
    "btc_imb_buy_max", "btc_imb_sell_max",
]
# AI500 本尊 score 榜历史（2026-09-02 恢复采集：TASK 121 曾判"恒3币无价值"弃用，
# 但端点已改版为动态 4 币 + compositeZ 综合评分。真 AI500 用法：score≥80 入场 / ≥67 条件
# / <60 平仓（公开策略 prompt 规则），与 net_flow 榜代理不是一回事——需独立积累验证）
SCORE_HISTORY_PATH = os.path.join(_AI500_UD, "ai500_score_history.csv")
SCORE_HISTORY_FIELDS = [
    "fetched_at_utc", "symbol", "pair", "score", "signal_peak", "price",
    "start_price", "start_time", "change_pct_value",
]
SCORE_FETCH_INTERVAL_S = 300  # score 每小时刷新（generatedAt ~30s 变但 score 低频），5 分钟采样足够
# 币安 aggTrades 逐笔目录（ai500_agg_collector.py 常驻采集）
AGG_TRADES_DIR = os.path.join(_AI500_UD, "agg_trades")
IMB_LOOKBACK_MIN = 30   # imbalance 观察窗口 = 30m（与策略周期对齐）
IMB_PRICE_STEP = 78.0   # 价格分层步长 ≈ 0.1%（78 USDT）
IMB_RATIO_THRESH = 3.0  # 失衡阈值 300%


class AI500V2FlowHold(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = '30m'
    can_short = False
    process_only_new_candles = True
    startup_candle_count = 100

    # 固定 5% 止损：纯风控，不是收益手段。
    # 配对差（止损5% - 纯持有4h）= -0.247%，90%CI [-0.894,+0.265]，留一日 8/8 折断
    # —— 收益上近似免费，作用是限制单笔尾部损失。
    stoploss = -0.05
    # 固定持有 3h：第一档 100 = 永不按 ROI 出场；第二档 180 分钟设 -1 = 无条件平仓。
    # 3h 是市场中性收益的点估计峰值(+0.338%)，但 CI 跨零 —— 选它是在噪声里挑最大值，
    # 唯一可辩护的理由是它同时让并发持仓数落在可控区间。
    minimal_roi = {"0": 100, "180": -1}
    use_exit_signal = False        # 无信号出场：出场只由时间(3h)和止损(5%)决定
    exit_profit_only = False
    ignore_roi_if_entry_signal = False
    trailing_stop = False          # 不加移动止损：配对差 -0.206%，CI 跨零，8/8 折断
    use_custom_stoploss = False    # 去掉 ATR 地板，改用框架层固定 5%

    # 8 天数据（BTC 闸门开 + 3h 持有 + 持仓期内同标的不重入）：
    #   入场 190 笔 = 23.8/天，并发持仓 中位 3 / p90 5 / 最大 7
    # 设 3 会硬性截断并发（先到先得 = 一个无依据的隐式排序规则）；设 8 覆盖最大值，
    # 保证「全取」—— 各种 top-N 排序规则实测 CI 全部跨零，选不出更好的。
    max_open_trades = 8

    # 币安 OI 缓存 TTL（秒）。OI 源数据是 5m 粒度，150s 内重复请求拿到的是同一根 bar，
    # 纯属浪费。实际 TTL 再加 hash(symbol)%60 的抖动，把 100 个标的的刷新错开，
    # 避免同时到期造成请求尖峰。
    OI_CACHE_SEC = 150
    OI_NEG_TTL_SEC = 3600   # 「该 symbol 币安无 OI」的负结果多久重试一次
    # 瞬时故障（429 / 代理超时 / 网络）的缓存时长。必须与上面那条严格区分：
    # 「币安没有这个合约」是**稳定事实**，缓存 1h 合理；「这次请求挂了」是**瞬时状态**，
    # 按 1h 缓存会把一次 429 风暴变成整整一小时的 OI 空窗（2026-09-05 实测踩中：
    # 覆盖率掉到 4.8%）。但也不能不缓存——故障期每 30s 全量重试正是 429 的放大器。
    # 60s 是折中：比 30s 主循环长（不放大），比 5m OI 粒度短（不丢数据）。
    OI_ERR_TTL_SEC = 60

    ENTRY_RANK = 3          # 入场：**瞬时** net_flow 排名 ≤ 3（2026-09-04 新增）
                            #   依据：「瞬时>3 但中位<=3」那批 -0.812%/4h，
                            #   90%CI [-1.509,-0.175]，留一日 1/8 折断 —— 本次唯一稳健结论。
                            #   30m 中位数窗口会放行已掉出榜的过气信号，靠这条卡掉。
    ENTRY_MEDIAN = 3        # 入场：过去 30m net_flow 排名中位数 ≤ 3（与 30m K 线粒度对齐）
    EXIT_RANK = 10          # 退出：当前 rank 跌出前 10 立即退（灵敏，不经中位数平滑）
    RANK_EXIT_CONFIRM = 3   # 退出平滑（09-01 方案A）：连续 3 次采样 rank>10 才平（≈1.5min 确认）
                            #   数据验证（16 笔 rank 轨迹重放）：N=2 与 N=3 效果相同（30s 采样粒度），
                            #   真掉队持续 >10 不受影响，单次抖动被过滤；N=5 多保 2 笔但增加真掉队拖尾风险
    RANK_WINDOW_SEC = 1800  # 中位数回看窗口 = 30m（与 30m K 线粒度对齐，原 1h）
    MIN_WINDOW_SEC = 900    # 中位数最少覆盖 15 分钟，样本不足视为不在前 3
    RANK_ABSENT = 99        # 不在 net_flow 榜时的 rank 占位值
    ENTRY_RANK_STALE_SEC = 900  # entry_rank 陈旧上限：快照距今超过 15min 视为无状态
                            #   口径直接沿用离线标定的 STALE_S=900（不是新引入的自由参数）。
                            #   为什么必须有：current_rank 只在抓榜成功时更新，抓失败
                            #   bot_loop_start 直接 return —— vergex 挂 1 小时，它就保留
                            #   1 小时前的 rank 并据此放行开仓。median_rank 无此问题
                            #   （每轮从 30m deque 重算，deque 空则删 key → 99 → 不入场）。
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

    # ATR 只作观察记录（写进 dataframe 供事后分析），**不再参与止损**。
    # 止损已改为框架层固定 -5%，见上方 stoploss。
    ATR_PERIOD = 40         # 30m K 线 ×40 = 20h 窗口

    # 移动止损阶梯已删除（原 TRAIL_STAGES）。
    # 依据：移动止损 3% 相对纯持有的配对差 -0.206%，90%CI [-1.029,+0.526]，
    # 留一日 8/8 折断 —— 数据分辨不出它有没有用。而收益分布是尾部主导的
    # （前 5% 信号贡献 +681%，后 95% 合计 -528%），移动止损结构上砍的正是右尾。
    # 在「分辨不出 + 机制上可疑」两条同时成立时，选不加（少一个自由参数）。

    # 仓位：等权，不做反波动率缩放。
    # V1 继承的 custom_stake_amount 按 lev = min(1.0, 0.20/年化实际波动) 缩仓，
    # 实测把应配 100 USDT 的两笔压到 11 / 25 USDT（因 MAX_LEV=1.0 只缩不放）。
    # 去掉的理由不是「哪种风控更好」，而是**口径一致**：8 天分析、MDE 1.31%、
    # 以及「约 5 个月才能把 0.3%/笔的边缘与零分开」的累积计划，全部按**每笔等权
    # 收益**计算。实盘若按波动率加权，采到的 P&L 就不是判据所写的那个估计量，
    # 将来无法直接接上 OOS 判据。本次部署的目的是采数据，口径必须对齐。
    # 尾部主导（前 5% 信号贡献 +681%）也意味着无法预知哪笔是赢家，任何加权都是
    # 在噪声上下注。见 AI500_STRATEGY_DESIGN.md §7。

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.rank_history: dict[str, deque] = {}     # symbol -> deque[(ts, rank)]
        self.median_rank: dict[str, float] = {}      # symbol -> 30m 中位数排名
        self.current_rank: dict[str, int] = {}       # symbol -> 最近一次采样 rank
        self.rank_sampled_at: dict[str, float] = {}  # symbol -> 该 rank 的采样时刻（epoch 秒）
        self.last_rank: dict[str, int] = {}          # symbol -> 上一次成功采样 rank（接口失败兜底，修复09-01）
        self.oi_delta_pct: dict[str, float | None] = {}     # symbol -> oi_delta_percent（None=币安取不到，保守拦截）
        # 币安 OI 请求节流（2026-09-05 修 429）：OI 本身是 5m 粒度，每 30s 轮询一次
        # 是 10 倍浪费；LIST_LIMIT 50->100 后每轮 100+ 次请求，叠加 bot 机器同 IP 的
        # 流量撞上 2400 req/min 的 IP 级上限（fetch_l2_order_book 报 429 只是受害者，
        # 不是元凶）。按 symbol 缓存结果 + 记住解析出的合约名（避免每轮都重试 1000 前缀）。
        # 值是 (写入时刻, 值或None, 本条自己的 TTL)——TTL 随条目走而不是全局常量，
        # 因为「查到了」「币安没这个合约」「这次请求挂了」三种结果的有效期完全不同。
        self._oi_cache: dict[tuple[str, int], tuple[float, float | None, float]] = {}
        self._oi_symbol_resolved: dict[str, tuple[str | None, float]] = {}
        self.btc_bias_pct: float | None = None       # BTC 前 30min 涨幅 %（方向过滤）
        self.btc_mtf_15m: int | None = None          # BTC 15m MACD 共振评分 -4~+4（观察版）
        self.btc_large_net_30m: float | None = None  # BTC 大单净流 USD（最近 30 分钟，与策略周期对齐）
        self.btc_large_net_15m: float | None = None  # BTC 大单净流 USD（最近 15 分钟，更灵敏）
        self.btc_imb_buy_max: float | None = None    # BTC 价格分层 buy imbalance 最大比（观察）
        self.btc_imb_sell_max: float | None = None   # BTC 价格分层 sell imbalance 最大比（观察）
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
        # --- 缓存命中判定（见 __init__ 里 _oi_cache 的说明）---
        # TTL 加按 symbol 的确定性抖动，避免 100 个标的同时到期造成请求尖峰。
        now_s = time.time()
        key = (symbol, window_min)
        ok_ttl = self.OI_CACHE_SEC + (hash(symbol) % 60)
        hit = self._oi_cache.get(key)
        if hit is not None and (now_s - hit[0]) < hit[2]:
            return hit[1]
        # 待尝试的 symbol 列表：合约名已解析过就只打那一个（省掉 1000 前缀重试）
        rec = self._oi_symbol_resolved.get(symbol)
        if rec is not None and rec[0] is not None:
            candidates = [rec[0]]                      # 合约名已知，只打这一个
        elif rec is not None and (now_s - rec[1]) < self.OI_NEG_TTL_SEC:
            # 已知币安查不到该 symbol 的 OI，且负结果尚未过期 → 本轮不请求。
            # 过期后会重新走完整解析（新上市的币可能稍后才有 OI），不是永久拉黑。
            self._oi_cache[key] = (now_s, None, ok_ttl)
            return None
        else:
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
        # 是否发生过瞬时异常。用于区分「币安干净地告诉我们没有这个合约」和
        # 「这次没问到（429/超时）」——后者绝不能写进 _oi_symbol_resolved 负结果。
        transient_err = False
        for bs in candidates:
            try:
                oi_hist = self.binance.fapiDataGetOpenInterestHist({
                    'symbol': bs,
                    'period': period,
                    'limit': limit,
                })
                if len(oi_hist) >= 2:
                    # 合约名有效（返回了数据）→ 记住它，后续不再试 1000 前缀。
                    # 注意：即便下面跨度校验不通过也要记，因为那是数据问题不是名字问题。
                    self._oi_symbol_resolved[symbol] = (bs, now_s)
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
                        val = (oi_now - oi_past) / oi_past * 100.0
                        self._oi_cache[key] = (now_s, val, ok_ttl)
                        return val
            except Exception as e:
                transient_err = True
                logger.debug(f"Binance OI fetch failed for {bs} window={window_min}m: {e}")
                continue
        # 全部候选都没拿到值。分两种情况处置，不可混为一谈：
        if transient_err:
            # 请求本身挂了（429/超时/代理）——这不是「该 symbol 没有 OI」的证据。
            # 不写 _oi_symbol_resolved 负结果（否则一次风暴静音整整一小时），
            # 只做 60s 短缓存防止故障期全量重试放大 429。
            self._oi_cache[key] = (now_s, None, self.OI_ERR_TTL_SEC)
            return None
        # 币安干净地返回了但无可用数据 → 若从未解析出可用合约名，记为「该 symbol 无 OI」，
        # 之后靠 OI_NEG_TTL_SEC 周期性重试（新上市的币可能稍后才有 OI），不是永久拉黑。
        cur = self._oi_symbol_resolved.get(symbol)
        if cur is None or cur[0] is None:
            self._oi_symbol_resolved[symbol] = (None, now_s)
        self._oi_cache[key] = (now_s, None, ok_ttl)
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
            self.rank_sampled_at[sym] = now   # 陈旧度判定用（入场闸门）
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
                "list_limit": LIST_LIMIT,   # 榜单深度，标注 50->100 切换时点
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
            # 字段结构变更时轮转旧文件：直接 append 会让新行比表头多一列，
            # CSV 变成锯齿状而读取时静默错位。轮转同时也把 list_limit=50
            # 与 =100 两段数据物理分开，符合「禁止当同质样本混用」。
            if not write_header:
                with open(HISTORY_PATH, "r", encoding="utf-8") as f:
                    old = (f.readline() or "").strip().split(",")
                if old != HISTORY_FIELDS:
                    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                    os.rename(HISTORY_PATH, "%s.%s.bak" % (HISTORY_PATH, ts))
                    write_header = True
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
        # 入场用的瞬时 rank：**不走 last_rank 兜底**。
        # last_rank 兜底是给出场用的（防接口失败误平仓，修复 #16/#46/#76）；
        # 用在入场上等于拿一个任意陈旧的 rank 放行开仓 —— 风险方向正好相反。
        # 取不到 → RANK_ABSENT(99) → 不入场，保守。
        entry_rank = self.current_rank.get(sym, self.RANK_ABSENT)
        sampled_at = self.rank_sampled_at.get(sym)
        if sampled_at is None or (time.time() - sampled_at) > self.ENTRY_RANK_STALE_SEC:
            entry_rank = self.RANK_ABSENT       # 快照过旧 → 视为不在榜 → 不入场
        oi_pct = self.oi_delta_pct.get(sym, None)
        dataframe['median_rank'] = median
        dataframe['current_rank'] = cur_rank
        dataframe['entry_rank'] = entry_rank
        # oi_ok 只作观察记录，**不再参与入场闸门**（2026-09-04 用户拍板移除）
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
        """入场闸门（2026-09-04 重设，依据 AI500_STRATEGY_DESIGN.md §7）。

        1. entry_rank <= 3   瞬时排名。本次唯一经得起留一日检验的结论。
        2. median_rank <= 3  保留原闸门（放松递减、收紧是单币单日假象，⭕）
        3. btc_ok            保留（>0 vs <=0 差 0.87pp，方向对但 CI 跨零，⭕）

        OI 闸门已移除：条件于 rank 之后中位数不升反降，MDE 从 1.3% 膨胀到 14.3%，
        无法自证有益（⭕ 非 ❌ —— 功效不足，不入证伪表）。
        """
        dataframe.loc[
            (dataframe['entry_rank'] <= self.ENTRY_RANK)
            & (dataframe['median_rank'] <= self.ENTRY_MEDIAN)
            & (dataframe['btc_ok'] == 1),
            ['enter_long', 'enter_tag']
        ] = (1, 'netflow_rank_entry')
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """不做信号出场。

        出场只有两条路径：
          - minimal_roi 的 180 分钟档 → 持满 3h 无条件平仓
          - stoploss = -5%           → 单笔尾部损失兜底

        原来的 rank 秒退 / MACD whisper / ATR 地板 / rank>50 灾难兜底全部去掉：
        三个出场机制的配对差（同一批入场、同一条价格路径，只换出场规则）实测
        全部 CI 跨零、留一日 8/8 折断 —— 数据分辨不出它们，每多一个就是一个
        无依据的自由参数。
        """
        return dataframe
