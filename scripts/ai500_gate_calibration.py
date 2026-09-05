#!/usr/bin/env python3
"""
【探索性——非结论，禁止引用为证据】
AI500 入场三闸门参数标定
========================
回答的问题：ENTRY_MEDIAN / OI_MIN / BTC 方向闸门这三个值到底取多少合适。

为什么可以离线做
----------------
入场闸门的三个输入在 bot 侧都有历史记录，不需要实时快照：
  - median_rank        —— ai500_netflow_hist_v2.csv.gz 直接记了 bot 当时算出的值
                          （不是我重建的，是实盘那一刻内存里的真值）
  - oi_delta_percent   —— 同上；已与币安官方 openInterestHist 归档交叉核对：
                          n=48,867，相关 0.9565，中位绝对差 0.037pp，同号率 90.0%，
                          且逐窗口比对确认口径就是 **30m**（5m/15m/1h/2h/4h 相关均明显更低）
  - BTC 30m 涨幅       —— hist_v2 没记，但可由 BTC 5m K 线离线重建（见 btc_bias_series）
前瞻收益来自 klines_hist/*.feather（526 标的 1m 收盘，08-18 ~ 09-02 UTC）。

同源铁律
--------
决策时点 T 取 30m 边界（策略 process_only_new_candles=True，只在边界评估）。
  - 闸门状态：T **之前**最后一条快照（且距 T 不超过 STALE_S，否则视为无状态）
  - BTC bias：只用 open_t + 5m <= T 的已收盘 5m bar
  - 入场价：T 之前最后一根已收盘 1m 的 close
  - 出场价：T+H 之前最后一根已收盘 1m 的 close
任何一处都不取 T 当根或之后的数据。

⚠️ 本脚本是**扫参**，在样本内、且是大规模多重比较。
   argmax 不可当作答案；只能看有没有稳健的**高原**。
   日数少（OI 干净窗口约 9 天）→ 按日区块自举只有 9 个块，MDE 会很大，
   多数格子大概率不可区分。这是设计上的功效上限，不是跑完再纠结的事。

用法
----
  python3 scripts/ai500_gate_calibration.py
  python3 scripts/ai500_gate_calibration.py --horizon-h 4 --boot 2000
"""
import argparse
import os
import sys
import time

import numpy as np
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL = os.path.join(_ROOT, "user_data", "ai500_local")
HIST = os.path.join(LOCAL, "ai500_netflow_hist_v2.csv.gz")
KL = os.path.join(LOCAL, "klines_hist")
BTC5 = os.path.join(LOCAL, "btc_5m_bias.feather")

BAR = 1800          # 策略 timeframe（评估边界）
STALE_S = 900       # 快照距边界超过这个秒数就当没状态（hist_v2 中位间隔 300s）
FEE_RT = 0.001      # 往返手续费
RANK_ABSENT = 99.0  # 策略里 tracked-but-off-board 的占位


# ---------------------------------------------------------------- BTC bias
def btc_bias_series(*_a) -> pd.DataFrame:
    """BTC 1m 收盘（来自 klines_hist，覆盖 08-18~09-02 UTC）。

    币安 fapi 本机直连返回 418，故不走网络，用本地 1m 重建。
    """
    df = pd.read_feather(os.path.join(KL, "BTCUSDT-1m.feather"))
    return pd.DataFrame({"t": df.t.astype("int64") // 10 ** 9,
                         "close": df.close.to_numpy(np.float64)})


def btc_ok_at(k: pd.DataFrame, bounds: np.ndarray) -> np.ndarray:
    """每个边界 T 上的 BTC 30m 涨幅 %。

    实盘 _get_btc_bias 取 5m×7 根：k[0] 的 open vs k[-1]（形成中那根）的 close，
    即「T-30m 的开盘」到「T 时价」。这里用 1m 收盘近似：
      起点 = T-30m 前一分钟的 close（≈ T-30m 那根 5m 的 open）
      终点 = T 前一分钟的 close（= T 时价，严格已收盘）
    与实盘的差别只在起点差 1 分钟。闸门只看符号，影响仅限接近零的边缘情形。
    """
    t, c = k.t.to_numpy(), k.close.to_numpy()
    j = np.searchsorted(t, bounds - 60, side="right") - 1          # 终点
    i = np.searchsorted(t, bounds - 1800 - 60, side="right") - 1   # 起点
    ok = (i >= 0) & (j >= 0) & (bounds - 60 - t[j] <= 600)
    out = np.full(len(bounds), np.nan)
    out[ok] = (c[j[ok]] - c[i[ok]]) / c[i[ok]] * 100.0
    return out


# ---------------------------------------------------------------- 价格
class Prices:
    """526 标的 1m 收盘，按秒级时间戳查「T 之前最后一根已收盘」。"""

    def __init__(self, syms):
        self.d = {}
        for s in syms:
            p = os.path.join(KL, f"{s}-1m.feather")
            if not os.path.exists(p):
                continue
            df = pd.read_feather(p)
            self.d[s] = ((df.t.astype("int64") // 10 ** 9).to_numpy(),
                         df.close.to_numpy(np.float64))

    def at(self, sym, ts):
        v = self.d.get(sym)
        if v is None:
            return np.nan
        t, c = v
        i = np.searchsorted(t, ts - 60, side="right") - 1
        if i < 0 or ts - 60 - t[i] > 600:        # 停牌/缺口超过 10min 不认
            return np.nan
        return c[i]

    def vec(self, syms, tss):
        return np.array([self.at(s, int(x)) for s, x in zip(syms, tss)])


# ---------------------------------------------------------------- 自举
def day_block_boot(days, vals, n=2000, seed=7):
    rng = np.random.default_rng(seed)
    days = np.asarray(days)
    vals = np.asarray(vals, dtype=float)
    groups = [vals[days == d] for d in sorted(set(days.tolist()))]
    groups = [g for g in groups if len(g)]
    if len(groups) < 3:
        return None
    out = np.empty(n)
    for i in range(n):
        pick = rng.integers(0, len(groups), len(groups))
        out[i] = np.concatenate([groups[j] for j in pick]).mean()
    return out


def summarize(days, vals, ndays, boot):
    """返回 (n, 均值%, 90%CI, MDE%)。"""
    v = np.asarray(vals, dtype=float)
    m = np.isfinite(v)
    if m.sum() < 10:
        return len(v), np.nan, None, np.nan
    bs = day_block_boot(np.asarray(days)[m], v[m], boot)
    if bs is None:
        return int(m.sum()), v[m].mean() * 100, None, np.nan
    lo, hi = np.percentile(bs, [5, 95])
    return (int(m.sum()), v[m].mean() * 100, (lo * 100, hi * 100),
            (1.96 + 0.8416) * bs.std() * 100)


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon-h", type=float, default=4.0)
    ap.add_argument("--boot", type=int, default=2000)
    args = ap.parse_args()
    H = int(args.horizon_h * 3600)

    print("载入 hist_v2 ...", flush=True)
    b = pd.read_csv(HIST)
    b["ts"] = pd.to_datetime(b.ts_utc, utc=True)
    b["sec"] = b.ts.astype("int64") // 10 ** 9

    # --- OI 干净起点：逐日看 oi_delta_percent 的非空覆盖 ---
    b["day"] = b.ts.dt.strftime("%Y-%m-%d")
    cov = b.groupby("day").agg(n=("symbol", "size"),
                               oi=("oi_delta_percent", lambda x: x.notna().mean() * 100),
                               med=("median_rank", lambda x: x.notna().mean() * 100))
    print("\n逐日字段覆盖率（%）")
    print(f"{'day':<12} {'rows':>8} {'oi非空':>8} {'med非空':>9}")
    for d, r in cov.iterrows():
        print(f"{d:<12} {int(r.n):>8} {r.oi:>8.1f} {r.med:>9.1f}")

    ok_days = cov.index[(cov.oi > 80) & (cov.med > 80)].tolist()
    # klines_hist 只到 09-02 23:59 UTC，前瞻还要 H 秒
    ok_days = [d for d in ok_days if d <= "2026-09-02"]
    if not ok_days:
        print("\n[!] 没有 OI 覆盖达标的日子，无法标定")
        return 1
    d0, d1 = min(ok_days), max(ok_days)
    print(f"\n标定窗口: {d0} ~ {d1}  ({len(ok_days)} 天)")

    b = b[b.day.isin(ok_days)].copy()

    # --- 30m 评估边界上的状态：取 T 之前最后一条快照 ---
    b["slot"] = (b.sec // BAR + 1) * BAR          # 该快照服务于哪个边界
    b = b.sort_values("sec")
    st = b.groupby(["symbol", "slot"], as_index=False).last()
    st = st[st.slot - st.sec <= STALE_S]
    # 策略里 tracked-but-off-board 记 RANK_ABSENT=99；median_rank 为空 = 窗口
    # 不足 MIN_WINDOW_SEC，闸门取不到值 → 不可能入场，直接剔除
    st = st.dropna(subset=["median_rank", "oi_delta_percent"])
    st["day"] = pd.to_datetime(st.slot, unit="s", utc=True).dt.strftime("%Y-%m-%d")
    print(f"评估点状态: {len(st):,} 条 (symbol×30m边界), "
          f"{st.symbol.nunique()} 标的, {st.slot.nunique()} 个边界")

    # --- BTC 方向闸门 ---
    bounds = np.sort(st.slot.unique())
    k5 = btc_bias_series(int(bounds[0]), int(bounds[-1]) + H + 3600)
    bias = pd.Series(btc_ok_at(k5, bounds), index=bounds)
    st["btc_bias"] = st.slot.map(bias)
    print(f"BTC bias 重建: {st.btc_bias.notna().mean()*100:.1f}% 有值, "
          f">0 占 {(st.btc_bias > 0).mean()*100:.1f}%")

    # --- 前瞻收益 ---
    print(f"载入 1m 收盘并计算 +{args.horizon_h:g}h 前瞻收益 ...", flush=True)
    px = Prices(sorted(st.symbol.unique()))
    print(f"  可用标的 {len(px.d)}/{st.symbol.nunique()}")
    p0 = px.vec(st.symbol.to_numpy(), st.slot.to_numpy())
    p1 = px.vec(st.symbol.to_numpy(), st.slot.to_numpy() + H)
    st["ret"] = np.where((p0 > 0) & np.isfinite(p1), p1 / p0 - 1.0 - FEE_RT, np.nan)
    st = st[st.ret.notna()].copy()
    # 市场中性：减掉同一边界上全体被跟踪标的的横截面均值
    st["mkt"] = st.groupby("slot").ret.transform("mean")
    st["xsret"] = st.ret - st.mkt
    print(f"  有效前瞻: {len(st):,} 条")

    ndays = st.day.nunique()

    # --- 基线：不设任何闸门 ---
    print(f"\n{'='*100}\n无条件基线（全体被跟踪标的，{ndays} 天）\n{'='*100}")
    for lab, col in (("原始收益", "ret"), ("市场中性", "xsret")):
        n, mu, ci, mde = summarize(st.day, st[col], ndays, args.boot)
        cis = f"[{ci[0]:+.3f}, {ci[1]:+.3f}]" if ci else "n/a"
        print(f"  {lab}  n={n:>7,}  均值 {mu:+.4f}%  90%CI {cis}  MDE {mde:.3f}%")

    # --- 单闸门归因：各自单独拦掉多少 ---
    print(f"\n{'='*100}\n单闸门通过率（当前配置 median<=3 / OI>3.0 / BTC>0）\n{'='*100}")
    g_med = st.median_rank <= 3
    g_oi = st.oi_delta_percent > 3.0
    g_btc = st.btc_bias > 0
    allg = g_med & g_oi & g_btc
    print(f"  median_rank <= 3      通过 {g_med.mean()*100:>6.2f}%")
    print(f"  OI 30m > 3.0%         通过 {g_oi.mean()*100:>6.2f}%")
    print(f"  BTC 30m 涨幅 > 0      通过 {g_btc.mean()*100:>6.2f}%")
    print(f"  三闸门同时            通过 {allg.mean()*100:>6.4f}%  "
          f"= {int(allg.sum())} 个信号 / {ndays} 天 = "
          f"{allg.sum()/ndays:.2f} 个/天")
    print("\n  在「另外两闸门已通过」的条件下，各闸门的边际拦截：")
    for lab, g, rest in (("median<=3", g_med, g_oi & g_btc),
                         ("OI>3.0", g_oi, g_med & g_btc),
                         ("BTC>0", g_btc, g_med & g_oi)):
        n_rest = int(rest.sum())
        blocked = int((rest & ~g).sum())
        print(f"    {lab:<12} 另两闸门通过 {n_rest:>6} 次，其中被本闸门拦掉 "
              f"{blocked:>6} 次 ({blocked/max(n_rest,1)*100:>5.1f}%)")

    # --- 二维扫参 ---
    MEDS = [1, 2, 3, 5, 8, 10, 15, 20, 99]
    OIS = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]
    for btc_on in (True, False):
        tag = "BTC 闸门开" if btc_on else "BTC 闸门关"
        base = g_btc if btc_on else pd.Series(True, index=st.index)
        print(f"\n{'='*100}\n扫参 [{tag}]  单元格 = 信号数/天 | 市场中性均值% | "
              f"90%CI 下界>0 打 *\n{'='*100}")
        print(f"{'OI\\med':<8}" + "".join(f"{m:>21}" for m in MEDS))
        for oi in OIS:
            cells = []
            for m in MEDS:
                sel = base & (st.median_rank <= m) & (st.oi_delta_percent > oi)
                sub = st[sel]
                if len(sub) < 10:
                    cells.append(f"{len(sub):>6}  ---        ")
                    continue
                n, mu, ci, mde = summarize(sub.day, sub.xsret, ndays, args.boot)
                star = "*" if (ci and ci[0] > 0) else " "
                cells.append(f"{len(sub)/ndays:>6.1f}/d {mu:+7.3f}%{star}   ")
            print(f"OI>{oi:<5.1f}" + "".join(f"{c:>21}" for c in cells))

    # --- 候选组合的完整表 ---
    print(f"\n{'='*100}\n候选组合明细（市场中性 + 原始，按日区块自举 {args.boot} 次）"
          f"\n{'='*100}")
    print(f"{'配置':<30} {'n':>6} {'/天':>7} {'中性%':>9} {'中性90%CI':>20} "
          f"{'MDE':>7} {'原始%':>9} {'胜率%':>7}")
    print("-" * 100)
    cands = [("当前配置 med<=3 OI>3.0 BTC", 3, 3.0, True),
             ("med<=3 OI>2.0 BTC", 3, 2.0, True),
             ("med<=3 OI>1.0 BTC", 3, 1.0, True),
             ("med<=3 OI>0.5 BTC", 3, 0.5, True),
             ("med<=3 OI 不设 BTC", 3, -99.0, True),
             ("med<=5 OI>1.0 BTC", 5, 1.0, True),
             ("med<=5 OI>2.0 BTC", 5, 2.0, True),
             ("med<=8 OI>1.0 BTC", 8, 1.0, True),
             ("med<=10 OI>1.0 BTC", 10, 1.0, True),
             ("med<=3 OI>1.0 无BTC", 3, 1.0, False),
             ("med<=5 OI>1.0 无BTC", 5, 1.0, False),
             ("med<=3 OI>3.0 无BTC", 3, 3.0, False)]
    for lab, m, oi, bt in cands:
        sel = (st.median_rank <= m) & (st.oi_delta_percent > oi)
        if bt:
            sel &= g_btc
        sub = st[sel]
        if len(sub) < 5:
            print(f"{lab:<30} {len(sub):>6}  [样本不足]")
            continue
        n, mu, ci, mde = summarize(sub.day, sub.xsret, ndays, args.boot)
        _, mu_r, _, _ = summarize(sub.day, sub.ret, ndays, args.boot)
        cis = f"[{ci[0]:+.3f},{ci[1]:+.3f}]" if ci else "n/a"
        print(f"{lab:<30} {n:>6} {n/ndays:>7.1f} {mu:>+9.3f} {cis:>20} "
              f"{mde:>7.3f} {mu_r:>+9.3f} {(sub.xsret>0).mean()*100:>7.1f}")

    out = os.path.join(LOCAL, "gate_calibration.csv")
    st[["symbol", "slot", "day", "median_rank", "oi_delta_percent", "btc_bias",
        "ret", "xsret"]].to_csv(out, index=False)
    print(f"\n明细 -> {out}")
    print("\n[!] 扫参为样本内多重比较；argmax 不是答案，只看有无稳健高原。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
