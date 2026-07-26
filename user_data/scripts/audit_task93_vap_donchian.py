"""
TASK 93（2026-07-11）：VAP 筹码密集区 × Donchian 突破定性——供应重现过滤电池

预注册见 quant_system_tasks.md TASK 93（判据跑数前冻结）。纯数据审计：
不动策略代码，不跑 Freqtrade 回测。

假设：Donchian 20/10 日线多头突破时，若突破日收盘仍 ≤ 近 90 天 VAP 价值
区间上沿（VAH），上方存在已接受的密集成交价值区=潜在供应重现区，per-trade
期望劣于已越过 VAH 的 CLEAR 突破。

构造（冻结）：
  - 1h futures 6 币（BTC/ETH/SOL/ADA/BNB/DOT），日线由 1h 重采样（UTC 日界）
  - VAP 窗口：突破日 00:00 UTC 之前 N 天的 1h bar（严格因果，主判 N=90，
    哨兵 N=60/180；N=180 臂仅含预热 ≥180 天的事件子集，样本单独报告）
  - 价格箱：对数等宽 0.25%；bar 量按 [low, high] 在对数价格上均匀分配
    （0.25% 箱宽下 log-均匀 vs 价格均匀差异可忽略，声明为已知近似）
  - VPOC=最大量箱；价值区间=自 VPOC 双向单箱贪心扩张至累积 ≥70% 总量；
    VAH=价值区间最高箱的上沿
  - 分类：突破日收盘 > VAH → CLEAR，否则 BLOCKED
  - 交易：收盘>前20日高（平仓状态下）→ 次日开盘入场；收盘<前10日低 →
    次日开盘出场；数据截断时按末根收盘平仓（笔数单独打印）；毛收益
    （两臂成本相同，分层 delta 免成本项）
  - 事件预热：突破日 00:00 − 90 天 ≥ 该币 1h 首根时间（保证窗口满覆盖；
    预注册可行性估算 122 个用的是 +95 天粗筛，本处为精确口径，实际数
    以本脚本输出为准）
  - V5 对照臂：同事件集，纯价格分类——突破日收盘 > lo90+0.85×(hi90−lo90)
    （hi90/lo90 = N=90 窗口 1h high 最大值 / low 最小值），零量能信息

预注册判据：
  V0 非退化：BLOCKED 占比 ∈ [15%, 85%]
  S1 样本：混池两臂各 n≥30
  V1 效应：delta(CLEAR−BLOCKED 平均毛收益)>0 且 PF delta>0 且 bootstrap
     95% CI 排 0；效应量下限 delta≥+1.0pp 或 BLOCKED 臂期望 ≤0
  V2 时间稳健：按入场日期对半切两半 delta 同号
  V3 跨币稳健：两臂各 ≥5 事件的币中 ≥2/3 同号
  V4 视界稳健：N=60/180 哨兵 delta 与 N=90 主臂同号（禁止测其他 N）
  V5 冗余对照：纯价格臂 delta ≥ VAP 主臂 delta → ⭕ 冗余
判定映射：全过→策略集成候选（仍需三层+OOS）；V1 CI 排零且方向为负→❌；
其余→⭕。跑数前预测 [INFERRED, MED]：⭕ 冗余（V5 是最可能死处）。
"""
import os
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DATA = os.path.join(BASE, "data", "binance", "futures")
COINS = ["BTC", "ETH", "SOL", "ADA", "BNB", "DOT"]
BIN_STEP = np.log(1.0025)          # 0.25% 对数箱
VA_PCT = 0.70
N_PRIMARY, N_SENTINELS = 90, (60, 180)
CTRL_Q = 0.85
RNG = np.random.default_rng(42)
N_BOOT = 10_000


def load_coin(coin):
    df = pd.read_feather(os.path.join(DATA, f"{coin}_USDT_USDT-1h-futures.feather"))
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    return df.sort_values("date").reset_index(drop=True)


def donchian_trades(daily):
    """Donchian 20/10 多头，状态机；返回 [(signal_day, entry_open, gross_ret, truncated)]"""
    hi20 = daily["high"].rolling(20).max().shift(1)
    lo10 = daily["low"].rolling(10).min().shift(1)
    trades, pos, sig_i = [], False, None
    for i in range(21, len(daily)):
        if not pos and daily["close"].iloc[i] > hi20.iloc[i]:
            if i + 1 >= len(daily):
                break                      # 无次日开盘，事件不成立
            pos, sig_i = True, i
        elif pos and daily["close"].iloc[i] < lo10.iloc[i]:
            entry = daily["open"].iloc[sig_i + 1]
            exit_p = daily["open"].iloc[i + 1] if i + 1 < len(daily) \
                else daily["close"].iloc[i]
            trades.append((daily["date"].iloc[sig_i], entry,
                           exit_p / entry - 1.0, i + 1 >= len(daily)))
            pos = False
    if pos:                                # 数据末端仍持仓 → 末根收盘平仓
        entry = daily["open"].iloc[sig_i + 1]
        trades.append((daily["date"].iloc[sig_i], entry,
                       daily["close"].iloc[-1] / entry - 1.0, True))
    return trades


def profile_stats(h1, day_start, n_days):
    """N 天窗口 VAP → (vah, hi_n, lo_n)；窗口未满覆盖返回 None"""
    t0 = day_start - pd.Timedelta(days=n_days)
    if t0 < h1["date"].iloc[0]:
        return None
    i0, i1 = h1["date"].searchsorted(t0), h1["date"].searchsorted(day_start)
    lo = h1["low"].values[i0:i1]
    hi = h1["high"].values[i0:i1]
    vol = h1["volume"].values[i0:i1]
    if len(lo) == 0 or vol.sum() <= 0:
        return None
    llo, lhi = np.log(lo), np.log(np.maximum(hi, lo))
    k_lo = np.floor(llo / BIN_STEP).astype(int)
    k_hi = np.floor(lhi / BIN_STEP).astype(int)
    kmin, kmax = k_lo.min(), k_hi.max()
    hist = np.zeros(kmax - kmin + 1)
    single = k_lo == k_hi
    np.add.at(hist, k_lo[single] - kmin, vol[single])
    for j in np.nonzero(~single)[0]:
        span = lhi[j] - llo[j]
        for k in range(k_lo[j], k_hi[j] + 1):
            ov = min(lhi[j], (k + 1) * BIN_STEP) - max(llo[j], k * BIN_STEP)
            hist[k - kmin] += vol[j] * ov / span
    # VPOC 双向单箱贪心扩张至 70%
    poc = int(hist.argmax())
    total, acc = hist.sum(), hist[poc]
    a = b = poc                            # [a, b] 已含区间
    while acc < VA_PCT * total:
        up = hist[b + 1] if b + 1 < len(hist) else -1.0
        dn = hist[a - 1] if a - 1 >= 0 else -1.0
        if up >= dn:
            b += 1; acc += up
        else:
            a -= 1; acc += dn
    vah = np.exp((b + kmin + 1) * BIN_STEP)   # 最高箱上沿
    return vah, hi.max(), lo.min()


def pf(x):
    w = x[x > 0].sum()
    l = -x[x < 0].sum()
    return w / l if l > 0 else float("inf")


def arm_delta(rets, clear):
    a, b = rets[clear], rets[~clear]
    if len(a) == 0 or len(b) == 0:
        return np.nan, np.nan, np.nan
    return a.mean() - b.mean(), a.mean(), b.mean()


def main():
    rows = []
    for coin in COINS:
        h1 = load_coin(coin)
        daily = (h1.set_index("date")
                 .resample("1D")
                 .agg({"open": "first", "high": "max", "low": "min",
                       "close": "last", "volume": "sum"})
                 .dropna().reset_index())
        n_trunc = 0
        for sig_day, entry, ret, trunc in donchian_trades(daily):
            p90 = profile_stats(h1, sig_day, N_PRIMARY)
            if p90 is None:
                continue                   # 预热不足，事件剔除
            vah90, hi90, lo90 = p90
            close_sig = daily.loc[daily["date"] == sig_day, "close"].iloc[0]
            row = {"coin": coin, "date": sig_day, "ret": ret * 100,
                   "clear90": close_sig > vah90,
                   "ctrl": close_sig > lo90 + CTRL_Q * (hi90 - lo90),
                   "trunc": trunc}
            for n in N_SENTINELS:
                p = profile_stats(h1, sig_day, n)
                row[f"clear{n}"] = (close_sig > p[0]) if p is not None else None
            rows.append(row)
            n_trunc += trunc
        print(f"{coin}: 事件 {sum(r['coin'] == coin for r in rows)}"
              f"（含截断平仓 {n_trunc}）")
    ev = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    rets, clear = ev["ret"].values, ev["clear90"].values.astype(bool)
    n_a, n_b = int(clear.sum()), int((~clear).sum())
    print(f"\n混池事件 {len(ev)}，CLEAR {n_a} / BLOCKED {n_b}"
          f"（BLOCKED 占比 {n_b / len(ev) * 100:.1f}%）")

    # ── V0 / S1 ─────────────────────────────────────────────
    v0 = 0.15 <= n_b / len(ev) <= 0.85
    s1 = n_a >= 30 and n_b >= 30
    print(f"V0 非退化: {'PASS' if v0 else 'FAIL'} | S1 两臂n≥30: "
          f"{'PASS' if s1 else 'FAIL'}")

    # ── V1 效应（主臂 N=90）────────────────────────────────
    d, ma, mb = arm_delta(rets, clear)
    pf_a, pf_b = pf(rets[clear]), pf(rets[~clear])
    boots = np.array([
        RNG.choice(rets[clear], n_a).mean() - RNG.choice(rets[~clear], n_b).mean()
        for _ in range(N_BOOT)])
    ci = np.percentile(boots, [2.5, 97.5])
    v1 = (d > 0 and pf_a - pf_b > 0 and not (ci[0] <= 0 <= ci[1])
          and (d >= 1.0 or mb <= 0))
    print(f"\nV1 主臂 N=90: CLEAR 均值 {ma:+.3f}pp (PF {pf_a:.2f}) vs "
          f"BLOCKED {mb:+.3f}pp (PF {pf_b:.2f})")
    print(f"   delta {d:+.3f}pp, bootstrap95CI [{ci[0]:+.3f}, {ci[1]:+.3f}]"
          f" → {'PASS' if v1 else 'FAIL'}")

    # ── V2 对半切 ───────────────────────────────────────────
    mid = ev["date"].iloc[len(ev) // 2]
    halves = []
    for m in (ev["date"] < mid, ev["date"] >= mid):
        dh, _, _ = arm_delta(rets[m.values], clear[m.values])
        halves.append(dh)
    v2 = (not any(np.isnan(halves))
          and np.sign(halves[0]) == np.sign(halves[1]) == np.sign(d))
    print(f"V2 对半切: H1 {halves[0]:+.3f}pp / H2 {halves[1]:+.3f}pp"
          f" → {'PASS' if v2 else 'FAIL'}")

    # ── V3 跨币 ────────────────────────────────────────────
    signs, detail = [], []
    for coin in COINS:
        m = (ev["coin"] == coin).values
        ca = (m & clear).sum()
        cb = (m & ~clear).sum()
        dc, _, _ = arm_delta(rets[m], clear[m])
        detail.append(f"   {coin}: n={ca}/{cb}, delta "
                      f"{dc:+.3f}pp{'' if ca >= 5 and cb >= 5 else '（样本不足，不计票）'}")
        if ca >= 5 and cb >= 5:
            signs.append(np.sign(dc) == np.sign(d))
    print("V3 跨币:")
    print("\n".join(detail))
    v3 = len(signs) > 0 and sum(signs) / len(signs) >= 2 / 3
    print(f"   计票币 {len(signs)} 个，同号 {sum(signs)}"
          f" → {'PASS' if v3 else 'FAIL'}")

    # ── V4 视界哨兵 ─────────────────────────────────────────
    v4_ok = []
    for n in N_SENTINELS:
        m = ev[f"clear{n}"].notna().values
        cn = np.array([bool(x) if x is not None else False
                       for x in ev[f"clear{n}"]])
        dn_, _, _ = arm_delta(rets[m], cn[m])
        same = np.sign(dn_) == np.sign(d)
        v4_ok.append(same)
        print(f"V4 N={n}: 事件 {int(m.sum())}, delta {dn_:+.3f}pp"
              f"（{'同号' if same else '异号'}）")
    v4 = all(v4_ok)
    print(f"V4 视界稳健 → {'PASS' if v4 else 'FAIL'}")

    # ── V5 纯价格对照 ───────────────────────────────────────
    ctrl = ev["ctrl"].values.astype(bool)
    d_ctrl, _, _ = arm_delta(rets, ctrl)
    v5 = not np.isnan(d_ctrl) and d > d_ctrl
    print(f"V5 对照臂（收盘>90d价格85分位, n={int(ctrl.sum())}/"
          f"{int((~ctrl).sum())}）: delta {d_ctrl:+.3f}pp vs VAP {d:+.3f}pp"
          f" → {'PASS（VAP 有量能增量）' if v5 else 'FAIL（冗余）'}")

    # ── 判定映射 ────────────────────────────────────────────
    print("\n" + "=" * 62)
    all_pass = v0 and s1 and v1 and v2 and v3 and v4 and v5
    neg = (not (ci[0] <= 0 <= ci[1])) and d < 0
    if all_pass:
        verdict = "全判据通过 → 策略集成候选（仍需三层校验+OOS，本审计不构成采纳）"
    elif neg:
        verdict = "❌ 机制性反向（CI 排零且 delta<0）：CLEAR 突破期望反而更差"
    else:
        verdict = "⭕ 不成立（未全过且无反向机制证据）"
    print(f"总判定: {verdict}")


if __name__ == "__main__":
    main()
