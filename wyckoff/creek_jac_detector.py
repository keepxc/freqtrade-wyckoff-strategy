"""
Task 2.11: Creek 线 / JAC / BUEC / Failed JAC 检测器

威科夫理论（Phase D 入场视角）：
    Creek（小溪）是积累区间内的动态阻力线，连接交易区间内多次反弹的峰值。
    价格在积累阶段反复试探 Creek 而未能突破，直到积累完成进入 Phase D/E。

核心信号：

    JAC (Jump Across the Creek)——跨越小溪
        条件：
            1. 价格收盘明确突破 Creek 线（close > creek_level × 1.005）
            2. 宽价差（Wide Spread）：bar_range > ATR × 1.3（Phase D 特征：容易性移动）
            3. 高成交量（High Volume）：volume > volume_sma_20 × 1.5
            4. 高收盘（Close Near High）：收在 K 线上 30%（close_position > 0.70）
            5. 非气候行动（Not Climax）：volume < volume_sma_20 × 4.0（真正 JAC 是容易性移动）
            6. 突破前价格在 Creek 下方（防止追高）
        含义：买方吸收完成，进入 Phase D 标志性事件

    BUEC (Back Up to the Edge of Creek)——回踩小溪边缘（Task 2.11-v3 重设计）
        条件（JAC 后 1-10 根 K 线内）：
            1. 低点触及固定 Creek（jac_creek_snapshot × 1.01）：low ≤ jac_creek_snapshot × 1.01
            2. 收盘仍站稳 Creek 上方：close > jac_creek_snapshot × 0.99
            3. 峰值努力对比：volume < volume_jac_bar × 0.50（BUEC 缩量 ≤ JAC 成交量 50%）
            4. 无 Failed JAC 警告：~failed_jac_active
        含义：最佳入场点 —— 固定 Creek 防漂移，成交量峰值对比替代绝对阈值，更精准识别 No Supply

    Failed JAC Active (失败的跨越状态）——假突破状态守卫
        条件（JAC 后 1-3 根 K 线内）：
            - close < jac_creek_snapshot × 0.99（跌穿固定 Creek）
        行为：设置 failed_jac_active=True，阻止当前 JAC 窗口内所有 BUEC 增强
        含义：突破无效，积累可能未完成，立即取消所有待触发 BUEC 信号

Creek 线动态计算：
    使用过去 50 根 K 线的高点分布（75% 分位数）作为 Creek 基准，
    再做 3 根 K 线平滑，得到稳健的动态阻力水平。
    此方法捕捉"价格反复触及的阻力"，与手工画 Creek 线高度吻合。

核心输出字段：
    creek_level            : float — 当前 Creek 线位置
    jac_detected           : bool  — 本根 K 线是否为 JAC
    jac_quality_score      : float — JAC 质量评分（0-100）
    bars_since_jac         : int   — 距上次 JAC 的 K 线数（-1=尚无JAC）
    jac_creek_snapshot     : float — JAC 触发时锁定的 Creek 线（固定值，不随后续漂移）
    volume_jac_bar         : float — JAC 那根 K 线的成交量（峰值努力参考基准）
    buec_detected          : bool  — 本根 K 线是否为 BUEC 确认（v3 重设计）
    buec_creek_level       : float — BUEC 时的 Creek 水平（用于止损计算）
    failed_jac_detected    : bool  — 本根 K 线是否确认上次 JAC 失败（原有逻辑）
    failed_jac_active      : bool  — Failed JAC 状态守卫（v3：固定Creek + 3棒窗口）
    jac_entry_signal       : bool  — JAC 直接入场信号（高质量 JAC，不等 BUEC）
    buec_entry_signal      : bool  — BUEC 入场信号（最优先）

防误判设计：
    - JAC 成交量上限（4x SMA）：防止把气候行动（Climax）误判为 JAC
    - BUEC 仅在 JAC 后 20 根有效：防止随机价格停留于Creek 附近误报
    - BUEC 要求收盘在 Creek 上方：确保反转方向正确
    - Failed JAC 自动延迟 BUEC 信号：5根K线冷却期
    - Composite Man 意图过滤：在策略层面加 is_accumulating/trending_up 前置条件
"""

import logging
import numpy as np
import pandas as pd
from pandas import DataFrame

logger = logging.getLogger(__name__)


class CreekJACDetector:
    """
    Creek 线 / JAC / BUEC / Failed JAC 检测器

    实现 Phase D 最高质量入场点识别。
    替代 Task 2.4 简化版，成为主力入场机制。
    """

    def __init__(self, strategy):
        """
        初始化检测器

        Args:
            strategy: 策略实例
        """
        self.strategy = strategy

        # Creek 线参数
        self.creek_lookback = 50          # 回溯窗口（K线数）
        self.creek_quantile = 0.75        # 高点分布分位数（75%）
        self.creek_smooth = 3             # 平滑窗口

        # JAC 检测参数
        self.jac_spread_multiplier = 1.3  # 宽价差阈值
        self.jac_volume_multiplier = 1.5  # 高成交量阈值
        self.jac_close_position = 0.70    # 高收盘阈值
        self.jac_climax_volume_max = 4.0  # 成交量上限（防气候行动误判）
        self.jac_creek_tolerance = 0.005  # 突破 Creek 的最小距离（0.5%）
        self.jac_min_quality = 55         # 入场最低质量评分

        # BUEC 检测参数（Task 2.11-v3 重设计）
        self.buec_max_bars = 10              # JAC 后最长等待 BUEC 的 K 线数
        self.buec_creek_tolerance = 0.01     # low 触及固定 Creek 的容差（+1%）
        self.buec_volume_ratio = 0.50        # BUEC 成交量 ≤ JAC 成交量 × 50%（峰值努力对比）
        self.buec_min_parent_quality = 65    # BUEC 所需母 JAC 最低质量分

        # Failed JAC 参数
        self.failed_jac_max_bars = 5         # JAC 后 5 根内跌破动态Creek = 失败（原有逻辑）
        self.failed_jac_active_bars = 3      # JAC 后 3 根内跌穿固定Creek → 设 failed_jac_active（v3 新增）
        self.failed_jac_cooldown = 5         # Failed JAC 后冷却期（K线数）

        logger.info(
            f"CreekJACDetector 初始化完成 "
            f"(Creek lookback={self.creek_lookback}, "
            f"JAC: spread={self.jac_spread_multiplier}x vol={self.jac_volume_multiplier}x, "
            f"BUEC max_bars={self.buec_max_bars})"
        )

    # -------------------------------------------------------------------------
    # 主入口
    # -------------------------------------------------------------------------

    def detect(self, dataframe: DataFrame) -> DataFrame:
        """
        执行 Creek/JAC/BUEC/Failed JAC 全流程检测

        Args:
            dataframe: 包含 OHLCV + 指标的 DataFrame

        Returns:
            添加了 Creek/JAC/BUEC 字段的 DataFrame
        """
        try:
            df = dataframe.copy()

            # 步骤 1：计算 Creek 线
            df = self._compute_creek_line(df)

            # 步骤 2：检测 JAC
            df = self._detect_jac(df)

            # 步骤 3：追踪 JAC 状态（bars_since_jac, failed_jac）
            df = self._track_jac_state(df)

            # 步骤 4：检测 BUEC
            df = self._detect_buec(df)

            # 步骤 5：生成最终入场信号
            df = self._compute_entry_signals(df)

            # 将结果写回原始 dataframe
            output_cols = [
                'creek_level', 'jac_detected', 'jac_quality_score',
                'bars_since_jac', 'jac_failed',
                'jac_creek_snapshot', 'volume_jac_bar', 'failed_jac_active',
                'buec_detected', 'buec_creek_level', 'failed_jac_detected',
                'jac_entry_signal', 'buec_entry_signal',
            ]
            for col in output_cols:
                if col in df.columns:
                    dataframe[col] = df[col].values

            # 日志摘要
            jac_count = int(dataframe['jac_detected'].sum())
            buec_count = int(dataframe['buec_detected'].sum())
            failed_count = int(dataframe['failed_jac_detected'].sum())
            failed_active_count = int(dataframe['failed_jac_active'].sum())
            jac_entry = int(dataframe['jac_entry_signal'].sum())
            buec_entry = int(dataframe['buec_entry_signal'].sum())
            logger.info(
                f"Task 2.11-v3 Creek/JAC/BUEC: JAC={jac_count}, BUEC={buec_count}, "
                f"FailedJAC={failed_count}, FailedJAC_active_bars={failed_active_count} "
                f"→ jac_entry={jac_entry}, buec_entry={buec_entry}"
            )

        except Exception as e:
            logger.error(f"CreekJACDetector.detect() 出错: {e}", exc_info=True)
            self._set_defaults(dataframe)

        return dataframe

    # -------------------------------------------------------------------------
    # 步骤 1：Creek 线
    # -------------------------------------------------------------------------

    def _compute_creek_line(self, df: DataFrame) -> DataFrame:
        """
        动态 Creek 线 = 过去 N 根 K 线高点的 75% 分位数（平滑）

        威科夫中 Creek 是积累区间内"跑得最多的那些高点"——
        75% 分位数自然排除了偶发性极端高点（Upthrust），
        同时保留了价格反复测试的主要阻力水平。
        """
        raw_creek = (
            df['high']
            .rolling(window=self.creek_lookback, min_periods=20)
            .quantile(self.creek_quantile)
        )
        # 3 根平滑，降低锯齿
        df['creek_level'] = raw_creek.rolling(window=self.creek_smooth, min_periods=1).mean()
        return df

    # -------------------------------------------------------------------------
    # 步骤 2：JAC 检测
    # -------------------------------------------------------------------------

    def _detect_jac(self, df: DataFrame) -> DataFrame:
        """
        JAC (Jump Across the Creek) 检测

        四大条件：
        1. 宽价差突破 Creek
        2. 高成交量（但非气候级）
        3. 高收盘位（上30%）
        4. 价格此前在 Creek 下方（要求真实突破）
        """
        creek = df['creek_level']
        bar_range = df['high'] - df['low']
        atr = df.get('atr', bar_range.rolling(14).mean())
        vol_sma = df['volume_sma_20']
        close_pos = (df['close'] - df['low']) / (bar_range + 1e-9)

        # 条件 1：收盘明确高于 Creek
        above_creek = df['close'] > creek * (1 + self.jac_creek_tolerance)

        # 条件 2：宽价差
        wide_spread = bar_range > atr * self.jac_spread_multiplier

        # 条件 3：高成交量（但有上限防气候误判）
        high_vol = (
            (df['volume'] > vol_sma * self.jac_volume_multiplier)
            & (df['volume'] < vol_sma * self.jac_climax_volume_max)
        )

        # 条件 4：高收盘位
        close_near_high = close_pos > self.jac_close_position

        # 条件 5：突破前价格在 Creek 下方或附近（实质性突破）
        prev_below_creek = df['close'].shift(1) < creek.shift(1) * 1.02

        # 全量 JAC
        jac_raw = above_creek & wide_spread & high_vol & close_near_high & prev_below_creek

        df['jac_detected'] = jac_raw

        # JAC 质量评分（0-100）
        df['jac_quality_score'] = self._compute_jac_quality(df, creek, bar_range, atr, close_pos)

        return df

    def _compute_jac_quality(
        self, df: DataFrame, creek: pd.Series,
        bar_range: pd.Series, atr: pd.Series, close_pos: pd.Series
    ) -> pd.Series:
        """
        JAC 质量评分（0-100），四维加权：
        - 突破幅度（30%）
        - 价差得分（30%）
        - 成交量得分（25%）
        - 收盘位置（15%）
        """
        vol_sma = df['volume_sma_20']

        # 1. 突破幅度得分：(close - creek) / creek，归一化到 0.5% → 3%
        breakout_pct = (df['close'] - creek) / (creek + 1e-9)
        breakout_score = (breakout_pct / 0.03).clip(0, 1)

        # 2. 价差得分：bar_range / (atr × 1.3)，归一化
        spread_ratio = bar_range / (atr * self.jac_spread_multiplier + 1e-9)
        spread_score = (spread_ratio - 1.0).clip(0, 1)  # 超过 1.3x 才得分

        # 3. 成交量得分：volume / (vol_sma × 1.5)，归一化，但 >4x 按 0 分（气候）
        vol_ratio = df['volume'] / (vol_sma * self.jac_volume_multiplier + 1e-9)
        climax_penalty = (df['volume'] > vol_sma * self.jac_climax_volume_max).astype(float)
        volume_score = ((vol_ratio - 1.0).clip(0, 1)) * (1 - climax_penalty)

        # 4. 收盘位置得分
        close_pos_score = ((close_pos - self.jac_close_position) / (1 - self.jac_close_position)).clip(0, 1)

        quality = (
            breakout_score * 30
            + spread_score * 30
            + volume_score * 25
            + close_pos_score * 15
        ).clip(0, 100)

        # 只对 jac_detected 行有意义
        quality = quality.where(df['jac_detected'], other=0.0)

        return quality

    # -------------------------------------------------------------------------
    # 步骤 3：JAC 状态追踪
    # -------------------------------------------------------------------------

    def _track_jac_state(self, df: DataFrame) -> DataFrame:
        """
        逐行追踪 JAC 状态（Task 2.11-v3 扩展）：
        - bars_since_jac：距上次 JAC 的 K 线数（-1 = 尚无JAC，0 = 当前JAC）
        - jac_failed：被确认失败的 JAC 的标记（动态 Creek，5棒窗口，原有逻辑）
        - jac_creek_snapshot：JAC 触发时锁定的 Creek 值（固定，不随后续 K 线漂移）
        - volume_jac_bar：JAC 那根 K 线的成交量（峰值努力基准）
        - failed_jac_active：固定 Creek + 3棒窗口内跌穿 → 取消后续 BUEC 增强（v3 新增）
        """
        n = len(df)
        bars_since_jac = np.full(n, -1, dtype=int)
        jac_failed_arr = np.zeros(n, dtype=bool)
        failed_jac_active_arr = np.zeros(n, dtype=bool)   # NEW v3
        jac_creek_snapshot_arr = np.zeros(n, dtype=float)  # NEW v3
        volume_jac_bar_arr = np.zeros(n, dtype=float)       # NEW v3

        jac_arr = df['jac_detected'].values
        close_arr = df['close'].values
        creek_arr = df['creek_level'].values
        volume_arr = df['volume'].values
        quality_arr = df['jac_quality_score'].values

        last_jac_idx = -1
        last_jac_quality = 0.0
        jac_failed_flag = False
        failed_jac_active_flag = False   # NEW v3
        last_jac_creek = 0.0             # NEW v3: locked creek at JAC bar
        last_jac_volume = 0.0            # NEW v3: locked volume at JAC bar
        failed_jac_event = np.zeros(n, dtype=bool)   # 记录 Failed JAC 发生时刻
        parent_jac_quality = np.zeros(n, dtype=float)  # 追踪每根 K 线对应的母 JAC 质量分

        for i in range(n):
            if jac_arr[i]:
                last_jac_idx = i
                last_jac_quality = float(quality_arr[i])
                jac_failed_flag = False
                failed_jac_active_flag = False   # NEW v3: 新 JAC 重置状态
                last_jac_creek = float(creek_arr[i])   # NEW v3: 锁定 Creek
                last_jac_volume = float(volume_arr[i]) # NEW v3: 锁定成交量

            if last_jac_idx >= 0:
                elapsed = i - last_jac_idx
                bars_since_jac[i] = elapsed
                parent_jac_quality[i] = last_jac_quality

                # --- 原有逻辑：Failed JAC（动态 Creek，5棒窗口）---
                if (
                    not jac_failed_flag
                    and 1 <= elapsed <= self.failed_jac_max_bars
                    and creek_arr[i] > 0
                    and close_arr[i] < creek_arr[i] * 0.99
                ):
                    jac_failed_flag = True
                    failed_jac_event[i] = True

                if jac_failed_flag:
                    jac_failed_arr[i] = True

                # --- NEW v3：failed_jac_active（固定 Creek，3棒窗口）---
                if (
                    not failed_jac_active_flag
                    and 1 <= elapsed <= self.failed_jac_active_bars
                    and last_jac_creek > 0
                    and close_arr[i] < last_jac_creek * 0.99
                ):
                    failed_jac_active_flag = True

                if failed_jac_active_flag:
                    failed_jac_active_arr[i] = True

                # 在 BUEC 窗口内传播固定 Creek 快照
                if elapsed <= self.buec_max_bars:
                    jac_creek_snapshot_arr[i] = last_jac_creek
                    volume_jac_bar_arr[i] = last_jac_volume

        df['bars_since_jac'] = bars_since_jac
        df['jac_failed'] = jac_failed_arr
        df['failed_jac_detected'] = failed_jac_event
        df['parent_jac_quality'] = parent_jac_quality
        df['failed_jac_active'] = failed_jac_active_arr    # NEW v3
        df['jac_creek_snapshot'] = jac_creek_snapshot_arr  # NEW v3
        df['volume_jac_bar'] = volume_jac_bar_arr           # NEW v3
        return df

    # -------------------------------------------------------------------------
    # 步骤 4：BUEC 检测
    # -------------------------------------------------------------------------

    def _detect_buec(self, df: DataFrame) -> DataFrame:
        """
        BUEC (Back Up to the Edge of Creek) 检测 — Task 2.11-v3 重设计

        核心改进（解决上次 -32% 拖累的根因）：
        1. 使用固定 Creek 快照（jac_creek_snapshot），消除动态 Creek 漂移后误报
        2. 峰值努力对比：volume < volume_jac_bar × 50%，比绝对阈值更可靠
        3. failed_jac_active 守卫：固定Creek + 3棒窗口，比原来 jac_failed 更严格

        触发条件（JAC 后 1-10 根）：
        - low ≤ jac_creek_snapshot × 1.01  （低点触及固定 Creek）
        - close > jac_creek_snapshot × 0.99 （收盘站稳 Creek 上方）
        - volume < volume_jac_bar × 0.50    （缩量：不超过 JAC 那根的 50%）
        - ~failed_jac_active                 （无 Failed JAC 守卫）
        """
        snapshot = df['jac_creek_snapshot']
        vol_jac = df['volume_jac_bar']
        bars_since = df['bars_since_jac']

        # 在 JAC 后有效窗口内（1-10 根）
        in_jac_window = (bars_since >= 1) & (bars_since <= self.buec_max_bars)

        # 有效快照（JAC 发生过，snapshot > 0）
        has_snapshot = snapshot > 0

        # 低点触及固定 Creek（+1% 容差）
        low_touches_creek = df['low'] <= snapshot * (1 + self.buec_creek_tolerance)

        # 收盘仍站稳 Creek 上方（-1% 缓冲）
        close_above_creek = df['close'] > snapshot * 0.99

        # 峰值努力对比：BUEC 成交量 ≤ JAC 成交量 × 50%
        volume_peak_compare = (vol_jac > 0) & (df['volume'] < vol_jac * self.buec_volume_ratio)

        # Failed JAC 守卫（v3 新增：固定Creek + 3棒窗口）
        no_failed_jac = ~df['failed_jac_active']

        buec = (
            in_jac_window
            & has_snapshot
            & low_touches_creek
            & close_above_creek
            & volume_peak_compare
            & no_failed_jac
        )

        df['buec_detected'] = buec

        # 记录 BUEC 时的 Creek 水平（使用固定快照，用于止损参考）
        df['buec_creek_level'] = np.where(buec, snapshot.values, np.nan)
        df['buec_creek_level'] = pd.Series(
            df['buec_creek_level']
        ).fillna(method='ffill').fillna(0)

        return df

    # -------------------------------------------------------------------------
    # 步骤 5：最终入场信号
    # -------------------------------------------------------------------------

    def _compute_entry_signals(self, df: DataFrame) -> DataFrame:
        """
        生成两类入场信号：
        - buec_entry_signal：BUEC入场（优先，最优风险收益）
        - jac_entry_signal：JAC直接入场（仅高质量JAC，不等BUEC）
        """
        # BUEC 入场：最优信号
        df['buec_entry_signal'] = df['buec_detected']

        # JAC 直接入场：质量评分高于门槛 且 未 Failed
        df['jac_entry_signal'] = (
            df['jac_detected']
            & (df['jac_quality_score'] >= self.jac_min_quality)
            & ~df['jac_failed']
        )

        return df

    # -------------------------------------------------------------------------
    # 工具方法
    # -------------------------------------------------------------------------

    def _set_defaults(self, dataframe: DataFrame) -> None:
        """出错时设置所有输出字段为安全默认值"""
        defaults = {
            'creek_level': dataframe['close'],
            'jac_detected': False,
            'jac_quality_score': 0.0,
            'bars_since_jac': -1,
            'jac_failed': False,
            'parent_jac_quality': 0.0,
            'jac_creek_snapshot': 0.0,    # NEW v3
            'volume_jac_bar': 0.0,         # NEW v3
            'failed_jac_active': False,    # NEW v3
            'buec_detected': False,
            'buec_creek_level': 0.0,
            'failed_jac_detected': False,
            'jac_entry_signal': False,
            'buec_entry_signal': False,
        }
        for col, val in defaults.items():
            if col not in dataframe.columns:
                dataframe[col] = val
