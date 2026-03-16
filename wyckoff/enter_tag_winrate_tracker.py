"""
Enter Tag Win Rate Drift Tracking (FUT-E8)

Enter Tag 胜率漂移跟踪：
- 统计各 enter_tag 的历史胜率
- 监控胜率漂移（时间窗口对比）
- 识别退化信号（胜率下降）
- 为信号质量评估提供数据
"""

import pandas as pd
import numpy as np
from pandas import DataFrame
from typing import Dict, List, Tuple, Optional
import logging
from datetime import datetime
from collections import defaultdict

logger = logging.getLogger(__name__)


class EnterTagWinRateTracker:
    """Enter Tag 胜率跟踪器"""
    
    def __init__(self):
        self.window_sizes = [50, 100, 200]  # 滚动窗口大小
        
    def analyze_enter_tag_performance(
        self,
        trades_df: DataFrame,
        reference_period: Tuple[str, str] = None
    ) -> Dict:
        """
        分析 enter_tag 表现
        
        Args:
            trades_df: 交易记录 DataFrame，需包含：
                - enter_tag: 入场标签
                - profit_ratio: 盈亏比
                - close_date: 平仓时间
            reference_period: 参考期间 (start_date, end_date)，用于对比
            
        Returns:
            分析结果字典
        """
        if 'enter_tag' not in trades_df.columns:
            return {'error': 'Missing enter_tag column'}
        
        results = {
            'timestamp': datetime.now().isoformat(),
            'total_trades': len(trades_df),
            'enter_tag_stats': {},
            'drift_analysis': {},
            'degradation_alerts': [],
        }
        
        # ── 1. 整体统计 ────────────────────────────────────────────
        results['enter_tag_stats'] = self._calculate_enter_tag_stats(trades_df)
        
        # ── 2. 漂移分析 ────────────────────────────────────────────
        if reference_period:
            results['drift_analysis'] = self._analyze_drift(
                trades_df, reference_period
            )
        
        # ── 3. 退化告警 ────────────────────────────────────────────
        results['degradation_alerts'] = self._detect_degradation(
            results['enter_tag_stats']
        )
        
        return results
    
    def _calculate_enter_tag_stats(self, trades_df: DataFrame) -> Dict:
        """计算各 enter_tag 的统计数据"""
        stats = {}
        
        for tag in trades_df['enter_tag'].unique():
            if pd.isna(tag):
                continue
            
            tag_trades = trades_df[trades_df['enter_tag'] == tag]
            
            if len(tag_trades) == 0:
                continue
            
            # 基础统计
            total_trades = len(tag_trades)
            winning_trades = (tag_trades['profit_ratio'] > 0).sum()
            win_rate = winning_trades / total_trades
            
            # 盈亏统计
            avg_profit = tag_trades['profit_ratio'].mean()
            median_profit = tag_trades['profit_ratio'].median()
            
            # 盈利因子
            gross_profit = tag_trades[tag_trades['profit_ratio'] > 0]['profit_ratio'].sum()
            gross_loss = abs(tag_trades[tag_trades['profit_ratio'] < 0]['profit_ratio'].sum())
            profit_factor = gross_profit / gross_loss if gross_loss > 0 else np.inf
            
            # 最大回撤（连续亏损）
            max_consecutive_losses = self._calculate_max_consecutive_losses(
                tag_trades['profit_ratio']
            )
            
            stats[tag] = {
                'total_trades': int(total_trades),
                'winning_trades': int(winning_trades),
                'losing_trades': int(total_trades - winning_trades),
                'win_rate': round(float(win_rate), 4),
                'avg_profit': round(float(avg_profit), 6),
                'median_profit': round(float(median_profit), 6),
                'profit_factor': round(float(profit_factor), 2) if profit_factor != np.inf else 'inf',
                'max_consecutive_losses': int(max_consecutive_losses),
            }
            
            # 滚动窗口胜率
            if len(tag_trades) >= 50:
                stats[tag]['rolling_winrate'] = self._calculate_rolling_winrate(
                    tag_trades['profit_ratio']
                )
        
        return stats
    
    def _calculate_max_consecutive_losses(self, profit_series: pd.Series) -> int:
        """计算最大连续亏损次数"""
        is_loss = (profit_series < 0).astype(int)
        consecutive = (is_loss.groupby((is_loss != is_loss.shift()).cumsum()).cumsum())
        return int(consecutive.max()) if len(consecutive) > 0 else 0
    
    def _calculate_rolling_winrate(self, profit_series: pd.Series) -> Dict:
        """计算滚动窗口胜率"""
        rolling_stats = {}
        
        for window in self.window_sizes:
            if len(profit_series) < window:
                continue
            
            is_win = (profit_series > 0).astype(int)
            rolling_wr = is_win.rolling(window).mean()
            
            rolling_stats[f'window_{window}'] = {
                'current': round(float(rolling_wr.iloc[-1]), 4),
                'mean': round(float(rolling_wr.mean()), 4),
                'std': round(float(rolling_wr.std()), 4),
                'min': round(float(rolling_wr.min()), 4),
                'max': round(float(rolling_wr.max()), 4),
            }
        
        return rolling_stats
    
    def _analyze_drift(
        self,
        trades_df: DataFrame,
        reference_period: Tuple[str, str]
    ) -> Dict:
        """分析胜率漂移"""
        if 'close_date' not in trades_df.columns:
            return {'error': 'Missing close_date column for drift analysis'}
        
        ref_start, ref_end = reference_period
        
        # 分割数据
        ref_trades = trades_df[
            (trades_df['close_date'] >= ref_start) &
            (trades_df['close_date'] <= ref_end)
        ]
        
        recent_trades = trades_df[trades_df['close_date'] > ref_end]
        
        drift_analysis = {}
        
        for tag in trades_df['enter_tag'].unique():
            if pd.isna(tag):
                continue
            
            ref_tag_trades = ref_trades[ref_trades['enter_tag'] == tag]
            recent_tag_trades = recent_trades[recent_trades['enter_tag'] == tag]
            
            if len(ref_tag_trades) < 10 or len(recent_tag_trades) < 10:
                continue  # 样本太小，跳过
            
            # 计算胜率
            ref_wr = (ref_tag_trades['profit_ratio'] > 0).mean()
            recent_wr = (recent_tag_trades['profit_ratio'] > 0).mean()
            
            # 计算漂移
            drift = recent_wr - ref_wr
            drift_pct = (drift / ref_wr * 100) if ref_wr > 0 else 0
            
            drift_analysis[tag] = {
                'reference_winrate': round(float(ref_wr), 4),
                'recent_winrate': round(float(recent_wr), 4),
                'drift': round(float(drift), 4),
                'drift_pct': round(float(drift_pct), 2),
                'reference_trades': int(len(ref_tag_trades)),
                'recent_trades': int(len(recent_tag_trades)),
                'is_degrading': drift < -0.05,  # 胜率下降超过 5%
            }
        
        return drift_analysis
    
    def _detect_degradation(self, enter_tag_stats: Dict) -> List[Dict]:
        """检测退化信号"""
        alerts = []
        
        for tag, stats in enter_tag_stats.items():
            # ── Alert 1：低胜率 ────────────────────────────────────
            if stats['win_rate'] < 0.4 and stats['total_trades'] >= 20:
                alerts.append({
                    'level': 'warning',
                    'tag': tag,
                    'type': 'low_winrate',
                    'message': f"{tag}: Low win rate {stats['win_rate']:.1%}",
                    'win_rate': stats['win_rate'],
                })
            
            # ── Alert 2：负期望 ────────────────────────────────────
            if stats['avg_profit'] < 0 and stats['total_trades'] >= 20:
                alerts.append({
                    'level': 'error',
                    'tag': tag,
                    'type': 'negative_expectancy',
                    'message': f"{tag}: Negative avg profit {stats['avg_profit']:.2%}",
                    'avg_profit': stats['avg_profit'],
                })
            
            # ── Alert 3：低盈利因子 ────────────────────────────────
            pf = stats['profit_factor']
            if pf != 'inf' and pf < 1.5 and stats['total_trades'] >= 20:
                alerts.append({
                    'level': 'warning',
                    'tag': tag,
                    'type': 'low_profit_factor',
                    'message': f"{tag}: Low profit factor {pf:.2f}",
                    'profit_factor': pf,
                })
            
            # ── Alert 4：高连续亏损 ────────────────────────────────
            if stats['max_consecutive_losses'] >= 5:
                alerts.append({
                    'level': 'info',
                    'tag': tag,
                    'type': 'high_consecutive_losses',
                    'message': f"{tag}: Max consecutive losses {stats['max_consecutive_losses']}",
                    'max_consecutive_losses': stats['max_consecutive_losses'],
                })
            
            # ── Alert 5：滚动胜率下降 ──────────────────────────────
            if 'rolling_winrate' in stats:
                for window_key, window_stats in stats['rolling_winrate'].items():
                    if window_stats['current'] < window_stats['mean'] - window_stats['std']:
                        alerts.append({
                            'level': 'warning',
                            'tag': tag,
                            'type': 'rolling_winrate_drop',
                            'message': f"{tag}: Rolling WR ({window_key}) dropped below mean-std",
                            'current': window_stats['current'],
                            'mean': window_stats['mean'],
                        })
        
        return alerts
    
    def generate_report(self, analysis_results: Dict) -> str:
        """生成胜率跟踪报告"""
        report = ["# Enter Tag Win Rate Tracking Report\n"]
        
        report.append(f"Generated: {analysis_results['timestamp']}\n")
        report.append(f"Total Trades: {analysis_results['total_trades']}\n\n")
        
        # ── 1. 告警 ────────────────────────────────────────────────
        alerts = analysis_results.get('degradation_alerts', [])
        if alerts:
            report.append("## ⚠️ Degradation Alerts\n\n")
            
            for alert in sorted(alerts, key=lambda x: {'error': 0, 'warning': 1, 'info': 2}[x['level']]):
                level_emoji = {
                    'error': '❌',
                    'warning': '⚠️',
                    'info': 'ℹ️'
                }.get(alert['level'], '•')
                
                report.append(f"{level_emoji} **{alert['level'].upper()}**: {alert['message']}\n")
            
            report.append("\n")
        
        # ── 2. Enter Tag 统计 ──────────────────────────────────────
        report.append("## Enter Tag Statistics\n\n")
        report.append("| Tag | Trades | Win Rate | Avg Profit | PF | Max Consec Loss |\n")
        report.append("|-----|--------|----------|------------|----|-----------------|\n")
        
        stats = analysis_results.get('enter_tag_stats', {})
        for tag, tag_stats in sorted(stats.items(), key=lambda x: x[1]['win_rate'], reverse=True):
            pf = tag_stats['profit_factor']
            pf_str = f"{pf:.2f}" if pf != 'inf' else '∞'
            
            report.append(
                f"| {tag} | {tag_stats['total_trades']} | "
                f"{tag_stats['win_rate']:.1%} | {tag_stats['avg_profit']:.2%} | "
                f"{pf_str} | {tag_stats['max_consecutive_losses']} |\n"
            )
        
        # ── 3. 漂移分析 ────────────────────────────────────────────
        drift = analysis_results.get('drift_analysis', {})
        if drift and 'error' not in drift:
            report.append("\n## Win Rate Drift Analysis\n\n")
            report.append("| Tag | Ref WR | Recent WR | Drift | Drift % | Status |\n")
            report.append("|-----|--------|-----------|-------|---------|--------|\n")
            
            for tag, drift_stats in sorted(drift.items(), key=lambda x: x[1]['drift']):
                status = "🔴 Degrading" if drift_stats['is_degrading'] else "✓ Stable"
                
                report.append(
                    f"| {tag} | {drift_stats['reference_winrate']:.1%} | "
                    f"{drift_stats['recent_winrate']:.1%} | {drift_stats['drift']:+.1%} | "
                    f"{drift_stats['drift_pct']:+.1f}% | {status} |\n"
                )
        
        # ── 4. 滚动胜率详情 ────────────────────────────────────────
        report.append("\n## Rolling Win Rate Details\n\n")
        
        for tag, tag_stats in stats.items():
            if 'rolling_winrate' not in tag_stats:
                continue
            
            report.append(f"### {tag}\n\n")
            report.append("| Window | Current | Mean | Std | Min | Max |\n")
            report.append("|--------|---------|------|-----|-----|-----|\n")
            
            for window_key, window_stats in tag_stats['rolling_winrate'].items():
                report.append(
                    f"| {window_key} | {window_stats['current']:.1%} | "
                    f"{window_stats['mean']:.1%} | {window_stats['std']:.1%} | "
                    f"{window_stats['min']:.1%} | {window_stats['max']:.1%} |\n"
                )
            
            report.append("\n")
        
        return ''.join(report)
    
    def save_report(self, analysis_results: Dict, filepath: str):
        """保存报告到文件"""
        report = self.generate_report(analysis_results)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(report)
        logger.info(f"Enter tag win rate report saved to {filepath}")


# ============================================================================
# 使用示例
# ============================================================================

def track_enter_tag_winrate(
    trades_df: DataFrame,
    reference_period: Tuple[str, str] = None,
    output_path: str = None
) -> Dict:
    """
    跟踪 enter_tag 胜率
    
    Args:
        trades_df: 交易记录 DataFrame
        reference_period: 参考期间 (start_date, end_date)
        output_path: 报告输出路径（可选）
    
    Returns:
        分析结果
    """
    tracker = EnterTagWinRateTracker()
    results = tracker.analyze_enter_tag_performance(trades_df, reference_period)
    
    # 打印报告
    report = tracker.generate_report(results)
    print(report)
    
    # 保存报告
    if output_path:
        tracker.save_report(results, output_path)
    
    return results


if __name__ == '__main__':
    print("Enter Tag Win Rate Tracker ready")
