"""
TASK 96（2026-07-13）：RS 条件轮动——BTC regime 门控跨截面动量的机制检验

预注册见 quant_system_tasks.md TASK 96（判据跑数前冻结）。纯数据审计。
引擎与 audit_task83_cross_sectional.py 逐行同口径（宇宙/动量/再平衡/成本/
几何周复合），新增唯一变量：regime = BTC 日线收盘 < EMA200（ewm span=200,
adjust=False）在再平衡信号日 t 的取值（t 收盘可知，因果）；周桶按起始
再平衡日 regime 归类（桶内首日收益属上期权重，稀释性近似，工程注记）。

机制假设的锐利预测：若 TASK 83 碎片化（k=30 死在 14/90 中间）是 regime
混合伪影，熊市子集内 k=14/30/90 应全部为正；k=30 在 regime 内仍败 → 机制
否证，RS 方向（含条件版本）彻底关闭。

判据：R5 熊市周 n≥50 / R1 k=30 熊市周均>0 且 CI 排0 且 ≥+0.30% /
R2 熊市内 k=14/30/90 全正 / R3 k=30 熊市周对半切双正 / R4 k=30 熊−牛>0。
降级条款：全过也不进集成候选，只升格"机制未被否证"，采纳前置 2026-07 起
OOS 延续窗口；任何 FAIL → RS 方向全部收官。
"""
import os
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
FUT = os.path.join(BASE, "data", "binance", "futures")
UNIVERSE = ["BTC", "ETH", "BNB", "XRP", "ADA", "SOL", "DOGE", "DOT",
            "LTC", "LINK", "AVAX", "TRX", "BCH", "ETC", "ATOM"]
LOOKBACKS = [7, 14, 30, 90]
JUDGED_KS = [14, 30, 90]
K_PRIMARY = 30
REBAL_DAYS = 7
N_LEG = 3
MIN_COINS = 10
COST_SIDE = 0.0011
N_BOOT = 10_000
WEEKLY_FLOOR = 0.0030
MIN_BEAR_WEEKS = 50
RNG = np.random.default_rng(42)


def load_close_matrix():
    frames = {}
    for sym in UNIVERSE:
        p = os.path.join(FUT, f"{sym}_USDT_USDT-1d-futures.feather")
        df = pd.read_feather(p).sort_values("date")
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
        frames[sym] = df.set_index("date")["close"].astype(float)
    mat = pd.DataFrame(frames)
    return mat[~mat.index.duplicated()].sort_index()


def run_lookback(close, k):
    """与 TASK 83 引擎逐行同口径；返回长短日收益与起点。"""
    ret = close.pct_change()
    n = len(close)
    mom = close / close.shift(k) - 1
    valid_cnt = mom.notna().sum(axis=1)
    cand = np.where(valid_cnt >= MIN_COINS)[0]
    if len(cand) == 0:
        return None
    start = cand[0]
    cols = list(close.columns)
    w_long = pd.DataFrame(0.0, index=close.index, columns=cols)
    w_short = pd.DataFrame(0.0, index=close.index, columns=cols)
    cur_l = np.zeros(len(cols))
    cur_s = np.zeros(len(cols))
    turn_l = np.zeros(n)
    turn_s = np.zeros(n)
    for i in range(start, n):
        if (i - start) % REBAL_DAYS == 0:
            m = mom.iloc[i].dropna()
            if len(m) >= MIN_COINS:
                ranked = m.sort_values()
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
    r_long = (w_long.shift(1) * ret).sum(axis=1) \
        - pd.Series(turn_l, index=close.index).shift(1).fillna(0) * COST_SIDE
    r_short = (w_short.shift(1) * ret).sum(axis=1) \
        + pd.Series(turn_s, index=close.index).shift(1).fillna(0) * COST_SIDE
    r_ls = (r_long - r_short) / 2.0
    return dict(ls=r_ls.loc[close.index[start:]], start=close.index[start])


def weekly_ls_with_regime(ls, bear):
    """几何周复合（同 TASK 83），周桶按起始日 regime 归类。"""
    w = (1 + ls).resample("7D").prod() - 1
    w = w.dropna()
    # 桶起始日 = 再平衡信号日；缺日 ffill，禁止 NaN.astype(bool) 静默为 True
    reg = bear.reindex(w.index, method="ffill").fillna(False)
    return w, reg.astype(bool)


def boot_ci(x):
    boot = np.array([RNG.choice(x, len(x)).mean() for _ in range(N_BOOT)])
    return np.percentile(boot, 2.5), np.percentile(boot, 97.5)


def main():
    close = load_close_matrix()
    btc = close["BTC"].dropna()
    ema200 = btc.ewm(span=200, adjust=False).mean()
    bear = (btc < ema200).reindex(close.index).ffill().fillna(False)
    print(f"宇宙 {len(close.columns)} 币  {close.index[0].date()} ~ "
          f"{close.index[-1].date()}；BTC 冰线下天数占比 "
          f"{bear.mean() * 100:.1f}%")

    res = {}
    for k in LOOKBACKS:
        out = run_lookback(close, k)
        w, reg = weekly_ls_with_regime(out["ls"], bear)
        wb, wu = w[reg.values], w[~reg.values]
        res[k] = dict(wb=wb, wu=wu)
        tag = "" if k in JUDGED_KS else "（仅报告，反转档不判）"
        print(f"\n===== k={k}d (起点 {out['start'].date()}) {tag}=====")
        print(f"  全体周: n={len(w)}, 周均 {w.mean():+.4%}")
        print(f"  熊市周: n={len(wb)}, 周均 {wb.mean():+.4%} | "
              f"牛市周: n={len(wu)}, 周均 {wu.mean():+.4%}")

    wb30 = res[K_PRIMARY]["wb"]
    wu30 = res[K_PRIMARY]["wu"]
    print("\n" + "=" * 62)

    # R5 样本
    r5 = len(wb30) >= MIN_BEAR_WEEKS
    print(f"R5 熊市周样本: n={len(wb30)} (≥{MIN_BEAR_WEEKS})"
          f" → {'PASS' if r5 else 'FAIL'}")

    # R1 主判：k=30 熊市子集
    ci_lo, ci_hi = boot_ci(wb30.values)
    m30 = wb30.mean()
    r1 = m30 > 0 and ci_lo > 0 and m30 >= WEEKLY_FLOOR
    print(f"R1 k=30 熊市周均 {m30:+.4%}, CI[{ci_lo:+.4%}, {ci_hi:+.4%}], "
          f"下限 {WEEKLY_FLOOR:.2%} → {'PASS' if r1 else 'FAIL'}")

    # R2 机制签名：熊市内三档全正
    means = {k: res[k]["wb"].mean() for k in JUDGED_KS}
    r2 = all(v > 0 for v in means.values())
    print("R2 熊市内三档: "
          + " / ".join(f"k={k} {v:+.4%}" for k, v in means.items())
          + f" → {'PASS' if r2 else 'FAIL（机制否证）'}")

    # R3 k=30 熊市周对半切
    mid = len(wb30) // 2
    h1, h2 = wb30.iloc[:mid].mean(), wb30.iloc[mid:].mean()
    r3 = h1 > 0 and h2 > 0
    print(f"R3 熊市周对半切: H1 {h1:+.4%} / H2 {h2:+.4%}"
          f" → {'PASS' if r3 else 'FAIL'}")

    # R4 集中度对比
    r4 = m30 - wu30.mean() > 0
    print(f"R4 熊−牛: {m30 - wu30.mean():+.4%}"
          f" → {'PASS' if r4 else 'FAIL'}")

    print("\n" + "=" * 62)
    if r5 and r1 and r2 and r3 and r4:
        verdict = ("机制未被否证 → 按降级条款排期 2026-07 起 OOS 延续窗口"
                   "验证（不进策略集成候选，不动代码）")
    else:
        verdict = "机制否证/不成立 → RS 方向（无条件+条件触发）全部收官关闭"
    print(f"总判定: {verdict}")


if __name__ == "__main__":
    main()
