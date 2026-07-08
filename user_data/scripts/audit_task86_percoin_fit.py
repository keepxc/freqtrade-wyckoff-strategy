"""
TASK 86（预研）：逐币参数适配 vs 统一参数——用户假设的可证伪检验

用户假设：TASK 84 广度组合不应直接套用 BTC 参数（20/10），应逐币寻找
适配参数。反方先验 [COMMON]：per-market fitting 是趋势文献点名的经典
过拟合（MOP/Hurst/Carver 全部使用跨品种统一参数）；逐币拟合样本内
必胜是构造保证，唯一有意义的裁判是 OOS。

跑数前声明的预测（可证伪）：拟合组样本内大胜，OOS 平手或反输，
且各币最优参数在两半段间大幅漂移（不稳定性诊断）。

设计（跑数前固定）：
  宇宙/窗口/规则：同 TASK 84（15 币，2020-10-01 起，vol-target 全套同款）
  参数网格：TASK 74 D1 同款 entry∈{10,15,20,25,30,40,50,60} ×
            exit∈{5,8,10,15,20,25,30}，56 组合/币
  拟合：前半段（IS）按 Calmar 选各币最优；IS 入场事件 <5 次的组合剔除；
        全网格无效则回退 20/10（声明）
  验证：后半段（OOS）冻结参数运行
  对照：统一 20/10（现役），同窗口同口径
  组合：等资本 15 币简单平均（同 TASK 84）

预注册判据：
  J1 拟合组合 OOS Calmar > 统一组合 OOS Calmar（用户假设方向）
  J2 OOS 上逐币改善的币数 > 50%（8/15）
  双过 → 逐币适配采纳候选（需 walk-forward 重拟合工程设计）；
  双败 → 逐币适配否决；分歧 → 按分级记录
附带诊断：IS 上拟合 vs 统一（预期拟合大胜=构造保证的对照演示）、
  各币 IS 最优参数 vs OOS 最优参数漂移。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd

from audit_task85_speed_blend import sleeve_returns, stats

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
FUT = os.path.join(BASE, "data", "binance", "futures")
UNIVERSE = ["BTC", "ETH", "BNB", "XRP", "ADA", "SOL", "DOGE", "DOT",
            "LTC", "LINK", "AVAX", "TRX", "BCH", "ETC", "ATOM"]
START = "2020-10-01"
ENTRIES = [10, 15, 20, 25, 30, 40, 50, 60]
EXITS = [5, 8, 10, 15, 20, 25, 30]
MIN_ENTRIES_IS = 5
DEFAULT = (20, 10)


def n_entry_events(r):
    """入场事件数近似：收益序列非零段的起点数。"""
    active = (r != 0).values
    return int(((~np.roll(active, 1)) & active).sum())


def main():
    dfs = {}
    for sym in UNIVERSE:
        df = pd.read_feather(os.path.join(FUT, f"{sym}_USDT_USDT-1d-futures.feather"))
        df = df.sort_values('date').reset_index(drop=True)
        df['date'] = pd.to_datetime(df['date']).dt.tz_localize(None)
        dfs[sym] = df

    # 用统一 20/10 的日期矩阵定义共同窗口与切分点（与 TASK 84 一致）
    base = {s: sleeve_returns(dfs[s], *DEFAULT) for s in UNIVERSE}
    mat = pd.DataFrame(base).dropna()
    mat = mat[mat.index >= pd.Timestamp(START)]
    mid_date = mat.index[len(mat) // 2]
    print(f"窗口: {mat.index[0].date()} ~ {mat.index[-1].date()}  IS/OOS 切分 {mid_date.date()}")

    fitted_is, fitted_oos, unif_is, unif_oos = {}, {}, {}, {}
    best_params, oos_best_params = {}, {}
    for sym in UNIVERSE:
        grid = {}
        for ne in ENTRIES:
            for nx in EXITS:
                r = sleeve_returns(dfs[sym], ne, nx)
                r = r[(r.index >= pd.Timestamp(START)) & r.index.isin(mat.index)]
                r_is, r_oos = r[r.index < mid_date], r[r.index >= mid_date]
                if n_entry_events(r_is) < MIN_ENTRIES_IS:
                    continue
                _, _, _, cal_is = stats(r_is)
                grid[(ne, nx)] = (cal_is, r_is, r_oos)
        if not grid:
            choice = DEFAULT
            r = sleeve_returns(dfs[sym], *DEFAULT)
            r = r[(r.index >= pd.Timestamp(START)) & r.index.isin(mat.index)]
            r_is, r_oos = r[r.index < mid_date], r[r.index >= mid_date]
        else:
            choice = max(grid, key=lambda k: (grid[k][0] if np.isfinite(grid[k][0]) else -9e9))
            _, r_is, r_oos = grid[choice]
            # OOS 最优参数（仅诊断漂移，不参与判定）
            oos_scores = {}
            for k, (_, _, ro) in grid.items():
                _, _, _, c_o = stats(ro)
                oos_scores[k] = c_o if np.isfinite(c_o) else -9e9
            oos_best_params[sym] = max(oos_scores, key=oos_scores.get)
        best_params[sym] = choice
        fitted_is[sym], fitted_oos[sym] = r_is, r_oos
        b = base[sym][base[sym].index.isin(mat.index)]
        unif_is[sym], unif_oos[sym] = b[b.index < mid_date], b[b.index >= mid_date]

    print(f"\n{'币':<6} {'IS最优':>9} {'OOS最优':>9} {'IS Calmar 拟合/统一':>22} {'OOS Calmar 拟合/统一':>22}")
    n_improve = 0
    for sym in UNIVERSE:
        _, _, _, cf_is = stats(fitted_is[sym])
        _, _, _, cu_is = stats(unif_is[sym])
        _, _, _, cf_o = stats(fitted_oos[sym])
        _, _, _, cu_o = stats(unif_oos[sym])
        if np.isnan(cf_o):
            cf_o = -9.99
        if np.isnan(cu_o):
            cu_o = -9.99
        imp = cf_o > cu_o
        n_improve += imp
        ob = oos_best_params.get(sym, ('-', '-'))
        print(f"{sym:<6} {str(best_params[sym]):>9} {str(ob):>9} "
              f"{cf_is:>10.2f}/{cu_is:<10.2f} {cf_o:>10.2f}/{cu_o:<10.2f} {'改善' if imp else ''}")

    p_fit_is = pd.DataFrame(fitted_is).fillna(0).mean(axis=1)
    p_fit_oos = pd.DataFrame(fitted_oos).fillna(0).mean(axis=1)
    p_unif_is = pd.DataFrame(unif_is).fillna(0).mean(axis=1)
    p_unif_oos = pd.DataFrame(unif_oos).fillna(0).mean(axis=1)
    print()
    res = {}
    for name, r in [("拟合组合 IS", p_fit_is), ("统一组合 IS", p_unif_is),
                    ("拟合组合 OOS", p_fit_oos), ("统一组合 OOS", p_unif_oos)]:
        t, a, d, c_ = stats(r)
        res[name] = c_
        print(f"{name:<12s} 累计 {t:+8.1%} | 年化 {a:+6.1%} | MaxDD {d:5.1%} | Calmar {c_:.2f}")

    j1 = res["拟合组合 OOS"] > res["统一组合 OOS"]
    j2 = n_improve > len(UNIVERSE) / 2
    print("\n" + "=" * 60)
    print(f"J1 拟合组合 OOS Calmar > 统一: {'PASS' if j1 else 'FAIL'} "
          f"({res['拟合组合 OOS']:.2f} vs {res['统一组合 OOS']:.2f})")
    print(f"J2 OOS 逐币改善 >50%: {'PASS' if j2 else 'FAIL'} ({n_improve}/{len(UNIVERSE)})")
    print(f"总判定: {'逐币适配采纳候选' if (j1 and j2) else '逐币适配否决' if not (j1 or j2) else '分歧，按分级记录'}")


if __name__ == "__main__":
    main()
