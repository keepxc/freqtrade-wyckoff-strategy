"""
Wyckoff V8 SC 检测器 — Exhaustion + Confirmation 两阶段模型
==========================================================
TASK 1 核心模块。日线层 SC/BC 识别。

设计约束（V8.3.1）：
- confirm_bar = close > zone_high_close（非前一日 high，已实测验证）
- 双通道确认：strong (close_pct>2.5%) / weak (close>open)
- Zone 冻结规则：闭合后锁死，不接受新K线
- 权重固定，threshold 可调

输入：日线 DataFrame（open/high/low/close/volume/date）
输出：v8_exhaustion_score, v8_zone_id, v8_zone_high_close, v8_zone_low,
      v8_confirm, v8_sc_confirmed, v8_sc_low, v8_sc_type, v8_sc_final
"""

import numpy as np
import pandas as pd
from pandas import DataFrame


# ============================================================
# 默认参数（来自 D-003）
# ============================================================
DEFAULT_PARAMS = {
    # 结构性参数（固定）
    'M': 252,           # 新低回溯期
    'K': 20,            # 均量计算期
    'P': 14,            # ATR 周期
    'w1': 0.35,         # 趋势强度权重
    'w2': 0.25,         # 成交量异常权重
    'w3': 0.25,         # 波动扩张权重
    'w4': 0.15,         # 位置约束权重

    # 第二组参数（从 P0 锚点反推）
    'X_60': 0.16,       # 60日跌幅归一化基准
    'X_3': 0.14,        # 3日跌幅归一化基准
    'V': 0.7,           # 成交量归一化基准
    'A': 0.5,           # ATR宽幅归一化基准

    # 第三组参数（用误报率校准）
    'R': 0.50,          # 回撤比例基准
    'B': 0.10,          # swing_low偏差基准

    # 阈值
    'threshold': 0.45,   # Exhaustion Score 阈值

    # Zone 参数
    'max_zone_days': 5,       # Zone 最大持续天数
    'confirm_timeout': 10,    # Zone 闭合后超时天数

    # Final SC 去重
    'final_window': 30,       # 去重窗口（天）
}


def add_v8_sc_detection(df: DataFrame, params: dict = None) -> DataFrame:
    """
    在日线 DataFrame 上运行 V8.3.1 SC 检测。

    参数：
        df: 日线 DataFrame，含 open/high/low/close/volume/date
        params: 参数字典，覆盖 DEFAULT_PARAMS

    返回：
        增加 v8_* 列的 DataFrame
    """
    p = {**DEFAULT_PARAMS, **(params or {})}
    df = df.copy()
    n = len(df)

    # ========================
    # 基础指标
    # ========================
    df['_vol_ma20'] = df['volume'].rolling(p['K']).mean()
    df['_atr14'] = (df['high'] - df['low']).rolling(p['P']).mean()
    df['_close_pct'] = (df['close'] - df['open']) / df['open']

    # ========================
    # C1: 趋势强度（双模式：60日/3日 max）
    # ========================
    decline_60 = df['close'] / df['close'].rolling(60).max().shift(1) - 1   # ≤ 0
    decline_3  = df['close'] / df['close'].shift(3) - 1
    c1_60 = np.maximum(0, -decline_60) / p['X_60']
    c1_3  = np.maximum(0, -decline_3) / p['X_3']
    c1 = np.maximum(c1_60, c1_3)

    # ========================
    # C2: 一年新低（前置硬条件）
    # ========================
    df['_low_252_min'] = df['low'].rolling(p['M']).min().shift(1)
    c2 = df['low'] < df['_low_252_min']

    # ========================
    # C3: 成交量异常（单日 + 3日累计 max）
    # ========================
    vol_1d = df['volume'] / df['_vol_ma20']
    vol_3d = df['volume'].rolling(3).sum() / (3 * df['_vol_ma20'])
    c3 = np.maximum(vol_1d, vol_3d) / p['V']

    # ========================
    # C4: 波动扩张（单日 + 3日累计 max）
    # ========================
    range_1d = (df['high'] - df['low']) / df['_atr14']
    range_3d = (df['high'].rolling(3).max() - df['low'].rolling(3).min()) / (3 * df['_atr14'])
    c4 = np.maximum(range_1d, range_3d) / p['A']

    # ========================
    # C5: 位置约束
    # ========================
    high_60 = df['high'].rolling(60).max().shift(1)
    drawdown = (df['low'] - high_60) / high_60
    c5_drawdown = np.clip(-drawdown / p['R'], 0, 1)

    low_60 = df['low'].rolling(60).min().shift(1)
    c5_swing = np.where(
        low_60 > 0,
        np.maximum(0, 1.0 - np.abs(df['low'] - low_60) / low_60 / p['B']),
        0
    )
    c5 = 0.5 * c5_drawdown + 0.5 * c5_swing

    # ========================
    # Exhaustion Score
    # ========================
    df['v8_exhaustion_score'] = (
        p['w1'] * c1 + p['w2'] * c3 + p['w3'] * c4 + p['w4'] * c5
    )

    # ========================
    # Zone 生命周期状态机
    # ========================
    in_exhaustion = c2.values & (df['v8_exhaustion_score'].values > p['threshold'])
    # 3日延续（解决 2018 慢熊 score 波动）
    in_exhaustion = pd.Series(in_exhaustion).rolling(3, min_periods=1).max().astype(bool).values

    zone_id = np.full(n, np.nan)
    zone_high_close = np.full(n, np.nan)
    zone_low = np.full(n, np.nan)
    confirm = np.full(n, False)
    sc_confirmed = np.full(n, False)
    sc_low = np.full(n, np.nan)
    sc_type = np.full(n, '', dtype=object)

    current_zid = 0
    in_zone = False
    zone_day1 = -1
    zhc = np.nan
    zl = np.nan
    zone_closed = False
    zone_closed_day = -1
    had_confirm = False

    for i in range(n):
        # ── Confirm 判定（先判定，后更新 zone，防自确认）──
        if not had_confirm and (in_zone or (zone_closed and i - zone_closed_day <= p['confirm_timeout'])):
            if not np.isnan(zhc) and df['close'].iloc[i] > zhc:
                strong = df['_close_pct'].iloc[i] > 0.025
                weak = df['close'].iloc[i] > df['open'].iloc[i]
                if strong or weak:
                    confirm[i] = True
                    sc_confirmed[i] = True
                    sc_low[i] = zl
                    had_confirm = True
                    zone_len = i - zone_day1
                    if zone_len >= 3:
                        sc_type[i] = 'slow_exhaustion'
                    elif abs(df['_close_pct'].iloc[i]) > 0.08:
                        sc_type[i] = 'panic'
                    else:
                        sc_type[i] = 'standard'

        # ── Zone 生命周期 ──
        if in_exhaustion[i]:
            if not in_zone:
                current_zid += 1
                in_zone = True
                zone_closed = False
                zone_closed_day = -1
                had_confirm = False
                zone_day1 = i
                zhc = df['close'].iloc[i]
                zl = df['low'].iloc[i]
            else:
                if i - zone_day1 + 1 <= p['max_zone_days']:
                    # Zone 持续 → 累积更新
                    zhc = max(zhc, df['close'].iloc[i])
                # 始终追踪最低点（不受 max_zone_days 限制）
                zl = min(zl, df['low'].iloc[i])
                # 出现显著新低（>5%）→ 重置 zone_high_close 和确认状态
                if zl < zone_low[i-1] * 0.95 if not np.isnan(zone_low[i-1]) else False:
                    zhc = df['close'].iloc[i]
                    had_confirm = False

            zone_id[i] = current_zid
            zone_high_close[i] = zhc
            zone_low[i] = zl
        else:
            if in_zone:
                zone_closed = True
                zone_closed_day = i
                in_zone = False
                zone_high_close[i] = zhc
                zone_low[i] = zl

            if zone_closed and not had_confirm and i - zone_closed_day > p['confirm_timeout']:
                zone_closed = False
                zhc = np.nan
                zl = np.nan

            if i > 0 and np.isnan(zone_high_close[i]) and zone_closed:
                zone_high_close[i] = zone_high_close[i-1]
                zone_low[i] = zone_low[i-1]

    df['v8_zone_id'] = zone_id
    df['v8_zone_high_close'] = zone_high_close
    df['v8_zone_low'] = zone_low
    df['v8_confirm'] = confirm
    df['v8_sc_confirmed'] = sc_confirmed
    df['v8_sc_low'] = sc_low
    df['v8_sc_type'] = sc_type

    # ========================
    # SC 底部接近度约束（30%：恐慌日 close 可远高于 low）
    # ========================
    NEAR_LOW_PCT = 0.30
    df['v8_sc_near_low'] = False
    sc_mask = df['v8_sc_confirmed']
    if sc_mask.any():
        closes = df.loc[sc_mask, 'close'].values
        lows = df.loc[sc_mask, 'v8_sc_low'].values
        valid = np.abs(closes - lows) / lows < NEAR_LOW_PCT
        df.loc[sc_mask[sc_mask].index[valid], 'v8_sc_near_low'] = True

    # ========================
    # Final SC 去重（因果实现，2026-07-06 未来函数修复）
    # ========================
    # 旧实现在 ±final_window 窗口内选最低 low 的 SC——win_end 向未来看 30 天，
    # 且已 final 的 SC 被排除出后续窗口导致去重失效 + 指标重绘（实测：2022-06
    # 大底回测锚 06-19@17936，live 当时只能锚 06-15@20100，低 10.8%）。
    # 因果规则：按时间顺序，首个候选即 final；其后 final_window 天内的候选抑制。
    # 决策只依赖历史，新数据到达不改写既有标记（幂等，无重绘）。
    # TASK 71 补充：冷却期内候选若 low 比上一 final 的 low 低超过 5%
    # （deeper_pct=0.95），视为新一轮 climax 事件而非重复信号，照常标 final——
    # 修复 2018-12 真实底部候选被 30 天冷却吞掉、结构无锚可用的缺陷。
    # 判断只用历史 final + 当前候选，因果、无重绘。
    DEEPER_PCT = 0.95
    sc_filtered = df['v8_sc_confirmed'] & df['v8_sc_near_low']
    df['v8_sc_final'] = False
    last_final_iloc = None
    last_final_low = np.nan
    for idx in df.index[sc_filtered]:
        iloc = df.index.get_loc(idx)
        cand_low = df['v8_sc_low'].loc[idx]
        if np.isnan(cand_low):
            cand_low = df['low'].loc[idx]
        if last_final_iloc is not None and (iloc - last_final_iloc) <= p['final_window']:
            is_deeper = (not np.isnan(last_final_low)) and cand_low < last_final_low * DEEPER_PCT
            if not is_deeper:
                continue
        df.loc[idx, 'v8_sc_final'] = True
        last_final_iloc = iloc
        last_final_low = cand_low

    # ========================
    # 清理临时列
    # ========================
    _keep = ['_close_pct']
    _drop = [c for c in df.columns if c.startswith('_') and c not in _keep]
    df.drop(columns=_drop, inplace=True)

    return df
