"""
Dry Run Drift Monitoring (FUT-E7)

Dry Run 漂移监控：
- 监控 dry-run 与回测结果的差异
- 检测字段缺失、数值漂移、逻辑不一致
- 生成漂移诊断报告
- 为 live trading 提供预警
"""

import pandas as pd
import numpy as np
from pandas import DataFrame
from typing import Dict, List, Tuple, Optional
import logging
from datetime import datetime
import json

logger = logging.getLogger(__name__)


class DryRunDriftMonitor:
    """Dry Run 漂移监控器"""
    
    def __init__(self):
        self.drift_threshold = 0.05  # 5% 漂移阈值
        
    def compare_backtest_vs_dryrun(
        self,
        backtest_df: DataFrame,
        dryrun_df: DataFrame,
        key_fields: List[str] = None
    ) -> Dict:
        """
        对比回测与 dry-run 结果
        
        Args:
            backtest_df: 回测结果 DataFrame
            dryrun_df: Dry-run 结果 DataFrame
            key_fields: 需要对比的关键字段列表
            
        Returns:
            漂移分析结果
        """
        if key_fields is None:
            key_fields = self._get_default_key_fields()
        
        results = {
            'timestamp': datetime.now().isoformat(),
            'backtest_bars': len(backtest_df),
            'dryrun_bars': len(dryrun_df),
            'field_comparison': {},
            'missing_fields': {},
            'drift_summary': {},
            'alerts': [],
        }
        
        # ── 1. 字段存在性检查 ──────────────────────────────────────
        results['missing_fields'] = self._check_missing_fields(
            backtest_df, dryrun_df, key_fields
        )
        
        # ── 2. 字段值对比 ──────────────────────────────────────────
        results['field_comparison'] = self._compare_field_values(
            backtest_df, dryrun_df, key_fields
        )
        
        # ── 3. 生成漂移摘要 ────────────────────────────────────────
        results['drift_summary'] = self._generate_drift_summary(
            results['field_comparison']
        )
        
        # ── 4. 生成告警 ────────────────────────────────────────────
        results['alerts'] = self._generate_alerts(results)
        
        return results
    
    def _get_default_key_fields(self) -> List[str]:
        """获取默认关键字段列表"""
        return [
            # Entry signals
            'enter_long',
            'enter_tag',
            
            # Exit signals
            'exit_long',
            'exit_tag',
            
            # Detectors
            'market_env',
            'cm_intent',
            'wyckoff_phase',
            'spring_bar',
            'axis_support',
            
            # Quality
            'spring_quality',
            'axis_quality',
            
            # Daily gate
            'daily_gate_result',
            'daily_gate_pass',
            
            # Confidence
            'market_env_confidence',
            'cm_confidence',
        ]
    
    def _check_missing_fields(
        self,
        backtest_df: DataFrame,
        dryrun_df: DataFrame,
        key_fields: List[str]
    ) -> Dict:
        """检查字段缺失"""
        missing = {
            'in_backtest_only': [],
            'in_dryrun_only': [],
            'in_both': [],
        }
        
        backtest_fields = set(backtest_df.columns)
        dryrun_fields = set(dryrun_df.columns)
        
        for field in key_fields:
            if field in backtest_fields and field in dryrun_fields:
                missing['in_both'].append(field)
            elif field in backtest_fields:
                missing['in_backtest_only'].append(field)
            elif field in dryrun_fields:
                missing['in_dryrun_only'].append(field)
        
        return missing
    
    def _compare_field_values(
        self,
        backtest_df: DataFrame,
        dryrun_df: DataFrame,
        key_fields: List[str]
    ) -> Dict:
        """对比字段值"""
        comparison = {}
        
        for field in key_fields:
            if field not in backtest_df.columns or field not in dryrun_df.columns:
                continue
            
            bt_values = backtest_df[field]
            dr_values = dryrun_df[field]
            
            # 根据字段类型选择对比方法
            if bt_values.dtype in [np.float64, np.float32, np.int64, np.int32]:
                comparison[field] = self._compare_numeric_field(
                    bt_values, dr_values, field
                )
            else:
                comparison[field] = self._compare_categorical_field(
                    bt_values, dr_values, field
                )
        
        return comparison
    
    def _compare_numeric_field(
        self,
        bt_values: pd.Series,
        dr_values: pd.Series,
        field_name: str
    ) -> Dict:
        """对比数值型字段"""
        # 对齐长度（取较短的）
        min_len = min(len(bt_values), len(dr_values))
        bt_values = bt_values.iloc[-min_len:]
        dr_values = dr_values.iloc[-min_len:]
        
        # 计算统计量
        bt_mean = bt_values.mean()
        dr_mean = dr_values.mean()
        
        bt_std = bt_values.std()
        dr_std = dr_values.std()
        
        # 计算漂移
        mean_drift = abs(bt_mean - dr_mean) / (abs(bt_mean) + 1e-9)
        std_drift = abs(bt_std - dr_std) / (abs(bt_std) + 1e-9)
        
        # 相关性
        correlation = bt_values.corr(dr_values) if len(bt_values) > 1 else np.nan
        
        return {
            'type': 'numeric',
            'backtest_mean': round(float(bt_mean), 4),
            'dryrun_mean': round(float(dr_mean), 4),
            'backtest_std': round(float(bt_std), 4),
            'dryrun_std': round(float(dr_std), 4),
            'mean_drift': round(float(mean_drift), 4),
            'std_drift': round(float(std_drift), 4),
            'correlation': round(float(correlation), 4) if not np.isnan(correlation) else None,
            'has_drift': mean_drift > self.drift_threshold,
        }
    
    def _compare_categorical_field(
        self,
        bt_values: pd.Series,
        dr_values: pd.Series,
        field_name: str
    ) -> Dict:
        """对比分类型字段"""
        # 对齐长度
        min_len = min(len(bt_values), len(dr_values))
        bt_values = bt_values.iloc[-min_len:]
        dr_values = dr_values.iloc[-min_len:]
        
        # 分布对比
        bt_dist = bt_values.value_counts(normalize=True).to_dict()
        dr_dist = dr_values.value_counts(normalize=True).to_dict()
        
        # 计算分布差异（KL 散度的简化版）
        all_categories = set(bt_dist.keys()) | set(dr_dist.keys())
        distribution_diff = 0.0
        
        for cat in all_categories:
            bt_prob = bt_dist.get(cat, 0)
            dr_prob = dr_dist.get(cat, 0)
            distribution_diff += abs(bt_prob - dr_prob)
        
        # 一致性（相同值的比例）
        agreement = (bt_values == dr_values).sum() / len(bt_values)
        
        return {
            'type': 'categorical',
            'backtest_distribution': bt_dist,
            'dryrun_distribution': dr_dist,
            'distribution_diff': round(distribution_diff, 4),
            'agreement_rate': round(float(agreement), 4),
            'has_drift': distribution_diff > self.drift_threshold * 2,  # 分类字段用更宽松的阈值
        }
    
    def _generate_drift_summary(self, field_comparison: Dict) -> Dict:
        """生成漂移摘要"""
        summary = {
            'total_fields': len(field_comparison),
            'fields_with_drift': 0,
            'numeric_fields': 0,
            'categorical_fields': 0,
            'high_drift_fields': [],
            'low_correlation_fields': [],
        }
        
        for field, comp in field_comparison.items():
            if comp.get('has_drift', False):
                summary['fields_with_drift'] += 1
                
                # 高漂移字段
                if comp['type'] == 'numeric':
                    if comp.get('mean_drift', 0) > self.drift_threshold * 2:
                        summary['high_drift_fields'].append({
                            'field': field,
                            'drift': comp['mean_drift']
                        })
                    
                    # 低相关性字段
                    if comp.get('correlation') is not None and comp['correlation'] < 0.8:
                        summary['low_correlation_fields'].append({
                            'field': field,
                            'correlation': comp['correlation']
                        })
                    
                    summary['numeric_fields'] += 1
                else:
                    if comp.get('distribution_diff', 0) > self.drift_threshold * 4:
                        summary['high_drift_fields'].append({
                            'field': field,
                            'drift': comp['distribution_diff']
                        })
                    
                    summary['categorical_fields'] += 1
        
        return summary
    
    def _generate_alerts(self, results: Dict) -> List[Dict]:
        """生成告警"""
        alerts = []
        
        # ── Alert 1：字段缺失 ──────────────────────────────────────
        missing = results['missing_fields']
        if missing['in_backtest_only']:
            alerts.append({
                'level': 'error',
                'type': 'missing_fields',
                'message': f"Fields missing in dry-run: {missing['in_backtest_only']}",
                'fields': missing['in_backtest_only']
            })
        
        # ── Alert 2：高漂移字段 ────────────────────────────────────
        high_drift = results['drift_summary'].get('high_drift_fields', [])
        if high_drift:
            alerts.append({
                'level': 'warning',
                'type': 'high_drift',
                'message': f"High drift detected in {len(high_drift)} fields",
                'fields': [f['field'] for f in high_drift]
            })
        
        # ── Alert 3：低相关性 ──────────────────────────────────────
        low_corr = results['drift_summary'].get('low_correlation_fields', [])
        if low_corr:
            alerts.append({
                'level': 'warning',
                'type': 'low_correlation',
                'message': f"Low correlation detected in {len(low_corr)} fields",
                'fields': [f['field'] for f in low_corr]
            })
        
        # ── Alert 4：整体漂移率过高 ────────────────────────────────
        drift_rate = results['drift_summary']['fields_with_drift'] / max(results['drift_summary']['total_fields'], 1)
        if drift_rate > 0.3:  # 超过 30% 字段有漂移
            alerts.append({
                'level': 'critical',
                'type': 'high_drift_rate',
                'message': f"High overall drift rate: {drift_rate:.1%}",
                'drift_rate': round(drift_rate, 3)
            })
        
        return alerts
    
    def generate_report(self, drift_results: Dict) -> str:
        """生成漂移监控报告"""
        report = ["# Dry Run Drift Monitoring Report\n"]
        
        report.append(f"Generated: {drift_results['timestamp']}\n\n")
        report.append(f"- Backtest Bars: {drift_results['backtest_bars']}\n")
        report.append(f"- Dry-run Bars: {drift_results['dryrun_bars']}\n\n")
        
        # ── 1. 告警 ────────────────────────────────────────────────
        alerts = drift_results.get('alerts', [])
        if alerts:
            report.append("## ⚠️ Alerts\n\n")
            for alert in alerts:
                level_emoji = {
                    'critical': '🔴',
                    'error': '❌',
                    'warning': '⚠️',
                    'info': 'ℹ️'
                }.get(alert['level'], '•')
                
                report.append(f"{level_emoji} **{alert['level'].upper()}**: {alert['message']}\n")
            report.append("\n")
        
        # ── 2. 漂移摘要 ────────────────────────────────────────────
        summary = drift_results.get('drift_summary', {})
        report.append("## Drift Summary\n\n")
        report.append(f"- Total Fields Compared: {summary.get('total_fields', 0)}\n")
        report.append(f"- Fields with Drift: {summary.get('fields_with_drift', 0)}\n")
        report.append(f"- Numeric Fields: {summary.get('numeric_fields', 0)}\n")
        report.append(f"- Categorical Fields: {summary.get('categorical_fields', 0)}\n\n")
        
        # ── 3. 字段对比详情 ────────────────────────────────────────
        report.append("## Field Comparison Details\n\n")
        
        field_comp = drift_results.get('field_comparison', {})
        for field, comp in sorted(field_comp.items()):
            drift_mark = "⚠️" if comp.get('has_drift', False) else "✓"
            report.append(f"### {drift_mark} {field}\n\n")
            
            if comp['type'] == 'numeric':
                report.append(f"- Type: Numeric\n")
                report.append(f"- Backtest Mean: {comp['backtest_mean']}\n")
                report.append(f"- Dry-run Mean: {comp['dryrun_mean']}\n")
                report.append(f"- Mean Drift: {comp['mean_drift']:.2%}\n")
                if comp.get('correlation') is not None:
                    report.append(f"- Correlation: {comp['correlation']:.3f}\n")
            else:
                report.append(f"- Type: Categorical\n")
                report.append(f"- Distribution Diff: {comp['distribution_diff']:.2%}\n")
                report.append(f"- Agreement Rate: {comp['agreement_rate']:.2%}\n")
            
            report.append("\n")
        
        # ── 4. 缺失字段 ────────────────────────────────────────────
        missing = drift_results.get('missing_fields', {})
        if missing['in_backtest_only'] or missing['in_dryrun_only']:
            report.append("## Missing Fields\n\n")
            
            if missing['in_backtest_only']:
                report.append("### In Backtest Only (Missing in Dry-run)\n")
                for field in missing['in_backtest_only']:
                    report.append(f"- {field}\n")
                report.append("\n")
            
            if missing['in_dryrun_only']:
                report.append("### In Dry-run Only (Missing in Backtest)\n")
                for field in missing['in_dryrun_only']:
                    report.append(f"- {field}\n")
                report.append("\n")
        
        return ''.join(report)
    
    def save_report(self, drift_results: Dict, filepath: str):
        """保存报告到文件"""
        report = self.generate_report(drift_results)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(report)
        logger.info(f"Drift monitoring report saved to {filepath}")


# ============================================================================
# 使用示例
# ============================================================================

def monitor_dryrun_drift(
    backtest_df: DataFrame,
    dryrun_df: DataFrame,
    output_path: str = None
) -> Dict:
    """
    监控 dry-run 漂移
    
    Args:
        backtest_df: 回测结果 DataFrame
        dryrun_df: Dry-run 结果 DataFrame
        output_path: 报告输出路径（可选）
    
    Returns:
        漂移分析结果
    """
    monitor = DryRunDriftMonitor()
    results = monitor.compare_backtest_vs_dryrun(backtest_df, dryrun_df)
    
    # 打印报告
    report = monitor.generate_report(results)
    print(report)
    
    # 保存报告
    if output_path:
        monitor.save_report(results, output_path)
    
    return results


if __name__ == '__main__':
    print("Dry Run Drift Monitor ready")
