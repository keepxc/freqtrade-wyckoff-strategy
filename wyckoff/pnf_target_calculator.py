"""
Task 2.12: 因果定律目标位（P&F Counting）模块

输出字段：
- 做多：tr_high, tr_low, tr_height,
  target_conservative/standard/aggressive, target_vertical
- 做空：dist_high, dist_low, dist_height,
  target_short_conservative/standard/aggressive, target_short_vertical,
  utad_adjusted_dist_high
- R:R：rr_long_standard, rr_short_standard

Task 3.3 更新：
  当 PhaseContextDetector（Task 3.3）提供精确 TR 边界时（phase_tr_valid=True），
  用 phase_tr_low / phase_tr_high 替换 60-bar 滚动 fallback，提升目标位精度。
  Phase 精确 TR 边界的优先级：
    climatic_type=='SC' 锚点（最高）> phase_tr_valid 边界（次高）> 60-bar fallback（保底）

P0 Fix (2026-05-02): 增强 fallback 逻辑
  - 扩大滚动窗口从60到120根K线
  - 添加 swing high/low fallback（当滚动窗口TR height < ATR × 2.0时）
"""

import numpy as np
import pandas as pd
from pandas import DataFrame


class PNFTargetCalculator:
    """P&F Counting 目标位计算器（Task 2.12）"""

    def __init__(self, strategy=None):
        self.strategy = strategy
        self.lookback_fallback = 120  # P2 Fix 已回退 (2026-05-04): 验证发现滚动窗口只影响48.7%的K线，效果有限
        self.utad_pre_high_lookback = 20

    def calculate_targets(self, dataframe: DataFrame) -> DataFrame:
        self._init_columns(dataframe)
        self._calculate_long_side(dataframe)
        self._calculate_short_side(dataframe)
        self._calculate_rr(dataframe)
        return dataframe

    def _init_columns(self, dataframe: DataFrame) -> None:
        dataframe['tr_high'] = 0.0
        dataframe['tr_low'] = 0.0
        dataframe['tr_height'] = 0.0
        dataframe['target_conservative'] = 0.0
        dataframe['target_standard'] = 0.0
        dataframe['target_aggressive'] = 0.0
        dataframe['target_vertical'] = 0.0
        
        # P1 Fix (2026-05-04): 添加 TR 来源诊断字段
        dataframe['tr_source'] = 'unknown'
        dataframe['tr_source_detail'] = ''

        dataframe['dist_high'] = 0.0
        dataframe['dist_low'] = 0.0
        dataframe['dist_height'] = 0.0
        dataframe['target_short_conservative'] = 0.0
        dataframe['target_short_standard'] = 0.0
        dataframe['target_short_aggressive'] = 0.0
        dataframe['target_short_vertical'] = 0.0
        dataframe['utad_adjusted_dist_high'] = 0.0

        dataframe['rr_long_standard'] = 0.0
        dataframe['rr_short_standard'] = 0.0

    def _calculate_long_side(self, dataframe: DataFrame) -> None:
        bars = len(dataframe)
        tr_low = np.zeros(bars, dtype=float)
        tr_high = np.zeros(bars, dtype=float)
        tr_height = np.zeros(bars, dtype=float)
        target_vertical = np.zeros(bars, dtype=float)
        tr_source = np.full(bars, 'unknown', dtype=object)  # P1 Fix: TR 来源标记
        tr_source_detail = np.full(bars, '', dtype=object)  # P1 Fix: TR 来源详情

        sc_mask = dataframe.get('climatic_type', 'none') == 'SC'
        is_sos = dataframe.get('is_SOS_bar', False).astype(bool)

        fallback_low = dataframe['low'].rolling(self.lookback_fallback, min_periods=1).min().values
        fallback_high = dataframe['high'].rolling(self.lookback_fallback, min_periods=1).max().values

        # Task 3.3: Phase 精确 TR 边界（从 PhaseContextDetector 提供，优先于 fallback）
        phase_tr_low_vals   = dataframe.get('phase_tr_low',   dataframe['close'] * 0).values
        phase_tr_high_vals  = dataframe.get('phase_tr_high',  dataframe['close'] * 0).values
        phase_tr_valid_vals = dataframe.get('phase_tr_valid', dataframe['close'] * 0).astype(bool).values

        last_sc_low = 0.0
        running_high_from_sc = 0.0
        last_sos_high = 0.0
        last_sos_height = 0.0

        for i in range(bars):
            if bool(sc_mask.iloc[i]):
                last_sc_low = float(dataframe['low'].iloc[i])
                running_high_from_sc = float(dataframe['high'].iloc[i])

            if last_sc_low > 0:
                running_high_from_sc = max(running_high_from_sc, float(dataframe['high'].iloc[i]))
                tr_low[i] = last_sc_low
                tr_high[i] = running_high_from_sc
                tr_source[i] = 'SC_anchor'  # P1 Fix: 标记来源
                tr_source_detail[i] = f'SC@{last_sc_low:.0f}'
            elif phase_tr_valid_vals[i] and phase_tr_low_vals[i] > 0:
                # Task 3.3: Phase 精确 TR 边界（替换 fallback，但低于 SC 锚点优先级）
                tr_low[i] = float(phase_tr_low_vals[i])
                tr_high[i] = float(phase_tr_high_vals[i])
                tr_source[i] = 'phase_context'  # P1 Fix: 标记来源
                tr_source_detail[i] = f'Phase[{phase_tr_low_vals[i]:.0f}-{phase_tr_high_vals[i]:.0f}]'
            else:
                # Fallback 1: 滚动窗口（120根K线）
                tr_low[i] = float(fallback_low[i])
                tr_high[i] = float(fallback_high[i])
                tr_source[i] = 'rolling_window'  # P1 Fix: 标记来源
                tr_source_detail[i] = f'Window{self.lookback_fallback}'
                
                # P0 Fix: Fallback 2 - 如果滚动窗口 TR height 太小，使用 swing high/low
                # 判断标准：TR height < ATR × 2.0（说明窗口内价格波动不足）
                atr_val = dataframe.get('atr_20', dataframe.get('atr', pd.Series(0, index=dataframe.index))).iloc[i]
                if atr_val > 0 and (tr_high[i] - tr_low[i]) < (atr_val * 2.0):
                    # 使用最近的 swing high/low（最近20根K线的极值）
                    swing_window = 20
                    start_idx = max(0, i - swing_window)
                    swing_low = float(dataframe['low'].iloc[start_idx:i+1].min())
                    swing_high = float(dataframe['high'].iloc[start_idx:i+1].max())
                    
                    # 只有当swing范围更大时才使用（避免用更小的范围替换）
                    if (swing_high - swing_low) > (tr_high[i] - tr_low[i]):
                        tr_low[i] = swing_low
                        tr_high[i] = swing_high
                        tr_source[i] = 'swing_fallback'  # P1 Fix: 标记来源
                        tr_source_detail[i] = f'Swing{swing_window}[{swing_low:.0f}-{swing_high:.0f}]'

            tr_height[i] = max(0.0, tr_high[i] - tr_low[i])

            if bool(is_sos.iloc[i]):
                last_sos_high = float(dataframe['high'].iloc[i])
                last_sos_height = max(1e-9, float(dataframe['high'].iloc[i] - dataframe['low'].iloc[i]))

            if last_sos_high > 0:
                target_vertical[i] = last_sos_high + last_sos_height * 3.0
            else:
                target_vertical[i] = tr_high[i] + tr_height[i]

        dataframe['tr_low'] = tr_low
        dataframe['tr_high'] = tr_high
        dataframe['tr_height'] = tr_height
        dataframe['tr_source'] = tr_source  # P1 Fix: 存储来源标记
        dataframe['tr_source_detail'] = tr_source_detail  # P1 Fix: 存储来源详情
        dataframe['target_conservative'] = tr_low + tr_height * 1.0
        dataframe['target_standard'] = tr_low + tr_height * 1.5
        dataframe['target_aggressive'] = tr_low + tr_height * 2.0
        dataframe['target_vertical'] = target_vertical

    def _calculate_short_side(self, dataframe: DataFrame) -> None:
        bars = len(dataframe)
        dist_high = np.zeros(bars, dtype=float)
        dist_low = np.zeros(bars, dtype=float)
        dist_height = np.zeros(bars, dtype=float)
        target_short_vertical = np.zeros(bars, dtype=float)
        utad_adjusted_dist_high = np.zeros(bars, dtype=float)

        bc_mask = dataframe.get('climatic_type', 'none') == 'BC'
        utad_mask = dataframe.get('utad_detected', False).astype(bool)
        sow_mask = dataframe.get('sow_bar_detected', False).astype(bool)

        fallback_low = dataframe['low'].rolling(self.lookback_fallback, min_periods=1).min().values
        fallback_high = dataframe['high'].rolling(self.lookback_fallback, min_periods=1).max().values
        pre_utad_high = dataframe['high'].rolling(self.utad_pre_high_lookback, min_periods=1).max().shift(1).fillna(dataframe['high']).values

        # Task 3.3: Phase 精确派发 TR 边界
        phase_dist_low_vals   = dataframe.get('phase_dist_low',   dataframe['close'] * 0).values
        phase_dist_high_vals  = dataframe.get('phase_dist_high',  dataframe['close'] * 0).values
        phase_dist_valid_vals = dataframe.get('phase_dist_valid', dataframe['close'] * 0).astype(bool).values

        last_dist_high = 0.0
        last_dist_low = 0.0
        last_sow_low = 0.0
        last_sow_height = 0.0

        for i in range(bars):
            if bool(bc_mask.iloc[i]):
                last_dist_high = float(dataframe['high'].iloc[i])
                last_dist_low = float(dataframe['low'].iloc[i])

            if last_dist_high > 0:
                last_dist_high = max(last_dist_high, float(dataframe['high'].iloc[i]))
                last_dist_low = min(last_dist_low, float(dataframe['low'].iloc[i]))
                dist_high[i] = last_dist_high
                dist_low[i] = last_dist_low
            elif phase_dist_valid_vals[i] and phase_dist_high_vals[i] > 0:
                # Task 3.3: Phase 精确派发 TR 边界（替换 fallback）
                dist_high[i] = float(phase_dist_high_vals[i])
                dist_low[i] = float(phase_dist_low_vals[i])
            else:
                # Fallback 1: 滚动窗口（120根K线）
                dist_high[i] = float(fallback_high[i])
                dist_low[i] = float(fallback_low[i])
                
                # P0 Fix: Fallback 2 - 如果滚动窗口 Dist height 太小，使用 swing high/low
                atr_val = dataframe.get('atr_20', dataframe.get('atr', pd.Series(0, index=dataframe.index))).iloc[i]
                if atr_val > 0 and (dist_high[i] - dist_low[i]) < (atr_val * 2.0):
                    # 使用最近的 swing high/low（最近20根K线的极值）
                    swing_window = 20
                    start_idx = max(0, i - swing_window)
                    swing_low = float(dataframe['low'].iloc[start_idx:i+1].min())
                    swing_high = float(dataframe['high'].iloc[start_idx:i+1].max())
                    
                    # 只有当swing范围更大时才使用
                    if (swing_high - swing_low) > (dist_high[i] - dist_low[i]):
                        dist_high[i] = swing_high
                        dist_low[i] = swing_low

            if bool(utad_mask.iloc[i]):
                utad_adjusted_dist_high[i] = min(dist_high[i], float(pre_utad_high[i]))
            else:
                utad_adjusted_dist_high[i] = dist_high[i]

            dist_height[i] = max(0.0, utad_adjusted_dist_high[i] - dist_low[i])

            if bool(sow_mask.iloc[i]):
                last_sow_low = float(dataframe['low'].iloc[i])
                last_sow_height = max(1e-9, float(dataframe['high'].iloc[i] - dataframe['low'].iloc[i]))

            if last_sow_low > 0:
                target_short_vertical[i] = last_sow_low - last_sow_height * 3.0
            else:
                target_short_vertical[i] = dist_low[i] - dist_height[i]

        dataframe['dist_high'] = utad_adjusted_dist_high
        dataframe['dist_low'] = dist_low
        dataframe['dist_height'] = dist_height
        dataframe['utad_adjusted_dist_high'] = utad_adjusted_dist_high

        dataframe['target_short_conservative'] = utad_adjusted_dist_high - dist_height * 1.0
        dataframe['target_short_standard'] = utad_adjusted_dist_high - dist_height * 1.5
        dataframe['target_short_aggressive'] = utad_adjusted_dist_high - dist_height * 2.0
        dataframe['target_short_vertical'] = target_short_vertical

    def _calculate_rr(self, dataframe: DataFrame) -> None:
        """
        R:R 计算
        - 风险：使用 1.5× ATR（与策略实际止损距离匹配，避免 TR 低点过远导致 R:R 错误偏低）
        - 收益：保守目标位到当前价的距离
        - 用 target_conservative 而非 target_standard，避免激进目标使 R:R 虚高
        """
        close = dataframe['close'].astype(float)
        atr = dataframe.get('atr_20', dataframe.get('atr', close * 0.01)).astype(float).clip(lower=1e-9)

        # 多头：风险 = ATR × 1.5（对应策略止损约 1-2 ATR 幅度）
        long_risk = (atr * 1.5).clip(lower=1e-9)
        long_reward = (dataframe['target_conservative'].astype(float) - close)
        dataframe['rr_long_standard'] = np.where(long_reward > 0, long_reward / long_risk, 0.0)

        # 空头：风险 = ATR × 1.5（UTAD 止损通常在突破高点上方约 1 ATR）
        short_risk = (atr * 1.5).clip(lower=1e-9)
        short_reward = (close - dataframe['target_short_conservative'].astype(float))
        dataframe['rr_short_standard'] = np.where(short_reward > 0, short_reward / short_risk, 0.0)
