# -*- coding: utf-8 -*-
"""BTC 日内时段波动分析：亚盘 vs 欧盘 vs 美盘，谁波动大。

方法论要点
----------
· **归一化是必须的**：2017-2018 的绝对波动是 2024-2026 的好几倍。
  不做归一化，小时画像会被年代主导，而不是被日内节奏主导。
  做法：每个 bar 的指标 ÷ 该 bar 所属日之前 30 天的滚动均值（trailing，无前视）。

· **按天配对比较**：直接比「亚盘小时均值 vs 美盘小时均值」会把年代效应混进去。
  正确做法是**在同一天内**比较两个时段（配对样本），再跨 ~3300 天做检验。
  年代因素在同一天内被完全消掉。

· **两个不同的量**：
  - 波动（magnitude）：|log 收益|、振幅 —— 回答「哪个时段更值得看」
  - 方向（direction）：log 收益本身 —— 回答「哪个时段更容易涨」

时段划分（UTC，固定不随夏令时调整；DST 会让欧/美边界漂移 1 小时，见文末说明）
  Asian      00:00 - 08:00   东京 09:00-17:00
  Europe     07:00 - 16:00   伦敦 08:00-17:00
  US         13:00 - 21:00   纽约 09:00-17:00
  EU/US 重叠 13:00 - 16:00
"""
import datetime
import json
import math
import os
import sys

import numpy as np
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import config as C

CSV = C.CSV
OUT = os.path.join(C.OUT_DIR, "session_vol.json")
DAY = 24
H_MS = 3_600_000

SESSIONS = {
    "亚盘": (0, 8),
    "欧盘": (7, 16),
    "美盘": (13, 21),
    "欧美重叠": (13, 16),
    "欧美合并": (13, 21),
    "其余时段": (21, 24),
}
ERAS = [("2017-2019", 2017, 2020), ("2020-2022", 2020, 2023),
        ("2023-2026", 2023, 2027)]


def load():
    df = pd.read_csv(CSV)
    df = df.sort_values("ts").reset_index(drop=True)
    ts = df["ts"].to_numpy(np.int64)
    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    return ts, o, h, l, c


def main():
    ts, o, h, l, c = load()
    n = len(ts)
    print(f"数据 {n:,} 根 1h  "
          f"{datetime.datetime.fromtimestamp(ts[0] / 1000, datetime.UTC):%Y-%m-%d} → "
          f"{datetime.datetime.fromtimestamp(ts[-1] / 1000, datetime.UTC):%Y-%m-%d}")

    # ---- 逐 bar 指标（无前视：用上一根收盘做基准）
    prev_c = np.concatenate([[np.nan], c[:-1]])
    ret = np.log(c / prev_c)
    rng = (h - l) / prev_c
    hour = ((ts // H_MS) % 24).astype(int)
    day = (ts // (H_MS * DAY)).astype(np.int64)
    year = pd.to_datetime(ts, unit="ms", utc=True).year.to_numpy()

    ok = np.isfinite(ret) & np.isfinite(rng)
    ret, rng, hour, day, year = (ret[ok], rng[ok], hour[ok], day[ok], year[ok])
    print(f"有效 bar {len(ret):,}  覆盖 {len(np.unique(day)):,} 天")

    # ---- 归一化基准：trailing 30 天（按 UTC 日聚合后再回填）
    d = pd.DataFrame({"day": day, "ret": ret, "rng": rng})
    daily = d.groupby("day").agg(dr=("ret", lambda x: np.abs(x).mean()),
                                 dg=("rng", "mean"))
    base_r = daily["dr"].rolling(30, min_periods=10).mean().shift(1)
    base_g = daily["dg"].rolling(30, min_periods=10).mean().shift(1)
    br = pd.Series(day).map(base_r).to_numpy(float)
    bg = pd.Series(day).map(base_g).to_numpy(float)
    nret = np.abs(ret) / br
    nrng = rng / bg
    good = np.isfinite(nret) & np.isfinite(nrng)
    nret, nrng, ret2, hour2, day2, year2 = (nret[good], nrng[good], ret[good],
                                            hour[good], day[good], year[good])
    rng2 = rng[good]
    print(f"归一化后可用 {len(nret):,} 根"
          f"（损失 {len(ret) - len(nret):,}，前 30 天无基准）")

    # ================================================ 小时画像
    print()
    print("=" * 88)
    print("一、24 小时画像（归一化后，1.00 = 该时段平均波动）")
    print("=" * 88)
    print(f"{'UTC':>4}{'归一|收益|':>11}{'归一振幅':>11}{'原始|收益|':>12}"
          f"{'原始振幅':>11}{'样本':>9}   时段")
    prof = {}
    for hh in range(24):
        m = hour2 == hh
        if m.sum() < 100:
            continue
        a = float(nret[m].mean())
        b = float(nrng[m].mean())
        raw_a = float(np.abs(ret2[m]).mean() * 100)
        raw_b = float(nrng[m].mean())
        raw_g = float(np.nanmean(rng2[m]) * 100)
        prof[hh] = {"nret": a, "nrng": b, "raw_ret_pct": raw_a,
                    "raw_rng_pct": raw_g, "n": int(m.sum())}
        tag = ""
        for name, (s, e) in SESSIONS.items():
            if s <= hh < e:
                tag = name
                break
        bar = "█" * int(round(a * 40))
        print(f"{hh:>4}{a:>11.3f}{b:>11.3f}{raw_a:>11.3f}%{raw_g:>10.3f}%"
              f"{m.sum():>9,}   {tag:<8}{bar}")

    # ================================================ 时段汇总
    print()
    print("=" * 88)
    print("二、时段汇总（归一化后）")
    print("=" * 88)
    print(f"{'时段':<10}{'UTC 范围':<14}{'归一|收益|':>11}{'归一振幅':>11}"
          f"{'vs 全样本':>11}{'样本':>10}")
    allr = float(nret.mean())
    sess_stats = {}
    for name, (s, e) in SESSIONS.items():
        m = (hour2 >= s) & (hour2 < e)
        if m.sum() < 100:
            continue
        a, b = float(nret[m].mean()), float(nrng[m].mean())
        sess_stats[name] = {"lo": s, "hi": e, "nret": a, "nrng": b,
                            "n": int(m.sum())}
        print(f"{name:<10}{f'{s:02d}:00-{e:02d}:00':<14}{a:>11.3f}{b:>11.3f}"
              f"{(a / allr - 1) * 100:>10.1f}%{m.sum():>10,}")

    # ================================================ 按天配对检验
    print()
    print("=" * 88)
    print("三、按天配对比较（同一天内比，年代效应被完全消除）")
    print("=" * 88)
    dd = pd.DataFrame({"day": day2, "hour": hour2, "nret": nret, "ret": ret2})
    pivot_v, pivot_d = {}, {}
    for name, (s, e) in SESSIONS.items():
        m = (dd["hour"] >= s) & (dd["hour"] < e)
        g = dd[m].groupby("day").agg(v=("nret", "mean"), d=("ret", "sum"))
        pivot_v[name], pivot_d[name] = g["v"], g["d"]
    pv = pd.DataFrame(pivot_v).dropna()
    pdd = pd.DataFrame(pivot_d).dropna()
    print(f"有效配对天数 {len(pv):,}")
    print()
    print(f"{'对比':<22}{'波动差':>10}{'胜率':>9}{'方向差(累计)':>14}{'胜率':>9}   t 值")
    pairs = [("亚盘", "欧美合并"), ("亚盘", "欧盘"), ("亚盘", "美盘"),
             ("欧盘", "美盘"), ("亚盘", "欧美重叠")]
    pair_res = {}
    for a, b in pairs:
        dv = (pv[a] - pv[b])
        dd_ = (pdd[a] - pdd[b])
        t = float(dv.mean() / (dv.std(ddof=1) / math.sqrt(len(dv))))
        td = float(dd_.mean() / (dd_.std(ddof=1) / math.sqrt(len(dd_))))
        pair_res[f"{a}-{b}"] = {
            "vol_diff": float(dv.mean()), "vol_win": float((dv > 0).mean()),
            "vol_t": t, "dir_diff": float(dd_.mean()),
            "dir_win": float((dd_ > 0).mean()), "dir_t": td, "n": int(len(dv))}
        print(f"{a + ' - ' + b:<22}{dv.mean():>+10.3f}"
              f"{f'{(dv > 0).mean() * 100:.1f}%':>9}"
              f"{dd_.mean() * 100:>13.4f}%"
              f"{f'{(dd_ > 0).mean() * 100:.1f}%':>9}   {t:>+6.1f}")

    # ================================================ 年代稳健性
    print()
    print("=" * 88)
    print("四、年代稳健性（归一化 |收益|，检验结论是否跨年代一致）")
    print("=" * 88)
    print(f"{'年代':<12}" + "".join(f"{k:>11}" for k in
                                   ["亚盘", "欧盘", "美盘", "欧美重叠"])
          + f"{'亚盘-欧美':>12}{'配对天数':>10}")
    era_rows = {}
    for lab, y0, y1 in ERAS:
        m = (year2 >= y0) & (year2 < y1)
        vals = {}
        for name, (s, e) in SESSIONS.items():
            mm = m & (hour2 >= s) & (hour2 < e)
            vals[name] = float(nret[mm].mean()) if mm.sum() > 100 else float("nan")
        dfe = pd.DataFrame({"day": day2[m], "hour": hour2[m], "nret": nret[m]})
        g = {}
        for name, (s, e) in SESSIONS.items():
            mm = (dfe["hour"] >= s) & (dfe["hour"] < e)
            g[name] = dfe[mm].groupby("day")["nret"].mean()
        pe = pd.DataFrame(g).dropna()
        diff = (pe["亚盘"] - pe["欧美合并"]) if len(pe) else None
        era_rows[lab] = {"vals": vals, "n_days": int(len(pe)),
                         "asian_minus_euus": float(diff.mean()) if len(pe) else None}
        print(f"{lab:<12}"
              + "".join(f"{vals[k]:>11.3f}" for k in
                        ["亚盘", "欧盘", "美盘", "欧美重叠"])
              + f"{diff.mean():>+12.3f}{len(pe):>10,}")

    # ================================================ 结论
    print()
    print("=" * 88)
    print("五、结论")
    print("=" * 88)
    hi_hours = sorted(prof, key=lambda k: -prof[k]["nret"])[:6]
    lo_hours = sorted(prof, key=lambda k: prof[k]["nret"])[:6]
    print(f"  波动最高的 6 个 UTC 小时：" +
          "  ".join(f"{h:02d}:00({prof[h]['nret']:.2f})" for h in sorted(hi_hours)))
    print(f"  波动最低的 6 个 UTC 小时：" +
          "  ".join(f"{h:02d}:00({prof[h]['nret']:.2f})" for h in sorted(lo_hours)))
    k = "亚盘-欧美合并"
    r = pair_res[k]
    print(f"\n  亚盘 vs 欧美合并（{r['n']:,} 个配对日）：")
    print(f"    波动差 {r['vol_diff']:+.3f}（亚盘 - 欧美），"
          f"亚盘更高的天数占 {r['vol_win'] * 100:.1f}%，t = {r['vol_t']:+.1f}")
    print(f"    方向差 {r['dir_diff'] * 100:+.4f}%（累计 log 收益），"
          f"t = {r['dir_t']:+.1f}")

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({"as_of": datetime.datetime.fromtimestamp(
            ts[-1] / 1000, datetime.UTC).strftime("%Y-%m-%d %H:%M"),
            "n_bars": int(len(ret)), "n_days": int(len(np.unique(day))),
            "hour_profile": {str(k): v for k, v in prof.items()},
            "sessions": sess_stats, "pairs": pair_res, "eras": era_rows},
            fh, ensure_ascii=False, indent=2)
    print(f"\n已写 {OUT}")


if __name__ == "__main__":
    main()
