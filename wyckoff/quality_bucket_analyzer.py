"""
RM-3: Quality Bucket 绩效映射分析器

目标：验证质量档位与真实交易绩效的映射关系

核心问题：
- 高质量信号组的真实收益/PF/胜率是否显著优于低质量组？
- 质量分层关系是否稳定（跨时间段、跨标的）？
- 质量评分系统是否存在偏差？

使用方法：
1. 收集回测结果（包含 enter_tag 和质量评分）
2. 按质量分层统计绩效
3. 验证分层稳定性
4. 生成验证报告

只有当验证通过后，才允许启用动态仓位系统。

作者：Kiro AI
日期：2026-03-14
版本：v1.0 - 初始实现
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
import logging
import json
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class QualityBucketStats:
    """质量分层统计"""
    tier: str
    count: int
    win_rate: float
    avg_profit: float
    avg_loss: float
    profit_factor: float
    total_profit: float
    max_drawdown: float
    avg_duration_hours: float
    sharpe_ratio: float


class QualityBucketAnalyzer:
    """
    质量分层绩效分析器
    
    功能：
    1. 从回测结果提取交易数据
    2. 按质量分层统计绩效
    3. 验证分层稳定性
    4. 生成验证报告
    """
    
    def __init__(self):
        """初始化分析器"""
        self.trades: List[Dict] = []
        self.quality_buckets: Dict[str, List[Dict]] = {
            'exceptional': [],
            'high': [],
            'medium': [],
            'low': [],
            'very_low': [],
        }
    
    def load_trades_from_backtest(
        self,
        backtest_result_path: str,
        quality_mapping: Optional[Dict[str, str]] = None
    ) -> int:
        """
        从回测结果加载交易数据
        
        Args:
            backtest_result_path: 回测结果文件路径（.json 或 .zip）
            quality_mapping: 信号类型到质量分层的映射（可选）
        
        Returns:
            加载的交易数量
        """
        try:
            # 支持 .zip 文件
            if backtest_result_path.endswith('.zip'):
                import zipfile
                import tempfile
                with zipfile.ZipFile(backtest_result_path, 'r') as zip_ref:
                    # 查找 JSON 文件
                    json_files = [f for f in zip_ref.namelist() if f.endswith('.json')]
                    if not json_files:
                        logger.error(f"No JSON file found in {backtest_result_path}")
                        return 0
                    
                    # 读取第一个 JSON 文件
                    with zip_ref.open(json_files[0]) as f:
                        data = json.load(f)
            else:
                with open(backtest_result_path, 'r') as f:
                    data = json.load(f)
            
            # 提取交易列表
            trades = None
            if 'trades' in data:
                trades = data['trades']
            elif 'results' in data and 'trades' in data['results']:
                trades = data['results']['trades']
            elif 'strategy' in data:
                # Freqtrade 新格式：data['strategy']['StrategyName']['trades']
                strategy_data = data['strategy']
                if isinstance(strategy_data, dict):
                    # 获取第一个策略的数据
                    for strategy_name, strategy_results in strategy_data.items():
                        if isinstance(strategy_results, dict) and 'trades' in strategy_results:
                            trades = strategy_results['trades']
                            logger.info(f"Found {len(trades)} trades for strategy: {strategy_name}")
                            break
                        elif isinstance(strategy_results, dict) and 'results' in strategy_results:
                            trades = strategy_results['results']
                            logger.info(f"Found {len(trades)} trades for strategy: {strategy_name}")
                            break
            
            if trades is None:
                logger.error(f"Cannot find trades in {backtest_result_path}")
                logger.error(f"Available keys: {list(data.keys())}")
                if 'strategy' in data:
                    logger.error(f"Strategy keys: {list(data['strategy'].keys())}")
                return 0
            
            # 处理每笔交易
            for trade in trades:
                # 提取关键信息
                trade_data = {
                    'pair': trade.get('pair', 'UNKNOWN'),
                    'open_date': trade.get('open_date', ''),
                    'close_date': trade.get('close_date', ''),
                    'profit_ratio': trade.get('profit_ratio', 0.0),
                    'profit_abs': trade.get('profit_abs', 0.0),
                    'enter_tag': trade.get('enter_tag', 'unknown'),
                    'exit_reason': trade.get('exit_reason', 'unknown'),
                    'duration': trade.get('duration', 0),
                }
                
                # 推断质量分层
                quality_tier = self._infer_quality_tier(
                    trade_data['enter_tag'],
                    quality_mapping
                )
                trade_data['quality_tier'] = quality_tier
                
                # 添加到总列表和分层列表
                self.trades.append(trade_data)
                if quality_tier in self.quality_buckets:
                    self.quality_buckets[quality_tier].append(trade_data)
            
            logger.info(
                f"✅ Loaded {len(self.trades)} trades from {backtest_result_path}"
            )
            return len(self.trades)
        
        except Exception as e:
            logger.error(f"Failed to load trades: {e}")
            return 0
    
    def _infer_quality_tier(
        self,
        enter_tag: str,
        quality_mapping: Optional[Dict[str, str]] = None
    ) -> str:
        """
        从 enter_tag 推断质量分层
        
        基于实际回测表现调整的映射规则：
        - wave_start: 97.5% 胜率 → high
        - axis_support: 68% 胜率，主力信号 → medium
        - sos: 80% 胜率 → medium
        - lps: 85.7% 胜率 → high
        
        Args:
            enter_tag: 入场标签
            quality_mapping: 自定义映射（可选）
        
        Returns:
            质量分层字符串
        """
        # 使用自定义映射
        if quality_mapping and enter_tag in quality_mapping:
            return quality_mapping[enter_tag]
        
        # 基于实际表现的映射规则
        enter_tag_lower = enter_tag.lower()
        
        # Exceptional (90-100): Spring #3, 共振信号, Spring Test 显著K线
        if any(x in enter_tag_lower for x in [
            'spring_3', 'spring3',
            'axis_sos_confluence',  # 轴线+SOS共振
            'spring_test_sig',      # Spring Test 显著K线（实际 50% 胜率，但样本少）
            'phase_d_hq',           # Phase D 高质量
        ]):
            return 'exceptional'
        
        # High (70-89): Wave Start (97.5%), LPS (85.7%), Spring #2, Phase D, BUEC, Efficiency
        if any(x in enter_tag_lower for x in [
            'wave_start',           # 波段起点（97.5% 胜率）
            'lps',                  # LPS 回测入场（85.7% 胜率）
            'spring_2', 'spring2',
            'phase_d',              # Phase D 确认入场（非 hq）
            'buec',                 # BUEC 入场
            'efficiency',           # 效率突破（100% 胜率，但样本少）
        ]):
            return 'high'
        
        # Medium (50-69): Axis Support (68%), SOS (80%), JAC
        if any(x in enter_tag_lower for x in [
            'axis_support',         # 轴线支撑（68% 胜率，主力信号）
            'sos',                  # SOS（80% 胜率）
            'jac',                  # JAC 入场
        ]):
            return 'medium'
        
        # Low (30-49): Spring #1, Spring 恢复（未分级）, UTAD (33.3%)
        if any(x in enter_tag_lower for x in [
            'spring_1', 'spring1',
            'spring',               # Spring 恢复（未分级）
            'utad',                 # UTAD（33.3% 胜率）
        ]):
            return 'low'
        
        # Very Low (0-29): SOW (33.3%), LPSY
        if any(x in enter_tag_lower for x in [
            'sow',                  # SOW（33.3% 胜率）
            'lpsy',
        ]):
            return 'very_low'
        
        # 默认：medium
        return 'medium'
    
    def calculate_bucket_stats(self, tier: str) -> Optional[QualityBucketStats]:
        """
        计算单个质量分层的统计数据
        
        Args:
            tier: 质量分层名称
        
        Returns:
            统计数据对象或 None
        """
        trades = self.quality_buckets.get(tier, [])
        if not trades:
            return None
        
        # 基础统计
        count = len(trades)
        profits = [t['profit_ratio'] for t in trades]
        wins = [p for p in profits if p > 0]
        losses = [p for p in profits if p < 0]
        
        win_rate = len(wins) / count if count > 0 else 0.0
        avg_profit = np.mean(wins) if wins else 0.0
        avg_loss = np.mean(losses) if losses else 0.0
        total_profit = sum(profits)
        
        # Profit Factor
        total_wins = sum(wins) if wins else 0.0
        total_losses = abs(sum(losses)) if losses else 0.0
        profit_factor = total_wins / total_losses if total_losses > 0 else float('inf')
        
        # 最大回撤
        cumulative = np.cumsum(profits)
        running_max = np.maximum.accumulate(cumulative)
        drawdown = running_max - cumulative
        max_drawdown = np.max(drawdown) if len(drawdown) > 0 else 0.0
        
        # 平均持仓时间（分钟转小时）
        durations = [t['duration'] for t in trades]
        avg_duration_hours = np.mean(durations) / 60.0 if durations else 0.0
        
        # Sharpe Ratio（简化版本）
        if len(profits) > 1:
            sharpe_ratio = np.mean(profits) / (np.std(profits) + 1e-9)
        else:
            sharpe_ratio = 0.0
        
        return QualityBucketStats(
            tier=tier,
            count=count,
            win_rate=win_rate,
            avg_profit=avg_profit,
            avg_loss=avg_loss,
            profit_factor=profit_factor,
            total_profit=total_profit,
            max_drawdown=max_drawdown,
            avg_duration_hours=avg_duration_hours,
            sharpe_ratio=sharpe_ratio,
        )
    
    def analyze_all_buckets(self) -> Dict[str, QualityBucketStats]:
        """
        分析所有质量分层
        
        Returns:
            分层统计字典
        """
        results = {}
        for tier in self.quality_buckets.keys():
            stats = self.calculate_bucket_stats(tier)
            if stats:
                results[tier] = stats
        return results
    
    def validate_quality_mapping(
        self,
        min_sample_size: int = 10
    ) -> Tuple[bool, Dict]:
        """
        验证质量映射是否有效
        
        验证标准：
        1. 高质量组的胜率 > 低质量组
        2. 高质量组的 PF > 低质量组
        3. 高质量组的平均收益 > 低质量组
        4. 各分层样本量充足
        
        Args:
            min_sample_size: 最小样本量要求
        
        Returns:
            (是否通过验证, 验证详情字典)
        """
        stats = self.analyze_all_buckets()
        
        validation_result = {
            'passed': False,
            'checks': {},
            'warnings': [],
            'recommendations': [],
        }
        
        # 检查1：样本量充足性
        sample_check = True
        for tier, stat in stats.items():
            if stat.count < min_sample_size:
                sample_check = False
                validation_result['warnings'].append(
                    f"{tier} tier has only {stat.count} samples (min: {min_sample_size})"
                )
        validation_result['checks']['sufficient_samples'] = sample_check
        
        # 检查2：胜率单调性（高质量 > 低质量）
        tier_order = ['exceptional', 'high', 'medium', 'low', 'very_low']
        win_rates = [stats[t].win_rate for t in tier_order if t in stats]
        win_rate_monotonic = all(
            win_rates[i] >= win_rates[i+1] 
            for i in range(len(win_rates)-1)
        )
        validation_result['checks']['win_rate_monotonic'] = win_rate_monotonic
        
        if not win_rate_monotonic:
            validation_result['warnings'].append(
                "Win rate is not monotonically decreasing across quality tiers"
            )
        
        # 检查3：Profit Factor 单调性
        pfs = [stats[t].profit_factor for t in tier_order if t in stats]
        pf_monotonic = all(
            pfs[i] >= pfs[i+1] 
            for i in range(len(pfs)-1)
        )
        validation_result['checks']['pf_monotonic'] = pf_monotonic
        
        if not pf_monotonic:
            validation_result['warnings'].append(
                "Profit Factor is not monotonically decreasing across quality tiers"
            )
        
        # 检查4：显著性差异（exceptional vs very_low）
        if 'exceptional' in stats and 'very_low' in stats:
            exc_wr = stats['exceptional'].win_rate
            vl_wr = stats['very_low'].win_rate
            significant_diff = (exc_wr - vl_wr) > 0.15  # 至少15%差异
            validation_result['checks']['significant_difference'] = significant_diff
            
            if not significant_diff:
                validation_result['warnings'].append(
                    f"Win rate difference between exceptional and very_low is only "
                    f"{(exc_wr - vl_wr)*100:.1f}% (expected >15%)"
                )
        
        # 综合判断
        all_checks_passed = all(validation_result['checks'].values())
        validation_result['passed'] = all_checks_passed
        
        # 生成建议
        if all_checks_passed:
            validation_result['recommendations'].append(
                "✅ Quality mapping is validated. Safe to enable dynamic position sizing."
            )
        else:
            validation_result['recommendations'].append(
                "⚠️ Quality mapping validation failed. DO NOT enable dynamic position sizing."
            )
            validation_result['recommendations'].append(
                "Recommended actions:"
            )
            validation_result['recommendations'].append(
                "1. Collect more samples for under-represented tiers"
            )
            validation_result['recommendations'].append(
                "2. Review quality scoring logic for inconsistencies"
            )
            validation_result['recommendations'].append(
                "3. Consider adjusting quality tier thresholds"
            )
        
        return all_checks_passed, validation_result
    
    def generate_report(
        self,
        output_path: Optional[str] = None
    ) -> str:
        """
        生成验证报告
        
        Args:
            output_path: 输出文件路径（可选）
        
        Returns:
            报告文本
        """
        stats = self.analyze_all_buckets()
        passed, validation = self.validate_quality_mapping()
        
        # 构建报告
        lines = []
        lines.append("=" * 80)
        lines.append("Quality Bucket Analysis Report")
        lines.append("=" * 80)
        lines.append(f"Total Trades: {len(self.trades)}")
        lines.append(f"Analysis Date: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")
        
        # 分层统计
        lines.append("Quality Tier Statistics:")
        lines.append("-" * 80)
        lines.append(
            f"{'Tier':<15} {'Count':<8} {'WinRate':<10} {'AvgProfit':<12} "
            f"{'PF':<8} {'Sharpe':<8} {'MaxDD':<10}"
        )
        lines.append("-" * 80)
        
        tier_order = ['exceptional', 'high', 'medium', 'low', 'very_low']
        for tier in tier_order:
            if tier in stats:
                s = stats[tier]
                lines.append(
                    f"{tier:<15} {s.count:<8} {s.win_rate*100:>8.1f}% "
                    f"{s.avg_profit*100:>10.2f}% {s.profit_factor:>6.2f}  "
                    f"{s.sharpe_ratio:>6.2f}  {s.max_drawdown*100:>8.2f}%"
                )
        
        lines.append("")
        
        # 验证结果
        lines.append("Validation Results:")
        lines.append("-" * 80)
        lines.append(f"Overall: {'✅ PASSED' if passed else '❌ FAILED'}")
        lines.append("")
        
        for check_name, check_result in validation['checks'].items():
            status = "✅" if check_result else "❌"
            lines.append(f"  {status} {check_name}")
        
        lines.append("")
        
        # 警告
        if validation['warnings']:
            lines.append("Warnings:")
            for warning in validation['warnings']:
                lines.append(f"  ⚠️ {warning}")
            lines.append("")
        
        # 建议
        lines.append("Recommendations:")
        for rec in validation['recommendations']:
            lines.append(f"  {rec}")
        
        lines.append("")
        lines.append("=" * 80)
        
        report = "\n".join(lines)
        
        # 输出到文件
        if output_path:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(report)
            logger.info(f"✅ Report saved to {output_path}")
        
        return report


# ============================================================================
# 命令行工具
# ============================================================================

def main():
    """命令行入口"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Analyze quality bucket performance mapping"
    )
    parser.add_argument(
        'backtest_result',
        help='Path to backtest result JSON file'
    )
    parser.add_argument(
        '--output',
        '-o',
        help='Output report path',
        default='quality_bucket_report.txt'
    )
    parser.add_argument(
        '--min-samples',
        type=int,
        default=10,
        help='Minimum sample size per tier'
    )
    
    args = parser.parse_args()
    
    # 创建分析器
    analyzer = QualityBucketAnalyzer()
    
    # 加载数据
    count = analyzer.load_trades_from_backtest(args.backtest_result)
    if count == 0:
        print("❌ No trades loaded. Exiting.")
        return
    
    # 生成报告
    report = analyzer.generate_report(args.output)
    print(report)
    
    # 验证
    passed, _ = analyzer.validate_quality_mapping(args.min_samples)
    
    if passed:
        print("\n✅ Validation PASSED. Safe to enable dynamic position sizing.")
        return 0
    else:
        print("\n❌ Validation FAILED. DO NOT enable dynamic position sizing.")
        return 1


if __name__ == '__main__':
    import sys
    sys.exit(main())
