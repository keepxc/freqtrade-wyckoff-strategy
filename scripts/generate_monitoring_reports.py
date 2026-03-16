#!/usr/bin/env python3
"""
Generate Monitoring Reports
生成监控报告脚本

使用方式：
    python scripts/generate_monitoring_reports.py --backtest-result <path>
"""

import sys
import argparse
import pandas as pd
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from wyckoff.detector_monitoring_panel import DetectorMonitoringPanel
from wyckoff.enter_tag_winrate_tracker import EnterTagWinRateTracker


def main():
    parser = argparse.ArgumentParser(description='Generate monitoring reports')
    parser.add_argument(
        '--backtest-result',
        type=str,
        help='Path to backtest result file (.feather or .json)'
    )
    parser.add_argument(
        '--trades',
        type=str,
        help='Path to trades file (for enter_tag tracking)'
    )
    parser.add_argument(
        '--output-dir',
        type=str,
        default='monitoring_reports',
        help='Output directory for reports'
    )
    
    args = parser.parse_args()
    
    # 创建输出目录
    output_dir = Path(args.output_dir)
    output_dir.mkdir(exist_ok=True)
    
    print("=" * 60)
    print("Monitoring Report Generator")
    print("=" * 60)
    print()
    
    # ── 1. Detector Monitoring Panel ──────────────────────────────
    if args.backtest_result:
        print("📊 Generating Detector Monitoring Report...")
        
        # 读取回测结果
        result_path = Path(args.backtest_result)
        if result_path.suffix == '.feather':
            df = pd.read_feather(result_path)
        elif result_path.suffix == '.json':
            df = pd.read_json(result_path)
        else:
            print(f"❌ Unsupported file format: {result_path.suffix}")
            return
        
        # 生成报告
        panel = DetectorMonitoringPanel()
        results = panel.analyze_dataframe(df)
        
        report_path = output_dir / 'detector_monitoring_report.md'
        panel.save_report(results, str(report_path))
        
        print(f"✅ Detector monitoring report saved to {report_path}")
        print()
    
    # ── 2. Enter Tag Win Rate Tracking ────────────────────────────
    if args.trades:
        print("📈 Generating Enter Tag Win Rate Report...")
        
        # 读取交易记录
        trades_path = Path(args.trades)
        if trades_path.suffix == '.feather':
            trades_df = pd.read_feather(trades_path)
        elif trades_path.suffix == '.json':
            trades_df = pd.read_json(trades_path)
        else:
            print(f"❌ Unsupported file format: {trades_path.suffix}")
            return
        
        # 生成报告
        tracker = EnterTagWinRateTracker()
        results = tracker.analyze_enter_tag_performance(trades_df)
        
        report_path = output_dir / 'enter_tag_winrate_report.md'
        tracker.save_report(results, str(report_path))
        
        print(f"✅ Enter tag win rate report saved to {report_path}")
        print()
    
    print("=" * 60)
    print("✅ All reports generated successfully!")
    print(f"📁 Reports location: {output_dir.absolute()}")
    print("=" * 60)


if __name__ == '__main__':
    main()
