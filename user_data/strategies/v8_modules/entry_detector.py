"""
Wyckoff V8 — 1H 执行层入场检测器
===================================
TASK 4 核心模块。

入场条件（按优先级）：
1. wave_start ChoCh：is_wave_start & wave_direction=='up' & wave_efficiency>阈值
2. Phase 上下文：Phase A/B 不开多，Phase D/E/unknown 允许
3. PNF 目标位：v8_target_1 必须存在（有因果依据才进场）
4. 基础风控：close < v8_target_1（当前价低于目标，有上涨空间）

设计决策（2026-05-10）：
- Phase unknown 同 D/E 一起允许，不确认坏不等于坏
- OI 接口预留（oi_confirmed 字段），默认 True 不过滤
- 不设复杂多条件，每增一个条件都需要回测验证

输入：1H DataFrame，已包含以下列：
  wave 层：is_wave_start, wave_direction, wave_efficiency
  phase 层：v8_phase（来自 phase_detector，已从日线 merge）
  pnf 层：v8_target_1, v8_tr_low, v8_tr_high（已从日线 merge）

输出：entry_long（bool Series）+ 各条件 bool Series（用于诊断）
"""

import numpy as np
import pandas as pd
from pandas import DataFrame


# 默认参数
DEFAULT_ENTRY_PARAMS = {
    'wave_efficiency_min': 0.0001,  # ChoCh 最低效率阈值（与 V7 保持一致）
    'phase_block': ['A', 'B'],      # 这些 Phase 不开多
    'require_pnf_target': True,     # 必须有 PNF 目标位
    'require_price_below_t1': True, # 入场价必须低于 T1（有上涨空间）
}


def compute_entry_signal(df: DataFrame, params: dict = None) -> DataFrame:
    """
    计算 V8 入场信号。

    返回 df，新增列：
      v8_choch_ok       : wave_start ChoCh 条件
      v8_phase_ok       : Phase 上下文条件
      v8_pnf_ok         : PNF 目标位条件
      v8_oi_ok          : OI 条件（预留，默认全 True）
      v8_entry_long     : 最终入场信号（所有条件 AND）
    """
    p = {**DEFAULT_ENTRY_PARAMS, **(params or {})}
    df = df.copy()

    # ── 条件 1：wave_start ChoCh ──────────────────────────────
    is_wave_start = df.get('is_wave_start', pd.Series(False, index=df.index))
    wave_direction = df.get('wave_direction', pd.Series('', index=df.index))
    wave_efficiency = df.get('wave_efficiency', pd.Series(0.0, index=df.index))

    df['v8_choch_ok'] = (
        is_wave_start &
        (wave_direction == 'up') &
        (wave_efficiency > p['wave_efficiency_min'])
    )

    # ── 条件 2：Phase 上下文 ──────────────────────────────────
    v8_phase = df.get('v8_phase', pd.Series('unknown', index=df.index)).fillna('unknown')
    df['v8_phase_ok'] = ~v8_phase.isin(p['phase_block'])

    # ── 条件 3：PNF 目标位存在 ────────────────────────────────
    v8_target_1 = df.get('v8_target_1', pd.Series(np.nan, index=df.index))
    if p['require_pnf_target']:
        pnf_exists = v8_target_1.notna()
    else:
        pnf_exists = pd.Series(True, index=df.index)

    # 入场价必须低于 T1（有上涨空间）
    if p['require_price_below_t1'] and p['require_pnf_target']:
        price_below_t1 = df['close'] < v8_target_1
    else:
        price_below_t1 = pd.Series(True, index=df.index)

    df['v8_pnf_ok'] = pnf_exists & price_below_t1.fillna(False)

    # ── 条件 4：OI 确认（预留接口，默认全通过）────────────────
    # 当历史 OI 数据到位时，在此处接入：
    # df['v8_oi_ok'] = df.get('oi_confirmed', pd.Series(True, index=df.index))
    df['v8_oi_ok'] = pd.Series(True, index=df.index)

    # ── 最终信号 ─────────────────────────────────────────────
    df['v8_entry_long'] = (
        df['v8_choch_ok'] &
        df['v8_phase_ok'] &
        df['v8_pnf_ok'] &
        df['v8_oi_ok']
    )

    return df


def entry_funnel_stats(df: DataFrame) -> dict:
    """
    返回入场漏斗统计，用于诊断。
    """
    choch = df.get('v8_choch_ok', pd.Series(False, index=df.index))
    phase_ok = df.get('v8_phase_ok', pd.Series(False, index=df.index))
    pnf_ok = df.get('v8_pnf_ok', pd.Series(False, index=df.index))
    oi_ok = df.get('v8_oi_ok', pd.Series(True, index=df.index))
    entry = df.get('v8_entry_long', pd.Series(False, index=df.index))

    return {
        'choch_signals': int(choch.sum()),
        'pass_phase': int((choch & phase_ok).sum()),
        'blocked_phase': int((choch & ~phase_ok).sum()),
        'pass_pnf': int((choch & phase_ok & pnf_ok).sum()),
        'blocked_pnf': int((choch & phase_ok & ~pnf_ok).sum()),
        'pass_oi': int((choch & phase_ok & pnf_ok & oi_ok).sum()),
        'final_entry': int(entry.sum()),
    }
