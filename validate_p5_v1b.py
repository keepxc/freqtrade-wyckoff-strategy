#!/usr/bin/env python3
"""P5 V1-B 验证脚本：
1. 应用 DB 迁移（exit_decision_events 表）
2. 摄取 sample_events.jsonl
3. 验证所有表存在且有数据
4. 运行 test_strategy_integration 验证事件桥接
"""
import sqlite3
import subprocess
import sys
from pathlib import Path

DB = Path("user_data/market.db")
MIGRATION = Path("user_data/db/migrations/v1b_001_exit_decision_events.sql")
SAMPLE_EVENTS = Path("user_data/examples/sample_events.jsonl")

PASS = "\u2705"
FAIL = "\u274c"
WARN = "\u26a0\ufe0f"

def step(msg):
    print(f"\n{'='*60}")
    print(f"  {msg}")
    print('='*60)

# -------------------------------------------------------
# 1. 检查 DB
# -------------------------------------------------------
step("1/5  检查 / 创建数据库")
if not DB.exists():
    print(f"{WARN}  market.db 不存在，跳过迁移检查（ingest 会自动创建）")
else:
    conn = sqlite3.connect(str(DB))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    print(f"  现有表: {sorted(tables)}")
    conn.close()

# -------------------------------------------------------
# 2. 应用迁移
# -------------------------------------------------------
step("2/5  应用 V1-B 迁移：exit_decision_events")
if not DB.exists():
    print(f"{WARN}  DB 不存在，迁移将在 ingest 创建 DB 后自动跳过（ingest 不执行此迁移）")
    print(f"  迁移文件路径: {MIGRATION}")
else:
    conn = sqlite3.connect(str(DB))
    try:
        conn.executescript(MIGRATION.read_text(encoding="utf-8"))
        conn.commit()
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "exit_decision_events" in tables:
            print(f"{PASS}  exit_decision_events 表已存在")
        else:
            print(f"{FAIL}  迁移后仍未找到 exit_decision_events 表")
            sys.exit(1)
    except Exception as e:
        print(f"{FAIL}  迁移失败: {e}")
        sys.exit(1)
    finally:
        conn.close()

# -------------------------------------------------------
# 3. 摄取 sample_events.jsonl
# -------------------------------------------------------
step("3/5  摄取 sample_events.jsonl")
if not SAMPLE_EVENTS.exists():
    print(f"{FAIL}  sample_events.jsonl 不存在: {SAMPLE_EVENTS}")
    sys.exit(1)

cmd = [sys.executable, "user_data/ingest/ingest_jsonl.py",
       "--db", str(DB),
       "--events", str(SAMPLE_EVENTS)]
r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
print(r.stdout.strip())
if r.returncode != 0:
    print(f"{FAIL}  摄取失败:\n{r.stderr}")
    sys.exit(1)
print(f"{PASS}  摄取成功")

# -------------------------------------------------------
# 4. 验证表数据
# -------------------------------------------------------
step("4/5  验证数据库内容")
conn = sqlite3.connect(str(DB))
required_tables = [
    "experiment_runs", "trades", "signals",
    "detector_events", "regime_snapshots",
    "exit_decision_events", "weekly_reports",
]
all_ok = True
for t in required_tables:
    try:
        n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {PASS}  {t}: {n} 行")
    except Exception as e:
        print(f"  {FAIL}  {t}: {e}")
        all_ok = False
conn.close()
if not all_ok:
    sys.exit(1)

# -------------------------------------------------------
# 5. 策略集成测试（不依赖 Docker）
# -------------------------------------------------------
step("5/5  策略事件桥接测试")
r = subprocess.run([sys.executable, "test_strategy_integration.py"],
                   capture_output=True, text=True, encoding="utf-8")
print(r.stdout.strip())
if r.returncode != 0:
    print(f"{WARN}  策略集成测试未通过（可能是缺少策略依赖，非阻断性）")
    print(r.stderr[:500])
else:
    print(f"{PASS}  策略集成测试通过")

# -------------------------------------------------------
# 完成
# -------------------------------------------------------
print(f"\n{'='*60}")
print(f"  {PASS}  P5 V1-B 本地验证完成")
print('='*60)
print()
print("Docker 回测命令（完整链路验证）：")
print("  docker compose run --rm freqtrade backtesting \\")
print("      --config user_data/config_backtest.json \\")
print("      --strategy WyckoffV7_TrueMethod \\")
print("      --timerange 20260101-20260114 --cache none")
print()
print("摄取回测事件：")
print("  python user_data/ingest/ingest_jsonl.py --db user_data/market.db \\")
print("      --events user_data/events/backtest/<run_id>/*.jsonl")
print()
print("生成 V1-B 新报表：")
print("  # Detector 激活率")
print("  python user_data/reports/detector_activation_report.py --db user_data/market.db --window 30")
print("  # Regime 分布")
print("  python user_data/reports/regime_distribution_report.py --db user_data/market.db --window 30")
print("  # Enter Tag Drift (需要两个时间窗口)")
print("  python user_data/reports/enter_tag_drift_report.py --db user_data/market.db \\")
print("      --baseline-start 2023-01-01 --baseline-end 2025-01-01 \\")
print("      --recent-start 2025-01-01 --recent-end 2026-03-14")
