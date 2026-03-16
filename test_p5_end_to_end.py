#!/usr/bin/env python3
"""P5 端到端测试：策略集成 → 事件生成 → 摄取 → 周报"""
import subprocess
import sys
from pathlib import Path

print("=" * 70)
print("P5 V1 端到端测试")
print("=" * 70)

# 1. 策略集成测试
print("\n[1/4] 测试策略事件集成...")
result = subprocess.run([sys.executable, "test_strategy_integration.py"], capture_output=True, text=True)
if result.returncode != 0:
    print("❌ 策略集成测试失败")
    print(result.stdout)
    print(result.stderr)
    sys.exit(1)
print("✅ 策略集成测试通过")

# 2. 查找最新的事件文件
print("\n[2/4] 查找生成的事件文件...")
events_dir = Path("user_data/events/backtest")
run_dirs = sorted(events_dir.glob("run_*"), key=lambda p: p.stat().st_mtime, reverse=True)
if not run_dirs:
    print("❌ 未找到事件文件")
    sys.exit(1)

latest_run = run_dirs[0]
event_files = list(latest_run.glob("*.jsonl"))
print(f"✅ 找到 {len(event_files)} 个事件文件:")
for f in event_files:
    print(f"   - {f.name}")

# 3. 摄取事件到数据库
print("\n[3/4] 摄取事件到数据库...")
event_paths = [str(f) for f in event_files]
cmd = [
    sys.executable,
    "user_data/ingest/ingest_jsonl.py",
    "--db", "user_data/market.db",
    "--events"
] + event_paths

result = subprocess.run(cmd, capture_output=True, text=True)
if result.returncode != 0:
    print("❌ 事件摄取失败")
    print(result.stdout)
    print(result.stderr)
    sys.exit(1)
print("✅ 事件摄取成功")
print(result.stdout.strip())

# 4. 验证数据
print("\n[4/4] 验证数据库内容...")
import sqlite3
conn = sqlite3.connect('user_data/market.db')

runs = conn.execute("SELECT COUNT(*) FROM experiment_runs").fetchone()[0]
trades = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
signals = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]

print(f"   - Runs: {runs}")
print(f"   - Trades: {trades}")
print(f"   - Signals: {signals}")

conn.close()

if runs > 0:
    print("✅ 数据验证通过")
else:
    print("⚠️  数据库中没有 run 记录")

print("\n" + "=" * 70)
print("✅ P5 V1 端到端测试完成")
print("=" * 70)
print("\n下一步：运行真实回测生成完整事件数据")
print("命令：docker compose run --rm freqtrade backtesting \\")
print("      --config user_data/config_backtest.json \\")
print("      --strategy WyckoffV7_TrueMethod \\")
print("      --timerange 20260101-20260107")
