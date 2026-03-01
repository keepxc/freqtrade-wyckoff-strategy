"""
Task 2.8: Composite Man 意图识别（Composite Man Intent Detector）

威科夫理论的本质是识别机构（Composite Man）的意图。所有形态识别都是手段，
意图判断才是目的。本模块通过五个维度的加权融合，判断当前市场中机构的行为状态。

核心输出字段：
    composite_intent  : str   — 机构当前意图
                                 'accumulating'  : 主动吸筹（最优入场背景）
                                 'testing'       : 测试支撑（积累中期，可入场）
                                 'trending_up'   : 上升趋势（markup phase，可入场）
                                 'distributing'  : 主动派发（禁止入场）
                                 'trending_down' : 下降趋势（markdown phase，禁止入场）
                                 'unclear'       : 意图不明（提高入场阈值）
    intent_confidence : float — 意图置信度（0.0 - 1.0）
    rs_signal         : str   — 相对强度方向（'rising' / 'falling' / 'neutral'）
                                 当前版本固定为 'neutral'（待 informative_pairs 实现后启用）
    composite_score_raw : float — 原始综合分数（调试用，-1.0 到 +1.0）

五个判断维度（加权融合）：

    | 维度         | 基础权重 | 吸筹信号              | 派发信号              |
    |-------------|---------|---------------------|--------------------|
    | 成交量结构    | 25%     | 下跌缩量 + 上涨放量    | 上涨缩量 + 下跌放量   |
    | 价格行为      | 20%     | 横盘内低点逐渐抬高     | 横盘内高点逐渐降低    |
    | 波段结构      | 25%     | 上涨波段增大，下跌缩小  | 下跌波段增大，上涨缩小 |
    | 事件序列      | 15%     | SC + 进入积累窗口      | BC + 进入积累窗口     |
    | 相对强度(RS)  | 15%     | BTC 强于大盘          | BTC 弱于大盘         |

相对强度（RS）说明：
    当前版本的 RS 维度权重为 0（需要 TOTAL 市值数据）。
    待 informative_pairs 集成后，RS 计算方式：
        btc_return = btc_close.pct_change(20)
        total_return = total_market.pct_change(20)
        relative_strength = btc_return - total_return
        rs_signal = 'rising' if rs_slope > 0 else 'falling' if rs_slope < 0 else 'neutral'
    缺失 RS 维度时，其他四个维度的权重重新归一化。

与系统集成：
    - 在 populate_indicators 中调用（在 climatic_action_detector 之后）
    - populate_entry_trend 中：用 composite_intent_ok 作为所有入场的前置过滤条件
    - 'distributing' / 'trending_down' 意图时触发快速平仓逻辑（Task 2.10 实现）
    - 'unclear' 意图时提高入场阈值（intent_confidence > 0.5 才允许）

物理依据（威科夫第二步法则）：
    "选择走势强于大盘、处于积累阶段的优质标的。"
    机构行为具有持续性，本模块通过滚动30根K线窗口检测意图变化。
"""

import numpy as np
import pandas as pd
import logging
from pandas import DataFrame

logger = logging.getLogger(__name__)


class CompositeManIntentDetector:
    """
    威科夫 Composite Man 意图识别器 — 五维度加权融合

    通过成交量结构、价格行为、波段结构、事件序列四个维度（RS 暂缺）
    综合判断机构当前的市场操作意图。
    """

    def __init__(self, strategy=None):
        self.strategy = strategy

        # === 分析窗口参数 ===
        self.window = 30             # 主分析窗口（K线数）
        self.trend_window = 50       # 趋势判断窗口
        self.half_window = 15        # 半窗口（短期 vs 长期比较）
        self.min_periods = 8         # 滚动计算最小数据量

        # === 成交量分析参数 ===
        self.vol_ratio_acc = 1.20    # 吸筹判定门槛（上涨量/下跌量 > 1.2）
        self.vol_ratio_dis = 0.83    # 派发判定门槛（上涨量/下跌量 < 0.83）

        # === 意图判定阈值 ===
        self.trend_up_threshold   =  0.42   # trending_up 阈值（强势上涨趋势）
        self.acc_threshold        =  0.25   # accumulating 阈值
        self.testing_threshold    =  0.08   # testing 阈值（轻微吸筹）
        self.dis_threshold        = -0.25   # distributing 阈值
        self.trend_down_threshold = -0.42   # trending_down 阈值

        # === 维度权重 ===
        # RS 维度权重为 0（待 informative_pairs 后启用）
        # 其余四维度重新归一化（原权重 25/20/25/15 → 合计 85%，归一化后如下）
        self.weights = {
            'volume':  0.294,   # 原 25% / 0.85
            'price':   0.235,   # 原 20% / 0.85
            'wave':    0.294,   # 原 25% / 0.85
            'event':   0.177,   # 原 15% / 0.85
            'rs':      0.000,   # 原 15%，暂时禁用（合计 = 1.0）
        }

    # =========================================================================
    # 公开接口
    # =========================================================================

    def detect(self, dataframe: DataFrame) -> DataFrame:
        """
        主检测入口：计算所有意图相关输出字段。

        Args:
            dataframe: 含有 OHLCV + Weis Wave + SC/BC 字段的 DataFrame

        Returns:
            添加了 composite_intent、intent_confidence、rs_signal、composite_score_raw 的 DataFrame
        """
        try:
            # 计算各维度分数（均为 [-1, +1] 的 pd.Series）
            volume_score = self._analyze_volume_structure(dataframe)
            price_score  = self._analyze_price_behavior(dataframe)
            wave_score   = self._analyze_wave_structure(dataframe)
            event_score  = self._analyze_event_sequence(dataframe)

            # RS 维度：当前固定为 neutral（权重 = 0）
            rs_signal = pd.Series('neutral', index=dataframe.index, dtype=str)

            # 加权合并
            composite_score = self._combine_scores(volume_score, price_score, wave_score, event_score)

            # 映射为意图标签
            dataframe['composite_intent']    = self._score_to_intent(composite_score, dataframe)
            dataframe['intent_confidence']   = self._calc_confidence(composite_score)
            dataframe['rs_signal']           = rs_signal
            dataframe['composite_score_raw'] = composite_score.round(4)

            # 调试统计
            intent_dist = dataframe['composite_intent'].value_counts().to_dict()
            logger.info(f'✅ CompositeManIntentDetector 完成，意图分布: {intent_dist}')

        except Exception as e:
            logger.error(f'❌ CompositeManIntentDetector.detect 失败: {e}', exc_info=True)
            dataframe['composite_intent']    = 'unclear'
            dataframe['intent_confidence']   = 0.5
            dataframe['rs_signal']           = 'neutral'
            dataframe['composite_score_raw'] = 0.0

        return dataframe

    # =========================================================================
    # 维度 1：成交量结构分析
    # =========================================================================

    def _analyze_volume_structure(self, dataframe: DataFrame) -> pd.Series:
        """
        成交量结构分析（权重 29.4%）

        吸筹特征：上涨K线平均成交量 > 下跌K线平均成交量（需求主导）
        派发特征：下跌K线平均成交量 > 上涨K线平均成交量（供应主导）

        物理依据：威科夫法则2（努力与结果）—— 上涨轻松、下跌费力 = 积累；反之 = 派发。

        返回 pd.Series，值域 [-1, +1]
            +1 = 强烈吸筹信号（上涨量远高于下跌量）
            -1 = 强烈派发信号（下跌量远高于上涨量）
             0 = 中性
        """
        win = self.window
        min_p = self.min_periods

        # 区分上涨（收 >= 开）和下跌（收 < 开）K线
        is_up_bar = dataframe['close'] >= dataframe['open']

        # 上涨K线成交量：滚动平均（忽略下跌K线贡献为0）
        up_vol_sum   = dataframe['volume'].where(is_up_bar, 0.0).rolling(win, min_periods=min_p).sum()
        up_count     = is_up_bar.astype(float).rolling(win, min_periods=min_p).sum().clip(lower=1)
        avg_up_vol   = up_vol_sum / up_count

        # 下跌K线成交量：滚动平均
        down_vol_sum = dataframe['volume'].where(~is_up_bar, 0.0).rolling(win, min_periods=min_p).sum()
        down_count   = (~is_up_bar).astype(float).rolling(win, min_periods=min_p).sum().clip(lower=1)
        avg_down_vol = down_vol_sum / down_count

        # 比率：> 1 = 上涨量更大（吸筹），< 1 = 下跌量更大（派发）
        denom = avg_down_vol.replace(0, np.nan).fillna(avg_up_vol.mean() if avg_up_vol.mean() > 0 else 1.0)
        vol_ratio = avg_up_vol / denom

        # 线性归一化到 [-1, +1]
        # 1.0  → 0（中性），1.2 → +1（强吸筹），0.83 → -1（强派发）
        acc_r = self.vol_ratio_acc   # 1.20
        dis_r = self.vol_ratio_dis   # 0.83
        # 正区间 [1.0, 1.2] → [0, +1]
        # 负区间 [0.83, 1.0] → [-1, 0]
        score_pos = (vol_ratio - 1.0) / (acc_r - 1.0)
        score_neg = (vol_ratio - 1.0) / (1.0 - dis_r)
        score = score_pos.where(vol_ratio >= 1.0, score_neg)

        return score.clip(-1.0, 1.0).fillna(0.0)

    # =========================================================================
    # 维度 2：价格行为分析
    # =========================================================================

    def _analyze_price_behavior(self, dataframe: DataFrame) -> pd.Series:
        """
        价格行为分析（权重 23.5%）

        吸筹特征：近期低点高于更早低点（供应逐渐被吸收，High Low = Higher Low）
        派发特征：近期高点低于更早高点（需求逐渐被耗尽，Lower High）

        物理依据：威科夫"弹簧测试"——每次回调的低点都高于前低，说明供应正在减少。

        返回 pd.Series，值域 [-1, +1]
        """
        win      = self.window
        half_win = self.half_window
        min_p    = self.min_periods

        # 近期（半窗口）最低点 vs 全窗口最低点
        recent_low  = dataframe['low'].rolling(half_win, min_periods=3).min()
        earlier_low = dataframe['low'].rolling(win,      min_periods=min_p).min()

        # 近期（半窗口）最高点 vs 全窗口最高点
        recent_high  = dataframe['high'].rolling(half_win, min_periods=3).max()
        earlier_high = dataframe['high'].rolling(win,      min_periods=min_p).max()

        # 以收盘价为基准，计算相对变化
        close_safe = dataframe['close'].clip(lower=1e-8)

        # 低点抬高比例（正值 = 吸筹）
        low_rising   = (recent_low  - earlier_low)  / close_safe
        # 高点降低比例（正值 = 派发，符号取反后与 low_rising 合并）
        high_falling = (earlier_high - recent_high) / close_safe

        # 综合分数：低点抬高（吸筹 +1）vs 高点降低（派发 -1）
        raw = low_rising - high_falling

        # tanh 软截断到 [-1, +1]，系数 15 控制灵敏度
        score = np.tanh(raw * 15)

        return pd.Series(score, index=dataframe.index).fillna(0.0)

    # =========================================================================
    # 维度 3：波段结构分析
    # =========================================================================

    def _analyze_wave_structure(self, dataframe: DataFrame) -> pd.Series:
        """
        波段结构分析（权重 29.4%）

        使用 Weis Wave 输出字段：wave_direction、wave_displacement、wave_efficiency

        吸筹特征：上涨波段位移增大（积累后的推进力增强）
        派发特征：下跌波段位移增大（派发后的压力增强）

        物理依据：威科夫法则1（供需法则）—— 积累后上涨波动能量应逐渐增加。

        返回 pd.Series，值域 [-1, +1]
        """
        win   = self.window
        min_p = self.min_periods

        # 检查必要字段
        if 'wave_direction' not in dataframe.columns or 'wave_displacement' not in dataframe.columns:
            logger.debug('⚠️ wave_direction/wave_displacement 字段缺失，波段结构维度返回 0')
            return pd.Series(0.0, index=dataframe.index)

        is_up_wave  = dataframe['wave_direction'] == 'up'
        displacement = dataframe['wave_displacement'].abs()

        # 上涨波段的滚动平均位移
        up_disp_sum  = displacement.where(is_up_wave,  0.0).rolling(win, min_periods=min_p).sum()
        up_cnt       = is_up_wave.astype(float).rolling(win, min_periods=min_p).sum().clip(lower=1)
        avg_up_disp  = up_disp_sum / up_cnt

        # 下跌波段的滚动平均位移
        down_disp_sum = displacement.where(~is_up_wave, 0.0).rolling(win, min_periods=min_p).sum()
        down_cnt      = (~is_up_wave).astype(float).rolling(win, min_periods=min_p).sum().clip(lower=1)
        avg_down_disp = down_disp_sum / down_cnt

        # 位移比率：> 1 = 上涨波段更大（积累），< 1 = 下跌波段更大（派发）
        denom = avg_down_disp.replace(0, np.nan).fillna(1.0)
        disp_ratio = avg_up_disp / denom

        # 主分：位移比率映射到 [-1, +1]
        main_score = np.tanh((disp_ratio - 1.0) * 2.0)

        # 辅助分：上涨波段效率（如果字段存在）
        if 'wave_efficiency' in dataframe.columns:
            eff = dataframe['wave_efficiency'].fillna(0.0)
            # 上涨波段的滚动平均效率
            up_eff_sum = eff.where(is_up_wave, 0.0).rolling(win, min_periods=min_p).sum()
            avg_up_eff = up_eff_sum / up_cnt
            # 效率通常是小数（0.001 ~ 0.05），用 tanh*30 放大
            eff_score = np.tanh(avg_up_eff * 30)
            # 70% 位移 + 30% 效率
            combined = 0.70 * main_score + 0.30 * eff_score
        else:
            combined = main_score

        return pd.Series(combined, index=dataframe.index).fillna(0.0)

    # =========================================================================
    # 维度 4：事件序列分析
    # =========================================================================

    def _analyze_event_sequence(self, dataframe: DataFrame) -> pd.Series:
        """
        事件序列分析（权重 17.7%）

        使用 ClimaticActionDetector（Task 2.7）的输出字段：
          - in_post_climax_window : bool — 是否处于 SC/BC 后的有效窗口（60根K线内）
          - climatic_type         : str  — 'SC' / 'BC' / 'none'

        SC 后积累窗口内 → 强烈吸筹信号（+0.8）
        BC 后积累窗口内 → 强烈派发信号（-0.8）
        无事件           → 中性（0.0）

        物理依据：威科夫"Phase A 入口规则"—— SC 后 60 根K线是识别积累意图的最佳窗口。

        返回 pd.Series，值域 [-0.8, +0.8]
        """
        score = pd.Series(0.0, index=dataframe.index)

        # Task 2.7 输出字段（若缺失则使用默认值）
        in_window    = dataframe.get('in_post_climax_window',
                                     pd.Series(False, index=dataframe.index))
        climax_type  = dataframe.get('climatic_type',
                                     pd.Series('none', index=dataframe.index))

        # SC 后积累窗口 → +0.8（不用 1.0，因为仍需其他维度确认）
        sc_condition = in_window & (climax_type == 'SC')
        bc_condition = in_window & (climax_type == 'BC')

        score = score.where(~sc_condition,  0.8)
        score = score.where(~bc_condition, -0.8)

        return score

    # =========================================================================
    # 融合与输出
    # =========================================================================

    def _combine_scores(self,
                        volume: pd.Series,
                        price:  pd.Series,
                        wave:   pd.Series,
                        event:  pd.Series) -> pd.Series:
        """
        加权合并四个维度的分数（RS 权重为 0）。

        返回 pd.Series，值域 [-1.0, +1.0]
        """
        w = self.weights
        composite = (
            volume * w['volume'] +
            price  * w['price']  +
            wave   * w['wave']   +
            event  * w['event']
        )
        return composite.clip(-1.0, 1.0)

    def _score_to_intent(self, score: pd.Series, dataframe: DataFrame) -> pd.Series:
        """
        将综合分数映射为意图标签。

        分级规则（从高到低优先级）：
            score >  0.42 且在 EMA200 上方 → 'trending_up'
            score >  0.25                   → 'accumulating'
            score >  0.08                   → 'testing'
            score > -0.08                   → 'unclear'
            score > -0.25                   → 'distributing'（偏空测试）
            score > -0.42                   → 'distributing'
            score <= -0.42 且在 EMA200 下方 → 'trending_down'

        注意：
            'trending_up'  会在 populate_entry_trend 的 composite_intent_ok 中被允许入场。
            'distributing' 和 'trending_down' 会阻止新入场。
        """
        # 基础意图：从最宽松到最严格逐层覆盖
        intent = pd.Series('unclear', index=dataframe.index, dtype=str)

        # EMA 趋势方向
        if 'ema_200' in dataframe.columns:
            above_ema = dataframe['close'] > dataframe['ema_200']
            below_ema = ~above_ema
        else:
            above_ema = pd.Series(True,  index=dataframe.index)
            below_ema = pd.Series(False, index=dataframe.index)

        # 逐层赋值（使用布尔索引，先弱后强，后覆盖前）
        intent[score >  self.testing_threshold]   = 'testing'
        intent[score >  self.acc_threshold]        = 'accumulating'
        intent[(score > self.trend_up_threshold) & above_ema] = 'trending_up'

        intent[score < -self.testing_threshold]   = 'testing'       # 负侧轻微：仍叫 testing（偏空测试阶段）
        intent[score < -self.acc_threshold]        = 'distributing'
        intent[(score < self.trend_down_threshold) & below_ema] = 'trending_down'

        return intent

    def _calc_confidence(self, score: pd.Series) -> pd.Series:
        """
        将综合分数绝对值转换为置信度（0.0 - 1.0）。

        分数绝对值越大 → 置信度越高。
        使用线性截断（max at 0.6 对应置信度 1.0，中性 0 对应 0.0）。
        """
        confidence = (score.abs() / 0.6).clip(0.0, 1.0)
        return confidence.round(3)
