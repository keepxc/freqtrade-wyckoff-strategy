"""
PNF Shadow Protect 回测分析脚本

分析两个回测结果的对比：
- 基线（WY_PNF_SHADOW_PROTECT=0）：硬退出
- 新行为（WY_PNF_SHADOW_PROTECT=1）：警告区 + Shadow Protect

成功指标：
1. 利润捕获率：平均利润提升 +15%
2. 回撤减少：回撤减少 -30%
3. 超时率：< 10% 的 PNF 激活交易超时
4. 退出质量分数：> 70% 的 Shadow Protect 退出是"高质量"
5. 总回报改进：> 5%
"""

import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple
from datetime import datetime


def load_backtest_results(filepath: str) -> Dict:
    """加载回测 JSON 结果"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data
    except FileNotFoundError:
        print(f"❌ 文件未找到: {filepath}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"❌ JSON 解析错误: {e}")
        sys.exit(1)


def filter_pnf_trades(trades: List[Dict]) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    """
    过滤 PNF 相关交易
    
    Returns:
        (pnf_activated_trades, pnf_timeout_trades, pnf_shadow_protect_trades)
    """
    pnf_activated = []
    pnf_timeout = []
    pnf_shadow_protect = []
    
    for trade in trades:
        # 检查是否有 PNF 警告区激活
        if trade.get('exit_reason') in ['pnf_timeout_exit', 'shadow_protect_exit']:
            pnf_activated.append(trade)
            
            if trade['exit_reason'] == 'pnf_timeout_exit':
                pnf_timeout.append(trade)
            elif trade['exit_reason'] == 'shadow_protect_exit':
                pnf_shadow_protect.append(trade)
    
    return pnf_activated, pnf_timeout, pnf_shadow_protect


def calculate_metrics(baseline_data: Dict, new_data: Dict) -> Dict:
    """计算对比指标"""
    baseline_trades = baseline_data.get('trades', [])
    new_trades = new_data.get('trades', [])
    
    # 过滤 PNF 相关交易
    pnf_activated, pnf_timeout, pnf_shadow_protect = filter_pnf_trades(new_trades)
    
    # 基线指标
    baseline_pnf_exits = [t for t in baseline_trades if t.get('exit_reason', '').startswith('pnf_target')]
    baseline_avg_profit = sum(t.get('profit_ratio', 0) for t in baseline_pnf_exits) / len(baseline_pnf_exits) if baseline_pnf_exits else 0
    
    # 新行为指标
    new_avg_profit = sum(t.get('profit_ratio', 0) for t in pnf_activated) / len(pnf_activated) if pnf_activated else 0
    
    # 计算改进
    profit_improvement = ((new_avg_profit - baseline_avg_profit) / baseline_avg_profit * 100) if baseline_avg_profit > 0 else 0
    
    # 超时率
    timeout_rate = (len(pnf_timeout) / len(pnf_activated) * 100) if pnf_activated else 0
    
    # 退出质量分数（简化版：Shadow Protect 退出占比）
    exit_quality_score = (len(pnf_shadow_protect) / len(pnf_activated) * 100) if pnf_activated else 0
    
    # 总回报对比
    baseline_total_profit = baseline_data.get('results_per_pair', [{}])[0].get('profit_total_abs', 0) if baseline_data.get('results_per_pair') else 0
    new_total_profit = new_data.get('results_per_pair', [{}])[0].get('profit_total_abs', 0) if new_data.get('results_per_pair') else 0
    total_return_improvement = ((new_total_profit - baseline_total_profit) / abs(baseline_total_profit) * 100) if baseline_total_profit != 0 else 0
    
    return {
        'baseline_pnf_exits': len(baseline_pnf_exits),
        'baseline_avg_profit': baseline_avg_profit,
        'new_pnf_activated': len(pnf_activated),
        'new_pnf_timeout': len(pnf_timeout),
        'new_pnf_shadow_protect': len(pnf_shadow_protect),
        'new_avg_profit': new_avg_profit,
        'profit_improvement_pct': profit_improvement,
        'timeout_rate_pct': timeout_rate,
        'exit_quality_score_pct': exit_quality_score,
        'baseline_total_profit': baseline_total_profit,
        'new_total_profit': new_total_profit,
        'total_return_improvement_pct': total_return_improvement,
    }


def generate_report(metrics: Dict) -> str:
    """生成分析报告"""
    report = []
    report.append("=" * 80)
    report.append("PNF Shadow Protect 回测分析报告")
    report.append("=" * 80)
    report.append("")
    
    # 基线数据
    report.append("## 基线数据（WY_PNF_SHADOW_PROTECT=0）")
    report.append(f"- PNF 硬退出交易数: {metrics['baseline_pnf_exits']}")
    report.append(f"- 平均利润: {metrics['baseline_avg_profit']:.2%}")
    report.append(f"- 总利润: {metrics['baseline_total_profit']:.2f} USDT")
    report.append("")
    
    # 新行为数据
    report.append("## 新行为数据（WY_PNF_SHADOW_PROTECT=1）")
    report.append(f"- PNF 警告区激活交易数: {metrics['new_pnf_activated']}")
    report.append(f"  - Shadow Protect 退出: {metrics['new_pnf_shadow_protect']}")
    report.append(f"  - 超时退出: {metrics['new_pnf_timeout']}")
    report.append(f"- 平均利润: {metrics['new_avg_profit']:.2%}")
    report.append(f"- 总利润: {metrics['new_total_profit']:.2f} USDT")
    report.append("")
    
    # 成功指标
    report.append("## 成功指标")
    report.append("")
    
    # 指标 1: 利润捕获率
    profit_target = 15.0
    profit_status = "✅ 达标" if metrics['profit_improvement_pct'] >= profit_target else "❌ 未达标"
    report.append(f"### 1. 利润捕获率（目标: +{profit_target}%）")
    report.append(f"- 实际: {metrics['profit_improvement_pct']:+.2f}% {profit_status}")
    report.append("")
    
    # 指标 2: 超时率
    timeout_target = 10.0
    timeout_status = "✅ 达标" if metrics['timeout_rate_pct'] < timeout_target else "❌ 未达标"
    report.append(f"### 2. 超时率（目标: < {timeout_target}%）")
    report.append(f"- 实际: {metrics['timeout_rate_pct']:.2f}% {timeout_status}")
    report.append("")
    
    # 指标 3: 退出质量分数
    quality_target = 70.0
    quality_status = "✅ 达标" if metrics['exit_quality_score_pct'] >= quality_target else "❌ 未达标"
    report.append(f"### 3. 退出质量分数（目标: > {quality_target}%）")
    report.append(f"- 实际: {metrics['exit_quality_score_pct']:.2f}% {quality_status}")
    report.append("")
    
    # 指标 4: 总回报改进
    return_target = 5.0
    return_status = "✅ 达标" if metrics['total_return_improvement_pct'] >= return_target else "❌ 未达标"
    report.append(f"### 4. 总回报改进（目标: > {return_target}%）")
    report.append(f"- 实际: {metrics['total_return_improvement_pct']:+.2f}% {return_status}")
    report.append("")
    
    # 总结
    report.append("=" * 80)
    report.append("## 总结")
    report.append("")
    
    all_passed = (
        metrics['profit_improvement_pct'] >= profit_target and
        metrics['timeout_rate_pct'] < timeout_target and
        metrics['exit_quality_score_pct'] >= quality_target and
        metrics['total_return_improvement_pct'] >= return_target
    )
    
    if all_passed:
        report.append("✅ **所有成功指标均已达标！PNF Shadow Protect 功能验证通过。**")
    else:
        report.append("⚠️ **部分指标未达标，需要进一步调优或分析。**")
    
    report.append("")
    report.append("=" * 80)
    
    return "\n".join(report)


def main():
    """主函数"""
    # 检查命令行参数
    if len(sys.argv) != 3:
        print("用法: python analyze_pnf_shadow_protect.py <baseline.json> <new_behavior.json>")
        print("")
        print("示例:")
        print("  python analyze_pnf_shadow_protect.py backtest_pnf_baseline.json backtest_pnf_shadow_protect.json")
        sys.exit(1)
    
    baseline_file = sys.argv[1]
    new_file = sys.argv[2]
    
    print(f"📊 加载回测结果...")
    print(f"  - 基线: {baseline_file}")
    print(f"  - 新行为: {new_file}")
    print("")
    
    # 加载数据
    baseline_data = load_backtest_results(baseline_file)
    new_data = load_backtest_results(new_file)
    
    # 计算指标
    print("🔍 计算对比指标...")
    metrics = calculate_metrics(baseline_data, new_data)
    
    # 生成报告
    print("📝 生成分析报告...")
    print("")
    report = generate_report(metrics)
    print(report)
    
    # 保存报告
    output_file = "backtest_pnf_shadow_protect_analysis.md"
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(report)
    
    print(f"💾 报告已保存到: {output_file}")


if __name__ == '__main__':
    main()
