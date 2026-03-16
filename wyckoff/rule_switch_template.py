"""
Rule Switch + Regression Check + Rollback Template (FUT-E9)

新增规则开关 + 回归对照 + 回滚要求模板：
- 为新规则提供标准化开关机制
- 自动回归对照（新规则 vs 基线）
- 快速回滚能力
- 版本控制与审计日志
"""

import os
import logging
from typing import Dict, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime
import json

logger = logging.getLogger(__name__)


@dataclass
class RuleConfig:
    """规则配置"""
    rule_id: str
    rule_name: str
    enabled: bool = False
    version: str = "1.0.0"
    author: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    description: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)
    rollback_version: Optional[str] = None
    audit_log: list = field(default_factory=list)


class RuleSwitchManager:
    """规则开关管理器"""
    
    def __init__(self, config_path: str = None):
        """
        Args:
            config_path: 配置文件路径（默认使用环境变量）
        """
        self.config_path = config_path or os.getenv(
            'WY_RULE_CONFIG_PATH',
            'wyckoff/rule_switches.json'
        )
        self.rules: Dict[str, RuleConfig] = {}
        self._load_config()
    
    def _load_config(self):
        """加载配置"""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    
                for rule_id, rule_data in data.items():
                    self.rules[rule_id] = RuleConfig(**rule_data)
                
                logger.info(f"Loaded {len(self.rules)} rule configs from {self.config_path}")
            except Exception as e:
                logger.error(f"Failed to load rule config: {e}")
        else:
            logger.warning(f"Rule config file not found: {self.config_path}")
    
    def _save_config(self):
        """保存配置"""
        try:
            data = {
                rule_id: {
                    'rule_id': rule.rule_id,
                    'rule_name': rule.rule_name,
                    'enabled': rule.enabled,
                    'version': rule.version,
                    'author': rule.author,
                    'created_at': rule.created_at,
                    'description': rule.description,
                    'parameters': rule.parameters,
                    'rollback_version': rule.rollback_version,
                    'audit_log': rule.audit_log,
                }
                for rule_id, rule in self.rules.items()
            }
            
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Saved rule config to {self.config_path}")
        except Exception as e:
            logger.error(f"Failed to save rule config: {e}")
    
    def register_rule(
        self,
        rule_id: str,
        rule_name: str,
        description: str = "",
        author: str = "",
        parameters: Dict[str, Any] = None,
        enabled: bool = False
    ) -> RuleConfig:
        """
        注册新规则
        
        Args:
            rule_id: 规则唯一标识
            rule_name: 规则名称
            description: 规则描述
            author: 作者
            parameters: 规则参数
            enabled: 是否启用
            
        Returns:
            RuleConfig 实例
        """
        if rule_id in self.rules:
            logger.warning(f"Rule {rule_id} already exists, updating...")
        
        rule = RuleConfig(
            rule_id=rule_id,
            rule_name=rule_name,
            description=description,
            author=author,
            parameters=parameters or {},
            enabled=enabled
        )
        
        rule.audit_log.append({
            'action': 'registered',
            'timestamp': datetime.now().isoformat(),
            'enabled': enabled
        })
        
        self.rules[rule_id] = rule
        self._save_config()
        
        logger.info(f"Registered rule: {rule_id} ({rule_name})")
        return rule
    
    def is_enabled(self, rule_id: str) -> bool:
        """
        检查规则是否启用
        
        Args:
            rule_id: 规则标识
            
        Returns:
            是否启用
        """
        # 优先检查环境变量（用于快速开关）
        env_key = f'WY_RULE_{rule_id.upper()}_ENABLED'
        env_value = os.getenv(env_key)
        
        if env_value is not None:
            return env_value.lower() in ('true', '1', 'yes', 'on')
        
        # 检查配置文件
        if rule_id in self.rules:
            return self.rules[rule_id].enabled
        
        # 默认关闭
        return False
    
    def enable_rule(self, rule_id: str, reason: str = ""):
        """启用规则"""
        if rule_id not in self.rules:
            logger.error(f"Rule {rule_id} not found")
            return
        
        self.rules[rule_id].enabled = True
        self.rules[rule_id].audit_log.append({
            'action': 'enabled',
            'timestamp': datetime.now().isoformat(),
            'reason': reason
        })
        
        self._save_config()
        logger.info(f"Enabled rule: {rule_id}")
    
    def disable_rule(self, rule_id: str, reason: str = ""):
        """禁用规则"""
        if rule_id not in self.rules:
            logger.error(f"Rule {rule_id} not found")
            return
        
        self.rules[rule_id].enabled = False
        self.rules[rule_id].audit_log.append({
            'action': 'disabled',
            'timestamp': datetime.now().isoformat(),
            'reason': reason
        })
        
        self._save_config()
        logger.info(f"Disabled rule: {rule_id}")
    
    def get_parameter(self, rule_id: str, param_name: str, default: Any = None) -> Any:
        """获取规则参数"""
        if rule_id not in self.rules:
            return default
        
        return self.rules[rule_id].parameters.get(param_name, default)
    
    def update_parameter(self, rule_id: str, param_name: str, value: Any):
        """更新规则参数"""
        if rule_id not in self.rules:
            logger.error(f"Rule {rule_id} not found")
            return
        
        old_value = self.rules[rule_id].parameters.get(param_name)
        self.rules[rule_id].parameters[param_name] = value
        
        self.rules[rule_id].audit_log.append({
            'action': 'parameter_updated',
            'timestamp': datetime.now().isoformat(),
            'parameter': param_name,
            'old_value': old_value,
            'new_value': value
        })
        
        self._save_config()
        logger.info(f"Updated parameter {param_name} for rule {rule_id}")
    
    def rollback_rule(self, rule_id: str, reason: str = ""):
        """回滚规则（禁用并记录）"""
        if rule_id not in self.rules:
            logger.error(f"Rule {rule_id} not found")
            return
        
        # 记录回滚版本
        self.rules[rule_id].rollback_version = self.rules[rule_id].version
        self.rules[rule_id].enabled = False
        
        self.rules[rule_id].audit_log.append({
            'action': 'rollback',
            'timestamp': datetime.now().isoformat(),
            'reason': reason,
            'version': self.rules[rule_id].version
        })
        
        self._save_config()
        logger.warning(f"Rolled back rule: {rule_id} (reason: {reason})")
    
    def get_audit_log(self, rule_id: str) -> list:
        """获取审计日志"""
        if rule_id not in self.rules:
            return []
        
        return self.rules[rule_id].audit_log
    
    def list_rules(self) -> Dict[str, Dict]:
        """列出所有规则"""
        return {
            rule_id: {
                'rule_name': rule.rule_name,
                'enabled': rule.enabled,
                'version': rule.version,
                'description': rule.description,
            }
            for rule_id, rule in self.rules.items()
        }


# ============================================================================
# 使用示例
# ============================================================================

# 全局实例
_rule_manager = None


def get_rule_manager() -> RuleSwitchManager:
    """获取全局规则管理器实例"""
    global _rule_manager
    if _rule_manager is None:
        _rule_manager = RuleSwitchManager()
    return _rule_manager


def is_rule_enabled(rule_id: str) -> bool:
    """快捷函数：检查规则是否启用"""
    return get_rule_manager().is_enabled(rule_id)


# ============================================================================
# 预定义规则示例
# ============================================================================

# 在策略初始化时注册规则
def register_default_rules():
    """注册默认规则"""
    manager = get_rule_manager()
    
    # P1: Daily Gate 相关规则
    manager.register_rule(
        rule_id='daily_gate_soft_mode',
        rule_name='Daily Gate Soft Mode',
        description='Daily gate soft mode (no hard block)',
        author='system',
        parameters={'mode': 'soft'},
        enabled=False  # 默认关闭
    )
    
    # P3: 波段级 E/R 相关规则
    manager.register_rule(
        rule_id='wave_er_exit_enhancement',
        rule_name='Wave E/R Exit Enhancement',
        description='Use wave-level effort/result for exit enhancement',
        author='system',
        parameters={'threshold': 0.7},
        enabled=False
    )
    
    # P4: Market Environment 相关规则
    manager.register_rule(
        rule_id='market_env_routing',
        rule_name='Market Environment Signal Routing',
        description='Route signals based on market environment',
        author='system',
        parameters={'enabled_envs': ['markup', 'trading_range']},
        enabled=False  # 默认关闭（P4 归档）
    )
    
    # RM3: Position Manager 相关规则
    manager.register_rule(
        rule_id='dynamic_position_sizing',
        rule_name='Dynamic Position Sizing',
        description='Adjust position size based on quality bucket',
        author='system',
        parameters={
            'mode': 'defensive',
            'high_multiplier': 1.0,
            'medium_multiplier': 0.85,
            'low_multiplier': 0.5
        },
        enabled=False
    )


# ============================================================================
# 策略中使用示例
# ============================================================================

"""
在策略代码中使用：

from wyckoff.rule_switch_template import is_rule_enabled, get_rule_manager

class WyckoffV7_TrueMethod(IStrategy):
    
    def __init__(self, config: dict) -> None:
        super().__init__(config)
        
        # 注册默认规则
        register_default_rules()
    
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        
        # 检查规则是否启用
        if is_rule_enabled('market_env_routing'):
            # 使用 market environment 路由
            manager = get_rule_manager()
            enabled_envs = manager.get_parameter(
                'market_env_routing',
                'enabled_envs',
                ['markup', 'trading_range']
            )
            
            # 只在允许的环境下入场
            env_filter = dataframe['market_env'].isin(enabled_envs)
            dataframe.loc[~env_filter, 'enter_long'] = 0
        
        return dataframe
"""


if __name__ == '__main__':
    # 测试
    register_default_rules()
    manager = get_rule_manager()
    
    print("Registered rules:")
    for rule_id, info in manager.list_rules().items():
        print(f"  {rule_id}: {info['rule_name']} (enabled={info['enabled']})")
