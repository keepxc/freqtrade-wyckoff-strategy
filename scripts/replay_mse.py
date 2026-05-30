"""
MSE 历史回放 — 模拟过去 N 天的 15 分钟扫描周期，统计 watchlist 命中情况。

用法：
  python scripts/replay_mse.py --days 30        # 回放 30 天
  python scripts/replay_mse.py --days 7 --fast   # 回放 7 天，跳过 API 限速等待

输出：
  - 扫描总次数 / WATCH+ 次数
  - watchlist 准入次数 / 币种数
  - 各币种停留在 watchlist 的总时长
"""

import subprocess, sys, os, json, time, argparse
from pathlib import Path
from datetime import datetime, timezone, timedelta

SCRIPT_DIR = Path(__file__).parent
DB = SCRIPT_DIR.parent / "user_data" / "market.db"
RUNNER = SCRIPT_DIR / "_replay_runner.py"

def run_scan_at(ts: int) -> dict:
    """Run one MSE scan at given unix timestamp, return stats."""
    env = os.environ.copy()
    env["MSE_SIMULATE_TS"] = str(ts)

    # Write a one-shot runner
    RUNNER.write_text(f"""
import sqlite3, sys, json, os
os.environ["MSE_SIMULATE_TS"] = "{ts}"
sys.path.insert(0, r"{SCRIPT_DIR.as_posix()}")
from market_state_engine import init_db, run_scan, DB_PATH, SIMULATE_TS
conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
init_db(conn)
run_scan(conn)
latest_ts = conn.execute("SELECT MAX(ts) FROM coin_states_v2").fetchone()[0]
grades = {{}}
for r in conn.execute("SELECT grade, COUNT(*) FROM coin_states_v2 WHERE ts=?", (latest_ts,)):
    grades[r[0]] = r[1]
wl = conn.execute("SELECT symbol, grade, score FROM watchlist ORDER BY score DESC").fetchall()
conn.close()
with open(r"{RUNNER.with_suffix('.json').as_posix()}", "w") as f:
    json.dump({{"grades": grades, "watchlist": [(w[0], w[1], w[2]) for w in wl], "ts": latest_ts}}, f)
""")

    subprocess.run([sys.executable, str(RUNNER)], capture_output=True, timeout=120, env=env)
    out_file = RUNNER.with_suffix(".json")
    data = {}
    if out_file.exists():
        data = json.loads(out_file.read_text())
        out_file.unlink(missing_ok=True)
    RUNNER.unlink(missing_ok=True)
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7, help="Days to replay")
    parser.add_argument("--fast", action="store_true", help="Skip rate limiting")
    args = parser.parse_args()

    # Clear existing watchlist and coin_states_v2 for a fresh replay
    import sqlite3
    conn = sqlite3.connect(str(DB))
    conn.execute("DELETE FROM watchlist")
    conn.execute("DELETE FROM coin_states_v2")
    conn.commit()
    conn.close()

    # Start from N days ago, end at now
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=args.days)

    # Round to nearest 15-minute boundary
    start_minute = (start.minute // 15) * 15
    start_ts = int(start.replace(minute=start_minute, second=0, microsecond=0).timestamp())
    end_ts = int(now.replace(second=0, microsecond=0).timestamp())

    total_scans = (end_ts - start_ts) // 900  # 15 min intervals
    print(f"Replay: {args.days} days ({start.strftime('%Y-%m-%d')} → {now.strftime('%Y-%m-%d')})")
    print(f"Scans: ~{total_scans} (every 15 min)")
    print(f"Mode: {'fast' if args.fast else 'rate-limited'}")
    print(f"{'='*60}")

    scan_count = 0
    watch_events = []  # (ts, symbol, grade, action: enter/exit)
    prev_wl = set()
    current_ts = start_ts

    # Progress display
    last_report = 0

    while current_ts <= end_ts:
        data = run_scan_at(current_ts)
        scan_count += 1

        if data:
            grades = data.get("grades", {})
            wl = data.get("watchlist", [])
            wl_set = {w[0] for w in wl}

            # Track watchlist changes
            entered = wl_set - prev_wl
            exited = prev_wl - wl_set
            for sym in entered:
                watch_events.append((current_ts, sym, "enter"))
            for sym in exited:
                watch_events.append((current_ts, sym, "exit"))
            prev_wl = wl_set

            # Progress every 4 hours (16 scans)
            if scan_count - last_report >= 16:
                dt = datetime.fromtimestamp(current_ts, tz=timezone.utc)
                wl_size = len(wl)
                w = grades.get("WATCH", 0)
                b = grades.get("B", 0)
                a = grades.get("A", 0)
                print(f"  {dt.strftime('%m-%d %H:%M')} | scan={scan_count:5d} | "
                      f"A={a} B={b} W={w} | WL={wl_size}")
                last_report = scan_count

        current_ts += 900  # +15 min
        if not args.fast:
            time.sleep(0.5)  # rate limit

    # ── Final Report ──
    print(f"\n{'='*60}")
    print(f"REPLAY COMPLETE: {scan_count} scans over {args.days} days")
    print(f"{'='*60}")

    # Unique coins ever in watchlist
    unique_wl = set()
    wl_entry_times = {}  # symbol -> first entry ts
    wl_total_time = {}   # symbol -> total seconds in watchlist
    in_wl_since = {}     # symbol -> entry ts

    for ts, sym, action in watch_events:
        if action == "enter":
            unique_wl.add(sym)
            if sym not in wl_entry_times:
                wl_entry_times[sym] = ts
            in_wl_since[sym] = ts
        elif action == "exit":
            if sym in in_wl_since:
                duration = ts - in_wl_since[sym]
                wl_total_time[sym] = wl_total_time.get(sym, 0) + duration
                del in_wl_since[sym]

    print(f"\nUnique coins ever in watchlist: {len(unique_wl)}")
    print(f"Total watchlist enter events: {sum(1 for e in watch_events if e[2]=='enter')}")

    # Top coins by time in watchlist
    if wl_total_time:
        print(f"\nTop 10 by watchlist duration:")
        sorted_coins = sorted(wl_total_time.items(), key=lambda x: -x[1])[:10]
        for sym, secs in sorted_coins:
            hours = secs / 3600
            entries = sum(1 for e in watch_events if e[1] == sym and e[2] == "enter")
            print(f"  {sym:12s}  {hours:6.1f}h  entries={entries}")

    # Grade distribution across all scans (query DB)
    conn = sqlite3.connect(str(DB))
    print(f"\nOverall grade distribution:")
    for r in conn.execute(
        "SELECT grade, COUNT(*) FROM coin_states_v2 GROUP BY grade ORDER BY COUNT(*) DESC"
    ):
        print(f"  {r[0]:12s}: {r[1]:,}")
    conn.close()


if __name__ == "__main__":
    main()
