#!/usr/bin/env python3
"""出场规则三候选 —— 纸面 shadow 执行器（预注册见 research/ai500_h1/EXIT_RULES_PREREG.md）。

职责（幂等）：
1. 从 user_data/ai500_local/score_*.csv 检测**新信号**（≥70 / ≥80 首破，24h 冷却），
   只取 SINCE 之后产生的（预注册时点），记入 exit_arms_signals.csv
2. 对窗口已走完的信号，用币安合约真实 K 线计算四臂结果（R0/A/B/C），追加 exit_arms_results.csv
3. 打印摘要（各臂 n / 中位 / 均值 / 配对差 vs R0）——**只描述，不下判决**（判决按预注册 §3）

用法：
  python3 scripts/exit_rule_shadow.py            # 正常一轮（cron 每 30 分钟）
  python3 scripts/exit_rule_shadow.py --selftest # 用 20 天回填 episode 验证代码路径（不写文件）
  python3 scripts/exit_rule_shadow.py --status   # 只看已有结果
"""
import csv, os, re, sys, statistics as st
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UD = os.path.join(ROOT, "user_data")
LOC = os.path.join(UD, "ai500_local")
SIG_CSV = os.path.join(LOC, "exit_arms_signals.csv")
RES_CSV = os.path.join(LOC, "exit_arms_results.csv")
KCACHE = os.path.join(LOC, "klines_cache")
LOG = os.path.join(LOC, "exit_rule_shadow.log")
CST, UTC = ZoneInfo("Asia/Shanghai"), timezone.utc
PROXY = os.environ.get("LOCAL_PROXY", "http://127.0.0.1:7890")
FEE, SLIP = 0.0005, 0.0005           # 单边；J4 用双倍档
COOLDOWN_H = 24
MAX_WINDOW_H = 72                    # 最长臂窗口；入场最多晚 4h → 78h 后判定为"已走完"
EVAL_AFTER_H = 78
SINCE = "2026-09-25T20:00:00+08:00"  # 预注册 commit 时点（之前的数据属回填，不入前向样本）
NUM = re.compile(r"-?\d+(?:\.\d+)?")

SIG_FIELDS = ["symbol", "threshold", "signal_ts_cst", "entry_ts_cst", "entry_price", "recorded_at_cst"]
RES_FIELDS = ["symbol", "threshold", "signal_ts_cst", "entry_ts_cst", "entry_price", "arm",
              "ret_pct", "ret_pct_x2cost", "exit_reason", "hold_h", "evaluated_at_cst"]


def log(msg):
    line = f"[{datetime.now(CST):%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as fh:
        fh.write(line + "\n")


def num(v):
    m = NUM.search(str(v).replace(",", "").replace("$", "")) if v is not None else None
    return float(m.group()) if m else None


def append_csv(path, fields, rows):
    new = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerows(rows)


# ---------------- 数据：score 榜 / K 线 ----------------
def load_board():
    """返回 {symbol: [(t_cst_naive, score)]}（时间一律按 +08:00 解释——列名会骗人）。"""
    import glob
    bysym = defaultdict(list)
    for path, tc in [(f"{UD}/ai500_score_history_v3.csv", "fetched_at_utc")] + \
                    [(f, "fetched_at_cst") for f in sorted(glob.glob(f"{LOC}/score_*.csv"))]:
        if not os.path.exists(path):
            continue
        for r in csv.DictReader(open(path, errors="replace")):
            t, sc = (r.get(tc) or "").strip(), num(r.get("score"))
            if t and sc is not None:
                bysym[r["symbol"]].append((datetime.fromisoformat(t).astimezone(CST).replace(tzinfo=None), sc))
    for s in bysym:
        bysym[s].sort()
    return bysym


def detect_signals(threshold, bysym, since_dt):
    out = []
    for s, rs in bysym.items():
        prev, last = None, None
        for i, (t, sc) in enumerate(rs):
            gap = (t - rs[i - 1][0]).total_seconds() / 60 if i else None
            if sc >= threshold and (prev is None or prev < threshold or (gap and gap > 60)):
                if last is None or (t - last) > timedelta(hours=COOLDOWN_H):
                    if t >= since_dt:
                        out.append((s, t))
                    last = t
            prev = sc
    return sorted(out, key=lambda x: x[1])


_ex = None
def exchange():
    global _ex
    if _ex is None:
        import ccxt
        _ex = ccxt.binanceusdm({"enableRateLimit": True,
                                "proxies": {"http": PROXY, "https": PROXY},
                                "options": {"defaultType": "future"}})
    return _ex


def _now_ms():
    return int(datetime.now(UTC).timestamp() * 1000)


def klines(sym, tf, since_ms, until_ms):
    """带本地缓存。请求窗口贴近"现在"时强制刷新（否则会拿到过期数据）。"""
    os.makedirs(KCACHE, exist_ok=True)
    fn = os.path.join(KCACHE, f"{sym}_{tf}.csv")
    need_fresh = until_ms >= _now_ms() - 8 * 3600_000
    data = []
    if os.path.exists(fn):
        data = [tuple(map(float, l.split(","))) for l in open(fn) if l.strip()]
        # since_ms 只作软提示：缓存只需覆盖"窗口末端前 3 天"（够窗口 + ATR(4h,14) 回看）
        covers = bool(data) and data[-1][0] >= until_ms - 3600_000 and data[0][0] <= until_ms - 3 * 86400_000
        fresh = bool(data) and data[-1][0] >= _now_ms() - 45 * 60_000
        if covers and (fresh or not need_fresh):
            return data
    out, cur = [], since_ms
    try:
        ex = exchange()
        while cur < until_ms:
            k = ex.fetch_ohlcv(f"{sym}/USDT:USDT", tf, since=cur, limit=1500)
            if not k:
                break
            out += [(float(a), float(b), float(c), float(d), float(e)) for a, b, c, d, e, *_ in k]
            nxt = k[-1][0] + 1
            if nxt <= cur:
                break
            cur = nxt
    except Exception as e:
        log(f"[warn] {sym} {tf} 拉取失败: {type(e).__name__}: {str(e)[:90]}")
        return data
    if out:
        with open(fn, "w") as fh:
            for r in out:
                fh.write(",".join(str(x) for x in r) + "\n")
    return out


def atr(sym, up_to_ms, n=14):
    k = [b for b in klines(sym, "4h", _now_ms() - 60 * 86400_000, _now_ms()) if b[0] + 14400_000 <= up_to_ms]
    if len(k) < n + 1:
        return None
    trs = [(max(b[2], p[4]) - min(b[3], p[4])) / p[4] for p, b in zip(k[-n - 1:-1], k[-n:])]
    return sum(trs) / len(trs)


# ---------------- 四臂模拟（与预注册 §1 一致）----------------
def run_arm(sym, entry_ms, entry_px, arm, cost_x=1.0):
    # 请求窗口 = [入场 −60 天, min(现在, 入场 + 80h)]：窗口已走完就用缓存，未走完才刷新
    until = min(_now_ms(), entry_ms + 80 * 3600_000)
    k15 = klines(sym, "15m", entry_ms - 60 * 86400_000, until)
    k4 = klines(sym, "4h", entry_ms - 60 * 86400_000, until)
    k1 = klines(sym, "1d", entry_ms - 60 * 86400_000, until)
    if not k15:
        return None
    a = atr(sym, entry_ms) or 0.08
    if arm == "R0":  sp, tf_h, cap_h = 0.05, 4, 48
    elif arm == "A":  sp, tf_h, cap_h = 0.05, 24, 72
    elif arm == "B":  sp, tf_h, cap_h = min(2 * a, 0.20), 4, 72
    elif arm == "C":  sp, tf_h, cap_h = min(2 * a, 0.20), 24, 72
    else: raise ValueError(arm)
    kx = k4 if tf_h == 4 else k1
    stop = entry_px * (1 - sp)
    cap = entry_ms + cap_h * 3600_000
    cost = (FEE * cost_x + SLIP * cost_x) * 200
    for b in k15:
        if b[0] <= entry_ms:
            continue
        if b[0] > cap:
            return ((b[1] / entry_px - 1) * 100 - cost, "cap", (cap - entry_ms) / 3600_000)
        if b[3] <= stop:
            return (-(sp * 100) - SLIP * cost_x * 100 - FEE * cost_x * 200, "stop", (b[0] - entry_ms) / 3600_000)
        cms = b[0] + 900_000
        if cms % (tf_h * 3600_000) == 0:
            kk = next((x for x in reversed(kx) if x[0] + tf_h * 3600_000 <= cms), None)
            if kk and kk[4] < kk[1]:
                ret = (b[4] / entry_px - 1) * 100
                return (ret - cost, f"{tf_h}h阴线", (b[0] - entry_ms) / 3600_000)
    return None


def entry_of(sym, t_sig):
    ms = int(t_sig.replace(tzinfo=CST).timestamp() * 1000)
    k15 = klines(sym, "15m", ms - 60 * 86400_000, _now_ms())
    b0 = next((b for b in k15 if (b[0] + 900_000) % 14_400_000 == 0 and b[0] + 900_000 >= ms), None)
    if b0 is None:
        b0 = next((b for b in k15 if b[0] >= ms), None)
    return b0


# ---------------- 主流程 ----------------
def status():
    if not os.path.exists(RES_CSV):
        print("（尚无结果文件——前向样本从预注册时点开始积累）")
        return
    rows = list(csv.DictReader(open(RES_CSV, errors="replace")))
    print(f"结果行数 {len(rows)}")
    byth = defaultdict(lambda: defaultdict(list))
    for r in rows:
        byth[r["threshold"]][r["arm"]].append(float(r["ret_pct"]))
    for th, arms in sorted(byth.items()):
        print(f"\n  ── ≥{th} 门 ──")
        base = {r["symbol"] + r["signal_ts_cst"]: float(r["ret_pct"]) for r in rows
                if r["threshold"] == th and r["arm"] == "R0"}
        for arm in ("R0", "A", "B", "C"):
            v = arms.get(arm, [])
            if not v:
                continue
            print(f"    {arm:<3} n={len(v):<4} 中位 {st.median(v):+7.2f}%  均值 {st.mean(v):+7.2f}%  累计 {sum(v):+8.1f}%")
            if arm != "R0":
                d = [float(r["ret_pct"]) - base[r["symbol"] + r["signal_ts_cst"]] for r in rows
                     if r["threshold"] == th and r["arm"] == arm and r["symbol"] + r["signal_ts_cst"] in base]
                if d:
                    print(f"        配对差 vs R0：均值 {st.mean(d):+6.2f}pp  中位 {st.median(d):+6.2f}pp  n={len(d)}"
                          f"  （J1 需 ≥ +5.0pp；弃案线 n=70）")


def main():
    if "--status" in sys.argv:
        status(); return 0
    if "--selftest" in sys.argv:
        eps = [("ZEC", "2026-09-05 14:32"), ("BULLA", "2026-09-05 15:16"), ("BULLA", "2026-09-06 22:21"),
               ("ZEC", "2026-09-08 04:12"), ("IOST", "2026-09-10 02:05"), ("ZEC", "2026-09-10 17:45"),
               ("ZEC", "2026-09-11 20:52"), ("BR", "2026-09-15 02:44"), ("AIN", "2026-09-15 22:40"),
               ("BR", "2026-09-16 21:17"), ("BR", "2026-09-18 15:32"), ("BR", "2026-09-19 18:18")]
        print("自检：用回填 episode 跑四臂代码路径（不写任何文件）")
        agg = defaultdict(list)
        for sym, hm in eps:
            t = datetime.strptime(hm, "%Y-%m-%d %H:%M")
            b0 = entry_of(sym, t)
            if b0 is None:
                print(f"  {sym} {hm}: 无 K 线，跳过"); continue
            line = []
            for arm in ("R0", "A", "B", "C"):
                r = run_arm(sym, b0[0], b0[4], arm)
                if r:
                    agg[arm].append(r[0])
                    line.append(f"{arm}={r[0]:+6.2f}%({r[1]})")
            print(f"  {sym:<6}{hm}  入场 {b0[4]:.6g}  " + "  ".join(line))
        print("\n  汇总：")
        for arm in ("R0", "A", "B", "C"):
            v = agg[arm]
            if v:
                print(f"    {arm:<3} n={len(v)} 中位 {st.median(v):+.2f}% 均值 {st.mean(v):+.2f}% 累计 {sum(v):+.1f}% 最差 {min(v):+.1f}%")
        return 0

    since = datetime.fromisoformat(SINCE).astimezone(CST).replace(tzinfo=None)
    board = load_board()
    known = set()
    if os.path.exists(SIG_CSV):
        for r in csv.DictReader(open(SIG_CSV, errors="replace")):
            known.add((r["symbol"], r["signal_ts_cst"]))
    prev_res = set()
    if os.path.exists(RES_CSV):
        for r in csv.DictReader(open(RES_CSV, errors="replace")):
            prev_res.add((r["symbol"], r["signal_ts_cst"], r["arm"]))

    new_sigs = []
    for th in (70, 80):
        for sym, t in detect_signals(th, board, since):
            key = (sym, t.strftime("%Y-%m-%d %H:%M:%S"))
            if key in known:
                continue
            b0 = entry_of(sym, t)
            if b0 is None:
                log(f"[skip] {sym} {key[1]} 无 K 线，下轮重试"); continue
            new_sigs.append({"symbol": sym, "threshold": th, "signal_ts_cst": key[1],
                             "entry_ts_cst": datetime.fromtimestamp(b0[0] / 1000, UTC).astimezone(CST).strftime("%Y-%m-%d %H:%M:%S"),
                             "entry_price": b0[4], "recorded_at_cst": f"{datetime.now(CST):%Y-%m-%d %H:%M:%S}"})
            known.add(key)
    if new_sigs:
        append_csv(SIG_CSV, SIG_FIELDS, new_sigs)
        log(f"新信号 {len(new_sigs)} 个: " + ", ".join(f"{r['symbol']}≥{r['threshold']}" for r in new_sigs))

    # 评估已走完窗口的信号
    sigs = list(csv.DictReader(open(SIG_CSV, errors="replace"))) if os.path.exists(SIG_CSV) else []
    now = datetime.now(CST).replace(tzinfo=None)
    done = 0
    for r in sigs:
        sig_t = datetime.strptime(r["signal_ts_cst"], "%Y-%m-%d %H:%M:%S")
        if (now - sig_t) < timedelta(hours=EVAL_AFTER_H):
            continue
        entry_ms = int(datetime.strptime(r["entry_ts_cst"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=CST).timestamp() * 1000)
        entry_px = float(r["entry_price"])
        rows = []
        for arm in ("R0", "A", "B", "C"):
            if (r["symbol"], r["signal_ts_cst"], arm) in prev_res:
                continue
            out = run_arm(r["symbol"], entry_ms, entry_px, arm)
            out2 = run_arm(r["symbol"], entry_ms, entry_px, arm, cost_x=2.0)
            if not out or not out2:
                continue
            rows.append({"symbol": r["symbol"], "threshold": r["threshold"], "signal_ts_cst": r["signal_ts_cst"],
                         "entry_ts_cst": r["entry_ts_cst"], "entry_price": entry_px, "arm": arm,
                         "ret_pct": round(out[0], 4), "ret_pct_x2cost": round(out2[0], 4),
                         "exit_reason": out[1], "hold_h": round(out[2], 2),
                         "evaluated_at_cst": f"{now:%Y-%m-%d %H:%M:%S}"})
        if rows:
            append_csv(RES_CSV, RES_FIELDS, rows)
            done += len(rows)
    log(f"本轮完成: 新信号 {len(new_sigs)}，评估结果 {done} 行")
    status()
    return 0


if __name__ == "__main__":
    sys.exit(main())
