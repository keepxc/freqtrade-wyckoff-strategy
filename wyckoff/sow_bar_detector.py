"""
Task 2.10: SOW Bar 检测器 + UTAD 陷阱检测 + LPSY 确认

威科夫理论（空头视角）：
    SOW（Sign of Weakness）Bar 是与 SOS Bar 完全对称的弱势信号，
    标志着供应方完全控制价格，是开空的早期确认信号。

    UTAD（Upthrust After Distribution）：
        价格向上突破分配区间上沿（阻力位）后，因无充分买盘支撑
        迅速回落，是价格"陷阱"——突破不能持续，方向反转。

    LPSY（Last Point of Supply）：
        Markdown 走势中价格回抽测试 Ice 线（支撑转阻力），
        此处成交量萎缩（买盘枯竭），收盘仍在 Ice 下方，
        是最后一次开空的高胜算机会。

核心设计（SOS 的完全镜像 + 扩展）：

SOW Bar 四大硬指标：
    1. 宽价差（Wide Spread）：bar_range > ATR × 1.5
    2. 高成交量（High Volume）：volume > volume_sma_20 × 1.5
    3. 低收盘（Close Near Low）：close_position < 0.30（收在下30%）
    4. 价格承诺（Price Breach）：close < nearest_support（跌破支撑位）

UTAD 陷阱检测：
    前提：
        - 价格向上突破近期阻力位（close > nearest_resistance）
        - 突破时成交量高（volume > volume_sma_20 × 2.0）：假突破特征
    确认（1-3根K线后）：
        - 收盘回落至阻力位以下（close < nearest_resistance × 0.99）
        - rs_signal = 'falling'（可选高权过滤，BTC 弱于大盘）
    触发：开空，止损在 UTAD 高点上方 1.5 × ATR

LPSY 确认做空：
    条件：
        - ice_line_active = True（Ice 线已激活）
        - 价格已有效跌破 Ice（通过 ice_break_confirmed 字段）
        - 回抽测试 Ice：close 接近 ice_level ± 1.5%（price 在 ice 附近）
        - No Demand 确认：no_demand_bar = True（买盘枯竭，缩量上涨）
        - 收盘未能重回 Ice 上方（close < ice_level × 1.005）
    触发：开空，止损在 Ice 上方 1.0 × ATR

快速平多逻辑（multi-exit）：
    SOW Bar 出现时，无论当前持多仓，立即触发多头止盈退出信号。
    在 populate_exit_trend 中体现为 exit_long = 1。

核心输出字段：
    sow_bar_detected      : bool   — 是否为 SOW Bar（四大硬指标全满足）
    sow_quality_score     : float  — SOW 质量评分（0-100）
    utad_detected         : bool   — 是否检测到 UTAD 陷阱
    utad_high             : float  — UTAD 高点价格（用于止损计算）
    utad_bars_since       : int    — 距 UTAD 突破的K线数（1-3内有效）
    lpsy_detected         : bool   — 是否检测到 LPSY 信号
    lpsy_ice_level        : float  — LPSY 时的 Ice 线价位（用于止损计算）
    sow_exit_long_signal  : bool   — SOW 快速平多触发器

防误判设计：
    - SOW 四大指标缺一不可（与 SOS 对称，稳健性优先）
    - UTAD：要求突破时成交量極高（2.0x），并在 1-3 根K线内快速回落
    - LPSY：必须有已激活的 Ice 线且已有跌破 Ice 确认，防止随机支撑测试误报
    - 只有在 trading_mode = 'futures'（合约模式）时才触发 enter_short

与系统集成：
    - populate_indicators 调用 detect()，产出上述字段
    - populate_entry_trend 中：SOW/UTAD/LPSY → enter_short = 1（合约模式）
    - populate_exit_trend 中：sow_exit_long_signal → exit_long = 1（强制平多）
    - Task 2.9 No Demand：LPSY 使用 no_demand_bar 作为确认维度
    - Task 2.8 Composite Man：rs_signal = 'falling' 作为 UTAD 的高权过滤
"""

import numpy as np
import pandas as pd
import logging
from pandas import DataFrame

logger = logging.getLogger(__name__)


class SOWBarDetector:
    """
    SOW Bar 检测器 + UTAD 陷阱 + LPSY 确认（Task 2.10）

    SOS Bar 的完全空头镜像，配合合约做空引擎使用。
    """

    def __init__(self, strategy=None):
        self.strategy = strategy

        # ── SOW Bar 四大硬指标阈值（与 SOS 对称）─────────────────────
        self.bar_range_multiplier    = 1.5     # 宽价差：1.5x ATR
        self.volume_multiplier       = 1.5     # 高成交量：1.5x SMA
        self.close_position_max      = 0.30    # 低收盘：收在下30%（SOS 是 >0.70）
        # 价格承诺：close < nearest_support（跌破支撑，与 SOS 对称的 close > resistance）

        # ── SOW 质量评分权重（与 SOS 对称）────────────────────────────
        self.weight_spread           = 0.30
        self.weight_volume           = 0.25
        self.weight_close_position   = 0.15
        self.weight_price_commitment = 0.30

        # ── UTAD 参数 ──────────────────────────────────────────────────
        self.utad_breakout_vol_mult  = 2.0     # 突破时需要的成交量倍数（高量=假突破特征）
        self.utad_revert_bars        = 3       # 突破后多少根K线内必须回落
        self.utad_revert_tolerance   = 0.01    # 回落必须跌破阻力位 × (1 - tolerance)

        # ── LPSY 参数 ─────────────────────────────────────────────────
        self.lpsy_ice_tolerance      = 0.015   # 回抽测试 Ice 的价格容差 ±1.5%
        self.lpsy_close_tolerance    = 0.005   # 收盘必须在 Ice 下方（Ice × 1.005 以下）

        # ── 快速平多阈值 ───────────────────────────────────────────────
        # SOW Bar 出现即触发 sow_exit_long_signal（无额外门槛）

        logger.info(
            f"SOWBarDetector 初始化完成 ("
            f"SOW: spread={self.bar_range_multiplier}x, vol={self.volume_multiplier}x, "
            f"close_pos<{self.close_position_max} | "
            f"UTAD: 突破量>{self.utad_breakout_vol_mult}x, 回落<{self.utad_revert_bars}根 | "
            f"LPSY: ice容差±{self.lpsy_ice_tolerance*100:.1f}%)"
        )

    # ─────────────────────────────────────────────────────────────────────────
    # 主检测入口
    # ─────────────────────────────────────────────────────────────────────────

    def detect(self, dataframe: DataFrame) -> DataFrame:
        """
        计算所有 SOW/UTAD/LPSY 相关字段。

        Args:
            dataframe: 包含 OHLCV + atr + volume_sma_20 +
                       nearest_support/resistance + ice_line_active +
                       ice_break_confirmed + no_demand_bar + rs_signal 的 DataFrame

        Returns:
            添加了 SOW 检测字段的 DataFrame
        """
        # 初始化输出字段
        dataframe['sow_bar_detected']     = False
        dataframe['sow_quality_score']    = 0.0
        dataframe['sow_spread_score']     = 0.0
        dataframe['sow_volume_score']     = 0.0
        dataframe['sow_close_pos_score']  = 0.0
        dataframe['sow_commitment_score'] = 0.0
        dataframe['utad_detected']        = False
        dataframe['utad_high']            = 0.0
        dataframe['utad_bars_since']      = 999
        dataframe['lpsy_detected']        = False
        dataframe['lpsy_ice_level']       = 0.0
        dataframe['sow_exit_long_signal'] = False

        try:
            dataframe = self._detect_sow_bars(dataframe)
            dataframe = self._detect_utad(dataframe)
            dataframe = self._detect_lpsy(dataframe)
            dataframe = self._compute_exit_long_signal(dataframe)
            self._log_stats(dataframe)
        except Exception as e:
            logger.warning(f"SOWBarDetector.detect() 异常: {e}", exc_info=True)

        return dataframe

    # ─────────────────────────────────────────────────────────────────────────
    # Step 1: SOW Bar 四大硬指标
    # ─────────────────────────────────────────────────────────────────────────

    def _detect_sow_bars(self, dataframe: DataFrame) -> DataFrame:
        """SOW Bar 检测：SOS 的完整镜像（方向相反）"""

        # 必要字段检查
        required = ['atr', 'volume_sma_20', 'nearest_support']
        for col in required:
            if col not in dataframe.columns:
                logger.warning(f"⚠️ SOW 检测缺少字段 '{col}'，跳过 SOW 检测")
                return dataframe

        # 基础计算（若尚未有 bar_range/close_position 则自行计算）
        if 'bar_range' not in dataframe.columns:
            dataframe['bar_range'] = dataframe['high'] - dataframe['low']
        if 'close_position' not in dataframe.columns:
            dataframe['close_position'] = np.where(
                dataframe['bar_range'] > 0,
                (dataframe['close'] - dataframe['low']) / dataframe['bar_range'],
                0.5
            )

        # ── 四大硬指标 ────────────────────────────────────────────────

        # 1. 宽价差
        cond_spread = dataframe['bar_range'] > (dataframe['atr'] * self.bar_range_multiplier)

        # 2. 高成交量
        cond_volume = dataframe['volume'] > (dataframe['volume_sma_20'] * self.volume_multiplier)

        # 3. 低收盘（SOS 的镜像：收在 K 线下30%）
        cond_close  = dataframe['close_position'] < self.close_position_max

        # 4. 价格承诺：收盘跌破支撑位（SOS 是突破阻力位）
        cond_commit = dataframe['close'] < dataframe['nearest_support']

        sow_mask = cond_spread & cond_volume & cond_close & cond_commit
        dataframe.loc[sow_mask, 'sow_bar_detected'] = True

        # ── 质量评分（0-100）──────────────────────────────────────────
        idx = dataframe[sow_mask].index
        if len(idx) > 0:
            # 价差得分
            spread_ratio = dataframe.loc[idx, 'bar_range'] / (
                dataframe.loc[idx, 'atr'] * self.bar_range_multiplier
            )
            dataframe.loc[idx, 'sow_spread_score'] = np.minimum(
                (spread_ratio - 1.0) / 1.0 + 0.5, 1.0
            )

            # 成交量得分
            vol_ratio = dataframe.loc[idx, 'volume'] / (
                dataframe.loc[idx, 'volume_sma_20'] * self.volume_multiplier
            )
            dataframe.loc[idx, 'sow_volume_score'] = np.minimum(
                (vol_ratio - 1.0) / 1.0 + 0.5, 1.0
            )

            # 收盘位置得分（越低越好，0% = 1.0分，30% = 0.5分）
            close_pos_norm = (self.close_position_max - dataframe.loc[idx, 'close_position']) / self.close_position_max
            dataframe.loc[idx, 'sow_close_pos_score'] = np.minimum(
                close_pos_norm * 0.5 + 0.5, 1.0
            )

            # 价格承诺得分（跌破支撑幅度越大，得分越高）
            breach = (dataframe.loc[idx, 'nearest_support'] - dataframe.loc[idx, 'close']) / (
                dataframe.loc[idx, 'nearest_support'] + 1e-9
            )
            dataframe.loc[idx, 'sow_commitment_score'] = np.minimum(
                breach * 10 + 0.5, 1.0
            )

            # 综合质量评分（0-100）
            dataframe.loc[idx, 'sow_quality_score'] = (
                dataframe.loc[idx, 'sow_spread_score']     * self.weight_spread +
                dataframe.loc[idx, 'sow_volume_score']     * self.weight_volume +
                dataframe.loc[idx, 'sow_close_pos_score']  * self.weight_close_position +
                dataframe.loc[idx, 'sow_commitment_score'] * self.weight_price_commitment
            ) * 100

        return dataframe

    # ─────────────────────────────────────────────────────────────────────────
    # Step 2: UTAD 陷阱检测
    # ─────────────────────────────────────────────────────────────────────────

    def _detect_utad(self, dataframe: DataFrame) -> DataFrame:
        """
        UTAD：价格短暂突破阻力位后高量回落。
        逻辑：
            1. 标记每一根高量突破阻力的K线（潜在 UTAD 突破柱）
            2. 在突破后 1-3 根K线内，检查是否快速回落阻力位以下
        """
        if 'nearest_resistance' not in dataframe.columns:
            return dataframe

        # 步骤一：潜在突破标记
        breakout_mask = (
            (dataframe['close'] > dataframe['nearest_resistance']) &
            (dataframe['volume'] > dataframe['volume_sma_20'] * self.utad_breakout_vol_mult)
        )

        # 同时记录突破时的高点价格
        dataframe['_utad_breakout_high'] = np.where(breakout_mask, dataframe['high'], np.nan)
        dataframe['_utad_breakout_high'] = dataframe['_utad_breakout_high'].ffill()
        # 只保留最近 utad_revert_bars 根K线内的高点
        # 标记哪些根K线在有效突破窗口内
        dataframe['_utad_breakout_bar_idx'] = np.where(breakout_mask, dataframe.index, np.nan)

        # 使用 rolling 方法：向前看 utad_revert_bars 根K线内是否有突破
        # 转为整数位置 index 操作
        n = len(dataframe)
        utad_confirmed = np.zeros(n, dtype=bool)
        utad_high_arr  = np.zeros(n, dtype=float)

        breakout_positions = np.where(breakout_mask.values)[0]

        for bp in breakout_positions:
            breakout_high = dataframe['high'].iloc[bp]
            breakout_resistance = dataframe['nearest_resistance'].iloc[bp]
            # 在突破后第 1 到 utad_revert_bars 根K线内查找回落
            for offset in range(1, self.utad_revert_bars + 1):
                check_pos = bp + offset
                if check_pos >= n:
                    break
                revert_close = dataframe['close'].iloc[check_pos]
                # 若收盘回落至阻力位以下（即突破失败）
                threshold = breakout_resistance * (1 - self.utad_revert_tolerance)
                if revert_close < threshold:
                    utad_confirmed[check_pos] = True
                    utad_high_arr[check_pos]  = breakout_high
                    break  # 只记录第一个确认K线

        dataframe['utad_detected'] = utad_confirmed
        dataframe['utad_high']     = utad_high_arr
        dataframe.loc[dataframe['utad_detected'], 'utad_bars_since'] = 0

        # 清理临时列
        dataframe.drop(columns=['_utad_breakout_high', '_utad_breakout_bar_idx'],
                       errors='ignore', inplace=True)

        return dataframe

    # ─────────────────────────────────────────────────────────────────────────
    # Step 3: LPSY 确认做空
    # ─────────────────────────────────────────────────────────────────────────

    def _detect_lpsy(self, dataframe: DataFrame) -> DataFrame:
        """
        LPSY：Ice 线跌破后的回抽测试。
        条件：
            1. ice_line_active = True
            2. ice_break_confirmed = True（已有效跌破 Ice）
            3. close 在 Ice ± lpsy_ice_tolerance 范围内（正在回抽测试）
            4. no_demand_bar = True（买盘枯竭）
            5. close < ice_level × (1 + lpsy_close_tolerance)（未能重回 Ice 上方）
        """
        # 必要字段检查
        ice_active  = dataframe.get('ice_line_active',    pd.Series(False, index=dataframe.index))
        ice_break   = dataframe.get('ice_break_confirmed', pd.Series(False, index=dataframe.index))
        no_demand   = dataframe.get('no_demand_bar',       pd.Series(False, index=dataframe.index))
        ice_level   = dataframe.get('ice_line_level',      pd.Series(0.0,   index=dataframe.index))

        # 若 ice_line_level 不存在，用 nearest_support 作为代理（向前看）
        if (ice_level == 0.0).all():
            ice_level = dataframe.get('nearest_support', pd.Series(0.0, index=dataframe.index))

        # 接近 Ice 的条件（回抽幅度在 ±1.5% 内）
        ice_nonzero = ice_level > 0
        near_ice = (
            ice_nonzero &
            (dataframe['close'] >= ice_level * (1 - self.lpsy_ice_tolerance)) &
            (dataframe['close'] <= ice_level * (1 + self.lpsy_ice_tolerance))
        )

        # 收盘必须未能重回 Ice 上方
        below_ice = dataframe['close'] < ice_level * (1 + self.lpsy_close_tolerance)

        lpsy_mask = (
            ice_active.astype(bool) &
            ice_break.astype(bool) &
            near_ice &
            no_demand.astype(bool) &
            below_ice
        )

        dataframe.loc[lpsy_mask, 'lpsy_detected']   = True
        dataframe.loc[lpsy_mask, 'lpsy_ice_level']  = ice_level[lpsy_mask]

        return dataframe

    # ─────────────────────────────────────────────────────────────────────────
    # Step 4: 快速平多触发
    # ─────────────────────────────────────────────────────────────────────────

    def _compute_exit_long_signal(self, dataframe: DataFrame) -> DataFrame:
        """
        SOW Bar 出现时触发快速平多。
        任何 sow_bar_detected = True 的 K 线，均设置 sow_exit_long_signal = True。
        """
        dataframe.loc[dataframe['sow_bar_detected'], 'sow_exit_long_signal'] = True
        return dataframe

    # ─────────────────────────────────────────────────────────────────────────
    # 日志统计
    # ─────────────────────────────────────────────────────────────────────────

    def _log_stats(self, dataframe: DataFrame) -> None:
        sow_count  = dataframe['sow_bar_detected'].sum()
        utad_count = dataframe['utad_detected'].sum()
        lpsy_count = dataframe['lpsy_detected'].sum()
        total      = len(dataframe)

        if sow_count > 0:
            avg_q = dataframe.loc[dataframe['sow_bar_detected'], 'sow_quality_score'].mean()
            logger.info(
                f"SOW Bar 检测: {sow_count} 个（平均质量={avg_q:.1f}）/ "
                f"UTAD={utad_count} / LPSY={lpsy_count} / "
                f"总K线={total}"
            )
        else:
            logger.debug(
                f"SOW Bar 检测完成: 无 SOW（UTAD={utad_count} / LPSY={lpsy_count}）"
            )
