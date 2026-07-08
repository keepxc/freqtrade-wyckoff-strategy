"""
TASK 83（预研）：C族跨截面动量——相对强弱轮动，最后一个未测原语

背景：单资产原语地图已收敛（趋势✅ 量能❌ 带符号流❌ 形态❌ 均值回归❌ carry✅）。
跨截面相对排名是与上述全部正交的新原语：不预测单币绝对方向，只赌相对强弱
的持续性。学术先验：截面动量在股票/商品/外汇跨百年稳健 [COMMON]；crypto
文献（Liu-Tsyvinski 等）报告 1-4 周视界有效。

宇宙（跑数前固定，2020-09 前已上币安 USDT 永续的头部币，15 个）：
  BTC ETH BNB XRP ADA SOL DOGE DOT LTC LINK AVAX TRX BCH ETC ATOM
  ⚠️ 幸存者偏差申明：宇宙按"今天仍是头部流动性"回选，绝对收益（尤其多头腿）
  被结构性抬高；长短对冲组合受此污染较小（两腿同受抬升），但结论只能主张
  "相对排名有无信息"，不能主张多头腿绝对收益可复现。如实标注为预研简化。

编码（跑数前固定，零调参）：
  信号：k 日总收益，k ∈ {7, 14, 30, 90}（四档全报告，禁止选择）
  再平衡：每 7 个自然日，信号用 t 收盘价计算，t+1 日起生效（因果）
  组合：截面排名，多头前 3 等权 / 空头后 3 等权；再平衡间权重固定
  成本：0.11%/边 × 换手率（sum|Δw|），与 TASK 74/79 同口径
  资本口径：长短组合 = (多腿收益 − 空腿收益)/2（2 单位资本，保守）
            纯多头轮动 = 多腿收益，基准 = 全宇宙等权买入持有
  起点：宇宙内 ≥10 个币有满 90 日历史后的首个再平衡日

预注册判据：
  X1 长短组合：净年化 > 0 且周收益 bootstrap 95% CI 排除 0，
     在 ≥2 个相邻 k 档同时成立（平台要求，防单点假阳性）
  X2 X1 通过的 k 档，时间对半切两半净收益均为正
  X3 纯多头轮动：净收益跑赢等权持有篮子（超额 > 0 且对半切同号），
     ≥1 个 X1 通过的 k 档成立——可部署形态（空头腿有方差拖累先验，TASK 77）
  全过 → 进策略层评估；X1/X2 过而 X3 败 → 记"信号有信息但无可部署形态"；
  X1 败 → C族关闭。
多重检验申明：4 k × 2 形态 = 8 检验；相邻平台要求 + 对半切使
  噪声假阳性 ≈ 5%² 量级/档。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import numpy as np
import pandas as pd

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
FUT = os.path.join(BASE, "data", "binance", "futures")
UNIVERSE = ["BTC", "ETH", "BNB", "XRP", "ADA", "SOL", "DOGE", "DOT",
            "LTC", "LINK", "AVAX", "TRX", "BCH", "ETC", "ATOM"]
LOOKBACKS = [7, 14, 30, 90]
REBAL_DAYS = 7
N_LEG = 3
MIN_COINS = 10
COST_SIDE = 0.0011
N_BOOT = 2000
SEED = 20260708


def load_close_matrix():
    frames = {}
    for sym in UNIVERSE:
        p = os.path.join(FUT, f"{sym}_USDT_USDT-1d-futures.feather")
        if not os.path.exists(p):
            print(f"  ⚠️ 缺数据: {sym}")
            continue
        df = pd.read_feather(p).sort_values('date')
        df['date'] = pd.to_datetime(df['date']).dt.tz_localize(None)
        frames[sym] = df.set_index('date')['close'].astype(float)
    mat = pd.DataFrame(frames)
    mat = mat[~mat.index.duplicated()].sort_index()
    return mat


def run_lookback(close, k):
    """返回 dict: 长短/纯多头/基准 的日收益序列（对齐 close.index）。"""
    ret = close.pct_change()
    n = len(close)
    # 信号：t 收盘的 k 日动量（只用 ≤t 数据）
    mom = close / close.shift(k) - 1

    # 首个合法再平衡日：≥MIN_COINS 个币有非NaN动量
    valid_cnt = mom.notna().sum(axis=1)
    start_candidates = np.where(valid_cnt >= MIN_COINS)[0]
    if len(start_candidates) == 0:
        return None
    start = start_candidates[0]

    cols = list(close.columns)
    w_long = pd.DataFrame(0.0, index=close.index, columns=cols)
    w_short = pd.DataFrame(0.0, index=close.index, columns=cols)
    cur_l = np.zeros(len(cols))
    cur_s = np.zeros(len(cols))
    turn_l = np.zeros(n)
    turn_s = np.zeros(n)
    for i in range(start, n):
        if (i - start) % REBAL_DAYS == 0:
            m = mom.iloc[i]
            avail = m.dropna()
            if len(avail) >= MIN_COINS:
                ranked = avail.sort_values()
                new_l = np.zeros(len(cols))
                new_s = np.zeros(len(cols))
                for s_ in ranked.index[-N_LEG:]:
                    new_l[cols.index(s_)] = 1.0 / N_LEG
                for s_ in ranked.index[:N_LEG]:
                    new_s[cols.index(s_)] = 1.0 / N_LEG
                turn_l[i] = np.abs(new_l - cur_l).sum()
                turn_s[i] = np.abs(new_s - cur_s).sum()
                cur_l, cur_s = new_l, new_s
        w_long.iloc[i] = cur_l
        w_short.iloc[i] = cur_s

    # t 日定的权重，t+1 日收益生效；换手成本记在生效首日
    r_long = (w_long.shift(1) * ret).sum(axis=1) - pd.Series(turn_l, index=close.index).shift(1).fillna(0) * COST_SIDE
    r_short_leg = (w_short.shift(1) * ret).sum(axis=1) + pd.Series(turn_s, index=close.index).shift(1).fillna(0) * COST_SIDE
    # 长短组合（2单位资本）：(long − short)/2，成本已含在两腿内
    r_ls = (r_long - r_short_leg) / 2.0
    # 等权基准：宇宙内当日有数据的币等权（无成本，对基准有利=保守）
    r_bench = ret.mean(axis=1)

    idx = close.index[start:]
    return dict(ls=r_ls.loc[idx], lo=r_long.loc[idx], bench=r_bench.loc[idx],
                start=close.index[start])


def weekly_agg(r):
    """周复合收益（几何，暴露方差拖累——TASK 77 教训）。"""
    return ((1 + r).resample('7D').prod() - 1).dropna()


def boot_ci(x, rng):
    boot = np.array([rng.choice(x, len(x)).mean() for _ in range(N_BOOT)])
    return np.percentile(boot, 2.5), np.percentile(boot, 97.5)


def comp(r):
    return (1 + r).prod() - 1


def stats_line(r):
    yrs = len(r) / 365.0
    total = comp(r)
    ann = (1 + total) ** (1 / yrs) - 1
    return total, ann


def main():
    rng = np.random.default_rng(SEED)
    close = load_close_matrix()
    print(f"宇宙: {len(close.columns)} 币  日期 {close.index[0].date()} ~ {close.index[-1].date()}")
    results = {}
    for k in LOOKBACKS:
        out = run_lookback(close, k)
        if out is None:
            print(f"k={k}: 起点不足")
            continue
        ls, lo, bench = out['ls'], out['lo'], out['bench']
        w_ls = weekly_agg(ls)
        # 纯多头超额：周复合多头 − 周复合基准（几何口径下的相对表现）
        w_lo, w_be = weekly_agg(lo), weekly_agg(bench)
        w_ex = (w_lo - w_be).dropna()
        t_ls, ann_ls = stats_line(ls)
        t_lo, ann_lo = stats_line(lo)
        t_be, ann_be = stats_line(bench)
        lo_ci, hi_ci = boot_ci(w_ls.values, rng)
        x1 = ann_ls > 0 and (lo_ci > 0)
        mid = len(ls) // 2
        h1, h2 = comp(ls.iloc[:mid]), comp(ls.iloc[mid:])
        x2 = h1 > 0 and h2 > 0
        exlo_ci, exhi_ci = boot_ci(w_ex.values, rng)
        e1 = comp(lo.iloc[:mid]) - comp(bench.iloc[:mid])
        e2 = comp(lo.iloc[mid:]) - comp(bench.iloc[mid:])
        ex_total = t_lo - t_be
        x3 = (ex_total > 0) and e1 > 0 and e2 > 0
        results[k] = dict(x1=x1, x2=x2, x3=x3, ann_ls=ann_ls)
        print(f"\n===== k={k}d (起点 {out['start'].date()}, {len(ls)} 日) =====")
        print(f"  长短组合: 累计 {t_ls:+8.1%} | 年化 {ann_ls:+6.1%} | "
              f"周CI[{lo_ci:+.3%},{hi_ci:+.3%}] | H1 {h1:+.1%}/H2 {h2:+.1%} | "
              f"X1{'√' if x1 else '×'} X2{'√' if x2 else '×'}")
        print(f"  纯多轮动: 累计 {t_lo:+8.1%} 年化 {ann_lo:+6.1%} | "
              f"等权基准: 累计 {t_be:+8.1%} 年化 {ann_be:+6.1%} | "
              f"超额 {ex_total:+.1%} 周超额CI[{exlo_ci:+.3%},{exhi_ci:+.3%}] "
              f"半段 {e1:+.1%}/{e2:+.1%} | X3{'√' if x3 else '×'}")

    print("\n" + "=" * 70)
    ks = [k for k in LOOKBACKS if k in results]
    x1_pass = [k for k in ks if results[k]['x1']]
    adjacent = any(LOOKBACKS[i] in x1_pass and LOOKBACKS[i + 1] in x1_pass
                   for i in range(len(LOOKBACKS) - 1))
    x1_final = len(x1_pass) >= 2 and adjacent
    x2_final = x1_final and all(results[k]['x2'] for k in x1_pass)
    x3_final = any(results[k]['x3'] for k in x1_pass)
    print(f"X1 长短净年化>0 + CI排0 + 相邻平台: {'PASS' if x1_final else 'FAIL'}  (通过档: {x1_pass})")
    print(f"X2 通过档对半切双正: {'PASS' if x2_final else 'FAIL'}")
    print(f"X3 纯多轮动跑赢等权篮子(≥1档): {'PASS' if x3_final else 'FAIL'}")
    if x1_final and x2_final and x3_final:
        verdict = "进入策略层评估"
    elif x1_final and x2_final:
        verdict = "信号有信息但无可部署形态"
    else:
        verdict = "C族关闭"
    print(f"总判定: {verdict}")


if __name__ == "__main__":
    main()
