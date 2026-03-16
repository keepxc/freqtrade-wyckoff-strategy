"""
P2 Regime Diagnostics
用于诊断 regime 失配证据，判断是否需要启动 P2

触发条件（满足任一条，可触发 P2 预研）：
A. 特定 regime 下持续跑输基线
B. 特定 regime 下出现方向性误配
C. 环境标签长期失真或异常常开
D. 回测已证明某一 regime 内存在稳定结构性短板
"""

import logging
from typing import Dict, List, Tuple, Optional
import pandas as pd
from pandas import DataFrame
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class RegimeDiagnostics:
    """
    Regime 失配诊断器
    
    用于监控和诊断 regime 相关的异常模式，判断是否需要启动 P2
    """
    
    def __init__(self):
        """初始化诊断器"""
        self.min_sample_size = 20  # 最小样本量要求
        self.min_backtest_sample = 30  # 回测最小样本量
        self.baseline_win_rate_threshold = 0.30  # 基线胜率下限（30%）
        self.profit_factor_threshold = 1.0  # Profit Factor 下限
        
        logger.info('✅ RegimeDiagnostics initialized')
    
    def check_trigger_condition_a(
        self,
        trades_df: DataFrame,
        regime_column: str = 'macro_phase',
        lookback_months: int = 2
    ) -> Tuple[bool, Dict]:
        """
        触发条件 A：特定 regime 下持续跑输基线
        
        检查逻辑：
        - 连续 2 个自然月 Dry Run 中
        - 某一 regime 内的交易：
          - 平均单笔收益持续低于对应回测基线 30% 以上
          - 或 Profit Factor 持续 < 1.0
        - 且该 regime 内样本数 ≥ 20 笔交易
        
        Args:
            trades_df: 交易记录 DataFrame (需包含: close_date, profit_ratio, regime 列)
            regime_column: regime 列名 (macro_phase/market_env/htf_market_env)
            lookback_months: 回看月数
        
        Returns:
            (triggered, details): 是否触发 + 详细信息
        """
        if trades_df.empty or regime_column not in trades_df.columns:
            return False, {'error': f'Missing {regime_column} column or empty trades'}
        
        # 计算回看时间窗口
        if 'close_date' not in trades_df.columns:
            return False, {'error': 'Missing close_date column'}
        
        latest_date = trades_df['close_date'].max()
        cutoff_date = latest_date - timedelta(days=lookback_months * 30)
        
        # 过滤最近 N 个月的交易
        recent_trades = trades_df[trades_df['close_date'] >= cutoff_date].copy()
        
        if len(recent_trades) < self.min_sample_size:
            return False, {
                'error': f'Insufficient samples: {len(recent_trades)} < {self.min_sample_size}'
            }
        
        # 按 regime 分组统计
        regime_stats = {}
        triggered_regimes = []
        
        for regime in recent_trades[regime_column].unique():
            regime_trades = recent_trades[recent_trades[regime_column] == regime]
            
            if len(regime_trades) < self.min_sample_size:
                continue
            
            # 计算关键指标
            avg_profit = regime_trades['profit_ratio'].mean()
            profit_factor = self._calculate_profit_factor(regime_trades['profit_ratio'])
            win_rate = (regime_trades['profit_ratio'] > 0).mean()
            
            regime_stats[regime] = {
                'trade_count': len(regime_trades),
                'avg_profit': avg_profit,
                'profit_factor': profit_factor,
                'win_rate': win_rate
            }
            
            # 检查是否触发条件
            if profit_factor < self.profit_factor_threshold:
                triggered_regimes.append(regime)
        
        triggered = len(triggered_regimes) > 0
        
        return triggered, {
            'triggered_regimes': triggered_regimes,
            'regime_stats': regime_stats,
            'lookback_months': lookback_months,
            'total_trades': len(recent_trades)
        }
    
    def check_trigger_condition_b(
        self,
        trades_df: DataFrame,
        signal_column: str = 'enter_tag',
        regime_column: str = 'macro_phase'
    ) -> Tuple[bool, Dict]:
        """
        触发条件 B：特定 regime 下出现方向性误配
        
        检查逻辑：
        - 在同一 regime 下，某类核心信号满足以下任一条件：
          - 连续 20 笔胜率明显异常
          - 或累计 20 笔（非连续），且时间窗口 ≤ 60 天，胜率明显异常
        - 例如：wyckoff_axis_support 胜率持续低于回测基线对应监控阈值
        - 且该异常不出现在其他 regime 中
        
        Args:
            trades_df: 交易记录 DataFrame
            signal_column: 信号类型列名
            regime_column: regime 列名
        
        Returns:
            (triggered, details): 是否触发 + 详细信息
        """
        if trades_df.empty:
            return False, {'error': 'Empty trades'}
        
        if signal_column not in trades_df.columns or regime_column not in trades_df.columns:
            return False, {'error': f'Missing {signal_column} or {regime_column} column'}
        
        # 按 regime + signal 分组统计
        signal_regime_stats = {}
        triggered_cases = []
        
        for regime in trades_df[regime_column].unique():
            regime_trades = trades_df[trades_df[regime_column] == regime]
            
            for signal in regime_trades[signal_column].unique():
                if pd.isna(signal) or signal == '':
                    continue
                
                signal_trades = regime_trades[regime_trades[signal_column] == signal]
                
                if len(signal_trades) < self.min_sample_size:
                    continue
                
                # 计算胜率
                win_rate = (signal_trades['profit_ratio'] > 0).mean()
                
                key = f'{regime}_{signal}'
                signal_regime_stats[key] = {
                    'regime': regime,
                    'signal': signal,
                    'trade_count': len(signal_trades),
                    'win_rate': win_rate
                }
                
                # 检查是否异常（胜率 < 基线阈值）
                if win_rate < self.baseline_win_rate_threshold:
                    # 检查该信号在其他 regime 下是否正常
                    other_regimes_ok = self._check_signal_in_other_regimes(
                        trades_df, signal, regime, signal_column, regime_column
                    )
                    
                    if other_regimes_ok:
                        triggered_cases.append({
                            'regime': regime,
                            'signal': signal,
                            'win_rate': win_rate,
                            'trade_count': len(signal_trades)
                        })
        
        triggered = len(triggered_cases) > 0
        
        return triggered, {
            'triggered_cases': triggered_cases,
            'signal_regime_stats': signal_regime_stats
        }
    
    def check_trigger_condition_c(
        self,
        dataframe: DataFrame,
        lookback_days: int = 30
    ) -> Tuple[bool, Dict]:
        """
        触发条件 C：环境标签长期失真或异常常开
        
        检查逻辑：
        - bear_market_mode 持续激活 > 30 天
        - 或 daily_choch_confirmed / macro_phase / htf_market_env 的分布
          连续 2 周明显偏离历史基线
        
        Args:
            dataframe: 包含 regime 字段的 DataFrame
            lookback_days: 回看天数
        
        Returns:
            (triggered, details): 是否触发 + 详细信息
        """
        if dataframe.empty:
            return False, {'error': 'Empty dataframe'}
        
        # 检查 bear_market_mode 持续激活
        bear_triggered = False
        bear_details = {}
        
        if 'bear_market_mode' in dataframe.columns:
            recent_df = dataframe.tail(lookback_days * 24)  # 假设 1H 数据
            bear_active_hours = recent_df['bear_market_mode'].sum()
            bear_active_days = bear_active_hours / 24
            
            if bear_active_days > 30:
                bear_triggered = True
                bear_details = {
                    'bear_active_days': bear_active_days,
                    'threshold': 30
                }
        
        # 检查 macro_phase 分布偏离
        macro_triggered = False
        macro_details = {}
        
        if 'macro_phase' in dataframe.columns:
            # 计算最近 2 周的分布
            recent_2w = dataframe.tail(14 * 24)
            recent_dist = recent_2w['macro_phase'].value_counts(normalize=True).to_dict()
            
            # 计算历史基线分布
            historical_dist = dataframe['macro_phase'].value_counts(normalize=True).to_dict()
            
            # 检查是否明显偏离（例如：markdown 占比从 20% 升至 60%）
            for phase in ['markdown', 'transition']:
                recent_pct = recent_dist.get(phase, 0)
                hist_pct = historical_dist.get(phase, 0)
                
                if recent_pct > hist_pct * 2 and recent_pct > 0.5:
                    macro_triggered = True
                    macro_details[phase] = {
                        'recent_pct': recent_pct,
                        'historical_pct': hist_pct
                    }
        
        triggered = bear_triggered or macro_triggered
        
        return triggered, {
            'bear_market_mode': bear_details if bear_triggered else None,
            'macro_phase': macro_details if macro_triggered else None
        }
    
    def check_trigger_condition_d(
        self,
        backtest_trades: DataFrame,
        regime_column: str = 'macro_phase'
    ) -> Tuple[bool, Dict]:
        """
        触发条件 D：回测已证明某一 regime 内存在稳定结构性短板
        
        检查逻辑：
        - 三段回测复盘中，某一 regime 下：
          - 交易样本 ≥ 30 笔
          - 平均收益持续为负，或显著低于其他 regime
          - 且问题集中在：RR 不匹配 / 持仓寿命不匹配 / 信号路由不匹配
        
        Args:
            backtest_trades: 回测交易记录 DataFrame
            regime_column: regime 列名
        
        Returns:
            (triggered, details): 是否触发 + 详细信息
        """
        if backtest_trades.empty or regime_column not in backtest_trades.columns:
            return False, {'error': f'Missing {regime_column} column or empty trades'}
        
        # 按 regime 分组统计
        regime_stats = {}
        triggered_regimes = []
        
        for regime in backtest_trades[regime_column].unique():
            regime_trades = backtest_trades[backtest_trades[regime_column] == regime]
            
            if len(regime_trades) < self.min_backtest_sample:
                continue
            
            # 计算关键指标
            avg_profit = regime_trades['profit_ratio'].mean()
            profit_factor = self._calculate_profit_factor(regime_trades['profit_ratio'])
            avg_duration = regime_trades['trade_duration'].mean() if 'trade_duration' in regime_trades.columns else None
            
            regime_stats[regime] = {
                'trade_count': len(regime_trades),
                'avg_profit': avg_profit,
                'profit_factor': profit_factor,
                'avg_duration_hours': avg_duration
            }
            
            # 检查是否触发条件
            if avg_profit < 0 or profit_factor < self.profit_factor_threshold:
                triggered_regimes.append(regime)
        
        # 检查是否存在显著差异（某个 regime 明显弱于其他）
        if len(regime_stats) > 1:
            avg_profits = [s['avg_profit'] for s in regime_stats.values()]
            max_profit = max(avg_profits)
            min_profit = min(avg_profits)
            
            # 如果最差 regime 的收益显著低于最好 regime（差距 > 50%）
            if max_profit > 0 and min_profit < max_profit * 0.5:
                for regime, stats in regime_stats.items():
                    if stats['avg_profit'] == min_profit:
                        if regime not in triggered_regimes:
                            triggered_regimes.append(regime)
        
        triggered = len(triggered_regimes) > 0
        
        return triggered, {
            'triggered_regimes': triggered_regimes,
            'regime_stats': regime_stats,
            'min_sample_size': self.min_backtest_sample
        }
    
    def run_full_diagnostics(
        self,
        trades_df: Optional[DataFrame] = None,
        dataframe: Optional[DataFrame] = None,
        backtest_trades: Optional[DataFrame] = None
    ) -> Dict:
        """
        运行完整的 P2 触发条件诊断
        
        Args:
            trades_df: Dry Run 交易记录
            dataframe: 包含 regime 字段的 OHLCV DataFrame
            backtest_trades: 回测交易记录
        
        Returns:
            完整诊断报告
        """
        report = {
            'timestamp': datetime.now().isoformat(),
            'p2_activation_recommended': False,
            'triggered_conditions': [],
            'condition_details': {}
        }
        
        # 检查条件 A
        if trades_df is not None:
            triggered_a, details_a = self.check_trigger_condition_a(trades_df)
            if triggered_a:
                report['triggered_conditions'].append('A')
                report['condition_details']['A'] = details_a
        
        # 检查条件 B
        if trades_df is not None:
            triggered_b, details_b = self.check_trigger_condition_b(trades_df)
            if triggered_b:
                report['triggered_conditions'].append('B')
                report['condition_details']['B'] = details_b
        
        # 检查条件 C
        if dataframe is not None:
            triggered_c, details_c = self.check_trigger_condition_c(dataframe)
            if triggered_c:
                report['triggered_conditions'].append('C')
                report['condition_details']['C'] = details_c
        
        # 检查条件 D
        if backtest_trades is not None:
            triggered_d, details_d = self.check_trigger_condition_d(backtest_trades)
            if triggered_d:
                report['triggered_conditions'].append('D')
                report['condition_details']['D'] = details_d
        
        # 判断是否推荐启动 P2
        report['p2_activation_recommended'] = len(report['triggered_conditions']) > 0
        
        return report
    
    def _calculate_profit_factor(self, profit_series: pd.Series) -> float:
        """计算 Profit Factor"""
        wins = profit_series[profit_series > 0].sum()
        losses = abs(profit_series[profit_series < 0].sum())
        
        if losses == 0:
            return float('inf') if wins > 0 else 0.0
        
        return wins / losses
    
    def _check_signal_in_other_regimes(
        self,
        trades_df: DataFrame,
        signal: str,
        current_regime: str,
        signal_column: str,
        regime_column: str
    ) -> bool:
        """检查该信号在其他 regime 下是否正常"""
        other_regimes = trades_df[
            (trades_df[regime_column] != current_regime) &
            (trades_df[signal_column] == signal)
        ]
        
        if len(other_regimes) < self.min_sample_size:
            return False
        
        win_rate = (other_regimes['profit_ratio'] > 0).mean()
        return win_rate >= self.baseline_win_rate_threshold
