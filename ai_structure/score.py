# -*- coding: utf-8 -*-
"""前向观察打分：回填已到期预测的实际涨跌，按 FORWARD_PROTOCOL.md 的冻结判据出报告。

只读 out/forward_log.csv 与 data/BTCUSDT_1h.csv，不联网、不改记录。
报告写 out/forward_score.md，并打印到终端。

用法：
    python score.py              # 默认以当前时刻为评估时点
    python score.py --now "2026-10-11 00:00"   # 指定评估时点（复盘用）
"""
import argparse
import csv
import datetime
import os
import sys

import numpy as np
import pandas as pd

import config as C

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import swing_watch as S     # noqa: E402  复用 wilson，保证与主报告同一口径
import ask_ai               # noqa: E402  PROMPT_VERSION

LOG = os.path.join(C.OUT_DIR, "forward_log.csv")
OUT = os.path.join(C.OUT_DIR, "forward_score.md")
H_MS = 3_600_000
DAY_MS = 24 * H_MS
MAX_LAG_H = 2        # 到期那根 bar 缺失时，最多顺延 2 根；再缺就算「无法兑现」


def load_log():
    if not os.path.exists(LOG):
        return []
    with open(LOG, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def resolve(rows, ts, close, h, now_ms):
    """给每行补 fwd_{h} 与 actual_{h}。未到期 / 无法兑现留空。"""
    for r in rows:
        r[f"fwd_{h}"], r[f"actual_{h}"] = None, ""
        if not r.get("as_of_ts") or not r.get("close"):
            continue
        t0 = int(r["as_of_ts"])
        tgt = t0 + h * DAY_MS
        if tgt + H_MS > now_ms:          # 到期 bar 尚未收盘
            continue
        j = int(np.searchsorted(ts, tgt))
        if j >= len(ts) or ts[j] - tgt > MAX_LAG_H * H_MS:
            continue
        f = close[j] / float(r["close"]) - 1
        r[f"fwd_{h}"] = f
        r[f"actual_{h}"] = "多" if f > 0 else "空"


def agree_events(rows, h):
    """三者一致的候选：管线正常 + 程序与 AI 同为 多 或同为 空。"""
    return [r for r in rows
            if r.get("pipeline_ok") == "1"
            and r.get(f"prog_dir_{h}") in ("多", "空")
            and r.get(f"prog_dir_{h}") == r.get(f"ai_dir_{h}")]


def nonoverlap(evts, h):
    """按 as_of 贪心取非重叠（步长 ≥ h 天），与 swing_watch.nonoverlap 同一规则。"""
    keep, last = [], None
    for r in sorted(evts, key=lambda r: int(r["as_of_ts"])):
        t = int(r["as_of_ts"])
        if last is None or t - last >= h * DAY_MS:
            keep.append(r)
            last = t
    return keep


def hit_stats(evts, h):
    done = [r for r in evts if r.get(f"actual_{h}")]
    k = sum(1 for r in done if r[f"actual_{h}"] == r[f"prog_dir_{h}"])
    n = len(done)
    lo, hi = S.wilson(k, n)
    return n, k, lo, hi, len(evts) - n


def expected_slots(t0, now, end_date):
    """[首次记录, min(now, 窗口末)) 之间排班槽位的个数。

    分母用「应跑槽位数」而非「实际有记录数」：cron 整轮没跑也必须算失败。
    槽位时刻 +1h 仍未到 now 的不计（给那一轮留出跑完的时间）；
    首次记录之前 30 分钟以内的槽位计入（首次记录本身就是那个槽位）。
    """
    n = 0
    d = t0.date()
    while d < end_date:
        for hm in (C.SLOT_ASIA, C.SLOT_EUUS):
            hh, mm = (int(x) for x in hm.split(":"))
            t = datetime.datetime(d.year, d.month, d.day, hh, mm,
                                  tzinfo=datetime.UTC)
            if t >= t0 - datetime.timedelta(minutes=30) \
                    and t + datetime.timedelta(hours=1) <= now:
                n += 1
        d += datetime.timedelta(days=1)
    return n


def checkpoint(rows, now):
    """管线检查点：首次记录起 FWD_CHECKPOINT_DAYS 天内。"""
    if not rows:
        return None
    t0 = min(datetime.datetime.strptime(r["run_at"], "%Y-%m-%d %H:%M")
             .replace(tzinfo=datetime.UTC) for r in rows)
    d0 = t0.date()
    end = d0 + datetime.timedelta(days=C.FWD_CHECKPOINT_DAYS)
    in_win = [r for r in rows
              if d0 <= datetime.date(int(r["run_key"][:4]), int(r["run_key"][4:6]),
                                     int(r["run_key"][6:8])) < end]
    expected = max(expected_slots(t0, now, end), len(in_win))
    pipe_ok = sum(1 for r in in_win if r.get("pipeline_ok") == "1")
    ai_back = [r for r in in_win if r.get("ai_status") == "ok"]
    parse_ok = sum(1 for r in ai_back if r.get("ai_parse_ok") == "1")
    return {"start": d0, "end": end, "complete": now.date() >= end,
            "expected": expected, "recorded": len(in_win),
            "pipe_ok": pipe_ok, "ai_back": len(ai_back), "parse_ok": parse_ok}


def pct(a, b):
    return f"{a / b * 100:.1f}%" if b else "—"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--now", help="评估时点 UTC，格式 'YYYY-MM-DD HH:MM'")
    args = ap.parse_args()
    now = (datetime.datetime.strptime(args.now, "%Y-%m-%d %H:%M")
           .replace(tzinfo=datetime.UTC) if args.now
           else datetime.datetime.now(datetime.UTC))
    now_ms = int(now.timestamp() * 1000)

    all_rows = load_log()
    # 只统计当前提示词版本的样本：提示词变更 = 前提条件变化，前后不混池
    # （PROTOCOL_AMENDMENT_20260928_v2.md）。检查点窗口也从首条当前版本记录起算。
    rows = [r for r in all_rows if r.get("prompt_version") == ask_ai.PROMPT_VERSION]
    excluded = len(all_rows) - len(rows)
    df = pd.read_csv(C.CSV)
    ts = df["ts"].to_numpy(np.int64)
    close = df["close"].to_numpy(float)
    H1, H2 = C.FWD_PRIMARY_H, C.FWD_SECONDARY_H
    for h in (H1, H2):
        resolve(rows, ts, close, h, now_ms)

    L = [f"# 前向观察打分  ·  评估时点 {now:%Y-%m-%d %H:%M} UTC",
         "",
         "> 判据见 FORWARD_PROTOCOL.md（2026-09-27 冻结）。本报告只陈述数字，"
         "不构成交易建议。",
         "",
         f"提示词版本 {ask_ai.PROMPT_VERSION}：记录 {len(rows)} 行（仅 asia / euus 槽位）；"
         f"旧版本样本 {excluded} 行已排除、不计入 n", ""]

    # ---- ① 管线检查点
    cp = checkpoint(rows, now)
    L += ["## ① 管线检查点（决定「是否继续观察」，不决定实盘）", ""]
    if not cp:
        L += ["尚无记录。", ""]
    else:
        po = cp["pipe_ok"] / cp["expected"] if cp["expected"] else 0
        pa = cp["parse_ok"] / cp["ai_back"] if cp["ai_back"] else 0
        ok1, ok2 = po >= C.FWD_PIPE_OK, pa >= C.FWD_PARSE_OK
        state = ("进行中" if not cp["complete"]
                 else ("通过" if ok1 and ok2 else "未通过"))
        L += [f"窗口 {cp['start']} → {cp['end']}（不含）  状态：**{state}**", "",
              "| 项 | 实测 | 门槛 | |",
              "|---|---|---|---|",
              f"| 管线成功率（分母=应跑槽位） | {cp['pipe_ok']}/{cp['expected']}"
              f" = {pct(cp['pipe_ok'], cp['expected'])} | ≥{C.FWD_PIPE_OK:.0%} |"
              f" {'✓' if ok1 else '✗'} |",
              f"| AI 结构化解析成功率（分母=AI 成功返回） | {cp['parse_ok']}/"
              f"{cp['ai_back']} = {pct(cp['parse_ok'], cp['ai_back'])} |"
              f" ≥{C.FWD_PARSE_OK:.0%} | {'✓' if ok2 else '✗'} |",
              f"| 实际有记录的槽位 | {cp['recorded']} | — | |", ""]

    # ---- ② 程序与 AI 一致率（描述性）
    L += ["## ② 程序 vs AI 方向分布（描述性，不是判据）", ""]
    for h in (H1, H2):
        both = [r for r in rows if r.get(f"prog_dir_{h}") and r.get(f"ai_dir_{h}")]
        cnt = {}
        for r in both:
            key = (r[f"prog_dir_{h}"], r[f"ai_dir_{h}"])
            cnt[key] = cnt.get(key, 0) + 1
        same = sum(v for (p, a), v in cnt.items() if p == a)
        L += [f"**{h} 天**：两侧均有值 {len(both)} 行，方向相同 {same} 行"
              f"（{pct(same, len(both))}）", "",
              "| 程序 \\ AI | 多 | 空 | 中性 |", "|---|---|---|---|"]
        for p in ("多", "空", "中性"):
            L.append(f"| {p} | " + " | ".join(str(cnt.get((p, a), 0))
                                               for a in ("多", "空", "中性")) + " |")
        L.append("")

    # ---- ③ 三者一致（实盘门槛）
    L += ["## ③ 三者一致 → 实盘门槛", ""]
    for h, primary in ((H1, True), (H2, False)):
        ev = agree_events(rows, h)
        ind = nonoverlap(ev, h)
        n, k, lo, hi, pend = hit_stats(ind, h)
        tag = "主判据" if primary else "副判据（只记录）"
        L += [f"**{h} 天 · {tag}**", "",
              f"- 程序与 AI 同向（非中性）且管线正常的记录：{len(ev)} 行",
              f"- 按 {h} 天步长去重后的独立窗口：{len(ind)} 个"
              f"（已兑现 {n}，未到期/无法兑现 {pend}）",
              f"- 命中 {k}/{n}，命中率 {pct(k, n)}，Wilson 95% CI "
              f"[{lo * 100:.1f}%, {hi * 100:.1f}%]" if n else
              "- 尚无已兑现的独立窗口"]
        if primary:
            if n < C.FWD_MIN_INDEP:
                v = (f"**未达实盘门槛**：已兑现独立窗口 {n} < {C.FWD_MIN_INDEP}。"
                     f"继续观察。")
            elif lo > C.FWD_BASE_RATE:
                v = (f"**达到实盘门槛**：n={n} ≥ {C.FWD_MIN_INDEP}，CI 下界 "
                     f"{lo * 100:.1f}% > 基准 {C.FWD_BASE_RATE:.0%}。"
                     f"下一步：按 CLAUDE.md B 类流程写交易规则并冻结前向判据，"
                     f"不是直接下单。")
            else:
                v = (f"**未达实盘门槛**：n={n} 已够，但 CI 下界 {lo * 100:.1f}% ≤ "
                     f"基准 {C.FWD_BASE_RATE:.0%}，命中率与「永远做多」不可区分。")
            L += ["", v]
        L.append("")

    # ---- ④ AI 概率校准（辅助，不是判据）
    L += ["## ④ AI 概率 p_up_7d 校准（辅助记录，不参与任何判据）", ""]
    pr = [r for r in rows if r.get("ai_p_up_7d") not in (None, "")
          and r.get(f"actual_{H1}") and r.get("pipeline_ok") == "1"]
    pr = nonoverlap(pr, H1)
    if pr:
        y = np.array([1.0 if r[f"actual_{H1}"] == "多" else 0.0 for r in pr])
        p = np.array([float(r["ai_p_up_7d"]) / 100 for r in pr])
        bs = float(np.mean((p - y) ** 2))
        bs0 = float(np.mean((C.FWD_BASE_RATE - y) ** 2))
        cons = [r for r in rows if r.get("ai_p_consistent") in ("0", "1")]
        L += [f"- 已兑现的独立窗口 {len(pr)} 个；AI Brier {bs:.3f} vs 恒报 "
              f"{C.FWD_BASE_RATE:.0%} 的 Brier {bs0:.3f}（越低越好）",
              f"- 概率与方向自洽率 {sum(r['ai_p_consistent'] == '1' for r in cons)}"
              f"/{len(cons)}"]
    else:
        L += ["- 尚无已兑现的带概率窗口"]
    L.append("")

    L += ["---",
          f"判据常量（config.py，冻结）：主期限 {H1}d / 副期限 {H2}d / 基准 "
          f"{C.FWD_BASE_RATE:.0%} / 独立窗口下限 {C.FWD_MIN_INDEP} / 检查点 "
          f"{C.FWD_CHECKPOINT_DAYS} 天、管线 ≥{C.FWD_PIPE_OK:.0%}、解析 "
          f"≥{C.FWD_PARSE_OK:.0%}"]

    text = "\n".join(L) + "\n"
    os.makedirs(C.OUT_DIR, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(text)
    print(f"已写 {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
