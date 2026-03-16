#!/usr/bin/env python3
"""
从 Freqtrade 回测结果补充 trade_close 事件

回测模式下，custom_exit 不会被调用，因此 trade_close 事件不会自动发射。
此脚本从回测结果 JSON 文件中读取交易数据，补充缺失的 trade_close 事件。
"""
import json
import sys
from pathlib import Path
from datetime import datetime, timezone


def find_latest_backtest_result():
    """查找最新的回测结果文件"""
    results_dir = Path("user_data/backtest_results")
    
    # 直接读取 .last_result.json
    last_result = results_dir / ".last_result.json"
    if last_result.exists():
        return last_result
    
    print("❌ 未找到 .last_result.json")
    return None


def find_run_dir_for_backtest(backtest_time):
    """根据回测时间查找对应的 run 目录"""
    events_dir = Path("user_data/events/backtest")
    run_dirs = sorted(events_dir.glob("run_*"), key=lambda p: p.stat().st_mtime, reverse=True)
    
    # 返回最新的 run 目录（假设是刚刚完成的回测）
    if run_dirs:
        return run_dirs[0]
    return None


def generate_trade_close_events(backtest_result, run_id):
    """从回测结果生成 trade_close 事件"""
    events = []
    
    for trade in backtest_result.get('trades', []):
        if trade.get('close_timestamp'):
            # 计算持仓时长
            open_ts = trade['open_timestamp'] / 1000  # 毫秒转秒
            close_ts = trade['close_timestamp'] / 1000
            duration_minutes = int((close_ts - open_ts) / 60)
            
            event = {
                "event_type": "trade_event",
                "event_subtype": "trade_close",
                "trade_id": f"trade_{int(open_ts)}",
                "run_id": run_id,
                "ts": datetime.fromtimestamp(close_ts, tz=timezone.utc).isoformat(),
                "pair": trade['pair'],
                "side": "long" if not trade.get('is_short', False) else "short",
                "exit_reason": trade.get('exit_reason', 'unknown'),
                "exit_tag": trade.get('exit_reason', 'unknown'),
                "exit_price": trade['close_rate'],
                "profit_pct": trade['profit_ratio'],
                "profit_abs": trade['profit_abs'],
                "duration_minutes": duration_minutes,
                "mfe_pct": None,  # 回测结果中没有这些数据
                "mae_pct": None,
            }
            events.append(event)
    
    return events


def append_events_to_file(run_dir, events):
    """将事件追加到 trades.jsonl 文件"""
    trades_file = run_dir / "trades.jsonl"
    
    with trades_file.open("a", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    
    print(f"✅ 追加了 {len(events)} 条 trade_close 事件到 {trades_file}")


def main():
    print("=" * 70)
    print("从回测结果补充 trade_close 事件")
    print("=" * 70)
    
    # 1. 查找最新的回测结果
    print("\n[1/4] 查找最新回测结果...")
    meta_file = find_latest_backtest_result()
    if not meta_file:
        sys.exit(1)
    
    print(f"✅ 找到: {meta_file.name}")
    
    # 2. 读取回测结果
    print("\n[2/4] 读取回测数据...")
    with meta_file.open("r", encoding="utf-8") as f:
        backtest_result = json.load(f)
    
    strategy_name = list(backtest_result['strategy'].keys())[0]
    trades_count = len(backtest_result['strategy'][strategy_name]['trades'])
    print(f"✅ 策略: {strategy_name}, 交易数: {trades_count}")
    
    # 3. 查找对应的 run 目录
    print("\n[3/4] 查找事件目录...")
    run_dir = find_run_dir_for_backtest(None)
    if not run_dir:
        print("❌ 未找到对应的 run 目录")
        sys.exit(1)
    
    print(f"✅ 找到: {run_dir.name}")
    
    # 提取 run_id
    run_id = run_dir.name
    
    # 4. 生成并追加 trade_close 事件
    print("\n[4/4] 生成 trade_close 事件...")
    events = generate_trade_close_events(
        backtest_result['strategy'][strategy_name],
        run_id
    )
    
    if events:
        append_events_to_file(run_dir, events)
    else:
        print("⚠️  没有需要补充的交易")
    
    print("\n" + "=" * 70)
    print("✅ 完成！现在可以重新摄取事件到数据库")
    print("=" * 70)
    print(f"\n命令：python user_data/ingest/ingest_jsonl.py \\")
    print(f"      --db user_data/market.db \\")
    print(f"      --events {run_dir}/trades.jsonl")


if __name__ == "__main__":
    main()
