"""
TASK ①（修正版，2026-07-10）：LPS 标注分层的因果口径复核 + No Supply 原著口径对照

背景：TASK 35（2026-06-19）在污染口径（TASK 60 未来函数修复前）下测得
"有 LPS 前导的多头 PF 83.89 vs 无 LPS 58.02（+45%）"，并承诺后续验证。
TASK 60~71 因果修复后基线塌缩至 3,187 USDT / 843笔 / 35.8% WR——那 469 笔
多头是被污染 ZigZag 入场产出的，+45% 先验按 TASK 65 分诊语义一律非约束。
本审计在因果口径存档（backtest-result-2026-07-07_09-01-42，已核验与
AGENTS.md 基线一致：843笔 / 3,187.01 USDT / 302胜）的 519 笔多头上复测。

TASK 36 已证伪 LPS/No Supply 作为**前向独立信号**（51.9%≈随机）；本审计
只测残余问题：**作为已有入场的分层标注，是否仍有 per-trade 质量鉴别力**。

对照臂（跑数前固定，零调参）：
  A  现行策略口径 lps_signal（WyckoffV8.py L229-267 精确复刻）：
     SOS₁(阳线+spread>ATR×0.6+vol>vol_ma10×1.2+涨幅>0.5%) → 1~6根内
     LPS(阴线 + vol<vol_ma10×0.7 + spread<ATR×0.5 + spread<sos_spread×0.7
         + close≥sos_low×0.99)
  B1 原著口径最小替换（Villahermosa Ch26 No Supply 量能条款）：
     A 中 vol<vol_ma10×0.7 → vol<前一根 且 vol<前两根；去掉 ATR×0.5 条款
     （原著无均量概念），其余同 A
  B2 原著口径完整版：B1 + 收盘偏高（close_position ≥ 0.5）
匹配法（与 TASK 35 原审计一致，保证可比）：入场 open_date 前 24H 窗口内
存在 LPS 事件 → has_lps=True。

实现注记：ATR 用 Wilder ewm(alpha=1/14) 复刻 talib ATR（host 无 talib）；
与策略的 ta.ATR 数值差异在平滑初期，对分层判断无实质影响（声明为已知近似）。

预注册判据（分层有效，三项同时满足才算"存活"）：
  S1 LPS 组样本 n ≥ 30
  S2 per-trade 平均利润 delta（LPS组 − 非LPS组）> 0 且 PF delta > 0
  S3 时间对半切：前后两半 delta 同号
任一不满足 → 分层鉴别力在因果口径下不成立，TASK 35 标注承诺闭环为 ⭕/❌。
跑数前预测申明 [INFERRED, MED]：+45% PF 优势大幅缩水或消失——污染口径的
ZigZag 入场天然聚集在真实好位置，LPS 与"好位置"的相关是搭便车，不是因果。
"""
import json
import os
import sys

import numpy as np
import pandas as pd

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
BT_JSON = os.path.join(BASE, "backtest_results", "causal_v8_extract",
                       "backtest-result-2026-07-07_09-01-42.json")
FEATHER = os.path.join(BASE, "data", "binance", "futures",
                       "BTC_USDT_USDT-1h-futures.feather")
LEAD_H = 24  # 与 TASK 35 原审计一致


def load_trades():
    with open(BT_JSON) as f:
        bt = json.load(f)
    s = bt["strategy"]["WyckoffV8"]
    tr = pd.DataFrame(s["trades"])
    assert len(tr) == 843 and abs(s["profit_total_abs"] - 3187.01) < 1, "基线核验失败"
    tr["open_date"] = pd.to_datetime(tr["open_date"]).dt.tz_localize(None)
    tr["profit_pct"] = tr["profit_ratio"] * 100
    tr["is_win"] = tr["profit_ratio"] > 0
    return tr[~tr["is_short"]].reset_index(drop=True)


def load_ohlcv():
    df = pd.read_feather(FEATHER)
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    df = df.sort_values("date").reset_index(drop=True)
    hi, lo, cl = df["high"], df["low"], df["close"]
    tr = pd.concat([hi - lo, (hi - cl.shift(1)).abs(), (lo - cl.shift(1)).abs()],
                   axis=1).max(axis=1)
    df["atr14"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    df["vol_ma10"] = df["volume"].rolling(10, min_periods=3).mean()
    df["spread"] = hi - lo
    df["move_pct"] = (cl - df["open"]) / df["open"] * 100
    df["is_sos"] = ((cl > df["open"]) & (df["spread"] > df["atr14"] * 0.6) &
                    (df["volume"] > df["vol_ma10"] * 1.2) & (df["move_pct"] > 0.5))
    return df


def lps_events(df, variant):
    """三个变体共用 SOS→LPS 序列骨架，仅 LPS bar 判定条款不同。"""
    sos = df["is_sos"].values
    vol = df["volume"].values
    vma = df["vol_ma10"].values
    spr = df["spread"].values
    atr = df["atr14"].values
    cl = df["close"].values
    op = df["open"].values
    lo = df["low"].values
    hi = df["high"].values
    out = np.zeros(len(df), dtype=bool)
    for i in range(6, len(df)):
        if not cl[i] < op[i]:                       # 阴线（三臂共用）
            continue
        if variant == "A":
            if not (vol[i] < vma[i] * 0.7 and spr[i] < atr[i] * 0.5):
                continue
        else:                                        # B1/B2：量<前两根，无均量/ATR条款
            if not (vol[i] < vol[i - 1] and vol[i] < vol[i - 2]):
                continue
            if variant == "B2":
                rng = hi[i] - lo[i]
                if rng <= 0 or (cl[i] - lo[i]) / rng < 0.5:   # 收盘偏高
                    continue
        for j in range(i - 1, max(i - 7, -1), -1):   # 1~6根内最近 SOS
            if sos[j]:
                if spr[i] < spr[j] * 0.7 and cl[i] >= lo[j] * 0.99:
                    out[i] = True
                break
    return df.loc[out, "date"].reset_index(drop=True)


def pf(x):
    w = x[x > 0].sum()
    l = -x[x < 0].sum()
    return w / l if l > 0 else float("inf")


def stratify(longs, ev_dates, label):
    ev = ev_dates.values
    has = np.zeros(len(longs), dtype=bool)
    for k, dt in enumerate(longs["open_date"].values):
        m = (ev >= dt - np.timedelta64(LEAD_H, "h")) & (ev < dt)
        has[k] = m.any()
    a, b = longs[has], longs[~has]
    d_mean = a["profit_pct"].mean() - b["profit_pct"].mean()
    d_pf = pf(a["profit_pct"]) - pf(b["profit_pct"])
    mid = longs["open_date"].iloc[len(longs) // 2]
    deltas_half = []
    for half in (longs["open_date"] < mid, longs["open_date"] >= mid):
        ah, bh = longs[half & pd.Series(has, index=longs.index)], \
                 longs[half & ~pd.Series(has, index=longs.index)]
        deltas_half.append(ah["profit_pct"].mean() - bh["profit_pct"].mean()
                           if len(ah) and len(bh) else np.nan)
    s1 = len(a) >= 30
    s2 = (d_mean > 0) and (d_pf > 0)
    s3 = (not any(np.isnan(deltas_half))) and \
         (np.sign(deltas_half[0]) == np.sign(deltas_half[1]) == np.sign(d_mean))
    print(f"\n===== 臂 {label}：LPS组 n={len(a)} / 非LPS组 n={len(b)} =====")
    print(f"  胜率      {a['is_win'].mean()*100:5.1f}% vs {b['is_win'].mean()*100:5.1f}%")
    print(f"  平均利润  {a['profit_pct'].mean():+.3f}% vs {b['profit_pct'].mean():+.3f}%"
          f"  (delta {d_mean:+.3f}pp)")
    print(f"  中位利润  {a['profit_pct'].median():+.3f}% vs {b['profit_pct'].median():+.3f}%")
    print(f"  利润因子  {pf(a['profit_pct']):.2f} vs {pf(b['profit_pct']):.2f}"
          f"  (delta {d_pf:+.2f})")
    print(f"  对半切 delta: H1 {deltas_half[0]:+.3f}pp / H2 {deltas_half[1]:+.3f}pp")
    print(f"  S1 n≥30: {'PASS' if s1 else 'FAIL'} | S2 双delta>0: "
          f"{'PASS' if s2 else 'FAIL'} | S3 对半同号: {'PASS' if s3 else 'FAIL'}"
          f"  → {'存活' if (s1 and s2 and s3) else '不成立'}")
    return s1 and s2 and s3


def main():
    longs = load_trades()
    print(f"因果口径多头: {len(longs)} 笔 "
          f"({longs['open_date'].min()} ~ {longs['open_date'].max()})，"
          f"整体胜率 {longs['is_win'].mean()*100:.1f}%，"
          f"平均利润 {longs['profit_pct'].mean():+.3f}%")
    df = load_ohlcv()
    verdicts = {}
    for v in ("A", "B1", "B2"):
        ev = lps_events(df, v)
        print(f"\n臂 {v}: LPS 事件总数 {len(ev)}")
        verdicts[v] = stratify(longs, ev, v)
    print("\n" + "=" * 60)
    alive = [v for v, ok in verdicts.items() if ok]
    print(f"总判定: {'存活臂 ' + ','.join(alive) if alive else '全部不成立'} → "
          f"{'LPS 分层鉴别力在因果口径下部分存活，进入下一层评估' if alive else 'TASK 35 标注承诺闭环：+45% PF 优势为污染口径伪影'}")


if __name__ == "__main__":
    main()
