#!/usr/bin/env python3
"""
lint_golden_rules.py — 机械化执行黄金规则。

检查策略文件、数据库和项目文档中的违规行为。
退出码: 0 = 干净, 1 = 发现违规。
"""

import re
import sys
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
P5_DB = PROJECT_ROOT / "P5" / "p5.db"
STRATEGY_FILE = PROJECT_ROOT / "user_data" / "strategies" / "WyckoffV7_TrueMethod.py"


def check_g001_downwave_params() -> list[str]:
    """G001: down_wave 参数不可变

    检查策略文件中 down_wave_eff_threshold 的 os.getenv() 默认值。
    注意：WY_DW_DISPLACEMENT 不是独立 env 参数，不检查。
    """
    violations = []
    if not STRATEGY_FILE.exists():
        violations.append(f"[G001] 策略文件未找到: {STRATEGY_FILE}")
        return violations

    text = STRATEGY_FILE.read_text()
    # 匹配: down_wave_eff_threshold: float = float(os.getenv('WY_DW_EFF_THRESHOLD', '0.001'))
    # 只检查 os.getenv 的默认值（第二个参数）
    pattern = r"""down_wave_eff_threshold[^=]*=\s*float\(\s*os\.getenv\(\s*['\"]WY_DW_EFF_THRESHOLD['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)\s*\)"""
    match = re.search(pattern, text)
    if not match:
        violations.append(
            "[G001] 未找到 down_wave_eff_threshold 的 os.getenv 定义. "
            "修复: 确认参数存在且格式为 float(os.getenv('WY_DW_EFF_THRESHOLD', '0.001'))"
        )
    elif match.group(1) != "0.001":
        violations.append(
            f"[G001] down_wave_eff_threshold 默认值: {match.group(1)}, "
            f"期望 0.001. "
            f"修复: 恢复为 os.getenv('WY_DW_EFF_THRESHOLD', '0.001') (见 DECISIONS.md D001)"
        )
    return violations


def check_g002_no_backtest_data() -> list[str]:
    """G002: P5 只用 dry_run 数据"""
    violations = []
    if not P5_DB.exists():
        violations.append(f"[G002] P5 数据库未找到: {P5_DB}（跳过检查）")
        return violations

    try:
        conn = sqlite3.connect(str(P5_DB))
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM experiment_runs WHERE mode = 'backtest'"
        )
        count = cur.fetchone()[0]
        if count > 0:
            violations.append(
                f"[G002] 发现 {count} 条 backtest 模式记录在 experiment_runs 中. "
                f"修复: python scripts/gc_market_db.py --mode backtest (见 D004)"
            )
        conn.close()
    except sqlite3.OperationalError as e:
        violations.append(f"[G002] 数据库查询失败: {e}")
    return violations


def check_g003_no_hardcoded_paths() -> list[str]:
    """G003: 禁止硬编码绝对路径 /freqtrade/"""
    violations = []
    # 检查关键 py 文件
    search_dirs = [
        PROJECT_ROOT / "user_data" / "strategies",
        PROJECT_ROOT / "wyckoff",
        PROJECT_ROOT / "scripts",
        PROJECT_ROOT,
    ]

    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        for py_file in search_dir.rglob("*.py"):
            # 跳过 venv
            if ".venv" in str(py_file):
                continue
            try:
                text = py_file.read_text()
                # 匹配字符串中的 /freqtrade/
                for line_no, line in enumerate(text.splitlines(), 1):
                    if re.search(r'["\']/[Ff]reqtrade/', line):
                        # 排除注释和路径示例
                        if "fixme" in line.lower() or "# example" in line.lower():
                            continue
                        rel = py_file.relative_to(PROJECT_ROOT)
                        violations.append(
                            f"[G003] {rel}:{line_no} 发现硬编码 /freqtrade/ 路径: "
                            f"{line.strip()[:80]}"
                            f" 修复: 替换为 /home/gyan/freqtrade/"
                        )
            except (IOError, UnicodeDecodeError):
                continue
    return violations


def check_g005_baseline_reference(file_path: Path) -> list[str]:
    """G005: 回测报告必须引用 V7.1 baseline

    认可两种引用方式：
    - 显式 baseline 对比（8997363, 8,374%, V7.1 baseline）
    - 声明"本报告生成于 baseline 确立之前"（说明性免责）
    """
    violations = []
    if not file_path.exists():
        return violations
    text = file_path.read_text()
    # 如果文件声明了"predates baseline"，跳过（历史报告）
    if re.search(r'生成于.*baseline.*之前|predates.*baseline', text):
        return violations
    # 宽松检查：文件如果包含 backtest 或 回测 关键字，也应有 baseline 引用
    if re.search(r'backtest|回测|8,374%|8.374', text, re.IGNORECASE):
        if not re.search(r'8997363|8,374%|V7\.1.*baseline|baseline.*V7\.1', text):
            violations.append(
                f"[G005] {file_path.name}: 包含回测结果但未引用 V7.1 baseline. "
                f"修复: 添加 baseline 对比 (见 DECISIONS.md D003)"
            )
    return violations


def main() -> int:
    exit_code = 0
    all_violations = []

    print("=" * 60)
    print("黄金规则 Lint 检查")
    print("=" * 60)

    # G001
    print("\n[G001] down_wave 参数检查...")
    violations = check_g001_downwave_params()
    all_violations.extend(violations)
    for v in violations:
        print(f"  ❌ {v}")
    if not violations:
        print("  ✅ 通过")

    # G002
    print("\n[G002] P5 数据源检查...")
    violations = check_g002_no_backtest_data()
    all_violations.extend(violations)
    for v in violations:
        print(f"  ❌ {v}")
    if not violations:
        print("  ✅ 通过")

    # G003
    print("\n[G003] 硬编码路径检查...")
    violations = check_g003_no_hardcoded_paths()
    all_violations.extend(violations)
    for v in violations:
        print(f"  ❌ {v}")
    if not violations:
        print("  ✅ 通过")

    # G005 — 检查最近的 backtest 报告
    print("\n[G005] Baseline 引用检查...")
    report_candidates = [
        PROJECT_ROOT / "TASK_NEW" / "BACKTEST_COMPARISON_20230101_20260305.md",
        PROJECT_ROOT / "P0_MIXED_STRATEGY_BACKTEST_RESULTS.md",
    ]
    violations = []
    for rp in report_candidates:
        violations.extend(check_g005_baseline_reference(rp))
    all_violations.extend(violations)
    for v in violations:
        print(f"  ❌ {v}")
    if not violations:
        print("  ✅ 通过")

    # Summary
    print("\n" + "=" * 60)
    if all_violations:
        print(f"发现 {len(all_violations)} 条违规:")
        for v in all_violations:
            print(f"  {v}")
        exit_code = 1
    else:
        print("所有检查通过 ✅")
        exit_code = 0
    print("=" * 60)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
