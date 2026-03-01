"""
Task 3.3: Phase A-E Context 识别器（Phase Context Detector）

═══════════════════════════════════════════════════════════════════════
职责：
  在 Task 3.2（事件序列状态机）输出的结构字段基础上，提供：
  1. Phase 感知的交易许可标签（pure infrastructure，不激活硬过滤）
  2. Phase 对齐的信号质量倍增器（Multiplier，永不降低现有质量分）
  3. Phase 精确 TR 边界（供 PNF 目标位计算替换 fallback 估算）

设计原则（避免历史教训重演）：
  ★ 倍增器原则：Phase 上下文只让好信号更好，不让差信号变好
     - phase_quality_mult ∈ [1.0, 1.3]，从不低于 1.0
     - 任何信号质量阈值不因 Phase 背景而降低
  ★ 零回归优先：所有过滤器初始设为纯基础设施（`*_ok` 字段不接入入场条件）
     - 硬过滤待 Task 3.4/3.5 行为验证通过后再激活
  ★ 无前瞻偏差：仅引用当前或历史 K 线字段
═══════════════════════════════════════════════════════════════════════

输出字段（34 个基础设施字段）：

  # ── 上下文许可（纯基础设施，当前不接入入场硬过滤）──────────────────
  phase_context_long_ok    : bool  — 处于积累结构中（phase_b/c/d/e），多头背景许可
  phase_context_short_ok   : bool  — 处于派发结构中（dist_phase_b/c/d），空头背景许可

  # ── 最优入场区域标记 ────────────────────────────────────────────────
  phase_optimal_long       : bool  — 最优多头入场区（Phase C Spring区 或 Phase D SOS区）
  phase_optimal_short      : bool  — 最优空头入场区（dist_phase_c UTAD区 或 dist_phase_d）
  phase_spring_zone        : bool  — Phase C 弹簧区（Spring 已发生，SOS 尚未出现）
  phase_sos_zone           : bool  — Phase D SOS 区（SOS 已发生，JAC 尚未出现）
  phase_trending_e         : bool  — Phase E 趋势加速区（JAC 确认后）
  phase_dist_utad_zone     : bool  — dist Phase C（UTAD 区域）
  phase_dist_sow_zone      : bool  — dist Phase D（SOW 已发生）

  # ── 信号质量倍增器（Multiplier，必须在 signal_quality 计算之后应用）──
  phase_quality_mult       : float — [1.0, 1.3]
    Phase C (Spring区)→Spring信号: ×1.30（最高可信度：Spring确认+最优区域）
    Phase D (SOS区)  →SOS/LPS信号: ×1.20（强劲动能：第一个 LPS 回踩确认）
    Phase E (JAC后)  →续涨信号:    ×1.10（趋势延续：加速期，风险较低）
    其他 Phase:                    ×1.00（无加成）

  # ── 精确 TR 边界（替换 pnf_target_calculator 的 fallback 估算）──────
  phase_tr_low             : float — 积累区精确低边界（SC Low，Phase B 以上的结构低点）
  phase_tr_high            : float — 积累区精确高边界（AR High，Phase B 以上的结构高点）
  phase_dist_low           : float — 派发区精确低边界（AR Low after BC）
  phase_dist_high          : float — 派发区精确高边界（BC High）
  phase_tr_valid           : bool  — TR 边界来自 Phase 精确计算（非 fallback）
  phase_dist_valid         : bool  — 派发 TR 边界来自 Phase 精确计算（非 fallback）

  # ── Phase 内距离计数（辅助理解仓位阶段）────────────────────────────
  bars_in_phase_b          : int   — 当前 Phase B 已持续的 K 线数（0=未在 Phase B）
  bars_in_phase_c          : int   — 当前 Phase C 已持续的 K 线数
  bars_in_phase_d          : int   — 当前 Phase D 已持续的 K 线数
  bars_in_dist_c           : int   — 当前 dist Phase C 已持续的 K 线数
  bars_in_dist_d           : int   — 当前 dist Phase D 已持续的 K 线数

  # ── 统一 Phase 摘要标签 ─────────────────────────────────────────────
  phase_context_tag        : str   — 可读摘要："accum_b" / "accum_c_spring" / "accum_d_sos" /
                                       "accum_e" / "dist_b" / "dist_c_utad" / "dist_d_sow" / "none"
"""

import numpy as np
import pandas as pd
import logging
from pandas import DataFrame

logger = logging.getLogger(__name__)

_MAX_BARS = 999
_PHASE_QUALITY_SPRING  = 1.30   # Phase C Spring 区倍增器
_PHASE_QUALITY_SOS     = 1.20   # Phase D SOS 区倍增器
_PHASE_QUALITY_E       = 1.10   # Phase E 趋势延续倍增器
_PHASE_QUALITY_DEFAULT = 1.00   # 无加成


class PhaseContextDetector:
    """
    Task 3.3: Phase A-E Context 识别器

    依赖的上游字段（必须在 detect() 之前已存在于 dataframe）：
        来自 EventSequenceStateMachine (Task 3.2)：
            wyckoff_phase, phase_confidence,
            accum_phase_a_complete, accum_in_phase_b, accum_in_phase_c, accum_in_phase_d,
            dist_phase_a_complete, dist_in_phase_b, dist_in_phase_c, dist_in_phase_d,
            seq_sc_confirmed, seq_ar_confirmed, seq_bc_confirmed,
            seq_spring_confirmed, seq_sos_confirmed, seq_jac_confirmed,
            seq_utad_confirmed, seq_sow_confirmed,
            bars_since_sc, bars_since_spring_seq, bars_since_sos, bars_since_bc,
            tr_midpoint, tr_phase_tag

        来自 ClimaticActionDetector (Task 2.7)：
            climatic_type, climatic_level  （SC Low / BC High）

        来自基础字段：
            high, low, close, open, volume
    """

    def __init__(self, strategy=None):
        self.strategy = strategy
        logger.info('✅ PhaseContextDetector (Task 3.3) 初始化完成')

    # =========================================================================
    # 公开接口
    # =========================================================================

    def detect(self, dataframe: DataFrame) -> DataFrame:
        """
        主入口：在事件序列状态机字段基础上计算 Phase Context 输出。

        调用时机：Phase 字段（Task 3.2）计算完成后、P&F 目标位计算之前。
        """
        self._init_output_fields(dataframe)

        try:
            # [1] 精确 TR 边界（Phase 结构锚点）
            self._compute_precise_tr_boundaries(dataframe)

            # [2] Phase 区域标记（spring_zone, sos_zone, trending_e 等）
            self._compute_phase_zones(dataframe)

            # [3] 上下文许可标签（pure infrastructure）
            self._compute_context_permissions(dataframe)

            # [4] 信号质量倍增器（Multiplier，不降低现有质量）
            self._compute_quality_multiplier(dataframe)

            # [5] Phase 内 K 线计数
            self._compute_bars_in_phase(dataframe)

            # [6] 统一 Phase 摘要标签（日志 + dashboard 可读）
            self._compute_phase_tag(dataframe)

            self._log_stats(dataframe)

        except Exception as e:
            logger.error(f'❌ PhaseContextDetector.detect() 异常: {e}', exc_info=True)

        return dataframe

    # =========================================================================
    # 字段初始化
    # =========================================================================

    def _init_output_fields(self, dataframe: DataFrame) -> None:
        """初始化所有输出字段为安全默认值"""
        # 上下文许可
        for col in ['phase_context_long_ok', 'phase_context_short_ok',
                    'phase_optimal_long', 'phase_optimal_short',
                    'phase_spring_zone', 'phase_sos_zone', 'phase_trending_e',
                    'phase_dist_utad_zone', 'phase_dist_sow_zone',
                    'phase_tr_valid', 'phase_dist_valid']:
            dataframe[col] = False

        # 质量倍增器（默认 1.0，不降低）
        dataframe['phase_quality_mult'] = _PHASE_QUALITY_DEFAULT

        # 精确 TR 边界
        for col in ['phase_tr_low', 'phase_tr_high', 'phase_dist_low', 'phase_dist_high']:
            dataframe[col] = 0.0

        # Phase 内 K 线计数
        for col in ['bars_in_phase_b', 'bars_in_phase_c', 'bars_in_phase_d',
                    'bars_in_dist_c', 'bars_in_dist_d']:
            dataframe[col] = 0

        # 摘要标签
        dataframe['phase_context_tag'] = 'none'

    # =========================================================================
    # [1] 精确 TR 边界
    # =========================================================================

    def _compute_precise_tr_boundaries(self, dataframe: DataFrame) -> None:
        """
        从 Phase 结构事件中提取精确的积累区/派发区边界。

        积累区：
            tr_low  = SC 当根 Low（供应耗尽点）
            tr_high = SC 后到当前根为止的最高 High（AR 确立的上轨）
            仅在 seq_sc_confirmed 已发生后才有值；Phase 未进入则保持 0

        派发区：
            dist_high = BC 当根 High（需求耗尽点，或 UTAD 修正高点）
            dist_low  = BC 后到当前根为止的最低 Low（AR 确立的下轨）
            仅在 seq_bc_confirmed 已发生后才有值
        """
        n = len(dataframe)

        sc_mask  = dataframe.get('seq_sc_confirmed',  pd.Series(False, index=dataframe.index)).values
        bc_mask  = dataframe.get('seq_bc_confirmed',  pd.Series(False, index=dataframe.index)).values
        utad_mask = dataframe.get('seq_utad_confirmed', pd.Series(False, index=dataframe.index)).values

        high_vals = dataframe['high'].values
        low_vals  = dataframe['low'].values

        phase_vals = dataframe.get('wyckoff_phase', pd.Series('unknown', index=dataframe.index)).values

        phase_tr_low  = np.zeros(n, dtype=float)
        phase_tr_high = np.zeros(n, dtype=float)
        phase_dist_low  = np.zeros(n, dtype=float)
        phase_dist_high = np.zeros(n, dtype=float)
        phase_tr_valid   = np.zeros(n, dtype=bool)
        phase_dist_valid = np.zeros(n, dtype=bool)

        # ── 积累侧 ──────────────────────────────────────────────────────────
        last_sc_low     = 0.0
        running_acc_high = 0.0

        for i in range(n):
            # 新 SC 重置积累 TR
            if sc_mask[i]:
                last_sc_low      = low_vals[i]
                running_acc_high = high_vals[i]

            # BC 出现时重置积累侧（新的派发周期开始）
            if bc_mask[i]:
                last_sc_low      = 0.0
                running_acc_high = 0.0

            if last_sc_low > 0:
                running_acc_high = max(running_acc_high, high_vals[i])
                p = phase_vals[i]
                # 只在积累 Phase 内才输出精确边界（防止越过派发周期后继续使用旧 SC 低点）
                if isinstance(p, str) and p.startswith('phase'):
                    phase_tr_low[i]   = last_sc_low
                    phase_tr_high[i]  = running_acc_high
                    phase_tr_valid[i] = True

        # ── 派发侧 ──────────────────────────────────────────────────────────
        last_bc_high    = 0.0
        running_dist_low = 0.0
        utad_adj_high   = 0.0

        for i in range(n):
            # 新 BC 重置派发 TR
            if bc_mask[i]:
                last_bc_high     = high_vals[i]
                running_dist_low = low_vals[i]
                utad_adj_high    = high_vals[i]

            if last_bc_high > 0:
                # BC 不断更新高点（直到 UTAD 固定顶部）
                if utad_adj_high == last_bc_high:
                    # UTAD 前：允许 high 上涨
                    utad_adj_high = max(utad_adj_high, high_vals[i])
                    last_bc_high  = utad_adj_high
                # SC 后积累侧出现时重置派发侧
                if sc_mask[i]:
                    last_bc_high     = 0.0
                    running_dist_low = 0.0
                    utad_adj_high    = 0.0
                else:
                    running_dist_low = min(running_dist_low, low_vals[i])

                p = phase_vals[i]
                if isinstance(p, str) and p.startswith('dist'):
                    phase_dist_high[i]  = utad_adj_high
                    phase_dist_low[i]   = running_dist_low
                    phase_dist_valid[i] = True

            # UTAD 固定派发区顶部（取 UTAD 前 20 根最高点）
            if utad_mask[i]:
                pre_utad_start = max(0, i - 20)
                utad_adj_high  = float(np.max(high_vals[pre_utad_start:i]))
                last_bc_high   = utad_adj_high  # 锁定高点

        dataframe['phase_tr_low']    = phase_tr_low
        dataframe['phase_tr_high']   = phase_tr_high
        dataframe['phase_dist_low']  = phase_dist_low
        dataframe['phase_dist_high'] = phase_dist_high
        dataframe['phase_tr_valid']  = phase_tr_valid
        dataframe['phase_dist_valid'] = phase_dist_valid

    # =========================================================================
    # [2] Phase 区域标记
    # =========================================================================

    def _compute_phase_zones(self, dataframe: DataFrame) -> None:
        """
        将 Task 3.2 的 accum_in_phase_* 字段映射为语义更清晰的区域布尔值。
        """
        in_b = dataframe.get('accum_in_phase_b', pd.Series(False, index=dataframe.index)).astype(bool)
        in_c = dataframe.get('accum_in_phase_c', pd.Series(False, index=dataframe.index)).astype(bool)
        in_d = dataframe.get('accum_in_phase_d', pd.Series(False, index=dataframe.index)).astype(bool)
        jac_seq = dataframe.get('seq_jac_confirmed', pd.Series(False, index=dataframe.index)).astype(bool)
        dist_in_c = dataframe.get('dist_in_phase_c', pd.Series(False, index=dataframe.index)).astype(bool)
        dist_in_d = dataframe.get('dist_in_phase_d', pd.Series(False, index=dataframe.index)).astype(bool)

        # Phase E：JAC 已确认（跨越积累区上轨，自由上涨）
        # 与 Task 2.5 in_phase_e 有本质区别：
        #   Task 2.5 phase_e = EMA 对齐 + 高位 + 非横盘（基于技术指标）
        #   Task 3.3 phase_trending_e = JAC 事件序列确认（基于结构事件）
        jac_col = dataframe.get('jac_detected', pd.Series(False, index=dataframe.index)).astype(bool)
        phase_e_from_jac = jac_col.rolling(30, min_periods=1).max().astype(bool)

        dataframe['phase_spring_zone']    = in_c
        dataframe['phase_sos_zone']       = in_d
        dataframe['phase_trending_e']     = phase_e_from_jac & ~in_c & ~in_d
        dataframe['phase_dist_utad_zone'] = dist_in_c
        dataframe['phase_dist_sow_zone']  = dist_in_d

    # =========================================================================
    # [3] 上下文许可标签
    # =========================================================================

    def _compute_context_permissions(self, dataframe: DataFrame) -> None:
        """
        Phase 感知型许可标签（纯基础设施，当前不接入硬过滤）。

        phase_context_long_ok  = 在积累结构中（Phase B/C/D/E），多头背景许可
        phase_context_short_ok = 在派发结构中（dist Phase B/C/D），空头背景许可

        phase_optimal_long  = Phase C（Spring 确认）或 Phase D（SOS 确认）
                              这是威科夫理论中胜算最高的两个多头入场区
        phase_optimal_short = dist Phase C（UTAD 区）或 dist Phase D（SOW 区）
        """
        in_b = dataframe.get('accum_in_phase_b',    pd.Series(False, index=dataframe.index)).astype(bool)
        in_c = dataframe['phase_spring_zone']
        in_d = dataframe['phase_sos_zone']
        phase_e = dataframe['phase_trending_e']

        dist_b = dataframe.get('dist_in_phase_b',   pd.Series(False, index=dataframe.index)).astype(bool)
        dist_c = dataframe['phase_dist_utad_zone']
        dist_d = dataframe['phase_dist_sow_zone']

        dataframe['phase_context_long_ok']  = in_b | in_c | in_d | phase_e
        dataframe['phase_context_short_ok'] = dist_b | dist_c | dist_d

        dataframe['phase_optimal_long']  = in_c | in_d          # Phase C + D = two optimal long zones
        dataframe['phase_optimal_short'] = dist_c | dist_d      # dist Phase C + D = two optimal short zones

    # =========================================================================
    # [4] 信号质量倍增器
    # =========================================================================

    def _compute_quality_multiplier(self, dataframe: DataFrame) -> None:
        """
        信号质量倍增器（Multiplier Only，永不低于 1.0）。

        优先级（由高到低）：
            Phase C（Spring 区）→ ×1.30  （Spring 确认是威科夫最高可信度结构）
            Phase D（SOS 区）   → ×1.20  （SOS 后 LPS 回踩是第二高可信度入场）
            Phase E（趋势延续） → ×1.10  （JAC 后续涨仍有结构支撑）
            其他               → ×1.00  （无加成，不降低）

        注意：派发侧暂不做空头质量倍增，等 Task 3.6 Markdown 攻击验证通过后再激活。
        """
        mult = np.ones(len(dataframe), dtype=float) * _PHASE_QUALITY_DEFAULT

        spring_zone = dataframe['phase_spring_zone'].values
        sos_zone    = dataframe['phase_sos_zone'].values
        trending_e  = dataframe['phase_trending_e'].values

        # 从低到高覆盖（高优先级在后，覆盖低优先级）
        mult[trending_e]  = _PHASE_QUALITY_E
        mult[sos_zone]    = _PHASE_QUALITY_SOS
        mult[spring_zone] = _PHASE_QUALITY_SPRING

        dataframe['phase_quality_mult'] = mult

    # =========================================================================
    # [5] Phase 内 K 线计数
    # =========================================================================

    def _compute_bars_in_phase(self, dataframe: DataFrame) -> None:
        """
        计算各 Phase 内已持续的 K 线数（顺向累计）。
        Phase 切换时重置为 0。
        """
        phase_map = {
            'bars_in_phase_b': 'accum_in_phase_b',
            'bars_in_phase_c': 'accum_in_phase_c',
            'bars_in_phase_d': 'accum_in_phase_d',
            'bars_in_dist_c':  'dist_in_phase_c',
            'bars_in_dist_d':  'dist_in_phase_d',
        }
        n = len(dataframe)

        for out_col, src_col in phase_map.items():
            src = dataframe.get(src_col, pd.Series(False, index=dataframe.index)).values
            result = np.zeros(n, dtype=np.int32)
            cnt = 0
            for i in range(n):
                if src[i]:
                    cnt += 1
                else:
                    cnt = 0
                result[i] = cnt
            dataframe[out_col] = result

    # =========================================================================
    # [6] 统一 Phase 摘要标签
    # =========================================================================

    def _compute_phase_tag(self, dataframe: DataFrame) -> None:
        """
        输出单一可读字符串，供日志和 dashboard 使用。
        优先级：dist 系列 > accum 系列 > none
        """
        n = len(dataframe)
        tags = np.full(n, 'none', dtype=object)

        spring_zone = dataframe['phase_spring_zone'].values
        sos_zone    = dataframe['phase_sos_zone'].values
        trending_e  = dataframe['phase_trending_e'].values
        in_b        = dataframe.get('accum_in_phase_b', pd.Series(False, index=dataframe.index)).values
        dist_b      = dataframe.get('dist_in_phase_b',  pd.Series(False, index=dataframe.index)).values
        dist_c      = dataframe['phase_dist_utad_zone'].values
        dist_d      = dataframe['phase_dist_sow_zone'].values

        # 从低优先级到高优先级（后覆盖前）
        tags[in_b]      = 'accum_b'
        tags[spring_zone] = 'accum_c_spring'
        tags[sos_zone]    = 'accum_d_sos'
        tags[trending_e]  = 'accum_e'
        tags[dist_b]      = 'dist_b'
        tags[dist_c]      = 'dist_c_utad'
        tags[dist_d]      = 'dist_d_sow'

        dataframe['phase_context_tag'] = tags

    # =========================================================================
    # 统计日志
    # =========================================================================

    def _log_stats(self, dataframe: DataFrame) -> None:
        total = len(dataframe)
        if total == 0:
            return

        tag_counts = dataframe['phase_context_tag'].value_counts().to_dict()

        spring_zone = int(dataframe['phase_spring_zone'].sum())
        sos_zone    = int(dataframe['phase_sos_zone'].sum())
        phase_e     = int(dataframe['phase_trending_e'].sum())
        dist_c      = int(dataframe['phase_dist_utad_zone'].sum())
        dist_d      = int(dataframe['phase_dist_sow_zone'].sum())
        tr_valid    = int(dataframe['phase_tr_valid'].sum())
        dist_valid  = int(dataframe['phase_dist_valid'].sum())

        long_ok  = int(dataframe['phase_context_long_ok'].sum())
        short_ok = int(dataframe['phase_context_short_ok'].sum())

        mult_above_1  = int((dataframe['phase_quality_mult'] > 1.0).sum())
        mult_130      = int((dataframe['phase_quality_mult'] >= 1.30).sum())

        logger.info(
            f'Task 3.3 PhaseContext ({total}根K线): '
            f'SpringZone={spring_zone} / SOSZone={sos_zone} / PhaseE={phase_e} | '
            f'DistC={dist_c} / DistD={dist_d} | '
            f'TR精确={tr_valid} / DistTR精确={dist_valid} | '
            f'LongOK={long_ok} / ShortOK={short_ok} | '
            f'QualityBoost>1.0={mult_above_1}(×1.3={mult_130})'
        )
        readable_tags = ' / '.join(f'{k}={v}' for k, v in sorted(tag_counts.items()) if k != 'none')
        if readable_tags:
            logger.info(f'Task 3.3 Phase分布: {readable_tags}')
