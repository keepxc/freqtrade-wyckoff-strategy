"""
Detector Activation/Conflict/Fallback Monitoring Panel (FUT-E6)

检测器激活率 / 冲突率 / fallback 率统计面板：
- 统计各检测器的激活频率
- 检测信号冲突（多个互斥信号同时激活）
- 监控 fallback 机制触发率
- 生成诊断报告
"""

import pandas as pd
import numpy as np
from pandas import DataFrame
from typing import Dict, List, Tuple
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)


class DetectorMonitoringPanel:
    """检测器监控面板"""
    
    def __init__(self):
        self.stats = defaultdict(lambda: defaultdict(int))
        
    def analyze_dataframe(self, dataframe: DataFrame) -> Dict:
        """
        分析 DataFrame 中的检测器激活情况
        
        Args:
            dataframe: 包含所有检测器输出字段的 DataFrame
            
        Returns:
            统计结果字典
        """
        total_bars = len(dataframe)
        
        if total_bars == 0:
            return {'error': 'Empty dataframe'}
        
        results = {
            'total_bars': total_bars,
            'activation_rates': {},
            'conflict_analysis': {},
            'fallback_analysis': {},
            'phase_distribution': {},
            'signal_quality': {},
        }
        
        # ── 1. 激活率统计 ──────────────────────────────────────────
        results['activation_rates'] = self._analyze_activation_rates(dataframe)
        
        # ── 2. 冲突分析 ────────────────────────────────────────────
        results['conflict_analysis'] = self._analyze_conflicts(dataframe)
        
        # ── 3. Fallback 分析 ───────────────────────────────────────
        results['fallback_analysis'] = self._analyze_fallbacks(dataframe)
        
        # ── 4. Phase 分布 ──────────────────────────────────────────
        results['phase_distribution'] = self._analyze_phase_distribution(dataframe)
        
        # ── 5. 信号质量 ────────────────────────────────────────────
        results['signal_quality'] = self._analyze_signal_quality(dataframe)
        
        return results
    
    def _analyze_activation_rates(self, df: DataFrame) -> Dict:
        """分析各检测器激活率"""
        total = len(df)
        rates = {}
        
        # Daily Event Detector
        if 'daily_bc' in df.columns:
            rates['daily_bc'] = {
                'count': int(df['daily_bc'].sum()),
                'rate': round(df['daily_bc'].sum() / total * 100, 2)
            }
        
        if 'daily_buec' in df.columns:
            rates['daily_buec'] = {
                'count': int(df['daily_buec'].sum()),
                'rate': round(df['daily_buec'].sum() / total * 100, 2)
            }
        
        # Event Sequence State Machine
        if 'ps_bar' in df.columns:
            rates['ps_bar'] = {
                'count': int(df['ps_bar'].sum()),
                'rate': round(df['ps_bar'].sum() / total * 100, 2)
            }
        
        if 'sc_bar' in df.columns:
            rates['sc_bar'] = {
                'count': int(df['sc_bar'].sum()),
                'rate': round(df['sc_bar'].sum() / total * 100, 2)
            }
        
        if 'spring_bar' in df.columns:
            rates['spring_bar'] = {
                'count': int(df['spring_bar'].sum()),
                'rate': round(df['spring_bar'].sum() / total * 100, 2)
            }
        
        # Market Environment Detector
        if 'market_env' in df.columns:
            env_counts = df['market_env'].value_counts()
            for env, count in env_counts.items():
                rates[f'market_env_{env}'] = {
                    'count': int(count),
                    'rate': round(count / total * 100, 2)
                }
        
        # Composite Man Detector
        if 'cm_intent' in df.columns:
            intent_counts = df['cm_intent'].value_counts()
            for intent, count in intent_counts.items():
                rates[f'cm_intent_{intent}'] = {
                    'count': int(count),
                    'rate': round(count / total * 100, 2)
                }
        
        return rates
    
    def _analyze_conflicts(self, df: DataFrame) -> Dict:
        """分析信号冲突"""
        conflicts = {}
        total = len(df)
        
        # ── 冲突 1：Spring vs SOW（互斥信号）──────────────────────
        if 'spring_bar' in df.columns and 'sow_bar' in df.columns:
            conflict_count = ((df['spring_bar'] == 1) & (df['sow_bar'] == 1)).sum()
            conflicts['spring_vs_sow'] = {
                'count': int(conflict_count),
                'rate': round(conflict_count / total * 100, 4),
                'severity': 'high' if conflict_count > 0 else 'none'
            }
        
        # ── 冲突 2：Accumulation vs Distribution（互斥意图）──────
        if 'cm_intent' in df.columns:
            # 检查是否有快速切换（可能是噪音）
            intent_changes = (df['cm_intent'] != df['cm_intent'].shift(1)).sum()
            conflicts['cm_intent_rapid_changes'] = {
                'count': int(intent_changes),
                'rate': round(intent_changes / total * 100, 2),
                'severity': 'medium' if intent_changes > total * 0.3 else 'low'
            }
        
        # ── 冲突 3：Market Env vs Phase（逻辑一致性）─────────────
        if 'market_env' in df.columns and 'wyckoff_phase' in df.columns:
            # markdown 环境下不应该有 Phase D/E（上涨阶段）
            markdown_with_phase_d = (
                (df['market_env'] == 'markdown') & 
                (df['wyckoff_phase'].isin(['D', 'E']))
            ).sum()
            
            conflicts['markdown_with_markup_phase'] = {
                'count': int(markdown_with_phase_d),
                'rate': round(markdown_with_phase_d / total * 100, 2),
                'severity': 'medium' if markdown_with_phase_d > 0 else 'none'
            }
        
        # ── 冲突 4：Daily Gate 冲突 ────────────────────────────────
        if 'daily_gate_result' in df.columns:
            gate_conflicts = (df['daily_gate_result'] == 'conflict').sum()
            conflicts['daily_gate_conflicts'] = {
                'count': int(gate_conflicts),
                'rate': round(gate_conflicts / total * 100, 2),
                'severity': 'high' if gate_conflicts > total * 0.1 else 'low'
            }
        
        return conflicts
    
    def _analyze_fallbacks(self, df: DataFrame) -> Dict:
        """分析 fallback 机制触发情况"""
        fallbacks = {}
        total = len(df)
        
        # ── Fallback 1：Market Env Transition ─────────────────────
        if 'market_env' in df.columns:
            transition_count = (df['market_env'] == 'transition').sum()
            fallbacks['market_env_transition'] = {
                'count': int(transition_count),
                'rate': round(transition_count / total * 100, 2),
                'description': 'Market env unclear, fallback to EMA'
            }
        
        # ── Fallback 2：Composite Man Neutral ─────────────────────
        if 'cm_intent' in df.columns:
            neutral_count = (df['cm_intent'] == 'neutral').sum()
            fallbacks['cm_intent_neutral'] = {
                'count': int(neutral_count),
                'rate': round(neutral_count / total * 100, 2),
                'description': 'Composite man intent unclear'
            }
        
        # ── Fallback 3：Daily Gate Soft ───────────────────────────
        if 'daily_gate_result' in df.columns:
            soft_count = (df['daily_gate_result'] == 'soft').sum()
            fallbacks['daily_gate_soft'] = {
                'count': int(soft_count),
                'rate': round(soft_count / total * 100, 2),
                'description': 'Daily gate soft mode (no hard block)'
            }
        
        # ── Fallback 4：低置信度信号 ──────────────────────────────
        confidence_fields = [
            'market_env_confidence',
            'cm_confidence',
            'daily_event_confidence',
        ]
        
        for field in confidence_fields:
            if field in df.columns:
                low_conf_count = (df[field] < 0.5).sum()
                fallbacks[f'{field}_low'] = {
                    'count': int(low_conf_count),
                    'rate': round(low_conf_count / total * 100, 2),
                    'description': f'{field} below 0.5 threshold'
                }
        
        return fallbacks
    
    def _analyze_phase_distribution(self, df: DataFrame) -> Dict:
        """分析 Wyckoff Phase 分布"""
        if 'wyckoff_phase' not in df.columns:
            return {}
        
        total = len(df)
        phase_counts = df['wyckoff_phase'].value_counts()
        
        distribution = {}
        for phase, count in phase_counts.items():
            distribution[f'phase_{phase}'] = {
                'count': int(count),
                'rate': round(count / total * 100, 2)
            }
        
        return distribution
    
    def _analyze_signal_quality(self, df: DataFrame) -> Dict:
        """分析信号质量"""
        quality = {}
        
        # ── 质量 1：Spring 质量分布 ────────────────────────────────
        if 'spring_quality' in df.columns:
            quality_counts = df[df['spring_bar'] == 1]['spring_quality'].value_counts()
            quality['spring_quality_dist'] = quality_counts.to_dict()
        
        # ── 质量 2：Axis 质量分布 ──────────────────────────────────
        if 'axis_quality' in df.columns:
            quality_counts = df[df['axis_support'] == 1]['axis_quality'].value_counts()
            quality['axis_quality_dist'] = quality_counts.to_dict()
        
        # ── 质量 3：平均置信度 ────────────────────────────────────
        confidence_fields = [
            'market_env_confidence',
            'cm_confidence',
            'daily_event_confidence',
        ]
        
        for field in confidence_fields:
            if field in df.columns:
                quality[f'{field}_mean'] = round(df[field].mean(), 3)
                quality[f'{field}_std'] = round(df[field].std(), 3)
        
        return quality
    
    def generate_report(self, analysis_results: Dict) -> str:
        """生成监控报告"""
        report = ["# Detector Monitoring Report\n"]
        
        total_bars = analysis_results.get('total_bars', 0)
        report.append(f"Total Bars Analyzed: {total_bars}\n\n")
        
        # ── 1. 激活率 ──────────────────────────────────────────────
        report.append("## 1. Activation Rates\n\n")
        report.append("| Detector/Signal | Count | Rate (%) |\n")
        report.append("|-----------------|-------|----------|\n")
        
        activation_rates = analysis_results.get('activation_rates', {})
        for signal, stats in sorted(activation_rates.items()):
            report.append(f"| {signal} | {stats['count']} | {stats['rate']}% |\n")
        
        # ── 2. 冲突分析 ────────────────────────────────────────────
        report.append("\n## 2. Conflict Analysis\n\n")
        report.append("| Conflict Type | Count | Rate (%) | Severity |\n")
        report.append("|---------------|-------|----------|----------|\n")
        
        conflicts = analysis_results.get('conflict_analysis', {})
        for conflict_type, stats in sorted(conflicts.items()):
            report.append(
                f"| {conflict_type} | {stats['count']} | "
                f"{stats['rate']}% | {stats['severity']} |\n"
            )
        
        # ── 3. Fallback 分析 ───────────────────────────────────────
        report.append("\n## 3. Fallback Analysis\n\n")
        report.append("| Fallback Type | Count | Rate (%) | Description |\n")
        report.append("|---------------|-------|----------|-------------|\n")
        
        fallbacks = analysis_results.get('fallback_analysis', {})
        for fb_type, stats in sorted(fallbacks.items()):
            desc = stats.get('description', '')
            report.append(
                f"| {fb_type} | {stats['count']} | "
                f"{stats['rate']}% | {desc} |\n"
            )
        
        # ── 4. Phase 分布 ──────────────────────────────────────────
        phase_dist = analysis_results.get('phase_distribution', {})
        if phase_dist:
            report.append("\n## 4. Phase Distribution\n\n")
            report.append("| Phase | Count | Rate (%) |\n")
            report.append("|-------|-------|----------|\n")
            
            for phase, stats in sorted(phase_dist.items()):
                report.append(f"| {phase} | {stats['count']} | {stats['rate']}% |\n")
        
        # ── 5. 信号质量 ────────────────────────────────────────────
        signal_quality = analysis_results.get('signal_quality', {})
        if signal_quality:
            report.append("\n## 5. Signal Quality\n\n")
            
            for metric, value in sorted(signal_quality.items()):
                if isinstance(value, dict):
                    report.append(f"\n### {metric}\n")
                    for k, v in value.items():
                        report.append(f"- {k}: {v}\n")
                else:
                    report.append(f"- {metric}: {value}\n")
        
        return ''.join(report)
    
    def save_report(self, analysis_results: Dict, filepath: str):
        """保存报告到文件"""
        report = self.generate_report(analysis_results)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(report)
        logger.info(f"Detector monitoring report saved to {filepath}")


# ============================================================================
# 使用示例
# ============================================================================

def analyze_backtest_results(dataframe: DataFrame, output_path: str = None):
    """
    分析回测结果中的检测器表现
    
    Args:
        dataframe: 回测后的完整 DataFrame
        output_path: 报告输出路径（可选）
    """
    panel = DetectorMonitoringPanel()
    results = panel.analyze_dataframe(dataframe)
    
    # 打印报告
    report = panel.generate_report(results)
    print(report)
    
    # 保存报告
    if output_path:
        panel.save_report(results, output_path)
    
    return results


if __name__ == '__main__':
    # 示例：分析回测结果
    # df = pd.read_feather('user_data/backtest_results/some_result.feather')
    # analyze_backtest_results(df, 'detector_monitoring_report.md')
    print("Detector Monitoring Panel ready")
