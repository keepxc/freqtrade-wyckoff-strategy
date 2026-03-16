"""
P2: Regime-Aware Authorization System
目标：把 market_env / macro_phase / bear_market_mode 从描述层升级为授权层

执行方向：
- 只允许做 multiplier / 授权
- 研究不同 regime 下的 RR multiplier
- 研究不同 regime 下的持仓时间授权
- 研究少量信号路由开关
- 保持为 future optimization 预埋点

约束：
- 不做全面动态切换
- 不允许 regime 替代信号质量
- 不允许仅因环境状态就降低关键质量阈值
"""

import logging
from typing import Dict, Tuple, Optional
import pandas as pd
from pandas import DataFrame, Series

logger = logging.getLogger(__name__)


class RegimeAuthorizer:
    """
    Regime-aware 授权系统
    
    核心原则：
    1. Regime 只做 multiplier / 授权，不做硬切换
    2. 不降低信号质量阈值
    3. 不替代 Wyckoff 证据
    4. 所有调整可关闭、可回滚
    """
    
    def __init__(self, strategy):
        """
        初始化 Regime 授权器
        
        Args:
            strategy: 策略实例（用于访问配置参数）
        """
        self.strategy = strategy
        
        # P2 开关：默认 off，保留为预埋点
        self.enabled = False
        
        # RR Multiplier 配置（不同 regime 下的风险回报调整系数）
        self.rr_multipliers = {
            # macro_phase 层级
            'markup': 1.0,      # 上升趋势：标准 RR
            'markdown': 1.3,    # 下降趋势：提高 RR 要求（更保守）
            'transition': 1.15, # 过渡期：略微提高 RR 要求
            
            # market_env 层级（1H 框架）
            'trending': 1.0,    # 趋势行情：标准 RR
            'trading_range': 1.1, # 震荡行情：略微提高 RR
            
            # htf_market_env 层级（4H 框架）
            'htf_markup': 1.0,     # 4H 上升：标准 RR
            'htf_markdown': 1.25,  # 4H 下降：提高 RR 要求
            'htf_transition': 1.1, # 4H 过渡：略微提高 RR
        }
        
        # 持仓时间授权配置（不同 regime 下的持仓时间调整系数）
        self.holding_time_multipliers = {
            # macro_phase 层级
            'markup': 1.0,      # 上升趋势：标准持仓时间
            'markdown': 0.7,    # 下降趋势：缩短持仓时间（快进快出）
            'transition': 0.85, # 过渡期：略微缩短持仓时间
            
            # market_env 层级
            'trending': 1.0,    # 趋势行情：标准持仓时间
            'trading_range': 0.8, # 震荡行情：缩短持仓时间
        }
        
        # 信号路由开关配置（不同 regime 下的信号启用/禁用）
        self.signal_routing = {
            # bear_market_mode 下的信号路由
            'bear_market': {
                'allow_spring': True,      # 允许 Spring 信号（熊市反转机会）
                'allow_sos': False,        # 禁用 SOS 信号（熊市突破易失败）
                'allow_lps': True,         # 允许 LPS 信号（熊市回调买点）
                'allow_buec': True,        # 允许 BUEC 信号（熊市衰竭买点）
                'allow_jac': True,         # 允许 JAC 信号（熊市跳跃买点）
                'allow_creek': True,       # 允许 Creek 信号（熊市小回调）
                'allow_phase_d': False,    # 禁用 Phase D 信号（需要明确积累）
            },
            
            # markdown (1H) 下的信号路由
            'markdown': {
                'allow_spring': True,
                'allow_sos': True,         # 允许 SOS（可能是局部反转）
                'allow_lps': True,
                'allow_buec': True,
                'allow_jac': True,
                'allow_creek': True,
                'allow_phase_d': True,
            },
            
            # trading_range 下的信号路由
            'trading_range': {
                'allow_spring': True,      # 震荡区间的 Spring 是核心信号
                'allow_sos': True,
                'allow_lps': True,
                'allow_buec': True,
                'allow_jac': True,
                'allow_creek': True,
                'allow_phase_d': True,
            },
        }
        
        logger.info('✅ RegimeAuthorizer initialized (默认 disabled，保留为预埋点)')
    
    def get_rr_multiplier(
        self,
        macro_phase: str,
        market_env: str,
        htf_market_env: str,
        bear_market_mode: bool
    ) -> float:
        """
        获取当前 regime 下的 RR multiplier
        
        Args:
            macro_phase: 宏观阶段 (markup/markdown/transition)
            market_env: 1H 市场环境 (trending/trading_range/transition)
            htf_market_env: 4H 市场环境 (markup/markdown/transition)
            bear_market_mode: 熊市模式开关
        
        Returns:
            RR multiplier (>1.0 表示提高 RR 要求，更保守)
        """
        if not self.enabled:
            return 1.0
        
        # 多层级 multiplier 累积（取最大值，不做乘积）
        multipliers = [1.0]
        
        # 层级 1: macro_phase
        if macro_phase in self.rr_multipliers:
            multipliers.append(self.rr_multipliers[macro_phase])
        
        # 层级 2: market_env (1H)
        if market_env in self.rr_multipliers:
            multipliers.append(self.rr_multipliers[market_env])
        
        # 层级 3: htf_market_env (4H)
        htf_key = f'htf_{htf_market_env}'
        if htf_key in self.rr_multipliers:
            multipliers.append(self.rr_multipliers[htf_key])
        
        # 层级 4: bear_market_mode（最高优先级）
        if bear_market_mode:
            multipliers.append(1.4)  # 熊市模式：显著提高 RR 要求
        
        # 返回最大 multiplier（最保守的要求）
        return max(multipliers)
    
    def get_holding_time_multiplier(
        self,
        macro_phase: str,
        market_env: str,
        bear_market_mode: bool
    ) -> float:
        """
        获取当前 regime 下的持仓时间 multiplier
        
        Args:
            macro_phase: 宏观阶段
            market_env: 1H 市场环境
            bear_market_mode: 熊市模式开关
        
        Returns:
            持仓时间 multiplier (<1.0 表示缩短持仓时间)
        """
        if not self.enabled:
            return 1.0
        
        # 多层级 multiplier 累积（取最小值，最保守）
        multipliers = [1.0]
        
        # 层级 1: macro_phase
        if macro_phase in self.holding_time_multipliers:
            multipliers.append(self.holding_time_multipliers[macro_phase])
        
        # 层级 2: market_env
        if market_env in self.holding_time_multipliers:
            multipliers.append(self.holding_time_multipliers[market_env])
        
        # 层级 3: bear_market_mode（最高优先级）
        if bear_market_mode:
            multipliers.append(0.6)  # 熊市模式：显著缩短持仓时间
        
        # 返回最小 multiplier（最保守的持仓时间）
        return min(multipliers)
    
    def is_signal_allowed(
        self,
        signal_type: str,
        macro_phase: str,
        market_env: str,
        bear_market_mode: bool
    ) -> bool:
        """
        判断当前 regime 下是否允许该信号类型
        
        Args:
            signal_type: 信号类型 (spring/sos/lps/buec/jac/creek/phase_d)
            macro_phase: 宏观阶段
            market_env: 1H 市场环境
            bear_market_mode: 熊市模式开关
        
        Returns:
            True: 允许该信号; False: 禁用该信号
        """
        if not self.enabled:
            return True  # 未启用时，所有信号都允许
        
        # 优先级 1: bear_market_mode（最严格）
        if bear_market_mode:
            routing = self.signal_routing.get('bear_market', {})
            signal_key = f'allow_{signal_type}'
            if signal_key in routing:
                return routing[signal_key]
        
        # 优先级 2: macro_phase == 'markdown'
        if macro_phase == 'markdown':
            routing = self.signal_routing.get('markdown', {})
            signal_key = f'allow_{signal_type}'
            if signal_key in routing:
                return routing[signal_key]
        
        # 优先级 3: market_env == 'trading_range'
        if market_env == 'trading_range':
            routing = self.signal_routing.get('trading_range', {})
            signal_key = f'allow_{signal_type}'
            if signal_key in routing:
                return routing[signal_key]
        
        # 默认：允许所有信号
        return True
    
    def apply_regime_authorization(
        self,
        dataframe: DataFrame,
        signal_quality: Series,
        signal_type: Series
    ) -> Tuple[Series, Series]:
        """
        应用 regime-aware 授权到信号质量和信号类型
        
        Args:
            dataframe: 包含 regime 字段的 DataFrame
            signal_quality: 原始信号质量 Series
            signal_type: 信号类型 Series (spring/sos/lps/etc.)
        
        Returns:
            (adjusted_quality, allowed_signals): 调整后的质量和允许的信号
        """
        if not self.enabled:
            return signal_quality, signal_type
        
        # 提取 regime 字段
        macro_phase = dataframe.get('macro_phase', pd.Series('markup', index=dataframe.index))
        market_env = dataframe.get('market_env', pd.Series('transition', index=dataframe.index))
        htf_market_env = dataframe.get('htf_market_env', pd.Series('transition', index=dataframe.index))
        bear_market_mode = dataframe.get('bear_market_mode', pd.Series(False, index=dataframe.index))
        
        # 初始化调整后的质量和信号
        adjusted_quality = signal_quality.copy()
        allowed_signals = signal_type.copy()
        
        # 逐行应用授权逻辑
        for idx in dataframe.index:
            # 获取当前行的 regime 状态
            curr_macro = macro_phase.loc[idx]
            curr_market = market_env.loc[idx]
            curr_htf = htf_market_env.loc[idx]
            curr_bear = bear_market_mode.loc[idx]
            curr_signal = signal_type.loc[idx]
            
            # 1. 检查信号路由：是否允许该信号类型
            if pd.notna(curr_signal) and curr_signal != '':
                if not self.is_signal_allowed(curr_signal, curr_macro, curr_market, curr_bear):
                    # 禁用该信号
                    allowed_signals.loc[idx] = ''
                    adjusted_quality.loc[idx] = 0.0
                    continue
            
            # 2. 应用 RR multiplier：调整信号质量
            rr_mult = self.get_rr_multiplier(curr_macro, curr_market, curr_htf, curr_bear)
            if rr_mult > 1.0:
                # RR multiplier > 1.0 表示提高要求，降低信号质量评分
                # 例如：rr_mult=1.3 → quality *= 0.77 (1/1.3)
                adjusted_quality.loc[idx] = signal_quality.loc[idx] / rr_mult
        
        return adjusted_quality, allowed_signals
    
    def get_regime_summary(self, dataframe: DataFrame) -> Dict:
        """
        获取当前 DataFrame 的 regime 分布摘要
        
        Args:
            dataframe: 包含 regime 字段的 DataFrame
        
        Returns:
            regime 分布统计字典
        """
        summary = {}
        
        # macro_phase 分布
        if 'macro_phase' in dataframe.columns:
            summary['macro_phase'] = dataframe['macro_phase'].value_counts().to_dict()
        
        # market_env 分布
        if 'market_env' in dataframe.columns:
            summary['market_env'] = dataframe['market_env'].value_counts().to_dict()
        
        # htf_market_env 分布
        if 'htf_market_env' in dataframe.columns:
            summary['htf_market_env'] = dataframe['htf_market_env'].value_counts().to_dict()
        
        # bear_market_mode 激活率
        if 'bear_market_mode' in dataframe.columns:
            bear_count = dataframe['bear_market_mode'].sum()
            total_count = len(dataframe)
            summary['bear_market_mode'] = {
                'active_bars': int(bear_count),
                'total_bars': total_count,
                'activation_rate': f'{bear_count/total_count*100:.1f}%' if total_count > 0 else '0%'
            }
        
        return summary
