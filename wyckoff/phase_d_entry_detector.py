"""
Phase D 确认入场检测器 (Task 2.4)

威科夫 Phase D 是积累阶段的最后确认，代表市场参与者即将行动的信号。
该模块实现三步确认机制，确保高概率入场。

理论依据：
- Phase D 是基于"无供应"特性的价格突破和支撑反应
- 必须同时满足三个条件，才能确认是真正的 Phase D 入场机会
- Phase D 的成功率（胜率）应该 >80%

三步确认机制：
1. 价格在轴线上方维持 (price_position > 0.5)  
   - 轴线代表关键支撑位，价格在其上方意味着买方控制
   - 这是多头环境的必要条件

2. SOS Bar 确认（当前或近5根K线内）
   - SOS Bar 是供应吸收的物理证据
   - 预示着供应枯竭，卖方无力继续压低价格

3. 无供应回踩 (No Supply Bounce Back)
   - 价格在SOS后的回踩特征：成交量缩小 + 价差收窄
   - 这是"卖方疲软"的威科夫证据
   - 通常导致快速上涨（跳过供应）

实现指标：
- phase_d_confirmed: 布尔值，是否满足所有三个条件
- phase_d_confidence: 0.0-1.0 的置信度评分
- phase_d_signal_strength: 信号强度（用于比较不同的 Phase D 机会）
"""

import numpy as np
import pandas as pd
from pandas import DataFrame
from typing import Dict, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


class PhaseDEntryDetector:
    """
    Phase D 确认入场检测器
    
    输出字段：
    - phase_d_confirmed: bool，是否是有效的Phase D入场
    - phase_d_confidence: 0.0-1.0，整体置信度
    - axis_above: bool，Step 1结果
    - sos_recent: bool，Step 2结果  
    - no_supply_bounce: bool，Step 3结果
    - phase_d_quality_score: 0-100，质量评分（用于排序多个信号）
    """
    
    def __init__(self, strategy=None):
        """
        初始化 Phase D 检测器
        
        Args:
            strategy: 引用策略实例（用于获取参数和其他检测器）
        """
        self.strategy = strategy
        logger.info("✅ PhaseDEntryDetector initialized")
    
    def detect_phase_d_entry(self, dataframe: DataFrame, metadata: Dict) -> DataFrame:
        """
        检测 Phase D 确认入场信号
        
        三步确认的完整流程：
        1. 检验价格位置 (Step 1: Price Position Above Axis)
        2. 检验SOS Bar (Step 2: Strength of Supply Confirmation)
        3. 检验无供应特征 (Step 3: No Supply Bounce)
        
        Args:
            dataframe: OHLCV数据
            metadata: 交易对元数据
            
        Returns:
            更新后的dataframe，添加phase_d_*字段
        """
        
        # 初始化输出字段
        dataframe['phase_d_confirmed'] = False
        dataframe['phase_d_confidence'] = 0.0
        dataframe['axis_above'] = False
        dataframe['sos_recent'] = False
        dataframe['no_supply_bounce'] = False
        dataframe['phase_d_quality_score'] = 0
        
        # 检查必需字段
        required_fields = ['close', 'high', 'low', 'volume', 'atr']
        for field in required_fields:
            if field not in dataframe.columns:
                logger.warning(f"⚠️ Missing required field: {field}")
                return dataframe
        
        # Step 1: 检查价格在轴线上方
        axis_above = self._step1_check_price_above_axis(dataframe)
        dataframe['axis_above'] = axis_above
        
        # Step 2: 检查最近是否有 SOS Bar
        sos_recent = self._step2_check_sos_recent(dataframe)
        dataframe['sos_recent'] = sos_recent
        
        # Step 3: 检查无供应回踩特征
        no_supply_bounce = self._step3_check_no_supply_bounce(dataframe)
        dataframe['no_supply_bounce'] = no_supply_bounce
        
        # 综合判断：三个条件都满足才能确认 Phase D
        phase_d_mask = axis_above & sos_recent & no_supply_bounce
        dataframe['phase_d_confirmed'] = phase_d_mask
        
        # 计算置信度（0.0-1.0）
        dataframe['phase_d_confidence'] = self._calculate_confidence(dataframe)
        
        # 计算质量评分（0-100）
        dataframe['phase_d_quality_score'] = self._calculate_quality_score(dataframe)
        
        return dataframe
    
    def _step1_check_price_above_axis(self, dataframe: DataFrame) -> pd.Series:
        """
        Step 1: 检查价格在轴线上方
        
        轴线代表关键支撑位，价格在轴线上方意味着：
        - 买方掌控市场
        - 支撑位有效
        - 处于多头结构中
        
        如果没有轴线信息，则使用价格位置的简化检测：
        - 价格在 52 周期内上半部（即 price_position > 0.5）
        - 或者价格在近期高点附近
        """
        
        # 首先尝试使用轴线信息
        if 'nearest_support' in dataframe.columns:
            # 使用 nearest_support 表示轴线位置
            axis_above = dataframe['close'] > dataframe['nearest_support']
            
            # 但要确保 nearest_support 有效（> 0）
            valid_axis = dataframe['nearest_support'] > 0
            
            # 综合判断
            result = axis_above & valid_axis
            
            logger.debug(
                f"Step 1: Using axis system. "
                f"Above axis: {result.sum()}/{len(result)} bars"
            )
            
            return result
        else:
            # 降级方案：使用价格位置判断
            # price_position = (close - 52周期低点) / (52周期高点 - 52周期低点)
            low_52 = dataframe['low'].rolling(52).min()
            high_52 = dataframe['high'].rolling(52).max()
            
            price_position = (
                (dataframe['close'] - low_52) / 
                (high_52 - low_52 + 1e-09)
            )
            
            # 价格在上半部 (> 0.5) 表示处于多头环境
            result = price_position > 0.5
            
            logger.debug(
                f"Step 1: Using price position (52-period). "
                f"Above 50%: {result.sum()}/{len(result)} bars"
            )
            
            return result
    
    def _step2_check_sos_recent(self, dataframe: DataFrame) -> pd.Series:
        """
        Step 2: 检查最近是否有 SOS Bar
        
        SOS (Strength of Supply) Bar 是明确的供应吸收信号：
        - 强有力的上涨条，打破近期高点
        - 伴随显著成交量增加
        - 收盘接近高点，显示买方力量
        
        该方法在近 5 根 K 线内查找 SOS Bar 的存在
        时间窗口：最近 5 根 K 线（通常，LPS 在 SOS 后 2-5 根 K 线出现）
        
        如果系统中已有 sos_bar_detected 字段（来自 SOSBarDetector），
        则直接使用；否则执行简化检测。
        """
        
        sos_window = 3  # 查找窗口：最近 3 根 K 线（SOS 后 0-3 根的 LPS 才最可靠）
        sos_recent = pd.Series(False, index=dataframe.index)
        
        # 如果有 SOS Bar 检测字段（来自 Task 2.2 的 SOSBarDetector）
        if 'sos_bar_detected' in dataframe.columns:
            # 使用滚动窗口：如果最近 5 根 K 线内有 SOS，则为真
            sos_recent = (
                dataframe['sos_bar_detected'].rolling(sos_window).max() == 1
            )
            
            logger.debug(
                f"Step 2: Using sos_bar_detected field. "
                f"SOS recent: {sos_recent.sum()}/{len(sos_recent)} bars"
            )
            
            return sos_recent
        
        # 降级方案：简化的 SOS 检测
        # SOS 特征：
        # 1. 突破近期高点（突破强度 > 0.5%）
        # 2. 成交量放大（> 平均值 1.5 倍）
        # 3. 收盘强势（close_position > 0.7）
        
        for i in range(len(dataframe)):
            if i < 20:  # 需要历史数据
                continue
            
            # 1. 突破检测
            recent_high = dataframe['high'].iloc[max(0, i - 10):i].max()
            breakout = dataframe['close'].iloc[i] > recent_high * 1.005
            
            # 2. 成交量放大
            avg_volume = dataframe['volume'].iloc[max(0, i - 20):i].mean()
            volume_surge = dataframe['volume'].iloc[i] > avg_volume * 1.5
            
            # 3. 收盘位置
            if dataframe['high'].iloc[i] > dataframe['low'].iloc[i]:
                close_position = (
                    (dataframe['close'].iloc[i] - dataframe['low'].iloc[i]) /
                    (dataframe['high'].iloc[i] - dataframe['low'].iloc[i])
                )
                strong_close = close_position > 0.7
            else:
                strong_close = False
            
            # 综合判断
            if breakout and volume_surge and strong_close:
                sos_recent.iloc[i] = True
        
        # 应用滚动窗口判断（最近 5 根 K 线内有 SOS）
        sos_recent_windowed = (
            sos_recent.rolling(sos_window).max() == 1
        )
        
        logger.debug(
            f"Step 2: Using fallback SOS detection. "
            f"SOS recent: {sos_recent_windowed.sum()}/{len(sos_recent_windowed)} bars"
        )
        
        return sos_recent_windowed
    
    def _step3_check_no_supply_bounce(self, dataframe: DataFrame) -> pd.Series:
        """
        Step 3: 检查无供应回踩 (No Supply Bounce Back)
        
        SOS Bar 之后，如果出现"无供应"回踩，说明：
        - 卖方已经疲软
        - 没有新的供应出现
        - 价格将快速上涨（跳过供应区）
        
        无供应回踩的威科夫物理证据：
        1. 成交量萎缩 (volume < 0.8 * SMA)
           - 卖方兴趣下降
           - 投资者不愿卖出
        
        2. 价差收窄 (bar_range < 0.7 * ATR)
           - 缺乏激进的卖压
           - 市场消息不利空
        
        3. 收盘位置中性偏强 (close_position > 0.4)
           - 表明虽然有回踩，但没有强势下跌
           - 买方仍然参与
        
        时间关系：
        - 无供应回踩通常发生在 SOS 后的 0-3 根 K 线内
        """
        
        no_supply = pd.Series(False, index=dataframe.index)
        
        # 检查必需字段
        required = ['volume', 'atr', 'close', 'open', 'high', 'low']
        missing = [f for f in required if f not in dataframe.columns]
        if missing:
            logger.warning(f"⚠️ Missing fields for no_supply check: {missing}")
            return no_supply
        
        # 计算关键指标
        # 1. 成交量 SMA（20 根 K 线）
        volume_sma = dataframe['volume'].rolling(20).mean()
        volume_low = dataframe['volume'] < volume_sma * 0.8
        
        # 2. 价差与 ATR 的比较
        bar_range = dataframe['high'] - dataframe['low']
        atr = dataframe['atr'] if 'atr' in dataframe.columns else bar_range.rolling(14).mean()
        range_narrow = bar_range < atr * 0.7
        
        # 3. 收阔位置偏强（中性偶患上方，不能有强力下跌）
        bar_range_safe = bar_range.replace(0, 1e-09)  # 避免除以零
        close_position = (
            (dataframe['close'] - dataframe['low']) / bar_range_safe
        )
        close_neutral_or_strong = close_position > 0.5  # 加强：0.4 → 0.5（收阔必须在甚中上方）
        
        # 综合判断：三个特征都满足
        no_supply = (
            volume_low & 
            range_narrow & 
            close_neutral_or_strong
        )
        
        logger.debug(
            f"Step 3: No supply bounce detection. "
            f"No supply: {no_supply.sum()}/{len(no_supply)} bars"
        )
        
        return no_supply
    
    def _calculate_confidence(self, dataframe: DataFrame) -> pd.Series:
        """
        计算 Phase D 信号的置信度 (0.0-1.0)
        
        置信度基于：
        1. 三个步骤的充分性（都满足 > 都得分更高）
        2. SOS质量评分（如果可用）
        3. 历史胜率（通过参数化）
        
        输出：0.0-1.0 的置信度值
        """
        
        confidence = pd.Series(0.0, index=dataframe.index)
        
        # 基础置信度：所有三个条件都满足
        all_three = (
            dataframe['axis_above'] & 
            dataframe['sos_recent'] & 
            dataframe['no_supply_bounce']
        )
        confidence[all_three] = 0.75  # 基础置信度 75%
        
        # 如果有 SOS 质量评分，使用它来调整置信度
        if 'sos_quality_score' in dataframe.columns:
            sos_quality_normalized = (
                dataframe['sos_quality_score'] / 100.0
            ).clip(0, 1)
            confidence = confidence * 0.5 + sos_quality_normalized * 0.5
        
        # 去掉未确认的信号的置信度
        confidence[~all_three] = 0.0
        
        return confidence
    
    def _calculate_quality_score(self, dataframe: DataFrame) -> pd.Series:
        """
        计算 Phase D 信号的质量评分 (0-100)
        
        用于比较多个 Phase D 信号，选择最优的入场点
        
        评分维度：
        1. 三个条件的同时满足（都满足 +30 分）
        2. SOS质量（最多 +30 分）
        3. 价格位置的偏离度（接近轴线 +20 分，远离轴线 -20 分）
        4. No Supply 的强度（成交量萎缩程度、价差收窄程度）
        
        输出：0-100 的质量评分
        """
        
        quality_score = pd.Series(0, index=dataframe.index, dtype=float)
        
        # 1. 基础分：三个条件都满足 +30 分
        all_three = (
            dataframe['axis_above'] & 
            dataframe['sos_recent'] & 
            dataframe['no_supply_bounce']
        )
        quality_score[all_three] += 30
        
        # 2. SOS质量分 (最多 +30 分)
        if 'sos_quality_score' in dataframe.columns:
            sos_contribution = (
                dataframe['sos_quality_score'] / 100.0 * 30
            ).clip(0, 30)
            quality_score += sos_contribution.fillna(0)
        else:
            quality_score[dataframe['sos_recent']] += 20  # 无质量评分时，默认给20分
        
        # 3. 价格位置分 (最多 +20 分)
        # 如果没有轴线信息，使用价格位置的绝对值
        if 'nearest_support' in dataframe.columns:
            # 根据价格与轴线的距离评分
            support_level = dataframe['nearest_support']
            valid_support = support_level > 0
            
            if valid_support.any():
                distance_pct = (
                    (dataframe['close'] - support_level) / 
                    (support_level + 1e-09) * 100
                )
                # 最优距离：2-5% 以上轴线
                optimal_distance = (
                    (distance_pct >= 2) & (distance_pct <= 5)
                )
                quality_score[optimal_distance & valid_support] += 20
                quality_score[~optimal_distance & valid_support] += 10
        
        # 4. No Supply 强度分 (最多 +20 分)
        if 'volume' in dataframe.columns and 'atr' in dataframe.columns:
            volume_sma = dataframe['volume'].rolling(20).mean()
            bar_range = dataframe['high'] - dataframe['low']
            atr = dataframe['atr'] if 'atr' in dataframe.columns else bar_range.rolling(14).mean()
            
            # 成交量萎缩程度（低于 50% 为极度萎缩）
            volume_ratio = dataframe['volume'] / (volume_sma + 1e-09)
            extreme_volume_shrink = volume_ratio < 0.5
            quality_score[extreme_volume_shrink & dataframe['no_supply_bounce']] += 15
            
            # 价差收窄程度
            range_ratio = bar_range / (atr + 1e-09)
            extreme_range_narrow = range_ratio < 0.5
            quality_score[extreme_range_narrow & dataframe['no_supply_bounce']] += 15
        
        # 限制分数范围
        quality_score = quality_score.clip(0, 100)
        
        return quality_score


def demo_phase_d_detector():
    """
    演示 Phase D 检测器的用法
    """
    # 示例：创建检测器
    detector = PhaseDEntryDetector()
    
    # 示例数据框（简化版）
    import pandas as pd
    np.random.seed(42)
    
    dates = pd.date_range('2026-01-01', periods=100, freq='1H')
    data = {
        'close': 50000 + np.cumsum(np.random.randn(100) * 50),
        'high': 50500 + np.cumsum(np.random.randn(100) * 60),
        'low': 49500 + np.cumsum(np.random.randn(100) * 50),
        'volume': np.random.uniform(100, 500, 100),
        'atr': 100 + np.random.uniform(-20, 20, 100),
    }
    df = pd.DataFrame(data, index=dates)
    
    # 检测 Phase D
    df = detector.detect_phase_d_entry(df, {})
    
    # 显示结果
    phase_d_bars = df[df['phase_d_confirmed']]
    if len(phase_d_bars) > 0:
        print(f"✅ Found {len(phase_d_bars)} Phase D signals")
        print(phase_d_bars[['phase_d_confidence', 'phase_d_quality_score']])
    else:
        print("❌ No Phase D signals found in demo data")


if __name__ == "__main__":
    demo_phase_d_detector()
