"""
Wyckoff V8 Phase 检测器 — A/B/C/D/E 状态机
==========================================
TASK 2 核心模块。基于 SC 确认后的 Phase 判断。

输入：日线 DataFrame + sc_detector 输出（v8_sc_final, v8_sc_low）
输出：v8_phase, v8_phase_allow_long, v8_tr_high, v8_tr_low, v8_ar_confirmed

Phase 定义（来自 D-005/D-006）：
  Phase A: SC 确认 → 等待 AR（15日内反弹 ≥ SC低点×110%）
  Phase B: AR 完成 → TR 震荡（≥20日）
  Phase C: Spring（跌破 TR Low 后 ≤3日 收回）
  Phase D: SOS（站上 TR 中线 + 放量）→ 主要入场区
  Phase E: 突破 TR High → 趋势延伸

回退逻辑：
  D→B: 收盘 < TR 中线 连续3日
  C→A: 收盘 < SC低点×0.97 且量正常
  E→D: 收盘 < TR High 连续2日
  冷却期: 回退后 5 日禁止升级
"""

import numpy as np
import pandas as pd
from pandas import DataFrame


DEFAULT_PHASE_PARAMS = {
    'ar_days': 15,          # AR 等待天数
    'ar_min_pct': 0.10,    # AR 最小反弹比例
    'phase_b_min_days': 20, # Phase B 最少天数
    'spring_max_days': 7,   # Spring 收回最大天数（放宽：Crypto Spring 回收更慢）
    'cooldown_days': 5,     # 回退冷却期
    'tr_buffer': 0.995,     # TR Low 缓冲
}


def add_v8_phase_detection(df: DataFrame, params: dict = None) -> DataFrame:
    """
    在日线 DataFrame 上运行 Phase A-E 判断。

    前置条件：df 必须包含 add_v8_sc_detection() 的输出列
    （v8_sc_final, v8_sc_low）

    返回：增加 v8_phase, v8_phase_allow_long, v8_tr_high, v8_tr_low, v8_ar_confirmed
    """
    p = {**DEFAULT_PHASE_PARAMS, **(params or {})}
    df = df.copy()
    n = len(df)

    # 输出列
    phase = np.full(n, 'unknown', dtype=object)
    phase_allow_long = np.full(n, False)
    tr_high = np.full(n, np.nan)
    tr_low = np.full(n, np.nan)
    ar_confirmed = np.full(n, False)

    # 状态变量
    current_phase = 'unknown'
    sc_low_val = np.nan
    sc_confirm_idx = -1
    tr_high_val = np.nan
    tr_low_val = np.nan
    tr_mid_val = np.nan
    tr_locked = False
    phase_b_start = -1
    phase_c_start = -1
    spring_low = np.nan

    # 回退冷却
    cooldown_until = -1
    consec_below_mid = 0     # D→B 计数
    consec_below_tr_high = 0  # E→D 计数

    for i in range(n):
        # ========================
        # SC 确认 → Phase A 开始
        # ========================
        if df['v8_sc_final'].iloc[i]:
            sc_confirm_idx = i
            sc_low_val = df['v8_sc_low'].iloc[i]
            tr_low_val = sc_low_val * p['tr_buffer'] if not np.isnan(sc_low_val) else np.nan
            tr_locked = False
            tr_high_val = np.nan
            tr_mid_val = np.nan
            current_phase = 'A'
            phase_b_start = -1
            phase_c_start = -1
            spring_low = np.nan
            consec_below_mid = 0
            consec_below_tr_high = 0
            cooldown_until = -1

        # ========================
        # Phase A: 等待 AR
        # ========================
        if current_phase == 'A' and sc_confirm_idx >= 0:
            days_since_sc = i - sc_confirm_idx
            _slice = df.iloc[sc_confirm_idx:i+1]
            highest = _slice['high'].max()

            if not np.isnan(sc_low_val) and highest >= sc_low_val * (1 + p['ar_min_pct']):
                # AR 完成
                tr_high_val = highest
                tr_low_val = sc_low_val * p['tr_buffer']
                tr_mid_val = (tr_high_val + tr_low_val) / 2
                tr_locked = True
                current_phase = 'B'
                phase_b_start = i
                ar_confirmed[i] = True
            elif days_since_sc > p['ar_days']:
                # AR 超时 → 结构废弃
                current_phase = 'unknown'
                sc_low_val = np.nan
                tr_locked = False

        # ========================
        # Phase B: TR 震荡
        # ========================
        if current_phase == 'B' and tr_locked:
            if phase_b_start >= 0 and (i - phase_b_start) > p['phase_b_min_days']:
                if i > cooldown_until:
                    close = df['close'].iloc[i]

                    # B → C: Spring（跌破 TR Low 后 ≤3日 收回）
                    for lookback in range(1, p['spring_max_days'] + 1):
                        if i - lookback >= 0:
                            past_low = df['low'].iloc[i - lookback]
                            if past_low < tr_low_val and close > tr_low_val:
                                current_phase = 'C'
                                phase_c_start = i
                                spring_low = past_low
                                break

                    # B → D/E: 跳过 C（V 型反弹）
                    # v8.4: 去量化——价格站上中线即为有效 SoS，不强制放量
                    if current_phase == 'B' and not np.isnan(tr_mid_val):
                        if close > tr_mid_val:
                            if not np.isnan(tr_high_val) and close > tr_high_val:
                                current_phase = 'E'  # 直接进 E
                            else:
                                current_phase = 'D'
                            consec_below_mid = 0

        # ========================
        # Phase C: Spring 测试
        # ========================
        if current_phase == 'C' and tr_locked:
            # C → D: SOS
            # v8.4: 去量化——价格站上中线即可
            if (not np.isnan(tr_mid_val) and df['close'].iloc[i] > tr_mid_val
                    and i > cooldown_until):
                current_phase = 'D'
                consec_below_mid = 0
                phase_c_start = -1

            # C → A: Spring 失败
            if not np.isnan(sc_low_val) and not np.isnan(tr_low_val):
                close = df['close'].iloc[i]
                # 收盘跌破 SC 低点 97%，且量正常（非恐慌新低）
                if close < sc_low_val * 0.97:
                    vol_ma20 = df['volume'].iloc[max(0, i-20):i].mean() if i >= 20 else 0
                    if vol_ma20 > 0 and df['volume'].iloc[i] < vol_ma20 * 2.0:
                        current_phase = 'A'
                        sc_low_val = np.nan  # 等待新 SC
                        tr_locked = False
                        cooldown_until = i + p['cooldown_days']
                        consec_below_mid = 0

        # ========================
        # Phase D: 需求主导
        # ========================
        if current_phase == 'D' and tr_locked:
            close = df['close'].iloc[i]

            # D → E: 突破 TR High
            if not np.isnan(tr_high_val) and close > tr_high_val and i > cooldown_until:
                current_phase = 'E'
                consec_below_tr_high = 0

            # D → B: 连续 3 日收盘 < TR 中线
            if not np.isnan(tr_mid_val):
                if close < tr_mid_val:
                    consec_below_mid += 1
                else:
                    consec_below_mid = 0
                if consec_below_mid >= 3 and i > cooldown_until:
                    current_phase = 'B'
                    consec_below_mid = 0
                    cooldown_until = i + p['cooldown_days']

        # ========================
        # Phase E: 趋势延伸
        # ========================
        if current_phase == 'E' and tr_locked:
            close = df['close'].iloc[i]

            # E → D: 收盘 < TR High 即回退（v8.4: 1日确认，原为2日）
            if not np.isnan(tr_high_val):
                if close < tr_high_val:
                    consec_below_tr_high += 1
                else:
                    consec_below_tr_high = 0
                if consec_below_tr_high >= 1 and i > cooldown_until:
                    current_phase = 'D'
                    consec_below_tr_high = 0
                    cooldown_until = i + p['cooldown_days']

        # ========================
        # 写入输出
        # ========================
        phase[i] = current_phase
        phase_allow_long[i] = current_phase in ('C', 'D', 'E')
        tr_high[i] = tr_high_val
        tr_low[i] = tr_low_val
        if tr_locked:
            ar_confirmed[i] = True

    df['v8_phase'] = phase
    df['v8_phase_allow_long'] = phase_allow_long
    df['v8_tr_high'] = tr_high
    df['v8_tr_low'] = tr_low
    df['v8_ar_confirmed'] = ar_confirmed

    return df
