"""
Task 4.1b-final: Daily 事件序列探测器（派发侧）

识别 Daily 级别 BC → AR → UTAD → SOW 完整派发序列，
输出 `dist_cause_sufficient` 字段作为熊市模式的**第二道防线（因果过滤器）**。

═══════════════════════════════════════════════════════════════════════════
功能定位：
─────────────────────────────────────────────────────────────────────────
  第一道防线（Task 4.1a）：daily_choch_confirmed
    → 物理熔断：Daily EMA200 结构破位（SOW Bar 或 3根持续收于 EMA200×0.98 下方）

  第二道防线（本模块）：dist_cause_sufficient
    → 因果过滤：确认具备真正的「派发因」，而不只是牛市深回调
    → 2026Q1 型回调（再积累）→ dist_cause_sufficient = False → 不进入熊市模式

联合触发（Task 4.1c 激活）：
    bear_market_mode = daily_choch_confirmed AND dist_cause_sufficient

═══════════════════════════════════════════════════════════════════════════
输出字段（全部基础设施字段，Task 4.1c 激活前不触发任何硬过滤）：
─────────────────────────────────────────────────────────────────────────
  daily_bc_detected       : bool  — 本日满足 BC 条件（路径A OR 路径B）
  daily_bc_path           : str   — 'path_a' / 'path_b' / 'path_ab' / 'none'
  daily_ar_high           : float — BC 后 3-15 根日线内确认的 AR 高点（锁定值，Task 4.1c 重置）
  daily_ar_low            : float — BC 至 AR 高点之间的最低点（SOW 跌破参考位）
  dist_sequence_progress  : str   — 'none' / 'bc' / 'ar' / 'utad' / 'sow'
                                    （序列进展状态，'utad'/'sow' 表示派发因充分）
  daily_utad_detected     : bool  — UTAD 假突破检测（量缩 + 接近 AR 高点）
  daily_dist_sow_detected : bool  — Daily SOW 检测（跌破 AR 低点 + 放量阴线）
  buying_exhaustion       : bool  — 渐进式耗尽顶部（无 BC 型补充路径）
  dist_cause_sufficient   : bool  — 派发因充分（OR: BC序列>=UTAD | 渐进耗尽）

═══════════════════════════════════════════════════════════════════════════
设计原则：
─────────────────────────────────────────────────────────────────────────
  1. 纯基础设施：全部输出字段，不激活任何硬过滤
  2. 无前瞻偏差：所有条件仅引用当前或历史 K 线字段
  3. 轻量化：Daily 数据（~1884根），单次 NumPy 前向扫描
  4. 自洽完备：模块内自行计算所需指标（EMA200 / ATR20 / SMA20 等），不依赖外部字段

BC 识别参数（由 Task 4.1b-study 固化）：
  路径A（放量型）:
    rise_window=120d, min_rise=30%, vol_ma_window=60d, vol_mult=2.5x,
    atr_mult=1.5x, close_pos_max=0.75
  路径B（上冲衰竭耗尽型，2021-11-10 型）:
    rise_window=120d, min_rise=50%, vol_ma_window=60d, vol_mult=1.0x,
    atr_mult=1.8x, upper_shadow_min=55%

验收标准（Task 4.1b-final）：
  - 2026Q1: dist_cause_sufficient = False（再积累，无派发因）
  - 2021-2022: dist_cause_sufficient = True（派发确认）
  - 2023-2026 回测: 零回归（≥ 581.60%/420笔/78.1%）
"""

import numpy as np
import pandas as pd
import logging
from pandas import DataFrame

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# BC 识别参数（Task 4.1b-study 固化）
# ─────────────────────────────────────────────────────────────────────────────
_BC_RISE_WINDOW        = 120    # 上涨背景窗口（日）
_BC_A_MIN_RISE         = 0.30   # 路径A：120日最小涨幅 30%
_BC_A_VOL_MA_WINDOW    = 60     # 路径A/B：成交量均线窗口
_BC_A_VOL_MULT         = 2.5    # 路径A：成交量倍数 2.5x
_BC_A_ATR_MULT         = 1.5    # 路径A：ATR 倍数 1.5x
_BC_A_CLOSE_POS_MAX    = 0.75   # 路径A：收盘位置上限（<75%）

_BC_B_MIN_RISE         = 0.50   # 路径B：120日最小涨幅 50%
_BC_B_VOL_MULT         = 1.0    # 路径B：成交量不萎缩（>= 1.0x），静悄悄出货
_BC_B_ATR_MULT         = 1.8    # 路径B：ATR 倍数 1.8x
_BC_B_UPPER_SHADOW     = 0.55   # 路径B：上影线比例 >= 55%（买力耗尽的物理证据）

# AR 参数
_AR_MIN_BARS           = 3      # AR 高点至少在 BC 后第 3 根
_AR_MAX_BARS           = 15     # AR 高点最晚在 BC 后第 15 根
_AR_MIN_RALLY_PCT      = 0.015  # AR 最小反弹幅度（相对 BC 收盘价 +1.5%）

# UTAD 参数
_UTAD_HIGH_PCT         = 0.97   # UTAD: high >= ar_high * 97%（接近AR高点）
_UTAD_VOL_RATIO        = 0.50   # UTAD: volume < bc_volume * 50%（量缩）

# SOW 参数
_SOW_VOL_MULT          = 1.5    # SOW: volume > 20日均量 * 1.5x

# Buying Exhaustion 参数
_BE_NO_NEW_HIGH_BARS   = 10     # 渐进耗尽：连续 N 根无新高
_BE_VOL_DEC_BARS       = 5      # 渐进耗尽：连续 N 根量萎缩
_BE_EMA_BREAK_BARS     = 3      # 渐进耗尽：N 根收于 EMA200 下方


class DailyEventDetector:
    """
    Daily 级别派发事件序列探测器

    运行在每根 Daily K 线上，识别 BC → AR → UTAD → SOW 序列，
    输出 dist_cause_sufficient 作为熊市模式第二道防线。

    用法（在主策略 populate_indicators 中）：
        detector = DailyEventDetector()
        df_1d = detector.detect(df_1d)
        # 然后将字段通过 merge_informative_pair 合并到 1H DataFrame
    """

    def __init__(self):
        # BC 路径A 参数
        self.bc_a_rise_window     = _BC_RISE_WINDOW
        self.bc_a_min_rise        = _BC_A_MIN_RISE
        self.bc_a_vol_ma_window   = _BC_A_VOL_MA_WINDOW
        self.bc_a_vol_mult        = _BC_A_VOL_MULT
        self.bc_a_atr_mult        = _BC_A_ATR_MULT
        self.bc_a_close_pos_max   = _BC_A_CLOSE_POS_MAX

        # BC 路径B 参数
        self.bc_b_rise_window     = _BC_RISE_WINDOW
        self.bc_b_min_rise        = _BC_B_MIN_RISE
        self.bc_b_vol_ma_window   = _BC_A_VOL_MA_WINDOW
        self.bc_b_vol_mult        = _BC_B_VOL_MULT
        self.bc_b_atr_mult        = _BC_B_ATR_MULT
        self.bc_b_upper_shadow    = _BC_B_UPPER_SHADOW

        # AR 参数
        self.ar_min_bars          = _AR_MIN_BARS
        self.ar_max_bars          = _AR_MAX_BARS
        self.ar_min_rally_pct     = _AR_MIN_RALLY_PCT

        # UTAD 参数
        self.utad_high_pct        = _UTAD_HIGH_PCT
        self.utad_vol_ratio       = _UTAD_VOL_RATIO

        # SOW 参数
        self.sow_vol_mult         = _SOW_VOL_MULT

        # Buying Exhaustion 参数
        self.be_no_new_high_bars  = _BE_NO_NEW_HIGH_BARS
        self.be_vol_dec_bars      = _BE_VOL_DEC_BARS
        self.be_ema_break_bars    = _BE_EMA_BREAK_BARS

    # ─────────────────────────────────────────────────────────────────────────
    # 公共入口：detect()
    # ─────────────────────────────────────────────────────────────────────────

    def detect(self, dataframe: DataFrame) -> DataFrame:
        """
        在 Daily DataFrame 上运行完整派发事件序列检测。

        参数：
            dataframe: Daily OHLCV DataFrame（需要足够的历史数据，建议 ≥ 220 根）

        返回：
            原 DataFrame + 新增的以下字段：
              daily_bc_detected, daily_bc_path,
              daily_ar_high, daily_ar_low,
              dist_sequence_progress, daily_utad_detected, daily_dist_sow_detected,
              buying_exhaustion, be_zone_high, dist_cause_sufficient
        """
        df = dataframe.copy()

        try:
            # Step 1: 计算基础指标（自洽，不依赖外部字段）
            df = self._add_indicators(df)

            # Step 2: BC 检测（双路径）
            df = self._detect_bc(df)

            # Step 3: Buying Exhaustion 检测（无 BC 型替补路径）
            df = self._detect_buying_exhaustion(df)

            # Step 4: 前向扫描 — BC → AR → UTAD → SOW 序列追踪
            df = self._scan_sequence(df)

            # Step 5: dist_cause_sufficient（使用 _scan_sequence 计算的持久状态）
            # Bug Fix #2: 替换原次逐根的 OR 计算，保证 dist_cause_sufficient 在确认后持续为 True
            df['dist_cause_sufficient'] = df['_dcs_persistent'].copy()
            df.drop(columns=['_dcs_persistent'], inplace=True, errors='ignore')

            # 统计日志
            n = len(df)
            if n > 0:
                bc_count       = int(df['daily_bc_detected'].sum())
                bc_a_count     = int((df['daily_bc_path'] == 'path_a').sum()
                                     + (df['daily_bc_path'] == 'path_ab').sum())
                bc_b_count     = int((df['daily_bc_path'] == 'path_b').sum()
                                     + (df['daily_bc_path'] == 'path_ab').sum())
                utad_count     = int(df['daily_utad_detected'].sum())
                sow_count      = int(df['daily_dist_sow_detected'].sum())
                be_count       = int(df['buying_exhaustion'].sum())
                dcs_count      = int(df['dist_cause_sufficient'].sum())
                logger.info(
                    f'Task 4.1b-final DailyEventDetector: '
                    f'BC={bc_count}(A={bc_a_count}/B={bc_b_count}) | '
                    f'UTAD={utad_count} | SOW={sow_count} | '
                    f'BuyExhaust={be_count} | '
                    f'dist_cause_sufficient={dcs_count}/{n} '
                    f'({dcs_count / n * 100:.1f}%)'
                )
        except Exception as e:
            logger.error(f'❌ DailyEventDetector.detect() 失败: {e}', exc_info=True)
            # 兼容层：所有字段初始化为安全默认值（允许多头运行）
            df = self._init_defaults(df)

        return df

    # ─────────────────────────────────────────────────────────────────────────
    # Step 1: 计算基础指标
    # ─────────────────────────────────────────────────────────────────────────

    def _add_indicators(self, df: DataFrame) -> DataFrame:
        """计算 BC/BE/UTAD/SOW 检测所需的指标（自洽）。"""
        # EMA200
        df['_d_ema200'] = df['close'].ewm(span=200, adjust=False).mean()

        # ATR20（手动计算，不依赖 ta 库）
        hl  = df['high'] - df['low']
        hc  = (df['high'] - df['close'].shift(1)).abs()
        lc  = (df['low']  - df['close'].shift(1)).abs()
        tr  = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        df['_d_atr20']     = tr.rolling(20).mean()

        # 成交量均线（20日 / 60日）
        df['_d_vol_sma20'] = df['volume'].rolling(20).mean()
        df['_d_vol_sma60'] = df['volume'].rolling(60).mean()

        # K 线基础特征
        df['_d_bar_range']      = df['high'] - df['low']
        df['_d_upper_shadow']   = (
            (df['high'] - df['close']) /
            df['_d_bar_range'].replace(0.0, np.nan)
        )
        df['_d_close_pos']      = (
            (df['close'] - df['low']) /
            df['_d_bar_range'].replace(0.0, np.nan)
        )

        # 区间内高点（前 60 根，不含当根——shift(1) 保证无前瞻）
        df['_d_high_60d']       = df['high'].rolling(60).max().shift(1)

        # 120日涨幅（前 120 根收盘 → 当根收盘）
        df['_d_rise_120d']      = (
            (df['close'] - df['close'].shift(self.bc_a_rise_window)) /
            df['close'].shift(self.bc_a_rise_window)
        )

        return df

    # ─────────────────────────────────────────────────────────────────────────
    # Step 2: BC 检测（双路径）
    # ─────────────────────────────────────────────────────────────────────────

    def _detect_bc(self, df: DataFrame) -> DataFrame:
        """
        Daily BC（Buying Climax）双路径检测。

        路径A（放量型，经典教科书）：
          ① 120日涨幅 > 30%（上涨背景）
          ② 创 60日新高（高点突破前期高点，需求旺盛）
          ③ 成交量 > 60日均量 × 2.5（极量，机构出货）
          ④ K线幅度 > ATR20 × 1.5（宽大价差）
          ⑤ 收盘位置 < 75%（收盘向下收缩，买力衰竭）

        路径B（上冲衰竭耗尽型，2021-11-10 BTC 型）：
          ① 120日涨幅 > 50%（更强的上涨背景）
          ② 创 60日新高
          ③ 成交量 > 60日均量 × 1.0（不萎缩即可，静悄悄出货）
          ④ K线幅度 > ATR20 × 1.8（宽幅，覆盖耗尽动作）
          ⑤ 上影线 >= 55%（买力耗尽的物理证据，收盘大幅低于最高价）
        """
        # ── 公共前置条件 ────────────────────────────────────────────────────
        rise_120d_ok  = df['_d_rise_120d'] > self.bc_a_min_rise      # 路径A min_rise
        rise_120d_b   = df['_d_rise_120d'] > self.bc_b_min_rise      # 路径B min_rise
        creates_60h   = df['high'] > df['_d_high_60d']                # 创 60日新高

        # ── 路径 A ──────────────────────────────────────────────────────────
        vol_ok_a      = (
            df['volume'] > df['_d_vol_sma60'] * self.bc_a_vol_mult
        )
        atr_ok_a      = df['_d_bar_range'] > df['_d_atr20'] * self.bc_a_atr_mult
        close_pos_ok_a = df['_d_close_pos'] < self.bc_a_close_pos_max

        bc_path_a = (
            rise_120d_ok &
            creates_60h  &
            vol_ok_a     &
            atr_ok_a     &
            close_pos_ok_a
        )

        # ── 路径 B ──────────────────────────────────────────────────────────
        vol_ok_b       = (
            df['volume'] > df['_d_vol_sma60'] * self.bc_b_vol_mult
        )
        atr_ok_b       = df['_d_bar_range'] > df['_d_atr20'] * self.bc_b_atr_mult
        shadow_ok_b    = df['_d_upper_shadow'] >= self.bc_b_upper_shadow

        bc_path_b = (
            rise_120d_b &
            creates_60h &
            vol_ok_b    &
            atr_ok_b    &
            shadow_ok_b
        )

        # ── 组合输出 ──────────────────────────────────────────────────────
        df['daily_bc_detected'] = (bc_path_a | bc_path_b).fillna(False).astype(bool)

        # 路径标签
        both = (bc_path_a & bc_path_b).fillna(False)
        path_series = pd.Series('none', index=df.index)
        path_series[bc_path_a & ~bc_path_b] = 'path_a'
        path_series[~bc_path_a & bc_path_b] = 'path_b'
        path_series[both]                    = 'path_ab'
        df['daily_bc_path'] = path_series

        return df

    # ─────────────────────────────────────────────────────────────────────────
    # Step 3: Buying Exhaustion 检测（渐进式耗尽顶部，无 BC 型替补路径）
    # ─────────────────────────────────────────────────────────────────────────

    def _detect_buying_exhaustion(self, df: DataFrame) -> DataFrame:
        """
        识别无明显单根 BC 的渐进式顶部形态。

        条件（同时满足）：
          ① 连续 10 根无新高：current high < rolling(10).max().shift(1)
          ② 连续 5 根量萎缩：v[i] < v[i-1] < v[i-2] < v[i-3] < v[i-4] < v[i-5]
          ③ 连续 3 根 close < EMA200：各根均收于 EMA200 下方

        物理含义：需求在没有明显气候行动棒的情况下悄然衰竭，
        随后价格慢慢滑入 EMA200 下方——这是熊市渐进启动的另一种模式。
        """
        # ① 连续 N 根无新高（相对前 N 根历史高点）
        rolling_high = df['high'].rolling(self.be_no_new_high_bars).max().shift(1)
        no_new_high  = df['high'] < rolling_high

        # ② 连续 5 根量萎缩（单调递减）
        vol_dec = (
            (df['volume'] < df['volume'].shift(1)) &
            (df['volume'].shift(1) < df['volume'].shift(2)) &
            (df['volume'].shift(2) < df['volume'].shift(3)) &
            (df['volume'].shift(3) < df['volume'].shift(4)) &
            (df['volume'].shift(4) < df['volume'].shift(5))
        )

        # ③ 连续 3 根 close < EMA200
        ema_break_each = (df['close'] < df['_d_ema200']).astype(int)
        ema_break_3    = (
            ema_break_each.rolling(self.be_ema_break_bars, min_periods=self.be_ema_break_bars)
            .min()
            .fillna(0)
            .astype(bool)
        )

        _be_signal = (no_new_high & vol_dec & ema_break_3).fillna(False).astype(bool)
        df['buying_exhaustion'] = _be_signal

        # TASK 15: BE 耗尽区间最高价（BE触发时向前回溯15根K线的最高价）
        # 物理含义：BE窗口（10根无新高+5根量萎缩+3根跌破EMA，最长约15根）的最高价
        # 是需求衰竭前的最后价格地标，作为 BE 路径下 sm_v2 的 cycle_high 锚点
        _be_window = max(self.be_no_new_high_bars, 15)
        _be_zone_peak = df['high'].rolling(_be_window, min_periods=_be_window).max()
        df['be_zone_high'] = np.where(_be_signal, _be_zone_peak, np.nan)

        return df

    # ─────────────────────────────────────────────────────────────────────────
    # Step 4: 前向扫描 — BC → AR → UTAD → SOW 序列追踪
    # ─────────────────────────────────────────────────────────────────────────

    def _scan_sequence(self, df: DataFrame) -> DataFrame:
        """
        前向扫描 Daily K 线，追踪 BC→AR→UTAD→SOW 派发序列。

        状态机：
          'none'  → 初始/重置状态
          'bc'    → BC 已发生，等待 AR 高点（3-15根窗口内）
          'ar'    → AR 高点已确认，等待 UTAD 或直通 SOW
                    - 路径1（经典）：high>=ar_high×97% 且量缩 → utad
                    - 路径2（直通）：close<ar_low + 放量阴线 → sow（跳过UTAD，2021型）
          'utad'  → UTAD 已确认，等待 SOW（跌破 AR 低点+放量阴线）
          'sow'   → SOW 已确认，派发序列完整

        重置规则：
          - 新的 BC 检测到 → 重置到 'bc' 状态（BC 优先级最高）
          - 无自动退出/重置（退出逻辑由 Task 4.1c 在策略层实现）

        daily_ar_high 含义：
          BC 后 3-15 根内的局部最高反弹高点（= 派发区上轨参考位）
          Task 4.1c 中当 close > daily_ar_high 时触发序列重置

        daily_ar_low 含义：
          BC 到 AR 高点之间的最低 low（= 派发区下轨/SOW 跌破参考位）
        """
        n = len(df)

        bc_det_v   = df['daily_bc_detected'].values
        high_v     = df['high'].values
        low_v      = df['low'].values
        close_v    = df['close'].values
        open_v     = df['open'].values
        volume_v   = df['volume'].values
        vol_sma20_v = df['_d_vol_sma20'].values
        ema200_v   = df['_d_ema200'].values

        # ── 输出数组 ─────────────────────────────────────────────────────────
        progress_arr = np.full(n, 'none', dtype=object)
        ar_high_arr  = np.full(n, np.nan, dtype=float)
        ar_low_arr   = np.full(n, np.nan, dtype=float)
        utad_arr     = np.zeros(n, dtype=bool)
        sow_arr      = np.zeros(n, dtype=bool)

        # ── 状态变量 ─────────────────────────────────────────────────────────
        state        = 'none'
        bc_idx       = -1
        bc_vol       = 0.0
        bc_close     = np.nan
        ar_high_val  = np.nan
        ar_low_val   = np.nan
        running_low  = np.inf   # 从 BC 开始的最低 low（用于确定 ar_low）
        # Bug Fix #1: exit_b 语义修正 — 需先经历 EMA200 以下才能「重回」EMA200 以上
        ema200_below_seen = False
        # Bug Fix #2: dist_cause_sufficient 双轨持久化
        # 路径1：BC 序列状态机（state 到达 utad/sow 才激活，与序列 exit 联动重置）
        dcs_active   = False
        # 路径2：Buying Exhaustion 分层门控（TASK 17）
        #   be_dcs_active       = 结构因（只有价格突破 be_zone_high_locked 或新 BC 才重置）
        #   be_zone_high_locked = BE 触发时锁定的供应上限锚点
        #   be_ema200_above_streak = EMA200 软门控计数（≥5 日关门，跌回立即重开）
        be_dcs_active          = False
        be_zone_high_locked    = np.nan   # TASK 17: 结构锚点，首次 BE 时采样，硬重置时清零
        be_ema200_below_seen   = False
        be_ema200_above_streak = 0
        dcs_arr      = np.zeros(n, dtype=bool)
        # 读取 buying_exhaustion 和 be_zone_high（Step 3 已预计算）
        be_v          = df['buying_exhaustion'].values if 'buying_exhaustion' in df.columns else np.zeros(n, dtype=bool)
        be_zone_high_v = df['be_zone_high'].values if 'be_zone_high' in df.columns else np.full(n, np.nan)

        for i in range(n):
            # EMA200 未就绪（前 200 根），跳过
            if np.isnan(ema200_v[i]):
                progress_arr[i] = 'none'
                continue

            # ── 新 BC：重置序列（优先级最高）─────────────────────────────
            if bc_det_v[i]:
                state       = 'bc'
                bc_idx      = i
                bc_vol      = volume_v[i]
                bc_close    = close_v[i]
                ar_high_val = np.nan
                ar_low_val  = np.nan
                running_low = low_v[i]          # 初始化为 BC 自身 low
                ema200_below_seen = False        # 新 BC：重置前一序列的 EMA200 跟踪
                dcs_active  = False              # 新 BC：重置持久状态
                # TASK 17: 新 BC = 新积累起点，旧分发之因物理兑现完毕，彻底清零 BE 路径
                be_dcs_active       = False
                be_zone_high_locked = np.nan
                be_ema200_below_seen   = False
                be_ema200_above_streak = 0
            else:
                # ── Task 4.1c 退出条件（无新 BC 时检查）────────────────────
                # 路径A：价格收回 AR 高点以上 → 派发区物理消亡，dist_cause_sufficient 自然置 False
                # 路径B：价格重回 EMA200 以上 → 宏观结构重置，daily_choch_confirmed 自然失效
                if state != 'none':
                    exit_a = (not np.isnan(ar_high_val) and close_v[i] > ar_high_val)
                    # Bug Fix #1: exit_b 须在价格曾低于 EMA200 之后才能「重回」触发
                    # 修复前：BC 在顶部后 close>ema200 立即触发，序列从未发展
                    # 修复后：仅在经历 EMA200 跌破后的价格恢复时才触发重置
                    exit_b = (ema200_below_seen
                               and not np.isnan(ema200_v[i])
                               and close_v[i] > ema200_v[i])
                    if exit_a or exit_b:
                        state             = 'none'
                        bc_idx            = -1
                        bc_vol            = 0.0
                        bc_close          = np.nan
                        ar_high_val       = np.nan
                        ar_low_val        = np.nan
                        running_low       = np.inf
                        ema200_below_seen  = False  # 退出时同步重置
                        dcs_active        = False   # 退出时重置持久状态

            # ── 状态为非 none 时，持续追踪最低点 ────────────────────────────
            if state != 'none' and bc_idx >= 0:
                if low_v[i] < running_low:
                    running_low = low_v[i]

            # ── 追踪价格是否曾低于 EMA200（exit_b 语义基础）──────────────────
            if state != 'none' and not np.isnan(ema200_v[i]) and close_v[i] < ema200_v[i]:
                ema200_below_seen = True

            # ── TASK 17: BE 路径分层门控 ─────────────────────────────────
            # 结构硬重置：close > be_zone_high_locked（供应上限被放量突破，分发因证伪）
            if be_dcs_active and not np.isnan(be_zone_high_locked) and close_v[i] > be_zone_high_locked:
                be_dcs_active          = False
                be_zone_high_locked    = np.nan
                be_ema200_below_seen   = False
                be_ema200_above_streak = 0

            # EMA200 软门控：连续5日上方关门，跌回立即重开（不清零 be_dcs_active）
            if not np.isnan(ema200_v[i]):
                if close_v[i] < ema200_v[i]:
                    be_ema200_below_seen   = True
                    be_ema200_above_streak = 0
                elif be_ema200_below_seen and close_v[i] > ema200_v[i]:
                    be_ema200_above_streak += 1
            # be_ema200_above_streak 不再触发 be_dcs_active 重置，仅用于门控

            bars_since_bc = i - bc_idx if bc_idx >= 0 else 99999

            # ═══════════════════════════════════════════════════════════════
            # 状态：'bc' → 等待 AR 高点（BC+3 至 BC+15 窗口）
            # ═══════════════════════════════════════════════════════════════
            if state == 'bc':
                if self.ar_min_bars <= bars_since_bc <= self.ar_max_bars:
                    # 在 AR 窗口内，追踪最大反弹高点
                    if (
                        not np.isnan(bc_close) and bc_close > 0
                        and (high_v[i] - bc_close) / bc_close >= self.ar_min_rally_pct
                    ):
                        if np.isnan(ar_high_val) or high_v[i] > ar_high_val:
                            ar_high_val = high_v[i]

                elif bars_since_bc > self.ar_max_bars:
                    # AR 窗口已关闭
                    if not np.isnan(ar_high_val):
                        # AR 确认：将 running_low 记为 ar_low（从 BC 到窗口关闭的最低点）
                        ar_low_val = running_low
                        state = 'ar'
                    # else: 窗口内无有效反弹，维持 'bc' 状态继续等待
                    # （宽松策略：允许 bc 状态延长，直到下一个 BC 重置）

            # ═══════════════════════════════════════════════════════════════
            # 状态：'ar' → 等待 UTAD 或直通 SOW
            # ═══════════════════════════════════════════════════════════════
            elif state == 'ar':
                # 路径1（经典）：UTAD — high 接近 ar_high 且量缩
                if (
                    not np.isnan(ar_high_val)
                    and high_v[i] >= ar_high_val * self.utad_high_pct
                    and bc_vol > 0
                    and volume_v[i] < bc_vol * self.utad_vol_ratio
                ):
                    utad_arr[i] = True
                    state = 'utad'

                # 路径2（直通）：ar→sow — 跌破 ar_low + 放量阴线（2021型，无UTAD）
                elif (
                    not np.isnan(ar_low_val)
                    and close_v[i] < ar_low_val
                    and not np.isnan(vol_sma20_v[i])
                    and vol_sma20_v[i] > 0
                    and volume_v[i] > vol_sma20_v[i] * self.sow_vol_mult
                    and close_v[i] < open_v[i]
                ):
                    sow_arr[i] = True
                    state = 'sow'

            # ═══════════════════════════════════════════════════════════════
            # 状态：'utad' → 等待 SOW（跌破 ar_low + 放量阴线）
            # ═══════════════════════════════════════════════════════════════
            elif state == 'utad':
                if (
                    not np.isnan(ar_low_val)
                    and close_v[i] < ar_low_val
                    and not np.isnan(vol_sma20_v[i])
                    and vol_sma20_v[i] > 0
                    and volume_v[i] > vol_sma20_v[i] * self.sow_vol_mult
                    and close_v[i] < open_v[i]         # 阴线（收盘 < 开盘）
                ):
                    sow_arr[i] = True
                    state = 'sow'

            # ═══════════════════════════════════════════════════════════════
            # 状态：'sow' → 持续（等待 Task 4.1c 退出逻辑重置）
            # ═══════════════════════════════════════════════════════════════
            # （状态保持 'sow'，直到下一个 BC 自动重置）

            # ── 写入输出 ──────────────────────────────────────────────────
            # Bug Fix #2 双轨持久化：
            #   路垄1：BC 序列到达 utad/sow → dcs_active（随状态机 exit 重置）
            #   路垄2：Buying Exhaustion 独立持久 → be_dcs_active（独立 EMA200 重回重置）
            if state in ('utad', 'sow'):
                dcs_active = True
            # TASK 17: BE 触发时采样结构锚点（只在首次激活时采样，防多次 BE 覆盖低点）
            if bool(be_v[i]):
                if not be_dcs_active:          # 仅首次激活时锁定锚点
                    be_zone_high_locked = be_zone_high_v[i]
                be_dcs_active = True
            # TASK 17: dcs_arr = BC路径 OR (BE路径因 AND NOT EMA200软门控)
            be_dcs_gated = be_dcs_active and (be_ema200_above_streak >= 5)
            dcs_arr[i] = dcs_active or (be_dcs_active and not be_dcs_gated)
            progress_arr[i] = state
            ar_high_arr[i]  = ar_high_val if not np.isnan(ar_high_val) else np.nan
            ar_low_arr[i]   = ar_low_val  if not np.isnan(ar_low_val)  else np.nan

        # 写回 DataFrame
        df['dist_sequence_progress']  = progress_arr
        df['daily_ar_high']           = ar_high_arr
        df['daily_ar_low']            = ar_low_arr
        df['daily_utad_detected']     = utad_arr.astype(bool)
        df['daily_dist_sow_detected'] = sow_arr.astype(bool)
        df['_dcs_persistent']         = dcs_arr.astype(bool)

        return df

    # ─────────────────────────────────────────────────────────────────────────
    # 兼容层：初始化安全默认值
    # ─────────────────────────────────────────────────────────────────────────

    def _init_defaults(self, df: DataFrame) -> DataFrame:
        """检测失败时初始化所有输出字段为安全默认值（允许多头继续运行）。"""
        n = len(df)
        df['daily_bc_detected']      = False
        df['daily_bc_path']          = 'none'
        df['daily_ar_high']          = np.nan
        df['daily_ar_low']           = np.nan
        df['dist_sequence_progress'] = 'none'
        df['daily_utad_detected']    = False
        df['daily_dist_sow_detected'] = False
        df['buying_exhaustion']      = False
        df['be_zone_high']           = np.nan
        df['dist_cause_sufficient']  = False
        return df
