#!/usr/bin/env python3
"""
入场三闸门拆解 + 与实盘成交记录对照
====================================
诊断性分析——回答「现有策略是否该换」，非参数搜索。

策略真实入场（AI500V1MacdExitDryRun.populate_entry_trend）：
    median_rank <= 3  AND  oi_delta_percent > 3.0  AND  BTC 前30min 涨幅 > 0

先前只测了第一个闸门（796 事件），那批数字不能代表实盘会开的仓。
本脚本用带 OI 列的 v2 历史把三个闸门逐层加上，并与 101 笔实盘成交对照，
分离「信号本身没料」和「出场逻辑吃掉了收益」这两种可能。

固定持有 vs 实盘的区别很重要：
  固定持有 = 信号的原始方向性（无止损、无提前退出、无费用）
  实盘     = 信号 + 出场逻辑 + 费用滑点
两者之差即出场逻辑与成本的净贡献。
"""
import glob
import os
import sys

import numpy as np
import pandas as pd

_D = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "user_data", "ai500_local")
HIST = os.path.join(_D, "ai500_netflow_hist_v2.csv.gz")
TRADES = os.path.join(_D, "ai500_live_trades.csv")
KL = os.path.join(_D, "klines_hist")

ENTRY_MEDIAN = 3
OI_MIN = 3.0
COOLDOWN_S = 7200
HORIZONS = [30, 60, 120, 240]
BOOT = 5000
SEED = 20260903


def boot_ci(vals, days, n=BOOT, seed=SEED):
    """按日区块自举——同日事件同进同出，吸收日内共同波动。"""
    u = np.unique(days)
    byd = [vals[days == d] for d in u]
    rng = np.random.default_rng(seed)
    m = np.array([np.concatenate([byd[i] for i in
                                  rng.integers(0, len(u), len(u))]).mean()
                  for _ in range(n)])
    return m.std(ddof=1), np.percentile(m, 5), np.percentile(m, 95), (m > 0).mean()


def main() -> int:
    print("[info] 载入价格 ...", flush=True)
    cl = {}
    for f in glob.glob(os.path.join(KL, "*-1m.feather")):
        s = os.path.basename(f).replace("-1m.feather", "")
        cl[s] = pd.read_feather(f, columns=["t", "close"]).set_index("t")["close"]
    cl = pd.DataFrame(cl)
    cl = cl.reindex(pd.date_range(cl.index.min(), cl.index.max(),
                                  freq="min", tz="UTC")).ffill()
    px = cl.shift(1)          # px.loc[t] = 时刻 t 可观测价格（上一根已收盘 bar）

    # BTC 闸门：前 30 分钟涨幅 > 0（策略用的是同一口径，从 K 线直接算）
    btc = px["BTCUSDT"]
    btc_ok = (btc / btc.shift(30) - 1.0) > 0

    print("[info] 载入 v2 历史 ...", flush=True)
    h = pd.read_csv(HIST, low_memory=False)
    h["ts"] = pd.to_datetime(h.ts_utc, utc=True)
    h["tm"] = h.ts.dt.floor("min")
    h = h[h.symbol.isin(px.columns)]
    h["oi"] = pd.to_numeric(h.oi_delta_percent, errors="coerce")

    print(f"[info] 行 {len(h):,} | oi_source: "
          f"{h.oi_source.value_counts(dropna=False).to_dict()}", flush=True)

    # 闸门
    g_rank = h.median_rank <= ENTRY_MEDIAN
    g_oi = h.oi > OI_MIN
    g_btc = h.tm.map(btc_ok).fillna(False).astype(bool)

    gates = {
        "①median_rank<=3 (仅闸门1)": g_rank,
        "①+②OI>3%": g_rank & g_oi,
        "①+③BTC30m>0": g_rank & g_btc,
        "①+②+③ 全部 (=实盘入场)": g_rank & g_oi & g_btc,
    }

    for name, mask in gates.items():
        sig = h[mask][["tm", "symbol"]].sort_values("tm")
        # 同标的 2h 冷却，避免同一波行情重复计入
        # 注意用 Series 迭代而非 .values —— 后者会剥掉 tz，与 px 的 tz-aware
        # 索引匹配不上，静默得到空结果
        keep, last = [], {}
        for i, sym, tm in zip(sig.index, sig.symbol, sig.tm):
            if sym not in last or (tm - last[sym]).total_seconds() >= COOLDOWN_S:
                keep.append(i)
                last[sym] = tm
        ev = sig.loc[keep]

        print("\n" + "=" * 84)
        print(f"{name}   去重后事件 {len(ev):,}"
              f"   独立日 {len(np.unique(ev.tm.dt.date))}")
        print("=" * 84)
        if len(ev) < 20:
            print("  事件太少，跳过")
            continue
        rows = []
        for H in HORIZONS:
            a, d = [], []
            for sym, t0 in zip(ev.symbol, ev.tm):
                t1 = t0 + pd.Timedelta(minutes=H)
                if t0 in px.index and t1 in px.index:
                    p0, p1 = px.at[t0, sym], px.at[t1, sym]
                    if np.isfinite(p0) and np.isfinite(p1) and p0 > 0:
                        a.append(p1 / p0 - 1)
                        d.append(t0.date())
            a, d = np.array(a), np.array(d)
            if len(a) < 20:
                continue
            sd, lo, hi, pg = boot_ci(a, d)
            rows.append(dict(H=f"{H}m", n=len(a), 均值=a.mean() * 100,
                             中位=np.median(a) * 100, 胜率=(a > 0).mean() * 100,
                             CI下=lo * 100, CI上=hi * 100,
                             P正=pg * 100, MDE=(1.96 + .8416) * sd * 100))
        print(pd.DataFrame(rows).round(3).to_string(index=False))

    # ---------- 实盘对照 ----------
    t = pd.read_csv(TRADES)
    c = t[t.is_open == 0].copy()
    c["open_date"] = pd.to_datetime(c.open_date)
    c["day"] = c.open_date.dt.date
    c["hold"] = (pd.to_datetime(c.close_date) - c.open_date).dt.total_seconds() / 60
    r = c.close_profit.values
    sd, lo, hi, pg = boot_ci(r, c.day.values)

    print("\n" + "=" * 84)
    print(f"实盘成交 {len(c)} 笔（{c.open_date.min().date()} → "
          f"{c.open_date.max().date()}，{c.day.nunique()} 个交易日）")
    print("  close_profit 已扣手续费；含出场逻辑与滑点")
    print("=" * 84)
    print(f"  均值 {r.mean()*100:+.3f}%  中位 {np.median(r)*100:+.3f}%  "
          f"胜率 {(r>0).mean()*100:.1f}%  持有中位 {c.hold.median():.0f} 分钟")
    print(f"  日区块自举 90% CI [{lo*100:+.3f}%, {hi*100:+.3f}%]  "
          f"P(均值>0)={pg*100:.1f}%  MDE {(1.96+.8416)*sd*100:.3f}%")
    print("\n  注：exit_reason 中无 macd_whisper_exit，说明这批成交不是"
          "AI500V1MacdExitDryRun，而是更早的版本。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
