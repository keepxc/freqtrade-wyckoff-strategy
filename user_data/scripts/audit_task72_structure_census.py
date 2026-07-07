"""
TASK 72（预研）：大结构跟随可行性——结构尺寸普查 + 因果翻转跟随器净捕获

论题（用户提出）：BTC 存在肉眼可见的大结构（120k 下跌段、多年牛市段），
小尺度结构交易已被证死（确认税 ≥ 结构尺寸，TASK 60），大尺度从未受审。
算术：可捕获 ≈ 结构尺寸 S − 2×确认阈值 X − 费用。

方法：因果翻转跟随器（flip-flop ZigZag follower）——
  状态 up/down；up 态跟踪最高收盘，收盘自极值回撤 ≥X → 当日收盘翻 down（反之同理）。
  永远在场：up 持多、down 持空。每段腿 = 一个结构段，腿收益自动内含
  确认税（两端各付 ~X）与震荡期出血（连续小亏腿）。决策全部在收盘，因果。

预注册判据（跑数前写定）：
  1. 存在阈值档（5%/10%/15%/20%）使扣费（0.22%/腿往返）后腿净捕获期望 > 0
     且累计为正
  2. 稳健性：样本对半切（前半/后半），两半累计均为正（趋势跟随逐年波动
     属画像本身，故用半段判据；逐年表描述性报告）
  3. 双数据集（spot 2018-2026 / futures 2019-2026）上 1、2 同时成立
  多空分方向报告（spot 空头为假想腿，futures 为真实可做空场所）。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import numpy as np
import pandas as pd

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DATA = [
    ("spot", os.path.join(BASE, "data", "binance", "BTC_USDT-1d.feather")),
    ("futures", os.path.join(BASE, "data", "binance", "futures", "BTC_USDT_USDT-1d-futures.feather")),
]
THRESHOLDS = [0.05, 0.10, 0.15, 0.20]
COST_PER_LEG = 0.0022  # taker 0.06%×2 + 滑点假设 0.05%×2


def flip_flop(closes: np.ndarray, x: float):
    """返回 legs 列表：(entry_i, exit_i, direction, gross_return)"""
    n = len(closes)
    state = None
    extreme = closes[0]
    extreme_i = 0
    entry_i = 0
    legs = []
    for i in range(1, n):
        c = closes[i]
        if state is None:
            # 初始化：首次出现 X 位移决定初始方向
            if c >= closes[0] * (1 + x):
                state, extreme, entry_i = 'up', c, i
            elif c <= closes[0] * (1 - x):
                state, extreme, entry_i = 'down', c, i
            continue
        if state == 'up':
            if c > extreme:
                extreme = c
            elif c <= extreme * (1 - x):
                legs.append((entry_i, i, 'long', c / closes[entry_i] - 1))
                state, extreme, entry_i = 'down', c, i
        else:
            if c < extreme:
                extreme = c
            elif c >= extreme * (1 + x):
                legs.append((entry_i, i, 'short', closes[entry_i] / c - 1))
                state, extreme, entry_i = 'up', c, i
    return legs


def main():
    for tag, path in DATA:
        df = pd.read_feather(path).sort_values("date").reset_index(drop=True)
        closes = df["close"].values.astype(float)
        dates = pd.to_datetime(df["date"]).dt.tz_localize(None)
        mid_date = dates.iloc[len(df) // 2]
        print(f"\n========== {tag}: {dates.iloc[0].date()} ~ {dates.iloc[-1].date()} ==========")
        for x in THRESHOLDS:
            legs = flip_flop(closes, x)
            if not legs:
                print(f"  X={x:.0%}: 0 legs")
                continue
            rows = pd.DataFrame(legs, columns=["ei", "xi", "dir", "gross"])
            rows["net"] = rows["gross"] - COST_PER_LEG
            rows["year"] = [dates.iloc[i].year for i in rows["ei"]]
            rows["half"] = ["H1" if dates.iloc[i] < mid_date else "H2" for i in rows["ei"]]
            cum = rows["net"].sum()
            h1, h2 = rows[rows["half"] == "H1"]["net"].sum(), rows[rows["half"] == "H2"]["net"].sum()
            lg, sh = rows[rows["dir"] == "long"], rows[rows["dir"] == "short"]
            print(f"  X={x:.0%}: legs={len(rows)} 净期望/腿 {rows['net'].mean():+.2%} "
                  f"中位 {rows['net'].median():+.2%} 正率 {(rows['net']>0).mean():.0%} "
                  f"累计 {cum:+.1%} | H1 {h1:+.1%} / H2 {h2:+.1%}")
            print(f"        多头腿 n={len(lg)} 均 {lg['net'].mean():+.2%} 累计 {lg['net'].sum():+.1%} | "
                  f"空头腿 n={len(sh)} 均 {sh['net'].mean():+.2%} 累计 {sh['net'].sum():+.1%}")
            yr = rows.groupby("year")["net"].sum()
            print(f"        逐年: " + " ".join(f"{y}:{v:+.0%}" for y, v in yr.items()))
            # 最长连亏腿
            neg = (rows["net"] < 0).astype(int)
            run = mx = 0
            for v in neg:
                run = run + 1 if v else 0
                mx = max(mx, run)
            print(f"        最长连亏 {mx} 腿")


if __name__ == "__main__":
    main()
