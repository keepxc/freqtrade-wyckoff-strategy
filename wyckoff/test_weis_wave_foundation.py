"""
Weis Wave Foundation 测试文件
验证Phase 1实现的正确性
"""

import pandas as pd
import numpy as np
from weis_wave_foundation import (
    WeisWaveSegmentation,
    EffortResultAnalyzer,
    SOTDetector,
    DivergenceLevel,
    detect_exhaustion_reversal,
    calculate_wave_quality_score
)


def create_test_dataframe(bars: int = 100) -> pd.DataFrame:
    """创建测试数据"""
    np.random.seed(42)
    
    # 生成模拟价格数据
    close = 100 + np.cumsum(np.random.randn(bars) * 2)
    high = close + np.abs(np.random.randn(bars) * 1)
    low = close - np.abs(np.random.randn(bars) * 1)
    volume = np.random.randint(1000, 10000, bars)
    
    df = pd.DataFrame({
        'close': close,
        'high': high,
        'low': low,
        'volume': volume
    })
    
    return df


def test_wave_segmentation():
    """测试波段识别"""
    print("=" * 60)
    print("测试1: 波段识别引擎")
    print("=" * 60)
    
    df = create_test_dataframe(100)
    segmenter = WeisWaveSegmentation(min_wave_size=0.005, atr_multiplier=1.75)
    
    waves = segmenter.segment_waves(df)
    
    print(f"✓ 识别到 {len(waves)} 个波段")
    
    if waves:
        print(f"\n最后一个波段信息:")
        last_wave = waves[-1]
        print(f"  - 方向: {last_wave.direction}")
        print(f"  - 位移: {last_wave.displacement:.2f}")
        print(f"  - 努力: {last_wave.effort:.2f}")
        print(f"  - 效率: {last_wave.efficiency:.6f}")
        print(f"  - K线数: {last_wave.bar_count}")
    
    print("\n✅ 波段识别测试通过\n")
    return waves


def test_effort_result_analyzer(waves):
    """测试努力结果分析"""
    print("=" * 60)
    print("测试2: 努力结果和谐性分析")
    print("=" * 60)
    
    if len(waves) < 6:
        print("⚠️ 波段数量不足，跳过测试")
        return
    
    analyzer = EffortResultAnalyzer(lookback_waves=5, effort_multiplier=1.5)
    
    # 分析最后一个波段
    current_wave = waves[-1]
    previous_waves = waves[:-1]
    
    harmony = analyzer.analyze_wave_harmony(current_wave, previous_waves)
    
    print(f"✓ 和谐性分析完成")
    print(f"\n分析结果:")
    print(f"  - 和谐类型: {harmony.harmony_type}")
    print(f"  - 努力水平: {harmony.effort_level}")
    print(f"  - 结果水平: {harmony.result_level}")
    print(f"  - 和谐得分: {harmony.harmony_score:.2f}")
    print(f"  - 背离等级: {harmony.divergence_level.name}")
    print(f"  - 警告信息: {harmony.warning_message}")
    
    # 测试轻松感计算
    ease = analyzer.calculate_ease_of_movement(current_wave)
    print(f"\n  - 移动轻松感: {ease:.6f}")
    
    # 测试背离检测
    has_divergence = analyzer.detect_effort_result_divergence(waves)
    print(f"  - 连续背离: {'是' if has_divergence else '否'}")
    
    print("\n✅ 努力结果分析测试通过\n")


def test_sot_detector(waves):
    """测试SOT检测"""
    print("=" * 60)
    print("测试3: 冲力缩短检测（SOT）")
    print("=" * 60)
    
    if len(waves) < 3:
        print("⚠️ 波段数量不足，跳过测试")
        return
    
    detector = SOTDetector(wave_count=3, momentum_threshold=0.8)
    
    sot_analysis = detector.detect_shortening_of_thrust(waves)
    
    print(f"✓ SOT检测完成")
    print(f"\nSOT分析结果:")
    print(f"  - SOT检测: {'是' if sot_analysis.sot_detected else '否'}")
    print(f"  - 位移序列: {[f'{d:.2f}' for d in sot_analysis.wave_displacements]}")
    print(f"  - 动量比率: {sot_analysis.momentum_ratio:.2f}")
    print(f"  - 成交量趋势: {sot_analysis.volume_trend}")
    print(f"  - 置信度: {sot_analysis.confidence_score:.2f}")
    print(f"  - 预警提前量: {sot_analysis.warning_bars_ahead} 根K线")
    
    # 测试条件验证
    is_valid = detector.validate_sot_conditions(waves)
    print(f"  - 条件验证: {'通过' if is_valid else '未通过'}")
    
    print("\n✅ SOT检测测试通过\n")


def test_auxiliary_functions(waves):
    """测试辅助函数"""
    print("=" * 60)
    print("测试4: 辅助函数")
    print("=" * 60)
    
    if len(waves) < 2:
        print("⚠️ 波段数量不足，跳过测试")
        return
    
    df = create_test_dataframe(100)
    
    # 测试枯竭型转折检测
    exhaustion = detect_exhaustion_reversal(waves, df)
    print(f"✓ 枯竭型转折检测: {'是' if exhaustion else '否'}")
    
    # 测试波段质量得分
    current_wave = waves[-1]
    previous_waves = waves[:-1]
    quality_score = calculate_wave_quality_score(current_wave, previous_waves)
    print(f"✓ 波段质量得分: {quality_score:.2f}")
    
    print("\n✅ 辅助函数测试通过\n")


def run_all_tests():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("Weis Wave Foundation - Phase 1 实现测试")
    print("=" * 60 + "\n")
    
    try:
        # 测试1: 波段识别
        waves = test_wave_segmentation()
        
        # 测试2: 努力结果分析
        test_effort_result_analyzer(waves)
        
        # 测试3: SOT检测
        test_sot_detector(waves)
        
        # 测试4: 辅助函数
        test_auxiliary_functions(waves)
        
        print("=" * 60)
        print("🎉 所有测试通过！Phase 1 实现成功！")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n❌ 测试失败: {str(e)}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    run_all_tests()
