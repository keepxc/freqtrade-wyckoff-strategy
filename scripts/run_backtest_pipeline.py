#!/usr/bin/env python3
"""
P5 全流程自动化脚本（回测 + 定时维护 合并版）

──────────────────────────────────────────────
【回测模式】（默认，mode=backtest）
──────────────────────────────────────────────
  # 完整流程：Docker 回测 → 摄入最新目录 → 出报表
  python scripts/run_backtest_pipeline.py --timerange 20260101-20260114

  # 跳过回测，摄入最新目录 + 出报表
  python scripts/run_backtest_pipeline.py --skip-backtest --week-start 2026-01-01 --week-end 2026-01-14

  # 仅重新出报表（不摄入）
  python scripts/run_backtest_pipeline.py --skip-backtest --skip-ingest --week-start 2026-01-01 --week-end 2026-01-14

──────────────────────────────────────────────
【定时维护模式】（mode=dry_run / live）
──────────────────────────────────────────────
  # 摄入所有 dry_run 事件目录 + 生成上周周报
  python scripts/run_backtest_pipeline.py --mode dry_run --all-runs --weekly

  # 摄入所有 live 事件目录
  python scripts/run_backtest_pipeline.py --mode live --all-runs

  # 处理回测 trade_close 补丁 + 摄入 backtest 目录
  python scripts/run_backtest_pipeline.py --mode backtest --all-runs --fix-trades

──────────────────────────────────────────────
参数说明
──────────────────────────────────────────────
  --mode            运行模式: backtest(默认) / dry_run / live
  --timerange       回测时间范围 YYYYMMDD-YYYYMMDD（backtest 模式必填）
  --strategy        策略名称（默认: WyckoffV7_TrueMethod）
  --config          回测配置文件（默认: user_data/config_backtest.json）
  --db              SQLite 路径（默认: user_data/market.db）
  --week-start      周报起始日期 YYYY-MM-DD
  --week-end        周报结束日期 YYYY-MM-DD
  --all-runs        摄入指定 mode 目录下的全部 run（定时维护用）
  --skip-backtest   跳过 Docker 回测
  --skip-ingest     跳过摄入
  --skip-reports    跳过报表生成
  --weekly          生成周报（dry_run/live 模式默认不生成，需显式开启）
  --fix-trades      运行 quick_fix_trades.py 补丁
  --run-type        run_type 标签（默认: baseline）
"""

import argparse
import subprocess
import sys
import os
from datetime import datetime, timedelta
from pathlib import Path

# ──────────────────────────────────────────────
# 路径设置
# ──────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="P5 全流程自动化（回测 + 定时维护）")
    p.add_argument("--mode", default="backtest", choices=["backtest", "dry_run", "live"],
                   help="运行模式（默认: backtest）")
    p.add_argument("--timerange", help="回测时间范围，格式 YYYYMMDD-YYYYMMDD")
    p.add_argument("--strategy", default="WyckoffV7_TrueMethod")
    p.add_argument("--config", default="user_data/config_backtest.json")
    p.add_argument("--db", default="user_data/market.db")
    p.add_argument("--week-start", help="周报起始日期 YYYY-MM-DD")
    p.add_argument("--week-end", help="周报结束日期 YYYY-MM-DD")
    p.add_argument("--all-runs", action="store_true",
                   help="摄入该 mode 下所有 run 目录（定时维护用，默认只处理最新一个）")
    p.add_argument("--skip-backtest", action="store_true", help="跳过 Docker 回测")
    p.add_argument("--skip-ingest", action="store_true", help="跳过摄入步骤")
    p.add_argument("--skip-reports", action="store_true", help="跳过报表生成")
    p.add_argument("--weekly", action="store_true",
                   help="生成周报（dry_run/live 模式需显式开启；backtest 模式默认开启）")
    p.add_argument("--fix-trades", action="store_true",
                   help="运行 quick_fix_trades.py 补丁（补充 trade_close 事件）")
    p.add_argument("--run-type", default="baseline")
    return p.parse_args()


def timerange_to_dates(timerange: str):
    """将 YYYYMMDD-YYYYMMDD 转换为 (week_start, week_end) 字符串 YYYY-MM-DD"""
    parts = timerange.split("-")
    if len(parts) != 2 or len(parts[0]) != 8 or len(parts[1]) != 8:
        return None, None
    start = datetime.strptime(parts[0], "%Y%m%d")
    end = datetime.strptime(parts[1], "%Y%m%d")
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


# ──────────────────────────────────────────────
# Step 1: Docker 回测
# ──────────────────────────────────────────────
def run_backtest(args: argparse.Namespace) -> bool:
    log(f"▶ Step 1: 运行 Docker 回测 [{args.timerange}] 策略={args.strategy}")
    cmd = [
        "docker", "compose", "run", "--rm", "freqtrade",
        "backtesting",
        "--config", args.config,
        "--strategy", args.strategy,
        "--timerange", args.timerange,
        "--cache", "none",
    ]
    log(f"  命令: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, cwd=str(ROOT))
        if result.returncode != 0:
            log(f"✗ 回测失败，退出码 {result.returncode}")
            return False
        log("✓ 回测完成")
        return True
    except FileNotFoundError:
        log("✗ docker 命令未找到，请确认 Docker Desktop 已安装并在 PATH 中")
        return False


# ──────────────────────────────────────────────
# Step 2a: 找最新事件目录（单个）
# ──────────────────────────────────────────────
def find_latest_events_dir(events_root: Path) -> Path | None:
    if not events_root.exists():
        log(f"✗ 事件根目录不存在: {events_root}")
        return None
    candidates = sorted(
        [d for d in events_root.iterdir() if d.is_dir()],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        log("✗ 未找到任何事件目录")
        return None
    latest = candidates[0]
    log(f"✓ 最新事件目录: {latest.name}")
    return latest


# ──────────────────────────────────────────────
# Step 2b: 找所有事件目录（批量维护用）
# ──────────────────────────────────────────────
def find_all_events_dirs(events_root: Path) -> list[Path]:
    if not events_root.exists():
        log(f"✗ 事件根目录不存在: {events_root}")
        return []
    dirs = sorted(
        [d for d in events_root.iterdir() if d.is_dir()],
        key=lambda d: d.stat().st_mtime,
    )
    log(f"✓ 找到 {len(dirs)} 个事件目录")
    return dirs


# ──────────────────────────────────────────────
# Step 3: 摄入 JSONL（单目录或多目录）
# ──────────────────────────────────────────────
def run_ingest_one(events_dir: Path, db_path: Path, label: str = "") -> bool:
    """摄入单个事件目录"""
    jsonl_files = list(events_dir.glob("*.jsonl"))
    if not jsonl_files:
        log(f"  跳过（无 .jsonl）: {events_dir.name}")
        return True

    # run.jsonl 必须排最前（外键约束）
    sorted_files = sorted(jsonl_files, key=lambda p: (0 if p.name == "run.jsonl" else 1, p.name))
    prefix = f"[{label}] " if label else ""
    log(f"{prefix}摄入 {events_dir.name}（{len(sorted_files)} 个文件）")
    for p in sorted_files:
        log(f"  {p.name}: {p.stat().st_size:,} bytes")

    ingest_script = ROOT / "user_data" / "ingest" / "ingest_jsonl.py"
    cmd = [sys.executable, str(ingest_script), "--db", str(db_path), "--events"] + [str(f) for f in sorted_files]
    utf8_env = {**os.environ, "PYTHONUTF8": "1"}
    result = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", env=utf8_env)
    if result.returncode != 0:
        log(f"✗ 摄入失败:\n{result.stderr}")
        return False
    log(f"✓ {result.stdout.strip()}")
    return True


def run_ingest(events_dir: Path, db_path: Path) -> bool:
    """摄入单个目录（向后兼容接口）"""
    return run_ingest_one(events_dir, db_path)


def run_ingest_all(events_dirs: list[Path], db_path: Path) -> bool:
    """批量摄入多个事件目录（定时维护用）"""
    if not events_dirs:
        log("没有需要处理的事件目录")
        return True
    ok = True
    for i, d in enumerate(events_dirs, 1):
        if not run_ingest_one(d, db_path, label=f"{i}/{len(events_dirs)}"):
            ok = False
    return ok


# ──────────────────────────────────────────────
# Step 4: 生成报表
# ──────────────────────────────────────────────
# ──────────────────────────────────────────────
# Fix trades patch（来自旧定时任务脚本）
# ──────────────────────────────────────────────
def run_fix_trades() -> None:
    fix_script = ROOT / "quick_fix_trades.py"
    if not fix_script.exists():
        log("⚠ quick_fix_trades.py 不存在，跳过")
        return
    log("▶ 运行 quick_fix_trades.py...")
    utf8_env = {**os.environ, "PYTHONUTF8": "1"}
    result = subprocess.run([sys.executable, str(fix_script)], cwd=str(ROOT),
                            capture_output=True, text=True, encoding="utf-8", env=utf8_env)
    if result.returncode == 0:
        log(f"✓ fix_trades 完成")
        if result.stdout.strip():
            print(result.stdout.strip())
    else:
        log(f"✗ fix_trades 失败:\n{result.stderr[:500]}")


def run_reports(db_path: Path, week_start: str, week_end: str) -> None:
    log(f"▶ 生成报表（周期 {week_start} ~ {week_end}）")
    reports_dir = ROOT / "user_data" / "reports"

    def run_report(name: str, script: str, extra_args: list[str]) -> None:
        cmd = [sys.executable, script] + extra_args
        utf8_env = {**os.environ, "PYTHONUTF8": "1"}
        result = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace", env=utf8_env)
        if result.returncode != 0:
            log(f"  ✗ {name} 失败:\n{result.stderr[:500]}")
        else:
            log(f"  ✓ {name}")
            # 打印报表前 20 行
            lines = result.stdout.strip().splitlines()
            for line in lines[:20]:
                print(f"    {line}")
            if len(lines) > 20:
                print(f"    ... （共 {len(lines)} 行）")

    # Detector 激活率报表
    run_report(
        "Detector 激活率报表",
        str(reports_dir / "detector_activation_report.py"),
        ["--db", str(db_path), "--baseline-start", week_start, "--baseline-end", week_end],
    )

    # Regime 分布报表
    run_report(
        "Regime 分布报表",
        str(reports_dir / "regime_distribution_report.py"),
        ["--db", str(db_path), "--baseline-start", week_start, "--baseline-end", week_end],
    )

    # Enter Tag Drift 报表
    run_report(
        "Enter Tag Drift 报表",
        str(reports_dir / "enter_tag_drift_report.py"),
        ["--db", str(db_path), "--baseline-start", week_start, "--baseline-end", week_end,
         "--recent-start", week_start, "--recent-end", week_end],
    )

    # 周报（落库）
    run_report(
        "Weekly Report",
        str(reports_dir / "build_weekly_report.py"),
        ["--db", str(db_path), "--week-start", week_start, "--week-end", week_end, "--store"],
    )
    log("✓ 所有报表生成完成")


# ──────────────────────────────────────────────
# DB 行数摘要
# ──────────────────────────────────────────────
def print_db_summary(db_path: Path) -> None:
    try:
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        tables = ["experiment_runs", "signals", "trades", "detector_events",
                  "regime_snapshots", "exit_decision_events", "weekly_reports"]
        log("📊 数据库摘要：")
        for t in tables:
            try:
                n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                print(f"    {t}: {n}")
            except Exception:
                pass
        conn.close()
    except Exception as e:
        log(f"  （无法读取数据库：{e}）")


# ──────────────────────────────────────────────
# 自动推导上周日期（定时维护用）
# ──────────────────────────────────────────────
def last_week_range() -> tuple[str, str]:
    from datetime import timezone
    now = datetime.now(timezone.utc)
    end = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=7)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


# ──────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────
def main() -> None:
    args = parse_args()
    os.chdir(ROOT)

    mode = args.mode
    events_root = ROOT / "user_data" / "events" / mode
    db_path = ROOT / args.db

    log(f"P5 全流程自动化  mode={mode}")
    log("-" * 50)

    # ── fix-trades 补丁 ──
    if args.fix_trades:
        run_fix_trades()

    # ── 推导周报日期 ──
    week_start = args.week_start
    week_end = args.week_end
    if mode == "backtest" and not week_start and args.timerange:
        week_start, week_end = timerange_to_dates(args.timerange)
    elif mode in ("dry_run", "live") and not week_start:
        week_start, week_end = last_week_range()
        log(f"自动推导上周日期: {week_start} ~ {week_end}")

    # ── Docker 回测（仅 backtest 模式）──
    if mode == "backtest" and not args.skip_backtest:
        if not args.timerange:
            print("错误：backtest 模式必须提供 --timerange（或 --skip-backtest）", file=sys.stderr)
            sys.exit(1)
        if not run_backtest(args):
            sys.exit(1)

    # ── 摄入 ──
    if not args.skip_ingest:
        if args.all_runs:
            # 批量：扫描 mode 目录下全部 run_*
            all_dirs = find_all_events_dirs(events_root)
            if not run_ingest_all(all_dirs, db_path):
                sys.exit(1)
        else:
            # 单个：最新一条（backtest 默认行为）
            events_dir = find_latest_events_dir(events_root)
            if events_dir is None:
                sys.exit(1)
            if not run_ingest(events_dir, db_path):
                sys.exit(1)

    print_db_summary(db_path)

    # ── 报表 ──
    # backtest 模式默认出报表；dry_run/live 需要显式 --weekly
    should_report = (not args.skip_reports) and (
        (mode == "backtest") or args.weekly
    )
    if should_report:
        if not week_start or not week_end:
            log("⚠ 未能确定报表时间范围，跳过报表")
            log("  提示: 手动添加 --week-start YYYY-MM-DD --week-end YYYY-MM-DD")
        else:
            run_reports(db_path, week_start, week_end)

    log("🎉 完成")


if __name__ == "__main__":
    main()
