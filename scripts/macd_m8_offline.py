#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MACD M8 离线重建器 —— 2026-09-11

替代 `macd_m8.py` 的**采集**职能。M8 的输入只有 `api/v3/klines`，K 线永久可查，
所以任意历史时刻的 M8 状态都能事后算出来，采集器可以停（RESEARCH_FRAMEWORK 教训 7）。

为什么这是「精确重放」而不是「近似重建」
----------------------------------------
采集器在时刻 T 取的是 `limit=500` 的最近 500 根（含当时那根未收盘的），再用
`closeTime < now` 剔除未收盘。本脚本改用 `endTime=T`：币安返回 openTime ≤ T 的
最后 500 根，再用同一句 `closeTime < T` 剔除。**两者取到的 K 线集合逐根相同**，
因此 EMA 的 seeding 起点也相同（`macd_m8.ema` 用首值做种，结果依赖窗口起点）——
不是"预热够长所以误差可忽略"，是同一个窗口算同一个数。

计算逻辑**直接 import `macd_m8` 的函数**（ema / macd_state / classify / TF_PARAMS），
不重写一遍。CLAUDE.md 教训 6：审计脚本本身也会错；复制实现就是给自己制造第二份
会漂移的真相。

能重建什么、不能重建什么
------------------------
- ✅ **给定 symbol + 时刻的 M8 状态**：完全可重建。
- ⚠️ **监控池本身**：MAJORS 9 个是硬编码可重建；AI500 top3 来自 score 榜，
  score 榜**不可重建**。但 V3 bot 一直在写 `ai500_score_history_v3.csv`，
  所以从该 CSV 能还原任意时刻的 top3 → 加上 `--pool-from` 后池子也可重建。
  **这也正是 score 榜采集绝不能停的原因。**
- ❌ 已下架 / 现货无此交易对的标的，历史 K 线取不到，与采集器当时报 error 同理。

数据源口径（2026-09-11 用户拍板：现货 → 合约）
----------------------------------------------
默认 `--source futures`（fapi 永续），与采集器同步切换。理由见 `macd_m8.KLINE_BASE`
注释：M8 是给 V3/B3 的合约交易做择时，现货口径本身就是错配，且现货缺 HYPE 与
8/34 个 score 榜标的；合约侧覆盖 34/34 + MAJORS 9/9。

⚠️ **两种口径数字不同，不得混池**。2026-09-11 之前的 `macd_m8.jsonl` 是现货口径
（行内无 `src` 字段），重放那批数据必须显式 `--source spot`。
换口径不削弱对齐校验的价值：校验验的是「重放机制对不对」，在旧口径下用旧数据验完，
机制正确性即已确立，再把同一机制套到新口径上。

用法
----
    # ① 机制等价性自检（本机可跑）：采集器路径 vs endTime 路径，当下时刻必须逐根相同
    python scripts/macd_m8_offline.py --selfcheck

    # ② 对齐校验（bot 机器跑）：拿留存的 jsonl 逐条重算，必须逐条一致
    python scripts/macd_m8_offline.py --validate scripts/macd_m8.jsonl

    # ③ 重建任意历史时刻
    python scripts/macd_m8_offline.py --at 2026-09-10T04:00:00+08:00 --symbols IOST,BTC
    python scripts/macd_m8_offline.py --at 2026-09-10T04:00:00+08:00 \
           --pool-from user_data/collect/ai500_score_history_v3.csv

代理：沿用 `B300_PROXY`（与采集器同一个环境变量，bot 上默认 7890 即可直接跑）。
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# Windows 控制台默认 GBK，编码不了 zone 图标/✅，会在打印时抛 UnicodeEncodeError
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 唯一真相源：计算逻辑一律来自采集器本体，不复制
from macd_m8 import (  # noqa: E402
    MAJORS, TF_PARAMS, TF_INTERVAL, ZONE_ICON, KLINE_LIMIT, REQUEST_GAP,
    KLINE_BASE, KLINE_SOURCE, macd_state, classify, proxied_get_json,
)

CST = timezone(timedelta(hours=8))


def fetch_klines_at(symbol: str, interval: str, end_ms: int, source: str = None):
    """取截至 end_ms 的已收盘 K 线 close 列表。

    与 `macd_m8.fetch_klines` 的唯一差别是把隐含的 endTime=now 显式化为 end_ms，
    剔除未收盘 K 的判据（closeTime < 截止时刻）一字不改。
    """
    url = (f"{KLINE_BASE[source or KLINE_SOURCE]}"
           f"?symbol={symbol}&interval={interval}&limit={KLINE_LIMIT}"
           f"&endTime={end_ms}")
    rows = proxied_get_json(url)
    closes = [float(r[4]) for r in rows if r[6] < end_ms]
    if len(closes) < 120:
        raise RuntimeError(f"{symbol} {interval} 收盘K不足: {len(closes)}")
    return closes


def state_at(sym: str, when: datetime, source: str = None) -> dict:
    """重建单个标的在 when 时刻的 M8 记录，字段与采集器落盘格式一致。"""
    end_ms = int(when.timestamp() * 1000)
    rec = {"ts": when.astimezone(CST).isoformat(timespec="seconds"),
           "symbol": sym, "src": source or KLINE_SOURCE, "tfs": {}}
    pair = f"{sym}USDT"
    for tf, (f, s, g) in TF_PARAMS.items():
        time.sleep(REQUEST_GAP)
        try:
            closes = fetch_klines_at(pair, TF_INTERVAL[tf], end_ms, source)
        except Exception as e:
            rec["error"] = str(e)[:90]
            return rec
        p, above, up, s_up, zone, hist = macd_state(closes, f, s, g)
        rec["tfs"][tf] = {"power": p, "zone": zone, "zone_icon": ZONE_ICON[zone],
                          "macd_above": above, "macd_up": up,
                          "signal_up": s_up, "hist": round(hist, 6)}
    p1, p2, p3, p4 = (rec["tfs"][tf]["power"] for tf in ("15m", "1h", "4h", "1d"))
    rec["state"] = classify(p1, p2, p3, p4)
    rec["powers"] = {"15m": p1, "1h": p2, "4h": p3, "1d": p4}
    return rec


def pool_at(score_csv: str, when: datetime, top_n: int = 3):
    """从 score 榜历史还原 when 时刻的监控池 = MAJORS + AI500 topN。"""
    import pandas as pd
    d = pd.read_csv(score_csv)
    # fetched_at_utc 字段名叫 utc 但写的是带 offset 的 CST，offset 内嵌且正确 → 禁止手动 -8h
    d["t"] = pd.to_datetime(d.fetched_at_utc, utc=True)
    snap = d[d.t <= when.astimezone(timezone.utc)]
    if snap.empty:
        raise SystemExit(f"score 榜历史里没有 {when} 之前的快照")
    last = snap[snap.t == snap.t.max()]
    top = last.sort_values("score", ascending=False).symbol.tolist()[:top_n]
    pool = list(dict.fromkeys(MAJORS))
    pool += [s for s in top if s not in pool]
    print(f"[pool] 快照 {last.t.iloc[0]} → AI500 top{len(top)}: {', '.join(top)}")
    return pool


# ---------------- 模式 ①：机制等价性自检 ----------------

def selfcheck(symbols, source=None):
    """采集器路径（limit=500 到 now）vs 本脚本路径（endTime=now）逐根比对。

    这一项验证的是**唯一的机制差异**——把隐含的 now 换成显式 endTime。
    K 线列表逐根相同 ⇒ EMA seeding 相同 ⇒ power/zone/state 必然相同。
    与 K 线口径（现货/合约）无关：两条路径用同一个 source，验的是取数机制。
    """
    from macd_m8 import fetch_klines as collector_fetch
    now_ms = int(time.time() * 1000)
    bad = 0
    for sym in symbols:
        pair = f"{sym}USDT"
        for tf in TF_PARAMS:
            time.sleep(REQUEST_GAP)
            try:
                a = collector_fetch(pair, TF_INTERVAL[tf], source)
                time.sleep(REQUEST_GAP)
                b = fetch_klines_at(pair, TF_INTERVAL[tf], now_ms, source)
            except Exception as e:
                print(f"  {sym:<8} {tf:<4} SKIP {str(e)[:50]}")
                continue
            # 采集器窗口与 endTime 窗口可能差最后 1 根（请求间隔内刚好收了一根）→ 尾部对齐
            n = min(len(a), len(b))
            same = a[-n:] == b[-n:]
            flag = "OK " if same else "差异"
            if not same:
                bad += 1
            print(f"  {sym:<8} {tf:<4} {flag} 采集器 {len(a)} 根 / 离线 {len(b)} 根 / 重叠 {n} 根")
    print(f"\n[selfcheck] {'全部一致 ✅' if bad == 0 else f'❌ {bad} 处不一致'}")
    return bad == 0


# ---------------- 模式 ②：对齐校验 ----------------

def validate(path: str, limit: int | None, source: str = None):
    """逐条重算留存的 macd_m8.jsonl，比对 powers 与 state。

    CLAUDE.md 教训 6：重建脚本本身也会错。启用离线重建**之前**必须先过这一关，
    否则就是拿一个未经校验的量具去替换一个在跑的量具。

    ⚠️ 口径必须对齐才有意义：2026-09-11 之前落盘的行是**现货**口径（无 src 字段），
    校验这批数据必须 `--source spot`，否则比的是两个不同的量，不一致毫无信息量。
    换合约口径并不影响本校验的价值——它验的是「重放机制对不对」，用旧数据在旧口径
    下验完，机制正确性即已确立，再把机制套到新口径上。
    """
    recs = []
    with open(path, encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if "state" in r:          # 跳过当时就报 error 的行
                recs.append(r)
    if limit:
        recs = recs[-limit:]
    print(f"[validate] 待校验 {len(recs)} 条（已跳过采集时报错的行）")

    src_seen = {r.get("src", "spot") for r in recs}
    print(f"[validate] 数据口径: {', '.join(sorted(src_seen))}（无 src 字段的行按现货计）"
          f" | 本次重放用: {source or KLINE_SOURCE}")
    if len(src_seen) > 1:
        print("  ⚠️ 文件内混有两种口径，必须按 src 分别校验，不得整体比对")
    elif (source or KLINE_SOURCE) not in src_seen:
        print("  ⚠️ 重放口径与数据口径不符——请加 --source " + sorted(src_seen)[0])

    agree_state = agree_power = fail = 0
    mismatches = []
    for i, r in enumerate(recs, 1):
        when = datetime.fromisoformat(r["ts"])
        try:
            new = state_at(r["symbol"], when, source)
        except Exception as e:
            fail += 1
            print(f"  [{i}/{len(recs)}] {r['symbol']:<8} {r['ts']} 重建失败 {str(e)[:50]}")
            continue
        if "state" not in new:
            fail += 1
            print(f"  [{i}/{len(recs)}] {r['symbol']:<8} {r['ts']} 取数失败 {new.get('error')}")
            continue
        p_ok = new["powers"] == r["powers"]
        s_ok = new["state"] == r["state"]
        agree_power += p_ok
        agree_state += s_ok
        if not (p_ok and s_ok):
            mismatches.append((r, new))
            print(f"  [{i}/{len(recs)}] {r['symbol']:<8} {r['ts']} ✗ "
                  f"powers {r['powers']} → {new['powers']} | "
                  f"state {r['state']} → {new['state']}")
        elif i % 20 == 0:
            print(f"  [{i}/{len(recs)}] ... 一致")

    n = len(recs) - fail
    print(f"\n=== 对齐校验结果 ===")
    print(f"可比对 {n} 条（重建失败 {fail} 条，多为已下架/现货无对）")
    if n:
        print(f"powers 完全一致: {agree_power}/{n} = {agree_power / n * 100:.2f}%")
        print(f"state  完全一致: {agree_state}/{n} = {agree_state / n * 100:.2f}%")
    print("\n判定：powers 一致率 100% 才算通过——M8 是确定性函数，"
          "同一窗口同一公式不存在'大致相同'。任何一条不一致都说明重建有偏差，"
          "必须定位清楚再启用，不得按比例四舍五入放行。")
    return mismatches


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selfcheck", action="store_true", help="机制等价性自检（当下时刻）")
    ap.add_argument("--validate", type=str, help="对齐校验：留存的 macd_m8.jsonl 路径")
    ap.add_argument("--limit", type=int, help="校验只取最近 N 条")
    ap.add_argument("--at", type=str, help="重建时刻，ISO 带时区，如 2026-09-10T04:00:00+08:00")
    ap.add_argument("--symbols", type=str, default="", help="逗号分隔；不给则用 MAJORS")
    ap.add_argument("--pool-from", type=str, help="从 score 榜历史 CSV 还原当时的监控池")
    ap.add_argument("--source", choices=("futures", "spot"), default=None,
                    help="K 线口径；默认合约。校验 2026-09-11 之前的旧数据须用 spot")
    args = ap.parse_args()

    if args.selfcheck:
        syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or MAJORS[:4]
        print(f"[selfcheck] 采集器路径 vs endTime 路径 | 口径 "
              f"{args.source or KLINE_SOURCE} | 标的: {', '.join(syms)}")
        sys.exit(0 if selfcheck(syms, args.source) else 1)

    if args.validate:
        validate(args.validate, args.limit, args.source)
        return

    if not args.at:
        ap.error("需要 --selfcheck / --validate / --at 之一")
    when = datetime.fromisoformat(args.at)
    if when.tzinfo is None:
        ap.error("--at 必须带时区，例如 +08:00，避免时区口径事故")

    if args.pool_from:
        pool = pool_at(args.pool_from, when)
    else:
        pool = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or list(MAJORS)

    print(f"[重建] {when.astimezone(CST).isoformat(timespec='seconds')} "
          f"| 口径 {args.source or KLINE_SOURCE} | 共 {len(pool)} 币")
    for sym in pool:
        rec = state_at(sym, when, args.source)
        if "state" not in rec:
            print(f"  {sym:<10} ✗ {rec.get('error')}")
            continue
        p = rec["powers"]
        zones = " ".join(f"{tf}:{rec['tfs'][tf]['zone_icon']}" for tf in ("15m", "1h", "4h", "1d"))
        print(f"  {sym:<10} p=[{p['15m']:>2},{p['1h']:>2},{p['4h']:>2},{p['1d']:>2}]  "
              f"{rec['state']:<10} {zones}")


if __name__ == "__main__":
    main()
