#!/usr/bin/env python3
"""僵尸过滤器参考价口径核查 —— 2026-09-11

**探索性——非结论，禁止引用为证据。** 用途：为「V3 僵尸过滤 stage ② 是否误杀」提供
立项判断依据（对标 RESEARCH_FRAMEWORK §4 的 audit_task114/116/117 前置核查模式）。

命题
----
V3 `_check_tradable` 的主判据（旧版）比的是：
    diff_pp = |(binance_last - start_open_1h)/start_open_1h*100 - change_pct_value|
即「用 start_time 所在那根 **1h** K 的开盘价复算的累计涨幅」与「接口报的累计涨幅」之差。

怀疑：接口 startPrice 记的是信号触发**那一刻**的价，而 1h 开盘价最多早 59 分钟。
能上 score 榜的标的按构造正在快速拉升，这段时间差会被直接读成「对不上」。

且该 pp 差 ≈ 参考价相对误差 × (1 + 累计涨幅) —— 标的涨得越多，同样的参考价误差
被放得越大，于是过滤器系统性地掐掉涨幅最大的那批。

验证
----
对 ai500_zombie_check.csv 里每个独立信号 episode（symbol + start_time），
拉 start_time 那一分钟的真实 1m K 开盘价，比较两种重建与接口 startPrice 的偏差。

结论（2026-09-11 实测，16 个 episode）
--------------------------------------
偏差中位 / p90：用 1h 开盘 1.03 / 10.45 pp；改用 1m 开盘 0.64 / 1.94 pp。
ARB 3.81→0.09、BULLA 19.38→0.58、FF 10.28→2.30、SOPH 10.62→1.14 —— 四个确认误杀。
ZEC 10.26→10.70 —— **改用 1m 后偏差仍在，ZEC 是真阳性**（参考价确实脱节）。
按评估次数加权：tradable 率 16.0% → 70.8%。

用法
----
    python scripts/audit_zombie_ref_price.py <zombie_check.csv 所在目录>
"""
import json
import sys
import time
import urllib.request

import pandas as pd

PROXY = "http://127.0.0.1:10809"
REF_PP = 3.0          # 新判据门槛：|接口 startPrice / 1m 开盘 - 1| > 3% 判僵尸
_opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))


def kline_1m(sym: str, ts: int):
    """取覆盖 ts（秒级 epoch）那根 1m K。"""
    url = (f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}USDT&interval=1m"
           f"&startTime={(ts - 60) * 1000}&endTime={(ts + 120) * 1000}&limit=5")
    for attempt in range(4):
        try:
            return json.load(_opener.open(url, timeout=20))
        except Exception as e:
            if attempt == 3:
                print(f"  ERR {sym}: {str(e)[:60]}")
                return []
            time.sleep(3)
    return []


def main(src_dir: str):
    z = pd.read_csv(f"{src_dir}/ai500_zombie_check.csv")
    z = z[z.reason.isin(["ok", "recompute_mismatch"])].copy()
    # 接口未直接落 startPrice，从 price / (1+change_pct) 反推（与策略同一口径）
    z["start_price"] = z.interface_price / (1 + z.interface_change_pct / 100)
    ep = z.groupby(["symbol", "start_time"], as_index=False).agg(
        start_price=("start_price", "median"),
        start_open_1h=("start_open", "median"),
        n=("symbol", "size"),
        reason=("reason", lambda s: s.mode()[0]))
    print(f"独立信号 episode: {len(ep)}")

    res = []
    for _, r in ep.iterrows():
        candles = kline_1m(r.symbol, int(r.start_time))
        time.sleep(0.3)
        open_1m = None
        for c in candles:
            if int(c[0]) <= r.start_time * 1000 < int(c[0]) + 60_000:
                open_1m = float(c[1])
                break
        if open_1m is None:
            continue
        res.append(dict(
            symbol=r.symbol, n=r.n, reason=r.reason,
            start_price=r.start_price, open_1m=open_1m, open_1h=r.start_open_1h,
            差_1m_pp=abs(r.start_price / open_1m - 1) * 100,
            差_1h_pp=abs(r.start_price / r.start_open_1h - 1) * 100))

    R = pd.DataFrame(res)
    R["旧判定"] = R.reason.map({"ok": "ok", "recompute_mismatch": "僵尸"})
    R["新判定"] = R.差_1m_pp.apply(lambda x: "僵尸" if x > REF_PP else "ok")

    print("\n=== 探索性——非结论 | 接口 startPrice 与两种重建的偏差 ===")
    print(R[["symbol", "n", "旧判定", "新判定", "差_1h_pp", "差_1m_pp"]].round(3).to_string(index=False))
    print(f"\n中位偏差: 1m 开盘 {R.差_1m_pp.median():.3f} pp | 1h 开盘 {R.差_1h_pp.median():.3f} pp")
    print(f"p90 偏差: 1m 开盘 {R.差_1m_pp.quantile(.9):.3f} pp | 1h 开盘 {R.差_1h_pp.quantile(.9):.3f} pp")

    chg = R[R.旧判定 != R.新判定]
    tot, old_ok, new_ok = R.n.sum(), R[R.旧判定 == "ok"].n.sum(), R[R.新判定 == "ok"].n.sum()
    print(f"\n改判 episode: {len(chg)} 个 —— {', '.join(chg.symbol)}")
    print(f"按评估次数加权 tradable 率: 旧 {old_ok / tot * 100:.1f}% → 新 {new_ok / tot * 100:.1f}%")
    print("\n仍被拦下:")
    print(R[R.新判定 == "僵尸"][["symbol", "差_1m_pp", "n"]].round(2).to_string(index=False))
    return R


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
