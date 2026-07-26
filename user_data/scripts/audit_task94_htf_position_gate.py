"""
TASK 94（2026-07-11）：高时间框架位置门控 Donchian 突破——TASK 93 副产品独立立项

预注册见 quant_system_tasks.md TASK 94（判据跑数前冻结）。纯数据审计：
不动策略代码，不跑 Freqtrade 回测。

假设：Donchian 20/10 日线多头突破若发生在 90 天价格区间高位（信号日收盘
> lo90 + 0.85×(hi90−lo90)），为大结构顶部的 markup 自由空气突破，期望优于
发生在区间深处（大结构 Phase B 内反弹进供应区）的突破。

构造（冻结）：
  - 15 币日线 futures（TASK 83 宇宙），Donchian 20/10 多头状态机同 TASK 93
    （收盘>前20日高→次日开盘入；收盘<前10日低→次日开盘出；末端截断按末根
    收盘平仓，笔数打印），per-trade 毛收益
  - 事件预热 180 根日线 bar（保证 W=180 哨兵臂与主臂事件集一致）
  - 位置变量：hi_W/lo_W = rolling(W).max/min().shift(1)（严格因果；W 按
    bar 计数，为日历天工程近似）；HIGH ⟺ 信号日收盘 > lo_W + Q×(hi_W−lo_W)
  - 主判臂 W=90/Q=0.85；哨兵 Q∈{0.75,0.95}@W=90、W∈{60,180}@Q=0.85
  - V5 准 OOS：剔除 TASK 93 发现集（BTC/ETH ≥2021-04-01、SOL ≥2022-04-01、
    ADA/BNB/DOT ∈ [2023-04-01, 2026-01-13]）
  - V6 慢通道冗余对照：非 90d 新高子集（收盘 ≤ hi_90）内门控仍须 delta>0

预注册判据：V0 LOW占比∈[15%,85%] / S1 两臂n≥30 / V1 delta>0 且 PF delta>0
且 CI 排0 且（delta≥1.0pp 或 LOW臂≤0）/ V2 对半切同号 / V3 计票币≥2/3 同号
/ V4 四哨兵全同号 / V5 准OOS delta>0 且两臂n≥30 / V6 子集 delta>0。
判定映射：全过→策略集成候选（仍需三层+OOS）；V1 CI排零且负→❌；其余→⭕。
"""
import os
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
FUT = os.path.join(BASE, "data", "binance", "futures")
COINS = ["BTC", "ETH", "BNB", "XRP", "ADA", "SOL", "DOGE", "DOT",
         "LTC", "LINK", "AVAX", "TRX", "BCH", "ETC", "ATOM"]
W_PRIMARY, Q_PRIMARY = 90, 0.85
Q_SENTINELS = (0.75, 0.95)
W_SENTINELS = (60, 180)
WARMUP_BARS = 180
RNG = np.random.default_rng(42)
N_BOOT = 10_000
# TASK 93 发现集窗口（coin: (start, end)），信号日落入即剔除出准 OOS
DISCOVERY = {
    "BTC": ("2021-04-01", "2026-12-31"),
    "ETH": ("2021-04-01", "2026-12-31"),
    "SOL": ("2022-04-01", "2026-12-31"),
    "ADA": ("2023-04-01", "2026-01-13"),
    "BNB": ("2023-04-01", "2026-01-13"),
    "DOT": ("2023-04-01", "2026-01-13"),
}


def donchian_trades(daily):
    """[(signal_idx, gross_ret, truncated)]，状态机同 TASK 93"""
    hi20 = daily["high"].rolling(20).max().shift(1)
    lo10 = daily["low"].rolling(10).min().shift(1)
    trades, pos, sig_i = [], False, None
    for i in range(21, len(daily)):
        if not pos and daily["close"].iloc[i] > hi20.iloc[i]:
            if i + 1 >= len(daily):
                break
            pos, sig_i = True, i
        elif pos and daily["close"].iloc[i] < lo10.iloc[i]:
            entry = daily["open"].iloc[sig_i + 1]
            exit_p = daily["open"].iloc[i + 1] if i + 1 < len(daily) \
                else daily["close"].iloc[i]
            trades.append((sig_i, exit_p / entry - 1.0, i + 1 >= len(daily)))
            pos = False
    if pos:
        entry = daily["open"].iloc[sig_i + 1]
        trades.append((sig_i, daily["close"].iloc[-1] / entry - 1.0, True))
    return trades


def pf(x):
    w = x[x > 0].sum()
    l = -x[x < 0].sum()
    return w / l if l > 0 else float("inf")


def arm_delta(rets, high):
    a, b = rets[high], rets[~high]
    if len(a) == 0 or len(b) == 0:
        return np.nan, np.nan, np.nan
    return a.mean() - b.mean(), a.mean(), b.mean()


def main():
    rows, n_trunc_total = [], 0
    windows = sorted({W_PRIMARY, *W_SENTINELS})
    for coin in COINS:
        df = pd.read_feather(
            os.path.join(FUT, f"{coin}_USDT_USDT-1d-futures.feather"))
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
        df = df.sort_values("date").reset_index(drop=True)
        hw = {w: df["high"].rolling(w).max().shift(1) for w in windows}
        lw = {w: df["low"].rolling(w).min().shift(1) for w in windows}
        n_ev = 0
        for sig_i, ret, trunc in donchian_trades(df):
            if sig_i < WARMUP_BARS:
                continue
            close = df["close"].iloc[sig_i]
            row = {"coin": coin, "date": df["date"].iloc[sig_i],
                   "ret": ret * 100, "trunc": trunc,
                   "not_new_high90": close <= hw[90].iloc[sig_i]}
            for w in windows:
                rng_w = hw[w].iloc[sig_i] - lw[w].iloc[sig_i]
                row[f"pos{w}"] = ((close - lw[w].iloc[sig_i]) / rng_w
                                  if rng_w > 0 else np.nan)
            rows.append(row)
            n_ev += 1
            n_trunc_total += trunc
        print(f"{coin}: 事件 {n_ev}")
    ev = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    assert not ev[[f"pos{w}" for w in windows]].isna().any().any(), \
        "位置变量含 NaN（区间宽度为零？）"
    rets = ev["ret"].values
    high = (ev["pos90"] > Q_PRIMARY).values
    n_a, n_b = int(high.sum()), int((~high).sum())
    print(f"\n混池事件 {len(ev)}（截断平仓 {n_trunc_total}），"
          f"HIGH {n_a} / LOW {n_b}（LOW 占比 {n_b / len(ev) * 100:.1f}%）")

    # ── V0 / S1 ─────────────────────────────────────────────
    v0 = 0.15 <= n_b / len(ev) <= 0.85
    s1 = n_a >= 30 and n_b >= 30
    print(f"V0 非退化: {'PASS' if v0 else 'FAIL'} | S1 两臂n≥30: "
          f"{'PASS' if s1 else 'FAIL'}")

    # ── V1 效应（主臂 W=90/Q=0.85）──────────────────────────
    d, ma, mb = arm_delta(rets, high)
    pf_a, pf_b = pf(rets[high]), pf(rets[~high])
    boots = np.array([
        RNG.choice(rets[high], n_a).mean() - RNG.choice(rets[~high], n_b).mean()
        for _ in range(N_BOOT)])
    ci = np.percentile(boots, [2.5, 97.5])
    v1 = (d > 0 and pf_a - pf_b > 0 and not (ci[0] <= 0 <= ci[1])
          and (d >= 1.0 or mb <= 0))
    print(f"\nV1 主臂: HIGH 均值 {ma:+.3f}pp (PF {pf_a:.2f}) vs "
          f"LOW {mb:+.3f}pp (PF {pf_b:.2f})")
    print(f"   delta {d:+.3f}pp, bootstrap95CI [{ci[0]:+.3f}, {ci[1]:+.3f}]"
          f" → {'PASS' if v1 else 'FAIL'}")

    # ── V2 对半切 ───────────────────────────────────────────
    mid = ev["date"].iloc[len(ev) // 2]
    halves = []
    for m in (ev["date"] < mid, ev["date"] >= mid):
        dh, _, _ = arm_delta(rets[m.values], high[m.values])
        halves.append(dh)
    v2 = (not any(np.isnan(halves))
          and np.sign(halves[0]) == np.sign(halves[1]) == np.sign(d))
    print(f"V2 对半切: H1 {halves[0]:+.3f}pp / H2 {halves[1]:+.3f}pp"
          f" → {'PASS' if v2 else 'FAIL'}")

    # ── V3 跨币 ────────────────────────────────────────────
    signs, detail = [], []
    for coin in COINS:
        m = (ev["coin"] == coin).values
        ca, cb = int((m & high).sum()), int((m & ~high).sum())
        dc, _, _ = arm_delta(rets[m], high[m])
        vote = ca >= 5 and cb >= 5
        detail.append(f"   {coin}: n={ca}/{cb}, delta {dc:+.3f}pp"
                      f"{'' if vote else '（样本不足，不计票）'}")
        if vote:
            signs.append(np.sign(dc) == np.sign(d))
    print("V3 跨币:")
    print("\n".join(detail))
    v3 = len(signs) > 0 and sum(signs) / len(signs) >= 2 / 3
    print(f"   计票币 {len(signs)} 个，同号 {sum(signs)}"
          f" → {'PASS' if v3 else 'FAIL'}")

    # ── V4 参数哨兵 ─────────────────────────────────────────
    v4_ok = []
    for q in Q_SENTINELS:
        h = (ev["pos90"] > q).values
        dq, _, _ = arm_delta(rets, h)
        same = np.sign(dq) == np.sign(d)
        v4_ok.append(same)
        print(f"V4 Q={q}@W=90: n={int(h.sum())}/{int((~h).sum())}, "
              f"delta {dq:+.3f}pp（{'同号' if same else '异号'}）")
    for w in W_SENTINELS:
        h = (ev[f"pos{w}"] > Q_PRIMARY).values
        dw, _, _ = arm_delta(rets, h)
        same = np.sign(dw) == np.sign(d)
        v4_ok.append(same)
        print(f"V4 W={w}@Q=0.85: n={int(h.sum())}/{int((~h).sum())}, "
              f"delta {dw:+.3f}pp（{'同号' if same else '异号'}）")
    v4 = all(v4_ok)
    print(f"V4 参数稳健 → {'PASS' if v4 else 'FAIL'}")

    # ── V5 准 OOS（剔除 TASK 93 发现集）────────────────────
    in_disc = np.zeros(len(ev), dtype=bool)
    for coin, (t0, t1) in DISCOVERY.items():
        in_disc |= ((ev["coin"] == coin)
                    & (ev["date"] >= pd.Timestamp(t0))
                    & (ev["date"] <= pd.Timestamp(t1))).values
    oos = ~in_disc
    d_oos, ma_o, mb_o = arm_delta(rets[oos], high[oos])
    n_oa = int((oos & high).sum())
    n_ob = int((oos & ~high).sum())
    v5 = d_oos > 0 and n_oa >= 30 and n_ob >= 30
    print(f"V5 准OOS（剔除发现集 {int(in_disc.sum())} 事件，余 {int(oos.sum())}）: "
          f"HIGH {ma_o:+.3f}pp (n={n_oa}) vs LOW {mb_o:+.3f}pp (n={n_ob}), "
          f"delta {d_oos:+.3f}pp → {'PASS' if v5 else 'FAIL'}")

    # ── V6 慢通道冗余对照 ───────────────────────────────────
    sub = ev["not_new_high90"].values
    d_sub, ma_s, mb_s = arm_delta(rets[sub], high[sub])
    n_sa = int((sub & high).sum())
    n_sb = int((sub & ~high).sum())
    v6 = not np.isnan(d_sub) and d_sub > 0
    print(f"V6 非90d新高子集（n={int(sub.sum())}，HIGH {n_sa}/LOW {n_sb}）: "
          f"HIGH {ma_s:+.3f}pp vs LOW {mb_s:+.3f}pp, delta {d_sub:+.3f}pp"
          f" → {'PASS' if v6 else 'FAIL（鉴别力全部来自90d新高对齐→冗余）'}")

    # ── 判定映射 ────────────────────────────────────────────
    print("\n" + "=" * 62)
    all_pass = v0 and s1 and v1 and v2 and v3 and v4 and v5 and v6
    neg = (not (ci[0] <= 0 <= ci[1])) and d < 0
    if all_pass:
        verdict = ("全判据通过 → 策略集成候选"
                   "（仍需组合级回测三层校验+OOS，本审计不构成采纳）")
    elif neg:
        verdict = "❌ 机制性反向（CI 排零且 delta<0）"
    else:
        verdict = "⭕ 不成立（未全过且无反向机制证据）"
    print(f"总判定: {verdict}")


if __name__ == "__main__":
    main()
