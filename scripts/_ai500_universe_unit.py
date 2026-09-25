#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI500 宇宙导出器夹具（池子规则 + 安全条款）。

守这些不变式（每条都对应 §6.16 里的一条承诺）：
  A. 池子规则：{score≥buffer} ∪ {window_days 内出现}，带后缀、去重
  B. 窗口边界正确（10 天前的标的在 7d 排除、15d 包含）
  C. 终值与 buffer 边界：score 正好等于 70 应入池；69.99 不入
  D. 安全条款①：历史缺失/损坏/空 → **不覆盖原 JSON**，退出码 2
  E. 安全条款②：正常写入是**原子**的（不留 .tmp），JSON 结构 = {pairs, refresh_period}
  F. 真跑（subprocess，带 env 覆盖）—— 不是 mock，是真实行为

运行：.venv/bin/python scripts/_ai500_universe_unit.py   （全通过 → 退出码 0）
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "scripts", "ai500_universe_export.py")
PY = os.path.join(REPO, ".venv", "bin", "python")
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"   [{detail}]" if detail else ""))


spec = importlib.util.spec_from_file_location("uni", SCRIPT)
uni = importlib.util.module_from_spec(spec)
spec.loader.exec_module(uni)

NOW = datetime.now(timezone.utc)
HDR = "fetched_at_utc,symbol,pair,score,signal_peak,price,start_price,start_time,change_pct_value"


def mkhist(rows):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "hist.csv")
    with open(p, "w", encoding="utf-8") as f:
        f.write(HDR + "\n")
        for sym, score, when in rows:
            f.write(f"{when},{sym},{sym}/USDT:USDT,{score},,,,, \n".replace(", \n", "\n"))
    return p


def run(hist, outdir):
    env = dict(os.environ, AI500_DATA_DIR=outdir, AI500_SCORE_HISTORY=hist)
    r = subprocess.run([PY, SCRIPT], env=env, capture_output=True, text=True, timeout=120)
    return r.returncode, r.stdout + r.stderr


print("=" * 78)
print("A/B/C. 池子规则")
print("=" * 78)
h = mkhist([
    ("BR", 92, NOW.isoformat()),                          # 高分区 + 窗口内
    ("OLDHIGH", 85, (NOW - timedelta(days=30)).isoformat()),  # 高分区（窗口外）→ 保留
    ("RECENT", 60, NOW.isoformat()),                      # 仅窗口内 → 保留
    ("STALE", 60, (NOW - timedelta(days=30)).isoformat()),    # 都不满足 → 剔除
    ("EDGE70", 70, NOW.isoformat()),                      # 正好等于门槛 → 保留
    ("EDGE6999", 69.99, (NOW - timedelta(days=30)).isoformat()),  # 差 0.01 → 剔除
    ("MID", 60, (NOW - timedelta(days=10)).isoformat()),  # 10 天前 → 7d 排除、15d 包含
])
pairs, st = uni.build_universe(h, 70, 7)
got = {p.split("/")[0] for p in pairs}
check("A1 高分区入选（含窗口外）", {"OLDHIGH", "BR"} <= got, f"{sorted(got)}")
check("A2 仅窗口内入选", "RECENT" in got)
check("A3 双条件不满足被剔除", "STALE" not in got)
check("A4 全体带 /USDT:USDT 后缀", all(p.endswith("/USDT:USDT") for p in pairs))
check("A5 去重", len(pairs) == len(set(pairs)))
check("B1 窗口边界：10 天前在 7d 被排除", "MID" not in got, f"{sorted(got)}")
pairs15, _ = uni.build_universe(h, 70, 15)
check("B2 放宽到 15d → MID 入池", "MID" in {p.split("/")[0] for p in pairs15})
check("C1 score 正好 70 入池", "EDGE70" in got)
check("C2 score 69.99 不入池", "EDGE6999" not in got)
check("C3 stats 计数自洽",
      st["peak"] == 3 and st["window"] == 3 and st["universe"] == len(pairs) == 4, f"{st}")

print()
print("=" * 78)
print("D. 安全条款①：空池绝不覆盖原文件")
print("=" * 78)
d = tempfile.mkdtemp()
out = os.path.join(d, "ai500_universe.json")
sentinel = {"pairs": ["SENTINEL/USDT:USDT"], "refresh_period": 999}
with open(out, "w") as f:
    json.dump(sentinel, f)
for label, hist in (("历史文件缺失", os.path.join(d, "nope.csv")),
                    ("历史为空", mkhist([]))):
    rc, log = run(hist, d)
    after = json.load(open(out))
    check(f"D1({label}) 退出码 2", rc == 2, f"rc={rc}")
    check(f"D2({label}) 原文件未被覆盖", after == sentinel, f"{after}")
bad = os.path.join(d, "bad.csv")
open(bad, "w").write("fetched_at_utc,symbol,score\nnot-a-date,,abc\n")
rc, log = run(bad, d)
check("D3 脏 CSV → 退出码 2 且不覆盖", rc == 2 and json.load(open(out)) == sentinel, f"rc={rc}")

print()
print("=" * 78)
print("D2. 在途仓并集（出场路径依赖框架数据，绝不能轮出池子）")
print("=" * 78)
import sqlite3
dbs = os.path.join(d, "t.sqlite")
con = sqlite3.connect(dbs)
con.execute("CREATE TABLE trades (pair TEXT, is_open INTEGER)")
con.executemany("INSERT INTO trades VALUES (?,?)",
                [("HELD/USDT:USDT", 1), ("CLOSED/USDT:USDT", 0)])
con.commit(); con.close()
env = dict(os.environ, AI500_DATA_DIR=d, AI500_SCORE_HISTORY=h, AI500_TRADES_DB=dbs)
r = subprocess.run([PY, SCRIPT], env=env, capture_output=True, text=True, timeout=120)
pl = json.load(open(out))["pairs"]
check("D2a 在途仓被并入池子", "HELD/USDT:USDT" in pl, f"{pl[-3:]}")
check("D2b 已平仓不入池", "CLOSED/USDT:USDT" not in pl)
check("D2c 退出码 0", r.returncode == 0, f"rc={r.returncode}")
env_nodb = dict(env, AI500_TRADES_DB=os.path.join(d, "nope.sqlite"))
r = subprocess.run([PY, SCRIPT], env=env_nodb, capture_output=True, text=True, timeout=120)
check("D2d DB 缺失 → 不报错（跳过并集）", r.returncode == 0, f"rc={r.returncode}")

print()
print("=" * 78)
print("G. 权威 AI500 名单（NOFX /api/ai500/list，score>70）—— 无 key 时必须零影响")
print("=" * 78)
check("G1 无 key → ([], 'no_key')", uni.fetch_ai500_membership("") == ([], "no_key"))
check("G2 无 key 不联网（注入的 fetch 不会被调用）",
      uni.fetch_ai500_membership("", _fetch=lambda u: (_ for _ in ()).throw(AssertionError()))[0] == [])

def mk_payload():
    return json.dumps({"success": True, "data": {"coins": [
        {"symbol": "BRUSDT", "score": 92.5},
        {"symbol": "ZEC/USDT", "score": 71},
        {"symbol": "WEAKUSDT", "score": 69.9},
        {"symbol": "NOSCORE", "score": None},
    ]}}).encode()

ms, note = uni.fetch_ai500_membership("k", _fetch=lambda u: mk_payload())
check("G3 解析出 score>70 的 symbol（多种形态）",
      "BR" in ms and "ZEC" in ms, f"{ms} / {note}")
check("G4 score<=70 被剔除", "WEAK" not in ms)
check("G5 无 score 字段的保守保留", "NOSCORE" in ms)
check("G6 去重且排序", ms == sorted(set(ms)))
check("G7 note 带形态诊断", note.startswith("ok("), note)

ms2, note2 = uni.fetch_ai500_membership("k", _fetch=lambda u: (_ for _ in ()).throw(TimeoutError()))
check("G8 网络异常 → 空表 + fetch_error（不抛）", ms2 == [] and note2.startswith("fetch_error"), note2)
ms3, note3 = uni.fetch_ai500_membership("k", _fetch=lambda u: b'{"success":true,"data":[]}')
check("G9 空载荷 → empty_payload", ms3 == [] and note3 == "empty_payload", note3)
ms4, note4 = uni.fetch_ai500_membership("k", _fetch=lambda u: b"not-json")
check("G10 非 JSON → fetch_error（不抛）", ms4 == [] and note4.startswith("fetch_error"), note4)

# 真跑：无 key 时产物必须与启发式一致（多出的键只进 manifest）
rc, log = run(h, d)
pl = set(json.load(open(out))["pairs"])
mf = json.load(open(os.path.join(d, "ai500_universe_manifest.json")))
base = set(uni.build_universe(h, 70, 7)[0])
check("G11 无 key 真跑：池子 == 启发式结果", pl == base, f"diff={pl ^ base}")
check("G12 manifest 记录来源标记", mf.get("ai500_membership_source") == "no_key", str(mf.get("ai500_membership_source")))
check("G13 退出码 0", rc == 0, f"rc={rc}")

print()
print("=" * 78)
print("E/F. 安全条款②：正常写入（真跑 subprocess）")
print("=" * 78)
rc, log = run(h, d)
payload = json.load(open(out))
check("E1 退出码 0", rc == 0, f"rc={rc}")
check("E2 JSON 结构 = {pairs, refresh_period}", set(payload) == {"pairs", "refresh_period"},
      f"{list(payload)}")
check("E3 refresh_period 正确", payload["refresh_period"] == uni.REFRESH_PERIOD)
check("E4 内容与 build_universe 一致",
      set(payload["pairs"]) == set(uni.build_universe(h, 70, 7)[0]))
check("E5 原子写：不留 .tmp", not os.path.exists(out + ".tmp"))
check("E6 写了 manifest（含 pairs 计数）",
      json.load(open(os.path.join(d, "ai500_universe_manifest.json")))["pairs"] == len(payload["pairs"]))
check("F1 日志里有对账行（stderr/stdout 含 pairs）", "池子" in log or "universe" in log)

print()
print("=" * 78)
print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
if FAIL:
    print("失败项: " + ", ".join(FAIL))
print("=" * 78)
sys.exit(1 if FAIL else 0)
