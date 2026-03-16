"""
LPS 检测器 — P6-3

Last Point of Support（最后支撑点）检测器。

理论依据（威科夫 7 大事件 §LPS）：
  LPS 是 SOS/JAC 突破后回踩时的支撑确认点，特征是：
  - 回测时区间狭窄（diminished spread）
  - 成交量极低（diminished volume）
  - 不重回 TR 内部
  - 随后重新恢复上行

设计原则（2026-03-16 理论沟通确认）：
  - LPS 是事件，不是区域：不是所有 Phase D bar 都是 LPS
  - 事件锚点：反转确认 bar（支撑确认后重新转强的那根），不是最低点 bar
  - 两阶段结构：
      lps_pullback_active  — 候选回踩过程标志（过程）
      lps_bar_detected     — 反转确认 bar 事件（点）
  - 主尺度：ATR 相对比例；辅尺度：TR 高度位置约束

默认参数：
  _LPS_WINDOW_BARS        = 15     （sos_bar_confirmed 后最多 15 根内寻找 LPS）
  _LPS_SPREAD_ATR_RATIO   = 0.8    （窄价差：true_range / ATR_14 <= 0.8）
  _LPS_VOLUME_RATIO       = 0.7    （缩量：volume / vol_ma_20 <= 0.7）
  _LPS_ALPHA              = 0.25   （位置约束：close >= tr_upper - 0.25 * tr_height）
  _LPS_EPSILON_ATR_MULT   = 0.10   （确认 bar 位置：close >= tr_upper - 0.10 * ATR_14）
  atr_n                   = 14
  vol_ma_n                = 20
"""

import logging
import numpy as np
import pandas as pd
from pandas import DataFrame

logger = logging.getLogger(__name__)


class LPSDetector:
    """
    LPS 候选事件检测器（P6-3）

    输出字段：
      lps_pullback_active : bool  — 当前处于 SOS 后的 LPS 候选回踩过程
      lps_bar_detected    : bool  — 当前 bar 是反转确认 bar（LPS 事件锚点）
      lps_quality_score   : float — 0~100，候选质量评分（加分项汇总）
    """

    # ── 默认参数 ──────────────────────────────────────────────────────────────
    _LPS_WINDOW_BARS      = 15    # sos_bar_confirmed 后最多多少根内寻找 LPS
    _LPS_SPREAD_ATR_RATIO = 0.8   # 窄价差主条件
    _LPS_VOLUME_RATIO     = 0.7   # 缩量主条件
    _LPS_ALPHA            = 0.25  # 位置约束：允许回踩深度（相对 tr_height）
    _LPS_EPSILON_ATR_MULT = 0.10  # 确认 bar 位置容忍带（与 P6-1 统一）

    def __init__(self, strategy=None):
        self.strategy = strategy
        logger.info(
            f"LPSDetector 初始化完成 "
            f"(window={self._LPS_WINDOW_BARS}, "
            f"spread_ratio<={self._LPS_SPREAD_ATR_RATIO}, "
            f"vol_ratio<={self._LPS_VOLUME_RATIO}, "
            f"alpha={self._LPS_ALPHA})"
        )

    def detect_lps(self, dataframe: DataFrame) -> DataFrame:
        """
        检测 LPS 候选事件。

        前置依赖（必须由上游先运行）：
          - event_sequence_state_machine.detect()  → accum_phase_a_complete, tr_upper, tr_lower
          - sos_bar_detector.confirm_sos_bars()     → sos_bar_confirmed
          - atr, volume_sma_20 列

        Returns:
          dataframe 附加 lps_pullback_active, lps_bar_detected, lps_quality_score
        """
        # 初始化输出字段
        dataframe['lps_pullback_active'] = False
        dataframe['lps_bar_detected']    = False
        dataframe['lps_quality_score']   = 0.0

        # 前置字段检查
        required = ['sos_bar_confirmed', 'accum_phase_a_complete',
                    'tr_upper', 'tr_lower', 'atr', 'volume_sma_20']
        missing = [c for c in required if c not in dataframe.columns]
        if missing:
            logger.warning(f"⚠️ LPSDetector 缺少字段 {missing}，跳过检测")
            return dataframe

        n = len(dataframe)
        if n < 3:
            return dataframe

        sos_conf_vals  = dataframe['sos_bar_confirmed'].values
        phase_a_vals   = dataframe['accum_phase_a_complete'].values
        tr_upper_vals  = dataframe['tr_upper'].values
        tr_lower_vals  = dataframe['tr_lower'].values
        atr_vals       = dataframe['atr'].values
        vol_ma_vals    = dataframe['volume_sma_20'].values
        close_vals     = dataframe['close'].values
        high_vals      = dataframe['high'].values
        low_vals       = dataframe['low'].values
        volume_vals    = dataframe['volume'].values

        # 计算 true_range（每根 bar 的真实波动幅度）
        prev_close = np.empty(n)
        prev_close[0] = close_vals[0]
        prev_close[1:] = close_vals[:-1]
        true_range = np.maximum(
            high_vals - low_vals,
            np.maximum(
                np.abs(high_vals - prev_close),
                np.abs(low_vals  - prev_close)
            )
        )

        pullback_active = np.zeros(n, dtype=bool)
        bar_detected    = np.zeros(n, dtype=bool)
        quality_score   = np.zeros(n, dtype=float)

        # ── 状态追踪 ──────────────────────────────────────────────────────────
        sos_anchor_idx   = -1   # 最近一次 sos_bar_confirmed 的 bar 索引
        in_pullback      = False
        pullback_start   = -1
        pullback_low     = np.nan

        for i in range(n):
            # ── 前置：当前积累结构是否仍有效 ────────────────────────────────
            if not phase_a_vals[i]:
                # 积累侧结构失效（BC 重置），终止当前候选
                in_pullback    = False
                pullback_start = -1
                sos_anchor_idx = -1
                pullback_low   = np.nan
                continue

            tr_u = tr_upper_vals[i]
            tr_l = tr_lower_vals[i]
            atr  = atr_vals[i]
            v_ma = vol_ma_vals[i]

            if np.isnan(tr_u) or np.isnan(tr_l) or np.isnan(atr) or atr <= 0:
                continue

            tr_height = tr_u - tr_l
            if tr_height <= 0:
                continue

            # ── 1. 记录 sos_bar_confirmed 锚点 ────────────────────────────
            if sos_conf_vals[i]:
                sos_anchor_idx = i
                in_pullback    = False   # 新 SOS，重置候选状态
                pullback_start = -1
                pullback_low   = np.nan

            # ── 2. 超时检查：超过窗口限制，作废当前候选 ───────────────────
            if in_pullback and sos_anchor_idx >= 0:
                if (i - sos_anchor_idx) > self._LPS_WINDOW_BARS:
                    in_pullback    = False
                    pullback_start = -1
                    pullback_low   = np.nan
                    continue

            # ── 3. 尚未触发 SOS，跳过 ─────────────────────────────────────
            if sos_anchor_idx < 0:
                continue

            # ── 4. 明显供给回归：作废当前候选 ─────────────────────────────
            # 条件：放量 + 阴线 + 跌破位置约束
            vol_surge    = (not np.isnan(v_ma)) and v_ma > 0 and (volume_vals[i] > v_ma * 1.5)
            bearish_body = close_vals[i] < prev_close[i]
            deep_reentry = close_vals[i] < (tr_u - self._LPS_ALPHA * tr_height)
            if in_pullback and vol_surge and bearish_body and deep_reentry:
                in_pullback    = False
                pullback_start = -1
                pullback_low   = np.nan
                continue

            # ── 5. 判断是否处于 LPS 候选回踩区 ────────────────────────────
            narrow_spread = (not np.isnan(v_ma)) and (true_range[i] / atr <= self._LPS_SPREAD_ATR_RATIO)
            low_volume    = (not np.isnan(v_ma)) and v_ma > 0 and (volume_vals[i] / v_ma <= self._LPS_VOLUME_RATIO)
            pos_valid     = close_vals[i] >= (tr_u - self._LPS_ALPHA * tr_height)
            below_tr_u    = close_vals[i] < tr_u  # 真正在回踩中（未重新突破）

            # 候选条件：至少满足 2 项特征，且价格在 TR 上边界到容忍带之间
            candidate_features = int(narrow_spread) + int(low_volume)
            is_pullback_bar = (candidate_features >= 1) and pos_valid and below_tr_u

            if is_pullback_bar and not in_pullback:
                # 进入候选回踩阶段
                in_pullback    = True
                pullback_start = i
                pullback_low   = low_vals[i]
            elif in_pullback:
                # 候选过程中，更新回踩低点
                if low_vals[i] < pullback_low:
                    pullback_low = low_vals[i]

            if in_pullback:
                pullback_active[i] = True

            # ── 6. lps_bar_detected：反转确认 bar ─────────────────────────
            if in_pullback and i > 0:
                epsilon_lps = self._LPS_EPSILON_ATR_MULT * atr

                # 硬门槛（3 条）
                reversal_dir  = (close_vals[i] > close_vals[i - 1]) or (high_vals[i] > high_vals[i - 1])
                pos_confirm   = close_vals[i] >= (tr_u - epsilon_lps)

                if reversal_dir and pos_confirm:
                    # 通过硬门槛，计算可选加分
                    score = 0.0
                    # 加分项 1：close 在本根上半部
                    bar_rng = high_vals[i] - low_vals[i]
                    if bar_rng > 0 and (close_vals[i] - low_vals[i]) / bar_rng > 0.5:
                        score += 25.0
                    # 加分项 2：volume 无明显供给扩张
                    if not np.isnan(v_ma) and v_ma > 0 and volume_vals[i] <= v_ma * 1.1:
                        score += 25.0
                    # 加分项 3：阳线实体
                    if close_vals[i] > prev_close[i]:
                        score += 25.0
                    # 加分项 4：未创回踩新低
                    if not np.isnan(pullback_low) and low_vals[i] >= pullback_low:
                        score += 25.0

                    bar_detected[i]  = True
                    quality_score[i] = score
                    # 确认 bar 出现后退出当前候选（等待下一次 SOS）
                    in_pullback    = False
                    pullback_start = -1
                    pullback_low   = np.nan

        dataframe['lps_pullback_active'] = pullback_active.astype(bool)
        dataframe['lps_bar_detected']    = bar_detected.astype(bool)
        dataframe['lps_quality_score']   = quality_score

        # ── 统计日志 ──────────────────────────────────────────────────────────
        lps_count  = int(bar_detected.sum())
        pb_count   = int(pullback_active.sum())
        if lps_count > 0:
            avg_q = quality_score[bar_detected].mean()
            logger.info(
                f"LPS 检测完成: {lps_count} 个 lps_bar_detected，"
                f"{pb_count} 根 pullback_active，平均质量 {avg_q:.1f}"
            )
        else:
            logger.debug("LPS 检测完成: 未发现 LPS 确认事件")

        return dataframe
