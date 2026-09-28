# -*- coding: utf-8 -*-
"""密集观测层：每 4h 记录一次（程序方向 / AI 方向），写入 out/dense_log.csv。

⚠️ 与前向样本（out/forward_log.csv，只有 asia / euus 两槽）**完全隔离**：
   不参与 n、不参与检查点分母与门槛、不影响 FORWARD_PROTOCOL.md 的任何判据。
   它的用途是「关于测量本身的证据」：
     ① 程序方向非中性的时间占比（相位覆盖度 → 用于判断采样时刻选得对不对）
     ② AI 方向在一天内的翻转次数（测量稳定性 → 用于判断「AI 方向」是否定义得不稳）
     ③ 各 model / provider 的方向分歧（换源后的可靠性）
   三个月后再看这张表，才谈要不要动判据。

用法（由 run_dense.sh 调用）：
    python dense_log.py --slot dense_00 --sw-rc 0 --chart-rc 0 --ai-rc 0
"""
import argparse
import csv
import datetime
import json
import os
import sys

import config as C

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import forward_log as FL      # 复用 prog_direction / parse_ai_dirs 口径，保证与样本层同源（noqa: E402）

RESULT = os.path.join(C.OUT_DIR, "watch_latest.json")
LOG = os.path.join(C.OUT_DIR, "dense_log.csv")

COLS = ["run_key", "slot", "run_at", "as_of", "as_of_ts", "close", "phase", "breakout",
        "data_age_h", "data_fresh", "sw_rc", "chart_rc", "ai_rc", "ai_status",
        "ai_parse_ok", "prog_dir_7", "prog_codes_7", "ai_dir_7", "ai_dir_30",
        "ai_p_up_7d", "ai_p_consistent", "prompt_version", "finish_reason",
        "ai_first_parse_ok", "ai_attempts",
        "model", "provider", "cost", "elapsed_s", "dense_ok"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", required=True)
    ap.add_argument("--sw-rc", type=int, default=0)
    ap.add_argument("--chart-rc", type=int, default=0)
    ap.add_argument("--ai-rc", type=int, default=0)
    args = ap.parse_args()

    now = datetime.datetime.now(datetime.UTC)
    today = now.strftime("%Y%m%d")
    row = {k: "" for k in COLS}
    row.update(run_key=f"{today}_{args.slot}", slot=args.slot,
               run_at=now.strftime("%Y-%m-%d %H:%M"),
               sw_rc=args.sw_rc, chart_rc=args.chart_rc, ai_rc=args.ai_rc)

    w = None
    if os.path.exists(RESULT):
        try:
            w = json.load(open(RESULT, encoding="utf-8"))
        except json.JSONDecodeError:
            w = None
    if w:
        pos = w.get("position", {})
        as_of_dt = datetime.datetime.strptime(w["as_of"], "%Y-%m-%d %H:%M").replace(tzinfo=datetime.UTC)
        as_of_ts = int(as_of_dt.timestamp() * 1000)
        age = (now.timestamp() * 1000 - (as_of_ts + FL.H_MS)) / FL.H_MS
        row.update(as_of=w["as_of"], as_of_ts=as_of_ts, close=w.get("last_close"),
                   phase=pos.get("phase"), breakout=int(bool(pos.get("breakout"))),
                   data_age_h=round(age, 2), data_fresh=int(age <= FL.FRESH_H))
        d, audit = FL.prog_direction(w, C.FWD_PRIMARY_H)
        row["prog_dir_7"] = d
        row["prog_codes_7"] = audit
    else:
        row["data_fresh"] = 0

    status = {0: "ok", 1: "fail", 2: "precond", 3: "quota"}.get(args.ai_rc, f"rc{args.ai_rc}")
    ai_file = os.path.join(C.OUT_DIR, f"ai_{today}_{args.slot}.json")
    parsed = None
    if os.path.exists(ai_file):
        try:
            a = json.load(open(ai_file, encoding="utf-8"))
            if w and w["as_of"] not in a.get("prompt", ""):
                status = "stale_ai"
            else:
                parsed = FL.parse_ai_dirs((a.get("response") or {}).get("reply"))
                row["model"] = a.get("model", "")
                row["provider"] = a.get("provider", "")
                row["cost"] = (a.get("usage") or {}).get("cost", "")
                row["elapsed_s"] = a.get("elapsed_s", "")
                row["prompt_version"] = a.get("prompt_version", "")
                row["finish_reason"] = a.get("finish_reason", "")
                row["ai_first_parse_ok"] = a.get("first_parse_ok", "")
                row["ai_attempts"] = a.get("attempt_count", "")
        except (json.JSONDecodeError, OSError):
            status = "bad_file"
    elif status == "ok":
        status = "no_file"

    row["ai_status"] = status
    row["ai_parse_ok"] = int(parsed is not None)
    if parsed:
        row["ai_dir_7"] = parsed.get(C.FWD_PRIMARY_H, "")
        row["ai_dir_30"] = parsed.get(C.FWD_SECONDARY_H, "")
        p = parsed.get("p_up_7d")
        row["ai_p_up_7d"] = "" if p is None else p
        row["ai_p_consistent"] = FL.p_consistent(parsed[C.FWD_PRIMARY_H], p)
    row["dense_ok"] = int(bool(w) and row["data_fresh"] == 1 and status == "ok"
                          and parsed is not None)

    rows = []
    if os.path.exists(LOG):
        with open(LOG, encoding="utf-8-sig", newline="") as fh:
            rows = [r for r in csv.DictReader(fh) if r.get("run_key") != row["run_key"]]
    rows.append({k: str(v) for k, v in row.items()})
    rows.sort(key=lambda r: r["run_key"])
    tmp = LOG + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=COLS, restval="", extrasaction="ignore")
        wr.writeheader()
        wr.writerows(rows)
    os.replace(tmp, LOG)

    print(f"[密集] {row['run_key']}  as_of={row['as_of']}  phase={row['phase']}"
          f"  程序7d={row['prog_dir_7']}  AI7d={row['ai_dir_7'] or '—'}"
          f"  ai={status}  模型={row['model'] or '—'}  provider={row['provider'] or '—'}"
          f"  dense_ok={row['dense_ok']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
