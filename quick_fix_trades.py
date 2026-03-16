#!/usr/bin/env python3
"""快速修复：从回测结果补充 trade_close 事件"""
import json
import sqlite3
import zipfile
from pathlib import Path
from datetime import datetime, timezone

# 1. 读取回测结果
print("读取回测结果...")
zip_file = Path("user_data/backtest_results/backtest-result-2026-03-14_12-05-41.zip")
with zipfile.ZipFile(zip_file) as zf:
    with zf.open(zf.namelist()[0]) as f:
        data = json.load(f)

strategy_name = list(data['strategy'].keys())[0]
trades = data['strategy'][strategy_name]['trades']
print(f"找到 {len(trades)} 笔交易")

# 2. 找到对应的 run_dir
run_dir = Path("user_data/events/backtest/run_20260314_120538_backtest_e5cb6acc")
run_id = run_dir.name

# 3. 生成 trade_close 事件
print(f"生成 trade_close 事件...")
trades_file = run_dir / "trades.jsonl"

with trades_file.open("a", encoding="utf-8") as f:
    for trade in trades:
        if trade.get('close_timestamp'):
            open_ts = trade['open_timestamp'] / 1000
            close_ts = trade['close_timestamp'] / 1000
            duration_minutes = int((close_ts - open_ts) / 60)
            
            event = {
                "event_type": "trade_event",
                "event_subtype": "trade_close",
                "trade_id": f"trade_{int(open_ts)}",
                "run_id": run_id,
                "ts": datetime.fromtimestamp(close_ts, tz=timezone.utc).isoformat(),
                "pair": trade['pair'],
                "side": "long",
                "exit_reason": trade.get('exit_reason', 'unknown'),
                "exit_tag": trade.get('exit_reason', 'unknown'),
                "exit_price": trade['close_rate'],
                "profit_pct": trade['profit_ratio'],
                "profit_abs": trade['profit_abs'],
                "duration_minutes": duration_minutes,
                "mfe_pct": None,
                "mae_pct": None,
            }
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

print(f"✅ 已追加 {len(trades)} 条 trade_close 事件")

# 4. 重新摄取
print("\n重新摄取 trades.jsonl...")
import subprocess
result = subprocess.run([
    "python", "user_data/ingest/ingest_jsonl.py",
    "--db", "user_data/market.db",
    "--events", str(trades_file)
], capture_output=True, text=True)
print(result.stdout)

# 5. 验证
print("\n验证数据...")
conn = sqlite3.connect('user_data/market.db')
closed_trades = conn.execute("SELECT COUNT(*) FROM trades WHERE exit_time IS NOT NULL").fetchone()[0]
print(f"已平仓交易数: {closed_trades}")
conn.close()

print("\n✅ 完成！")
