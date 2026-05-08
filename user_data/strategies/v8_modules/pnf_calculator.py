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
