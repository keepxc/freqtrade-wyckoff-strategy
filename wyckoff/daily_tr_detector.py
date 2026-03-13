"""
P1: 日线 TR 边界识别器（Daily Trading Range Detector）

职责：识别日线级别的积累区间（TR），为 H1 入场提供结构性背景门控。
与 daily_event_detector.py 职责分离：后者识别「派发侧」序列，本模块识别「积累侧」TR。

═══════════════════════════════════════════════════════════════════════════
核心设计：三窗口双尺度判定
─────────────────────────────────────────────────────────────────────────
  主窗口（60日，锚）   : 结构识别主依据，抗噪音
  快速窗口（20日，雷达）: 早期感知，防止"60日判TR但短期已破坏结构"
  慢速窗口（120日，保险丝）: 稳定性校验，边界模糊时提供背书

TR_exists 逻辑：
    TR_exists = TR60_confirmed AND (TR20_not_breaking OR TR120_consistent)

TR60_confirmed 条件：
    1. 高低点重叠度 >= daily_tr_min_overlap (默认 0.65)
       — 60日内高点/低点的重叠比例，衡量横盘压缩程度
    2. 区间宽度 / ATR20 在合理范围 [2.0, 12.0]
       — 太窄（<2x ATR）是单根K线噪音；太宽（>12x ATR）是趋势而非TR
    3. 假突破回收次数 >= 1
       — 突破近期极值后在3根K线内收回，是威科夫TR的物理定义

TR20_not_breaking：
    — 20日窗口内无方向性突破（价格未脱离60日TR边界 * 1.02）
    — 防止"60日判TR但短期已破坏结构"的误判

TR120_consistent：
    — 120日窗口同样满足重叠度 >= 0.55（更宽松阈值，稳定性背书）
    — 在TR60边界模糊时作为补充确认

Phase C/D 识别（来自 EventSeqSM 辅证）：
    — 读取日线层 wyckoff_phase 状态（若可用）
    — Spring / SOS / LPS 出现即标记 Phase C 或 D
    — 若日线 EventSeqSM 未运行，则降级到 "unknown"

双通道一致性仲裁：
    — 主副一致  → 强确认，daily_gate_score = +1.0
    — 主副冲突  → 降级，daily_gate_score = +0.3（soft 模式不 hard block）
    — 主判定否定 → 阻断，daily_gate_score = -1.0

═══════════════════════════════════════════════════════════════════════════
输出字段：
─────────────────────────────────────────────────────────────────────────
  daily_tr_exists         : bool  — TR 综合判定（TR60 AND (TR20 OR TR120)）
  daily_tr60_confirmed    : bool  — 60日主窗口TR确认
  daily_tr20_not_breaking : bool  — 20日快速窗口未破坏结构
  daily_tr120_consistent  : bool  — 120日慢速窗口稳定性背书
  daily_phase_cd          : bool  — 日线 Phase C 或 D 已确认
  daily_phase_label       : str   — 'phase_c' / 'phase_d' / 'unknown'
  daily_tr_high           : float — TR 上边界（60日高点）
  daily_tr_low            : float — TR 下边界（60日低点）
  daily_tr_width_atr      : float — TR 宽度 / ATR20（诊断用）
  daily_tr_overlap        : float — 高低点重叠度（诊断用）
  daily_gate_score        : float — 门控评分：+1.0 / +0.3 / -1.0

═══════════════════════════════════════════════════════════════════════════
设计原则：
─────────────────────────────────────────────────────────────────────────
  1. 纯基础设施：全部输出字段，gate_mode 由策略层控制
  2. 无前瞻偏差：所有条件仅引用当前或历史K线
  3. 自洽完备：自行计算 ATR20，不依赖外部字段（复用 daily_atr20 若已有）
  4. 降级安全：数据不足时全部字段退化为安全默认值（不 block）
"""

import numpy as np
import pandas as pd
import logging
from pandas import DataFrame

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# 默认参数（可由策略层通过参数覆盖）
# ─────────────────────────────────────────────────────────────────────────────
_TR_WINDOW_MAIN      = 60     # 主窗口（日）
_TR_WINDOW_FAST      = 20     # 快速窗口（日）
_TR_WINDOW_SLOW      = 120    # 慢速窗口（日）
_TR_MIN_OVERLAP      = 0.65   # 主窗口最低重叠度
_TR_SLOW_MIN_OVERLAP = 0.55   # 慢速窗口最低重叠度（更宽松）
_TR_CONFLICT_SCORE   = 0.30   # 主副冲突时的 soft 降级分值
_TR_WIDTH_MIN_ATR    = 2.0    # TR 宽度下限（ATR 倍数）
_TR_WIDTH_MAX_ATR    = 12.0   # TR 宽度上限（ATR 倍数）
_TR_FAKEOUT_WINDOW   = 3      # 假突破回收确认窗口（根）
_TR_BREAK_BUFFER     = 1.02   # 突破缓冲（2%，防止噪音误判为突破）
_TR_CORE_BAND_OFFSET = 0.25   # 重叠度统计时使用 TR 中部 50% 区域

# Phase C/D 事件标识（与 event_sequence_state_machine 保持一致）
_PHASE_C_EVENTS = {'spring', 'spring_test', 'phase_c'}
_PHASE_D_EVENTS = {'sos', 'lps', 'back_up_to_creek', 'phase_d'}


class DailyTRDetector:
    """
    日线 TR 边界识别器。

    使用方法：
        detector = DailyTRDetector()
        df_1d = detector.detect(df_1d)
        # df_1d 将包含所有输出字段
    """

    def __init__(
        self,
        tr_window_main: int = _TR_WINDOW_MAIN,
        tr_window_fast: int = _TR_WINDOW_FAST,
        tr_window_slow: int = _TR_WINDOW_SLOW,
        tr_min_overlap: float = _TR_MIN_OVERLAP,
        tr_slow_min_overlap: float = _TR_SLOW_MIN_OVERLAP,
        tr_conflict_score: float = _TR_CONFLICT_SCORE,
        tr_width_min_atr: float = _TR_WIDTH_MIN_ATR,
        tr_width_max_atr: float = _TR_WIDTH_MAX_ATR,
        core_band_offset: float = _TR_CORE_BAND_OFFSET,
        fakeout_window: int = _TR_FAKEOUT_WINDOW,
        break_buffer: float = _TR_BREAK_BUFFER,
        use_slow_check: bool = True,
    ):
        self.tr_window_main      = tr_window_main
        self.tr_window_fast      = tr_window_fast
        self.tr_window_slow      = tr_window_slow
        self.tr_min_overlap      = tr_min_overlap
        self.tr_slow_min_overlap = tr_slow_min_overlap
        self.tr_conflict_score   = tr_conflict_score
        self.tr_width_min_atr    = tr_width_min_atr
        self.tr_width_max_atr    = tr_width_max_atr
        self.core_band_offset    = core_band_offset
        self.fakeout_window      = fakeout_window
        self.break_buffer        = break_buffer
        self.use_slow_check      = use_slow_check

    # ─────────────────────────────────────────────────────────────────────────
    # 公开接口
    # ─────────────────────────────────────────────────────────────────────────

    def detect(self, df: DataFrame) -> DataFrame:
        """
        在日线 DataFrame 上运行 TR 识别，返回附加输出字段的 DataFrame。
        最少需要 max(tr_window_slow, 30) 根日线数据。
        """
        df = df.copy()
        min_bars = max(self.tr_window_slow, 30) + 5
        if len(df) < min_bars:
            logger.warning(
                f'DailyTRDetector: 数据不足 {len(df)}/{min_bars} 根，'
                f'全部字段退化为安全默认值'
            )
            return self._init_default_fields(df)

        try:
            df = self._compute_atr(df)
            df = self._detect_tr_windows(df)
            df = self._detect_fakeout_recovery(df)
            df = self._compute_tr60_confirmed(df)
            df = self._compute_tr_exists(df)
            df = self._detect_phase_cd(df)
            df = self._compute_gate_score(df)
            return df
        except Exception as e:
            logger.error(f'DailyTRDetector.detect() 异常: {e}', exc_info=True)
            return self._init_default_fields(df)

    # ─────────────────────────────────────────────────────────────────────────
    # 内部步骤
    # ─────────────────────────────────────────────────────────────────────────

    def _compute_atr(self, df: DataFrame) -> DataFrame:
        """计算 ATR20（若 df 已有 daily_atr20 则复用）。"""
        if 'daily_atr20' in df.columns and df['daily_atr20'].notna().sum() > 20:
            df['_atr'] = df['daily_atr20']
        else:
            high  = df['high'].values.astype(float)
            low   = df['low'].values.astype(float)
            close = df['close'].values.astype(float)
            n = len(df)
            tr = np.zeros(n)
            tr[0] = high[0] - low[0]
            for i in range(1, n):
                tr[i] = max(high[i] - low[i],
                            abs(high[i] - close[i - 1]),
                            abs(low[i]  - close[i - 1]))
            atr = pd.Series(tr, index=df.index).rolling(20, min_periods=5).mean()
            df['_atr'] = atr
        return df

    def _detect_tr_windows(self, df: DataFrame) -> DataFrame:
        """计算三个窗口的 TR 高低点、宽度/ATR、重叠度。"""
        close = df['close']
        high  = df['high']
        low   = df['low']
        atr   = df['_atr'].replace(0, np.nan).ffill().fillna(1.0)

        for win, suffix in [
            (self.tr_window_main, '_60'),
            (self.tr_window_fast, '_20'),
            (self.tr_window_slow if self.use_slow_check else self.tr_window_main, '_120'),
        ]:
            roll_high = high.rolling(win, min_periods=max(win // 2, 5)).max()
            roll_low  = low.rolling(win, min_periods=max(win // 2, 5)).min()
            width     = roll_high - roll_low

            # 重叠度：统计窗口内有多少根 K 线持续覆盖 TR 中部区域。
            # 直接求“所有 K 线公共交集”对 BTC 日线过于严格，几乎恒为 0；
            # 这里改为衡量横盘期间价格是否反复回到区间中部。
            core_low = roll_low + width * self.core_band_offset
            core_high = roll_high - width * self.core_band_offset
            overlap_ratio = (
                ((high >= core_low) & (low <= core_high))
                .rolling(win, min_periods=max(win // 2, 5))
                .mean()
                .clip(0, 1)
            )

            df[f'_tr_high{suffix}']    = roll_high
            df[f'_tr_low{suffix}']     = roll_low
            df[f'_tr_width_atr{suffix}'] = (width / atr).clip(lower=0)
            df[f'_tr_overlap{suffix}'] = overlap_ratio

        return df

    def _detect_fakeout_recovery(self, df: DataFrame) -> DataFrame:
        """
        假突破回收检测（基于前一日已知的主窗口 TR 边界）：
        价格突破 TR 边界 * break_buffer 后，在 fakeout_window 根内收回。
        仅在实际回收到区间内的那根 K 线上记一次事件，避免前瞻。
        """
        high  = df['high'].values.astype(float)
        low   = df['low'].values.astype(float)
        close = df['close'].values.astype(float)
        tr_high = df['_tr_high_60'].shift(1).values.astype(float)
        tr_low  = df['_tr_low_60'].shift(1).values.astype(float)
        n = len(df)

        fakeout_count = np.zeros(n, dtype=int)
        for breakout_idx in range(1, n):
            if np.isnan(tr_high[breakout_idx]) or np.isnan(tr_low[breakout_idx]):
                continue

            up_break = high[breakout_idx] > tr_high[breakout_idx] * self.break_buffer
            down_break = low[breakout_idx] < tr_low[breakout_idx] / self.break_buffer
            if not (up_break or down_break):
                continue

            recovery_end = min(n - 1, breakout_idx + self.fakeout_window)
            for recovery_idx in range(breakout_idx, recovery_end + 1):
                if up_break and close[recovery_idx] <= tr_high[breakout_idx]:
                    fakeout_count[recovery_idx] += 1
                    break
                if down_break and close[recovery_idx] >= tr_low[breakout_idx]:
                    fakeout_count[recovery_idx] += 1
                    break

        # 在主窗口内累计最近确认过的假突破回收次数
        df['_fakeout_count_60'] = (
            pd.Series(fakeout_count, index=df.index)
            .rolling(self.tr_window_main, min_periods=max(self.tr_window_main // 2, 5))
            .sum()
        )
        return df

    def _compute_tr60_confirmed(self, df: DataFrame) -> DataFrame:
        """
        TR60 三项条件全部满足时才确认：
        1. 重叠度 >= min_overlap
        2. 宽度在 [min_atr, max_atr] 范围内
        3. 窗口内假突破回收次数 >= 1
        """
        overlap_ok  = df['_tr_overlap_60'] >= self.tr_min_overlap
        width_ok    = (
            (df['_tr_width_atr_60'] >= self.tr_width_min_atr) &
            (df['_tr_width_atr_60'] <= self.tr_width_max_atr)
        )
        fakeout_ok  = df['_fakeout_count_60'] >= 1

        df['daily_tr60_confirmed'] = overlap_ok & width_ok & fakeout_ok
        # 暴露诊断字段
        df['daily_tr_overlap']     = df['_tr_overlap_60'].round(3)
        df['daily_tr_width_atr']   = df['_tr_width_atr_60'].round(2)
        df['daily_tr_high']        = df['_tr_high_60']
        df['daily_tr_low']         = df['_tr_low_60']
        return df

    def _compute_tr_exists(self, df: DataFrame) -> DataFrame:
        """
        组合三窗口：TR_exists = TR60_confirmed AND (TR20_not_breaking OR TR120_consistent)
        """
        # TR20_not_breaking：20日窗口未出现方向性突破（close 未脱离前一日已知 60日 TR 边界 * buffer）
        close = df['close']
        tr_high_60 = df['_tr_high_60'].shift(1)
        tr_low_60  = df['_tr_low_60'].shift(1)
        above_break = close > tr_high_60 * self.break_buffer
        below_break = close < tr_low_60 / self.break_buffer
        any_break_20 = (
            (above_break | below_break)
            .rolling(self.tr_window_fast, min_periods=1)
            .sum()
            .gt(0)
        )
        df['daily_tr20_not_breaking'] = ~any_break_20

        # TR120_consistent：120日窗口重叠度 >= slow_min_overlap（稳定性背书）
        if self.use_slow_check:
            df['daily_tr120_consistent'] = (
                df['_tr_overlap_120'] >= self.tr_slow_min_overlap
            )
        else:
            df['daily_tr120_consistent'] = df['daily_tr60_confirmed']  # 关闭慢窗口时用主窗口代替

        # 组合
        df['daily_tr_exists'] = (
            df['daily_tr60_confirmed'] &
            (df['daily_tr20_not_breaking'] | df['daily_tr120_consistent'])
        )
        return df

    def _detect_phase_cd(self, df: DataFrame) -> DataFrame:
        """
        日线 Phase C/D 识别（辅判定）。
        优先读取 EventSeqSM 输出的 wyckoff_phase 字段（若已在 df 中）；
        否则降级到"日线 TR 内出现 Spring 特征"的简化判断。
        """
        # 方案一：读取 EventSeqSM 字段
        if 'wyckoff_phase' in df.columns:
            phase_series = df['wyckoff_phase'].fillna('unknown')
            phase_c = phase_series.isin(['phase_c', 'spring', 'spring_test'])
            phase_d = phase_series.isin(['phase_d', 'sos', 'lps', 'back_up_to_creek'])
            df['daily_phase_label'] = 'unknown'
            df.loc[phase_c, 'daily_phase_label'] = 'phase_c'
            df.loc[phase_d, 'daily_phase_label'] = 'phase_d'
            df['daily_phase_cd'] = phase_c | phase_d
        else:
            # 方案二：简化降级版
            # 在 TR60_confirmed 区间内维护一个轻量状态机：
            # Spring 触发后进入 phase_c，后续上推到区间上半部时切到 phase_d。
            close  = df['close'].values.astype(float)
            low    = df['low'].values.astype(float)
            tr_low = df['_tr_low_60'].values.astype(float)
            tr_high= df['_tr_high_60'].values.astype(float)
            tr60_confirmed = df.get(
                'daily_tr60_confirmed',
                pd.Series(False, index=df.index)
            ).astype(bool).values
            n = len(df)

            phase_label = np.array(['unknown'] * n, dtype=object)
            for i in range(1, n):
                if np.isnan(tr_low[i]) or np.isnan(tr_high[i]) or not tr60_confirmed[i]:
                    continue
                prev_state = phase_label[i - 1] if tr60_confirmed[i - 1] else 'unknown'
                spring_like = low[i] <= tr_low[i] * 1.005 and close[i] > tr_low[i]
                phase_d_like = close[i] > tr_low[i] + (tr_high[i] - tr_low[i]) * 0.6

                if spring_like:
                    phase_label[i] = 'phase_c'
                elif prev_state == 'phase_c' and phase_d_like:
                    phase_label[i] = 'phase_d'
                elif prev_state in ('phase_c', 'phase_d'):
                    phase_label[i] = prev_state

            df['daily_phase_label'] = phase_label
            df['daily_phase_cd'] = pd.Series(phase_label, index=df.index).isin(['phase_c', 'phase_d'])

        return df

    def _compute_gate_score(self, df: DataFrame) -> DataFrame:
        """
        双通道一致性仲裁，计算 daily_gate_score：
          主副一致（TR_exists AND phase_cd）  → +1.0 强确认
          主确认但phase未知                   → +0.3 冲突降级
          主判定否定（TR 不存在）              → -1.0 阻断
        """
        tr_exists = df['daily_tr_exists'].astype(bool)
        phase_cd  = df['daily_phase_cd'].astype(bool)

        score = pd.Series(-1.0, index=df.index)  # 默认：否定
        # TR 存在 + Phase C/D 双确认 → 强确认
        score[tr_exists & phase_cd]  = 1.0
        # TR 存在但 Phase 未知 → 冲突降级（soft 分数）
        score[tr_exists & ~phase_cd] = self.tr_conflict_score

        df['daily_gate_score'] = score
        return df

    # ─────────────────────────────────────────────────────────────────────────
    # 安全默认值（数据不足或异常时）
    # ─────────────────────────────────────────────────────────────────────────

    def _init_default_fields(self, df: DataFrame) -> DataFrame:
        df['daily_tr_exists']          = False
        df['daily_tr60_confirmed']     = False
        df['daily_tr20_not_breaking']  = True   # 默认"未破坏"，不误触发 hard block
        df['daily_tr120_consistent']   = False
        df['daily_phase_cd']           = False
        df['daily_phase_label']        = 'unknown'
        df['daily_tr_high']            = float('nan')
        df['daily_tr_low']             = float('nan')
        df['daily_tr_width_atr']       = float('nan')
        df['daily_tr_overlap']         = float('nan')
        df['daily_gate_score']         = 0.0    # 默认中性，不奖励也不惩罚
        return df
