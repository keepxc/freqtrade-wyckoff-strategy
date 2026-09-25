#!/usr/bin/env python3
"""
采集守护 —— 断采告警
====================

**为什么有这个脚本**（2026-09-18 立）：本项目已记录**三次静默断采**，每次都是事后翻数据
才发现，最长 10 天无人知晓——

  · `aggTrades` 09-06 17:52 断，9/13 才发现（7 天）
  · `netflow_oi` / `btc_mtf` / `netflow_compact` 09-04 23:1x 三路同断，9/14 才发现（10 天）
  · `user_data/collect/` 的 4 条 symlink 少一层路径而断链，9/9 建、9/14 才发现（5 天）

教训（`DRYRUN_STATUS_20260911.md` §一）：**cron 健康不能只看 last_status/exit 0，
必须核对产物时间戳与内容增长。** 本脚本就是把这句话变成每小时自动执行的检查。

**判据用「体积增长」而不是 mtime**：mtime 会被 touch/权限操作刷新，但采集器真的死了
文件体积就不再长。故每次记录体积，只有体积**变大**才刷新 `last_growth`；
告警看的是 `now - last_growth`。首次运行用 mtime 播种。

**同时覆盖策略自身产物**：V3 的 score/zombie/netflow_multiwin 由策略循环写，
它们停更 = 策略停了。上面三次事故里没有一次是策略停，但那只是运气。

只读（除状态文件与告警文件外不写）。设计为每小时 cron 跑一次：
    5 * * * * cd ~/freqtrade && python3 scripts/collect_watchdog.py >> logs/watchdog.log 2>&1
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(REPO, "user_data", ".watchdog_state.json")
ALERT = os.path.join(REPO, "user_data", "WATCHDOG_ALERT.txt")

# 告警冷却：同一条路子持续断采时，每隔这么久再推一次，避免每小时刷屏
REALERT_H = 12

# ---------------------------------------------------------------------------
# 监控目标。stale_h = 多久没长新内容就算断采。
#
# 阈值取「正常节奏 × 2~3」，不要贴着正常节奏设——采集器有正常抖动
# （vergex 间歇失败属已知常态），贴太紧会把噪声报成故障，报多了就没人看了。
#
# ⚠️ 只列**应当在采**的。已停采的（agg_trades / netflow_oi / btc_mtf /
#    macd_m8 / netflow_compact）**故意不列**——列了就会永久告警，
#    而"永远在响的告警"等于没有告警。将来恢复采集时再加回来。
# ---------------------------------------------------------------------------
TARGETS = [
    # key                路径(glob)                                    节奏      阈值h
    # 2026-09-25：V3 主动终止 ⇒ score/zombie/netflow 由 V3 策略循环写出改为本机常驻采集器
    # （ai500-local-collector.service）写出，路径随之改到 user_data/ai500_local/。
    # zombie 检查是 V3 专有产物（僵尸行过滤），随 V3 一起退役，不再监控（列了就是永久告警）。
    ("score_ai500",      "user_data/ai500_local/score_*.csv",           "常驻 每5min",   2),
    ("netflow_ai500",    "user_data/ai500_local/netflow_multiwin_*.csv","常驻 每60s",    2),
    ("b300_snapshots",   "user_data/collect/b300_snapshots.jsonl",    "cron 每4h",      7),
    ("b300_signals",     "user_data/collect/b300_signals.jsonl",      "cron 每4h",      7),
    ("b300_breakout",    "user_data/collect/b300_breakout.jsonl",     "cron 每日00:20", 30),
    ("oi_hist",          "user_data/ai500_oi_hist/oi_*.csv.gz",       "cron 每6h",      10),
]

COLLECT_DIR = os.path.join(REPO, "user_data", "collect")


def probe(pattern):
    """返回 (总体积, 最新mtime, 文件数, 断链数)。找不到文件返回 (None, ...)。"""
    files = glob.glob(os.path.join(REPO, pattern))
    if not files:
        return None, None, 0, 0
    size = 0
    newest = 0.0
    broken = 0
    for fp in files:
        try:
            st = os.stat(fp)          # 跟随 symlink；断链会抛 OSError
        except OSError:
            broken += 1
            continue
        size += st.st_size
        newest = max(newest, st.st_mtime)
    if broken and size == 0:
        return None, None, len(files), broken
    return size, newest, len(files), broken


def broken_links():
    """user_data/collect/ 里的断链 symlink（9/14 那次事故的直接检查）。"""
    out = []
    if not os.path.isdir(COLLECT_DIR):
        return out
    for name in os.listdir(COLLECT_DIR):
        p = os.path.join(COLLECT_DIR, name)
        if os.path.islink(p) and not os.path.exists(p):
            out.append((name, os.readlink(p)))
    return out


def send_telegram(cfg_path, text):
    """复用 freqtrade config 里已有的 telegram 凭据（那些 config 已 gitignore）。

    不接受命令行/环境变量传 token——多一个存放点就多一个泄露点。
    """
    try:
        with open(cfg_path, encoding="utf-8") as f:
            tg = json.load(f).get("telegram", {})
    except OSError as e:
        print(f"  [tg] 读不到配置 {cfg_path}: {e}")
        return False
    if not (tg.get("token") and tg.get("chat_id")):
        print("  [tg] 配置里没有 token/chat_id，跳过推送")
        return False
    url = f"https://api.telegram.org/bot{tg['token']}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": tg["chat_id"], "text": text}).encode()
    try:
        # urlopen 走 HTTPS_PROXY 环境变量；bot 上 telegram 必须过代理
        with urllib.request.urlopen(url, data=data, timeout=20) as r:
            ok = r.status == 200
        print(f"  [tg] 推送{'成功' if ok else '失败'}")
        return ok
    except Exception as e:                      # 网络抖动不应让守护进程崩掉
        print(f"  [tg] 推送异常: {e.__class__.__name__} {e}")
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--telegram-config",
                    default=os.path.join(REPO, "user_data", "config_ai500_v3_dryrun.json"))
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="只检查不写状态、不推送")
    args = ap.parse_args()

    now = time.time()
    nowdt = datetime.now(timezone.utc).astimezone()
    try:
        with open(STATE, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}

    print(f"=== 采集守护 {nowdt:%Y-%m-%d %H:%M:%S %z} ===")
    # red   = 当前确实处于故障的路子（决定 ALERT 文件与退出码）
    # push  = red 里这一轮该推送的（冷却期内的不推）
    # 两者必须分开：冷却的语义是「不重复打扰」，不是「故障已解除」。
    red, push, lines = [], [], []

    for key, pattern, cadence, stale_h in TARGETS:
        size, mtime, nfiles, nbroken = probe(pattern)
        st = state.get(key, {})

        if size is None:
            msg = f"{key}: ❌ 产物不存在" + (f"（{nbroken} 条断链 symlink）" if nbroken else "")
            red.append(msg)
            push.append(msg)
            lines.append(f"  {key:<18} ❌ 缺失      {pattern}")
            continue

        # 体积变大才算「有新内容」；首次运行用 mtime 播种
        last_growth = st.get("last_growth")
        if last_growth is None:
            last_growth = mtime
        elif size > st.get("size", -1):
            last_growth = now

        stale = (now - last_growth) / 3600.0
        if not args.dry_run:
            state[key] = {"size": size, "last_growth": last_growth}

        flag = "🟢" if stale < stale_h else "🔴"
        lines.append(f"  {key:<18} {flag} 停更 {stale:6.2f}h / 阈值 {stale_h:>3}h  "
                     f"{nfiles} 文件 {size/1e6:8.2f}MB  [{cadence}]")
        if stale >= stale_h:
            msg = (f"{key}: 🔴 已 {stale:.1f}h 无新内容"
                   f"（阈值 {stale_h}h，节奏 {cadence}）")
            red.append(msg)
            last_alert = st.get("last_alert", 0)
            if (now - last_alert) / 3600.0 >= REALERT_H:
                push.append(msg)
                if not args.dry_run:
                    state[key]["last_alert"] = now
            else:
                if not args.dry_run:
                    state[key]["last_alert"] = last_alert
                lines[-1] += "  (推送冷却中，故障仍在)"

    print("\n".join(lines))

    bl = broken_links()
    if bl:
        msg = "⚠️ 断链 symlink: " + ", ".join(f"{n} -> {t}" for n, t in bl)
        print(f"  {msg}")
        red.append(msg)
        push.append(msg)

    if not red:
        print("\n✅ 全部正常")
        if os.path.exists(ALERT) and not args.dry_run:
            os.remove(ALERT)            # 只有真的全好了才清告警文件
    else:
        body = f"🔴 采集断采告警 {nowdt:%Y-%m-%d %H:%M}\n" + "\n".join(red)
        print("\n" + body)
        if not args.dry_run:
            with open(ALERT, "w", encoding="utf-8") as f:
                f.write(body + "\n")
            if push and not args.no_telegram:
                send_telegram(args.telegram_config, body)
        if not push:
            print("（本轮全部处于推送冷却期，未重复推送；ALERT 文件保留）")

    if not args.dry_run:
        with open(STATE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)

    return 1 if red else 0


if __name__ == "__main__":
    sys.exit(main())
