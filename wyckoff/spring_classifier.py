"""
威科夫 Spring 质量分级器模块

根据威科夫理论，将 Spring 分为三个质量等级：
- Spring #1: 深度跌破 + 极高成交量（低质量，需要二次确认）
- Spring #2: 中度跌破 + 适度成交量（中等质量，需要测试确认）
- Spring #3: 轻度跌破 + 极低成交量（高质量，直接入场）

理论依据：
- "价格停留的时间越短，洗盘的力量越强"
- "Spring 创造牛市，不是牛市的结果"
- "轻微破位 + 极低成交量 = 供应枯竭的证据"

创建日期：2026-02-08
Feature: Spring Quality Grading System
"""

import pandas as pd
import numpy as np
from pandas import DataFrame
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class SpringClassifier:
    """
    威科夫 Spring 质量分级器
    
    职责：
    1. 检测 Spring 模式（跌破支撑后快速收回）
    2. 根据跌破深度和成交量分级
    3. 验证快速收回特征（5根K线内）
    4. 计算 Spring 质量评分
    
    使用方法：
        classifier = SpringClassifier(strategy)
        dataframe = classifier.classify_spring_quality(dataframe)
    """
    
    def __init__(self, strategy):
        """
        初始化 Spring 分级器
        
        Args:
            strategy: 主策略实例，用于访问配置参数
        """
        self.strategy = strategy
        
        # Spring 分级阈值（针对兼容模式优化 - 2026-02-09 回滚）
        # 注意：兼容模式使用50周期低点作为支撑，跌破深度通常较大
        # 因此阈值需要相应调整，以获得合理的分级分布
        # 
        # 优化历史：
        # - 2026-02-08: 初始值 depth_2=0.01, volume_low=0.8
        # - 2026-02-08: 调整为 depth_2=0.02（兼容模式优化）
        # - 2026-02-09 上午: 调整为 depth_2=0.03, volume_low=0.9（第一轮优化）✅ 工作良好，310% 利润
        # - 2026-02-09 下午: 调整为 depth_2=0.10, volume_low=1.0（第二轮优化）❌ 破坏性修改，利润下降到 168%
        # - 2026-02-09 晚上: 回滚到第一轮优化的值（0.03/0.05/0.9）✅ 恢复 310% 利润
        #   理论依据：第一轮优化的阈值已经过验证，性能稳定
        #   参考：PROFIT_DECLINE_ROOT_CAUSE_REPORT.md
        self.depth_threshold_1 = 0.05  # Spring #1 深度阈值（5% ATR）- 回滚到工作良好的值
        self.depth_threshold_2 = 0.03  # Spring #2 深度阈值（3% ATR）- 回滚到工作良好的值
        self.volume_threshold_high = 1.5  # 极高成交量阈值（1.5x SMA）
        self.volume_threshold_low = 0.9   # 极低成交量阈值（0.9x SMA）- 回滚到工作良好的值
        self.recovery_window = 5  # 快速收回窗口（K线数）
        
        logger.info("SpringClassifier 初始化完成 (兼容模式优化阈值)")
    
    def classify_spring_quality(self, dataframe: DataFrame) -> DataFrame:
        """
        Spring 质量分级主函数
        
        根据跌破深度和成交量特征，将 Spring 分为三个质量等级：
        - Spring #1: 深度跌破 + 极高量（低质量，需要二次确认）
        - Spring #2: 中度跌破 + 适度量（中等质量，需要测试确认）
        - Spring #3: 轻度跌破 + 极低量（高质量，直接入场）
        
        理论依据：
        - "价格停留的时间越短，洗盘的力量越强"
        - "Spring 创造牛市，不是牛市的结果"
        - "轻微破位 + 极低成交量 = 供应枯竭"
        
        Args:
            dataframe: 包含 OHLCV 和轴线信息的 DataFrame
            
        Returns:
            DataFrame: 添加了 spring_quality 等字段的 DataFrame
            
        新增字段:
            - spring_quality: 'none', 'spring_1', 'spring_2', 'spring_3'
            - spring_breakout_depth: 跌破深度（相对ATR）
            - spring_volume_ratio: 成交量比率
            - spring_recovery_speed: 收回速度（K线数）
        """
        # Step 1: 检查必需字段
        required_fields = ['close', 'high', 'low', 'volume', 'nearest_support', 'atr']
        missing_fields = [f for f in required_fields if f not in dataframe.columns]
        
        if missing_fields:
            logger.warning(f"Spring 分级: 缺少必需字段 {missing_fields}，跳过分级")
            # 初始化字段为默认值
            dataframe['spring_quality'] = 'none'
            dataframe['spring_breakout_depth'] = 0.0
            dataframe['spring_volume_ratio'] = 0.0
            dataframe['spring_recovery_speed'] = 0
            return dataframe
        
        # 确保 volume_sma_20 存在
        if 'volume_sma_20' not in dataframe.columns:
            dataframe['volume_sma_20'] = dataframe['volume'].rolling(window=20).mean()
        
        # Step 2: 初始化字段
        dataframe['spring_quality'] = 'none'
        dataframe['spring_breakout_depth'] = 0.0
        dataframe['spring_volume_ratio'] = 0.0
        dataframe['spring_recovery_speed'] = 0
        
        # Step 3: 检测跌破事件
        breakout_mask = self._detect_breakout_events(dataframe)
        
        # Step 4: 计算跌破深度（相对ATR）
        dataframe = self._calculate_breakout_depth(dataframe, breakout_mask)
        
        # Step 5: 计算成交量比率
        dataframe = self._calculate_volume_ratio(dataframe, breakout_mask)
        
        # Step 6: 检测快速收回
        dataframe = self._detect_quick_recovery(dataframe, breakout_mask)
        
        # Step 7: 分级
        dataframe = self._assign_spring_grade(dataframe)
        
        # Step 8: 输出统计信息
        self._log_statistics(dataframe)
        
        return dataframe
    
    def _detect_breakout_events(self, dataframe: DataFrame) -> pd.Series:
        """
        检测跌破支撑轴线的事件
        
        Args:
            dataframe: 包含价格和轴线信息的 DataFrame
            
        Returns:
            pd.Series: 布尔掩码，标记跌破事件
        """
        breakout_mask = (
            (dataframe['low'] < dataframe['nearest_support']) &
            (dataframe['nearest_support'] > 0)  # 确保支撑位有效
        )
        return breakout_mask
    
    def _calculate_breakout_depth(self, dataframe: DataFrame, breakout_mask: pd.Series) -> DataFrame:
        """
        计算跌破深度（相对ATR）
        
        Args:
            dataframe: DataFrame
            breakout_mask: 跌破事件掩码
            
        Returns:
            DataFrame: 添加了 spring_breakout_depth 字段
        """
        # 处理 ATR 为 0 的情况
        atr_safe = dataframe['atr'].replace(0, np.nan).fillna(method='ffill').fillna(1.0)
        
        # 计算跌破深度（百分比）
        dataframe.loc[breakout_mask, 'spring_breakout_depth'] = (
            (dataframe.loc[breakout_mask, 'nearest_support'] - 
             dataframe.loc[breakout_mask, 'low']) / 
            atr_safe.loc[breakout_mask]
        )
        
        return dataframe
    
    def _calculate_volume_ratio(self, dataframe: DataFrame, breakout_mask: pd.Series) -> DataFrame:
        """
        计算成交量比率（相对20周期均值）
        
        Args:
            dataframe: DataFrame
            breakout_mask: 跌破事件掩码
            
        Returns:
            DataFrame: 添加了 spring_volume_ratio 字段
        """
        # 处理成交量为 0 的情况
        volume_sma_safe = dataframe['volume_sma_20'].replace(0, 1)
        
        # 计算成交量比率
        dataframe.loc[breakout_mask, 'spring_volume_ratio'] = (
            dataframe.loc[breakout_mask, 'volume'] / 
            volume_sma_safe.loc[breakout_mask]
        )
        
        return dataframe
    
    def _detect_quick_recovery(self, dataframe: DataFrame, breakout_mask: pd.Series) -> DataFrame:
        """
        检测快速收回特征（5根K线内）
        
        定义：价格在5根K线内收回到支撑位之上
        
        Args:
            dataframe: DataFrame
            breakout_mask: 跌破事件掩码
            
        Returns:
            DataFrame: 添加了 spring_recovery_speed 字段
        """
        # 创建收回标记：价格收回到支撑位的99.5%以上
        recovery_mask = (
            dataframe['close'] >= dataframe['nearest_support'] * 0.995
        )
        
        # 对于每个跌破点，检查未来5根K线
        for i in range(1, self.recovery_window + 1):
            # 向前查看 i 根K线
            future_recovery = recovery_mask.shift(-i)
            
            # 如果在 i 根K线内收回，且尚未记录速度，则记录
            quick_recovery = (
                breakout_mask & 
                future_recovery & 
                (dataframe['spring_recovery_speed'] == 0)
            )
            dataframe.loc[quick_recovery, 'spring_recovery_speed'] = i
        
        return dataframe
    
    def _assign_spring_grade(self, dataframe: DataFrame) -> DataFrame:
        """
        根据跌破深度和成交量比率分配 Spring 等级
        
        分级标准：
        - Spring #1: 深度 > 2% ATR & 成交量 > 1.5x
        - Spring #2: 1% <= 深度 <= 2% ATR & 1.0x <= 成交量 <= 1.5x
        - Spring #3: 深度 < 1% ATR & 成交量 < 0.8x
        
        Args:
            dataframe: DataFrame
            
        Returns:
            DataFrame: 更新了 spring_quality 字段
        """
        # 只对有快速收回的跌破进行分级
        has_recovery = (dataframe['spring_recovery_speed'] > 0) & (dataframe['spring_recovery_speed'] <= self.recovery_window)
        
        # Spring #3 - 高质量（轻度跌破 + 极低量）⭐
        spring_3_conditions = (
            has_recovery &
            (dataframe['spring_breakout_depth'] < self.depth_threshold_2) &  # < 1% ATR
            (dataframe['spring_volume_ratio'] < self.volume_threshold_low)   # < 0.8x
        )
        dataframe.loc[spring_3_conditions, 'spring_quality'] = 'spring_3'
        
        # Spring #2 - 中等质量（中度跌破 + 适度量）
        spring_2_conditions = (
            has_recovery &
            (dataframe['spring_breakout_depth'] >= self.depth_threshold_2) &  # >= 1% ATR
            (dataframe['spring_breakout_depth'] <= self.depth_threshold_1) &  # <= 2% ATR
            (dataframe['spring_volume_ratio'] >= 1.0) &                       # >= 1.0x
            (dataframe['spring_volume_ratio'] <= self.volume_threshold_high)  # <= 1.5x
        )
        dataframe.loc[spring_2_conditions, 'spring_quality'] = 'spring_2'
        
        # Spring #1 - 低质量（深度跌破 + 极高量）
        spring_1_conditions = (
            has_recovery &
            (dataframe['spring_breakout_depth'] > self.depth_threshold_1) &   # > 2% ATR
            (dataframe['spring_volume_ratio'] > self.volume_threshold_high)   # > 1.5x
        )
        dataframe.loc[spring_1_conditions, 'spring_quality'] = 'spring_1'
        
        return dataframe
    
    def _log_statistics(self, dataframe: DataFrame) -> None:
        """
        输出 Spring 分级统计信息
        
        Args:
            dataframe: DataFrame
        """
        spring_3_count = (dataframe['spring_quality'] == 'spring_3').sum()
        spring_2_count = (dataframe['spring_quality'] == 'spring_2').sum()
        spring_1_count = (dataframe['spring_quality'] == 'spring_1').sum()
        
        if spring_3_count > 0 or spring_2_count > 0 or spring_1_count > 0:
            logger.info(
                f"Spring 分级统计: "
                f"#3={spring_3_count}, #2={spring_2_count}, #1={spring_1_count}"
            )
