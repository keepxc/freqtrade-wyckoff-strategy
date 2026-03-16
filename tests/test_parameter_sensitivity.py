"""
Parameter Sensitivity Testing Framework (FUT-E5)

参数敏感性测试模板：
- 测试关键参数变化对系统行为的影响
- 识别脆弱参数（小变化导致大影响）
- 识别鲁棒参数（变化影响可控）
- 为参数调优提供数据支持
"""

import pytest
import pandas as pd
import numpy as np
from pandas import DataFrame
from typing import Dict, List, Tuple, Callable
import json


class ParameterSensitivityTester:
    """参数敏感性测试器"""
    
    def __init__(self, detector_class, detector_kwargs: dict = None):
        """
        Args:
            detector_class: 检测器类
            detector_kwargs: 检测器初始化参数
        """
        self.detector_class = detector_class
        self.detector_kwargs = detector_kwargs or {}
        
    def test_parameter_sensitivity(
        self,
        dataframe: DataFrame,
        parameter_name: str,
        test_values: List,
        metric_func: Callable,
        baseline_value = None
    ) -> Dict:
        """
        测试单个参数的敏感性
        
        Args:
            dataframe: 测试数据
            parameter_name: 参数名称
            test_values: 测试值列表
            metric_func: 评估指标函数 func(result_df) -> float
            baseline_value: 基线值（如果为 None，使用第一个测试值）
            
        Returns:
            {
                'parameter': str,
                'baseline_value': value,
                'baseline_metric': float,
                'results': [{'value': v, 'metric': m, 'delta': d, 'delta_pct': p}, ...],
                'sensitivity_score': float,  # 平均变化率
                'max_delta': float,
                'is_robust': bool  # 是否鲁棒（变化 < 20%）
            }
        """
        results = []
        baseline_metric = None
        
        for i, value in enumerate(test_values):
            # 创建检测器实例
            kwargs = self.detector_kwargs.copy()
            detector = self.detector_class(**kwargs)
            
            # 设置参数
            setattr(detector, parameter_name, value)
            
            # 运行检测
            result_df = detector.detect(dataframe.copy())
            
            # 计算指标
            metric = metric_func(result_df)
            
            # 记录基线
            if baseline_value is None and i == 0:
                baseline_metric = metric
                baseline_value = value
            elif baseline_value == value:
                baseline_metric = metric
            
            results.append({
                'value': value,
                'metric': metric,
            })
        
        # 计算变化
        if baseline_metric is None or baseline_metric == 0:
            baseline_metric = results[0]['metric']
            baseline_value = results[0]['value']
        
        for r in results:
            r['delta'] = r['metric'] - baseline_metric
            r['delta_pct'] = (r['delta'] / baseline_metric * 100) if baseline_metric != 0 else 0
        
        # 计算敏感性分数
        deltas = [abs(r['delta_pct']) for r in results]
        sensitivity_score = np.mean(deltas)
        max_delta = max(deltas)
        
        return {
            'parameter': parameter_name,
            'baseline_value': baseline_value,
            'baseline_metric': baseline_metric,
            'results': results,
            'sensitivity_score': round(sensitivity_score, 2),
            'max_delta': round(max_delta, 2),
            'is_robust': max_delta < 20.0  # 最大变化 < 20% 视为鲁棒
        }
    
    def test_multiple_parameters(
        self,
        dataframe: DataFrame,
        parameter_configs: Dict[str, List],
        metric_func: Callable
    ) -> Dict[str, Dict]:
        """
        测试多个参数的敏感性
        
        Args:
            dataframe: 测试数据
            parameter_configs: {'param_name': [test_values], ...}
            metric_func: 评估指标函数
            
        Returns:
            {'param_name': sensitivity_result, ...}
        """
        results = {}
        
        for param_name, test_values in parameter_configs.items():
            results[param_name] = self.test_parameter_sensitivity(
                dataframe, param_name, test_values, metric_func
            )
        
        return results
    
    def generate_report(self, sensitivity_results: Dict[str, Dict]) -> str:
        """生成敏感性测试报告"""
        report = ["# Parameter Sensitivity Test Report\n"]
        
        # 按敏感性排序
        sorted_params = sorted(
            sensitivity_results.items(),
            key=lambda x: x[1]['sensitivity_score'],
            reverse=True
        )
        
        report.append("## Summary\n")
        report.append("| Parameter | Baseline | Sensitivity Score | Max Delta | Robust |\n")
        report.append("|-----------|----------|-------------------|-----------|--------|\n")
        
        for param_name, result in sorted_params:
            robust_mark = "✓" if result['is_robust'] else "✗"
            report.append(
                f"| {param_name} | {result['baseline_value']} | "
                f"{result['sensitivity_score']:.2f}% | {result['max_delta']:.2f}% | {robust_mark} |\n"
            )
        
        report.append("\n## Detailed Results\n")
        
        for param_name, result in sorted_params:
            report.append(f"\n### {param_name}\n")
            report.append(f"- Baseline Value: {result['baseline_value']}\n")
            report.append(f"- Baseline Metric: {result['baseline_metric']:.4f}\n")
            report.append(f"- Sensitivity Score: {result['sensitivity_score']:.2f}%\n")
            report.append(f"- Max Delta: {result['max_delta']:.2f}%\n")
            report.append(f"- Robust: {'Yes' if result['is_robust'] else 'No'}\n\n")
            
            report.append("| Value | Metric | Delta | Delta % |\n")
            report.append("|-------|--------|-------|----------|\n")
            
            for r in result['results']:
                report.append(
                    f"| {r['value']} | {r['metric']:.4f} | "
                    f"{r['delta']:+.4f} | {r['delta_pct']:+.2f}% |\n"
                )
        
        return ''.join(report)


# ============================================================================
# 示例测试用例
# ============================================================================

class TestMarketEnvDetectorSensitivity:
    """MarketEnvironmentDetector 参数敏感性测试"""
    
    @pytest.fixture
    def sample_dataframe(self):
        """创建测试数据"""
        n = 100
        df = DataFrame({
            'date': pd.date_range('2023-01-01', periods=n, freq='1h'),
            'high': np.linspace(40000, 50000, n),
            'low': np.linspace(38000, 48000, n),
            'close': np.linspace(39000, 49000, n),
            'open': np.linspace(38500, 48500, n),
            'volume': [500] * n,
            'atr': [500] * n,
        })
        return df
    
    def test_half_window_sensitivity(self, sample_dataframe):
        """测试 half_window 参数敏感性"""
        from wyckoff.market_env_detector import MarketEnvironmentDetector
        
        tester = ParameterSensitivityTester(MarketEnvironmentDetector)
        
        # 定义评估指标：markup 检测率
        def metric_func(df):
            return (df['market_env'] == 'markup').sum() / len(df)
        
        result = tester.test_parameter_sensitivity(
            dataframe=sample_dataframe,
            parameter_name='half_window',
            test_values=[10, 15, 20, 25, 30],
            metric_func=metric_func,
            baseline_value=20
        )
        
        assert result['parameter'] == 'half_window'
        assert result['baseline_value'] == 20
        assert 'sensitivity_score' in result
        
        print(f"\nhalf_window sensitivity: {result['sensitivity_score']:.2f}%")
        print(f"Robust: {result['is_robust']}")
    
    def test_markup_thresh_sensitivity(self, sample_dataframe):
        """测试 markup_thresh 参数敏感性"""
        from wyckoff.market_env_detector import MarketEnvironmentDetector
        
        tester = ParameterSensitivityTester(MarketEnvironmentDetector)
        
        def metric_func(df):
            return (df['market_env'] == 'markup').sum() / len(df)
        
        result = tester.test_parameter_sensitivity(
            dataframe=sample_dataframe,
            parameter_name='markup_thresh',
            test_values=[1.0, 1.25, 1.5, 1.75, 2.0],
            metric_func=metric_func,
            baseline_value=1.5
        )
        
        assert result['parameter'] == 'markup_thresh'
        print(f"\nmarkup_thresh sensitivity: {result['sensitivity_score']:.2f}%")
    
    def test_multiple_parameters(self, sample_dataframe):
        """测试多个参数的敏感性"""
        from wyckoff.market_env_detector import MarketEnvironmentDetector
        
        tester = ParameterSensitivityTester(MarketEnvironmentDetector)
        
        def metric_func(df):
            return (df['market_env'] == 'markup').sum() / len(df)
        
        parameter_configs = {
            'half_window': [10, 15, 20, 25, 30],
            'markup_thresh': [1.0, 1.25, 1.5, 1.75, 2.0],
            'persistence_bars': [3, 5, 7, 10],
        }
        
        results = tester.test_multiple_parameters(
            dataframe=sample_dataframe,
            parameter_configs=parameter_configs,
            metric_func=metric_func
        )
        
        # 生成报告
        report = tester.generate_report(results)
        
        print("\n" + report)
        
        # 验证所有参数都被测试
        assert 'half_window' in results
        assert 'markup_thresh' in results
        assert 'persistence_bars' in results


class TestDailyEventDetectorSensitivity:
    """DailyEventDetector 参数敏感性测试示例"""
    
    @pytest.fixture
    def sample_dataframe(self):
        n = 100
        df = DataFrame({
            'date': pd.date_range('2023-01-01', periods=n, freq='1h'),
            'open': [45000] * n,
            'high': [46000] * n,
            'low': [44000] * n,
            'close': [45000] * n,
            'volume': [500] * n,
            'atr': [500] * n,
            'ema_50': [45000] * n,
            'ema_200': [44000] * n,
            'volume_ma': [400] * n,
        })
        
        # 模拟 BC 场景
        df.loc[50, 'volume'] = 2000
        df.loc[50, 'close'] = 47000
        
        return df
    
    def test_bc_detection_sensitivity(self, sample_dataframe):
        """测试 BC 检测参数敏感性（示例）"""
        from wyckoff.daily_event_detector import DailyEventDetector
        
        tester = ParameterSensitivityTester(DailyEventDetector)
        
        def metric_func(df):
            return df['daily_bc'].sum() if 'daily_bc' in df.columns else 0
        
        # 注意：这里需要根据实际的 DailyEventDetector 参数调整
        # 这只是一个示例框架
        
        print("\nDailyEventDetector sensitivity test framework ready")


if __name__ == '__main__':
    pytest.main([__file__, '-v', '-s'])
