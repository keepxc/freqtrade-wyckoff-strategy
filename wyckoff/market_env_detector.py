"""
Task 2.13: 市场环境识别器（Market Environment Detector）

威科夫理论：所有威科夫分析以"当前是区间还是趋势"为前提。
用 EMA 判断趋势是滞后指标，且无法区分"区间震荡"和"趋势下跌"。

核心输出字段：
    market_env            : str   — 'trading_range' / 'markup' / 'markdown' / 'transition'
    market_env_confidence : float — 0.0 - 1.0（当前环境判断的置信度）
    hh_score              : float — 近期高点相对于前期高点偏移（ATR单位，正=Higher High）
    hl_score              : float — 近期低点相对于前期低点偏移（ATR单位，正=Higher Low）
    trend_score           : float — 综合方向评分（正=多头结构，负=空头结构）

判断逻辑（基于价格结构，完全不依赖 EMA）：

    半窗口比较法（Half-Window Comparison）：
        - 将 lookback 窗口一分为二：最近 half 根 vs 前 half 根
        - 比较两个子窗口的最高价（High）和最低价（Low）
        - ATR 归一化偏差分：消除不同价格水平下的比较失真

    markup (上涨趋势):
        - 近期最高价 > 前期最高价（Higher High），偏差 > markup_thresh × ATR
        - 近期最低价 ≥ 前期最低价（Higher Low or 不创新低），偏差 > -1.0 × ATR
        - 综合特征：价格结构持续抬升

    markdown (下跌趋势):
        - 近期最低价 < 前期最低价（Lower Low），偏差 < -markdown_thresh × ATR
        - 近期最高价 ≤ 前期最高价（Lower High or 不创新高），偏差 < 1.0 × ATR
        - 综合特征：价格结构持续下移

    trading_range (横盘区间):
        - 既不满足明显上涨也不满足明显下跌条件
        - 近期与前期区间高度重叠，价格在固定带内震荡

    transition (过渡期):
        - markup 与 markdown 持久化标签同时激活（矛盾信号，方向不明）
        - 用于过渡阶段，系统将回退到 EMA 兜底条件

持久化机制：
    - 一旦进入 markup/markdown 状态，需要连续 persistence_bars 根K线反向信号才退出
    - 避免单根K线噪音频繁切换环境标签
    - markup 与 markdown 同时激活时标记为 transition（互斥保护）

信号路由（配合入场条件）：
    trading_range → Spring / Axis支撑（积累阶段优先信号）
    markup        → BUEC / LPS / SOS（推进阶段信号）
    markdown      → SOW / LPSY（Markdown阶段做空信号）；阻止新多头入场
    transition    → 保守，依赖 EMA 兜底

物理依据：
    - 半窗口比较比 EMA 更直接反映价格结构的实际变化
    - ATR 归一化确保在 BTC 不同价格区间（$10k vs $100k）下标准一致
    - 与 Composite Man 检测器互补：Composite Man 看意图，market_env 看结构
    - 与 Phase E Context 互补：Phase E 是短期加速识别，market_env 是中期结构定位

与系统集成：
    - populate_indicators 中调用 detect()，产出上述字段
    - _update_trading_permission 中：markdown 环境阻止新多头入场
    - populate_entry_trend 中：SOW/LPSY 在 markdown 环境下拥有更高优先级
    - Task 3.5 熊市防御：market_env = 'markdown' 触发 bear_market_mode 条件

验收标准（Task 2.13）：
    - 替换 EMA 后假信号减少 >30%（至少维持 baseline zero-regression）
    - 识别准确率 >75%（Walk-Forward 分段验证）
    - 与现有系统无硬冲突（分阶段替换 EMA 条件）
"""

import numpy as np
import pandas as pd
import logging
from pandas import DataFrame

logger = logging.getLogger(__name__)


class MarketEnvironmentDetector:
    """
    市场环境识别器（Task 2.13）

    基于价格结构的半窗口比较法，识别以下四种威科夫市场阶段大背景：
        markup        — 上涨趋势（Higher High + Higher Low）
        markdown      — 下跌趋势（Lower Low + Lower High）
        trading_range — 横盘区间（High/Low 波动在固定带内）
        transition    — 过渡期（矛盾信号，等待方向确认）

    完全不依赖 EMA，通过比较最近 half_window 根K线与再前 half_window 根K线
    的高低点结构来判断当前市场所处的威科夫阶段大背景。
    """

    def __init__(self, strategy=None):
        self.strategy = strategy

        # ── 窗口参数 ─────────────────────────────────────────────────
        self.half_window = 20
        # 半窗口大小：比较最近20根 vs 前20根
        #   total_window = half_window * 2 = 40 根K线
        #   1H 时间框架 ≈ 40 小时 ≈ 1.7 天（捕捉中期结构变化）

        # ── 方向判断阈值（ATR 倍数）──────────────────────────────────
        # markup  判断：hh_score > markup_thresh（近期高点高出前期高点 N 个 ATR）
        self.markup_thresh   = 1.5    # 近期最高价需比前期最高价高出 1.5x ATR
        # markdown 判断：hl_score < -markdown_thresh（近期低点低于前期低点 N 个 ATR）
        self.markdown_thresh = 1.5    # 近期最低价需比前期最低价低出 1.5x ATR
        # 同向条件宽松阈值（HH 时要求 HL 不能比前期低太多，反之亦然）
        self.counter_loose   = 1.0    # 容忍对面方向的偏差在 ±1.0 ATR 内

        # ── 持久化参数 ────────────────────────────────────────────────
        # 出现趋势信号后，在 persistence_bars 根K线内维持标签（避免噪音）
        self.persistence_bars = 5

    # ──────────────────────────────────────────────────────────────────
    # 主接口
    # ──────────────────────────────────────────────────────────────────

    def detect(self, dataframe: DataFrame) -> DataFrame:
        """
        主检测入口：计算所有市场环境字段

        Args:
            dataframe: 包含 OHLCV + atr 字段的 DataFrame
                       （atr 来自 ta.ATR(timeperiod=14)，在 populate_indicators 中已计算）

        Returns:
            添加了以下字段的 DataFrame：
                market_env            : str   ('markup'/'markdown'/'trading_range'/'transition')
                market_env_confidence : float (0.0 - 1.0)
                hh_score              : float (ATR 单位，HH 分)
                hl_score              : float (ATR 单位，HL 分)
                trend_score           : float (综合方向分，正=多头，负=空头)
        """
        half = self.half_window

        # 防御：数据不足时返回默认值
        if len(dataframe) < half * 2 + 5:
            dataframe['market_env']            = 'transition'
            dataframe['market_env_confidence'] = 0.5
            dataframe['hh_score']              = 0.0
            dataframe['hl_score']              = 0.0
            dataframe['trend_score']           = 0.0
            return dataframe

        # ── ATR（前瞻防护：用 shift(1) 的 ATR，消除当前K线影响）──────
        atr = dataframe['atr'].shift(1).fillna(method='bfill').replace(0, np.nan).fillna(1.0)

        # ── 近期窗口（最近 half 根）的最高/最低 ──────────────────────
        recent_high = dataframe['high'].rolling(half, min_periods=half // 2).max()
        recent_low  = dataframe['low'].rolling(half, min_periods=half // 2).min()

        # ── 前期窗口（再往前 half 根）的最高/最低 ────────────────────
        # shift(half) 先把时间轴后移 half 根，再对 rolling(half) 取极值
        # 这样得到的是 [i-2*half, i-half] 区间的极值，完全是过去数据
        earlier_high = dataframe['high'].shift(half).rolling(half, min_periods=half // 2).max()
        earlier_low  = dataframe['low'].shift(half).rolling(half, min_periods=half // 2).min()

        # ── ATR 归一化偏差分 ─────────────────────────────────────────
        # hh_score > 0 → Higher High（近期最高价超过前期）
        # hl_score > 0 → Higher Low（近期最低价高于前期）
        hh_score = (recent_high - earlier_high) / (atr + 1e-9)
        hl_score = (recent_low  - earlier_low)  / (atr + 1e-9)

        # 综合方向评分（高点和低点各占 50%）
        # 正 = 多头结构（HH + HL）
        # 负 = 空头结构（LL + LH）
        trend_score = (hh_score + hl_score) / 2.0

        # ── 初始分类 ─────────────────────────────────────────────────
        # markup：近期高点显著更高 + 低点没有明显更低
        is_markup   = (hh_score > self.markup_thresh) & (hl_score > -self.counter_loose)

        # markdown：近期低点显著更低 + 高点没有明显更高
        is_markdown = (hl_score < -self.markdown_thresh) & (hh_score < self.counter_loose)

        # ── 持久化：滚动窗口内出现过即维持标签 ──────────────────────
        persist = self.persistence_bars
        markup_persistent   = is_markup.rolling(persist, min_periods=1).max().astype(bool)
        markdown_persistent = is_markdown.rolling(persist, min_periods=1).max().astype(bool)

        # ── 最终分类 ──────────────────────────────────────────────────
        # 默认：横盘区间
        market_env = pd.Series('trading_range', index=dataframe.index, dtype=object)

        # markup/markdown 覆盖默认
        market_env[markup_persistent]   = 'markup'
        market_env[markdown_persistent] = 'markdown'

        # 矛盾（markup 与 markdown 同时持久化）→ transition
        both_active = markup_persistent & markdown_persistent
        market_env[both_active] = 'transition'

        # ── 置信度 ────────────────────────────────────────────────────
        # 用 trend_score 绝对值经软饱和函数映射到 [0, 1]
        # |score|=0 → confidence 0.5（中性）; |score|→∞ → confidence 1.0
        score_abs  = trend_score.abs()
        confidence = 0.5 + 0.5 * (score_abs / (score_abs + 3.0))  # 映射到 [0.5, 1.0]
        confidence = confidence.clip(0.0, 1.0)

        # transition 时置信度降低（方向不明）
        confidence = confidence.where(market_env != 'transition', confidence * 0.6)

        # ── NaN 填充 ─────────────────────────────────────────────────
        market_env  = market_env.fillna('transition')
        hh_score    = hh_score.fillna(0.0)
        hl_score    = hl_score.fillna(0.0)
        trend_score = trend_score.fillna(0.0)
        confidence  = confidence.fillna(0.5)

        # ── 写回 dataframe ────────────────────────────────────────────
        dataframe['market_env']            = market_env.values
        dataframe['market_env_confidence'] = confidence.round(3).values
        dataframe['hh_score']              = hh_score.round(3).values
        dataframe['hl_score']              = hl_score.round(3).values
        dataframe['trend_score']           = trend_score.round(3).values

        # ── 统计日志 ──────────────────────────────────────────────────
        total = len(dataframe)
        if total > 0:
            counts = dataframe['market_env'].value_counts().to_dict()
            markup_pct   = counts.get('markup',        0) * 100 // total
            markdown_pct = counts.get('markdown',      0) * 100 // total
            tr_pct       = counts.get('trading_range', 0) * 100 // total
            trans_pct    = counts.get('transition',    0) * 100 // total
            logger.debug(
                f'MarketEnvDetector: 总K线={total} | '
                f'markup={counts.get("markup", 0)}({markup_pct}%) | '
                f'markdown={counts.get("markdown", 0)}({markdown_pct}%) | '
                f'trading_range={counts.get("trading_range", 0)}({tr_pct}%) | '
                f'transition={counts.get("transition", 0)}({trans_pct}%)'
            )

        return dataframe
