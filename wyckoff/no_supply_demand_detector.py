"""
Task 2.9: No Supply / No Demand 微结构检测器

威科夫理论：
    No Supply（无供应）：成交量极低的窄幅K线出现在支撑位附近或积累区间内，
        说明卖方已无力压价，专业资金即将主导上行。

    No Demand（无需求）：成交量极低的窄幅K线出现在阻力位附近或派发区间内，
        说明买方已无力推价，专业资金即将主导下行。

核心设计——位置约束（location_constraint）：
    只有K线发生在"前期有巨量供应/需求痕迹"的价位附近，才触发信号。
    这防止了在随机位置的缩量漂移被误读为专业意图。

    合法位置（满足任一）：
        A. 前期 SC/BC 的价格水平（±1%）—— climatic_level 字段提供
        B. 高成交量节点（HVN）：近 60 根K线内成交量前 20% 的价位（±1%）
        C. 关键均线支撑/阻力（±0.5%）：EMA50 / EMA200

核心输出字段：
    no_supply_bar        : bool   — 当前K线是否为无供应柱
    no_demand_bar        : bool   — 当前K线是否为无需求柱
    no_supply_strength   : float  — 无供应信号强度（0.0-1.0），越大越强
    no_demand_strength   : float  — 无需求信号强度（0.0-1.0），越大越强
    ns_location_valid    : bool   — 当前价位是否满足位置约束（用于外部调试）
    nd_location_valid    : bool   — 当前价位是否满足位置约束（用于外部调试）
    hvn_level            : float  — 最近高成交量节点价位（0.0 = 无有效 HVN）

物理依据：
    - No Supply 出现在前期 SC 附近：SC 时供应被吸收，此处再次出现无供应说明测试通过
    - No Supply 必须缩量：成交量 < 20周期均量 × 0.7 → 没有新供应进场
    - No Supply 必须窄幅：K线范围 < ATR × 0.5 → 价格无法移动 = 无供应阻力
    - No Supply 收盘偏高：K线内收盘位置 > 40% → 买方略占上风

与系统集成：
    - LPS 信号需同时满足 no_supply_bar（强化 LPS 确认质量）
    - populate_entry_trend：axis_support 入场可集成 no_supply_bar 作为额外加分
    - exit_trend：no_demand_bar 出现在持仓过程中 → 触发早期警告退出标记
    - Task 2.10 SOW/UTAD/LPSY：no_supply/no_demand 作为结构确认维度

防误判设计：
    - 位置约束为硬性过滤（没有合法位置 → 输出 False，不降级误报）
    - 成交量约束和宽幅约束同时满足才触发
    - 输出 no_supply_strength 便于上层策略按强度分级使用
"""

import numpy as np
import pandas as pd
import logging
from pandas import DataFrame

logger = logging.getLogger(__name__)


class NoSupplyDemandDetector:
    """No Supply / No Demand 微结构检测器（Task 2.9）"""

    def __init__(self, strategy=None):
        self.strategy = strategy

        # ── No Supply / No Demand 基础门槛 ─────────────────────────────
        self.volume_mult_threshold  = 0.70   # 成交量必须低于均量 × 70%
        self.range_atr_threshold    = 0.60   # K线范围必须小于 ATR × 60%
        self.close_pos_ns_min       = 0.35   # No Supply：收盘位置 ≥ 35%（买方轻微主导）
        self.close_pos_nd_max       = 0.65   # No Demand：收盘位置 ≤ 65%（卖方轻微主导）

        # ── 位置约束参数 ───────────────────────────────────────────────
        self.climatic_tolerance     = 0.012  # SC/BC 价位容差 ±1.2%
        self.hvn_lookback           = 60     # 高成交量节点回看窗口
        self.hvn_top_pct            = 0.20   # 高成交量节点：成交量前 20% 的K线
        self.hvn_tolerance          = 0.012  # HVN 价位容差 ±1.2%
        self.ema_tolerance          = 0.005  # EMA 支撑/阻力容差 ±0.5%

        # ── 强度计算参数 ───────────────────────────────────────────────
        self.volume_score_weight    = 0.40   # 成交量缩减对强度的权重
        self.range_score_weight     = 0.35   # 窄幅对强度的权重
        self.location_score_weight  = 0.25   # 位置精准度对强度的权重

        # ── 持久化窗口 ─────────────────────────────────────────────────
        self.signal_persistence     = 3      # 信号持续 N 根K线（避免单根错过）

    # =========================================================================
    # 主检测入口
    # =========================================================================

    def detect(self, dataframe: DataFrame) -> DataFrame:
        """
        主检测入口：计算所有 No Supply / No Demand 相关字段

        Args:
            dataframe: 含 OHLCV + atr_20 + volume_sma_20 + ema_50 + ema_200 +
                       close_position + bar_range +
                       climatic_level（Task 2.7 产出）的 DataFrame

        Returns:
            添加了 ns/nd 字段的 DataFrame
        """
        try:
            df = dataframe.copy()

            # 基础字段准备
            volume_sma = self._get_volume_sma(df)
            atr        = self._get_atr(df)
            bar_range  = df['high'] - df['low']
            close_pos  = (df['close'] - df['low']) / (bar_range + 1e-9)

            # ── 基础条件（成交量 + 宽幅）──────────────────────────────
            low_volume = df['volume'] < (volume_sma * self.volume_mult_threshold)
            narrow_bar = bar_range < (atr * self.range_atr_threshold)
            base_ok    = low_volume & narrow_bar

            # ── 收盘位置分裂 ───────────────────────────────────────────
            ns_close_ok = close_pos >= self.close_pos_ns_min   # No Supply：收盘偏高
            nd_close_ok = close_pos <= self.close_pos_nd_max   # No Demand：收盘偏低

            # ── 高成交量节点（HVN）检测 ────────────────────────────────
            hvn_levels = self._calc_hvn(df, volume_sma)

            # ── 位置约束（No Supply：需在支撑 / SC 附近）────────────────
            ns_location = self._ns_location_valid(df, hvn_levels)
            nd_location = self._nd_location_valid(df, hvn_levels)

            # ── 原始信号（无持久化）────────────────────────────────────
            ns_raw = base_ok & ns_close_ok & ns_location
            nd_raw = base_ok & nd_close_ok & nd_location

            # ── 持久化（前 N 根内有信号则当根也保持）─────────────────
            ns_signal = ns_raw.rolling(self.signal_persistence, min_periods=1).max().astype(bool)
            nd_signal = nd_raw.rolling(self.signal_persistence, min_periods=1).max().astype(bool)

            # ── 强度计算 ───────────────────────────────────────────────
            ns_strength = self._calc_strength(
                df['volume'], volume_sma, bar_range, atr, ns_location)
            nd_strength = self._calc_strength(
                df['volume'], volume_sma, bar_range, atr, nd_location)

            # 未满足位置约束时强度强制为 0
            ns_strength = ns_strength.where(ns_location, 0.0)
            nd_strength = nd_strength.where(nd_location, 0.0)

            # ── 写入 DataFrame ─────────────────────────────────────────
            dataframe['no_supply_bar']      = ns_signal
            dataframe['no_demand_bar']      = nd_signal
            dataframe['no_supply_strength'] = ns_strength.round(3)
            dataframe['no_demand_strength'] = nd_strength.round(3)
            dataframe['ns_location_valid']  = ns_location
            dataframe['nd_location_valid']  = nd_location
            dataframe['hvn_level']          = hvn_levels.round(2)

            # 日志统计
            ns_count = int(ns_signal.sum())
            nd_count = int(nd_signal.sum())
            logger.info(
                f"✅ NoSupplyDemandDetector 完成，"
                f"No Supply={ns_count}，No Demand={nd_count}，"
                f"共 {len(dataframe)} 根K线"
            )

        except Exception as e:
            logger.error(f"❌ NoSupplyDemandDetector.detect() 出错: {e}", exc_info=True)
            dataframe['no_supply_bar']      = False
            dataframe['no_demand_bar']      = False
            dataframe['no_supply_strength'] = 0.0
            dataframe['no_demand_strength'] = 0.0
            dataframe['ns_location_valid']  = False
            dataframe['nd_location_valid']  = False
            dataframe['hvn_level']          = 0.0

        return dataframe

    # =========================================================================
    # 位置约束检测
    # =========================================================================

    def _ns_location_valid(self, df: DataFrame,
                           hvn_levels: pd.Series) -> pd.Series:
        """
        No Supply 位置约束：当前收盘价必须在以下任一位置的 ±tol 范围内：
            A. 最近 SC 的 climatic_level（±1.2%）
            B. 近 60 根K线内高成交量节点（±1.2%）
            C. EMA50 或 EMA200（±0.5%，作为动态支撑）
        """
        close = df['close']
        valid = pd.Series(False, index=df.index)

        # A. SC 价位（climatic_level + type == 'SC'）
        if 'climatic_level' in df.columns and 'climatic_type' in df.columns:
            sc_level = df['climatic_level'].copy()
            # 仅使用 SC 类型的气候行动价位
            sc_level = sc_level.where(
                df.get('climatic_type', pd.Series('none', index=df.index)) != 'BC',
                other=np.nan
            )
            # 向前填充（保留最近一次 SC 价位）
            sc_level_filled = sc_level.replace(0.0, np.nan).ffill()
            near_sc = (
                sc_level_filled.notna() &
                (close >= sc_level_filled * (1 - self.climatic_tolerance)) &
                (close <= sc_level_filled * (1 + self.climatic_tolerance))
            )
            valid = valid | near_sc

        # B. HVN 价位
        hvn_valid = hvn_levels.notna() & (hvn_levels > 0)
        near_hvn = (
            hvn_valid &
            (close >= hvn_levels * (1 - self.hvn_tolerance)) &
            (close <= hvn_levels * (1 + self.hvn_tolerance))
        )
        valid = valid | near_hvn

        # C. EMA50 支撑
        if 'ema_50' in df.columns:
            ema50 = df['ema_50']
            near_ema50 = (
                ema50.notna() &
                (close >= ema50 * (1 - self.ema_tolerance)) &
                (close <= ema50 * (1 + self.ema_tolerance * 3))  # 允许略高于 EMA50
            )
            valid = valid | near_ema50

        # D. EMA200 支撑（更强的支撑位）
        if 'ema_200' in df.columns:
            ema200 = df['ema_200']
            near_ema200 = (
                ema200.notna() &
                (close >= ema200 * (1 - self.ema_tolerance)) &
                (close <= ema200 * (1 + self.ema_tolerance * 4))
            )
            valid = valid | near_ema200

        return valid

    def _nd_location_valid(self, df: DataFrame,
                           hvn_levels: pd.Series) -> pd.Series:
        """
        No Demand 位置约束：当前收盘价必须在以下任一位置的 ±tol 范围内：
            A. 最近 BC 的 climatic_level（±1.2%）
            B. 近 60 根K线内高成交量节点（±1.2%）
            C. EMA50 或 EMA200（±0.5%，作为动态阻力）
        """
        close = df['close']
        valid = pd.Series(False, index=df.index)

        # A. BC 价位
        if 'climatic_level' in df.columns and 'climatic_type' in df.columns:
            bc_level = df['climatic_level'].copy()
            bc_level = bc_level.where(
                df.get('climatic_type', pd.Series('none', index=df.index)) != 'SC',
                other=np.nan
            )
            bc_level_filled = bc_level.replace(0.0, np.nan).ffill()
            near_bc = (
                bc_level_filled.notna() &
                (close >= bc_level_filled * (1 - self.climatic_tolerance)) &
                (close <= bc_level_filled * (1 + self.climatic_tolerance))
            )
            valid = valid | near_bc

        # B. HVN 价位
        hvn_valid = hvn_levels.notna() & (hvn_levels > 0)
        near_hvn = (
            hvn_valid &
            (close >= hvn_levels * (1 - self.hvn_tolerance)) &
            (close <= hvn_levels * (1 + self.hvn_tolerance))
        )
        valid = valid | near_hvn

        # C. EMA50 阻力
        if 'ema_50' in df.columns:
            ema50 = df['ema_50']
            near_ema50 = (
                ema50.notna() &
                (close >= ema50 * (1 - self.ema_tolerance * 3)) &
                (close <= ema50 * (1 + self.ema_tolerance))
            )
            valid = valid | near_ema50

        # D. EMA200 阻力
        if 'ema_200' in df.columns:
            ema200 = df['ema_200']
            near_ema200 = (
                ema200.notna() &
                (close >= ema200 * (1 - self.ema_tolerance * 4)) &
                (close <= ema200 * (1 + self.ema_tolerance))
            )
            valid = valid | near_ema200

        return valid

    # =========================================================================
    # 高成交量节点（HVN）计算
    # =========================================================================

    def _calc_hvn(self, df: DataFrame,
                  volume_sma: pd.Series) -> pd.Series:
        """
        计算滚动窗口内的高成交量节点（HVN）价位。

        方法：在近 hvn_lookback 根K线内，找出成交量排名前 hvn_top_pct 的K线，
              取其收盘价（VWAP 近似）的均值作为 HVN 代表价位。

        返回 pd.Series：每根K线对应的最近 HVN 价位（0.0 = 无有效 HVN）
        """
        hvn = pd.Series(0.0, index=df.index)
        closes = df['close'].values
        volumes = df['volume'].values
        n = len(df)

        for i in range(self.hvn_lookback, n):
            window_start = i - self.hvn_lookback
            vol_window   = volumes[window_start:i]
            cls_window   = closes[window_start:i]

            # 找出成交量前 hvn_top_pct 的 bar
            threshold = np.percentile(vol_window, (1 - self.hvn_top_pct) * 100)
            high_vol_mask = vol_window >= threshold

            if high_vol_mask.sum() > 0:
                # 使用成交量加权价格作为 HVN 代表价位
                hvn_vol   = vol_window[high_vol_mask]
                hvn_price = cls_window[high_vol_mask]
                hvn.iloc[i] = float(np.average(hvn_price, weights=hvn_vol))

        return hvn

    # =========================================================================
    # 辅助字段
    # =========================================================================

    def _calc_strength(self, volume: pd.Series, volume_sma: pd.Series,
                       bar_range: pd.Series, atr: pd.Series,
                       location_valid: pd.Series) -> pd.Series:
        """
        计算信号强度（0.0 - 1.0）。

        三个维度：
            1. 成交量缩减程度（越低于均量越强）
            2. K线窄幅程度（越窄于ATR越强）
            3. 位置精准度（满足位置约束则附加权重，否则 0）
        """
        # 成交量分数（最低到最高映射到 0→1，逆向）
        vol_ratio    = (volume / (volume_sma + 1e-9)).clip(0.0, 1.0)
        vol_score    = (1.0 - vol_ratio).clip(0.0, 1.0)

        # 窄幅分数
        range_ratio  = (bar_range / (atr + 1e-9)).clip(0.0, 1.0)
        range_score  = (1.0 - range_ratio).clip(0.0, 1.0)

        # 位置分数（布尔型：满足=1.0，不满足=0.0）
        location_score = location_valid.astype(float)

        strength = (
            vol_score    * self.volume_score_weight   +
            range_score  * self.range_score_weight    +
            location_score * self.location_score_weight
        ).clip(0.0, 1.0)

        return strength.round(3)

    def _get_volume_sma(self, df: DataFrame) -> pd.Series:
        """获取或计算 20 周期成交量均线"""
        if 'volume_sma_20' in df.columns:
            return df['volume_sma_20']
        return df['volume'].rolling(20, min_periods=3).mean().fillna(df['volume'])

    def _get_atr(self, df: DataFrame) -> pd.Series:
        """获取或计算 ATR（使用 atr_20 或回退到 (high-low) 的 20 周期均值）"""
        if 'atr_20' in df.columns:
            return df['atr_20']
        bar_range = df['high'] - df['low']
        return bar_range.rolling(20, min_periods=3).mean().fillna(bar_range)
