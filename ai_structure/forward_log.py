# -*- coding: utf-8 -*-
"""前向观察：每个槽位跑完后，把「程序方向 / AI 方向」记一行到 out/forward_log.csv。

判据与口径见 FORWARD_PROTOCOL.md（2026-09-27 冻结）。本脚本只**记录**，不打分；
打分（回填实际涨跌）是 score.py 的事——两者分开，是为了让记录这一步
在任何时候都拿不到未来价格。

程序方向（冻结口径 a）
------------------------
只认判定码「方向确认 / 有增量」——即 swing_watch.verdict 返回的
"edge"（四层全过）/ "weak"（有增量、样本外不一致）/ "dir"（CI 排除 50%、无增量）。
这三档共同点是 95%CI 排除 50%；"none" 与样本不足一律「中性」。
「当前状态所属」按 swing_watch.analyse 里各掩码的定义取：
  move / days / up_all 三组只在 phase == up 时才包含当前 bar；
  breakout 组只在 breakout == True 时包含当前 bar。
下行腿没有对应的条件组，程序方向恒为「中性」——这是程序能力边界，不是 bug。
多个适用组方向冲突 → 中性。

用法（由 run_session.sh 调用）：
    python forward_log.py --slot asia --sw-rc 0 --chart-rc 0 --ai-rc 0
"""
import argparse
import csv
import datetime
import json
import os
import re
import sys

import config as C

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import swing_watch as S     # noqa: E402

RESULT = os.path.join(C.OUT_DIR, "watch_latest.json")
LOG = os.path.join(C.OUT_DIR, "forward_log.csv")
H_MS = 3_600_000
SLOTS = ("asia", "euus")          # 只有排班槽位进前向样本；manual 不记
DIRS = ("多", "空", "中性")
PROG_CODES = ("edge", "weak", "dir")   # 冻结口径 a
FRESH_H = 2.0                     # 最后一根已收盘 bar 距现在 ≤ 2h 才算数据新鲜

COLS = ["run_key", "slot", "run_at", "as_of", "as_of_ts", "close",
        "phase", "breakout", "data_age_h", "data_fresh",
        "sw_rc", "chart_rc", "ai_rc", "ai_status", "ai_file", "ai_parse_ok",
        f"prog_dir_{C.FWD_PRIMARY_H}", f"prog_dir_{C.FWD_SECONDARY_H}",
        f"prog_codes_{C.FWD_PRIMARY_H}", f"prog_codes_{C.FWD_SECONDARY_H}",
        f"ai_dir_{C.FWD_PRIMARY_H}", f"ai_dir_{C.FWD_SECONDARY_H}",
        "ai_p_up_7d", "ai_p_consistent", "prompt_version",
        "pipeline_ok"]


def applicable_states(pos):
    keys = []
    if pos.get("phase") == "up":
        keys += ["move", "days", "up_all"]
    if pos.get("breakout"):
        keys.append("breakout")
    return keys


def prog_direction(w, h):
    """返回 (方向, 审计串)。"""
    hk = str(h)
    base = w.get("base", {}).get(hk)
    cond = w.get("conditional", {}).get(hk, {})
    if not base or not base.get("usable"):
        return "中性", "base:unusable"
    n_tests = 4 * len(S.HORIZONS)       # 与 swing_watch 的 Bonferroni 口径一致
    sides, audit = set(), []
    for key in applicable_states(w.get("position", {})):
        s = cond.get(key)
        if not s:
            audit.append(f"{key}:missing")
            continue
        _, code, _, _ = S.verdict(s, base, h, ab=s.get("ab"), n_tests=n_tests)
        audit.append(f"{key}:{code}")
        if code not in PROG_CODES:
            continue
        # 三档都要求 95%CI 排除 50%，方向取 CI 所在一侧
        if s["p_up_lo"] > 0.5:
            sides.add("多")
        elif s["p_up_hi"] < 0.5:
            sides.add("空")
    d = sides.pop() if len(sides) == 1 else "中性"
    return d, "|".join(audit) or "none_applicable"


DIR_RE = re.compile(r"DIRECTION_JSON\s*[:：]\s*(\{.*?\})", re.S)


def parse_ai_dirs(reply):
    """从 AI 回答里抽 DIRECTION_JSON。取**最后一个**匹配（提示词要求放最后一行）。"""
    if not reply:
        return None
    m = DIR_RE.findall(reply)
    if not m:
        return None
    raw = m[-1].replace("“", '"').replace("”", '"')
    try:
        d = json.loads(raw)
    except json.JSONDecodeError:
        return None
    out = {}
    for h in (C.FWD_PRIMARY_H, C.FWD_SECONDARY_H):
        v = d.get(f"dir_{h}d")
        if v not in DIRS:
            return None
        out[h] = v
    # p_up_7d 是辅助字段（v2 起）：缺失/非法只置空，不影响方向解析——
    # 解析成功率是冻结的检查点判据，只按方向字段算
    p = d.get("p_up_7d")
    if isinstance(p, str) and p.strip().isdigit():
        p = int(p.strip())
    out["p_up_7d"] = (int(p) if isinstance(p, (int, float)) and not isinstance(p, bool)
                      and 0 <= p <= 100 else None)
    return out


def p_consistent(d7, p):
    """p_up_7d 与 dir_7d 是否符合提示词里的定义（>55 多 / <45 空 / 45~55 中性）。"""
    if p is None:
        return ""
    want = "多" if p > 55 else ("空" if p < 45 else "中性")
    return int(want == d7)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", required=True)
    ap.add_argument("--sw-rc", type=int, default=0)
    ap.add_argument("--chart-rc", type=int, default=0)
    ap.add_argument("--ai-rc", type=int, default=0)
    args = ap.parse_args()

    if args.slot not in SLOTS:
        print(f"[前向] 槽位 {args.slot} 不在前向样本内（只记 {SLOTS}），跳过")
        return 0

    now = datetime.datetime.now(datetime.UTC)
    today = now.strftime("%Y%m%d")
    row = {k: "" for k in COLS}
    row.update(run_key=f"{today}_{args.slot}", slot=args.slot,
               run_at=now.strftime("%Y-%m-%d %H:%M"),
               sw_rc=args.sw_rc, chart_rc=args.chart_rc, ai_rc=args.ai_rc)

    # ---- 程序侧
    w = None
    if os.path.exists(RESULT):
        try:
            w = json.load(open(RESULT, encoding="utf-8"))
        except json.JSONDecodeError:
            w = None
    if w:
        pos = w.get("position", {})
        as_of_dt = datetime.datetime.strptime(w["as_of"], "%Y-%m-%d %H:%M") \
            .replace(tzinfo=datetime.UTC)
        as_of_ts = int(as_of_dt.timestamp() * 1000)
        # as_of 是最后一根 bar 的开盘时间；它的收盘时间 = as_of + 1h
        age = (now.timestamp() * 1000 - (as_of_ts + H_MS)) / H_MS
        row.update(as_of=w["as_of"], as_of_ts=as_of_ts,
                   close=w.get("last_close"), phase=pos.get("phase"),
                   breakout=int(bool(pos.get("breakout"))),
                   data_age_h=round(age, 2), data_fresh=int(age <= FRESH_H))
        for h in (C.FWD_PRIMARY_H, C.FWD_SECONDARY_H):
            d, audit = prog_direction(w, h)
            row[f"prog_dir_{h}"] = d
            row[f"prog_codes_{h}"] = audit
    else:
        row["data_fresh"] = 0

    # ---- AI 侧
    status = {0: "ok", 1: "fail", 2: "precond", 3: "quota"}.get(args.ai_rc,
                                                              f"rc{args.ai_rc}")
    ai_file = os.path.join(C.OUT_DIR, f"ai_{today}_{args.slot}.json")
    parsed = None
    if os.path.exists(ai_file):
        row["ai_file"] = os.path.basename(ai_file)
        try:
            a = json.load(open(ai_file, encoding="utf-8"))
            # AI 看到的图必须是本轮这份数据画的，否则方向与程序不可比
            if w and w["as_of"] not in a.get("prompt", ""):
                status = "stale_ai"
            else:
                parsed = parse_ai_dirs((a.get("response") or {}).get("reply"))
                row["prompt_version"] = a.get("prompt_version", "")
        except (json.JSONDecodeError, OSError):
            status = "bad_file"
    elif status == "ok":
        status = "no_file"
    if not row["prompt_version"]:
        # AI 没产出文件的轮次也要带版本号，否则管线失败会从 v2 的分母里消失
        import ask_ai
        row["prompt_version"] = ask_ai.PROMPT_VERSION
    row["ai_status"] = status
    row["ai_parse_ok"] = int(parsed is not None)
    for h in (C.FWD_PRIMARY_H, C.FWD_SECONDARY_H):
        row[f"ai_dir_{h}"] = parsed[h] if parsed else ""
    if parsed:
        p = parsed.get("p_up_7d")
        row["ai_p_up_7d"] = "" if p is None else p
        row["ai_p_consistent"] = p_consistent(parsed[C.FWD_PRIMARY_H], p)

    row["pipeline_ok"] = int(bool(w) and row["data_fresh"] == 1
                             and args.sw_rc == 0 and args.chart_rc == 0
                             and status == "ok")

    # ---- 落盘：同 run_key 覆盖，其余追加
    rows = []
    if os.path.exists(LOG):
        with open(LOG, encoding="utf-8-sig", newline="") as fh:
            rows = [r for r in csv.DictReader(fh)
                    if r.get("run_key") != row["run_key"]]
    rows.append({k: str(v) for k, v in row.items()})
    rows.sort(key=lambda r: r["run_key"])
    tmp = LOG + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=COLS, restval="",
                            extrasaction="ignore")
        wr.writeheader()
        wr.writerows(rows)
    os.replace(tmp, LOG)

    h1, h2 = C.FWD_PRIMARY_H, C.FWD_SECONDARY_H
    print(f"[前向] {row['run_key']}  as_of={row['as_of']}  close={row['close']}"
          f"  程序 {h1}d={row[f'prog_dir_{h1}']} {h2}d={row[f'prog_dir_{h2}']}"
          f"  AI {h1}d={row[f'ai_dir_{h1}'] or '—'} {h2}d={row[f'ai_dir_{h2}'] or '—'}"
          f"  ai={status}  pipeline_ok={row['pipeline_ok']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
