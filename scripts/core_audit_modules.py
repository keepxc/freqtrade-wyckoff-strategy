#!/usr/bin/env python3
"""
Core Audit A - Module Registry Generator

自动扫描 wyckoff 模块与策略主文件的集成状态，生成：
- CA-1: 完整模块清单
- CA-2: 按功能分类（active_core / descriptive_only / future_hook / orphan_or_isolated）
- CA-3: JSON 格式模块职责表（输入/输出/下游/状态）

输出文件：
- docs/core_module_registry.json (CA-21)
- docs/core_audit_report.md (CA-20 A/B/C 部分)

使用方式：
    python scripts/core_audit_modules.py
"""

import ast
import json
import os
import re
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set


@dataclass
class ModuleInfo:
    """模块信息数据结构"""
    module_path: str              # 如 "wyckoff.sos_bar_detector"
    class_name: str               # 如 "SOSBarDetector"
    file_path: str                # 完整文件路径
    is_imported: bool             # 是否被策略 import
    is_instantiated: bool         # 是否被实例化
    is_called: bool               # 是否被调用
    status: str                   # active_core / descriptive_only / future_hook / orphan_or_isolated
    inputs: List[str]             # 输入字段列表
    outputs: List[str]            # 输出字段列表
    downstream: List[str]         # 下游调用点
    risk_level: str               # high / medium / low
    notes: str                    # 备注


class ModuleRegistryGenerator:
    """模块注册表生成器"""
    
    def __init__(self, project_root: str = "e:/freqtrade"):
        self.project_root = Path(project_root)
        self.wyckoff_dir = self.project_root / "wyckoff"
        self.strategy_file = self.project_root / "user_data" / "strategies" / "WyckoffV7_TrueMethod.py"
        self.modules: List[ModuleInfo] = []
        
        # 策略文件中的调用模式
        self.call_patterns = {
            'populate_indicators': r'\.(\w+)\.detect\(|dataframe\s*=\s*self\.(\w+)\.detect\(',
            'populate_entry_trend': r'\.(\w+)\.get\(|dataframe\.get\([\'"](\w+)[\'"]',
            'populate_exit_trend': r'\.(\w+)\.get\(|dataframe\.get\([\'"](\w+)[\'"]',
            'custom_exit': r'\.(\w+)\.get\(|dataframe\.get\([\'"](\w+)[\'"]',
        }
    
    def scan_wyckoff_modules(self):
        """扫描 wyckoff 目录下所有模块"""
        if not self.wyckoff_dir.exists():
            print(f"⚠️  Wyckoff 目录不存在: {self.wyckoff_dir}")
            return
        
        for py_file in sorted(self.wyckoff_dir.glob("*.py")):
            if py_file.name.startswith("_") or py_file.name == "__init__.py":
                continue
            
            module_name = py_file.stem
            class_name = self._extract_class_name(py_file)
            
            if class_name:
                module_path = f"wyckoff.{module_name}"
                self.modules.append(ModuleInfo(
                    module_path=module_path,
                    class_name=class_name,
                    file_path=str(py_file.relative_to(self.project_root)),
                    is_imported=False,
                    is_instantiated=False,
                    is_called=False,
                    status="unknown",
                    inputs=[],
                    outputs=[],
                    downstream=[],
                    risk_level="unknown",
                    notes=""
                ))
    
    def _extract_class_name(self, py_file: Path) -> Optional[str]:
        """从 Python 文件中提取类名"""
        try:
            with open(py_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    # 优先返回以 Detector 结尾的类名
                    if node.name.endswith('Detector') or node.name.endswith('Analyzer'):
                        return node.name
            # 如果没有 Detector/Analyzer，返回第一个类名
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    return node.name
        except Exception as e:
            print(f"⚠️  解析 {py_file} 失败: {e}")
        return None
    
    def analyze_strategy_integrations(self):
        """分析策略文件中的集成情况"""
        if not self.strategy_file.exists():
            print(f"⚠️  策略文件不存在: {self.strategy_file}")
            return
        
        with open(self.strategy_file, 'r', encoding='utf-8') as f:
            strategy_content = f.read()
        
        # 1. 检查 import 语句
        imported_modules = []
        for module in self.modules:
            import_pattern = f'from {module.module_path} import {module.class_name}'
            if import_pattern in strategy_content:
                module.is_imported = True
                imported_modules.append(module.module_path)
        
        # 2. 检查实例化
        for module in self.modules:
            # 查找 self.xxx = XxxDetector(self)
            instantiate_pattern = rf'self\.\w+\s*=\s*{module.class_name}\s*\(\s*self'
            if re.search(instantiate_pattern, strategy_content):
                module.is_instantiated = True
        
        # 3. 检查调用和字段引用
        self._analyze_field_usage(strategy_content)
        self._analyze_method_calls(strategy_content)
    
    def _analyze_field_usage(self, content: str):
        """分析字段使用情况 - 增强版：真实追踪消费链"""
        # 1. 提取所有 dataframe.get() 和直接字段访问
        field_patterns = r'dataframe\.get\([\'"](\w+)[\'"]\)|dataframe\[\'(\w+)\'\]|dataframe\.(\w+)'
        all_fields = set(re.findall(field_patterns, content))
        all_fields = {f[0] or f[1] or f[2] for f in all_fields}
        
        # 2. 分析每个方法中的字段消费
        # 识别关键方法：populate_entry_trend, populate_exit_trend, custom_exit
        method_consumers = {
            'populate_entry_trend': [],
            'populate_exit_trend': [],
            'custom_exit': [],
            'custom_stoploss': [],
            'compatibility_layer': [],
            'update_trading_permission': []
        }
        
        for method_name in method_consumers.keys():
            # 提取方法内容
            method_pattern = rf'def {method_name}\([^)]+\):(.*?)(?=\n    def |\nclass |\Z)'
            method_match = re.search(method_pattern, content, re.DOTALL)
            if method_match:
                method_content = method_match.group(1)
                # 提取该方法中使用的字段
                used_in_method = re.findall(r'\b([a-z_][a-z0-9_]*)\b', method_content.lower())
                # 过滤出看起来像字段名的（排除关键字）
                keywords = {'if', 'else', 'elif', 'return', 'not', 'and', 'or', 'in', 'for', 'while', 'len', 'sum', 'max', 'min', 'str', 'float', 'int', 'bool', 'pd', 'np', 'dataframe', 'self', 'true', 'false', 'none'}
                used_fields_in_method = [f for f in used_in_method if f not in keywords and len(f) > 3]
                method_consumers[method_name] = set(used_fields_in_method)
        
        # 3. 每个模块可能的输出字段（基于代码分析）
        module_outputs = self._get_module_output_mapping()
        
        for module in self.modules:
            module_path = module.module_path
            if module_path in module_outputs:
                outputs = module_outputs[module_path]
                module.outputs = outputs
                
                # 4. 追踪字段消费链
                downstream_list = []
                for output_field in outputs:
                    # 检查该字段在哪些方法中被使用
                    for method_name, used_fields in method_consumers.items():
                        if output_field in used_fields:
                            downstream_list.append(method_name)
                
                module.downstream = list(set(downstream_list))  # 去重
                
                # 检查这些字段是否被使用
                used_fields = [f for f in outputs if f in all_fields]
                if used_fields or module.downstream:
                    module.is_called = True
    
    def _analyze_method_calls(self, content: str):
        """分析方法调用"""
        # 查找 detector.detect() 调用
        call_pattern = r'(\w+)\.detect\(|self\.(\w+)\.detect\('
        all_calls = re.findall(call_pattern, content)
        all_callers = {c[0] or c[1] for c in all_calls}
        
        # 提取实例变量名（如 self.sos_bar_detector）
        instance_pattern = r'self\.(\w+)\s*=\s*\w+\('
        all_instances = re.findall(instance_pattern, content)
        
        for module in self.modules:
            # 检查是否有对应的方法调用
            if module.class_name.lower().replace('_', '').replace('detector', '') in all_instances:
                module.is_called = True
    
    def _get_module_output_mapping(self) -> Dict[str, List[str]]:
        """获取模块到输出字段的映射（基于代码分析）"""
        return {
            'wyckoff.wave_analyzer': ['wave_direction', 'is_wave_start', 'wave_displacement', 'wave_efficiency', 'swing_up_er_weak', 'swing_down_er_exhaustion'],
            'wyckoff.effort_result_analyzer': ['effort', 'result', 'effort_result_harmony', 'wyckoff_warning', 'ease_of_movement', 'harmony_score'],
            'wyckoff.pattern_recognizer': ['wyckoff_pattern', 'pattern_confidence'],
            'wyckoff.spring_classifier': ['spring_quality', 'spring_breakout_depth', 'spring_volume_ratio', 'spring_recovery_speed'],
            'wyckoff.sos_bar_detector': ['sos_bar_detected', 'sos_quality_score', 'sos_bar_confirmed'],
            'wyckoff.lps_detector': ['lps_pullback_active', 'lps_bar_detected', 'lps_quality_score'],
            'wyckoff.phase_d_entry_detector': ['phase_d_confirmed', 'phase_d_confidence', 'phase_d_quality_score'],
            'wyckoff.climatic_action_detector': ['climatic_action_detected', 'climatic_type', 'climatic_level', 'post_climax_bars', 'in_post_climax_window'],
            'wyckoff.composite_man_detector': ['composite_intent', 'intent_confidence', 'rs_signal', 'RS_available', 'rs_score'],
            'wyckoff.no_supply_demand_detector': ['no_supply_bar', 'no_demand_bar', 'no_supply_strength', 'no_demand_strength', 'ns_location_valid', 'nd_location_valid', 'hvn_level'],
            'wyckoff.sow_bar_detector': ['sow_bar_detected', 'sow_quality_score', 'utad_detected', 'utad_high', 'utad_bars_since', 'lpsy_detected', 'lpsy_ice_level', 'sow_exit_long_signal'],
            'wyckoff.creek_jac_detector': ['creek_level', 'jac_detected', 'jac_quality_score', 'bars_since_jac', 'jac_failed', 'jac_creek_snapshot', 'volume_jac_bar', 'failed_jac_active', 'buec_detected', 'buec_creek_level', 'jac_entry_signal', 'buec_entry_signal'],
            'wyckoff.pnf_target_calculator': ['tr_high', 'tr_low', 'tr_height', 'target_conservative', 'target_standard', 'target_aggressive', 'target_vertical', 'rr_long_standard', 'rr_short_standard'],
            'wyckoff.market_env_detector': ['market_env', 'market_env_confidence', 'hh_score', 'hl_score', 'trend_score'],
            'wyckoff.significant_bar_detector': ['sig_bar_bullish', 'sig_bar_bearish', 'sig_bar_score_bull', 'sig_bar_score_bear', 'sig_bar_after_spring', 'bars_since_spring'],
            'wyckoff.event_sequence_state_machine': ['wyckoff_phase', 'phase_confidence', 'accum_phase_a_complete', 'accum_in_phase_b', 'accum_in_phase_c', 'accum_in_phase_d', 'tr_midpoint', 'tr_phase_tag', 'tr_upper', 'tr_lower', 'phase_b_high', 'seq_ps_detected', 'seq_sc_confirmed', 'seq_ar_confirmed', 'seq_st_confirmed', 'seq_spring_confirmed', 'seq_sos_confirmed', 'seq_jac_confirmed'],
            'wyckoff.phase_context_detector': ['phase_context_long_ok', 'phase_context_short_ok', 'phase_optimal_long', 'phase_optimal_short', 'phase_spring_zone', 'phase_sos_zone', 'phase_trending_e', 'phase_quality_mult'],
            'wyckoff.daily_event_detector': ['daily_bc_detected', 'daily_bc_path', 'daily_ar_high', 'daily_ar_low', 'dist_sequence_progress', 'daily_utad_detected', 'daily_dist_sow_detected', 'buying_exhaustion', 'dist_cause_sufficient'],
            'wyckoff.daily_tr_detector': ['daily_tr_exists', 'daily_tr60_confirmed', 'daily_tr20_not_breaking', 'daily_tr120_consistent', 'daily_phase_cd', 'daily_phase_label', 'daily_tr_high', 'daily_tr_low', 'daily_tr_width_atr', 'daily_tr_overlap', 'daily_gate_score'],
        }
    
    def classify_modules(self):
        """分类模块状态"""
        with open(self.strategy_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        for module in self.modules:
            # 孤岛模块：未 import 或未实例化
            if not module.is_imported or not module.is_instantiated:
                module.status = "orphan_or_isolated"
                module.risk_level = "low"
                module.notes = "未接入策略"
                continue
            
            # 未被调用：future_hook
            if not module.is_called:
                module.status = "future_hook"
                module.risk_level = "medium"
                module.notes = "已实现但未接入信号"
                continue
            
            # 已调用：判断是 active_core 还是 descriptive_only
            # 检查字段是否影响入场/退出逻辑
            if self._affects_trading_logic(module, content):
                module.status = "active_core"
                module.risk_level = "high"
                module.notes = "直接影响交易行为"
            else:
                module.status = "descriptive_only"
                module.risk_level = "low"
                module.notes = "仅输出描述性字段，未进入决策"
            
            # 特殊处理
            if "creek_jac" in module.module_path:
                module.status = "orphan_or_isolated"
                module.risk_level = "medium"
                module.notes = "字段已计算，但入场信号被显式禁用"
    
    def _affects_trading_logic(self, module: ModuleInfo, content: str) -> bool:
        """判断模块是否影响交易逻辑"""
        # 检查输出字段是否在 enter_long / exit_long / enter_short / exit_short 条件中
        signals = ['enter_long', 'enter_short', 'exit_long', 'exit_short']
        
        for signal in signals:
            signal_pattern = rf"{signal}\s*=\s*\(.*?\)|loc\[.*?\[.*?{signal}.*?\]\s*=\s*1"
            if re.search(signal_pattern, content):
                # 提取条件部分，检查是否包含模块的输出字段
                # 简化处理：如果在整个文件中，模块输出字段出现在信号赋值附近
                for output in module.outputs:
                    # 检查输出字段是否在信号逻辑中被使用
                    if output in content:
                        # 进一步检查是否在相关方法中
                        if f'populate_entry_trend' in content or f'populate_exit_trend' in content or f'custom_exit' in content:
                            return True
        
        # 特殊字段检测
        high_impact_fields = [
            'composite_intent', 'rs_signal', 'spring_quality', 'sos_bar_detected',
            'phase_d_confirmed', 'climatic_action_detected', 'dist_cause_sufficient',
            'bear_market_mode', 'daily_tr_exists', 'axis_strength_tier',
            'tr_upper', 'tr_lower', 'phase_quality_mult', 'rr_long_standard'
        ]
        
        for field in module.outputs:
            if field in high_impact_fields:
                return True
        
        return False
    
    def generate_registry_json(self, output_path: str = "docs/core_module_registry.json"):
        """生成 JSON 格式模块注册表"""
        registry = {
            "generated_at": datetime.now().isoformat(),
            "strategy_version": "WyckoffV7_TrueMethod",
            "total_modules": len(self.modules),
            "modules": []
        }
        
        for module in self.modules:
            registry["modules"].append(asdict(module))
        
        # 按状态统计
        status_counts = {}
        for module in self.modules:
            status_counts[module.status] = status_counts.get(module.status, 0) + 1
        registry["status_summary"] = status_counts
        
        # 写入文件
        output_file = self.project_root / output_path
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(registry, f, indent=2, ensure_ascii=False)
        
        print(f"✅ 生成模块注册表: {output_file}")
        print(f"   总计 {registry['total_modules']} 个模块")
        for status, count in sorted(status_counts.items()):
            print(f"   {status}: {count} 个")
    
    def generate_markdown_report(self, output_path: str = "docs/core_audit_report.md"):
        """生成 Markdown 格式报告"""
        lines = [
            "# Core Audit Report - 模块清单 (CA-20)",
            "",
            f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"策略版本: WyckoffV7_TrueMethod",
            "",
            "---",
            "",
            "## A. 完整模块清单 (CA-1)",
            "",
            f"总计: {len(self.modules)} 个模块",
            ""
        ]
        
        # 按状态分组
        modules_by_status = {}
        for module in self.modules:
            if module.status not in modules_by_status:
                modules_by_status[module.status] = []
            modules_by_status[module.status].append(module)
        
        # 输出按状态分组的表格
        for status in ['active_core', 'descriptive_only', 'future_hook', 'orphan_or_isolated']:
            if status not in modules_by_status:
                continue
            
            status_map = {
                'active_core': '🔥 核心交易模块',
                'descriptive_only': '📊 描述性模块',
                'future_hook': '🔗 预埋接口',
                'orphan_or_isolated': '❌ 孤岛模块'
            }
            
            lines.append(f"### {status_map.get(status, status)}")
            lines.append("")
            
            modules = modules_by_status[status]
            lines.append(f"数量: {len(modules)} 个")
            lines.append("")
            lines.append("| 模块 | 类名 | 风险等级 | 备注 |")
            lines.append("|------|------|----------|------|")
            
            for module in sorted(modules, key=lambda m: m.module_path):
                risk_icon = {'high': '🔴', 'medium': '🟡', 'low': '🟢', 'unknown': '⚪'}[module.risk_level]
                lines.append(f"| `{module.module_path}` | `{module.class_name}` | {risk_icon} {module.risk_level} | {module.notes} |")
            
            lines.append("")
        
        # B 部分：详细字段映射
        lines.append("---")
        lines.append("")
        lines.append("## B. 输入/输出字段映射 (CA-21)")
        lines.append("")
        lines.append("| 模块 | 输出字段 | 下游调用 |")
        lines.append("|------|----------|----------|")
        
        for module in sorted(self.modules, key=lambda m: m.module_path):
            if not module.outputs:
                continue
            
            outputs_str = ", ".join([f"`{f}`" for f in module.outputs[:5]])
            if len(module.outputs) > 5:
                outputs_str += f" ... (共 {len(module.outputs)} 个)"
            
            downstream_str = ", ".join([f"`{d}`" for d in module.downstream[:3]])
            if not module.downstream:
                downstream_str = "-"
            
            lines.append(f"| `{module.module_path}` | {outputs_str} | {downstream_str} |")
        
        lines.append("")
        
        # C 部分：高风险模块说明
        lines.append("---")
        lines.append("")
        lines.append("## C. 高风险模块说明")
        lines.append("")
        
        high_risk_modules = [m for m in self.modules if m.status in ['active_core', 'future_hook']]
        
        if high_risk_modules:
            for module in high_risk_modules:
                lines.append(f"### {module.class_name}")
                lines.append("")
                lines.append(f"- **模块路径**: `{module.module_path}`")
                lines.append(f"- **状态**: {module.status}")
                lines.append(f"- **风险等级**: {module.risk_level}")
                lines.append(f"- **输出字段**: {', '.join(module.outputs)}")
                lines.append(f"- **备注**: {module.notes}")
                lines.append("")
        else:
            lines.append("无高风险模块")
            lines.append("")
        
        # 写入文件
        output_file = self.project_root / output_path
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        
        print(f"✅ 生成审计报告: {output_file}")
    
    def run(self):
        """运行完整分析流程"""
        print("=" * 60)
        print("Core Audit A - 模块注册表生成器")
        print("=" * 60)
        print()
        
        print("📂 扫描 wyckoff 模块...")
        self.scan_wyckoff_modules()
        print(f"   发现 {len(self.modules)} 个模块")
        
        print("\n🔍 分析策略集成情况...")
        self.analyze_strategy_integrations()
        
        print("\n🏷️  分类模块状态...")
        self.classify_modules()
        
        print("\n📊 生成输出文件...")
        self.generate_registry_json()
        self.generate_markdown_report()
        
        print("\n✅ 完成!")
        print()


if __name__ == "__main__":
    generator = ModuleRegistryGenerator()
    generator.run()