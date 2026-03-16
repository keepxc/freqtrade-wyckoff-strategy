"""
RM-3: 动态仓位管理系统

目标：建立真正与交易质量一致的动态仓位框架

核心原则：
- 仓位系统必须后于质量系统验证
- 只有当 quality 与绩效分层关系稳定后，才允许引入动态仓位
- 先做"减仓防守型"动态仓位，再考虑"进攻型"加仓
- 仓位变化不以回撤失控为代价

物理依据：
- 高质量信号（Spring #3, Strong Axis, JAC）应获得更高仓位
- 低质量信号（Weak Axis, 低评分 SOS）应降低仓位
- 不确定环境（markdown, 连续亏损）应降低整体仓位

实施阶段：
1. 建立 quality bucket 绩效映射（统计验证）
2. 实现轻度分层仓位（保守映射）
3. 优先实现减仓防守型逻辑
4. 暂不实现进攻型放大

约束：
- 必须有独立开关（dynamic_position_mode）
- 默认值必须为 'off'
- 必须可回滚到固定仓位
- 必须记录仓位调整原因

作者：Kiro AI
日期：2026-03-14
版本：v1.0 - 初始实现（默认关闭）
"""

import pandas as pd
from pandas import DataFrame
import logging
from typing import Dict, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class QualityTier(Enum):
    """信号质量分层"""
    EXCEPTIONAL = "exceptional"  # 90-100: Spring #3, Strong Axis + JAC
    HIGH = "high"                # 70-89: Medium Axis, High SOS
    MEDIUM = "medium"            # 50-69: Standard signals
    LOW = "low"                  # 30-49: Weak signals
    VERY_LOW = "very_low"        # 0-29: Marginal signals


@dataclass
class PositionConfig:
    """仓位配置"""
    base_stake: float = 1.0      # 基础仓位（默认 1.0x）
    
    # 质量分层仓位倍数（保守设置）
    exceptional_multiplier: float = 1.0   # 暂不放大
    high_multiplier: float = 1.0          # 暂不放大
    medium_multiplier: float = 0.8        # 轻度降低
    low_multiplier: float = 0.5           # 明显降低
    very_low_multiplier: float = 0.3      # 大幅降低
    
    # 环境调整系数（防守型）
    markdown_multiplier: float = 0.7      # markdown 环境降低 30%
    consecutive_loss_multiplier: float = 0.6  # 连续亏损降低 40%
    
    # 风险控制
    min_position_size: float = 0.2        # 最小仓位 20%
    max_position_size: float = 1.0        # 最大仓位 100%（暂不放大）


class PositionManager:
    """
    动态仓位管理器
    
    功能：
    1. 统一信号质量评分
    2. 质量分层映射
    3. 环境调整
    4. 仓位计算
    5. 统计记录
    """
    
    def __init__(self, strategy, config: Optional[PositionConfig] = None):
        """
        初始化仓位管理器
        
        Args:
            strategy: 策略实例（用于访问参数和状态）
            config: 仓位配置（可选，默认使用保守配置）
        """
        self.strategy = strategy
        self.config = config or PositionConfig()
        
        # 统计记录
        self.quality_stats: Dict[str, list] = {
            'exceptional': [],
            'high': [],
            'medium': [],
            'low': [],
            'very_low': []
        }
        
        # 仓位调整记录
        self.position_adjustments: list = []
        
        logger.info(
            f"✅ PositionManager initialized "
            f"(mode=defensive, max_size={self.config.max_position_size:.2f})"
        )
    
    def calculate_unified_quality_score(
        self,
        dataframe: DataFrame,
        idx: int,
        signal_type: str
    ) -> float:
        """
        计算统一的信号质量评分（0-100）
        
        整合各个检测器的质量评分，形成统一标准
        
        Args:
            dataframe: 数据框
            idx: 当前索引
            signal_type: 信号类型（spring_3, spring_2, spring_1, sos, axis, phase_d, etc.）
        
        Returns:
            统一质量评分（0-100）
        """
        base_score = 50.0  # 默认中等质量
        
        # Spring 信号质量映射
        if signal_type == 'spring_3':
            # Spring #3 是最高质量信号
            base_score = 95.0
            # 如果有 spring_confidence，进一步调整
            if 'spring_confidence' in dataframe.columns:
                confidence = dataframe.loc[idx, 'spring_confidence']
                base_score = 90.0 + confidence * 10.0
        
        elif signal_type == 'spring_2':
            base_score = 75.0
            if 'spring_confidence' in dataframe.columns:
                confidence = dataframe.loc[idx, 'spring_confidence']
                base_score = 70.0 + confidence * 15.0
        
        elif signal_type == 'spring_1':
            base_score = 55.0
            if 'spring_confidence' in dataframe.columns:
                confidence = dataframe.loc[idx, 'spring_confidence']
                base_score = 50.0 + confidence * 15.0
        
        # SOS 信号质量
        elif signal_type == 'sos':
            if 'sos_quality_score' in dataframe.columns:
                base_score = dataframe.loc[idx, 'sos_quality_score']
            else:
                base_score = 65.0  # 默认中上质量
        
        # 轴线支撑信号质量
        elif signal_type.startswith('axis_'):
            axis_tier = dataframe.loc[idx, 'axis_strength_tier'] if 'axis_strength_tier' in dataframe.columns else 'weak'
            test_confidence = dataframe.loc[idx, 'test_confidence'] if 'test_confidence' in dataframe.columns else 0.5
            
            if axis_tier == 'strong':
                base_score = 80.0 + test_confidence * 15.0
            elif axis_tier == 'medium':
                base_score = 65.0 + test_confidence * 15.0
            else:  # weak
                base_score = 45.0 + test_confidence * 15.0
        
        # Phase D 信号质量
        elif signal_type == 'phase_d':
            if 'phase_d_quality_score' in dataframe.columns:
                base_score = dataframe.loc[idx, 'phase_d_quality_score']
            else:
                base_score = 60.0
        
        # JAC 信号质量
        elif signal_type == 'jac':
            if 'jac_quality_score' in dataframe.columns:
                base_score = dataframe.loc[idx, 'jac_quality_score']
            else:
                base_score = 70.0
        
        # LPS 信号质量
        elif signal_type == 'lps':
            base_score = 70.0  # LPS 是高质量回测入场
        
        # 其他信号类型
        else:
            base_score = 50.0  # 默认中等质量
        
        return max(0.0, min(100.0, base_score))
    
    def classify_quality_tier(self, quality_score: float) -> QualityTier:
        """
        将质量评分映射到质量分层
        
        Args:
            quality_score: 质量评分（0-100）
        
        Returns:
            质量分层枚举
        """
        if quality_score >= 90:
            return QualityTier.EXCEPTIONAL
        elif quality_score >= 70:
            return QualityTier.HIGH
        elif quality_score >= 50:
            return QualityTier.MEDIUM
        elif quality_score >= 30:
            return QualityTier.LOW
        else:
            return QualityTier.VERY_LOW
    
    def get_quality_multiplier(self, quality_tier: QualityTier) -> float:
        """
        获取质量分层对应的仓位倍数
        
        Args:
            quality_tier: 质量分层
        
        Returns:
            仓位倍数
        """
        multiplier_map = {
            QualityTier.EXCEPTIONAL: self.config.exceptional_multiplier,
            QualityTier.HIGH: self.config.high_multiplier,
            QualityTier.MEDIUM: self.config.medium_multiplier,
            QualityTier.LOW: self.config.low_multiplier,
            QualityTier.VERY_LOW: self.config.very_low_multiplier,
        }
        return multiplier_map.get(quality_tier, 1.0)
    
    def get_environment_multiplier(
        self,
        dataframe: DataFrame,
        idx: int,
        trade_history: Optional[list] = None
    ) -> Tuple[float, str]:
        """
        获取环境调整系数（防守型）
        
        Args:
            dataframe: 数据框
            idx: 当前索引
            trade_history: 交易历史（可选，用于检测连续亏损）
        
        Returns:
            (环境倍数, 调整原因)
        """
        multiplier = 1.0
        reasons = []
        
        # 1. Markdown 环境降低仓位
        if 'market_env' in dataframe.columns:
            market_env = dataframe.loc[idx, 'market_env']
            if market_env == 'markdown':
                multiplier *= self.config.markdown_multiplier
                reasons.append('markdown_env')
        
        # 2. 连续亏损降低仓位
        if trade_history is not None and len(trade_history) >= 3:
            recent_trades = trade_history[-3:]
            consecutive_losses = all(
                trade.get('profit_ratio', 0) < 0 
                for trade in recent_trades
            )
            if consecutive_losses:
                multiplier *= self.config.consecutive_loss_multiplier
                reasons.append('consecutive_losses')
        
        # 3. 物理重置（ChoCh/Climax）降低仓位
        if 'climatic_action_detected' in dataframe.columns:
            if dataframe.loc[idx, 'climatic_action_detected']:
                multiplier *= 0.5  # 气候行动时大幅降低
                reasons.append('climatic_action')
        
        reason_str = ','.join(reasons) if reasons else 'normal'
        return multiplier, reason_str
    
    def calculate_position_size(
        self,
        dataframe: DataFrame,
        idx: int,
        signal_type: str,
        trade_history: Optional[list] = None
    ) -> Tuple[float, Dict]:
        """
        计算动态仓位大小
        
        Args:
            dataframe: 数据框
            idx: 当前索引
            signal_type: 信号类型
            trade_history: 交易历史（可选）
        
        Returns:
            (仓位倍数, 元数据字典)
        """
        # 1. 计算统一质量评分
        quality_score = self.calculate_unified_quality_score(
            dataframe, idx, signal_type
        )
        
        # 2. 质量分层
        quality_tier = self.classify_quality_tier(quality_score)
        
        # 3. 获取质量倍数
        quality_multiplier = self.get_quality_multiplier(quality_tier)
        
        # 4. 获取环境倍数
        env_multiplier, env_reason = self.get_environment_multiplier(
            dataframe, idx, trade_history
        )
        
        # 5. 计算最终仓位
        final_position = self.config.base_stake * quality_multiplier * env_multiplier
        
        # 6. 风险控制：限制仓位范围
        final_position = max(
            self.config.min_position_size,
            min(self.config.max_position_size, final_position)
        )
        
        # 7. 构建元数据
        metadata = {
            'signal_type': signal_type,
            'quality_score': quality_score,
            'quality_tier': quality_tier.value,
            'quality_multiplier': quality_multiplier,
            'env_multiplier': env_multiplier,
            'env_reason': env_reason,
            'final_position': final_position,
            'base_stake': self.config.base_stake,
        }
        
        # 8. 记录统计
        self.quality_stats[quality_tier.value].append(quality_score)
        self.position_adjustments.append(metadata)
        
        return final_position, metadata
    
    def get_statistics(self) -> Dict:
        """
        获取仓位管理统计信息
        
        Returns:
            统计字典
        """
        stats = {
            'total_adjustments': len(self.position_adjustments),
            'quality_distribution': {},
            'avg_position_by_tier': {},
            'env_adjustments': {},
        }
        
        # 质量分布统计
        for tier, scores in self.quality_stats.items():
            if scores:
                stats['quality_distribution'][tier] = {
                    'count': len(scores),
                    'avg_score': sum(scores) / len(scores),
                    'min_score': min(scores),
                    'max_score': max(scores),
                }
        
        # 各分层平均仓位
        tier_positions = {}
        for adj in self.position_adjustments:
            tier = adj['quality_tier']
            if tier not in tier_positions:
                tier_positions[tier] = []
            tier_positions[tier].append(adj['final_position'])
        
        for tier, positions in tier_positions.items():
            stats['avg_position_by_tier'][tier] = sum(positions) / len(positions)
        
        # 环境调整统计
        env_counts = {}
        for adj in self.position_adjustments:
            reason = adj['env_reason']
            env_counts[reason] = env_counts.get(reason, 0) + 1
        stats['env_adjustments'] = env_counts
        
        return stats
    
    def log_statistics(self):
        """记录统计信息到日志"""
        stats = self.get_statistics()
        
        logger.info("=" * 60)
        logger.info("PositionManager Statistics")
        logger.info("=" * 60)
        logger.info(f"Total adjustments: {stats['total_adjustments']}")
        
        logger.info("\nQuality Distribution:")
        for tier, data in stats['quality_distribution'].items():
            logger.info(
                f"  {tier}: {data['count']} signals, "
                f"avg={data['avg_score']:.1f}, "
                f"range=[{data['min_score']:.1f}, {data['max_score']:.1f}]"
            )
        
        logger.info("\nAverage Position by Tier:")
        for tier, avg_pos in stats['avg_position_by_tier'].items():
            logger.info(f"  {tier}: {avg_pos:.2f}x")
        
        logger.info("\nEnvironment Adjustments:")
        for reason, count in stats['env_adjustments'].items():
            logger.info(f"  {reason}: {count} times")
        
        logger.info("=" * 60)


# ============================================================================
# 辅助函数
# ============================================================================

def create_position_manager(
    strategy,
    mode: str = 'off',
    config: Optional[PositionConfig] = None
) -> Optional[PositionManager]:
    """
    创建仓位管理器工厂函数
    
    Args:
        strategy: 策略实例
        mode: 模式（'off', 'defensive', 'balanced', 'aggressive'）
        config: 自定义配置（可选）
    
    Returns:
        PositionManager 实例或 None（mode='off'时）
    """
    if mode == 'off':
        logger.info("PositionManager disabled (mode=off)")
        return None
    
    # 根据模式创建配置
    if config is None:
        if mode == 'defensive':
            config = PositionConfig(
                exceptional_multiplier=1.0,
                high_multiplier=1.0,
                medium_multiplier=0.8,
                low_multiplier=0.5,
                very_low_multiplier=0.3,
                markdown_multiplier=0.7,
                consecutive_loss_multiplier=0.6,
                max_position_size=1.0,
            )
        elif mode == 'balanced':
            # 未来扩展：平衡模式（轻度放大高质量信号）
            config = PositionConfig(
                exceptional_multiplier=1.2,
                high_multiplier=1.1,
                medium_multiplier=0.9,
                low_multiplier=0.6,
                very_low_multiplier=0.4,
                max_position_size=1.2,
            )
        elif mode == 'aggressive':
            # 未来扩展：进攻模式（需要充分验证后才能启用）
            logger.warning(
                "⚠️ Aggressive mode not recommended until quality mapping is validated"
            )
            config = PositionConfig(
                exceptional_multiplier=1.5,
                high_multiplier=1.3,
                medium_multiplier=1.0,
                low_multiplier=0.7,
                very_low_multiplier=0.5,
                max_position_size=1.5,
            )
        else:
            logger.error(f"Unknown mode: {mode}, using defensive")
            config = PositionConfig()
    
    return PositionManager(strategy, config)
