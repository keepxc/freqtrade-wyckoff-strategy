"""
威科夫轴线优化模块 - Phase 1: 强轴线优化

目标：
- 释放被过滤的强轴线支撑信号
- 保持原有逻辑不变（并行测试）
- 使用独立的 enter_tag 标记

理论依据：
1. 测试3次以上的轴线 = 市场共识 = 高可靠性
2. 强轴线本身就是"因果积累"的证据
3. 无需额外的EMA或牛市确认

风险等级：极低（强轴线已经过市场验证）
"""

from pandas import DataFrame
import pandas as pd
from typing import Tuple


class AxisOptimization:
    """
    轴线优化类 - Phase 1
    
    提供强轴线的优化入场逻辑
    """
    
    def __init__(self):
        """初始化轴线优化器"""
        # Phase 1 参数：强轴线判断标准（修复版本 - 2026-01-29）
        self.strong_axis_test_count = 3      # 测试次数阈值（保留）
        self.strong_axis_strength_score = 0.5  # 强度评分阈值（从0.7降低到0.5）
        self.strong_axis_confidence = 0.25    # 置信度阈值（保留）
        
        # 新增：normal类型的阈值
        self.normal_confidence_threshold = 0.4  # normal类型的置信度阈值
        self.normal_strength_threshold = 0.3    # normal类型的支撑强度阈值
        
    def identify_strong_axis(self, dataframe: DataFrame) -> pd.Series:
        """
        识别强轴线（修复版本 - 选项C：分级标准）
        
        强轴线的定义（分级标准）：
        1. 吸收型测试（Absorption）- 最强信号，无条件接受
        2. 正常测试（Normal）+ 中等条件 - 主要信号来源
        3. 高支撑强度 - 任何类型都接受
        
        修复说明（2026-01-29）：
        - 问题：原标准过于严格，导致V2信号=0
        - 数据：absorption只有2个，support_strength>=0.7很少
        - 解决：放宽normal类型要求，降低支撑强度阈值
        
        Args:
            dataframe: 包含轴线数据的DataFrame
            
        Returns:
            pd.Series: 布尔序列，True表示强轴线
        """
        # 检查字段是否存在
        required_fields = ['axis_test_type', 'support_strength', 'test_confidence']
        missing_fields = [f for f in required_fields if f not in dataframe.columns]
        
        if missing_fields:
            print(f"❌ AXIS优化: 缺少字段: {missing_fields}")
            return pd.Series([False] * len(dataframe))
        
        # 打印字段统计（仅在有数据时）
        if len(dataframe) > 0:
            support_data = dataframe[dataframe['support_strength'] > 0]['support_strength']
            confidence_data = dataframe[dataframe['test_confidence'] > 0]['test_confidence']
            
            print(f"\n📊 AXIS优化 - 字段统计:")
            print(f"  support_strength:")
            if len(support_data) > 0:
                print(f"    有效数据: {len(support_data)} 行")
                print(f"    最小值: {support_data.min():.4f}")
                print(f"    最大值: {support_data.max():.4f}")
                print(f"    平均值: {support_data.mean():.4f}")
                print(f"    >=0.3: {(support_data >= 0.3).sum()} 个")
                print(f"    >=0.5: {(support_data >= 0.5).sum()} 个")
            else:
                print(f"    ⚠️  无有效数据")
            
            print(f"  test_confidence:")
            if len(confidence_data) > 0:
                print(f"    有效数据: {len(confidence_data)} 行")
                print(f"    最小值: {confidence_data.min():.4f}")
                print(f"    最大值: {confidence_data.max():.4f}")
                print(f"    平均值: {confidence_data.mean():.4f}")
                print(f"    >=0.4: {(confidence_data >= 0.4).sum()} 个")
                print(f"    >=0.6: {(confidence_data >= 0.6).sum()} 个")
            else:
                print(f"    ⚠️  无有效数据")
            
            print(f"  axis_test_type:")
            print(f"    absorption: {(dataframe['axis_test_type'] == 'absorption').sum()} 个")
            print(f"    normal: {(dataframe['axis_test_type'] == 'normal').sum()} 个")
            print(f"    spring: {(dataframe['axis_test_type'] == 'spring').sum()} 个")
        
        strong_axis = (
            # 条件1: absorption 无条件接受（最强信号）
            (dataframe['axis_test_type'] == 'absorption') |
            
            # 条件2: normal + 中等条件（主要来源）
            (
                (dataframe['axis_test_type'] == 'normal') &
                (dataframe['test_confidence'] >= self.normal_confidence_threshold) &  # 0.4
                (dataframe['support_strength'] >= self.normal_strength_threshold)     # 0.3
            ) |
            
            # 条件3: 高支撑强度（任何类型）
            (dataframe['support_strength'] >= self.strong_axis_strength_score)  # 0.5
        )
        
        # 打印匹配结果
        if len(dataframe) > 0:
            print(f"\n🎯 AXIS优化 - 强轴线匹配:")
            print(f"  条件1 (absorption): {(dataframe['axis_test_type'] == 'absorption').sum()} 个")
            cond2 = (
                (dataframe['axis_test_type'] == 'normal') &
                (dataframe['test_confidence'] >= self.normal_confidence_threshold) &
                (dataframe['support_strength'] >= self.normal_strength_threshold)
            )
            print(f"  条件2 (normal+条件): {cond2.sum()} 个")
            print(f"  条件3 (support>=0.5): {(dataframe['support_strength'] >= self.strong_axis_strength_score).sum()} 个")
            print(f"  总计强轴线: {strong_axis.sum()} 个")
        
        return strong_axis
    
    def generate_strong_axis_entry(
        self,
        dataframe: DataFrame,
        original_entry: pd.Series,
        wyckoff_context_ok: pd.Series,
        harmony_ok: pd.Series,
        no_sot_warning: pd.Series,
        quality_ok: pd.Series
    ) -> pd.Series:
        """
        生成强轴线优化入场信号（V2版本）
        
        优化策略：
        - 移除 axis_trading_allowed（即移除 not_distribution & trend_support）
        - 降低置信度要求
        - 只保留核心威科夫条件
        
        Args:
            dataframe: 数据框
            original_entry: 原有的入场信号（V1）
            wyckoff_context_ok: 威科夫背景OK
            harmony_ok: 努力结果和谐
            no_sot_warning: 无SOT警告
            quality_ok: 波段质量OK
            
        Returns:
            pd.Series: 强轴线优化入场信号
        """
        # 识别强轴线
        strong_axis = self.identify_strong_axis(dataframe)
        
        # 强轴线支撑入场（放宽限制）
        strong_axis_entry = (
            strong_axis &  # 必须是强轴线
            (dataframe['nearest_support'] > 0) &
            (dataframe['support_distance'] < dataframe['dynamic_distance_threshold']) &
            (dataframe['test_confidence'] > self.strong_axis_confidence) &
            
            # 移除 axis_trading_allowed（即移除 not_distribution & trend_support）
            # 只保留核心威科夫条件
            wyckoff_context_ok &
            harmony_ok &
            no_sot_warning &
            quality_ok &
            
            # 排除已经被 V1 捕获的信号（避免重复）
            ~original_entry
        )
        
        return strong_axis_entry
    
    def generate_strong_spring_entry(
        self,
        dataframe: DataFrame,
        original_spring: pd.Series,
        wyckoff_context_ok: pd.Series,
        harmony_ok: pd.Series,
        no_sot_warning: pd.Series,
        quality_ok: pd.Series
    ) -> pd.Series:
        """
        生成强轴线Spring优化入场信号（V2版本）
        
        Spring理论：
        - Spring是"创造牛市"的信号，不是牛市的结果
        - Phase C的终端震仓通常发生在EMA200下方
        - Spring + 快速收回 = 最强买入信号
        
        Args:
            dataframe: 数据框
            original_spring: 原有的Spring信号（V1）
            wyckoff_context_ok: 威科夫背景OK
            harmony_ok: 努力结果和谐
            no_sot_warning: 无SOT警告
            quality_ok: 波段质量OK
            
        Returns:
            pd.Series: 强轴线Spring优化入场信号
        """
        # 识别强轴线
        strong_axis = self.identify_strong_axis(dataframe)
        
        # 强轴线 Spring 优化版本
        strong_spring_entry = (
            dataframe['spring_detected'] &
            strong_axis &  # 必须是强轴线
            (dataframe['nearest_support'] > 0) &
            (dataframe['close'] >= dataframe['nearest_support'] * 0.995) &  # 放宽到-0.5%
            (dataframe['support_distance'] < dataframe['dynamic_distance_threshold']) &
            (dataframe['volume'] < dataframe['volume'].rolling(20).mean() * 0.8) &
            
            # 移除 axis_trading_allowed
            wyckoff_context_ok &
            harmony_ok &
            no_sot_warning &
            quality_ok &
            
            # 排除已经被 V1 捕获的信号
            ~original_spring
        )
        
        return strong_spring_entry
    
    def apply_optimization(
        self,
        dataframe: DataFrame,
        axis_support_v1: pd.Series,
        spring_v1: pd.Series,
        wyckoff_context_ok: pd.Series,
        harmony_ok: pd.Series,
        no_sot_warning: pd.Series,
        quality_ok: pd.Series
    ) -> Tuple[pd.Series, pd.Series]:
        """
        应用 Phase 1 优化
        
        返回两个新的信号：
        1. axis_support_v2_strong: 强轴线支撑优化信号
        2. spring_v2_strong: 强轴线Spring优化信号
        
        Args:
            dataframe: 数据框
            axis_support_v1: 原有轴线支撑信号
            spring_v1: 原有Spring信号
            wyckoff_context_ok: 威科夫背景OK
            harmony_ok: 努力结果和谐
            no_sot_warning: 无SOT警告
            quality_ok: 波段质量OK
            
        Returns:
            Tuple[pd.Series, pd.Series]: (强轴线支撑信号, 强轴线Spring信号)
        """
        # 生成强轴线支撑信号
        axis_support_v2 = self.generate_strong_axis_entry(
            dataframe=dataframe,
            original_entry=axis_support_v1,
            wyckoff_context_ok=wyckoff_context_ok,
            harmony_ok=harmony_ok,
            no_sot_warning=no_sot_warning,
            quality_ok=quality_ok
        )
        
        # 生成强轴线Spring信号
        spring_v2 = self.generate_strong_spring_entry(
            dataframe=dataframe,
            original_spring=spring_v1,
            wyckoff_context_ok=wyckoff_context_ok,
            harmony_ok=harmony_ok,
            no_sot_warning=no_sot_warning,
            quality_ok=quality_ok
        )
        
        return axis_support_v2, spring_v2
    
    def get_statistics(self, dataframe: DataFrame) -> dict:
        """
        获取优化统计信息
        
        Args:
            dataframe: 数据框
            
        Returns:
            dict: 统计信息
        """
        strong_axis = self.identify_strong_axis(dataframe)
        
        stats = {
            'total_bars': len(dataframe),
            'strong_axis_count': strong_axis.sum(),
            'strong_axis_percentage': strong_axis.sum() / len(dataframe) * 100,
            'absorption_type': (dataframe['axis_test_type'] == 'absorption').sum(),
            'normal_type': (dataframe['axis_test_type'] == 'normal').sum(),
            'high_support_strength': (dataframe['support_strength'] >= self.strong_axis_strength_score).sum(),
            'high_confidence': (dataframe['test_confidence'] >= self.normal_confidence_threshold).sum(),
            'normal_with_conditions': (
                (dataframe['axis_test_type'] == 'normal') &
                (dataframe['test_confidence'] >= self.normal_confidence_threshold) &
                (dataframe['support_strength'] >= self.normal_strength_threshold)
            ).sum()
        }
        
        return stats


# 全局实例（单例模式）
_axis_optimizer = None

def get_axis_optimizer() -> AxisOptimization:
    """
    获取轴线优化器实例（单例）
    
    Returns:
        AxisOptimization: 轴线优化器实例
    """
    global _axis_optimizer
    if _axis_optimizer is None:
        _axis_optimizer = AxisOptimization()
    return _axis_optimizer
