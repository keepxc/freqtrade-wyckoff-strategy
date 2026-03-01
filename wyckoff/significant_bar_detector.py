"""
显著K线检测器 (Significant Bar Detector) — Task 3.1

实现日期: 2026-02-27

理论依据：
威科夫理论中，"显著K线"（Significant Bar）是机构资金以明确方向高度介入的直接物理证据。
它与普通 K 线的根本区别在于：
  - 价格区间异常宽大（机构强力驱动）
  - 成交量异常高（大资金进场）
  - 收盘位置偏向一侧（方向性明确）
  - 突破重要价位（价格承诺）

四大硬指标（缺一不可）：
  1. 宽价差：bar_range > ATR × 1.5
  2. 成交量匹配：volume > volume_sma × 1.5
  3. 收盘位置：close_position > 0.7（看涨）/ < 0.3（看跌）
  4. 价格承诺：close > 前 N 根 K 线最高 high（看涨）/ < 前 N 根 K 线最低 low（看跌）

应用规则（Task 3.1 集成点）：
  - SOS Bar 增强验证：sos_bar_detected 的 K 线必须同时满足 sig_bar_bullish
    （提升 SOS 信号纯度，减少假 SOS）
  - Spring 后的 Test 确认：spring_detected 后 spring_lookback 根 K 线内若出现
    sig_bar_bullish → 触发 spring_test_sig_bar 入场信号（Phase D 开始的确认）

输出字段：
  - sig_bar_bullish (bool)：多头显著K线（四大指标全满足）
  - sig_bar_bearish (bool)：空头显著K线（四大指标全满足）
  - sig_bar_score_bull (float 0-100)：多头显著K线质量评分
  - sig_bar_score_bear (float 0-100)：空头显著K线质量评分
  - sig_bar_after_spring (bool)：spring_detected 后 spring_lookback 根内出现的多头显著K线
  - bars_since_spring (int)：距上次 spring_detected=True 经过的 K 线数（-1=超出窗口）
"""

import logging
import numpy as np
import pandas as pd
from pandas import DataFrame

logger = logging.getLogger(__name__)


class SignificantBarDetector:
    """
    显著K线检测器 (Task 3.1)

    威科夫理论中，显著K线（Significant Bar）是机构资金目的性介入的最直接信号。
    本检测器作为通用基础设施，供 SOS 验证和 Spring Test 确认两条路径使用。
    """

    def __init__(self, strategy):
        """
        初始化显著K线检测器

        Args:
            strategy: 策略实例，用于访问参数
        """
        self.strategy = strategy

        # ── 四大硬指标阈值 ──────────────────────────────────────────────────
        self.bar_range_multiplier   = 1.5   # 宽价差：bar_range > ATR × 1.5
        self.volume_multiplier      = 1.5   # 成交量：volume > SMA × 1.5
        self.close_position_bull    = 0.7   # 多头收盘位置下限（0=最低, 1=最高）
        self.close_position_bear    = 0.3   # 空头收盘位置上限

        # ── 价格承诺：突破前 N 根 K 线的极值 ──────────────────────────────
        self.price_commitment_lookback = 10  # 看涨：close > 近10根最高 high
                                             # 看跌：close < 近10根最低 low

        # ── Spring 后显著K线窗口 ──────────────────────────────────────────
        self.spring_lookback = 10  # Spring 后 N 根 K 线内出现显著多头 K 线 → sig_bar_after_spring

        # ── 质量评分权重（多/空共享相同权重）────────────────────────────────
        self.weight_spread      = 0.30   # 宽价差权重
        self.weight_volume      = 0.25   # 成交量权重
        self.weight_close_pos   = 0.15   # 收盘位置权重
        self.weight_commitment  = 0.30   # 价格承诺权重

        logger.info(
            f"SignificantBarDetector 初始化完成 "
            f"(spread×{self.bar_range_multiplier}, vol×{self.volume_multiplier}, "
            f"close_bull>{self.close_position_bull}, close_bear<{self.close_position_bear}, "
            f"commitment_lookback={self.price_commitment_lookback}, "
            f"spring_window={self.spring_lookback})"
        )

    # ─────────────────────────────────────────────────────────────────────────
    def detect(self, dataframe: DataFrame) -> DataFrame:
        """
        检测显著K线，并计算 Spring 后的确认信号。

        Args:
            dataframe: 包含 OHLCV、atr、volume_sma_20、spring_detected 的 DataFrame

        Returns:
            添加了以下字段的 DataFrame：
              sig_bar_bullish / sig_bar_bearish
              sig_bar_score_bull / sig_bar_score_bear
              sig_bar_after_spring / bars_since_spring
        """

        # ── 初始化输出字段 ─────────────────────────────────────────────────
        dataframe['sig_bar_bullish']       = False
        dataframe['sig_bar_bearish']       = False
        dataframe['sig_bar_score_bull']    = 0.0
        dataframe['sig_bar_score_bear']    = 0.0
        dataframe['sig_bar_after_spring']  = False
        dataframe['bars_since_spring']     = -1

        # ── 前置检查 ────────────────────────────────────────────────────────
        if 'atr' not in dataframe.columns:
            logger.warning("⚠️ SignificantBarDetector: 缺少 atr 字段，跳过检测")
            return dataframe

        vol_col = 'volume_sma_20'
        if vol_col not in dataframe.columns:
            # 降级：实时计算20周期成交量均值
            dataframe[vol_col] = dataframe['volume'].rolling(20, min_periods=5).mean()
            logger.debug("SignificantBarDetector: 实时计算 volume_sma_20")

        # ── 基础字段计算 ────────────────────────────────────────────────────
        bar_range     = dataframe['high'] - dataframe['low']
        close_pos_raw = np.where(
            bar_range > 0,
            (dataframe['close'] - dataframe['low']) / bar_range,
            0.5
        )
        close_position = pd.Series(close_pos_raw, index=dataframe.index)

        # 价格承诺的参考极值（shift(1) 确保不使用当前 K 线，无前瞻偏差）
        rolling_high = dataframe['high'].shift(1).rolling(
            self.price_commitment_lookback, min_periods=3
        ).max()
        rolling_low  = dataframe['low'].shift(1).rolling(
            self.price_commitment_lookback, min_periods=3
        ).min()

        # ══════════════════════════════════════════════════════════════════
        # 多头显著K线（四大指标）
        # ══════════════════════════════════════════════════════════════════
        cond_spread_bull    = bar_range > (dataframe['atr'] * self.bar_range_multiplier)
        cond_volume_bull    = dataframe['volume'] > (dataframe[vol_col] * self.volume_multiplier)
        cond_close_bull     = close_position > self.close_position_bull
        cond_commit_bull    = dataframe['close'] > rolling_high          # 突破前10根最高 high

        bull_mask = (
            cond_spread_bull &
            cond_volume_bull &
            cond_close_bull  &
            cond_commit_bull
        )
        dataframe.loc[bull_mask, 'sig_bar_bullish'] = True

        # ══════════════════════════════════════════════════════════════════
        # 空头显著K线（四大指标）
        # ══════════════════════════════════════════════════════════════════
        cond_spread_bear  = bar_range > (dataframe['atr'] * self.bar_range_multiplier)
        cond_volume_bear  = dataframe['volume'] > (dataframe[vol_col] * self.volume_multiplier)
        cond_close_bear   = close_position < self.close_position_bear
        cond_commit_bear  = dataframe['close'] < rolling_low             # 跌破前10根最低 low

        bear_mask = (
            cond_spread_bear &
            cond_volume_bear &
            cond_close_bear  &
            cond_commit_bear
        )
        dataframe.loc[bear_mask, 'sig_bar_bearish'] = True

        # ══════════════════════════════════════════════════════════════════
        # 质量评分（0-100）
        # ══════════════════════════════════════════════════════════════════
        atr_safe    = dataframe['atr'].replace(0, np.nan)
        vol_sma_safe = dataframe[vol_col].replace(0, np.nan)

        if bull_mask.any():
            idx = dataframe.index[bull_mask]

            score_spread = np.minimum(
                (bar_range[bull_mask] / (atr_safe[bull_mask] * self.bar_range_multiplier) - 1.0) + 0.5,
                1.0
            )
            score_volume = np.minimum(
                (dataframe.loc[idx, 'volume'] / (vol_sma_safe[idx] * self.volume_multiplier) - 1.0) + 0.5,
                1.0
            )
            score_close = np.minimum(
                (close_position[bull_mask] - self.close_position_bull) / (1.0 - self.close_position_bull) * 0.5 + 0.5,
                1.0
            )
            breakthrough = (
                (dataframe.loc[idx, 'close'] - rolling_high[idx]).clip(lower=0)
                / dataframe.loc[idx, 'close'].replace(0, np.nan)
            )
            score_commit = np.minimum(breakthrough * 20 + 0.5, 1.0)

            dataframe.loc[idx, 'sig_bar_score_bull'] = (
                score_spread * self.weight_spread  +
                score_volume * self.weight_volume  +
                score_close  * self.weight_close_pos +
                score_commit * self.weight_commitment
            ) * 100

        if bear_mask.any():
            idx = dataframe.index[bear_mask]

            score_spread = np.minimum(
                (bar_range[bear_mask] / (atr_safe[bear_mask] * self.bar_range_multiplier) - 1.0) + 0.5,
                1.0
            )
            score_volume = np.minimum(
                (dataframe.loc[idx, 'volume'] / (vol_sma_safe[idx] * self.volume_multiplier) - 1.0) + 0.5,
                1.0
            )
            score_close = np.minimum(
                (self.close_position_bear - close_position[bear_mask]) / self.close_position_bear * 0.5 + 0.5,
                1.0
            )
            breakthrough = (
                (rolling_low[idx] - dataframe.loc[idx, 'close']).clip(lower=0)
                / dataframe.loc[idx, 'close'].replace(0, np.nan)
            )
            score_commit = np.minimum(breakthrough * 20 + 0.5, 1.0)

            dataframe.loc[idx, 'sig_bar_score_bear'] = (
                score_spread * self.weight_spread  +
                score_volume * self.weight_volume  +
                score_close  * self.weight_close_pos +
                score_commit * self.weight_commitment
            ) * 100

        # ══════════════════════════════════════════════════════════════════
        # Spring 后的显著K线（sig_bar_after_spring）
        # 威科夫理论：Spring（Phase C 终端震仓）→ Test（缩量回踩）→ 显著K线（Phase D 开始）
        # 当显著多头K线出现在 Spring 后 spring_lookback 根内，代表积累周期 Phase D 启动
        # ══════════════════════════════════════════════════════════════════
        spring_col = 'spring_detected'
        if spring_col in dataframe.columns:
            spring_detected = dataframe[spring_col].astype(bool)

            # 计算每根 K 线距离最近 spring_detected=True 的距离
            # 方法：向前滚动窗口，判断过去 spring_lookback 根内是否有 Spring
            # bars_since_spring = 过去 N 根内第一个 spring_detected 的位置
            since_arr = np.full(len(dataframe), -1, dtype=int)
            spring_indices = dataframe.index[spring_detected].tolist()

            if spring_indices:
                # 遍历每个 Spring 发生的行索引，向后填充 bars_since_spring
                spring_pos_map = {idx: pos for pos, idx in enumerate(dataframe.index)}
                for sp_idx in spring_indices:
                    sp_pos = spring_pos_map[sp_idx]
                    for offset in range(1, self.spring_lookback + 1):
                        target_pos = sp_pos + offset
                        if target_pos < len(since_arr):
                            # 只在尚未被更近一个 Spring 覆盖时写入
                            if since_arr[target_pos] == -1 or offset < since_arr[target_pos]:
                                since_arr[target_pos] = offset

            dataframe['bars_since_spring'] = since_arr

            # sig_bar_after_spring：spring_lookback 窗口内的显著多头 K 线
            in_spring_window = (dataframe['bars_since_spring'] >= 1) & (dataframe['bars_since_spring'] <= self.spring_lookback)
            dataframe['sig_bar_after_spring'] = dataframe['sig_bar_bullish'] & in_spring_window
        else:
            logger.debug("SignificantBarDetector: spring_detected 字段不可用，sig_bar_after_spring 全为 False")

        # ── 统计日志 ────────────────────────────────────────────────────────
        n_bull  = int(bull_mask.sum())
        n_bear  = int(bear_mask.sum())
        n_after = int(dataframe['sig_bar_after_spring'].sum())
        if n_bull > 0 or n_bear > 0:
            avg_bull = dataframe.loc[bull_mask, 'sig_bar_score_bull'].mean() if n_bull > 0 else 0.0
            avg_bear = dataframe.loc[bear_mask, 'sig_bar_score_bear'].mean() if n_bear > 0 else 0.0
            logger.info(
                f"SignificantBar 检测完成: 多头={n_bull}(均分{avg_bull:.1f}) | "
                f"空头={n_bear}(均分{avg_bear:.1f}) | Spring后确认={n_after}"
            )
        else:
            logger.debug("SignificantBar 检测完成: 未发现显著K线")

        return dataframe
