"""
运动反转检测器 (Reversal of Movement) — BOOK-1

来源：《The Wyckoff Methodology in Depth》第27章 — Rubén Villahermosa
核心定义：市场控制权锚定在最后一根显著柱线（Significant Bar）上，
反向显著柱线收盘击穿该锚点 = 控制权物理反转。

与 change_of_character 的区别：
- ChoCh：统计异常（wave_displacement 突变）→ 滞后，需积累窗口
- RoM：物理击穿（sig_bar 收盘突破锚点）→ 领先，单根 K 线即可判定

设计原则：
- 无状态检测器（sig_bar）→ 有状态裁判（RoM）→ 向量化消费方（strategy）
- 默认关闭（WY_ROM_ENABLED=0），日志先行，数据驱动决策
- 关闭时仍记录信号，为 AI 审计提供领先性分析数据
"""

import logging
import os

import numpy as np
import pandas as pd
from pandas import DataFrame

logger = logging.getLogger(__name__)


class ReversalOfMovement:
    """
    运动反转检测器 (BOOK-1)

    追踪显著柱线构成的"控制权锚点"，当反向显著柱线收盘击穿锚点时
    标记 rom_reversal_down / rom_reversal_up。

    输出字段：
        rom_reversal_down (bool): 多头控制权被空头 sig_bar 收盘击穿
        rom_reversal_up   (bool): 空头控制权被多头 sig_bar 收盘击穿
        rom_control_bar_low  (float): 当前多头锚点价格（NaN = 无锚点）
        rom_control_bar_high (float): 当前空头锚点价格（NaN = 无锚点）
    """

    def __init__(self, expiry_limit: int = 48):
        """
        Args:
            expiry_limit: macro_phase=transition 持续多少根 1H K 线后
                          锚点视为被"吸收"而失效（默认 48 = 2 天）
        """
        self.expiry_limit = expiry_limit

    def detect(self, df: DataFrame) -> DataFrame:
        """
        在 1H dataframe 上追踪锚点并判定运动反转。

        前置条件：df 中必须已有 sig_bar_bullish / sig_bar_bearish 列。
        macro_phase 列也必须存在（用于锚点过期判定）。

        Returns:
            新增 4 列的 df（原地修改）。
        """
        n = len(df)
        if n == 0:
            df['rom_reversal_down'] = False
            df['rom_reversal_up'] = False
            df['rom_control_bar_low'] = np.nan
            df['rom_control_bar_high'] = np.nan
            df['rom_anchor_long_idx'] = -1
            df['rom_anchor_short_idx'] = -1
            return df

        # 确保 sig_bar 列存在
        if 'sig_bar_bullish' not in df.columns:
            df['sig_bar_bullish'] = False
        if 'sig_bar_bearish' not in df.columns:
            df['sig_bar_bearish'] = False
        if 'macro_phase' not in df.columns:
            df['macro_phase'] = 'transition'

        # 输出数组
        rom_down = np.zeros(n, dtype=bool)
        rom_up = np.zeros(n, dtype=bool)
        ctrl_low = np.full(n, np.nan)
        ctrl_high = np.full(n, np.nan)
        anchor_long_idx = np.full(n, -1, dtype=int)   # 当前多头锚点建立时的 bar 索引
        anchor_short_idx = np.full(n, -1, dtype=int)

        # 状态
        long_anchor = np.nan   # 最后一根多头 sig_bar 的 low
        short_anchor = np.nan  # 最后一根空头 sig_bar 的 high
        long_anchor_idx = -1   # 锚点建立时的 bar 索引（用于计算锚点年龄）
        short_anchor_idx = -1
        bars_in_transition = 0  # 连续处于 transition 的 K 线计数

        # 预取数组（避免 iloc 循环的性能开销）
        sig_bull = df['sig_bar_bullish'].values
        sig_bear = df['sig_bar_bearish'].values
        highs = df['high'].values
        lows = df['low'].values
        closes = df['close'].values
        phases = df['macro_phase'].values

        reversal_down_count = 0
        reversal_up_count = 0

        for i in range(n):
            phase = phases[i] if isinstance(phases[i], str) else str(phases[i])

            # ── 1. 锚点过期：transition 持续超过 expiry_limit ──
            if phase == 'transition':
                bars_in_transition += 1
            else:
                bars_in_transition = 0

            if bars_in_transition > self.expiry_limit:
                if not np.isnan(long_anchor) or not np.isnan(short_anchor):
                    logger.debug(
                        f'RoM 锚点过期吸收: transition 持续 {bars_in_transition} 根, '
                        f'long_anchor={long_anchor:.1f} short_anchor={short_anchor:.1f}'
                    )
                long_anchor = np.nan
                short_anchor = np.nan
                long_anchor_idx = -1
                short_anchor_idx = -1

            # ── 2. 锚点锁定：捕捉最新机构意图 ──
            if sig_bull[i]:
                long_anchor = lows[i]
                long_anchor_idx = i
                bars_in_transition = 0  # 新 sig_bar 重置过期计数
                logger.debug(f'RoM 多头锚点更新: low={lows[i]:.1f} @ bar {i}')

            if sig_bear[i]:
                short_anchor = highs[i]
                short_anchor_idx = i
                bars_in_transition = 0
                logger.debug(f'RoM 空头锚点更新: high={highs[i]:.1f} @ bar {i}')

            # ── 3. 击穿判定：反向显著柱线收盘穿透锚点 ──
            # "以显著柱线反转显著柱线" —— 必须由反向 sig_bar 确认
            # 多头控制权被击穿：空头 sig_bar 收盘 < 多头锚点
            if sig_bear[i] and not np.isnan(long_anchor) and closes[i] < long_anchor:
                rom_down[i] = True
                reversal_down_count += 1
                anchor_age_bars = i - long_anchor_idx if long_anchor_idx >= 0 else 0
                logger.info(
                    f'RoM ↓ 控制权反转: sig_bar_bear close={closes[i]:.1f} < '
                    f'long_anchor={long_anchor:.1f} @ bar {i}, '
                    f'anchor_age={anchor_age_bars}bars'
                )
                long_anchor = np.nan  # 击穿后重置
                long_anchor_idx = -1

            # 空头控制权被击穿：多头 sig_bar 收盘 > 空头锚点
            if sig_bull[i] and not np.isnan(short_anchor) and closes[i] > short_anchor:
                rom_up[i] = True
                reversal_up_count += 1
                anchor_age_bars = i - short_anchor_idx if short_anchor_idx >= 0 else 0
                logger.info(
                    f'RoM ↑ 控制权反转: sig_bar_bull close={closes[i]:.1f} > '
                    f'short_anchor={short_anchor:.1f} @ bar {i}, '
                    f'anchor_age={anchor_age_bars}bars'
                )
                short_anchor = np.nan
                short_anchor_idx = -1

            # 记录当前锚点状态
            ctrl_low[i] = long_anchor
            ctrl_high[i] = short_anchor
            anchor_long_idx[i] = long_anchor_idx
            anchor_short_idx[i] = short_anchor_idx

        df['rom_reversal_down'] = rom_down
        df['rom_reversal_up'] = rom_up
        df['rom_control_bar_low'] = ctrl_low
        df['rom_control_bar_high'] = ctrl_high
        df['rom_anchor_long_idx'] = anchor_long_idx
        df['rom_anchor_short_idx'] = anchor_short_idx

        logger.info(
            f'RoM 检测完成: reversal_down={reversal_down_count}, '
            f'reversal_up={reversal_up_count}, rows={n}'
        )

        return df


def is_rom_enabled() -> bool:
    """检查 RoM 是否启用（投票模式）"""
    return os.getenv('WY_ROM_ENABLED', '0').lower() not in ('0', 'false', 'off')
