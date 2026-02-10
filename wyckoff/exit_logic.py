"""
威科夫退出逻辑模块 - Exit Logic Module

实现威科夫理论的智能退出系统：
1. 气候行动检测 (Climax Action Detection)
2. 努力结果背离分析 (Effort-Result Divergence)
3. SOT预警系统 (Signs of Trouble Warning)
4. 自动反应确认 (Automatic Reaction Confirmation)
5. 二次测试失败检测 (Secondary Test Failure)
6. 吸收行为识别 (Absorption Behavior Detection)
"""

import pandas as pd
from pandas import DataFrame
import logging

logger = logging.getLogger(__name__)


class ExitLogic:
    """威科夫退出逻辑类"""
    
    def __init__(self):
        """初始化退出逻辑"""
        self.waves_cache = []
    
    def detect_intelligent_wyckoff_climax(self, dataframe: DataFrame, waves_cache: list = None) -> pd.Series:
        """
        威科夫真实退出系统 - 三级确认机制
        
        基于威科夫理论的力量对比分析，实现真正的"大资金行为"识别：
        
        1. 持仓确认：正常回调 (Normal Pullback)
           - 成交量比率 < 0.8 且价差收窄 = "缺乏兴趣"
           - 威科夫理论：无供应测试，卖盘耗尽，持仓确认
        
        2. 预警/减仓：异常下降 (Supply Entering) 
           - 成交量比率 > 1.5 且收盘在低位 = "SOW Bar"
           - 威科夫理论：供应接管控制权，进入Phase A
        
        3. 最终离场：特征改变 (Change of Character)
           - 成交量比率 > 2.0 且ChoCh = 质变
           - 威科夫理论：控制权彻底转移，跌破冰层
        """
        
        if waves_cache is not None:
            self.waves_cache = waves_cache
        
        if len(dataframe) < 50:
            return pd.Series([False] * len(dataframe), index=dataframe.index)
        
        # === 威科夫力量分析基础指标 ===
        
        # 成交量比率（努力度量）
        volume_ratio = dataframe['volume'] / (dataframe['volume_sma_20'] + 1e-9)
        
        # 价差比率（结果度量）
        price_range = dataframe['high'] - dataframe['low']
        range_ratio = price_range / (dataframe['atr_20'] + 1e-9)
        
        # 收盘位置（力量方向）
        close_position = (dataframe['close'] - dataframe['low']) / (price_range + 1e-9)
        
        # Phase E 背景确认（趋势主权）
        phase_e_active = (
            (dataframe['close'] > dataframe['ema_200']) &  # 牛市背景
            (dataframe['close'] > dataframe['ema_50'])     # 短期趋势
        )
        
        # === 第一级：持仓确认 - Normal Pullback ===
        
        normal_pullback = (
            (volume_ratio < 0.8) &           # 成交量萎缩（缺乏兴趣）
            (range_ratio < 1.0) &            # 价差收窄（无供应）
            phase_e_active                   # Phase E背景
        )
        
        # === 第二级：预警减仓 - Supply Entering ===
        
        supply_entering = (
            (volume_ratio > 1.5) &           # 成交量显著增加（大资金参与）
            (close_position < 0.4) &         # 收盘在低位（弱势信号条）
            (range_ratio > 0.8) &            # 价差较宽（显著下行结果）
            phase_e_active                   # 仍在Phase E中
        )
        
        # === 第三级：最终离场 - Change of Character ===
        
        # ChoCh检测：波段位移显著增加
        current_wave_displacement = dataframe['wave_displacement'].fillna(0)
        historical_displacement = current_wave_displacement.rolling(10).mean()
        
        # 特征改变条件
        change_of_character = (
            (volume_ratio > 2.0) &           # 极高成交量（质变信号）
            (close_position < 0.3) &         # 收盘极低位（控制权转移）
            (range_ratio > 1.5) &            # 极宽价差（打破趋势背脊）
            (
                (current_wave_displacement > historical_displacement * 1.5) |  # 位移超过前波50%
                (dataframe['close'] < dataframe['close'].rolling(20).min())    # 或创近期新低
            )
        )
        
        # === 威科夫吸收保护机制 ===
        
        # 识别"接盘"行为：巨大努力但结果微弱
        absorption_detected = (
            (volume_ratio > 2.0) &           # 巨大成交量
            (close_position > 0.6) &         # 但收盘在中高位
            (range_ratio < 1.2) &            # 价差相对有限
            phase_e_active                   # Phase E背景
        )
        
        # 无需求反弹确认（二次测试）
        no_demand_rally = self.detect_no_demand_rally(dataframe, supply_entering)
        
        # === 三级退出决策逻辑 ===
        
        # 第一级：持仓确认（不退出）
        hold_position = normal_pullback & ~supply_entering & ~change_of_character
        
        # 第二级：预警减仓（部分退出，等待确认）
        warning_exit = (
            supply_entering & 
            ~absorption_detected &           # 非吸收行为
            ~hold_position                   # 非正常回调
        )
        
        # 第三级：最终离场（完全退出）
        final_exit = (
            change_of_character |            # 特征改变
            (warning_exit & no_demand_rally) # 或预警后无需求反弹确认
        )
        
        # === Phase E 路径守恒保护 ===
        
        # 在强劲牛市中，优先视为"普通洗盘"
        ordinary_shakeout_protection = (
            phase_e_active &                 # Phase E背景
            (dataframe['close'] > dataframe['ema_50'] * 0.95) &  # 仍在趋势通道内
            ~change_of_character             # 非特征改变
        )
        
        # 应用保护机制
        protected_warning_exit = warning_exit & ~ordinary_shakeout_protection
        protected_final_exit = final_exit & ~(ordinary_shakeout_protection & ~change_of_character)
        
        # 最终威科夫智能退出信号
        intelligent_exit = protected_warning_exit | protected_final_exit
        
        return intelligent_exit
    
    def detect_no_demand_rally(self, dataframe: DataFrame, supply_signal: pd.Series) -> pd.Series:
        """
        检测无需求反弹 - 威科夫二次确认机制
        
        在异常下降后寻找"无需求反弹"：
        - 窄价差、极低成交量
        - 无法触及前期高点
        - 确认供应主导趋势
        """
        
        no_demand = pd.Series([False] * len(dataframe), index=dataframe.index)
        
        if len(dataframe) < 10:
            return no_demand
        
        # 寻找供应信号后的反弹
        for i in range(5, len(dataframe)):
            # 检查前5根K线是否有供应信号
            if supply_signal.iloc[max(0, i-5):i].any():
                
                current_bar = dataframe.iloc[i]
                recent_bars = dataframe.iloc[max(0, i-10):i+1]
                
                # 无需求反弹特征
                # 1. 窄价差
                price_range = current_bar['high'] - current_bar['low']
                narrow_spread = price_range < current_bar['atr_20'] * 0.5
                
                # 2. 极低成交量
                low_volume = current_bar['volume'] < current_bar['volume_sma_20'] * 0.6
                
                # 3. 无法触及前期高点
                recent_high = recent_bars['high'].max()
                failed_retest = (
                    current_bar['high'] < recent_high * 0.98 and
                    current_bar['close'] < recent_high * 0.95
                )
                
                # 4. 上涨但无力
                weak_rally = (
                    current_bar['close'] > current_bar['open'] and  # 收阳
                    current_bar['close'] < current_bar['low'] + price_range * 0.6  # 但收盘位置不高
                )
                
                if narrow_spread and low_volume and failed_retest and weak_rally:
                    no_demand.iloc[i] = True
        
        return no_demand
    
    def detect_sot_warning_from_waves(self, dataframe: DataFrame, waves_cache: list = None) -> pd.Series:
        """基于Weis Wave检测SOT预警"""
        
        if waves_cache is not None:
            self.waves_cache = waves_cache
        
        sot_warning = pd.Series([False] * len(dataframe), index=dataframe.index)
        
        if not hasattr(self, 'waves_cache') or len(self.waves_cache) < 3:
            return sot_warning
        
        # 获取最近的上升波段
        recent_up_waves = [w for w in self.waves_cache[-6:] if hasattr(w, 'is_up_wave') and w.is_up_wave]
        
        if len(recent_up_waves) >= 3:
            # 检查位移递减模式
            last_three = recent_up_waves[-3:]
            displacements = [w.displacement for w in last_three]
            
            # SOT条件：连续递减且递减幅度显著
            if (displacements[2] < displacements[1] < displacements[0] and
                displacements[2] < displacements[0] * 0.7):  # 放宽条件：0.6 → 0.7
                
                # 在最后一个波段的K线上标记SOT预警
                last_wave = recent_up_waves[-1]
                if last_wave.end_idx < len(dataframe):
                    wave_range = range(last_wave.start_idx, min(last_wave.end_idx + 1, len(dataframe)))
                    for idx in wave_range:
                        sot_warning.iloc[idx] = True
        
        return sot_warning
    
    def detect_effort_result_divergence_warning(self, dataframe: DataFrame) -> pd.Series:
        """检测努力结果背离预警"""
        
        if len(dataframe) < 20:
            return pd.Series([False] * len(dataframe), index=dataframe.index)
        
        # 计算努力（成交量相对强度）
        effort_ratio = dataframe['volume'] / (dataframe['volume'].rolling(20).mean() + 1e-9)
        
        # 计算结果（价格运动效率）
        price_range = dataframe['high'] - dataframe['low']
        atr_20 = price_range.rolling(20).mean()
        result_ratio = price_range / (atr_20 + 1e-9)
        
        # 背离检测：高努力低结果
        high_effort = effort_ratio > 2.0  # 放宽条件：2.5 → 2.0
        low_result = result_ratio < 0.8   # 放宽条件：0.6 → 0.8
        
        # 收盘位置疲软
        close_position = (dataframe['close'] - dataframe['low']) / (price_range + 1e-9)
        weak_close = close_position < 0.4
        
        # 综合背离预警
        divergence_warning = high_effort & low_result & weak_close
        
        return divergence_warning
