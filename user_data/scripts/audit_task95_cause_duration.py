"""
TASK 95（2026-07-13）：因果律时长分层——盘整基期长度 × Donchian 突破效果
（TASK 47 宿主移植：TR持续时间→PNF乘数 的结构等价，宿主换 Donchian，
 不复活 PNF 目标位框架）

预注册见 wyckoff_v8_redesign.md TASK 95（判据跑数前冻结）。纯数据审计。

假设（威科夫因果律）：信号日前价格离开 20 日通道顶越久（基期 D 越长，
因越大），突破后 per-trade 收益越高（果越大）。

构造（冻结）：
  - 事件集同 TASK 94：15 币日线 Donchian 20/10 多头，180 bar 预热
  - D = 信号日 t − 最近一个"收盘>前20日高"的 bar s（s<t）；无先例剔除报数
  - 主判臂：混池 D 中位切分（HIGH-CAUSE = D>中位）
  - 哨兵：(a) 上/下三分位对比 (b) D 用 10 日通道 (c) D 用 55 日通道
  - V5 剂量响应：Spearman ρ(D, ret) > 0

判据：V0 两臂∈[15%,85%] / S1 n≥30 / V1 delta>0 且 PF delta>0 且 CI排0
且 delta≥1.0pp / V2 对半切同号 / V3 计票币≥2/3 / V4 三哨兵同号 / V5 ρ>0。
映射：全过→集成候选（仍需三层+OOS）；V1 CI排零且负→❌；其余→⭕。
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
WARMUP_BARS = 180
CHANNELS = (10, 20, 55)      # 20 主判，10/55 哨兵
RNG = np.random.default_rng(42)
N_BOOT = 10_000


def donchian_trades(daily):
    """[(signal_idx, gross_ret, truncated)]，状态机同 TASK 93/94"""
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


def cause_duration(df, w):
    """D[t] = t − 最近 s<t 满足 close_s > 前 w 日高；无先例 → NaN"""
    b = (df["close"] > df["high"].rolling(w).max().shift(1)).values
    idx = np.where(b, np.arange(len(b)), -1)
    last = np.maximum.accumulate(idx)
    d = np.full(len(b), np.nan)
    valid = np.arange(len(b)) >= 1
    prev = np.roll(last, 1)          # last[t-1]
    prev[0] = -1
    ok = valid & (prev >= 0)
    d[ok] = np.arange(len(b))[ok] - prev[ok]
    return d


def pf(x):
    w = x[x > 0].sum()
    l = -x[x < 0].sum()
    return w / l if l > 0 else float("inf")


def arm_delta(rets, high):
    a, b = rets[high], rets[~high]
    if len(a) == 0 or len(b) == 0:
        return np.nan, np.nan, np.nan
    return a.mean() - b.mean(), a.mean(), b.mean()


def spearman(x, y):
    rx = pd.Series(x).rank().values
    ry = pd.Series(y).rank().values
    return np.corrcoef(rx, ry)[0, 1]


def main():
    rows, n_drop = [], 0
    for coin in COINS:
        df = pd.read_feather(
            os.path.join(FUT, f"{coin}_USDT_USDT-1d-futures.feather"))
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
        df = df.sort_values("date").reset_index(drop=True)
        dur = {w: cause_duration(df, w) for w in CHANNELS}
        n_ev = 0
        for sig_i, ret, trunc in donchian_trades(df):
            if sig_i < WARMUP_BARS:
                continue
            if np.isnan(dur[20][sig_i]):
                n_drop += 1
                continue
            rows.append({"coin": coin, "date": df["date"].iloc[sig_i],
                         "ret": ret * 100,
                         **{f"d{w}": dur[w][sig_i] for w in CHANNELS}})
            n_ev += 1
        print(f"{coin}: 事件 {n_ev}")
    ev = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    rets = ev["ret"].values
    d20 = ev["d20"].values
    med = np.median(d20)
    high = d20 > med
    n_a, n_b = int(high.sum()), int((~high).sum())
    print(f"\n混池事件 {len(ev)}（无先例剔除 {n_drop}），D20 中位 {med:.0f} bar，"
          f"HIGH-CAUSE {n_a} / LOW-CAUSE {n_b}")
    print(f"D20 分布: P10={np.percentile(d20,10):.0f} P25={np.percentile(d20,25):.0f} "
          f"P50={med:.0f} P75={np.percentile(d20,75):.0f} P90={np.percentile(d20,90):.0f}")

    # ── V0 / S1 ─────────────────────────────────────────────
    v0 = 0.15 <= n_b / len(ev) <= 0.85 and 0.15 <= n_a / len(ev) <= 0.85
    s1 = n_a >= 30 and n_b >= 30
    print(f"V0 非退化: {'PASS' if v0 else 'FAIL'} | S1 两臂n≥30: "
          f"{'PASS' if s1 else 'FAIL'}")

    # ── V1 效应（主臂 D20 中位切分）────────────────────────
    d, ma, mb = arm_delta(rets, high)
    pf_a, pf_b = pf(rets[high]), pf(rets[~high])
    boots = np.array([
        RNG.choice(rets[high], n_a).mean() - RNG.choice(rets[~high], n_b).mean()
        for _ in range(N_BOOT)])
    ci = np.percentile(boots, [2.5, 97.5])
    v1 = (d > 0 and pf_a - pf_b > 0 and not (ci[0] <= 0 <= ci[1]) and d >= 1.0)
    print(f"\nV1 主臂: HIGH-CAUSE 均值 {ma:+.3f}pp (PF {pf_a:.2f}) vs "
          f"LOW-CAUSE {mb:+.3f}pp (PF {pf_b:.2f})")
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

    # ── V4 哨兵 ────────────────────────────────────────────
    v4_ok = []
    q33, q67 = np.percentile(d20, [100 / 3, 200 / 3])
    top, bot = d20 >= q67, d20 <= q33
    d_ter = rets[top].mean() - rets[bot].mean()
    same = np.sign(d_ter) == np.sign(d)
    v4_ok.append(same)
    print(f"V4 三分位对比: 上{int(top.sum())}/下{int(bot.sum())}, "
          f"delta {d_ter:+.3f}pp（{'同号' if same else '异号'}）")
    for w in (10, 55):
        dw_arr = ev[f"d{w}"].values
        m = ~np.isnan(dw_arr)
        hw = dw_arr > np.median(dw_arr[m])
        dw_, _, _ = arm_delta(rets[m], hw[m])
        same = np.sign(dw_) == np.sign(d)
        v4_ok.append(same)
        print(f"V4 D{w} 中位切分: 事件 {int(m.sum())}, delta {dw_:+.3f}pp"
              f"（{'同号' if same else '异号'}）")
    v4 = all(v4_ok)
    print(f"V4 参数哨兵 → {'PASS' if v4 else 'FAIL'}")

    # ── V5 剂量响应 ─────────────────────────────────────────
    rho = spearman(d20, rets)
    v5 = rho > 0
    print(f"V5 剂量响应: Spearman ρ(D20, ret) = {rho:+.4f}"
          f" → {'PASS' if v5 else 'FAIL'}")

    # ── 判定映射 ────────────────────────────────────────────
    print("\n" + "=" * 62)
    all_pass = v0 and s1 and v1 and v2 and v3 and v4 and v5
    neg = (not (ci[0] <= 0 <= ci[1])) and d < 0
    if all_pass:
        verdict = ("全判据通过 → 策略集成候选"
                   "（仍需组合级回测三层校验+OOS，本审计不构成采纳）")
    elif neg:
        verdict = "❌ 机制性反向（CI 排零且 delta<0）：因果律在该宿主上反向"
    else:
        verdict = "⭕ 不成立（未全过且无反向机制证据）"
    print(f"总判定: {verdict}")


if __name__ == "__main__":
    main()
