# -*- coding: utf-8 -*-
"""AI 识图腿回溯（遮蔽版主判据 + 40 点原样版估泄漏）。

判据与样本规则已在 RETRO_AI_LEG_PREREG_20261001.md 冻结，本脚本只执行，不得改判据后重跑。

对每个采样点 T（与 walkforward_prog.py 同网格）：
  1) 只取 ts + 1h <= T 的已收盘 1h K 线，用 make_chart.render 出图
     （masked：去日期/时间轴/根数，价格归一为指数；raw：与生产同法）
  2) 提示词：raw 与生产相同；masked 去掉 as_of，收盘写 100（指数）
  3) 复用 ask_vision.run_rounds 调用（候选顺序/温度/max_tokens/重试与生产一致）
  4) 兑现：T + 7 天那根 1h 收盘 vs T 时收盘

断点续跑：已成功解析的点跳过；失败的点下次重跑会再试（attempts 全部留痕）。
免费额度用完（整点全是 HTTP 429）即停，次日接着跑。

用法（bot 机，需要 OPENROUTER_API_KEY）：
    .venv/bin/python retro_ai.py --mode masked            # 主判据，全部准入点
    .venv/bin/python retro_ai.py --mode raw               # 40 点原样版
    .venv/bin/python retro_ai.py --mode masked --dry-run --limit 3   # 只出图+打印提示词，不调用
选项：--limit N 本次最多处理 N 个点；--sleep 秒（默认 4，免费档 20 次/分钟）
"""
import argparse
import base64
import bisect
import csv
import datetime
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)

import config as C          # noqa: E402
import make_chart as MC     # noqa: E402
import ask_ai               # noqa: E402
import ask_vision as V      # noqa: E402
import forward_log as FL    # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DAY, HOUR = 86_400_000, 3_600_000
START = "2017-09-01"        # 与 walkforward_prog.py 同网格（预注册 §1）
STEP_DAYS = 7
N_RAW = 40                  # 原样版等距抽点数（预注册 §2）
H1, H2 = C.FWD_PRIMARY_H, C.FWD_SECONDARY_H

RETRO_DIR = os.path.join(C.OUT_DIR, "retro_ai")
ATTEMPTS = os.path.join(C.OUT_DIR, "retro_ai_attempts.jsonl")
REPLIES = os.path.join(C.OUT_DIR, "retro_ai_replies.jsonl")
COLS = ["T", "ts", "close", "mode", "status", "model", "provider",
        "ai_dir_7", "ai_dir_30", "ai_p_up_7d", "actual7", "actual30",
        "attempts", "first_fail", "first_resp_ok", "elapsed_s", "prompt_version", "run_at"]

MASKED_HEAD = """这是 BTCUSDT 的图：上栏为日线（最近约 14 个月），下栏为 4H（最近约 2 个月）。
价格已归一为指数：最后一根已收盘 1h K 线的收盘 = 100；图上不显示日期。
注意：两栏最右侧那根 K 线（当天日线、当前 4H）尚未走完，不要当作完整 K 线解读。
"""


def out_csv(mode):
    return os.path.join(C.OUT_DIR, f"retro_ai_{mode}.csv")


def build_prompt(mode, as_of, close):
    if mode == "masked":
        return MASKED_HEAD + ask_ai.PROMPT_BODY.format(close="100（指数）")
    # 与生产相同：AI_PROMPT_STATE=0 时生产即 PROMPT_HEAD + PROMPT_BODY（不拼程序状态）
    return (ask_ai.PROMPT_HEAD + ask_ai.PROMPT_BODY).format(as_of=as_of, close=f"{close:,.0f}")


def load_rows(path):
    rows = []
    with open(path, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows.append((int(r["ts"]), float(r["open"]), float(r["high"]),
                         float(r["low"]), float(r["close"])))
    rows.sort(key=lambda x: x[0])
    return rows


def grid_points(rows):
    """预注册 §1：同 walkforward 网格；日线满窗；7 天兑现已存在。"""
    ts = [r[0] for r in rows]
    t = int(datetime.datetime(2017, 9, 1, 0, 30, tzinfo=datetime.UTC).timestamp() * 1000)
    first_ok = ts[0] + C.DAILY_DAYS * DAY
    pts = []
    while True:
        k = bisect.bisect_right(ts, t - HOUR)          # 在 T 已收盘的 bar 数
        if k == 0:
            t += STEP_DAYS * DAY
            continue
        base_ts = ts[k - 1]
        if base_ts + H1 * DAY > ts[-1]:
            break
        if t >= first_ok:
            pts.append((t, k))
        t += STEP_DAYS * DAY
    return pts


def realized(rows, ts, k, h):
    base_ts, base_c = rows[k - 1][0], rows[k - 1][4]
    j = bisect.bisect_left(ts, base_ts + h * DAY)
    if j >= len(rows):
        return ""
    return "多" if rows[j][4] > base_c else "空"


def raw_subset(pts):
    """预注册 §2：遮蔽网格上第 round(i·N/40) 个，i=0..39。"""
    n = len(pts)
    idx = sorted({min(n - 1, round(i * n / N_RAW)) for i in range(N_RAW)})
    return [pts[i] for i in idx]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("masked", "raw"), required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=4.0)
    args = ap.parse_args()

    os.makedirs(RETRO_DIR, exist_ok=True)
    snap = os.path.join(tempfile.mkdtemp(prefix="retro_"), "BTCUSDT_1h.csv")
    shutil.copy(C.CSV, snap)          # 快照，不与运行中的 cron 抢文件
    rows = load_rows(snap)
    ts = [r[0] for r in rows]

    pts = grid_points(rows)
    if args.mode == "raw":
        pts = raw_subset(pts)
    print(f"[{args.mode}] 准入点 {len(pts)}  "
          f"{datetime.datetime.fromtimestamp(pts[0][0] / 1000, datetime.UTC):%Y-%m-%d} → "
          f"{datetime.datetime.fromtimestamp(pts[-1][0] / 1000, datetime.UTC):%Y-%m-%d}")

    path = out_csv(args.mode)
    done = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig", newline="") as fh:
            done = {r["T"]: r for r in csv.DictReader(fh)}
    todo = [p for p in pts if done.get(_tstr(p[0]), {}).get("status") != "ok"]
    print(f"  已完成 {len(pts) - len(todo)}，待跑 {len(todo)}")
    if args.limit:
        todo = todo[:args.limit]

    key = ""
    if not args.dry_run:
        key, src = V.read_key()
        if not key:
            print("[中止] 没有 OpenRouter API key", file=sys.stderr)
            return 2
        print(f"  key 来源 {src}  候选 {V._models()}")
    op = V.opener()
    max_tokens = int(os.environ.get("VISION_MAX_TOKENS", "6000"))
    tries = max(1, int(os.environ.get("VISION_TRIES", "2")))
    V.RAW = os.path.join(RETRO_DIR, "raw_response.txt")
    net_streak = 0

    for n, (T, k) in enumerate(todo, 1):
        tstr = _tstr(T)
        sub = rows[:k]
        as_of = datetime.datetime.fromtimestamp(sub[-1][0] / 1000, datetime.UTC).strftime("%Y-%m-%d %H:%M")
        close = sub[-1][4]
        img = os.path.join(RETRO_DIR, f"{args.mode}_{tstr[:10]}.png")
        MC.render(sub, img, mask=(args.mode == "masked"))
        prompt = build_prompt(args.mode, as_of, close)
        if args.dry_run:
            print(f"\n[dry-run] {tstr}  close={close:,.0f}  图 {img}")
            print(prompt)
            continue

        V.IMG = img
        b64 = base64.b64encode(open(img, "rb").read()).decode()
        print(f"\n[{n}/{len(todo)}] {tstr}  close={close:,.0f}")
        winner, attempts = V.run_rounds(V._models(), tries, prompt, b64, key, op, max_tokens)
        rk = f"retro_{args.mode}_{tstr[:10]}"
        V.log_attempts(rk, args.mode, attempts, path=ATTEMPTS)
        first_fail, first_resp_ok = V.summarize(attempts)

        row = {c: "" for c in COLS}
        row.update({"T": tstr, "ts": sub[-1][0], "close": round(close, 2), "mode": args.mode,
                    "actual7": realized(rows, ts, k, H1), "actual30": realized(rows, ts, k, H2),
                    "attempts": len(attempts), "first_fail": first_fail,
                    "first_resp_ok": first_resp_ok, "prompt_version": ask_ai.PROMPT_VERSION,
                    "run_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")})
        if winner:
            model, d, reply, dt, _, _ = winner
            parsed = FL.parse_ai_dirs(reply)
            row.update({"status": "ok", "model": d.get("model"), "provider": d.get("provider"),
                        "ai_dir_7": parsed[H1], "ai_dir_30": parsed[H2],
                        "ai_p_up_7d": "" if parsed.get("p_up_7d") is None else parsed["p_up_7d"],
                        "elapsed_s": dt})
            with open(REPLIES, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"run_key": rk, "model": d.get("model"), "reply": reply},
                                    ensure_ascii=False) + "\n")
            print(f"  → 7d={row['ai_dir_7']}  30d={row['ai_dir_30']}  p={row['ai_p_up_7d']}"
                  f"  实际7d={row['actual7']}")
        else:
            row["status"] = "fail"
        done[tstr] = row
        _write(path, done)

        if not winner and attempts and all(a.get("http_status") == 429 for a in attempts):
            print("[停止] 整点全是 HTTP 429，免费额度大概率已用完。明天再跑，会从断点继续。",
                  file=sys.stderr)
            return 3
        # 整点全是网络层失败（没拿到任何 HTTP 状态：代理断/SSL EOF/超时）——与模型无关。
        # 连续 3 点即停，避免把剩余点一路刷成 fail（2026-10-02 代理节点抖动时出现过）
        net_down = bool(not winner and attempts
                        and all(a.get("http_status") is None for a in attempts))
        net_streak = net_streak + 1 if net_down else 0
        if net_streak >= 3:
            print("[停止] 连续 3 个点全是网络层失败，检查代理后重跑，会从断点继续。", file=sys.stderr)
            return 4
        time.sleep(args.sleep)

    if not args.dry_run:
        ok = sum(1 for r in done.values() if r["status"] == "ok")
        print(f"\n写出 {path}  成功 {ok}/{len(pts)}")
    return 0


def _tstr(T):
    return datetime.datetime.fromtimestamp(T / 1000, datetime.UTC).strftime("%Y-%m-%d %H:%M")


def _write(path, done):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=COLS, restval="", extrasaction="ignore")
        wr.writeheader()
        wr.writerows(sorted(done.values(), key=lambda r: r["T"]))
    os.replace(tmp, path)


if __name__ == "__main__":
    sys.exit(main())
