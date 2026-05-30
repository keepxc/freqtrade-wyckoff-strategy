"""MSE Parameter Sweep — 测试不同窗口参数对评分稳定性的影响。

用法：
  python scripts/sweep_mse_params.py

环境变量覆盖：
  MSE_CVD_WINDOW=20 MSE_PA_RANGE_N=4 python scripts/sweep_mse_params.py --quick

输出：每个参数组合的评分分布、最高分、WATCH+币种数。
"""

import subprocess, sys, os, json, time
from pathlib import Path

# ── 参数网格定义 ──
GRID = {
    # CVD_WINDOW: 滚动窗口 bar 数（15m×N）
    "CVD_WINDOW":       [20, 30, 40, 50, 60],       # 5h ~ 15h
    # CVD_SLOPE_N: 斜率判断 bar 数
    "CVD_SLOPE_N":       [3, 4, 6, 8, 10],           # 45m ~ 2.5h
    # PA_RANGE_N: Price Acceptance 突破范围 bar 数
    "PA_RANGE_N":        [4, 6, 8, 12, 16],           # 1h ~ 4h
}

QUICK_MODE = "--quick" in sys.argv
if QUICK_MODE:
    GRID = {k: [v[0], v[-1]] for k, v in GRID.items()}  # just extremes
    print("⚡ Quick mode: testing parameter extremes only")

# We'll sweep one param at a time (others at default)
# to isolate each parameter's effect.

SCRIPT = Path(__file__).parent / "market_state_engine.py"
DB = Path(__file__).parent.parent / "user_data" / "market.db"

def run_scan_with_params(env_overrides: dict) -> dict:
    """Run a single MSE scan with given env vars, return grade distribution."""
    env = os.environ.copy()
    for k, v in env_overrides.items():
        env[f"MSE_{k}"] = str(v)

    # Write a temp runner script
    runner = Path(__file__).parent / "_sweep_runner.py"
    runner.write_text(f"""
import sqlite3, sys, json, os
for k, v in {env_overrides}.items():
    os.environ[f"MSE_{{k}}"] = str(v)
sys.path.insert(0, r"{Path(__file__).parent.as_posix()}")
from market_state_engine import init_db, run_scan, DB_PATH
conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
init_db(conn)
run_scan(conn)
ts = conn.execute("SELECT MAX(ts) FROM coin_states_v2").fetchone()[0]
grades = {{}}
for r in conn.execute("SELECT grade, COUNT(*) FROM coin_states_v2 WHERE ts=? GROUP BY grade", (ts,)):
    grades[r[0]] = r[1]
top = conn.execute("SELECT symbol, grade, score FROM coin_states_v2 WHERE ts=? ORDER BY score DESC LIMIT 3", (ts,)).fetchall()
conn.close()
with open(r"{runner.with_suffix('.json').as_posix()}", "w") as f:
    json.dump({{"grades": grades, "top3": [(t[0], t[1], t[2]) for t in top]}}, f)
""")

    result = subprocess.run(
        [sys.executable, str(runner)],
        capture_output=True, text=True, timeout=120, env=env,
        cwd=Path(__file__).parent.parent,
    )

    out_file = runner.with_suffix(".json")
    data = {}
    if out_file.exists():
        try:
            data = json.loads(out_file.read_text())
        except Exception:
            pass
        out_file.unlink(missing_ok=True)
    runner.unlink(missing_ok=True)

    data["stderr"] = result.stderr[-300:] if result.stderr else ""
    return data


def sweep_one_param(param_name: str, values: list[int], default_env: dict):
    """Sweep a single parameter, print comparison table."""
    print(f"\n{'='*70}")
    print(f"  Sweeping: {param_name}")
    print(f"  Values: {values}")
    print(f"  Default env: {default_env}")
    print(f"{'='*70}")
    print(f"{'Value':>6s}  {'A':>3s} {'B':>3s} {'WATCH':>5s} {'NOISE':>5s} {'SQUEEZE':>7s} {'BLOCKED':>7s}  {'Top-1':12s} {'Score':>5s}")
    print("-" * 70)

    for val in values:
        env = {**default_env, param_name: val}
        result = run_scan_with_params(env)
        g = result.get("grades", {})
        top = result.get("top3", [])
        top1 = top[0] if top else ("-", "-", 0)
        print(
            f"  {val:>6d}  {g.get('A',0):>3d} {g.get('B',0):>3d} "
            f"{g.get('WATCH',0):>5d} {g.get('NOISE',0):>5d} "
            f"{g.get('SQUEEZE',0):>7d} {g.get('BLOCKED',0):>7d}  "
            f"{top1[0]:12s} {top1[2]:5.0f}"
        )
        if result.get("stderr"):
            print(f"         ⚠️  stderr: {result['stderr'][:100]}")
        time.sleep(1)  # rate limit


def main():
    print("MSE Parameter Sweep — Testing window parameter sensitivity")
    print(f"DB: {DB}")

    # Default values for params not being swept
    defaults = {
        "CVD_WINDOW": 40, "CVD_SLOPE_N": 6, "PA_RANGE_N": 6,
        "CVD_DIVERGE_WINDOW": 20, "PA_SUSTAIN_N": 3,
        "TREND_MA_WINDOW": 40, "VOL_MA_WINDOW": 20,
    }

    for param, values in GRID.items():
        sweep_one_param(param, values, defaults)
        print()

    print("\n✅ Sweep complete. Compare 'WATCH + B + A' counts across values.")


if __name__ == "__main__":
    main()
