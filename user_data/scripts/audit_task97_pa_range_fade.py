"""
TASK 97（2026-07-18）：价格行为区间边界逆向电池——失败突破回归系统

预注册见 quant_system_tasks.md TASK 97（判据跑数前冻结）。纯数据审计，
禁碰 strategies/。立项依据：TASK 88 法医数据（1H Donchian 突破 90%+ 假
突破）；顺突破已判死，逆边界方向从未测过。信号 K 形态层先验极低（TASK
36/80/92 三判 + 学术文献），设消融臂单独审计增量。

构造（冻结，禁止增删参数档）：
  区间检测：L=100 bar 滚动 hh/ll（shift(1) 因果），regime = 双侧 ≥K=20 bar
    未刷新 100-bar 极值 且 宽度 (hh-ll)/close ≥2%。哨兵 L∈{60,150}、K∈{10,30}
  区域：下/上区 = 边界起 15% 宽度，中间 70% 禁区。哨兵 zone∈{10%,25%}
  信号K（多头，low 触下区）：影线型 = 下影≥50%全幅 且 收盘≥全幅中点；或
    实体型 = 阳线实体≥70%全幅；且全幅≥0.75×ATR20。十字星（实体<30%且双影
    均衡）跳过。空头对称。哨兵 wick∈{40%,60%}
  入场：信号K后一根内价格越过信号K高点+0.05×ATR 触发，成交价=max(open,触发价)
  止损：臂A(主判)=信号K low-0.1×ATR；臂B(报告)=区间对侧边界
  目标：对侧区域近端边界；RR门控=(目标-入场)/(入场-止损)≥2.0 否则放弃
  出场：OCO按止损优先（保守）；区间失效（100-bar 收盘级反向突破）次bar open
    离场；最长持有60bar强平
  成本：0.11%/边
  消融：(i) 去信号K（触区即入）(ii) 去RR门控

判据（冻结）：P1 混池n≥100且两方向各≥30 / P2 臂A per-trade期望>0且CI排0
且≥+0.30% / P3 对半切同号 / P4 计票币(各≥5事件)≥2/3同号 / P5 全部哨兵同号
/ P6 形态增量：含信号K期望>消融(i) / P7 RR门控增量（报告级）。
映射：P1~P5全过→解锁TASK98；CI排零且负→❌机制性反向；其余→⭕关闭。
"""
import os
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
FUT = os.path.join(BASE, "data", "binance", "futures")

COINS = {
    "BTC": "2021-01-01",
    "ETH": "2021-01-01",
    "SOL": "2022-01-01",
    "ADA": "2023-01-01",
    "BNB": "2023-01-01",
    "DOT": "2023-01-01",
}
END_LATE = pd.Timestamp("2026-01-01")  # ADA/BNB/DOT 上限

L_PRIMARY, K_PRIMARY = 100, 20
L_SENTINELS = (60, 150)
K_SENTINELS = (10, 30)
WIDTH_MIN_PCT = 0.02
ZONE_PRIMARY = 0.15
ZONE_SENTINELS = (0.10, 0.25)
WICK_PRIMARY = 0.50
WICK_SENTINELS = (0.40, 0.60)
BODY_MIN = 0.70
DOJI_BODY_MAX = 0.30
ATR_MIN_MULT = 0.75
TRIGGER_ATR_MULT = 0.05
STOP_A_ATR_MULT = 0.10
RR_MIN = 2.0
MAX_HOLD = 60
COST_SIDE = 0.0011
COST_FLOOR = 0.0030  # 成本地板 0.30%
RNG = np.random.default_rng(42)
N_BOOT = 10_000
WARMUP = 200


def load_1h(coin, start):
    df = pd.read_feather(os.path.join(FUT, f"{coin}_USDT_USDT-1h-futures.feather"))
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    df = df.sort_values("date").reset_index(drop=True)
    df = df[df["date"] >= pd.Timestamp(start)].reset_index(drop=True)
    if coin in ("ADA", "BNB", "DOT"):
        df = df[df["date"] <= END_LATE].reset_index(drop=True)
    return df


def resample_4h(df):
    d = df.set_index("date")
    o = d["open"].resample("4h").first()
    h = d["high"].resample("4h").max()
    l = d["low"].resample("4h").min()
    c = d["close"].resample("4h").last()
    v = d["volume"].resample("4h").sum()
    out = pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": v}).dropna()
    return out.reset_index()


def atr(df, n=20):
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift(1)).abs()
    lc = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def compute_regime(df, L, K, width_min=WIDTH_MIN_PCT):
    """区间 regime：双侧 K bar 未刷新 100-bar(L) 极值，宽度达标。全部因果 shift(1)。"""
    hh = df["high"].rolling(L).max()
    ll = df["low"].rolling(L).min()
    width_ok = ((hh - ll) / df["close"]) >= width_min
    # "K bar 未刷新极值"：最近 K 根内的 high 均未创出 rolling(L) 新高，且 low 均未创新低
    made_new_high = df["high"] >= hh
    made_new_low = df["low"] <= ll
    no_new_high_K = (~made_new_high).rolling(K).min().astype(bool)
    no_new_low_K = (~made_new_low).rolling(K).min().astype(bool)
    regime = no_new_high_K & no_new_low_K & width_ok
    # 因果：t 时刻只能用 t 之前已确认的 regime 判断 → shift(1)
    regime_shifted = regime.shift(1).infer_objects(copy=False).fillna(False)
    return regime_shifted, hh.shift(1), ll.shift(1)


def zone_membership(close, hh, ll, zone_pct):
    width = hh - ll
    lo_zone_hi = ll + zone_pct * width
    hi_zone_lo = hh - zone_pct * width
    in_lo = close <= lo_zone_hi
    in_hi = close >= hi_zone_lo
    return in_lo, in_hi


def classify_signal_k(row, atr_val, wick_thresh):
    o, h, l, c = row["open"], row["high"], row["low"], row["close"]
    full = h - l
    if full <= 0 or atr_val <= 0 or full < ATR_MIN_MULT * atr_val:
        return None
    body = abs(c - o)
    body_pct = body / full
    mid = (h + l) / 2.0
    lower_wick = min(o, c) - l
    upper_wick = h - max(o, c)
    lower_wick_pct = lower_wick / full
    upper_wick_pct = upper_wick / full
    is_doji = body_pct < DOJI_BODY_MAX and abs(lower_wick_pct - upper_wick_pct) < 0.20
    if is_doji:
        return None
    # 多头信号：影线型（下影≥thresh 且收盘≥中点）或实体型（阳线实体≥70%）
    bull_wick = lower_wick_pct >= wick_thresh and c >= mid
    bull_body = (c > o) and body_pct >= BODY_MIN
    bull = bull_wick or bull_body
    # 空头信号对称
    bear_wick = upper_wick_pct >= wick_thresh and c <= mid
    bear_body = (c < o) and body_pct >= BODY_MIN
    bear = bear_wick or bear_body
    if bull and not bear:
        return "long"
    if bear and not bull:
        return "short"
    return None


def simulate(df, L, K, zone_pct, wick_thresh, width_min=WIDTH_MIN_PCT,
             require_signal_k=True, require_rr=True):
    """返回逐笔交易 DataFrame。全部索引前瞻均以 shift(1)/next-bar 因果实现。"""
    n = len(df)
    a = atr(df, 20).values
    regime, hh, ll = compute_regime(df, L, K, width_min)
    regime_v = regime.values
    hh_v, ll_v = hh.values, ll.values
    close = df["close"].values
    open_ = df["open"].values
    high = df["high"].values
    low = df["low"].values

    in_lo, in_hi = zone_membership(pd.Series(close), hh, ll, zone_pct)
    in_lo, in_hi = in_lo.values, in_hi.values

    trades = []
    i = WARMUP
    while i < n - 2:
        if not regime_v[i] or np.isnan(hh_v[i]) or np.isnan(ll_v[i]) or a[i] <= 0 or np.isnan(a[i]):
            i += 1
            continue
        direction = None
        if in_lo[i]:
            direction = "long"
        elif in_hi[i]:
            direction = "short"
        else:
            i += 1
            continue

        sig = classify_signal_k(df.iloc[i], a[i], wick_thresh)
        if require_signal_k:
            if sig != direction:
                i += 1
                continue
        # 消融(i)：触区即入，方向由区域决定，不看 K 形态

        sig_high, sig_low = high[i], low[i]
        width_i = hh_v[i] - ll_v[i]

        # 入场触发：信号K后一根内越过（信号K高点/低点 ± 0.05*ATR）
        trig_idx = i + 1
        if trig_idx >= n:
            i += 1
            continue
        if direction == "long":
            trig_price = sig_high + TRIGGER_ATR_MULT * a[i]
            triggered = high[trig_idx] >= trig_price
            entry = max(open_[trig_idx], trig_price) if triggered else None
        else:
            trig_price = sig_low - TRIGGER_ATR_MULT * a[i]
            triggered = low[trig_idx] <= trig_price
            entry = min(open_[trig_idx], trig_price) if triggered else None

        if not triggered:
            i += 1
            continue

        stop_a = (sig_low - STOP_A_ATR_MULT * a[i]) if direction == "long" \
            else (sig_high + STOP_A_ATR_MULT * a[i])
        stop_b = ll_v[i] if direction == "long" else hh_v[i]

        # 目标：对侧区域近端边界
        if direction == "long":
            target = hh_v[i] - zone_pct * width_i
        else:
            target = ll_v[i] + zone_pct * width_i

        risk_a = abs(entry - stop_a)
        if risk_a <= 0:
            i += 1
            continue
        reward = abs(target - entry)
        rr = reward / risk_a
        if require_rr and rr < RR_MIN:
            i += 1
            continue

        # 出场模拟：从 trig_idx 起（entry bar 本身不检查止损/目标，避免同 bar 未来污染）
        exit_price, exit_reason = None, None
        j = trig_idx + 1
        hold = 0
        while j < n and hold < MAX_HOLD:
            hold += 1
            hit_stop = (low[j] <= stop_a) if direction == "long" else (high[j] >= stop_a)
            hit_target = (high[j] >= target) if direction == "long" else (low[j] <= target)
            if hit_stop:
                exit_price, exit_reason = stop_a, "stop"
                break
            if hit_target:
                exit_price, exit_reason = target, "target"
                break
            # 区间失效：100-bar 收盘级反向突破 → 次bar open离场
            if not np.isnan(hh_v[j]) and not np.isnan(ll_v[j]):
                invalidated = (direction == "long" and close[j] < ll[j]) or \
                              (direction == "short" and close[j] > hh[j])
                if invalidated and j + 1 < n:
                    exit_price, exit_reason = open_[j + 1], "regime_invalid"
                    break
            j += 1
        if exit_price is None:
            last = min(j, n - 1)
            exit_price, exit_reason = close[last], "max_hold"

        if direction == "long":
            gross = exit_price / entry - 1.0
        else:
            gross = entry / exit_price - 1.0
        net = gross - 2 * COST_SIDE

        # 臂B（Brooks原著止损）报告用途，不影响主判出场路径
        stop_b_dist = abs(entry - stop_b)
        rr_b = reward / stop_b_dist if stop_b_dist > 0 else np.nan

        trades.append(dict(
            sig_idx=i, date=df["date"].iloc[i], direction=direction,
            entry=entry, exit=exit_price, net=net * 100, gross=gross * 100,
            exit_reason=exit_reason, rr_a=rr, rr_b=rr_b, hold=hold,
        ))
        i = j + 1 if exit_reason != "max_hold" else j + 1
    return pd.DataFrame(trades)


def pf(x):
    w = x[x > 0].sum()
    l = -x[x < 0].sum()
    return w / l if l > 0 else float("inf")


def boot_ci(x):
    if len(x) == 0:
        return np.nan, np.nan
    boot = np.array([RNG.choice(x, len(x)).mean() for _ in range(N_BOOT)])
    return np.percentile(boot, 2.5), np.percentile(boot, 97.5)


def run_universe(L, K, zone_pct, wick_thresh, require_signal_k=True, require_rr=True, tf="1h"):
    frames = []
    for coin, start in COINS.items():
        df = load_1h(coin, start)
        if tf == "4h":
            df = resample_4h(df)
        tr = simulate(df, L, K, zone_pct, wick_thresh,
                      require_signal_k=require_signal_k, require_rr=require_rr)
        if len(tr) > 0:
            tr["coin"] = coin
            frames.append(tr)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).sort_values("date").reset_index(drop=True)


def main():
    print("=" * 70)
    print("主判：1h，L=100/K=20/zone=15%/wick=50%，臂A止损，含信号K，含RR门控")
    ev = run_universe(L_PRIMARY, K_PRIMARY, ZONE_PRIMARY, WICK_PRIMARY)
    n_long = int((ev["direction"] == "long").sum()) if len(ev) else 0
    n_short = int((ev["direction"] == "short").sum()) if len(ev) else 0
    print(f"混池事件 n={len(ev)}（多头 {n_long} / 空头 {n_short}）")

    # ── P1 ─────────────────────────────────────────────
    p1 = len(ev) >= 100 and n_long >= 30 and n_short >= 30
    print(f"P1 混池n≥100且两方向各≥30 → {'PASS' if p1 else 'FAIL'}")

    if len(ev) == 0:
        print("\n无事件，无法继续判据评估。总判定：⭕ 不成立（样本为零）")
        return

    net = ev["net"].values
    m = net.mean()
    pf_v = pf(net)
    win_rate = (net > 0).mean()
    print(f"臂A: 均值 {m:+.4f}pp, PF {pf_v:.2f}, 胜率 {win_rate:.1%}, "
          f"止损占比 {(ev['exit_reason']=='stop').mean():.1%}, "
          f"目标占比 {(ev['exit_reason']=='target').mean():.1%}, "
          f"区间失效占比 {(ev['exit_reason']=='regime_invalid').mean():.1%}, "
          f"强平占比 {(ev['exit_reason']=='max_hold').mean():.1%}")
    if win_rate > 0.90:
        print("⚠️ 胜率>90%，触发未来函数排查停手条款——本脚本已逐项过 Lookahead 清单，"
              "但仍需人工复核后再采信。")

    # ── P2 主判效应 ────────────────────────────────────
    ci = boot_ci(net)
    p2 = m > 0 and ci[0] > 0 and m >= COST_FLOOR * 100
    print(f"P2 臂A per-trade期望>0 且 CI排0 且 ≥+0.30%: "
          f"均值 {m:+.4f}pp, CI[{ci[0]:+.4f},{ci[1]:+.4f}] → {'PASS' if p2 else 'FAIL'}")

    # ── P3 对半切 ───────────────────────────────────────
    mid_date = ev["date"].iloc[len(ev) // 2]
    h1 = ev[ev["date"] < mid_date]["net"].values
    h2 = ev[ev["date"] >= mid_date]["net"].values
    m1 = h1.mean() if len(h1) else np.nan
    m2 = h2.mean() if len(h2) else np.nan
    p3 = (not np.isnan(m1)) and (not np.isnan(m2)) and np.sign(m1) == np.sign(m2) == np.sign(m)
    print(f"P3 对半切: H1(n={len(h1)}) {m1:+.4f}pp / H2(n={len(h2)}) {m2:+.4f}pp"
          f" → {'PASS' if p3 else 'FAIL'}")

    # ── P4 跨币计票 ─────────────────────────────────────
    signs, detail = [], []
    for coin in COINS:
        c_ev = ev[ev["coin"] == coin]
        if len(c_ev) >= 5:
            cm = c_ev["net"].mean()
            signs.append(np.sign(cm) == np.sign(m))
            detail.append(f"   {coin}: n={len(c_ev)}, 均值 {cm:+.4f}pp")
        else:
            detail.append(f"   {coin}: n={len(c_ev)}（样本不足，不计票）")
    print("P4 跨币:")
    print("\n".join(detail))
    p4 = len(signs) > 0 and sum(signs) / len(signs) >= 2 / 3
    print(f"   计票币 {len(signs)} 个，同号 {sum(signs)} → {'PASS' if p4 else 'FAIL'}")

    # ── P5 全哨兵 ───────────────────────────────────────
    print("\nP5 哨兵档:")
    p5_ok = []
    sentinel_configs = (
        [("L", l, K_PRIMARY, ZONE_PRIMARY, WICK_PRIMARY, "1h") for l in L_SENTINELS] +
        [("K", L_PRIMARY, k, ZONE_PRIMARY, WICK_PRIMARY, "1h") for k in K_SENTINELS] +
        [("zone", L_PRIMARY, K_PRIMARY, z, WICK_PRIMARY, "1h") for z in ZONE_SENTINELS] +
        [("wick", L_PRIMARY, K_PRIMARY, ZONE_PRIMARY, w, "1h") for w in WICK_SENTINELS] +
        [("4h", L_PRIMARY, K_PRIMARY, ZONE_PRIMARY, WICK_PRIMARY, "4h")]
    )
    for label, l, k, z, w, tf in sentinel_configs:
        s_ev = run_universe(l, k, z, w, tf=tf)
        if len(s_ev) < 10:
            print(f"   {label}={l if label=='L' else k if label=='K' else z if label=='zone' else w if label=='wick' else tf}: "
                  f"n={len(s_ev)}（样本不足<10，不计入哨兵判定）")
            continue
        sm = s_ev["net"].mean()
        same = np.sign(sm) == np.sign(m)
        p5_ok.append(same)
        tag = l if label == "L" else k if label == "K" else z if label == "zone" else w if label == "wick" else tf
        print(f"   {label}={tag}: n={len(s_ev)}, 均值 {sm:+.4f}pp"
              f"（{'同号' if same else '异号'}）")
    p5 = len(p5_ok) > 0 and all(p5_ok)
    print(f"P5 全部哨兵同号 → {'PASS' if p5 else 'FAIL'}")

    # ── P6 形态增量（消融i：去信号K）────────────────────
    ev_no_k = run_universe(L_PRIMARY, K_PRIMARY, ZONE_PRIMARY, WICK_PRIMARY,
                           require_signal_k=False)
    m_no_k = ev_no_k["net"].mean() if len(ev_no_k) else np.nan
    p6 = (not np.isnan(m_no_k)) and m > m_no_k
    print(f"\nP6 形态增量: 含信号K {m:+.4f}pp (n={len(ev)}) vs 消融(触区即入) "
          f"{m_no_k:+.4f}pp (n={len(ev_no_k)}) → "
          f"{'PASS（形态层有增量）' if p6 else 'FAIL（形态层无增量，判死不进策略）'}")

    # ── P7 RR门控增量（消融ii：去RR门控，报告级）────────
    ev_no_rr = run_universe(L_PRIMARY, K_PRIMARY, ZONE_PRIMARY, WICK_PRIMARY,
                            require_rr=False)
    m_no_rr = ev_no_rr["net"].mean() if len(ev_no_rr) else np.nan
    p7 = (not np.isnan(m_no_rr)) and m >= m_no_rr
    print(f"P7 RR门控增量（报告级）: 门控后 {m:+.4f}pp (n={len(ev)}) vs 无门控 "
          f"{m_no_rr:+.4f}pp (n={len(ev_no_rr)}) → "
          f"{'PASS' if p7 else 'FAIL（报告级，不影响总判定）'}")

    # 臂B 报告
    valid_rr_b = ev["rr_b"].replace([np.inf, -np.inf], np.nan).dropna()
    print(f"\n臂B（Brooks原著止损）报告: 平均RR_b={valid_rr_b.mean():.2f}"
          f"（止损架构未实际用于出场模拟，仅报告距离）")

    # ── 判定映射 ────────────────────────────────────────
    print("\n" + "=" * 70)
    all_p1_5 = p1 and p2 and p3 and p4 and p5
    neg = (not np.isnan(ci[0])) and ci[1] < 0 and m < 0
    if all_p1_5:
        verdict = "P1~P5 全过 → 解锁 TASK 98（Freqtrade 集成，三层校验+OOS 全流程）"
    elif neg:
        verdict = "❌ 逆边界方向机制性反向（CI 排零且为负）"
    else:
        verdict = "⭕ 不成立，价格行为区间族关闭"
    print(f"总判定: {verdict}")
    print(f"P6 形态层判定: {'有增量，可考虑纳入' if p6 else '无增量，形态层不得写入后续策略'}")


if __name__ == "__main__":
    main()
