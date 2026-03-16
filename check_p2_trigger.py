#!/usr/bin/env python3
"""
P2 触发条件检查脚本

用途：
- 检查是否满足 P2 启动条件
- 分析 regime 分布和交易表现
- 生成 P2 启动建议报告

使用方法：
    python check_p2_trigger.py

输出：
- P2 触发条件诊断报告
- Regime 分布统计
- 是否建议启动 P2
"""

import sys
import os
import json
from pathlib import Path
from datetime import datetime

# 添加 wyckoff 模块路径
wyckoff_path = Path(__file__).parent
if str(wyckoff_path) not in sys.path:
    sys.path.insert(0, str(wyckoff_path))

try:
    from wyckoff.regime_diagnostics import RegimeDiagnostics
    import pandas as pd
    import logging
except ImportError as e:
    print(f"❌ 导入失败: {e}")
    print("请确保已安装必要的依赖：pandas")
    sys.exit(1)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def load_backtest_trades(backtest_results_dir: str = 'user_data/backtest_results') -> pd.DataFrame:
    """
    加载回测交易记录
    
    Args:
        backtest_results_dir: 回测结果目录
    
    Returns:
        交易记录 DataFrame
    """
    results_path = Path(backtest_results_dir)
    
    # 查找最新的回测结果
    last_result_file = results_path / '.last_result.json'
    
    if not last_result_file.exists():
        logger.warning(f"未找到 .last_result.json，尝试查找最新的回测结果文件")
        
        # 查找所有 meta.json 文件
        meta_files = sorted(results_path.glob('backtest-result-*.meta.json'), reverse=True)
        
        if not meta_files:
            logger.error(f"未找到任何回测结果文件")
            return pd.DataFrame()
        
        last_result_file = meta_files[0]
        logger.info(f"使用最新回测结果: {last_result_file.name}")
    
    try:
        with open(last_result_file, 'r', encoding='utf-8') as f:
            meta_data = json.load(f)
        
        # 提取交易列表
        if 'strategy' in meta_data and 'WyckoffV7_TrueMethod' in meta_data['strategy']:
            strategy_data = meta_data['strategy']['WyckoffV7_TrueMethod']
            trades = strategy_data.get('trades', [])
            
            if not trades:
                logger.warning("回测结果中没有交易记录")
                return pd.DataFrame()
            
            # 转换为 DataFrame
            df = pd.DataFrame(trades)
            
            # 转换时间字段
            if 'open_date' in df.columns:
                df['open_date'] = pd.to_datetime(df['open_date'])
            if 'close_date' in df.columns:
                df['close_date'] = pd.to_datetime(df['close_date'])
            
            # 计算交易持续时间（小时）
            if 'open_date' in df.columns and 'close_date' in df.columns:
                df['trade_duration'] = (df['close_date'] - df['open_date']).dt.total_seconds() / 3600
            
            logger.info(f"✅ 加载了 {len(df)} 笔交易记录")
            return df
        
        else:
            logger.error("回测结果格式不正确")
            return pd.DataFrame()
    
    except Exception as e:
        logger.error(f"加载回测结果失败: {e}")
        return pd.DataFrame()


def load_ohlcv_data(data_dir: str = 'user_data/data/binance') -> pd.DataFrame:
    """
    加载 OHLCV 数据（用于检查 regime 标签分布）
    
    Args:
        data_dir: 数据目录
    
    Returns:
        OHLCV DataFrame
    """
    data_path = Path(data_dir)
    
    # 查找 BTC/USDT 1H 数据
    btc_file = data_path / 'BTC_USDT_USDT-1h.feather'
    
    if not btc_file.exists():
        logger.warning(f"未找到 BTC/USDT 1H 数据文件: {btc_file}")
        return pd.DataFrame()
    
    try:
        df = pd.read_feather(btc_file)
        
        # 转换时间字段
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'])
        
        logger.info(f"✅ 加载了 {len(df)} 根 K 线数据")
        return df
    
    except Exception as e:
        logger.error(f"加载 OHLCV 数据失败: {e}")
        return pd.DataFrame()


def print_report(report: dict):
    """打印诊断报告"""
    print("\n" + "=" * 80)
    print("P2 触发条件诊断报告")
    print("=" * 80)
    print(f"\n生成时间: {report['timestamp']}")
    print(f"\nP2 启动建议: {'✅ 建议启动' if report['p2_activation_recommended'] else '❌ 暂不建议启动'}")
    
    if report['triggered_conditions']:
        print(f"\n触发的条件: {', '.join(report['triggered_conditions'])}")
        
        for condition in report['triggered_conditions']:
            details = report['condition_details'].get(condition, {})
            
            print(f"\n{'─' * 80}")
            print(f"条件 {condition} 详情:")
            print(f"{'─' * 80}")
            
            if condition == 'A':
                print("特定 regime 下持续跑输基线")
                print(f"  触发的 regime: {details.get('triggered_regimes', [])}")
                print(f"  回看月数: {details.get('lookback_months', 0)}")
                print(f"  总交易数: {details.get('total_trades', 0)}")
                
                regime_stats = details.get('regime_stats', {})
                if regime_stats:
                    print("\n  Regime 统计:")
                    for regime, stats in regime_stats.items():
                        print(f"    {regime}:")
                        print(f"      交易数: {stats['trade_count']}")
                        print(f"      平均收益: {stats['avg_profit']:.4f}")
                        print(f"      Profit Factor: {stats['profit_factor']:.2f}")
                        print(f"      胜率: {stats['win_rate']:.2%}")
            
            elif condition == 'B':
                print("特定 regime 下出现方向性误配")
                triggered_cases = details.get('triggered_cases', [])
                if triggered_cases:
                    print("\n  触发的案例:")
                    for case in triggered_cases:
                        print(f"    Regime: {case['regime']}, Signal: {case['signal']}")
                        print(f"      胜率: {case['win_rate']:.2%} (低于阈值 30%)")
                        print(f"      交易数: {case['trade_count']}")
            
            elif condition == 'C':
                print("环境标签长期失真或异常常开")
                
                bear_details = details.get('bear_market_mode')
                if bear_details:
                    print(f"\n  bear_market_mode 持续激活:")
                    print(f"    激活天数: {bear_details['bear_active_days']:.1f} 天")
                    print(f"    阈值: {bear_details['threshold']} 天")
                
                macro_details = details.get('macro_phase')
                if macro_details:
                    print(f"\n  macro_phase 分布偏离:")
                    for phase, info in macro_details.items():
                        print(f"    {phase}:")
                        print(f"      近期占比: {info['recent_pct']:.2%}")
                        print(f"      历史占比: {info['historical_pct']:.2%}")
            
            elif condition == 'D':
                print("回测已证明某一 regime 内存在稳定结构性短板")
                print(f"  触发的 regime: {details.get('triggered_regimes', [])}")
                print(f"  最小样本量: {details.get('min_sample_size', 0)}")
                
                regime_stats = details.get('regime_stats', {})
                if regime_stats:
                    print("\n  Regime 统计:")
                    for regime, stats in regime_stats.items():
                        print(f"    {regime}:")
                        print(f"      交易数: {stats['trade_count']}")
                        print(f"      平均收益: {stats['avg_profit']:.4f}")
                        print(f"      Profit Factor: {stats['profit_factor']:.2f}")
                        if stats['avg_duration_hours']:
                            print(f"      平均持仓时间: {stats['avg_duration_hours']:.1f} 小时")
    
    else:
        print("\n✅ 未触发任何 P2 启动条件")
        print("\n当前系统运行正常，暂不需要启动 P2 regime-aware 授权系统")
    
    print("\n" + "=" * 80)


def main():
    """主函数"""
    print("P2 触发条件检查脚本")
    print("=" * 80)
    
    # 初始化诊断器
    diagnostics = RegimeDiagnostics()
    
    # 加载数据
    print("\n正在加载数据...")
    backtest_trades = load_backtest_trades()
    ohlcv_data = load_ohlcv_data()
    
    # 运行诊断
    print("\n正在运行诊断...")
    report = diagnostics.run_full_diagnostics(
        trades_df=backtest_trades if not backtest_trades.empty else None,
        dataframe=ohlcv_data if not ohlcv_data.empty else None,
        backtest_trades=backtest_trades if not backtest_trades.empty else None
    )
    
    # 打印报告
    print_report(report)
    
    # 保存报告
    report_file = Path('p2_trigger_report.json')
    with open(report_file, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)
    
    print(f"\n报告已保存至: {report_file}")
    
    # 返回状态码
    return 0 if report['p2_activation_recommended'] else 1


if __name__ == '__main__':
    sys.exit(main())
