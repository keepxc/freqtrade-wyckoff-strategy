# -*- coding: utf-8 -*-
"""前向观察模块自检：forward_log.py / score.py / 已收盘过滤。

用法：.venv/bin/python selftest_forward.py      退出码 0 全过 / 1 有失败

与 selftest.py 分开，是因为这里测的是「记录与打分的口径」，
任何一项失败都意味着前向样本会被错算——比出图失败严重得多。
"""
import copy
import sys

import numpy as np

import config as C

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import forward_log as F     # noqa: E402
import score as SC          # noqa: E402
import swing_watch as S     # noqa: E402

FAIL = []
H_MS = 3_600_000
DAY_MS = 24 * H_MS


def check(name, cond, detail=""):
    if not cond:
        FAIL.append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"   {detail}" if detail else ""))


# ---------------------------------------------------------------- 已收盘过滤
def t_closed():
    print("\n已收盘过滤（数据正确性）")
    now = 10 * H_MS + 30 * 60_000             # 10:30
    rows = [[8 * H_MS, 0, 0, 0, 0, 0, 9 * H_MS - 1],       # 08:00 已收
            [9 * H_MS, 0, 0, 0, 0, 0, 10 * H_MS - 1],      # 09:00 已收
            [10 * H_MS, 0, 0, 0, 0, 0, 11 * H_MS - 1]]     # 10:00 进行中
    out = S.closed_only(rows, now)
    check("进行中的 bar 被剔除", [k[0] for k in out] == [8 * H_MS, 9 * H_MS])


# ---------------------------------------------------------------- AI 解析
def t_parse():
    print("\nAI 方向解析")
    ok = '分析……\nDIRECTION_JSON: {"dir_7d": "多", "dir_30d": "中性"}'
    r = F.parse_ai_dirs(ok)
    check("标准格式（无概率，v1 兼容）", r[7] == "多" and r[30] == "中性"
          and r["p_up_7d"] is None)
    v2 = 'DIRECTION_JSON: {"dir_7d": "多", "dir_30d": "空", "p_up_7d": 62}'
    r = F.parse_ai_dirs(v2)
    check("v2 格式带概率", r[7] == "多" and r[30] == "空" and r["p_up_7d"] == 62)
    r = F.parse_ai_dirs('DIRECTION_JSON: {"dir_7d": "多", "dir_30d": "空", "p_up_7d": 150}')
    check("概率越界 → 置空，方向仍有效", r is not None and r["p_up_7d"] is None)
    check("概率与方向自洽判定",
          (F.p_consistent("多", 62), F.p_consistent("多", 50),
           F.p_consistent("中性", 55), F.p_consistent("空", 44),
           F.p_consistent("多", None)) == (1, 0, 1, 1, ""))
    check("全角冒号 + 中文引号",
          F.parse_ai_dirs('DIRECTION_JSON：{“dir_7d”: “空”, “dir_30d”: “空”}')
          == {7: "空", 30: "空", "p_up_7d": None})
    two = ('示例 DIRECTION_JSON: {"dir_7d": "多", "dir_30d": "多"}\n'
           '最终 DIRECTION_JSON: {"dir_7d": "空", "dir_30d": "中性"}')
    check("多处出现取最后一个", F.parse_ai_dirs(two) == {7: "空", 30: "中性", "p_up_7d": None})
    check("缺失 → None", F.parse_ai_dirs("我认为偏多") is None)
    check("非法值（看多）→ None",
          F.parse_ai_dirs('DIRECTION_JSON: {"dir_7d": "看多", "dir_30d": "多"}') is None)
    check("缺字段 → None",
          F.parse_ai_dirs('DIRECTION_JSON: {"dir_7d": "多"}') is None)
    check("原样照抄占位符 → None",
          F.parse_ai_dirs('DIRECTION_JSON: {"dir_7d": "<方向>", "dir_30d": "<方向>"}')
          is None)
    check("空回答 → None", F.parse_ai_dirs(None) is None)


# ---------------------------------------------------------------- 程序方向
def stat(k, n):
    lo, hi = S.wilson(k, n)
    return {"n_indep": n, "usable": True, "p_up": k / n, "p_up_lo": lo,
            "p_up_hi": hi, "wins": k, "median": 0.0, "mean": 0.0,
            "ab": [{"usable": True, "p_up": k / n}, {"usable": True, "p_up": k / n}]}


def t_prog():
    print("\n程序方向映射（冻结口径 a）")
    base = stat(55, 100)
    neutral = stat(50, 100)                    # CI 含 50%
    bull = stat(75, 100)                       # CI 排除 50%，偏多
    bear = stat(25, 100)
    w = {"position": {"phase": "up", "breakout": True},
         "base": {"7": base},
         "conditional": {"7": {"move": neutral, "days": neutral,
                               "up_all": neutral, "breakout": bull}}}
    d, a = F.prog_direction(w, 7)
    check("仅 breakout 组偏多 → 多", d == "多", a)

    w_dir = copy.deepcopy(w)
    w_dir["conditional"]["7"]["breakout"] = stat(62, 100)   # CI 排除 50%，相对 55% 无增量
    d, a = F.prog_direction(w_dir, 7)
    check("dir 档（方向确认、无增量）也算方向 → 多",
          d == "多" and "breakout:dir" in a, a)

    w2 = copy.deepcopy(w)
    w2["position"]["breakout"] = False
    d, a = F.prog_direction(w2, 7)
    check("未突破时 breakout 组不适用 → 中性", d == "中性", a)

    w3 = copy.deepcopy(w)
    w3["position"] = {"phase": "down", "breakout": False}
    d, a = F.prog_direction(w3, 7)
    check("下行腿无适用组 → 中性", d == "中性" and a == "none_applicable", a)

    w4 = copy.deepcopy(w)
    w4["conditional"]["7"]["move"] = bear
    d, a = F.prog_direction(w4, 7)
    check("适用组方向冲突 → 中性", d == "中性", a)

    w5 = copy.deepcopy(w)
    w5["conditional"]["7"]["breakout"] = neutral
    d, a = F.prog_direction(w5, 7)
    check("全部含 50% → 中性", d == "中性", a)

    small = stat(10, 10)
    small["n_indep"] = C.MIN_INDEP - 1
    w6 = copy.deepcopy(w)
    w6["conditional"]["7"]["breakout"] = small
    d, a = F.prog_direction(w6, 7)
    check("样本不足 → 中性", d == "中性", a)


# ---------------------------------------------------------------- 打分
def t_score():
    print("\n打分：兑现、未来数据隔离、去重")
    t0 = 1_700_000_000_000 // H_MS * H_MS
    n = 40 * 24
    ts = t0 + np.arange(n, dtype=np.int64) * H_MS
    close = 100 + np.arange(n, dtype=float)            # 单调上涨
    rows = [{"as_of_ts": str(t0), "close": "100"},
            {"as_of_ts": str(t0 + 10 * DAY_MS), "close": str(close[240])}]

    # 评估时点刚好在第一行到期 bar 收盘前 1 分钟 → 不得兑现
    now = t0 + 7 * DAY_MS + H_MS - 60_000
    SC.resolve(rows, ts, close, 7, now)
    check("到期 bar 未收盘不兑现（无未来数据）", rows[0]["fwd_7"] is None)

    now = t0 + 7 * DAY_MS + H_MS
    SC.resolve(rows, ts, close, 7, now)
    exp = close[7 * 24] / 100 - 1
    check("到期后按 as_of+7d 那根收盘兑现", rows[0]["fwd_7"] is not None
          and abs(rows[0]["fwd_7"] - exp) < 1e-12 and rows[0]["actual_7"] == "多",
          f"fwd={rows[0]['fwd_7']}")
    check("第二行此时未到期", rows[1]["fwd_7"] is None)

    # 缺 bar：到期那根及其后 2 根都缺 → 无法兑现
    gap = np.isin(np.arange(n), [168, 169, 170])
    SC.resolve(rows, ts[~gap], close[~gap], 7, now + 100 * DAY_MS)
    check("到期附近缺 bar 超容忍 → 不兑现", rows[0]["fwd_7"] is None)

    # 去重：每 12h 一条、连续 20 天 → 7 天步长只剩 3 条
    ev = [{"as_of_ts": str(t0 + i * 12 * H_MS)} for i in range(40)]
    check("7 天步长去重", len(SC.nonoverlap(ev, 7)) == 3,
          f"{len(SC.nonoverlap(ev, 7))}")

    # 三者一致筛选：管线失败 / 中性 / 不同向 都排除
    rs = [{"pipeline_ok": "1", "prog_dir_7": "多", "ai_dir_7": "多"},
          {"pipeline_ok": "0", "prog_dir_7": "多", "ai_dir_7": "多"},
          {"pipeline_ok": "1", "prog_dir_7": "中性", "ai_dir_7": "中性"},
          {"pipeline_ok": "1", "prog_dir_7": "多", "ai_dir_7": "空"}]
    check("一致筛选只留管线正常且同向非中性", len(SC.agree_events(rs, 7)) == 1)

    # 命中统计
    hs = [{"prog_dir_7": "多", "actual_7": "多"},
          {"prog_dir_7": "空", "actual_7": "多"},
          {"prog_dir_7": "多", "actual_7": ""}]
    n_, k_, lo, hi, pend = SC.hit_stats(hs, 7)
    check("命中统计 1/2、未到期 1", (n_, k_, pend) == (2, 1, 1))

    # 应跑槽位数（默认 00:30 / 14:00）
    import datetime as dt
    U = dt.timezone.utc
    t0 = dt.datetime(2026, 9, 27, 14, 0, tzinfo=U)          # 首次记录 = 当天 euus
    end = dt.date(2026, 10, 11)
    e1 = SC.expected_slots(t0, dt.datetime(2026, 9, 27, 15, 0, tzinfo=U), end)
    e2 = SC.expected_slots(t0, dt.datetime(2026, 9, 28, 15, 0, tzinfo=U), end)
    e3 = SC.expected_slots(t0, dt.datetime(2026, 11, 1, tzinfo=U), end)
    check("应跑槽位：部署当天只算已过的槽位", (e1, e2, e3) == (1, 3, 27),
          f"{e1},{e2},{e3}")


def t_prompt():
    print("\n提示词 v2")
    import ask_ai
    import json, os, tempfile
    d = tempfile.mkdtemp()
    json.dump({"as_of": "2026-09-28 05:00", "last_close": 84398.86,
               "position": {"phase": "up"}}, open(os.path.join(d, "w.json"), "w"))
    ask_ai.RESULT = os.path.join(d, "w.json")
    p = ask_ai.build_prompt()
    check("不含程序状态", "当前客观状态" not in p)
    check("含 as_of（forward_log 靠它判 stale）", "2026-09-28 05:00" in p)
    check("方向定义带当前收盘价", "高于当前收盘 84,399 的概率 > 55%" in p)
    check("7 天看 4H、30 天看日线", "以下栏 4H 为主" in p and "以上栏日线为主" in p)
    check("末行为 v2 JSON 格式", p.strip().splitlines()[-1].startswith(
        'DIRECTION_JSON: {"dir_7d": "<方向>", "dir_30d": "<方向>", "p_up_7d": <概率>}'))
    check("已删除操作建议与 3 个月", "操作建议" not in p and "3 个月" not in p)
    ask_ai.RESULT = os.path.join(d, "none.json")
    check("缺结果时回退提示词不报错", "DIRECTION_JSON" in ask_ai.build_prompt())


def main():
    t_prompt()
    t_closed()
    t_parse()
    t_prog()
    t_score()
    print(f"\n{'全部通过' if not FAIL else '失败 ' + str(len(FAIL)) + ' 项：' + ', '.join(FAIL)}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
