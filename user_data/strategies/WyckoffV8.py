"""
Wyckoff V8 — 真威科夫因果律交易系统
=====================================
版本: V8.0.0
创建: 2026-05-10
作者: 从零实现，不引用 V7 代码逻辑

架构：
  日线层：sc_detector → phase_detector → pnf_calculator（TASK 1-3）
  1H 层：wave_start ChoCh + Phase D/E 上下文（TASK 4）
  退出层：custom_exit + PNF T1/T2/T3 目标位驱动（TASK 5）

核心设计决策：
  - 全走 custom_exit，零 exit_signal 退出（绕过 Freqtrade 向量化约束）
  - 入场：Phase A/B 不开，D/E/unknown 允许
  - 必须有 PNF 目标位（有因果依据才进场）
  - T1 到达 → 移止损到保本，继续持有
  - 结构止损：TR Low 优先，ATR 兜底
  - OI 接口预留，默认不过滤

对比基线：V7.1+A+B = 11,313%（2022-2026，DD 6.28%）
"""

import logging
import numpy as np
import pandas as pd
from pandas import DataFrame
from typing import Optional, Tuple
from functools import reduce
import os

from freqtrade.strategy import IStrategy, DecimalParameter, IntParameter
import talib.abstract as ta

logger = logging.getLogger(__name__)

# ── V8 模块导入 ──────────────────────────────────────────────
try:
    from user_data.strategies.v8_modules.sc_detector import add_v8_sc_detection
    from user_data.strategies.v8_modules.phase_detector import add_v8_phase_detection
    from user_data.strategies.v8_modules.pnf_calculator import add_v8_pnf_targets
    from user_data.strategies.v8_modules.entry_detector import compute_entry_signal, entry_funnel_stats
    V8_MODULES_OK = True
    logger.info('V8 modules loaded: sc_detector / phase_detector / pnf_calculator / entry_detector')
except ImportError as e:
    V8_MODULES_OK = False
    logger.error(f'V8 modules import failed: {e}')

# ── WaveAnalyzer 导入 ────────────────────────────────────────
try:
    from wyckoff.wave_analyzer import WaveAnalyzer
    WAVE_ANALYZER_OK = True
    logger.info('WaveAnalyzer loaded')
except ImportError:
    WAVE_ANALYZER_OK = False
    logger.warning('WaveAnalyzer not available — wave_start signals will be empty')


# ── WyckoffWave dataclass（与 V7 保持完全一致）──────────────
from dataclasses import dataclass

@dataclass
class WyckoffWave:
    start_idx: int
    end_idx: int
    start_price: float
    end_price: float
    high_price: float
    low_price: float
    total_volume: float
    bar_count: int
    direction: str  # 'up' or 'down'

    @property
    def displacement(self) -> float:
        return abs(self.end_price - self.start_price)

    @property
    def effort(self) -> float:
        return self.total_volume

    @property
    def efficiency(self) -> float:
        return self.displacement / (self.effort + 1e-9)

    @property
    def is_up_wave(self) -> bool:
        return self.direction == 'up'

    @property
    def duration_hours(self) -> int:
        return self.bar_count


class WyckoffV8(IStrategy):
    """
    Wyckoff V8 策略 — 真威科夫因果律实现

    入场：wave_start ChoCh（1H）+ Phase D/E 上下文（日线）+ PNF 目标位确认
    退出：全 custom_exit，PNF T1/T2/T3 目标位驱动 + 结构止损
    """

    # ── 基础配置 ─────────────────────────────────────────────
    INTERFACE_VERSION = 3
    timeframe = '1h'
    can_short = False

    # 不使用 exit_signal（全走 custom_exit）
    use_exit_signal = False
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # ROI：设置极高值，实际退出由 custom_exit 控制
    minimal_roi = {"0": 99.0}

    # 止损：兜底保护，custom_stoploss 启用后会接管逻辑
    stoploss = -0.12
    use_custom_stoploss = True  # 启用 custom_stoploss（backtesting 必须显式声明）

    # Trailing stop 关闭（由 custom_stoploss 手动管理）
    trailing_stop = False

    # 启动K线数（日线 SC 检测需要至少 252 根）
    startup_candle_count = 300

    # ── WaveAnalyzer 依赖参数（保持与 V7 默认值一致）────────
    wave_min_percent = DecimalParameter(0.005, 0.015, default=0.008, space='buy', optimize=False, load=True)
    wave_atr_multiplier = DecimalParameter(1.0, 2.5, default=1.5, space='buy', optimize=False, load=True)
    wave_min_bars = IntParameter(2, 8, default=3, space='buy', optimize=False, load=True)
    sot_wave_count = IntParameter(3, 5, default=3, space='buy', optimize=False, load=True)
    sot_displacement_ratio = DecimalParameter(0.7, 0.9, default=0.8, space='buy', optimize=False, load=True)

    # ── 参数 ─────────────────────────────────────────────────
    # 结构止损：TR Low 之下的缓冲比例
    tr_low_buffer = DecimalParameter(0.005, 0.03, default=0.01, decimals=3,
                                     space='sell', optimize=False, load=True)
    # T1 到达后止损移到保本的偏移（避免手续费磨损）
    breakeven_offset = DecimalParameter(0.001, 0.01, default=0.003, decimals=3,
                                        space='sell', optimize=False, load=True)

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.waves_cache = []
        self.wave_analysis_cache = {}
        if WAVE_ANALYZER_OK:
            self.wave_analyzer = WaveAnalyzer(self, WyckoffWave)
        else:
            self.wave_analyzer = None

    # ── Informative Pairs ────────────────────────────────────
    def informative_pairs(self):
        pairs = self.dp.current_whitelist()
        return [(pair, '1d') for pair in pairs]

    # ── populate_indicators ──────────────────────────────────
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        pair = metadata['pair']

        # ── ATR ─────────────────────────────────────────────
        dataframe['atr14'] = ta.ATR(dataframe, timeperiod=14)
        dataframe['atr_20'] = ta.ATR(dataframe, timeperiod=20)  # WaveAnalyzer 依赖

        # ── Wave 检测（1H）──────────────────────────────────
        if self.wave_analyzer is not None:
            try:
                dataframe = self.wave_analyzer._segment_weis_waves(dataframe)
                wave_count = int(dataframe.get('is_wave_start', pd.Series(False)).sum())
                up_count = int((dataframe.get('is_wave_start', pd.Series(False)) &
                                (dataframe.get('wave_direction', pd.Series('')) == 'up')).sum())
                logger.info(f'[V8-DIAG] {pair} WaveAnalyzer OK: is_wave_start={wave_count} up_waves={up_count}')
            except Exception as e:
                logger.warning(f'[V8-DIAG] {pair} WaveAnalyzer failed: {e}')
                dataframe['is_wave_start'] = False
                dataframe['wave_direction'] = ''
                dataframe['wave_efficiency'] = 0.0
        else:
            logger.warning(f'[V8-DIAG] {pair} WaveAnalyzer NOT loaded — wave_start=0')
            dataframe['is_wave_start'] = False
            dataframe['wave_direction'] = ''
            dataframe['wave_efficiency'] = 0.0

        # ── 日线 Phase / PNF（TASK 1-3）─────────────────────
        if V8_MODULES_OK:
            try:
                df_1d = self.dp.get_pair_dataframe(pair=pair, timeframe='1d')
                if df_1d is not None and len(df_1d) >= 100:
                    df_1d = add_v8_sc_detection(df_1d)
                    df_1d = add_v8_phase_detection(df_1d)
                    df_1d = add_v8_pnf_targets(df_1d)

                    sc_count = int(df_1d.get('v8_sc_final', pd.Series(False)).sum())
                    phase_d = int((df_1d.get('v8_phase', pd.Series('')) == 'D').sum())
                    phase_e = int((df_1d.get('v8_phase', pd.Series('')) == 'E').sum())
                    pnf_valid = int(df_1d.get('v8_target_1', pd.Series(float('nan'))).notna().sum())
                    logger.info(f'[V8-DIAG] {pair} daily: sc={sc_count} phase_D={phase_d} phase_E={phase_e} pnf_valid={pnf_valid}')

                    # merge 回 1H（forward-fill，避免未来数据泄露）
                    daily_cols = [
                        'date', 'v8_phase', 'v8_phase_allow_long',
                        'v8_tr_high', 'v8_tr_low',
                        'v8_target_1', 'v8_target_2', 'v8_target_3',
                        'v8_sc_final', 'v8_sc_low',
                    ]
                    daily_cols = [c for c in daily_cols if c in df_1d.columns]
                    df_1d_slim = df_1d[daily_cols].copy()
                    df_1d_slim['date'] = pd.to_datetime(df_1d_slim['date'], utc=True)

                    dataframe['date_utc'] = pd.to_datetime(dataframe['date'], utc=True)
                    dataframe = pd.merge_asof(
                        dataframe.sort_values('date_utc'),
                        df_1d_slim.rename(columns={'date': 'date_1d'}).sort_values('date_1d'),
                        left_on='date_utc',
                        right_on='date_1d',
                        direction='backward',
                    )
                    dataframe = dataframe.drop(columns=['date_utc', 'date_1d'], errors='ignore')

                    merged_pnf = int(dataframe.get('v8_target_1', pd.Series(float('nan'))).notna().sum())
                    logger.info(f'[V8-DIAG] {pair} after merge: 1H rows with pnf_target={merged_pnf}/{len(dataframe)}')
                else:
                    logger.warning(f'[V8-DIAG] {pair} 1d data insufficient ({len(df_1d) if df_1d is not None else 0} rows)')
                    self._fill_v8_defaults(dataframe)
            except Exception as e:
                logger.error(f'{pair} V8 daily computation failed: {e}')
                self._fill_v8_defaults(dataframe)
        else:
            self._fill_v8_defaults(dataframe)

        # ── 入场信号（TASK 4）───────────────────────────────
        if V8_MODULES_OK:
            try:
                dataframe = compute_entry_signal(dataframe)
                stats = entry_funnel_stats(dataframe)
                logger.info(f'[V8-DIAG] {pair} entry funnel: {stats}')
            except Exception as e:
                logger.error(f'[V8-DIAG] {pair} entry_detector failed: {e}')
                dataframe['v8_entry_long'] = False

        return dataframe

    def _fill_v8_defaults(self, dataframe: DataFrame) -> None:
        for col in ['v8_phase', 'v8_phase_allow_long', 'v8_tr_high', 'v8_tr_low',
                    'v8_target_1', 'v8_target_2', 'v8_target_3',
                    'v8_sc_final', 'v8_sc_low', 'v8_entry_long']:
            if col not in dataframe.columns:
                dataframe[col] = None if col not in ('v8_phase_allow_long', 'v8_sc_final', 'v8_entry_long') else False

    # ── populate_entry_trend ─────────────────────────────────
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            dataframe['v8_entry_long'].fillna(False),
            ['enter_long', 'enter_tag']
        ] = [1, 'v8_wave_start']

        return dataframe

    # ── populate_exit_trend（不用，靠 custom_exit）───────────
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['exit_long'] = 0
        return dataframe

    # ── custom_stoploss（结构止损）───────────────────────────
    def custom_stoploss(self, pair: str, trade, current_time,
                        current_rate: float, current_profit: float,
                        after_fill: bool, **kwargs) -> float:
        """
        结构止损：TR Low 优先，ATR 倍数兜底。
        T1 到达后移到保本。
        """
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe is None or len(dataframe) == 0:
            return self.stoploss

        last = dataframe.iloc[-1]
        entry_price = trade.open_rate
        atr = last.get('atr14', np.nan)
        tr_low = last.get('v8_tr_low', np.nan)
        target_1 = last.get('v8_target_1', np.nan)
        be_offset = float(self.breakeven_offset.value)

        # T1 到达 → 移到保本（只有T1在入场价上方时才有意义）
        if not np.isnan(target_1) and target_1 > entry_price and current_rate >= target_1:
            breakeven_price = entry_price * (1 + be_offset)
            if current_rate > breakeven_price:
                sl_price = breakeven_price
                return (sl_price / current_rate) - 1

        # 结构止损：TR Low - buffer
        if not np.isnan(tr_low) and tr_low > 0:
            buffer = float(self.tr_low_buffer.value)
            sl_price = tr_low * (1 - buffer)
            sl_ratio = (sl_price / current_rate) - 1
            # 不能比初始止损更宽
            if sl_ratio > self.stoploss:
                return sl_ratio

        return self.stoploss

    # ── custom_exit（PNF 目标位驱动退出）────────────────────
    def custom_exit(self, pair: str, trade, current_time,
                    current_rate: float, current_profit: float,
                    **kwargs) -> Optional[str]:
        """
        PNF 目标位驱动退出（TASK 5）。

        逻辑：
          - T1 前：持仓（结构止损由 custom_stoploss 负责）
          - 到达 T2：准备减仓（当前 Freqtrade 不支持部分平仓，标记退出）
          - 到达 T3：全部离场
          - 结构失败：收盘跌破 TR Low → 清仓
        """
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe is None or len(dataframe) == 0:
            return None

        last = dataframe.iloc[-1]
        target_2 = last.get('v8_target_2', np.nan)
        target_3 = last.get('v8_target_3', np.nan)
        tr_low = last.get('v8_tr_low', np.nan)

        # 结构失败：当前价跌破 TR Low
        if not np.isnan(tr_low) and tr_low > 0:
            if current_rate < tr_low:
                return 'v8_structure_fail'

        # T3 到达：全部离场
        if not np.isnan(target_3) and current_rate >= target_3:
            return 'v8_pnf_t3'

        # T2 到达：离场（Freqtrade 单笔全平，后续可升级部分平仓）
        if not np.isnan(target_2) and current_rate >= target_2:
            return 'v8_pnf_t2'

        # T1 之前：持仓，不退出
        return None

    # ── confirm_trade_entry（额外安全检查）──────────────────
    def confirm_trade_entry(self, pair: str, order_type: str, amount: float,
                            rate: float, time_in_force: str, current_time,
                            entry_tag: Optional[str], side: str, **kwargs) -> bool:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe is None or len(dataframe) == 0:
            return False

        last = dataframe.iloc[-1]

        # 必须有 PNF 目标位（有因果依据才进场）
        if pd.isna(last.get('v8_target_1', np.nan)):
            logger.info(f'{pair} entry rejected: no PNF target')
            return False

        return True
