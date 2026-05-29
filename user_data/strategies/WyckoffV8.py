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
    from user_data.strategies.v8_modules.pnf_calculator import add_v8_pnf_targets, add_v8_short_pnf_targets
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

# ── DailyEventDetector 导入（TASK S-1：派发序列 BC→AR→UTAD→SOW）────
try:
    from wyckoff.daily_event_detector import DailyEventDetector
    DAILY_EVENT_DETECTOR_OK = True
    logger.info('DailyEventDetector loaded')
except ImportError:
    DAILY_EVENT_DETECTOR_OK = False
    logger.warning('DailyEventDetector not available — short signals will be disabled')


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
    can_short = True

    # down_wave 通过 exit_signal 向量化路径退出（比 ROI/custom_exit 更早触发）
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # ROI：极高值，防止 config 覆盖干扰 custom_exit 和 exit_signal
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

        if DAILY_EVENT_DETECTOR_OK:
            self.daily_event_detector = DailyEventDetector()
        else:
            self.daily_event_detector = None

        # ── P5 事件桥接器 ────────────────────────────────────
        try:
            from user_data.strategy_event_bridge import StrategyEventBridge
            self.event_bridge = StrategyEventBridge(self, config)
        except Exception as e:
            self.event_bridge = None
            logger.warning(f'[V8] StrategyEventBridge init failed: {e}')

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
        dataframe['swing_low_24'] = dataframe['low'].rolling(48, min_periods=1).min()

        # ── ATH 距离（TASK 10 Phase E 警戒区）─────────────
        # expanding().max() 保证只用历史数据，无前瞻偏差
        dataframe['ath'] = dataframe['high'].expanding().max()
        dataframe['ath_ratio'] = (dataframe['close'] / dataframe['ath']).clip(upper=1.0)

        # ── Wave 检测（1H）──────────────────────────────────
        if self.wave_analyzer is not None:
            try:
                dataframe = self.wave_analyzer._segment_weis_waves(dataframe)
                # ZigZag 回溯修复：wave_start 标记在 bar N 确认，但实盘要到 N+min_bars 才知道
                # 把信号向后移 min_bars 根，消除 lookahead bias
                _lag = int(self.wave_min_bars.value)
                for _col in ['is_wave_start', 'is_wave_end']:
                    if _col in dataframe.columns:
                        dataframe[_col] = dataframe[_col].shift(_lag).fillna(False).astype(bool)
                # wave_direction / efficiency / displacement 跟随 is_wave_start 的原始位，不移位
                # （它们描述的是波段属性，不是触发信号）
                wave_count = int(dataframe.get('is_wave_start', pd.Series(False)).sum())
                up_count = int((dataframe.get('is_wave_start', pd.Series(False)) &
                                (dataframe.get('wave_direction', pd.Series('')) == 'up')).sum())
                logger.info(f'[V8-DIAG] {pair} WaveAnalyzer OK (lag={_lag}): is_wave_start={wave_count} up_waves={up_count}')
            except Exception as e:
                logger.warning(f'[V8-DIAG] {pair} WaveAnalyzer failed: {e}')
                dataframe['is_wave_start'] = False
                dataframe['wave_direction'] = ''
                dataframe['wave_efficiency'] = 0.0
                dataframe['wave_displacement'] = 0.0
        else:
            logger.warning(f'[V8-DIAG] {pair} WaveAnalyzer NOT loaded — wave_start=0')
            dataframe['is_wave_start'] = False
            dataframe['wave_direction'] = ''
            dataframe['wave_efficiency'] = 0.0
            dataframe['wave_displacement'] = 0.0

        # ── 日线 Phase / PNF（TASK 1-3）─────────────────────
        if V8_MODULES_OK:
            try:
                df_1d = self.dp.get_pair_dataframe(pair=pair, timeframe='1d')
                if df_1d is not None and len(df_1d) >= 100:
                    df_1d = add_v8_sc_detection(df_1d)
                    df_1d = add_v8_phase_detection(df_1d)
                    df_1d = add_v8_pnf_targets(df_1d)
                    df_1d = add_v8_short_pnf_targets(df_1d)  # TASK S-3

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
                        'target_short_conservative', 'target_short_standard',
                        'target_short_aggressive', 'rr_short_standard',
                    ]
                    daily_cols = [c for c in daily_cols if c in df_1d.columns]
                    df_1d_slim = df_1d[daily_cols].copy()
                    df_1d_slim['date'] = pd.to_datetime(df_1d_slim['date'], utc=True)
                    # 日线数据向后移 1 天：防止 lookahead bias
                    # 日线 bar 的 close/phase/PNF 在当天结束后才可知
                    # 必须等次日 1H bar 才能使用
                    df_1d_slim['date'] = df_1d_slim['date'] + pd.Timedelta(days=1)

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

                    # ── TASK S-1: 派发序列探测（BC→AR→UTAD→SOW）────────
                    if self.daily_event_detector is not None:
                        try:
                            df_1d = self.daily_event_detector.detect(df_1d)
                        except Exception as _ded_err:
                            logger.warning(f'[V8-DIAG] {pair} DailyEventDetector failed: {_ded_err}')
                            for _col in ['daily_bc_detected', 'daily_bc_path', 'daily_ar_high',
                                         'daily_ar_low', 'dist_sequence_progress',
                                         'daily_utad_detected', 'daily_dist_sow_detected',
                                         'buying_exhaustion', 'dist_cause_sufficient']:
                                df_1d[_col] = False if 'detected' in _col or 'exhaustion' in _col or 'sufficient' in _col else (float('nan') if 'high' in _col or 'low' in _col else 'none')
                    else:
                        df_1d['daily_bc_detected']       = False
                        df_1d['daily_bc_path']           = 'none'
                        df_1d['daily_ar_high']           = float('nan')
                        df_1d['daily_ar_low']            = float('nan')
                        df_1d['dist_sequence_progress']  = 'none'
                        df_1d['daily_utad_detected']     = False
                        df_1d['daily_dist_sow_detected'] = False
                        df_1d['buying_exhaustion']       = False
                        df_1d['dist_cause_sufficient']   = False

                    # 把派发序列字段追加进 merge slim
                    # ── TASK 13 Fix 1: sm_v2 — BC独立熊市结构判断（日线级别）──
                    # 条件：120日内最高点回撤 >= 20% AND close < EMA200*0.97 持续 5 根日线
                    _1d_ema200 = df_1d['close'].ewm(span=200, adjust=False).mean()
                    _1d_high_120d = df_1d['high'].rolling(120, min_periods=30).max()
                    _1d_drawdown = (_1d_high_120d - df_1d['close']) / _1d_high_120d.replace(0, float('nan'))
                    _sm_v2_raw = (_1d_drawdown >= 0.20) & (df_1d['close'] < _1d_ema200 * 0.97)
                    df_1d['daily_sm_v2'] = (
                        _sm_v2_raw.astype(int).rolling(5, min_periods=5).min().fillna(0).astype(bool)
                    )

                    # ── TASK 13 Fix 2: fallback SOW — sm_v2 激活时的结构性做空信号 ──
                    # 替代 ar_low 的锚点：使用 20 日滚动最低价（shift(1) 防未来函数）
                    _1d_vol_ma20 = df_1d['volume'].rolling(20, min_periods=10).mean()
                    _1d_low_20d = df_1d['low'].rolling(20, min_periods=10).min().shift(1)
                    _fallback_sow_raw = (
                        df_1d['daily_sm_v2'] &
                        (df_1d['close'] < df_1d['open']) &                              # 阴线
                        ((df_1d['open'] - df_1d['close']) / df_1d['open'].replace(0, float('nan')) >= 0.02) &  # 跌幅 >= 2%
                        (df_1d['volume'] > _1d_vol_ma20 * 1.5) &                       # 放量 1.5x
                        (df_1d['close'] < _1d_low_20d)                                  # 创近期 20 日新低
                    )
                    df_1d['daily_fallback_sow'] = _fallback_sow_raw.fillna(False)

                    _sm_v2_cnt = int(df_1d['daily_sm_v2'].sum())
                    _fb_sow_cnt = int(df_1d['daily_fallback_sow'].sum())
                    logger.info(f'[V8-DIAG] {pair} TASK13: daily_sm_v2={_sm_v2_cnt} daily_fallback_sow={_fb_sow_cnt}')

                    dist_cols = ['date', 'daily_bc_detected', 'daily_bc_path',
                                 'daily_ar_high', 'daily_ar_low', 'dist_sequence_progress',
                                 'daily_utad_detected', 'daily_dist_sow_detected',
                                 'buying_exhaustion', 'dist_cause_sufficient',
                                 'daily_sm_v2', 'daily_fallback_sow']
                    dist_cols = [c for c in dist_cols if c in df_1d.columns]
                    df_dist = df_1d[dist_cols].copy()
                    df_dist['date'] = pd.to_datetime(df_dist['date'], utc=True) + pd.Timedelta(days=1)
                    dataframe['date_utc2'] = pd.to_datetime(dataframe['date'], utc=True)
                    dataframe = pd.merge_asof(
                        dataframe.sort_values('date_utc2'),
                        df_dist.rename(columns={'date': 'date_dist'}).sort_values('date_dist'),
                        left_on='date_utc2', right_on='date_dist',
                        direction='backward',
                    )
                    dataframe = dataframe.drop(columns=['date_utc2', 'date_dist'], errors='ignore')

                    _bc_cnt  = int(dataframe.get('daily_bc_detected', pd.Series(False)).sum())
                    _dcs_cnt = int(dataframe.get('dist_cause_sufficient', pd.Series(False)).sum())
                    logger.info(f'[V8-DIAG] {pair} dist_seq: bc_bars={_bc_cnt} dist_cause_sufficient_bars={_dcs_cnt}')

                    # ── TASK S-2: bear_market_mode + sustained_markdown ──
                    # daily_choch_confirmed：复用 v8_sc_final 近似（SC 确认 = 结构改变）
                    # 派发侧：用 daily_bc_detected 触发 choch
                    _daily_choch = dataframe.get(
                        'daily_bc_detected', pd.Series(False, index=dataframe.index)
                    ).fillna(False).astype(bool)
                    _dist_cause = dataframe.get(
                        'dist_cause_sufficient', pd.Series(False, index=dataframe.index)
                    ).fillna(False).astype(bool)
                    dataframe['bear_market_mode'] = _daily_choch & _dist_cause

                    # sustained_markdown：choch + close < daily_ema200×0.97 持续 30 根 1H
                    _daily_ema200 = dataframe.get('v8_tr_low', pd.Series(float('nan'), index=dataframe.index))
                    # 用 EMA200 估算：V8 无独立 daily_ema200，用 close 的 EMA200 代替
                    _ema200_1h = dataframe['close'].ewm(span=200, adjust=False).mean()
                    _sm_raw = (
                        _daily_choch &
                        (dataframe['close'] < _ema200_1h * 0.97)
                    )
                    dataframe['sustained_markdown'] = (
                        _sm_raw.astype(int).rolling(30, min_periods=30).min().fillna(0).astype(bool)
                    )
                    dataframe['_short_bear_gate'] = (
                        dataframe['bear_market_mode'] | dataframe['sustained_markdown']
                    )

                    _bm = int(dataframe['bear_market_mode'].sum())
                    _sm = int(dataframe['sustained_markdown'].sum())
                    logger.info(f'[V8-DIAG] {pair} S-2: bear_market_mode={_bm} sustained_markdown={_sm}')

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

        # ── P5 字段适配：把 V8 专有列映射到 P5 期望的标准列名 ──
        dataframe['phase_context_tag'] = dataframe.get('v8_phase', pd.Series('unknown', index=dataframe.index)).fillna('unknown')
        dataframe['daily_tr_exists'] = dataframe.get('v8_tr_low', pd.Series(np.nan, index=dataframe.index)).notna()
        # daily_choch_confirmed：Phase E = 积累结构的空头方向 ChoCh（上涨趋势延续中），
        # 不等同于分发 ChoCh。用 phase in D/E 作为"结构已确认趋势方向"的代理指标。
        _v8_phase = dataframe.get('v8_phase', pd.Series('unknown', index=dataframe.index)).fillna('unknown')
        dataframe['daily_choch_confirmed'] = _v8_phase.isin(['D', 'E'])
        # daily_sm_v2：TASK 13 BC 独立熊市闸门（直接透传）
        dataframe['daily_sm_v2'] = dataframe.get('daily_sm_v2', pd.Series(False, index=dataframe.index)).fillna(False)
        # macro_phase：V8 phase A-E 直接映射（V7 markup/markdown 概念在 V8 里用 phase 表达）
        dataframe['macro_phase'] = _v8_phase
        # market_env：基于 bear_market_mode + phase 推导字符串环境标签
        _bear_mode = dataframe.get('bear_market_mode', pd.Series(False, index=dataframe.index)).fillna(False).astype(bool)
        dataframe['market_env'] = np.where(_bear_mode, 'bearish',
                                  np.where(_v8_phase.isin(['D', 'E']), 'markup', 'accumulation'))
        # daily_gate_score：V8 无 TR gate score；用 dist_cause_sufficient 作代理（1.0=因果充足）
        dataframe['daily_gate_score'] = dataframe.get(
            'dist_cause_sufficient', pd.Series(False, index=dataframe.index)
        ).fillna(False).astype(float)

        # ── P5 事件发射 ──────────────────────────────────────
        if self.event_bridge is not None:
            self.event_bridge.emit_detector_events_from_dataframe(
                pair=pair, timeframe=self.timeframe, dataframe=dataframe)
            self.event_bridge.emit_regime_snapshot(
                pair=pair, timeframe=self.timeframe, dataframe=dataframe)

        return dataframe

    def _fill_v8_defaults(self, dataframe: DataFrame) -> None:
        bool_cols = {'v8_phase_allow_long', 'v8_sc_final', 'v8_entry_long',
                     'daily_bc_detected', 'daily_utad_detected', 'daily_dist_sow_detected',
                     'buying_exhaustion', 'dist_cause_sufficient',
                     'bear_market_mode', 'sustained_markdown', '_short_bear_gate',
                     'daily_sm_v2', 'daily_fallback_sow'}
        float_cols = {'v8_tr_high', 'v8_tr_low', 'v8_target_1', 'v8_target_2', 'v8_target_3',
                      'v8_sc_low', 'daily_ar_high', 'daily_ar_low',
                      'target_short_conservative', 'target_short_standard', 'rr_short_standard'}
        str_cols   = {'v8_phase', 'daily_bc_path', 'dist_sequence_progress'}
        all_cols = bool_cols | float_cols | str_cols
        for col in all_cols:
            if col not in dataframe.columns:
                if col in bool_cols:
                    dataframe[col] = False
                elif col in float_cols:
                    dataframe[col] = np.nan
                else:
                    dataframe[col] = 'unknown'

    # ── populate_entry_trend ─────────────────────────────────
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        entry_mask = dataframe['v8_entry_long'].fillna(False)
        dataframe.loc[entry_mask, 'enter_long'] = 1
        v8_phase = dataframe.get('v8_phase', pd.Series('unknown', index=dataframe.index)).fillna('unknown')
        dataframe.loc[entry_mask, 'enter_tag'] = ('v8_ws_' + v8_phase.str.lower()).where(entry_mask, '')

        # 计算 T1/T2 相对位置，供 P5 信号记录和周报统计使用
        t1 = dataframe.get('v8_target_1', pd.Series(np.nan, index=dataframe.index))
        t2 = dataframe.get('v8_target_2', pd.Series(np.nan, index=dataframe.index))
        dataframe['v8_above_t1'] = dataframe['close'] > t1.fillna(np.inf)
        dataframe['v8_t2_exists'] = t2.notna()

        # ── TASK S-4: enter_short ───────────────────────────────
        # 触发条件（三路径）：
        #   路径1 SOW：daily_dist_sow_detected（经典 BC→AR→SOW 或 ar→sow 直通）
        #   路径2 UTAD：daily_utad_detected（UTAD 确认后提前入场）
        #   路径3 BE：buying_exhaustion（渐进式分配顶，无单根 BC，2022型）
        dataframe['enter_short'] = 0

        sow_entry  = (
            dataframe.get('daily_dist_sow_detected',
                          pd.Series(False, index=dataframe.index)).fillna(False).astype(bool) |
            dataframe.get('daily_fallback_sow',                              # TASK 13: BC独立 fallback SOW
                          pd.Series(False, index=dataframe.index)).fillna(False).astype(bool)
        )
        utad_entry = dataframe.get('daily_utad_detected',
                                   pd.Series(False, index=dataframe.index)).fillna(False).astype(bool)
        be_entry   = dataframe.get('buying_exhaustion',
                                   pd.Series(False, index=dataframe.index)).fillna(False).astype(bool)

        composite_intent = dataframe.get('composite_intent',
                                         pd.Series('unclear', index=dataframe.index))
        short_intent_ok = ~composite_intent.isin(['accumulating', 'trending_up', 'testing'])

        rr_short_ok   = dataframe.get('rr_short_standard',
                                      pd.Series(0.0, index=dataframe.index)).fillna(0.0) >= 1.0
        _bear_gate    = (
            dataframe.get('dist_cause_sufficient',
                          pd.Series(False, index=dataframe.index)).fillna(False).astype(bool) |
            dataframe.get('sustained_markdown',
                          pd.Series(False, index=dataframe.index)).fillna(False).astype(bool) |
            dataframe.get('daily_sm_v2',                                      # TASK 13: BC独立熊市闸门
                          pd.Series(False, index=dataframe.index)).fillna(False).astype(bool)
        )
        pnf_valid_short = dataframe.get('target_short_conservative',
                                        pd.Series(0.0, index=dataframe.index)).fillna(0.0) > 0

        # BE 路径：不要求 RR（无 BC 锚点时 RR 计算可能偏低），用 _bear_gate 门控
        be_gate = (
            be_entry & _bear_gate & pnf_valid_short & short_intent_ok
        )

        final_short_entry = (
            (sow_entry | utad_entry) &
            short_intent_ok & rr_short_ok & _bear_gate & pnf_valid_short
        ) | be_gate

        # 优先级：UTAD > SOW > BE（UTAD/SOW 是更强的结构确认）
        dataframe.loc[final_short_entry & be_entry,   'enter_tag'] = 'v8_short_be'
        dataframe.loc[final_short_entry & sow_entry,  'enter_tag'] = 'v8_short_sow'
        dataframe.loc[final_short_entry & utad_entry, 'enter_tag'] = 'v8_short_utad'
        dataframe.loc[final_short_entry, 'enter_short'] = 1

        _be_cnt  = int(be_gate.sum())
        _sow_cnt = int((final_short_entry & sow_entry).sum())
        _pair = metadata.get('pair', '')
        logger.info(f'[V8-DIAG] {_pair} enter_short: sow={_sow_cnt} be={_be_cnt}')

        # P5：发射信号事件
        if self.event_bridge is not None:
            self.event_bridge.emit_signals_from_dataframe(dataframe, metadata)

        return dataframe

    # ── populate_exit_trend（down_wave 主退出，向量化路径）──────
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['exit_long']  = 0
        dataframe['exit_short'] = 0

        # rolling(4) 捕获最近4根K线内的 wave_start（与V7一致，避免实盘最后一根K线漏判）
        _recent_wave_start = dataframe['is_wave_start'].rolling(4, min_periods=1).max().astype(bool)

        is_down_wave = (
            _recent_wave_start &
            (dataframe['wave_direction'] == 'down') &
            (dataframe['wave_efficiency'] > 0.001) &
            (dataframe['wave_displacement'] > dataframe['close'] * 0.008)
        )
        dataframe.loc[is_down_wave, ['exit_long', 'exit_tag']] = [1, 'v8_down_wave']

        # ── TASK S-5: exit_short — 上升 wave_start 出现 → 空头平仓 ──
        # 物理含义：up_wave ChoCh = 供应消耗完毕，空头逻辑失效，必须平仓
        is_up_wave = (
            _recent_wave_start &
            (dataframe['wave_direction'] == 'up') &
            (dataframe['wave_efficiency'] > 0.001) &
            (dataframe['wave_displacement'] > dataframe['close'] * 0.008)
        )
        dataframe.loc[is_up_wave, ['exit_short', 'exit_tag']] = [1, 'v8_up_wave_reverse']

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
        be_offset = float(self.breakeven_offset.value)

        # ── TASK S-6: 空头止损分支 ──────────────────────────────
        if trade.is_short:
            # 空头结构止损用派发侧 AR 高点；BE 路径无状态机锚点时 fallback 到近期 20 日高点
            ar_high = last.get('daily_ar_high', np.nan)
            if np.isnan(ar_high) or ar_high <= 0:
                ar_high = float(dataframe['high'].tail(20).max())
            target_short_cons = last.get('target_short_conservative', np.nan)

            # T1 (conservative) 到达 → 移到保本（空头：入场价 × (1 - offset)）
            if (not np.isnan(target_short_cons) and target_short_cons > 0
                    and target_short_cons < entry_price
                    and current_rate <= target_short_cons):
                breakeven_price = entry_price * (1 - be_offset)
                if current_rate < breakeven_price:
                    return (entry_price * (1 + be_offset) / current_rate) - 1

            # 结构止损：AR High + buffer（价格突破派发区顶部 = 结构失败）
            if not np.isnan(ar_high) and ar_high > 0:
                buffer = float(self.tr_low_buffer.value)
                sl_price = ar_high * (1 + buffer)
                return (sl_price / current_rate) - 1

            return self.stoploss

        # ── 多头止损分支 ─────────────────────────────────────────
        enter_tag = getattr(trade, 'enter_tag', '') or ''
        is_phase_e_regime = enter_tag in ('v8_ws_e', 'v8_ws_unknown')

        tr_low = last.get('v8_tr_low', np.nan)
        target_1 = last.get('v8_target_1', np.nan)

        # T1 到达 → 移到保本（Phase D/E 均适用，只有T1在入场价上方时才有意义）
        if not np.isnan(target_1) and target_1 > entry_price and current_rate >= target_1:
            breakeven_price = entry_price * (1 + be_offset)
            if current_rate > breakeven_price:
                return (breakeven_price / current_rate) - 1

        # Phase D/E/unknown 统一：TR Low 宏观锚点（"大因"止损）
        # 审计结论：Phase E 用宽止损让 v8_down_wave 主导退出，复利链完整性最优
        if not np.isnan(tr_low) and tr_low > 0:
            buffer = float(self.tr_low_buffer.value)
            sl_price = tr_low * (1 - buffer)
            return (sl_price / current_rate) - 1
        return self.stoploss

    # ── custom_exit（PNF 目标位驱动退出）────────────────────
    def custom_exit(self, pair: str, trade, current_time,
                    current_rate: float, current_profit: float,
                    **kwargs) -> Optional[str]:
        """
        1H 波浪驱动退出 + 日线结构兜底。

        优先级：
          1. 结构失败：当前价跌破 TR Low（日线结构破坏，紧急出场）
          2. 1H down_wave 开始：与入场 up_wave 对称，主要退出信号
          3. PNF T2 到达：利润目标确认退出（辅助）
        """
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe is None or len(dataframe) == 0:
            return None

        last = dataframe.iloc[-1]
        entry_price = trade.open_rate

        # ── TASK S-6: 空头退出分支 ──────────────────────────────
        if trade.is_short:
            # 空头结构失败用派发侧 AR 高点；BE 路径无状态机锚点时 fallback 到近期 20 日高点
            ar_high = last.get('daily_ar_high', np.nan)
            if np.isnan(ar_high) or ar_high <= 0:
                ar_high = float(dataframe['high'].tail(20).max())
            target_short_std = last.get('target_short_standard', np.nan)

            # 1. 结构失败：价格突破派发区 AR High（做空逻辑彻底失效）
            if not np.isnan(ar_high) and ar_high > 0:
                if current_rate > ar_high:
                    return 'v8_short_structure_fail'

            if (not np.isnan(target_short_std) and target_short_std > 0
                    and target_short_std < entry_price
                    and current_rate <= target_short_std):
                return 'v8_short_pnf_std'

            return None

        # ── 多头退出分支（原逻辑）───────────────────────────────
        tr_low = last.get('v8_tr_low', np.nan)
        target_2 = last.get('v8_target_2', np.nan)

        # 1. 结构失败：当前价跌破 TR Low（日线结构破坏，紧急退出）
        if not np.isnan(tr_low) and tr_low > 0:
            if current_rate < tr_low:
                return 'v8_structure_fail'

        if not np.isnan(target_2) and target_2 > entry_price and current_rate >= target_2:
            return 'v8_pnf_t2'

        return None

    def _emit_trade_close(self, trade, pair: str, reason: str, exit_price: float,
                          current_time, **extra) -> None:
        if self.event_bridge is None:
            return
        try:
            duration_min = int((current_time - trade.open_date_utc).total_seconds() / 60)
            profit_pct = (exit_price - trade.open_rate) / trade.open_rate
            self.event_bridge.emit_trade_close(
                pair=pair,
                trade_id=str(trade.id),
                side='long',
                exit_reason=reason,
                exit_tag=reason,
                exit_price=exit_price,
                profit_pct=profit_pct,
                profit_abs=profit_pct * trade.stake_amount,
                duration_minutes=duration_min,
                current_time=current_time,
                **extra,
            )
        except Exception as e:
            logger.warning(f'[V8] P5 trade_close emit failed: {e}')

    # ── confirm_trade_exit（捕获向量化退出的 P5 close 事件）──
    def confirm_trade_exit(self, pair: str, trade, order_type: str, amount: float,
                           rate: float, time_in_force: str, exit_reason: str,
                           current_time, **kwargs) -> bool:
        if exit_reason and self.event_bridge is not None:
            # 滑点审计：取信号触发 K 线（当前已分析 df 最后一根）的收盘价
            signal_bar_close = None
            t1_price = None
            t1_triggered = None
            try:
                df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
                if df is not None and len(df) > 0:
                    last = df.iloc[-1]
                    signal_bar_close = float(last['close'])
                    t1_price = float(last.get('v8_target_1', float('nan')))
                    if np.isnan(t1_price):
                        t1_price = None
                    # T1 是否触发：当前 fill 价或信号价 >= T1
                    if t1_price is not None:
                        t1_triggered = 1.0 if (rate >= t1_price or signal_bar_close >= t1_price) else 0.0
            except Exception:
                pass
            self._emit_trade_close(
                trade, pair, exit_reason, rate, current_time,
                signal_bar_close=signal_bar_close,
                t1_price=t1_price,
                t1_triggered=t1_triggered,
            )
        return True

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

        # P5：发射开仓事件
        if self.event_bridge is not None:
            trade_id = f"{pair}_{current_time.strftime('%Y%m%d%H%M%S')}"
            self.event_bridge.emit_trade_open(
                pair=pair,
                trade_id=trade_id,
                side=side,
                enter_tag=entry_tag or 'v8_wave_start',
                entry_price=rate,
                stake_amount=amount,
                leverage=1.0,
                current_time=current_time,
                dataframe=dataframe,
            )

        return True
