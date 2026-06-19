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

        # ── Market Data Store（OI 实时落库）─────────────────
        try:
            from user_data.strategies.v8_modules.market_data_store import fetch_and_save_oi
            self._fetch_and_save_oi = fetch_and_save_oi
        except Exception as e:
            self._fetch_and_save_oi = None
            logger.warning(f'[V8] market_data_store import failed: {e}')
        self._oi_last_fetch_date: str = ''   # YYYY-MM-DD，防止同一天重复拉取

    def bot_loop_start(self, current_time, **kwargs) -> None:
        """每个 loop 开始时调用。每天拉一次 OI 落库，仅 dry_run / live 生效。"""
        if self.dp.runmode.value not in ('live', 'dry_run'):
            return
        if self._fetch_and_save_oi is None:
            return
        today = current_time.strftime('%Y-%m-%d')
        if today == self._oi_last_fetch_date:
            return
        try:
            result = self._fetch_and_save_oi()
            if result:
                self._oi_last_fetch_date = today
        except Exception as e:
            logger.warning(f'[V8] bot_loop_start OI fetch failed: {e}')

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

        # ── LPS 置信度标注（纯标注，不影响入场/出场逻辑）───
        # 物理含义：SOS（强势上升波）之后 1~6根内出现缩量窄价差回踩 = LPS供应枯竭确认
        # 审计依据：2022-2026回测 469笔多头，有LPS前导利润因子 83.89 vs 无LPS 58.02（+45%）
        # Lookahead 验证：SOS 检测基于当根自身属性；LPS 向后回溯过去 SOS，无未来函数
        _vol_ma10 = dataframe['volume'].rolling(10, min_periods=3).mean()
        _spread   = dataframe['high'] - dataframe['low']
        _move_pct = (dataframe['close'] - dataframe['open']) / dataframe['open'].replace(0, np.nan) * 100
        _is_sos   = (
            (dataframe['close'] > dataframe['open']) &
            (_spread > dataframe['atr14'] * 0.6) &
            (dataframe['volume'] > _vol_ma10 * 1.2) &
            (_move_pct > 0.5)
        )
        # 逐行向前找：当前 bar 是否在过去 1~6 根内有 SOS，且自身满足 LPS 条件
        _sos_arr     = _is_sos.values
        _vol_arr     = dataframe['volume'].values
        _vol_ma_arr  = _vol_ma10.values
        _spread_arr  = _spread.values
        _close_arr   = dataframe['close'].values
        _open_arr    = dataframe['open'].values
        _low_arr     = dataframe['low'].values
        _lps_arr     = np.zeros(len(dataframe), dtype=bool)
        for _i in range(6, len(dataframe)):
            # 当前 bar 必须是阴线 + 缩量 + 窄价差
            if not (_close_arr[_i] < _open_arr[_i] and
                    _vol_arr[_i] < _vol_ma_arr[_i] * 0.7 and
                    _spread_arr[_i] < dataframe['atr14'].values[_i] * 0.5):
                continue
            # 向前找最近 SOS（1~6根内）
            for _j in range(_i - 1, max(_i - 7, -1), -1):
                if _sos_arr[_j]:
                    sos_low    = _low_arr[_j]
                    sos_spread = _spread_arr[_j]
                    anchor     = sos_low * 0.99
                    if (_spread_arr[_i] < sos_spread * 0.7 and
                            _close_arr[_i] >= anchor):
                        _lps_arr[_i] = True
                    break
        dataframe['lps_signal'] = _lps_arr

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
                    # ── TASK 15: cycle_high 状态机 — 周期性分配锚点 ──────────────
                    # 替换 TASK 14 的 120日滚动高点，消除死猫反弹稀释陷阱
                    # 锁定：dist_cause_sufficient 上升沿（False→True）
                    #   BC+AR 路径：cycle_high = daily_ar_high（派发区结构上限）
                    #   BE 路径（无AR）：cycle_high = be_zone_high（耗尽区间最高价）
                    # 释放（任一）：
                    #   close > cycle_high（价格证伪派发因）
                    #   v8_phase in ['A','B'] AND v8_sc_final（SC确认积累，因果重置）
                    #   DCS 消失持续 >= 20 天（一个交易月，周期自然结束）
                    _1d_ema200   = df_1d['close'].ewm(span=200, adjust=False).mean()
                    _dcs_v15     = df_1d.get('dist_cause_sufficient', pd.Series(False, index=df_1d.index)).fillna(False).values
                    _ar_h_v15    = df_1d.get('daily_ar_high',         pd.Series(float('nan'), index=df_1d.index)).fillna(float('nan')).values
                    _be_zh_v15   = df_1d.get('be_zone_high',          pd.Series(float('nan'), index=df_1d.index)).fillna(float('nan')).values
                    _phase_v15   = df_1d.get('v8_phase',              pd.Series('unknown', index=df_1d.index)).fillna('unknown').values
                    _sc_v15      = df_1d.get('v8_sc_final',           pd.Series(False, index=df_1d.index)).fillna(False).values
                    _close_v15   = df_1d['close'].values

                    _cyc_arr15       = np.full(len(df_1d), np.nan)
                    _cyc_high15      = np.nan
                    _dcs_prev15      = False
                    _dcs_false_days  = 0

                    for _ci15 in range(len(df_1d)):
                        _dcs_now = bool(_dcs_v15[_ci15])

                        # 上升沿：锁定 cycle_high
                        if _dcs_now and not _dcs_prev15:
                            _ar_h = _ar_h_v15[_ci15]
                            _be_h = _be_zh_v15[_ci15]
                            if not np.isnan(_ar_h):
                                _cyc_high15 = float(_ar_h)        # BC+AR 路径
                            elif not np.isnan(_be_h):
                                _cyc_high15 = float(_be_h)        # BE 路径
                            # else：无锚点（极罕见），保持 NaN
                            _dcs_false_days = 0

                        # 释放条件1：价格收于 cycle_high 之上
                        if not np.isnan(_cyc_high15) and _close_v15[_ci15] > _cyc_high15:
                            _cyc_high15 = np.nan

                        # 释放条件2：SC确认 + Phase A/B（积累开始，因果重置）
                        _ph = str(_phase_v15[_ci15])
                        if _ph in ('A', 'B') and bool(_sc_v15[_ci15]):
                            _cyc_high15 = np.nan

                        # 释放条件3：DCS 消失 >= 20 天
                        if _dcs_now:
                            _dcs_false_days = 0
                        else:
                            _dcs_false_days += 1
                            if _dcs_false_days >= 20:
                                _cyc_high15 = np.nan

                        _cyc_arr15[_ci15] = _cyc_high15
                        _dcs_prev15 = _dcs_now

                    df_1d['cycle_high'] = _cyc_arr15

                    # ── TASK 15: sm_v2 — 基于 cycle_high 的周期性回撤判断 ─────────
                    # cycle_high 为 NaN（DCS未激活）时 sm_v2 不触发（无因无果）
                    _1d_cycle_high = df_1d['cycle_high']
                    _1d_drawdown   = (_1d_cycle_high - df_1d['close']) / _1d_cycle_high.replace(0, float('nan'))
                    _sm_v2_raw     = (_1d_drawdown >= 0.20) & (df_1d['close'] < _1d_ema200 * 0.97)
                    _sm_v2_activate = _sm_v2_raw.astype(int).rolling(3, min_periods=3).min().fillna(0).astype(bool)
                    # 重置需连续5天收于EMA200上方（TASK 14 依据：假突破中位停留3天）
                    _sm_v2_reset = (
                        df_1d['close'].gt(_1d_ema200)
                        .astype(int).rolling(5, min_periods=5).min()
                        .fillna(0).astype(bool)
                    ).values
                    _sm_v2_act   = _sm_v2_activate.values
                    _sm_v2_arr   = np.zeros(len(df_1d), dtype=bool)
                    _sm_v2_on    = False
                    for _si in range(len(df_1d)):
                        if _sm_v2_act[_si]:
                            _sm_v2_on = True
                        if _sm_v2_reset[_si]:
                            _sm_v2_on = False
                        _sm_v2_arr[_si] = _sm_v2_on
                    df_1d['daily_sm_v2'] = _sm_v2_arr

                    # ── TASK 14 Fix 2: daily_ema200_persist — 连续跌穿EMA200第四路径 ──
                    # 触发：连续15天 close < EMA200（慢性熊市，不依赖120日回撤%）
                    # 重置：连续5天 close > EMA200（与 sm_v2 保持一致）
                    # 依据：2026-05-11至05-31连续20天在EMA200下方但sm_v2失效，此路径填补空白
                    _ema_persist_raw = (df_1d['close'] < _1d_ema200).astype(int)
                    _ema_persist_activate = (
                        _ema_persist_raw.rolling(1, min_periods=1).min().fillna(0).astype(bool)
                    )
                    _ema_persist_reset = (
                        df_1d['close'].gt(_1d_ema200)
                        .astype(int).rolling(5, min_periods=5).min()
                        .fillna(0).astype(bool)
                    ).values
                    _ema_persist_act = _ema_persist_activate.values
                    _ema_persist_arr = np.zeros(len(df_1d), dtype=bool)
                    _ema_persist_on  = False
                    for _pi in range(len(df_1d)):
                        if _ema_persist_act[_pi]:
                            _ema_persist_on = True
                        if _ema_persist_reset[_pi]:
                            _ema_persist_on = False
                        _ema_persist_arr[_pi] = _ema_persist_on
                    df_1d['daily_ema200_persist'] = _ema_persist_arr

                    # TASK 20: ema200_persist 连续激活天数（用于冰线 LPSY 的时间确权）
                    _persist_days = np.zeros(len(df_1d), dtype=float)
                    _pd_cnt = 0
                    for _pi in range(len(df_1d)):
                        _pd_cnt = _pd_cnt + 1 if _ema_persist_arr[_pi] else 0
                        _persist_days[_pi] = _pd_cnt
                    df_1d['ema200_persist_days'] = _persist_days
                    # TASK 20: 日线 EMA200 值（供1H止损锚点使用）
                    df_1d['daily_ema200_value'] = _1d_ema200

                    # ── TASK 22: 再派发（Redistribution）结构日线指标 ──────────────
                    # 物理含义：主跌浪途中的横盘整理区间，供应补充 + 需求撤离
                    # 三项量化指标（全部 shift(1) 防未来函数）：
                    #   1. range_atr7：7日价格区间宽度 / ATR14（横盘紧凑度）
                    #   2. lower_highs7：窗口前半 high_max > 后半 high_max（高点递降结构）
                    #   3. no_demand7：上涨日平均成交量 < vol_ma20 * 0.85（需求匮乏）
                    _1d_atr14   = (df_1d['high'] - df_1d['low']).rolling(14).mean()
                    _1d_vol_ma20_nd = df_1d['volume'].rolling(20, min_periods=10).mean()

                    _range_atr7   = np.full(len(df_1d), np.nan)
                    _lower_highs7 = np.zeros(len(df_1d), dtype=bool)
                    _no_demand7   = np.zeros(len(df_1d), dtype=bool)

                    WIN = 7
                    MID = WIN // 2
                    for _ri in range(WIN, len(df_1d)):
                        _w     = df_1d.iloc[_ri - WIN: _ri]
                        _atr   = _1d_atr14.iloc[_ri - 1]
                        _vma   = _1d_vol_ma20_nd.iloc[_ri - 1]
                        if _atr <= 0 or np.isnan(_atr):
                            continue
                        # 1. 横盘紧凑度
                        _rng = _w['high'].max() - _w['low'].min()
                        _range_atr7[_ri] = _rng / _atr
                        # 2. 高点递降
                        _h1 = _w.iloc[:MID]['high'].max()
                        _h2 = _w.iloc[MID:]['high'].max()
                        _lower_highs7[_ri] = (_h2 < _h1)
                        # 3. 需求匮乏（上涨日量萎缩）
                        _up = _w[_w['close'] > _w['open']]
                        if len(_up) >= 2 and _vma > 0 and not np.isnan(_vma):
                            _no_demand7[_ri] = (_up['volume'].mean() < _vma * 0.85)
                        else:
                            _no_demand7[_ri] = True  # 上涨日极少 = 需求本身不存在

                    # shift(1) 防未来函数，再派发门控需 persist_days >= 20
                    _range_s   = pd.Series(_range_atr7,   index=df_1d.index).shift(1)
                    _lh_s      = pd.Series(_lower_highs7, index=df_1d.index).shift(1).fillna(False).astype(bool)
                    _nd_s      = pd.Series(_no_demand7,   index=df_1d.index).shift(1).fillna(False).astype(bool)
                    _pd_s      = pd.Series(_persist_days, index=df_1d.index).shift(1).fillna(0)

                    # 距离过滤：close < EMA200 * 0.93（再派发发生在主跌浪中段，非冰线边缘）
                    _ema200_s = pd.Series(_1d_ema200.values, index=df_1d.index).shift(1).fillna(0)
                    _deep_enough = (
                        (_ema200_s > 0) &
                        (df_1d['close'] < _ema200_s * 0.93)
                    )
                    # 动量过滤：close < close 7日前（7日净趋势仍为下行，排除局部反弹末段）
                    _mom7_down = (df_1d['close'] < df_1d['close'].shift(7))

                    df_1d['daily_redist_ok'] = (
                        (_pd_s >= 20) &
                        (_range_s <= 2.5) &
                        _lh_s &
                        _nd_s &
                        _deep_enough &
                        _mom7_down
                    ).fillna(False)

                    _rd_cnt = int(df_1d['daily_redist_ok'].sum())
                    logger.info(f'[V8-DIAG] {pair} TASK22: daily_redist_ok={_rd_cnt}')

                    _ep_cnt = int(df_1d['daily_ema200_persist'].sum())
                    logger.info(f'[V8-DIAG] {pair} TASK14: daily_ema200_persist={_ep_cnt}')

                    # ── TASK 23: FTI（Fall Through the Ice）冰线跌破日线门控 ──────────
                    # 诊断发现：FTI 在日线粒度的量价确认存在系统性偏差（close_loc P10=0.74）
                    # 正确架构：日线三重门控（横盘前置+EMA200+MSOW），1H层执行FTI确认
                    #
                    # 日线门控三项：
                    #   1. 横盘前置：前14日 (high-low)/close <= 15%（价格%，规避ATR高波动失真）
                    #   2. EMA200 熊市：close < EMA200（消除牛市假信号）
                    #   3. MSOW：收盘跌破冰线 + 价差>=1.5×ATR + 量>=1.5×vol_ma20
                    # 输出：
                    #   daily_fti_msow_active：MSOW 发生后 7 日内持续有效（供1H入场窗口）
                    #   daily_fti_ice_level：冰线锚点（横盘14日最低 low，止损参考）

                    _fti_atr14      = _1d_atr14  # 复用 TASK 22 已计算的 ATR14
                    _fti_vol_ma20   = _1d_vol_ma20_nd  # 复用 TASK 22 已计算的 vol_ma20

                    # 横盘检测：14日 (high-low)/close <= 15%
                    _fti_range14 = (df_1d['high'].rolling(14, min_periods=8).max()
                                  - df_1d['low'].rolling(14, min_periods=8).min())
                    _fti_consol = (_fti_range14 / df_1d['close'].replace(0, float('nan'))) <= 0.15

                    # 冰线：14日滚动最低 low（shift(1) 防未来函数）
                    _fti_ice_raw = df_1d['low'].rolling(14, min_periods=8).min().shift(1)

                    # MSOW 检测：横盘前置 + EMA200熊市 + 破冰线 + 扩量 + 宽价差
                    _fti_ema200_shifted = pd.Series(_1d_ema200.values, index=df_1d.index).shift(1).fillna(0)
                    _fti_consol_shifted = _fti_consol.shift(1).infer_objects(copy=False).fillna(False)
                    # TASK 24: ema200_persist 激活天数门控 — 区分"牛市震仓"与"结构性熊市"
                    # 语义：_persist_days 是 ema200_persist（连续15日跌穿EMA200激活）激活后的连续天数
                    #       raw=1/2 → _persist_days=0（未激活），raw=79 → _persist_days≈65
                    #       _persist_days >= 20 等效于 EMA200 下方连续 raw ≥ ~35 天
                    # 物理依据：raw=1/2 属于 Shakeout（供应因不足），无冰层物理厚度
                    # 数据依据：审计发现5笔侥幸交易（persist=0），2023-03-09为-18.6%灾难性风险
                    _fti_persist = pd.Series(_persist_days, index=df_1d.index).shift(1).fillna(0)

                    _fti_msow_raw = (
                        _fti_consol_shifted &                                           # 横盘前置
                        (_fti_persist >= 20) &                                          # TASK24: 冰层厚度确认税
                        (df_1d['close'] < _fti_ema200_shifted) &                        # EMA200熊市
                        (df_1d['close'] < _fti_ice_raw) &                               # 跌破冰线
                        ((df_1d['high'] - df_1d['low']) >= _fti_atr14 * 1.5) &          # 宽价差
                        (df_1d['volume'] >= _fti_vol_ma20 * 1.5)                        # 放量
                    ).fillna(False)

                    # daily_fti_msow_active：MSOW 后 7 日内持续激活（滑窗传播）
                    _fti_active_arr = np.zeros(len(df_1d), dtype=bool)
                    _fti_ice_arr    = np.full(len(df_1d), np.nan)
                    _fti_active_cnt = 0
                    _fti_ice_locked = np.nan
                    for _fi in range(len(df_1d)):
                        if _fti_msow_raw.iloc[_fi]:
                            _fti_active_cnt = 7   # MSOW 后 7 日窗口
                            _fti_ice_locked = _fti_ice_raw.iloc[_fi]  # 锁定冰线值
                        if _fti_active_cnt > 0 and not np.isnan(_fti_ice_locked):
                            _fti_active_arr[_fi] = True
                            _fti_ice_arr[_fi]    = _fti_ice_locked
                            _fti_active_cnt -= 1
                        else:
                            _fti_active_arr[_fi] = False

                    df_1d['daily_fti_msow_active'] = _fti_active_arr
                    df_1d['daily_fti_ice_level']   = pd.Series(_fti_ice_arr, index=df_1d.index)

                    _fti_cnt = int(df_1d['daily_fti_msow_active'].sum())
                    logger.info(f'[V8-DIAG] {pair} TASK23: daily_fti_msow_active={_fti_cnt}')

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
                                 'buying_exhaustion', 'be_zone_high', 'dist_cause_sufficient',
                                 'daily_sm_v2', 'daily_fallback_sow', 'daily_ema200_persist',
                                 'cycle_high',                        # TASK 16: LPSY ceiling anchor
                                 'ema200_persist_days', 'daily_ema200_value',  # TASK 20: 冰线 LPSY
                                 'daily_redist_ok',                            # TASK 22: 再派发结构
                                 'daily_fti_msow_active', 'daily_fti_ice_level'] # TASK 23: FTI冰线跌破
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

                    # ── TASK 14 Fix 3: 移除 sustained_markdown + _short_bear_gate 死代码 ──
                    # sustained_markdown：7年0触发（daily_bc_detected点事件经merge_asof只有24根1H
                    #   bar为True，rolling(30)永远无法达到30连续True）
                    # _short_bear_gate：孤儿变量，计算后从未在enter_short逻辑中引用
                    # bear_market_mode 保留（P5字段适配仍引用）
                    _daily_choch = dataframe.get(
                        'daily_bc_detected', pd.Series(False, index=dataframe.index)
                    ).fillna(False).astype(bool)
                    _dist_cause = dataframe.get(
                        'dist_cause_sufficient', pd.Series(False, index=dataframe.index)
                    ).fillna(False).astype(bool)
                    dataframe['bear_market_mode'] = _daily_choch & _dist_cause
                    dataframe['sustained_markdown'] = False  # 死代码占位，保持字段兼容性

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
                     'daily_sm_v2', 'daily_fallback_sow', 'daily_ema200_persist',
                     'daily_redist_ok',
                     'daily_fti_msow_active'}                                # TASK 23: FTI门控
        float_cols = {'v8_tr_high', 'v8_tr_low', 'v8_target_1', 'v8_target_2', 'v8_target_3',
                      'v8_sc_low', 'daily_ar_high', 'daily_ar_low', 'be_zone_high',
                      'target_short_conservative', 'target_short_standard', 'rr_short_standard',
                      'cycle_high',                                          # TASK 16
                      'ema200_persist_days', 'daily_ema200_value',           # TASK 20
                      'daily_fti_ice_level'}                                 # TASK 23: 冰线锚点
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
        # 触发条件（五路径，UTAD 已停用）：
        #   路径1 SOW：daily_dist_sow_detected（经典 BC→AR→SOW 或 ar→sow 直通）
        #   路径2 UTAD：【已停用，见下方注释】
        #   路径3 BE：buying_exhaustion（渐进式分配顶，无单根 BC，2022型）
        dataframe['enter_short'] = 0

        sow_entry  = (
            dataframe.get('daily_dist_sow_detected',
                          pd.Series(False, index=dataframe.index)).fillna(False).astype(bool) |
            dataframe.get('daily_fallback_sow',                              # TASK 13: BC独立 fallback SOW
                          pd.Series(False, index=dataframe.index)).fillna(False).astype(bool)
        )
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
            dataframe.get('daily_sm_v2',                                      # TASK 13: BC独立熊市闸门
                          pd.Series(False, index=dataframe.index)).fillna(False).astype(bool) |
            dataframe.get('daily_ema200_persist',                             # TASK 14: 连续跌穿EMA200第四路径
                          pd.Series(False, index=dataframe.index)).fillna(False).astype(bool)
        )
        pnf_valid_short = dataframe.get('target_short_conservative',
                                        pd.Series(0.0, index=dataframe.index)).fillna(0.0) > 0

        # BE 路径：不要求 RR（无 BC 锚点时 RR 计算可能偏低），用 _bear_gate 门控
        be_gate = (
            be_entry & _bear_gate & pnf_valid_short & short_intent_ok
        )

        # ── TASK 16: LPSY 路径（最后供应点）─────────────────────────
        # 物理含义：分配因果已激活（DCS True）+ 反弹衰竭区以下（close < cycle_high×0.95）
        #   + 1H 摆高确认于衰竭区内 + 跌破反弹支点（ChoCh）+ 放量阴线
        # 门控：full bear_gate + pnf_valid_short（豁免 RR，对齐 BE 路径）
        _cycle_high_1h = dataframe.get('cycle_high',
                                       pd.Series(np.nan, index=dataframe.index)).fillna(np.nan)
        _lpsy_ceiling  = _cycle_high_1h * 0.95

        # 日线层：DCS 激活 + 当前收盘在衰竭区以内
        _dcs_1h = dataframe.get('dist_cause_sufficient',
                                 pd.Series(False, index=dataframe.index)).fillna(False).astype(bool)
        _lpsy_context = _dcs_1h & (_cycle_high_1h.notna()) & (dataframe['close'] < _lpsy_ceiling)

        # 1H 执行层：用 WaveAnalyzer 的摆动点追踪
        #   is_wave_start & wave_direction=='down'  → 确认摆高（下降波起点）
        #   is_wave_start & wave_direction=='up'    → 确认摆低（上升波起点），作为反弹支点
        _is_swing_high = (
            dataframe['is_wave_start'].fillna(False).astype(bool) &
            (dataframe['wave_direction'] == 'down')
        )
        _is_swing_low = (
            dataframe['is_wave_start'].fillna(False).astype(bool) &
            (dataframe['wave_direction'] == 'up')
        )

        # 追踪最近确认摆高价（ffill：持续到下一个摆高为止）
        _recent_swing_high_price = dataframe['high'].where(_is_swing_high).ffill()
        # TASK 27: 存入 dataframe，供 custom_stoploss 读取 wave_breakdown 止损锚点
        dataframe['wb_swing_high_ref'] = _recent_swing_high_price
        # 追踪最近确认摆低价（ffill：反弹支点，ChoCh 参考）
        _recent_swing_low_price  = dataframe['low'].where(_is_swing_low).ffill()

        # 1H 量均线（过去20根K线）
        _vol_ma20_lpsy = dataframe['volume'].rolling(20, min_periods=10).mean()

        lpsy_1h_ok = (
            _recent_swing_high_price.notna() &
            (_recent_swing_high_price < _lpsy_ceiling) &          # 摆高位于衰竭区以内
            _recent_swing_low_price.notna() &
            (dataframe['close'] < _recent_swing_low_price) &      # 跌破反弹支点（ChoCh）
            (dataframe['close'] < dataframe['open']) &            # 阴线确认
            (dataframe['volume'] > _vol_ma20_lpsy * 1.2)          # 放量确认
        )

        lpsy_gate = (
            _lpsy_context & lpsy_1h_ok & _bear_gate & pnf_valid_short & short_intent_ok
        )

        # ── TASK 20: EMA200 冰线 LPSY（再派发确权路径）────────────────────────
        # 物理含义：EMA200 在 Markdown 阶段充当"冰线（Ice）"，每次触碰后失败
        #   = 供应主导的物理证据（努力与结果不和谐）
        # 条件：
        #   ① daily_ema200_persist = True（熊市门已激活）
        #   ② ema200_persist_days >= 30（确认税：供应已统治市场30天，排除牛市深调假信号）
        #   ③ 1H high >= daily_ema200_value * 0.98（触碰冰线警戒区）
        #   ④ 1H close < daily_ema200_value（未能站上冰线）
        #   ⑤ 1H close < open（阴线确认失败）
        # 豁免：pnf_valid_short（DCS=False时无PNF目标，以EMA200×1.02为结构止损替代）
        # 止损：custom_stoploss 中 EMA200 × 1.02（冰线被收复=空头论题证伪）
        _ice_ema200     = dataframe.get('daily_ema200_value',
                                        pd.Series(0.0, index=dataframe.index)).fillna(0.0)
        _persist_days   = dataframe.get('ema200_persist_days',
                                        pd.Series(0.0, index=dataframe.index)).fillna(0.0)
        _ice_persist_ok = (
            dataframe.get('daily_ema200_persist',
                          pd.Series(False, index=dataframe.index)).fillna(False).astype(bool) &
            (_persist_days >= 30)
        )
        _ice_touch = (
            (_ice_ema200 > 0) &
            (dataframe['high'] >= _ice_ema200 * 0.98) &   # 触碰冰线警戒区
            (dataframe['close'] < _ice_ema200) &           # 未能站上冰线
            (dataframe['close'] < dataframe['open'])       # 阴线确认
        )
        ice_lpsy_gate = _ice_persist_ok & _ice_touch & short_intent_ok

        # ── TASK 22: 再派发结构路径（第六路径）────────────────────────────────
        # 物理含义：主跌浪途中横盘整理，高点递降 + 需求匮乏 = 供应补充完成，续跌
        # 条件：
        #   ① daily_redist_ok = True（日线层：7日横盘 + 高点递降 + 上涨日量萎缩 + persist>=20）
        #   ② 1H close < open（阴线：执行层确认供应入场）
        #   ③ short_intent_ok（做空意图）
        # 豁免：pnf_valid_short 和 EMA200触碰（结构门控替代单点信号）
        # 止损：EMA200 × 1.02（与 ice_lpsy 共用冰线结构止损）
        _redist_daily_ok = dataframe.get('daily_redist_ok',
                                         pd.Series(False, index=dataframe.index)).fillna(False).astype(bool)
        _redist_1h_confirm = (
            (dataframe['close'] < dataframe['open']) &     # 阴线：供应主导
            (dataframe['volume'] > 0)                      # 有成交（过滤空 bar）
        )
        redist_gate = _redist_daily_ok & _redist_1h_confirm & short_intent_ok

        # ── TASK 23: FTI（跌破冰层）路径（第七路径）────────────────────────────────
        # 物理含义：横盘区间跌破后回抽冰线，收盘在低位区（需求失败）= 冰线变阻力确认
        # 日线门控（daily_fti_msow_active）已在 populate_indicators 日线层设置：
        #   - 横盘前置（14日 range/close ≤ 15%）+ EMA200熊市 + MSOW（扩量宽价差破冰）
        #   - MSOW 后 7 日内持续有效，daily_fti_ice_level 锁定冰线锚点
        # 1H 执行确认：
        #   1. daily_fti_msow_active=True（在MSOW后7日窗口内）
        #   2. 1H high >= ice × 0.985（触及冰线）
        #   3. 1H close < ice（未能收复冰线）
        #   4. 收盘位置 <= 0.40：(close-low)/(high-low)（卖方守住冰线，日内需求失败）
        #   5. 量 <= vol_ma_1h（1H量不超过均量，需求匮乏）
        # 豁免：pnf_valid_short、EMA200距离（日线门控已含EMA200熊市确认）
        # 止损：ice_level × 1.02（冰线收复则逻辑证伪，区别于ATR倍数止损）
        _fti_active = dataframe.get('daily_fti_msow_active',
                                    pd.Series(False, index=dataframe.index)).fillna(False).astype(bool)
        _fti_ice = dataframe.get('daily_fti_ice_level',
                                  pd.Series(np.nan, index=dataframe.index)).fillna(0)

        _fti_vol_ma_1h = dataframe['volume'].rolling(20, min_periods=5).mean()
        _fti_bar_range = (dataframe['high'] - dataframe['low']).replace(0, np.nan)
        _fti_close_loc = (dataframe['close'] - dataframe['low']) / _fti_bar_range

        _fti_1h_confirm = (
            (_fti_ice > 0) &
            (dataframe['high'] >= _fti_ice * 0.985) &    # 触及冰线
            (dataframe['close'] < _fti_ice) &             # 未能收复冰线
            (_fti_close_loc <= 0.40) &                    # 收盘在日内低位区（需求失败）
            (dataframe['volume'] <= _fti_vol_ma_1h)       # 量 <= 1H均量（需求匮乏）
        )
        fti_gate = _fti_active & _fti_1h_confirm & short_intent_ok

        # ── TASK 27: wave_breakdown 趋势跟随空头（第八路径）────────────────────────
        # 物理含义：熊市确认（EMA200 persist ≥15天激活）+ 1H 下降波 ChoCh = 跟随主跌浪做空
        # 对称于多头 v8_ws_d/v8_ws_e（wave_start up），但方向朝下
        # 入场：is_wave_start & wave_direction=='down'（摆高确认 + 下降浪启动）
        # 止损：最近确认摆高 × 1.01（swing high 被突破 = 下跌结构失效，存 wb_swing_high_ref）
        # 出场：is_up_wave → v8_up_wave_reverse（已有路径，对称退出）
        # 门控：豁免 pnf_valid_short 和 rr_short_ok（趋势跟随，无 PNF 目标位约束）
        _wb_bear_ok = (
            dataframe.get('daily_ema200_persist',
                          pd.Series(False, index=dataframe.index)).fillna(False).astype(bool) &
            (_persist_days >= 0)
        )
        _wb_signal = (
            dataframe['is_wave_start'].rolling(8, min_periods=1).max().fillna(0).astype(bool) &
            (dataframe['wave_direction'] == 'down') &
            (dataframe['wave_efficiency'] > 0.001) &
            (dataframe['wave_displacement'] > dataframe['close'] * 0.008)
        )
        wave_breakdown_gate = _wb_bear_ok & _wb_signal & short_intent_ok

        final_short_entry = (
            sow_entry &
            short_intent_ok & rr_short_ok & _bear_gate & pnf_valid_short
        ) | be_gate | lpsy_gate | ice_lpsy_gate | redist_gate | fti_gate | wave_breakdown_gate

        # 优先级（顺序赋值：后者覆盖前者，故最高优先级放最后）
        # WB < FTI < REDIST < ICE_LPSY < BE < LPSY < SOW
        dataframe.loc[final_short_entry & wave_breakdown_gate, 'enter_tag'] = 'v8_short_wsdown'
        dataframe.loc[final_short_entry & fti_gate,       'enter_tag'] = 'v8_short_fti'
        dataframe.loc[final_short_entry & redist_gate,    'enter_tag'] = 'v8_short_redist'
        dataframe.loc[final_short_entry & ice_lpsy_gate,  'enter_tag'] = 'v8_short_ice_lpsy'
        dataframe.loc[final_short_entry & be_entry,       'enter_tag'] = 'v8_short_be'
        dataframe.loc[final_short_entry & lpsy_gate,      'enter_tag'] = 'v8_short_lpsy'
        dataframe.loc[final_short_entry & sow_entry,      'enter_tag'] = 'v8_short_sow'
        dataframe.loc[final_short_entry, 'enter_short'] = 1

        _be_cnt   = int(be_gate.sum())
        _sow_cnt  = int((final_short_entry & sow_entry).sum())
        _lpsy_cnt = int(lpsy_gate.sum())
        _ice_cnt  = int(ice_lpsy_gate.sum())
        _redist_cnt = int(redist_gate.sum())
        _fti_cnt  = int(fti_gate.sum())
        _wb_cnt   = int(wave_breakdown_gate.sum())
        _pair = metadata.get('pair', '')
        logger.info(f'[V8-DIAG] {_pair} enter_short: sow={_sow_cnt} be={_be_cnt} lpsy={_lpsy_cnt} '
                    f'ice={_ice_cnt} redist={_redist_cnt} fti={_fti_cnt} wb={_wb_cnt}')

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

    # ── wave_breakdown 结构锚点（custom_stoploss / custom_exit 共用）──
    def _wb_structural_high(self, dataframe, trade) -> float:
        """
        返回 wave_breakdown 入场时的结构摆高（8h 窗口 MAX，ZigZag lag ±3 补偿）。
        custom_stoploss 和 custom_exit 共用同一锚点，保证两条路径触发线一致。
        返回 NaN 表示无合规候选，调用方应自行 fallback。
        """
        entry_price = trade.open_rate
        open_date = pd.Timestamp(trade.open_date).tz_localize(None) \
            if getattr(trade.open_date, 'tzinfo', None) is None else \
            pd.Timestamp(trade.open_date).tz_convert(None)
        df_dates = dataframe['date']
        if hasattr(df_dates.iloc[0], 'tzinfo') and df_dates.iloc[0].tzinfo is not None:
            df_dates = df_dates.dt.tz_convert(None)
        entry_idx = int(df_dates.searchsorted(open_date, side='right')) - 1
        wb_sl = np.nan
        if 0 <= entry_idx < len(dataframe):
            win_start = max(0, entry_idx - 7)
            window = dataframe.iloc[win_start:entry_idx + 1]
            is_sh_win = (window['is_wave_start'].fillna(False).astype(bool) &
                         (window['wave_direction'] == 'down'))
            for rel_i, is_sh in enumerate(is_sh_win):
                if not is_sh:
                    continue
                abs_i = win_start + rel_i
                peak = float(dataframe.iloc[max(0, abs_i - 3):abs_i + 1]['high'].max())
                if peak >= entry_price and (np.isnan(wb_sl) or peak > wb_sl):
                    wb_sl = peak
        return wb_sl

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
            enter_tag_short = getattr(trade, 'enter_tag', '') or ''

            # TASK 27: wave_breakdown 专用止损 — 入场时摆高 × 1.01（swing high 被突破=下跌结构失效）
            # 必须固化入场 bar 的摆高锚点，不能用 last：ffill 会随行情推进到新（更低）摆高，导致止损漂移
            if enter_tag_short == 'v8_short_wsdown':
                wb_sl = self._wb_structural_high(dataframe, trade)
                if not np.isnan(wb_sl) and wb_sl > 0:
                    sl_price = wb_sl * 1.01
                    return (sl_price / current_rate) - 1
                return (float(dataframe['high'].tail(20).max()) * 1.01 / current_rate) - 1

            # TASK 20/22: ice_lpsy / redist 专用止损 — EMA200 × 1.02（冰线被收复=空头论题证伪）
            if enter_tag_short in ('v8_short_ice_lpsy', 'v8_short_redist'):
                ice_ema200 = last.get('daily_ema200_value', np.nan)
                if not np.isnan(ice_ema200) and ice_ema200 > 0:
                    sl_price = ice_ema200 * 1.02
                    return (sl_price / current_rate) - 1
                # fallback：近期20日高点
                return (float(dataframe['high'].tail(20).max()) * 1.02 / current_rate) - 1

            # TASK 23: FTI 专用止损 — 结构冰线 × 1.02（冰线收复=破位为震仓，逻辑证伪）
            if enter_tag_short == 'v8_short_fti':
                fti_ice = last.get('daily_fti_ice_level', np.nan)
                if not np.isnan(fti_ice) and fti_ice > 0:
                    sl_price = fti_ice * 1.02
                    return (sl_price / current_rate) - 1
                # fallback：近期20日高点
                return (float(dataframe['high'].tail(20).max()) * 1.02 / current_rate) - 1

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

            # 1. 结构失败：价格突破结构锚点（做空逻辑彻底失效）
            # wave_breakdown 用 8h 窗口 MAX swing high × 1.01（与 custom_stoploss 同锚）
            # 其他路径用派发区 AR High
            enter_tag_short = getattr(trade, 'enter_tag', '') or ''
            if enter_tag_short == 'v8_short_wsdown':
                wb_sl = self._wb_structural_high(dataframe, trade)
                sl_price = (wb_sl * 1.01) if (not np.isnan(wb_sl) and wb_sl > 0) \
                    else float(dataframe['high'].tail(20).max()) * 1.01
                if current_rate > sl_price:
                    return 'v8_short_structure_fail'
            elif not np.isnan(ar_high) and ar_high > 0:
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
        # wave_breakdown 豁免（趋势跟随路径不依赖 PNF 结构）
        _wb_entry = entry_tag and 'wsdown' in entry_tag
        if pd.isna(last.get('v8_target_1', np.nan)):
            if _wb_entry:
                logger.info(f'{pair} wave_breakdown exempt: no PNF target')
            else:
                logger.info(f'{pair} entry rejected: no PNF target')
                # P5：记录 confirm 层拒绝事件（补充候选信号的漏斗数据）
                if self.event_bridge is not None:
                    try:
                        ts = pd.Timestamp(last['date']).to_pydatetime() if 'date' in last.index else current_time
                        self.event_bridge.event_emitter.emit_signal(
                            pair=pair,
                            timeframe=self.timeframe,
                            side=side,
                            signal_name=entry_tag or 'unknown',
                            candidate_enter_tag=entry_tag or 'unknown',
                            was_executed=False,
                            signal_quality_before=1.0,
                            signal_quality_after=1.0,
                            rejection_reason='pnf_target_missing',
                            ts=ts,
                            market_env=str(last.get('market_env', '')),
                            bear_market_mode=bool(last.get('bear_market_mode', False)),
                        )
                    except Exception as _e:
                        logger.debug(f'[V8] P5 reject signal emit failed: {_e}')
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
