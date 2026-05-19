"""
Wyckoff V8 P&F 目标位计算器
============================
TASK 3 核心模块。基于 TR 边界的 P&F 因果目标位。

输入：日线 DataFrame + sc_detector + phase_detector 输出
      （v8_sc_final, v8_sc_low, v8_phase, v8_tr_high, v8_tr_low）

输出：v8_target_1, v8_target_2, v8_target_3（因果目标位，锁定不变）

设计约束（D-003, TASK 3）：
- Box Size = ATR_14 × 0.1
- Column Count = round(TR Height / Box Size)，上限 100
- 目标位锁定在 Phase C/D 开始时，此后不重算
- target_1 = 1.0×, target_2 = 1.5×, target_3 = 2.0×
"""

import numpy as np
import pandas as pd
from pandas import DataFrame


DEFAULT_PNF_PARAMS = {
    'box_atr_mult': 0.1,       # Box Size = ATR × this
    'max_columns': 100,         # Column Count 上限
    'fulfillment': [1.0, 1.5, 2.0],  # target_1/2/3 的 Fulfillment Factor
}


def add_v8_pnf_targets(df: DataFrame, params: dict = None) -> DataFrame:
    """
    计算 P&F 因果目标位。

    前置条件：df 必须包含 add_v8_sc_detection() 和 add_v8_phase_detection() 的输出。
    """
    p = {**DEFAULT_PNF_PARAMS, **(params or {})}
    df = df.copy()
    n = len(df)

    # ATR_14（取 TR 确认时的值）
    df['_atr14'] = (df['high'] - df['low']).rolling(14).mean()

    target_1 = np.full(n, np.nan)
    target_2 = np.full(n, np.nan)
    target_3 = np.full(n, np.nan)
    target_locked = np.full(n, False)

    # 状态：是否已锁定
    locked_t1 = np.nan
    locked_t2 = np.nan
    locked_t3 = np.nan
    was_locked = False

    for i in range(n):
        # ── 锁定触发：Phase B 退出（进入 C/D/E 的任意一个）──
        phase_now = df['v8_phase'].iloc[i]
        phase_prev = df['v8_phase'].iloc[i-1] if i > 0 else 'unknown'

        lock_trigger = (phase_prev == 'B' and phase_now in ('C', 'D', 'E'))

        if lock_trigger and not was_locked:
            tr_h = df['v8_tr_high'].iloc[i]
            tr_l = df['v8_tr_low'].iloc[i]
            tr_height = tr_h - tr_l
            atr = df['_atr14'].iloc[i]

            if (not np.isnan(tr_h) and not np.isnan(tr_l)
                    and tr_height > 0 and not np.isnan(atr) and atr > 0):
                box_size = atr * p['box_atr_mult']
                columns = min(round(tr_height / box_size), p['max_columns'])
                projection = columns * box_size

                locked_t1 = tr_l + projection * p['fulfillment'][0]
                locked_t2 = tr_l + projection * p['fulfillment'][1]
                locked_t3 = tr_l + projection * p['fulfillment'][2]
                was_locked = True

        # ── Phase 回退 → 目标位作废 ──
        if was_locked and phase_now == 'A':
            was_locked = False
            locked_t1 = np.nan
            locked_t2 = np.nan
            locked_t3 = np.nan

        # 新 SC → 重置
        if df['v8_sc_final'].iloc[i]:
            was_locked = False
            locked_t1 = np.nan
            locked_t2 = np.nan
            locked_t3 = np.nan

        # ── 写入 ──
        target_1[i] = locked_t1
        target_2[i] = locked_t2
        target_3[i] = locked_t3
        target_locked[i] = was_locked

    df['v8_target_1'] = target_1
    df['v8_target_2'] = target_2
    df['v8_target_3'] = target_3
    df['v8_target_locked'] = target_locked

    df.drop(columns=['_atr14'], inplace=True)

    return df


# ── TASK S-3: 空头 PNF 目标位 ────────────────────────────────

DEFAULT_SHORT_PNF_PARAMS = {
    'lookback_fallback': 120,       # 无 BC 锚点时滚动窗口
    'utad_pre_lookback': 20,        # UTAD 调整：前 N 根最高 high
    'fulfillment_short': [1.0, 1.5, 2.0],  # conservative / standard / aggressive
    'rr_risk_atr_mult': 1.5,        # 风险 = ATR × this
    'min_rr': 1.0,
}


def add_v8_short_pnf_targets(df: DataFrame, params: dict = None) -> DataFrame:
    """
    计算空头 PNF 目标位（派发区因果结构）。

    输入列（来自 daily_event_detector.detect()）：
      daily_bc_detected   : BC 确认（派发区锚点触发）
      daily_utad_detected : UTAD 洗盘确认（用于调整 dist_high）
      atr14               : ATR_14（或用 high-low rolling 近似）

    输出列：
      target_short_conservative : utad_adjusted_dist_high - dist_height × 1.0
      target_short_standard     : utad_adjusted_dist_high - dist_height × 1.5
      target_short_aggressive   : utad_adjusted_dist_high - dist_height × 2.0
      rr_short_standard         : (close - target_short_conservative) / (ATR × rr_risk_atr_mult)
    """
    p = {**DEFAULT_SHORT_PNF_PARAMS, **(params or {})}
    df = df.copy()
    n = len(df)

    atr14 = (df['high'] - df['low']).rolling(14).mean()

    tsc  = np.full(n, np.nan)  # conservative
    tss  = np.full(n, np.nan)  # standard
    tsa  = np.full(n, np.nan)  # aggressive
    rr_s = np.full(n, np.nan)  # rr_short_standard

    bc_detected      = df.get('daily_bc_detected',  pd.Series(False, index=df.index)).fillna(False).astype(bool)
    utad_detected    = df.get('daily_utad_detected', pd.Series(False, index=df.index)).fillna(False).astype(bool)

    dist_high_locked = np.nan   # BC 锚定后追踪的派发区最高 high
    dist_low_locked  = np.nan   # BC 锚定后追踪的派发区最低 low
    in_dist          = False    # 是否在派发序列中

    for i in range(n):
        high_i  = df['high'].iloc[i]
        low_i   = df['low'].iloc[i]
        close_i = df['close'].iloc[i]
        atr_i   = atr14.iloc[i]

        # BC 触发 → 开始追踪派发区
        if bc_detected.iloc[i]:
            in_dist          = True
            dist_high_locked = high_i
            dist_low_locked  = low_i

        # 派发区持续更新（追踪最高 high / 最低 low）
        if in_dist:
            dist_high_locked = max(dist_high_locked, high_i)
            dist_low_locked  = min(dist_low_locked, low_i)

        # 无 BC 锚点 → 用滚动窗口 fallback
        if not in_dist:
            lb = p['lookback_fallback']
            start = max(0, i - lb + 1)
            dist_high_locked = df['high'].iloc[start:i+1].max()
            dist_low_locked  = df['low'].iloc[start:i+1].min()

        if np.isnan(dist_high_locked) or np.isnan(dist_low_locked):
            continue

        dist_height = dist_high_locked - dist_low_locked
        if dist_height <= 0:
            continue

        # UTAD 调整：UTAD 确认时，dist_high 向下修正到 UTAD 前 N 根最高点
        utad_adj_dist_high = dist_high_locked
        if utad_detected.iloc[i]:
            lb2 = p['utad_pre_lookback']
            start2 = max(0, i - lb2)
            utad_adj_dist_high = df['high'].iloc[start2:i].max() if i > start2 else dist_high_locked
            utad_adj_dist_high = min(dist_high_locked, utad_adj_dist_high)

        ff = p['fulfillment_short']
        tsc[i] = utad_adj_dist_high - dist_height * ff[0]
        tss[i] = utad_adj_dist_high - dist_height * ff[1]
        tsa[i] = utad_adj_dist_high - dist_height * ff[2]

        # RR：做空收益 = close - target_short_conservative，风险 = ATR × mult
        if not np.isnan(atr_i) and atr_i > 0 and close_i > tsc[i]:
            rr_s[i] = (close_i - tsc[i]) / (atr_i * p['rr_risk_atr_mult'])

    df['target_short_conservative'] = tsc
    df['target_short_standard']     = tss
    df['target_short_aggressive']   = tsa
    df['rr_short_standard']         = rr_s

    return df
