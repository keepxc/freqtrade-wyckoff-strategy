"""
TASK 79（预研）：A族资金费率 carry——结构性溢价画像 + 现货-永续套保滑轨

论题：永续资金费率是结构性租金（多头付费给空头为常态），收租不需要预测
方向。候选形态：long spot + short perp（delta 中性）在费率为正时收取 funding。

数据：Freqtrade funding_rate feather（8h 结算事件，费率在 open 列）。
BTC 2019-09~2026-07（7467 事件），ETH 2021-01~2026-06（5944 事件）。

资本口径（保守）：1 单位现货名义 + 1 单位永续全额保证金 = 2 单位资本；
收益率按 2 单位资本计。费用：开仓 2 腿 × 0.11% = 0.22% 名义（=0.11% 资本），
平仓同。基差风险未建模（两腿同时成交，入/出场基差噪声并入滑点假设），
如实标注为预研简化，实现层需实测。

择时规则（跑数前固定，禁止调参）：
  进场：过去 7 天（21 个结算事件）funding 均值年化 ≥ 5%
  出场：同均值年化 < 0%
  稳健性附注（预先声明、全部报告、不做选择）：窗口 3d/14d 两个扰动档

预注册判据：
  K1 扣费后净收益年化（按全样本时间摊，空仓期不记收益）≥ 3%，BTC 与 ETH 同过
  K2 时间对半切，两半净收益均为正（双资产）
  K3 在场时间占比 ≥ 20%（太薄没有配置价值）
  全过 → 进实现评估（双腿执行工程）；任一失败 → carry 方向关闭或降级。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import numpy as np
import pandas as pd

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
ASSETS = [
    ("BTC", os.path.join(BASE, "data", "binance", "futures", "BTC_USDT_USDT-1h-funding_rate.feather")),
    ("ETH", os.path.join(BASE, "data", "binance", "futures", "ETH_USDT_USDT-1h-funding_rate.feather")),
]
EVENTS_PER_DAY = 3
EVENTS_PER_YEAR = EVENTS_PER_DAY * 365
COST_ENTRY_CAP = 0.0011   # 2腿×0.11%名义 / 2单位资本
COST_EXIT_CAP = 0.0011
ENTER_ANN = 0.05
EXIT_ANN = 0.0
MAIN_WIN_D = 7
PERTURB_WINS_D = [3, 14]


def profile(tag, f):
    ann = f * EVENTS_PER_YEAR
    print(f"  画像: 事件数 {len(f)}  正费率占比 {(f > 0).mean():.0%}  "
          f"均值年化 {ann.mean():+.1%}  中位年化 {np.median(ann):+.1%}")
    yr = f.groupby(f.index.year).apply(lambda s: s.mean() * EVENTS_PER_YEAR)
    print("  逐年均值年化: " + "  ".join(f"{y}:{v:+.0%}" for y, v in yr.items()))
    ac1 = f.autocorr(1)
    ac21 = f.rolling(21).mean().autocorr(21)
    print(f"  持续性: 单事件自相关(lag1) {ac1:.2f}  7日均值自相关(lag21) {ac21:.2f}")


def carry_sleeve(f, win_d):
    win = win_d * EVENTS_PER_DAY
    trail_ann = f.rolling(win).mean().shift(1) * EVENTS_PER_YEAR  # 只用已结算历史
    n = len(f)
    in_pos = np.zeros(n, dtype=bool)
    state = False
    for i in range(n):
        t = trail_ann.iloc[i]
        if np.isnan(t):
            in_pos[i] = state
            continue
        if not state and t >= ENTER_ANN:
            state = True
        elif state and t < EXIT_ANN:
            state = False
        in_pos[i] = state
    pnl = np.where(in_pos, f.values / 2.0, 0.0)  # funding 名义收益 / 2单位资本
    entries = ((~np.roll(in_pos, 1)) & in_pos)
    entries[0] = in_pos[0]
    exits = (np.roll(in_pos, 1) & (~in_pos))
    exits[0] = False
    pnl = pnl - entries * COST_ENTRY_CAP - exits * COST_EXIT_CAP
    return pnl, in_pos, int(entries.sum())


def main():
    k_all = {}
    for tag, path in ASSETS:
        df = pd.read_feather(path)
        df['date'] = pd.to_datetime(df['date'])
        f = df.set_index('date')['open'].astype(float)
        years = (f.index[-1] - f.index[0]).days / 365.0
        print(f"\n========== {tag}: {f.index[0].date()} ~ {f.index[-1].date()} ({years:.1f} 年) ==========")
        profile(tag, f)
        for win_d in [MAIN_WIN_D] + PERTURB_WINS_D:
            pnl, in_pos, n_rt = carry_sleeve(f, win_d)
            total = pnl.sum()          # 简单加总（费率级别复利影响可忽略，如实声明）
            ann_ret = total / years
            mid = len(f) // 2
            h1, h2 = pnl[:mid].sum(), pnl[mid:].sum()
            tim = in_pos.mean()
            label = f"win={win_d}d" + ("（主规则）" if win_d == MAIN_WIN_D else "（扰动档）")
            print(f"  {label:<14s} 净累计 {total:+7.2%} | 年化 {ann_ret:+6.2%} | "
                  f"H1 {h1:+6.2%}/H2 {h2:+6.2%} | 在场 {tim:.0%} | round-trips {n_rt}")
            if win_d == MAIN_WIN_D:
                k_all[tag] = dict(ann=ann_ret, h1=h1, h2=h2, tim=tim)
    print("\n" + "=" * 70)
    k1 = all(v['ann'] >= 0.03 for v in k_all.values())
    k2 = all(v['h1'] > 0 and v['h2'] > 0 for v in k_all.values())
    k3 = all(v['tim'] >= 0.20 for v in k_all.values())
    detail = {t: f"{v['ann']:+.2%}" for t, v in k_all.items()}
    print(f"K1 净年化≥3%（双资产）: {'PASS' if k1 else 'FAIL'}  {detail}")
    print(f"K2 半段双正（双资产）: {'PASS' if k2 else 'FAIL'}")
    print(f"K3 在场≥20%: {'PASS' if k3 else 'FAIL'}")
    print(f"总判定: {'进入实现评估' if (k1 and k2 and k3) else 'carry 方向关闭/降级'}")


if __name__ == "__main__":
    main()
