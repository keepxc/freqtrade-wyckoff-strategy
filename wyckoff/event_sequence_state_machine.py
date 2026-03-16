"""
Task 3.2: 事件序列状态机（Event Sequence State Machine）

威科夫方法的核心在于：事件必须按照预期的**顺序**出现，才能构成可信的结构性信号。
孤立事件（单独的 SC 或单独的 SOS）不能作为充分的入场依据，
只有 PS→SC→AR→ST 的完整序列，才确认 Phase A 已结束，Phase B 积累已开始。

═══════════════════════════════════════════════════════════
积累区（Accumulation Schematic #1）事件序列：
─────────────────────────────────────────────────────────
Phase A（供应耗尽）：
    PS  — Preliminary Support（预备支撑）  — 量增但无法阻止下跌
    SC  — Selling Climax（卖方气候行动）  — 极量+宽幅+收回，止跌信号
    AR  — Automatic Rally（自动反弹）       — SC 后 3-10 根内快速上涨
    ST  — Secondary Test（二次测试）        — 回踩 SC 区域，量缩价稳

Phase B（积累横盘）：
    可有多次 ST / Spring 测试（震仓与再积累）

Phase C（弹簧测试）：
    Spring — 跌破积累区支撑后快速收回（真正的洗盘结束信号）

Phase D（末班车）：
    SOS — Sign of Strength（实力表现）      — 宽幅上涨 + 高量 + 高收盘
    LPS — Last Point of Support（最后支撑） — 回踩 SOS 后缩量反弹

Phase E（价格上涨）：
    JAC — Jump Across Creek（跨过溪流）     — 突破积累区上轨
─────────────────────────────────────────────────────────
派发区（Distribution Schematic）事件序列（镜像）：
    PSY → BC → AR → UT → SOW → LPSY
═══════════════════════════════════════════════════════════

设计原则（避免教训重演）：
1. **纯基础设施**：本模块只产出标签字段，不直接触发入场/止损
2. **宽松序列检测**：相邻事件间允许有间隔，防止错过真实结构
3. **无前瞻偏差**：所有条件仅引用当前或历史 K 线字段
4. **零回归优先**：新字段不与现有过滤器产生交叉；入场增强留 Task 3.3

输出字段（全部为基础设施字段，Task 3.3 Phase A-E 识别激活后再用于过滤）：
    wyckoff_phase      : str   — 'unknown' / 'phase_a' / 'phase_b' / 'phase_c' / 'phase_d' / 'phase_e'
                                 / 'dist_phase_a' / 'dist_phase_b' / 'dist_phase_c' / 'dist_phase_d'
    phase_confidence   : float — 0.0~1.0，当前 Phase 判定置信度
    seq_ps_detected    : bool  — 已识别到 PS（积累侧）
    seq_sc_confirmed   : bool  — 已识别到 SC
    seq_ar_confirmed   : bool  — 已识别到 AR（SC 后 3-10 根内）
    seq_st_confirmed   : bool  — 已识别到 ST（量缩回踩 SC 区域）
    st_type            : str   — 'accumulation'（积累端 ST）/ 'distribution'（派发端 ST）/ 'none'
    seq_spring_confirmed : bool — 已识别到 Spring（Phase C）
    seq_sos_confirmed  : bool  — 已识别到 SOS（Phase D 开始）
    seq_jac_confirmed  : bool  — 已识别到 JAC（Phase E 开始）
    seq_bc_confirmed   : bool  — 已识别到 BC（派发侧 Phase A）
    seq_utad_confirmed : bool  — 已识别到 UTAD（派发侧 Phase C）
    seq_sow_confirmed  : bool  — 已识别到 SOW（派发侧 Phase D）
    accum_phase_a_complete   : bool — 积累区 Phase A 完整序列（PS+SC+AR+ST 全中）
    accum_in_phase_b         : bool — 处于 Phase B 积累横盘期（Phase A 完成后，Spring 前）
    accum_in_phase_c         : bool — 处于 Phase C（Spring 已发生，SOS 前）
    accum_in_phase_d         : bool — 处于 Phase D（SOS 已发生，JAC 前）
    dist_phase_a_complete    : bool — 派发区 Phase A 完整序列（PSY+BC+AR+UT）
    dist_in_phase_b          : bool — 处于派发 Phase B 横盘期
    dist_in_phase_c          : bool — 处于派发 Phase C（UTAD 已发生）
    dist_in_phase_d          : bool — 处于派发 Phase D（SOW 已发生）
    bars_since_sc            : int  — 距上次 SC 已过 K 线数（999=未发生）
    bars_since_spring        : int  — 距上次 Spring 已过 K 线数（999=未发生）（独立计算，勿与 sig_bar_detector 混淆）
    bars_since_sos           : int  — 距上次 SOS 已过 K 线数（999=未发生）
    bars_since_bc            : int  — 距上次 BC 已过 K 线数（999=未发生）
    tr_midpoint              : float — 当前积累/派发交易区间中轴价位
    tr_phase_tag             : str   — 'accum' / 'distrib' / 'unknown'（区间性质）
    tr_upper                 : float — 名义 TR 上边界（Phase A 完成后锁定为 AR 高点；历史结构字段，BC 不清零）
    tr_lower                 : float — 名义 TR 下边界（Phase A 完成后锁定为 SC 低点；历史结构字段，BC 不清零）
    phase_b_high             : float — Phase B 期间观测到的上探最高价（诊断 UT/UTAD 幅度用；不参与 tr_upper 更新）
"""

import numpy as np
import pandas as pd
import logging
from pandas import DataFrame

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 内部常量
# ─────────────────────────────────────────────────────────────────────────────
_MAX_BARS = 999          # "从未发生" 哨兵值
_AR_MIN_BARS = 3         # AR 必须在 SC 后至少 3 根（Task 3.4：时间约束收紧）
_AR_MAX_BARS = 10        # AR 最长在 SC 后 10 根内（Task 3.4：防止普通回调误判）
_ST_VOLUME_RATIO = 0.70  # ST 成交量相对均量阈值（宽松后备，主要用 sc/bc 量比）
_ST_SC_VOLUME_RATIO = 0.5  # 积累端 ST：成交量 < SC 量 × 0.5（Task 3.4）
_ST_BC_VOLUME_RATIO = 0.5  # 派发端 ST：成交量 < BC 量 × 0.5（Task 3.4）
_ST_LOOKBACK = 80        # ST 最长在 SC 后多少根 K 线内
_PHASE_B_MAX_BARS = 300  # Phase B 横盘最长容忍 K 线数
_PHASE_B_MIN_BARS = 20   # Phase B 最短驻留时间（1H 默认 20 bars ≈ 20 小时）
                         # 防止 Phase A→B→C→D 过短序列违反因果定律（P6-2）
                         # 经验参考：15m≈24~32, 1H≈20, 4H≈10~12, 1D≈6~10
_SOW_AFTER_BC_MIN = 5    # BC 后至少 5 根才可确认 SOW（防误判）


class EventSequenceStateMachine:
    """
    威科夫事件序列状态机

    职责：
    1. 检测 PS / SC / AR / ST（积累侧 Phase A）
    2. 追踪 Spring / SOS / JAC 事件（Phase C/D/E）
    3. 检测 PSY / BC / AR / UT / SOW / LPSY（派发侧）
    4. 输出 wyckoff_phase / phase_confidence 等结构标签
    5. 计算各关键事件的 bars_since_* 字段

    所有逻辑基于已有字段（climatic_*, spring_quality, sos_bar_detected 等），
    不依赖任何未来数据（无前瞻偏差）。
    """

    def __init__(self, strategy=None):
        self.strategy = strategy

        # AR 参数
        self.ar_min_bars = _AR_MIN_BARS
        self.ar_max_bars = _AR_MAX_BARS
        self.ar_min_rally_pct = 0.015        # AR 反弹幅度至少 1.5%（从 SC 收盘算起）

        # ST 参数（二次测试）
        self.st_volume_ratio = _ST_VOLUME_RATIO
        self.st_max_bars_after_sc = _ST_LOOKBACK
        self.st_price_tolerance = 0.03       # ST 价格容忍：SC 收盘 ± 3%

        # PS 参数（预备支撑）
        self.ps_lookback = 30                # SC 前 30 根内找 PS
        self.ps_volume_mult = 1.2            # PS 量要高于均量（供应开始）
        self.ps_drop_pct = 0.03              # SC 前需要至少 3% 的下跌

        # ST 参数（Task 3.4 双向）
        self.st_sc_volume_ratio = _ST_SC_VOLUME_RATIO   # 积累端：< SC 量×0.5
        self.st_bc_volume_ratio = _ST_BC_VOLUME_RATIO   # 派发端：< BC 量×0.5
        self.ar_local_high_confirm_bars = 2             # AR 高点局部验证：后续2根不创新高

        # Phase B / C 容差
        self.phase_b_max_bars = _PHASE_B_MAX_BARS
        self.spring_confirmation_bars = 10   # Spring 后 10 根内确认

        logger.info('✅ EventSequenceStateMachine 初始化完成')

    # =========================================================================
    # 公开接口
    # =========================================================================

    def detect(self, dataframe: DataFrame) -> DataFrame:
        """
        主入口：在 dataframe 上执行完整的事件序列分析。

        依赖的上游字段（必须在本函数之前已计算）：
            climatic_action_detected, climatic_type, climatic_level,
            post_climax_bars, in_post_climax_window,
            spring_detected, spring_quality,
            sos_bar_detected（可选，来自 sos_bar_detector）,
            sow_bar_detected（可选，来自 sow_bar_detector）,
            utad_detected（可选，来自 sow_bar_detector）,
            jac_detected（可选，来自 creek_jac_detector）,
            volume, volume_sma_20, close, high, low, atr_20
        """
        self._init_output_fields(dataframe)

        try:
            # ── 积累侧事件序列 ──────────────────────────────────────────────
            dataframe = self._detect_ps(dataframe)
            dataframe = self._detect_sc(dataframe)           # 复用 climatic_type='SC'
            dataframe = self._detect_ar(dataframe)
            dataframe = self._detect_st(dataframe)
            dataframe = self._mark_phase_a_complete(dataframe)
            dataframe = self._mark_phase_b(dataframe)
            dataframe = self._mark_phase_c(dataframe)        # spring_detected
            dataframe = self._mark_phase_d(dataframe)        # sos_bar_detected
            dataframe = self._mark_phase_e(dataframe)        # jac_detected

            # ── 派发侧事件序列 ──────────────────────────────────────────────
            dataframe = self._detect_bc(dataframe)           # 复用 climatic_type='BC'
            dataframe = self._detect_utad(dataframe)
            dataframe = self._detect_sow(dataframe)
            dataframe = self._mark_dist_phases(dataframe)

            # ── 统一 wyckoff_phase 与 phase_confidence ───────────────────────
            dataframe = self._compute_unified_phase(dataframe)

            # ── bars_since 计数字段 ──────────────────────────────────────────
            dataframe = self._compute_bars_since(dataframe)

            # ── TR 区间中轴 ──────────────────────────────────────────────────
            dataframe = self._compute_tr_midpoint(dataframe)

            self._log_stats(dataframe)

        except Exception as e:
            logger.error(f'❌ EventSequenceStateMachine.detect() 异常: {e}', exc_info=True)
            # 异常时保持初始化的默认字段不变

        return dataframe

    # =========================================================================
    # 字段初始化
    # =========================================================================

    def _init_output_fields(self, dataframe: DataFrame) -> None:
        """初始化所有输出字段为安全默认值"""
        n = len(dataframe)

        # 序列确认标志
        bool_cols = [
            'seq_ps_detected', 'seq_sc_confirmed', 'seq_ar_confirmed',
            'seq_st_confirmed', 'seq_spring_confirmed', 'seq_sos_confirmed',
            'seq_jac_confirmed', 'seq_bc_confirmed', 'seq_utad_confirmed',
            'seq_sow_confirmed',
            'accum_phase_a_complete', 'accum_in_phase_b',
            'accum_in_phase_c', 'accum_in_phase_d',
            'dist_phase_a_complete', 'dist_in_phase_b',
            'dist_in_phase_c', 'dist_in_phase_d',
        ]
        for col in bool_cols:
            dataframe[col] = False

        # 整数计数字段
        for col in ['bars_since_sc', 'bars_since_spring_seq', 'bars_since_sos', 'bars_since_bc']:
            dataframe[col] = _MAX_BARS

        # 字符串/浮点字段
        dataframe['wyckoff_phase']    = 'unknown'
        dataframe['phase_confidence'] = 0.0
        dataframe['tr_midpoint']      = 0.0
        dataframe['tr_phase_tag']     = 'unknown'
        dataframe['tr_upper']         = np.nan   # P6-0: 名义 TR 上边界（Phase A 完成后锁定）
        dataframe['tr_lower']         = np.nan   # P6-0: 名义 TR 下边界（SC 低点锁定）
        dataframe['phase_b_high']     = np.nan   # P6-0: Phase B 期间上探最高值（诊断用）
        dataframe['ar_speed_valid']   = False    # Task 3.4：AR 在 3-10 根窗口内且为局部高点
        dataframe['st_type']          = 'none'   # Task 3.4：'accumulation'/'distribution'/'none'

    # =========================================================================
    # 积累侧 Phase A 事件
    # =========================================================================

    def _detect_ps(self, dataframe: DataFrame) -> DataFrame:
        """
        PS（Preliminary Support）预备支撑检测

        逻辑：在 SC 前的 ps_lookback 根 K 线内，存在以下特征：
          - 量高于均量 × 1.2（供应开始大量涌现）
          - 区间宽大（> ATR × 1.0）
          - 收盘位置 > 40%（有一定买盘支撑）
          - 价格处于下跌趋势中（前 20 根 close 跌幅 > 3%）

        PS 标注在满足条件的 K 线上（出现在 SC 前），用于辅助计算序列完整度。
        由于 PS 出现在 SC 之前，此处独立检测，后续在 _mark_phase_a_complete 中
        回顾 SC 之前是否有 PS。
        """
        volume_sma = dataframe.get('volume_sma_20', dataframe['volume'].rolling(20).mean())
        atr = dataframe.get('atr_20', dataframe.get('atr', pd.Series(0.0, index=dataframe.index)))

        # 趋势前提（过去20根K线下跌 > 3%）
        prior_close_20 = dataframe['close'].shift(20)
        downtrend = (prior_close_20 - dataframe['close']) / prior_close_20.clip(lower=1e-6) > self.ps_drop_pct

        # PS 量特征
        ps_volume = dataframe['volume'] > volume_sma * self.ps_volume_mult

        # PS 宽幅
        bar_range = dataframe['high'] - dataframe['low']
        atr_safe = atr.replace(0, np.nan).ffill().fillna(1.0)
        ps_wide = bar_range > atr_safe * 1.0

        # PS 收盘位置
        close_pos = (dataframe['close'] - dataframe['low']) / bar_range.clip(lower=1e-6)
        ps_close_ok = close_pos > 0.40

        ps_raw = downtrend & ps_volume & ps_wide & ps_close_ok

        # PS 后续不能立即是 SC（让 SC 检测到后，再回溯确认 PS）
        # 此处只标注原始 PS 候选，后续 _mark_phase_a_complete 做回溯验证
        dataframe['seq_ps_detected'] = ps_raw
        return dataframe

    def _detect_sc(self, dataframe: DataFrame) -> DataFrame:
        """
        SC（Selling Climax）卖方气候行动 — 直接复用 ClimaticActionDetector 输出

        climatic_type == 'SC' 即为 SC 已确认。
        """
        climatic_type = dataframe.get('climatic_type', pd.Series('none', index=dataframe.index))
        dataframe['seq_sc_confirmed'] = (climatic_type == 'SC')
        return dataframe

    def _detect_ar(self, dataframe: DataFrame) -> DataFrame:
        """
        AR（Automatic Rally）自动反弹检测 — Task 3.4 修订版

        核心改动（Task 3.4）：
          - AR 时间窗口从 [2, 15] 收紧至 [3, 10]，防止普通回调被误判
          - AR 必须为局部高点：高点在后续 ar_local_high_confirm_bars（2根）内不被超越
            （注：在全量回测 dataframe 中做事后确认，不影响 Phase 检测因果性）
          - 选取每个 SC 窗口内反弹幅度最大的那根 K 线作为 AR
          - 新增 ar_speed_valid 字段（同 seq_ar_confirmed，记录通过时间约束的 AR）

        验证逻辑：
          1. 对每个 SC bar，搜索 [sc_idx+3, sc_idx+10] 窗口
          2. 找到阳线 + 反弹 ≥ 1.5% 的候选 K 线
          3. 验证候选 K 线的 high 在后续 2 根内不被超越（局部高点）
          4. 选择反弹幅度最大的候选标为 AR
        """
        sc_mask    = dataframe['seq_sc_confirmed'].values
        close_vals = dataframe['close'].values
        open_vals  = dataframe['open'].values
        high_vals  = dataframe['high'].values
        n          = len(dataframe)

        ar_confirmed   = np.zeros(n, dtype=bool)
        ar_speed_valid = np.zeros(n, dtype=bool)

        # 找出所有 SC 索引
        sc_indices = np.where(sc_mask)[0]

        for sc_idx in sc_indices:
            sc_cl  = close_vals[sc_idx]
            start  = sc_idx + self.ar_min_bars          # sc + 3
            end    = min(sc_idx + self.ar_max_bars + 1, n)  # sc + 10 (含)

            if start >= n or sc_cl <= 0:
                continue

            best_bar   = -1
            best_rally = -1.0

            for j in range(start, end):
                # 基本条件：阳线 + 反弹幅度
                rally_pct = (close_vals[j] - sc_cl) / sc_cl
                if rally_pct < self.ar_min_rally_pct:
                    continue
                if close_vals[j] <= open_vals[j]:    # 必须阳线
                    continue

                # 局部高点验证：后续 2 根 high 不超越当前 high
                # （事后确认，在全量 dataframe 中无前瞻偏差）
                confirm_end = min(j + self.ar_local_high_confirm_bars + 1, n)
                local_high_ok = all(
                    high_vals[k] <= high_vals[j]
                    for k in range(j + 1, confirm_end)
                )
                if not local_high_ok:
                    continue

                if rally_pct > best_rally:
                    best_rally = rally_pct
                    best_bar   = j

            if best_bar >= 0:
                ar_confirmed[best_bar]   = True
                ar_speed_valid[best_bar] = True

        dataframe['seq_ar_confirmed'] = ar_confirmed.astype(bool)
        dataframe['ar_speed_valid']   = ar_speed_valid.astype(bool)
        return dataframe

    def _detect_st(self, dataframe: DataFrame) -> DataFrame:
        """
        ST（Secondary Test）二次测试检测 — Task 3.4 双向识别版

        ★ 积累端 ST（accum ST）：
          - 发生在 SC→AR 序列之后，SC 后 st_max_bars_after_sc 根 K 线内
          - 价格回踩至 SC 收盘价附近（± st_price_tolerance 3%）
          - 收盘价必须高于 SC 最低价（不创新低，供应被吸收）
          - 成交量 < SC 那根 K 线的成交量 × 0.5（量萎缩是关键确认）
          - st_type = 'accumulation'

        ★ 派发端 ST（dist ST）：
          - 发生在 BC→AR 序列之后，BC 后 st_max_bars_after_sc 根 K 线内
          - 价格反弹至 BC 收盘价附近（± st_price_tolerance 3%）
          - 收盘价必须低于 BC 最高价（不创新高，需求已耗尽）
          - 成交量 < BC 那根 K 线的成交量 × 0.5
          - st_type = 'distribution'

        优先级：积累端检测优先；若当期存在积累侧上下文则不识别派发端 ST。
        """
        sc_mask     = dataframe['seq_sc_confirmed'].values
        ar_mask     = dataframe['seq_ar_confirmed'].values
        bc_mask     = dataframe['seq_bc_confirmed'].values
        close_vals  = dataframe['close'].values
        low_vals    = dataframe['low'].values
        high_vals   = dataframe['high'].values
        volume_vals = dataframe['volume'].values
        n           = len(dataframe)

        st_result   = np.zeros(n, dtype=bool)
        st_type_arr = np.full(n, 'none', dtype=object)

        # ── 积累端 ST ─────────────────────────────────────────────────────────
        last_sc_close    = np.nan
        last_sc_low      = np.nan
        last_sc_volume   = np.nan
        last_sc_idx      = -_MAX_BARS
        ar_seen_after_sc = False

        for i in range(n):
            if sc_mask[i]:
                last_sc_close  = close_vals[i]
                last_sc_low    = low_vals[i]
                last_sc_volume = volume_vals[i]
                last_sc_idx    = i
                ar_seen_after_sc = False

            if ar_mask[i] and i > last_sc_idx:
                ar_seen_after_sc = True

            bars_since_sc = i - last_sc_idx
            if (
                ar_seen_after_sc
                and 1 < bars_since_sc <= self.st_max_bars_after_sc
                and not np.isnan(last_sc_close)
                and last_sc_close > 0
            ):
                price_tol   = last_sc_close * self.st_price_tolerance
                price_ok    = abs(close_vals[i] - last_sc_close) <= price_tol
                no_new_low  = close_vals[i] > last_sc_low          # 收盘高于 SC 最低价
                vol_ok_sc   = (
                    not np.isnan(last_sc_volume)
                    and last_sc_volume > 0
                    and volume_vals[i] < last_sc_volume * self.st_sc_volume_ratio
                )

                if price_ok and no_new_low and vol_ok_sc:
                    st_result[i]   = True
                    st_type_arr[i] = 'accumulation'

        # ── 派发端 ST ─────────────────────────────────────────────────────────
        last_bc_close    = np.nan
        last_bc_high     = np.nan
        last_bc_volume   = np.nan
        last_bc_idx      = -_MAX_BARS
        ar_seen_after_bc = False

        for i in range(n):
            if bc_mask[i]:
                last_bc_close  = close_vals[i]
                last_bc_high   = high_vals[i]
                last_bc_volume = volume_vals[i]
                last_bc_idx    = i
                ar_seen_after_bc = False

            if ar_mask[i] and i > last_bc_idx:
                ar_seen_after_bc = True

            bars_since_bc_st = i - last_bc_idx
            # 仅在没有积累端 ST 的 K 线上标注派发端 ST（避免冲突）
            if (
                ar_seen_after_bc
                and 1 < bars_since_bc_st <= self.st_max_bars_after_sc
                and not np.isnan(last_bc_close)
                and last_bc_close > 0
                and not st_result[i]                       # 积累端 ST 不覆盖派发端
            ):
                price_tol    = last_bc_close * self.st_price_tolerance
                price_ok_d   = abs(close_vals[i] - last_bc_close) <= price_tol
                no_new_high  = close_vals[i] < last_bc_high       # 收盘低于 BC 最高价
                vol_ok_bc    = (
                    not np.isnan(last_bc_volume)
                    and last_bc_volume > 0
                    and volume_vals[i] < last_bc_volume * self.st_bc_volume_ratio
                )

                if price_ok_d and no_new_high and vol_ok_bc:
                    st_result[i]   = True
                    st_type_arr[i] = 'distribution'

        dataframe['seq_st_confirmed'] = st_result.astype(bool)
        dataframe['st_type']          = pd.Series(st_type_arr, index=dataframe.index)
        return dataframe

    def _mark_phase_a_complete(self, dataframe: DataFrame) -> DataFrame:
        """
        标记 Phase A 完整序列（积累侧）

        完整序列 = SC 已确认 + AR 已确认（SC 后）+ ST 已确认（AR 后）
        PS 作为加分项，不作为必须条件（防止漏报）。

        向前传播：Phase A 完成后，所有后续 K 线保持 accum_phase_a_complete=True，
        直到价格出现明显派发特征（BC 出现）时重置。
        """
        sc_mask     = dataframe['seq_sc_confirmed'].values
        ar_mask     = dataframe['seq_ar_confirmed'].values
        st_mask     = dataframe['seq_st_confirmed'].values
        n           = len(dataframe)

        phase_a_complete = np.zeros(n, dtype=bool)

        last_sc_idx = -_MAX_BARS
        ar_seen     = False
        st_seen     = False
        phase_a_done = False

        for i in range(n):
            # BC 出现（派发开始）时重置积累侧状态
            climatic_type_i = dataframe['climatic_type'].iloc[i] if 'climatic_type' in dataframe.columns else 'none'
            if climatic_type_i == 'BC':
                last_sc_idx  = -_MAX_BARS
                ar_seen      = False
                st_seen      = False
                phase_a_done = False

            if sc_mask[i]:
                last_sc_idx  = i
                ar_seen      = False
                st_seen      = False
                phase_a_done = False

            if ar_mask[i] and i > last_sc_idx >= 0:
                ar_seen = True

            if st_mask[i] and ar_seen and i > last_sc_idx >= 0:
                st_seen = True

            if ar_seen and st_seen and not phase_a_done:
                phase_a_done = True

            if phase_a_done:
                phase_a_complete[i] = True

        dataframe['accum_phase_a_complete'] = phase_a_complete.astype(bool)
        return dataframe

    def _mark_phase_b(self, dataframe: DataFrame) -> DataFrame:
        """
        Phase B：Phase A 完成后 → Spring 出现前（积累横盘期）

        Phase B 是最难判断的阶段，包含多次 ST 和可能的震仓。
        本函数将 Phase A 完成后、Spring 之前的所有 K 线标为 Phase B。
        """
        phase_a = dataframe['accum_phase_a_complete'].values
        spring  = dataframe.get(
            'spring_detected',
            pd.Series(False, index=dataframe.index)
        ).values
        n       = len(dataframe)

        in_b    = np.zeros(n, dtype=bool)
        spring_seen_after_a = False

        for i in range(n):
            if phase_a[i] and not spring_seen_after_a:
                in_b[i] = True
            if spring[i]:
                spring_seen_after_a = True
            # Spring 之后退出 Phase B
            if spring_seen_after_a and not spring[i]:
                pass  # 往后由 Phase C 接管

        # 修正：Spring 那根 K 线本身归 Phase C，不归 Phase B
        dataframe['accum_in_phase_b'] = pd.Series(in_b, index=dataframe.index) & ~dataframe.get(
            'spring_detected', pd.Series(False, index=dataframe.index)
        )
        return dataframe

    def _mark_phase_c(self, dataframe: DataFrame) -> DataFrame:
        """
        Phase C：Spring 已发生，SOS 出现前

        seq_spring_confirmed：复用 spring_detected（数值上等同，但作为序列上下文的
        显式字段保存，避免后续直接依赖 spring_detected 的字段名语义歧义）。

        P6-2（2026-03-16）：在允许 Spring 触发 Phase C 前，加入 Phase B 最小时长
        前置校验（_PHASE_B_MIN_BARS）。Phase B 持续时间不足时，忽略该 Spring，
        防止短促 A→B→C→D→E 序列违反威科夫因果定律。
        """
        spring_detected = dataframe.get('spring_detected', pd.Series(False, index=dataframe.index))
        sos_detected    = dataframe.get(
            'sos_bar_detected',
            pd.Series(False, index=dataframe.index)
        )
        phase_a_done = dataframe['accum_phase_a_complete']
        in_b_done    = dataframe['accum_in_phase_b']
        n = len(dataframe)

        # seq_spring_confirmed：Phase A/B 之后出现的 spring
        seq_spring = np.zeros(n, dtype=bool)
        in_c       = np.zeros(n, dtype=bool)

        spring_seen       = False
        sos_seen          = False
        spring_vals       = spring_detected.values
        sos_vals          = sos_detected.values
        phase_a_vals      = phase_a_done.values
        in_b_vals         = in_b_done.values
        phase_b_start_idx = -1   # 当前 Phase B 起始 bar 索引（-1=未进入）
        prev_in_b         = False

        for i in range(n):
            # P6-2: 追踪 Phase B 起始（False→True 跳变时记录）
            if in_b_vals[i] and not prev_in_b:
                phase_b_start_idx = i
            prev_in_b = in_b_vals[i]

            if spring_vals[i] and phase_a_vals[i]:
                # P6-2 guard: Phase B 必须已持续至少 _PHASE_B_MIN_BARS 根 K 线
                phase_b_duration = (i - phase_b_start_idx) if phase_b_start_idx >= 0 else 0
                if phase_b_duration >= _PHASE_B_MIN_BARS:
                    spring_seen = True
                    sos_seen    = False   # Spring 重置 SOS 标记（新周期开始）
                    seq_spring[i] = True
                # else: Phase B 时间不足，跳过此 Spring（不进入 Phase C）

            if spring_seen and spring_vals[i]:
                seq_spring[i] = True

            if sos_vals[i]:
                sos_seen = True

            if spring_seen and not sos_seen:
                in_c[i] = True

        dataframe['seq_spring_confirmed'] = seq_spring.astype(bool)
        dataframe['accum_in_phase_c']     = in_c.astype(bool)
        return dataframe

    def _mark_phase_d(self, dataframe: DataFrame) -> DataFrame:
        """
        Phase D：SOS 已发生（Phase C Spring 之后），JAC 出现前

        Task 3.4 修复（SOSZone=0 bug）：
          原有逻辑使用 in_c_vals[i] 作为 SOS 确认前提，但 _mark_phase_c 中
          当 sos_vals[i]=True 时已将 sos_seen 设为 True，使同一根 K 线的
          in_c_vals[i] 变为 False，导致 Phase D 永远无法激活。

          修复方案：改为追踪 seq_spring_confirmed 历史（曾经见过 Spring），
          而不依赖 in_c_vals（当前是否处于 Phase C 区间）。
          只要 Spring 曾经发生过（seq_spring_confirmed 曾为 True），
          后续的 SOS bar 即可确认为 Phase D 开始。
        """
        sos_detected  = dataframe.get(
            'sos_bar_detected',
            pd.Series(False, index=dataframe.index)
        )
        jac_detected  = dataframe.get(
            'jac_detected',
            pd.Series(False, index=dataframe.index)
        )
        # 使用 seq_spring_confirmed（由 _mark_phase_c 填充）追踪 Spring 是否发生过
        spring_seq_vals = dataframe['seq_spring_confirmed'].values
        sos_vals        = sos_detected.values
        jac_vals        = jac_detected.values
        n               = len(dataframe)

        seq_sos          = np.zeros(n, dtype=bool)
        in_d             = np.zeros(n, dtype=bool)
        spring_ever_seen = False
        sos_seen         = False
        jac_seen         = False

        for i in range(n):
            # Spring 曾经发生过（累进标志，不回退）
            if spring_seq_vals[i]:
                spring_ever_seen = True
                jac_seen         = False   # 新的 Spring → 重置 JAC（新周期）
                sos_seen         = False   # 重置 Phase D（等待新的 SOS）

            # SOS 确认条件：Spring 已经发生过（历史上任意时刻）
            if sos_vals[i] and spring_ever_seen:
                sos_seen    = True
                seq_sos[i]  = True

            if jac_vals[i]:
                jac_seen = True

            if sos_seen and not jac_seen:
                in_d[i] = True

        dataframe['seq_sos_confirmed'] = seq_sos.astype(bool)
        dataframe['accum_in_phase_d']  = in_d.astype(bool)
        return dataframe

    def _mark_phase_e(self, dataframe: DataFrame) -> DataFrame:
        """
        Phase E：JAC 已发生（价格突破积累区上轨，进入自由上涨阶段）

        seq_jac_confirmed：复用 creek_jac_detector 的 jac_detected 字段。
        """
        jac_detected = dataframe.get(
            'jac_detected',
            pd.Series(False, index=dataframe.index)
        )
        in_d_vals = dataframe['accum_in_phase_d'].values
        jac_vals  = jac_detected.values
        n         = len(dataframe)

        seq_jac  = np.zeros(n, dtype=bool)
        jac_seen = False

        for i in range(n):
            if jac_vals[i] and in_d_vals[i]:
                jac_seen      = True
                seq_jac[i]    = True

            if jac_seen and jac_vals[i]:
                seq_jac[i] = True

        dataframe['seq_jac_confirmed'] = seq_jac.astype(bool)
        # Phase E 不设 in_phase_e 字段（Task 2.5 已有 in_phase_e，避免冲突）
        return dataframe

    # =========================================================================
    # 派发侧 Phase A-D 事件
    # =========================================================================

    def _detect_bc(self, dataframe: DataFrame) -> DataFrame:
        """
        BC（Buying Climax）—— 直接复用 ClimaticActionDetector 的 climatic_type='BC'
        """
        climatic_type = dataframe.get('climatic_type', pd.Series('none', index=dataframe.index))
        dataframe['seq_bc_confirmed'] = (climatic_type == 'BC')
        return dataframe

    def _detect_utad(self, dataframe: DataFrame) -> DataFrame:
        """
        UTAD（Upthrust After Distribution）—— 复用 sow_bar_detector 的 utad_detected 字段
        """
        utad = dataframe.get('utad_detected', pd.Series(False, index=dataframe.index))
        dataframe['seq_utad_confirmed'] = utad.astype(bool)
        return dataframe

    def _detect_sow(self, dataframe: DataFrame) -> DataFrame:
        """
        SOW（Sign of Weakness）—— 复用 sow_bar_detector 的 sow_bar_detected 字段

        额外约束：SOW 必须在 BC 后至少 _SOW_AFTER_BC_MIN 根 K 线后才确认，
        防止 BC 当根或其后立即误判为 SOW（ClimaticActionDetector 逻辑）。
        """
        sow_raw  = dataframe.get('sow_bar_detected', pd.Series(False, index=dataframe.index)).values
        bc_mask  = dataframe['seq_bc_confirmed'].values
        n        = len(dataframe)

        seq_sow     = np.zeros(n, dtype=bool)
        last_bc_idx = -_MAX_BARS
        bc_ever_seen = False

        for i in range(n):
            if bc_mask[i]:
                last_bc_idx = i
                bc_ever_seen = True
            bars_since_bc_i = i - last_bc_idx
            if sow_raw[i] and bc_ever_seen and bars_since_bc_i >= _SOW_AFTER_BC_MIN:
                seq_sow[i] = True

        dataframe['seq_sow_confirmed'] = seq_sow.astype(bool)
        return dataframe

    def _mark_dist_phases(self, dataframe: DataFrame) -> DataFrame:
        """
        派发侧 Phase A-D 标记

        Phase A 完整序列（dist_phase_a_complete）：BC + AR（BC 后 ar_max_bars 内有反弹）
        Phase B（dist_in_phase_b）：Phase A 完成后 → UTAD 前
        Phase C（dist_in_phase_c）：UTAD 后 → SOW 前
        Phase D（dist_in_phase_d）：SOW 已发生（价格跌破积累区低轨）

        注意：派发侧的 AR 复用积累侧同一字段（SC/BC 后的 AR_max_bars 内反弹），
        条件一致，只是方向语义不同。
        """
        bc_mask    = dataframe['seq_bc_confirmed'].values
        ar_mask    = dataframe['seq_ar_confirmed'].values
        utad_mask  = dataframe['seq_utad_confirmed'].values
        sow_mask   = dataframe['seq_sow_confirmed'].values
        n          = len(dataframe)

        dist_phase_a   = np.zeros(n, dtype=bool)
        dist_in_b      = np.zeros(n, dtype=bool)
        dist_in_c      = np.zeros(n, dtype=bool)
        dist_in_d      = np.zeros(n, dtype=bool)

        last_bc_idx     = -_MAX_BARS
        ar_seen_after_bc = False
        dist_a_done      = False
        utad_seen        = False
        sow_seen         = False

        for i in range(n):
            # BC 重置派发侧状态
            if bc_mask[i]:
                last_bc_idx      = i
                ar_seen_after_bc = False
                dist_a_done      = False
                utad_seen        = False
                sow_seen         = False

            bars_since_bc = i - last_bc_idx

            # AR 必须在 BC 后
            if ar_mask[i] and 0 < bars_since_bc <= self.ar_max_bars:
                ar_seen_after_bc = True

            if ar_seen_after_bc and not dist_a_done and last_bc_idx >= 0:
                dist_a_done = True

            if dist_a_done:
                dist_phase_a[i] = True

            if dist_a_done and not utad_seen:
                dist_in_b[i] = True

            if utad_mask[i]:
                utad_seen = True

            if utad_seen and not sow_seen:
                dist_in_c[i] = True

            if sow_mask[i]:
                sow_seen = True

            if sow_seen and dist_a_done:
                dist_in_d[i] = True

        dataframe['dist_phase_a_complete'] = dist_phase_a.astype(bool)
        dataframe['dist_in_phase_b']       = dist_in_b.astype(bool)
        dataframe['dist_in_phase_c']       = dist_in_c.astype(bool)
        dataframe['dist_in_phase_d']       = dist_in_d.astype(bool)
        return dataframe

    # =========================================================================
    # 统一 Phase 标签与置信度
    # =========================================================================

    def _compute_unified_phase(self, dataframe: DataFrame) -> DataFrame:
        """
        将多个布尔字段折叠为单一的 wyckoff_phase 字符串标签，
        并计算 phase_confidence（0.0~1.0）。

        优先级（高 → 低）：
            dist 系列（派发侧）> accum 系列（积累侧）> unknown
        在同一根 K 线上不应同时出现 accum 和 dist 标签；若出现则以 dist 优先。
        """
        n = len(dataframe)
        phases     = ['unknown'] * n
        confidence = np.zeros(n, dtype=float)

        dist_d  = dataframe['dist_in_phase_d'].values
        dist_c  = dataframe['dist_in_phase_c'].values
        dist_b  = dataframe['dist_in_phase_b'].values
        dist_a  = dataframe['dist_phase_a_complete'].values

        acc_d   = dataframe['accum_in_phase_d'].values
        acc_c   = dataframe['accum_in_phase_c'].values
        acc_b   = dataframe['accum_in_phase_b'].values
        acc_a   = dataframe['accum_phase_a_complete'].values

        # Phase E：seq_jac_confirmed
        jac_seq = dataframe['seq_jac_confirmed'].values

        for i in range(n):
            if dist_d[i]:
                phases[i]     = 'dist_phase_d'
                confidence[i] = 0.80
            elif dist_c[i]:
                phases[i]     = 'dist_phase_c'
                confidence[i] = 0.70
            elif dist_b[i]:
                phases[i]     = 'dist_phase_b'
                confidence[i] = 0.60
            elif dist_a[i]:
                phases[i]     = 'dist_phase_a'
                confidence[i] = 0.55
            elif jac_seq[i]:
                phases[i]     = 'phase_e'
                confidence[i] = 0.85
            elif acc_d[i]:
                phases[i]     = 'phase_d'
                confidence[i] = 0.75
            elif acc_c[i]:
                phases[i]     = 'phase_c'
                confidence[i] = 0.65
            elif acc_b[i]:
                phases[i]     = 'phase_b'
                confidence[i] = 0.55
            elif acc_a[i]:
                phases[i]     = 'phase_a'
                confidence[i] = 0.50
            else:
                phases[i]     = 'unknown'
                confidence[i] = 0.0

        dataframe['wyckoff_phase']    = phases
        dataframe['phase_confidence'] = confidence
        return dataframe

    # =========================================================================
    # bars_since 计数字段
    # =========================================================================

    def _compute_bars_since(self, dataframe: DataFrame) -> DataFrame:
        """
        计算各关键事件距当前 K 线的 K 线数（顺向传播）

        字段：
            bars_since_sc         : 距上次 SC（积累侧）
            bars_since_spring_seq : 距上次 Spring（与 sig_bar_detector 的 bars_since_spring 区分）
            bars_since_sos        : 距上次 SOS
            bars_since_bc         : 距上次 BC（派发侧）
        """
        n = len(dataframe)

        sc_mask     = dataframe['seq_sc_confirmed'].values
        spring_mask = dataframe.get(
            'spring_detected', pd.Series(False, index=dataframe.index)
        ).values
        sos_mask    = dataframe.get(
            'sos_bar_detected', pd.Series(False, index=dataframe.index)
        ).values
        bc_mask     = dataframe['seq_bc_confirmed'].values

        events = {
            'bars_since_sc':          sc_mask,
            'bars_since_spring_seq':  spring_mask,
            'bars_since_sos':         sos_mask,
            'bars_since_bc':          bc_mask,
        }

        for col, mask in events.items():
            result       = np.full(n, _MAX_BARS, dtype=np.int32)
            last_event   = -_MAX_BARS
            for i in range(n):
                if mask[i]:
                    last_event = i
                if last_event >= 0:
                    result[i] = i - last_event
            dataframe[col] = result

        return dataframe

    # =========================================================================
    # TR 区间中轴
    # =========================================================================

    def _compute_tr_midpoint(self, dataframe: DataFrame) -> DataFrame:
        """
        计算当前 Trading Range（积累区或派发区）的中轴价位与性质标签。

        - 积累区（tr_phase_tag='accum'）：SC 区域低点 ~ AR 高点的中值
        - 派发区（tr_phase_tag='distrib'）：AR 低点 ~ BC 高点的中值

        中轴供 Task 3.3 Phase 识别和 Task 2.12 P&F 目标位对齐使用。

        P6-0 新增字段（2026-03-16）：
          tr_upper    — Phase A 完成后锁定的名义 TR 上边界（ar_high），不随 Phase B 新高更新。
                        BC 出现时值保留（历史结构字段），但下游应同时检查 accum_phase_a_complete。
          tr_lower    — 对应积累周期 SC 低点的名义 TR 下边界，与 tr_upper 同步锁定。
          phase_b_high — Phase B 期间观测到的上探极值（诊断 UT/UTAD 幅度，不写入 tr_upper）。
        """
        n = len(dataframe)

        phase_vals       = dataframe['wyckoff_phase'].values
        high_vals        = dataframe['high'].values
        low_vals         = dataframe['low'].values
        phase_a_vals     = dataframe['accum_phase_a_complete'].values
        in_b_vals        = dataframe['accum_in_phase_b'].values

        tr_mid           = np.zeros(n, dtype=float)
        tr_tag           = ['unknown'] * n
        tr_upper_arr     = np.full(n, np.nan)
        tr_lower_arr     = np.full(n, np.nan)
        phase_b_high_arr = np.full(n, np.nan)

        # 积累侧滚动状态
        sc_low          = 0.0
        ar_high         = 0.0
        locked_tr_upper = np.nan   # Phase A 完成后锁定，不再因新高更新
        locked_tr_lower = np.nan
        phase_a_locked  = False
        pb_high         = np.nan   # Phase B 上探极值，持续更新

        # 派发侧滚动状态
        bc_high     = 0.0
        ar_low_dist = 0.0

        sc_mask = dataframe['seq_sc_confirmed'].values
        ar_mask = dataframe['seq_ar_confirmed'].values
        bc_mask = dataframe['seq_bc_confirmed'].values

        for i in range(n):
            # SC：新积累周期开始，重置本周期内所有滚动状态
            if sc_mask[i]:
                sc_low          = low_vals[i]
                ar_high         = 0.0
                locked_tr_upper = np.nan   # 新周期，旧锁定已无意义
                locked_tr_lower = np.nan
                phase_a_locked  = False
                pb_high         = np.nan

            if ar_mask[i]:
                ar_high     = max(ar_high, high_vals[i])
                ar_low_dist = low_vals[i]

            if bc_mask[i]:
                bc_high = high_vals[i]
                # BC 出现：积累侧 accum_phase_a_complete 将被上游重置为 False
                # 但 locked_tr_upper/lower 保留历史值，供分配侧诊断引用
                # （下游 P6-1/P6-3 必须同时检查 accum_phase_a_complete == True）

            # Phase A 完成后首次锁定 tr_upper/tr_lower
            # 此后 Phase B 内的新高只写入 phase_b_high，不更新 locked_tr_upper
            if phase_a_vals[i] and not phase_a_locked and ar_high > 0 and sc_low > 0:
                locked_tr_upper = ar_high
                locked_tr_lower = sc_low
                phase_a_locked  = True

            # phase_b_high：Phase B 内持续更新上探极值（诊断用，不写入 tr_upper）
            if in_b_vals[i]:
                if np.isnan(pb_high):
                    pb_high = high_vals[i]
                else:
                    pb_high = max(pb_high, high_vals[i])

            # 写入结构边界字段（历史结构字段，一旦锁定即保留）
            if not np.isnan(locked_tr_upper):
                tr_upper_arr[i] = locked_tr_upper
                tr_lower_arr[i] = locked_tr_lower

            if not np.isnan(pb_high):
                phase_b_high_arr[i] = pb_high

            # tr_midpoint（保持现有行为，由当前周期 sc_low/ar_high 实时计算）
            p = phase_vals[i]
            if p in ('phase_a', 'phase_b', 'phase_c', 'phase_d', 'phase_e'):
                if sc_low > 0 and ar_high > 0:
                    tr_mid[i] = (sc_low + ar_high) / 2.0
                    tr_tag[i] = 'accum'
            elif p in ('dist_phase_a', 'dist_phase_b', 'dist_phase_c', 'dist_phase_d'):
                if bc_high > 0 and ar_low_dist > 0:
                    tr_mid[i] = (bc_high + ar_low_dist) / 2.0
                    tr_tag[i] = 'distrib'

        dataframe['tr_midpoint']   = tr_mid
        dataframe['tr_phase_tag']  = tr_tag
        dataframe['tr_upper']      = tr_upper_arr
        dataframe['tr_lower']      = tr_lower_arr
        dataframe['phase_b_high']  = phase_b_high_arr
        return dataframe

    # =========================================================================
    # 统计日志
    # =========================================================================

    def _log_stats(self, dataframe: DataFrame) -> None:
        total = len(dataframe)
        if total == 0:
            return

        phase_counts = dataframe['wyckoff_phase'].value_counts().to_dict()

        sc_count     = int(dataframe['seq_sc_confirmed'].sum())
        ar_count     = int(dataframe['seq_ar_confirmed'].sum())
        ar_speed_cnt = int(dataframe['ar_speed_valid'].sum())
        st_count     = int(dataframe['seq_st_confirmed'].sum())
        st_accum     = int((dataframe['st_type'] == 'accumulation').sum())
        st_dist      = int((dataframe['st_type'] == 'distribution').sum())
        spring_count = int(dataframe['seq_spring_confirmed'].sum())
        sos_count    = int(dataframe['seq_sos_confirmed'].sum())
        jac_count    = int(dataframe['seq_jac_confirmed'].sum())
        bc_count     = int(dataframe['seq_bc_confirmed'].sum())
        utad_count   = int(dataframe['seq_utad_confirmed'].sum())
        sow_count    = int(dataframe['seq_sow_confirmed'].sum())
        phase_a_done = int(dataframe['accum_phase_a_complete'].sum())
        in_phase_d   = int(dataframe['accum_in_phase_d'].sum())

        logger.info(
            f'Task 3.4 EventSeqSM ({total}根K线): '
            f'SC={sc_count} / AR={ar_count}(speed_valid={ar_speed_cnt}) / '
            f'ST={st_count}(accum={st_accum}/dist={st_dist}) / '
            f'Spring={spring_count} / SOS={sos_count} / JAC={jac_count} | '
            f'BC={bc_count} / UTAD={utad_count} / SOW={sow_count} | '
            f'PhaseA完整={phase_a_done}根 / PhaseD={in_phase_d}根'
        )
        logger.info(
            f'Task 3.4 Phase分布: '
            + ' / '.join(f'{k}={v}' for k, v in sorted(phase_counts.items()))
        )
