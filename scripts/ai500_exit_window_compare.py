#!/usr/bin/env python3
"""
【探索性——非结论，禁止引用为证据】
出场方案横评：whisper 标签分布诊断 + 固定持有窗口（含 4h 备选）
=================================================================
接 scripts/ai500_macd_exit_replay.py 的结论：MACD whisper 出场在 48h 上限下
显著劣于实盘现有出场。本脚本回答两个后续问题：

  1. **为什么不触发** —— 把 101 笔持仓期内每个 30m 边界上的 whisper 标签统计
     出来。出场规则只认 bicak / karisik-A 两种，如果这两种在样本里几乎不出现，
     那这条规则在结构上就不是「出场规则」，而是「持有到超时」。
  2. **固定窗口好不好** —— 直接把「持有 N 小时后无条件平仓」当作对照方案，
     N 取 1/2/3/4/6/8/12/24h，与实盘实际配对比较。这是用户提出的备选路线
     （Part A 的市场中性分析指向 3-6h），这里用真实入场直接验。

同 replay 脚本的口径：出场价 = T 之前最后一根已收盘 15m 的 close；扣 0.1%
往返手续费；显著性走按日区块自举（不用 sd/sqrt(n)）。

用法
----
  python3 scripts/ai500_exit_window_compare.py
"""
import os
import sys
from collections import Counter

import numpy as np
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
sys.path.insert(0, os.path.join(_ROOT, "user_data", "strategies"))

from AI500V1MacdExitDryRun import AI500V1MacdExitDryRun as S      # noqa: E402
from ai500_macd_exit_replay import (Book, BAR, FEE_RT, LOCAL, TRADES,  # noqa: E402
                                    atr_at, bearish_new, bearish_old,
                                    day_block_boot, whisper_at)

HOURS = [1, 2, 3, 4, 6, 8, 12, 24]
DIAG_H = 24          # 标签诊断的观察长度


def main():
    s = S.__new__(S)
    d = pd.read_csv(TRADES)
    d["sym"] = d.pair.str.split("/").str[0] + "USDT"
    d["open_ts"] = pd.to_datetime(d.open_date, utc=True).astype("int64") // 10 ** 9
    d["day"] = pd.to_datetime(d.open_date).dt.strftime("%Y-%m-%d")

    books, cache = {}, {}
    labels, n_old, n_new, n_obs = Counter(), 0, 0, 0
    rows = []
    for _, r in d.iterrows():
        if r.sym not in books:
            books[r.sym] = Book(r.sym)
        b = books[r.sym]
        rec = {"id": r.id, "sym": r.sym, "day": r.day,
               "live_ret": float(r.close_profit)}

        # --- 固定持有窗口 ---
        floor = atr_at(b, int(r.open_ts))
        for hh in HOURS:
            px = b.price_at(int(r.open_ts) + int(hh * 3600))
            rec[f"h{hh}"] = (px / float(r.open_rate) - 1.0 - FEE_RT
                             if px is not None else np.nan)
        # 4h + ATR 止损（唯一在实盘策略里真实存在的第二道闸）
        for hh in (3, 4, 6):
            t = ((int(r.open_ts) // BAR) + 1) * BAR
            end = int(r.open_ts) + int(hh * 3600)
            hit = None
            while t <= end:
                px = b.price_at(t)
                if px is None:
                    break
                if floor is not None and px <= floor:
                    hit = px
                    break
                t += BAR
            if hit is None:
                hit = b.price_at(end)
            rec[f"h{hh}_atr"] = (hit / float(r.open_rate) - 1.0 - FEE_RT
                                 if hit is not None else np.nan)

        # --- whisper 标签诊断 ---
        t = ((int(r.open_ts) // BAR) + 1) * BAR
        for _ in range(int(DIAG_H * 3600 / BAR)):
            res = whisper_at(s, r.sym, b, t, cache)
            if res is None:
                break
            w, p = res
            labels[w] += 1
            n_obs += 1
            n_old += bearish_old(w, p)
            n_new += bearish_new(w, p)
            t += BAR
        rows.append(rec)
        if len(rows) % 25 == 0:
            print(f"  ... {len(rows)}/{len(d)}", flush=True)

    res = pd.DataFrame(rows)

    print(f"\n{'='*72}\n1) whisper LABEL DIAGNOSIS  ({n_obs:,} bar-observations, "
          f"first {DIAG_H}h of each of the {len(d)} entries)\n{'='*72}")
    for w, c in labels.most_common():
        print(f"  {w:<14} {c:>7,}  {c/n_obs*100:>6.2f}%")
    print(f"\n  bearish under OLD rule : {n_old:>7,}  {n_old/n_obs*100:>6.2f}%")
    print(f"  bearish under NEW rule : {n_new:>7,}  {n_new/n_obs*100:>6.2f}%")

    print(f"\n{'='*72}\n2) FIXED HOLDING WINDOW vs LIVE  (n={len(res)}, paired)"
          f"\n{'='*72}")
    print(f"{'variant':<20} {'n':>4} {'mean%':>9} {'med%':>9} {'win%':>7} "
          f"{'sum%':>9} | {'delta vs live':>13} {'90% CI':>22} {'MDE':>7}")
    print("-" * 108)
    live = res.live_ret
    print(f"{'LIVE actual':<20} {len(live):>4} {live.mean()*100:>9.3f} "
          f"{live.median()*100:>9.3f} {(live>0).mean()*100:>7.1f} "
          f"{live.sum()*100:>9.2f} |")
    variants = [f"h{h}" for h in HOURS] + ["h3_atr", "h4_atr", "h6_atr"]
    for v in variants:
        col = res[v]
        m = col.notna()
        if m.sum() < 20:
            print(f"{v:<20} {m.sum():>4}  [样本不足，跳过]")
            continue
        sub, lv, dy = col[m], live[m], res.day[m]
        diff = (sub - lv).to_numpy()
        bs = day_block_boot(dy.tolist(), diff, 2000)
        lo, hi = np.percentile(bs, [5, 95])
        mde = (1.96 + 0.8416) * bs.std()
        star = " *" if lo > 0 else ""
        print(f"{v:<20} {m.sum():>4} {sub.mean()*100:>9.3f} "
              f"{sub.median()*100:>9.3f} {(sub>0).mean()*100:>7.1f} "
              f"{sub.sum()*100:>9.2f} | {diff.mean()*100:>+13.3f} "
              f"[{lo*100:>+8.3f},{hi*100:>+8.3f}] {mde*100:>6.3f}{star}")
    print("\n  * = 自举 90% CI 下界 > 0（方向性证据，非采纳判据）")
    print("  [!] 一次跑了 11 个窗口 —— 多重比较，单个 CI 排除零不足以立结论")

    # 最要紧的一问：所有出场方案都亏，那问题是不是出在**入场**？
    # 对 live_ret 本身做按日区块自举，看这批入场的绝对收益是否显著为负。
    print(f"\n{'='*72}\n3) 入场信号本身：live per-trade 收益的绝对水平"
          f"\n{'='*72}")
    for name, col in (("LIVE actual", res.live_ret), ("hold 4h", res.h4),
                      ("hold 4h + ATR", res.h4_atr)):
        bs = day_block_boot(res.day.tolist(), col.to_numpy(), 2000)
        lo, hi = np.percentile(bs, [5, 95])
        print(f"  {name:<16} 均值 {col.mean()*100:+.3f}%  "
              f"90% CI [{lo*100:+.3f}, {hi*100:+.3f}]  "
              f"{'← 上界 < 0，显著为负' if hi < 0 else '← CI 跨零'}")

    out = os.path.join(LOCAL, "exit_window_compare.csv")
    res.to_csv(out, index=False)
    print(f"\ndetail -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
