"""
探索性——非结论，禁止引用为证据（2026-07-22）

问题：Phase 0——配对交易（pairs trading）新独立策略线的可行性筛选。三组候选
（ETH/BNB、SOL/ADA、ADA/DOT，取自本地已有 1h 合约数据的六个币种交集）是否存在
统计上稳定的协整关系？"稳定"指跨多个不重叠时间窗口协整关系不漂移，而不是像教程
notebook 那样在单一窗口里选对+测对（选择偏差）。本脚本只回答这个问题，不写入任何
策略设计文档的判定表，不构成采纳依据。若三组都测不出稳定协整，如实报告"搞不了"。

方法：
1. 全样本 Engle-Granger 协整检验（仅供参考，非判据——同窗口选对+测对是选择偏差）
2. 滚动不重叠窗口（90天/窗）协整检验，观察 p-value 随时间是否稳定
3. 训练（60%）/holdout（40%）拆分：用训练窗口估的对冲比例（OLS beta）在 holdout
   窗口做 ADF 检验，检查用训练期参数外推到未见过的数据时价差是否仍平稳——
   这是防止"同一窗口既选对又测对"的核心检验，比单纯滚动窗口更接近真实可交易性
4. 半衰期（half-life，AR(1) 估计）供参考：过长（数月级）说明均值回归太慢，
   即使协整显著也可能不具备可交易性

数据：user_data/data/binance/futures/{SYM}_USDT_USDT-1h-futures.feather（本地已有，未新下载）
"""
import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.tsa.stattools import coint, adfuller

DATA_DIR = "user_data/data/binance/futures"

PAIRS = [
    ("ETH", "BNB"),
    ("SOL", "ADA"),
    ("ADA", "DOT"),
]

WINDOW_DAYS = 90
STEP_DAYS = 90  # 不重叠


def load_close(sym):
    df = pd.read_feather(f"{DATA_DIR}/{sym}_USDT_USDT-1h-futures.feather")
    df = df[["date", "close"]].set_index("date")
    df.columns = [sym]
    return df


def align(a_sym, b_sym):
    a = load_close(a_sym)
    b = load_close(b_sym)
    df = a.join(b, how="inner").dropna()
    df[f"log_{a_sym}"] = np.log(df[a_sym])
    df[f"log_{b_sym}"] = np.log(df[b_sym])
    return df


def hedge_ratio_and_spread(logA, logB):
    X = sm.add_constant(logB)
    model = sm.OLS(logA, X).fit()
    beta = model.params.iloc[1]
    spread = logA - beta * logB
    return beta, spread


def half_life_hours(spread):
    """AR(1): delta_t = lambda*(spread_{t-1} - mean) + eps；half-life = -ln(2)/lambda（单位：1h bar数）"""
    lag = spread.shift(1)
    delta = spread - lag
    d = pd.concat([lag, delta], axis=1).dropna()
    d.columns = ["lag", "delta"]
    X = sm.add_constant(d["lag"])
    model = sm.OLS(d["delta"], X).fit()
    lam = model.params.iloc[1]
    if lam >= 0:
        return np.inf  # 非均值回归
    return -np.log(2) / lam


def windowed_coint(df, a_sym, b_sym, window_days, step_days):
    rows = []
    start, end = df.index.min(), df.index.max()
    win = pd.Timedelta(days=window_days)
    step = pd.Timedelta(days=step_days)
    cur = start
    while cur + win <= end:
        w = df.loc[cur:cur + win]
        if len(w) < 200:
            cur += step
            continue
        logA, logB = w[f"log_{a_sym}"], w[f"log_{b_sym}"]
        _, pvalue, _ = coint(logA, logB)
        beta, spread = hedge_ratio_and_spread(logA, logB)
        hl = half_life_hours(spread)
        rows.append({
            "window_start": cur.date(),
            "window_end": (cur + win).date(),
            "pvalue": round(pvalue, 4),
            "beta": round(beta, 4),
            "half_life_hours": round(hl, 1) if np.isfinite(hl) else np.inf,
        })
        cur += step
    return pd.DataFrame(rows)


def train_holdout_check(df, a_sym, b_sym, train_frac=0.6):
    n = len(df)
    split = int(n * train_frac)
    train, holdout = df.iloc[:split], df.iloc[split:]

    logA_tr, logB_tr = train[f"log_{a_sym}"], train[f"log_{b_sym}"]
    _, p_tr, _ = coint(logA_tr, logB_tr)
    beta_tr, spread_tr = hedge_ratio_and_spread(logA_tr, logB_tr)
    hl_tr = half_life_hours(spread_tr)

    # 用训练期估的 beta（不重新拟合）外推到 holdout，检验价差是否仍平稳
    logA_ho, logB_ho = holdout[f"log_{a_sym}"], holdout[f"log_{b_sym}"]
    spread_ho = logA_ho - beta_tr * logB_ho
    adf_p_ho = adfuller(spread_ho)[1]

    return {
        "train_range": (str(train.index.min().date()), str(train.index.max().date())),
        "holdout_range": (str(holdout.index.min().date()), str(holdout.index.max().date())),
        "train_coint_pvalue": round(p_tr, 4),
        "train_beta": round(beta_tr, 4),
        "train_half_life_hours": round(hl_tr, 1) if np.isfinite(hl_tr) else np.inf,
        "holdout_adf_pvalue_using_train_beta": round(adf_p_ho, 4),
    }


def main():
    print("=" * 78)
    print("探索性分析 —— 非结论，禁止引用为证据")
    print("配对交易候选币对协整关系筛选（Phase 0）")
    print("=" * 78)

    summary = []

    for a_sym, b_sym in PAIRS:
        print(f"\n{'#' * 78}")
        print(f"# 候选对: {a_sym}/USDT vs {b_sym}/USDT")
        print(f"{'#' * 78}")

        df = align(a_sym, b_sym)
        print(f"对齐后数据范围: {df.index.min()} -> {df.index.max()}, {len(df)} 根 1h K线")

        logA_full, logB_full = df[f"log_{a_sym}"], df[f"log_{b_sym}"]
        _, p_full, _ = coint(logA_full, logB_full)
        print(f"\n[全样本参考] Engle-Granger p-value = {p_full:.4f}（仅供参考，非判据，同窗口选对+测对属选择偏差）")

        print(f"\n[滚动窗口协整稳定性] 窗口={WINDOW_DAYS}天，不重叠")
        w = windowed_coint(df, a_sym, b_sym, WINDOW_DAYS, STEP_DAYS)
        if w.empty:
            print("数据不足，无法分窗口")
            sig_ratio = 0.0
        else:
            print(w.to_string(index=False))
            sig_count = int((w["pvalue"] < 0.05).sum())
            sig_ratio = sig_count / len(w)
            print(f"\n显著窗口数（p<0.05）: {sig_count}/{len(w)} ({sig_ratio * 100:.0f}%)")

        print(f"\n[训练/Holdout 拆分检验] train=60%, holdout=40%（核心：防选择偏差）")
        th = train_holdout_check(df, a_sym, b_sym)
        for k, v in th.items():
            print(f"  {k}: {v}")

        summary.append({
            "pair": f"{a_sym}/{b_sym}",
            "full_sample_pvalue": round(p_full, 4),
            "rolling_window_sig_ratio": round(sig_ratio, 2),
            "holdout_adf_pvalue": th["holdout_adf_pvalue_using_train_beta"],
            "train_half_life_hours": th["train_half_life_hours"],
        })

    print(f"\n{'=' * 78}")
    print("汇总（探索性，非判据）")
    print(f"{'=' * 78}")
    print(pd.DataFrame(summary).to_string(index=False))
    print("\n以上全部为探索性输出，未过预注册/三层终止校验，禁止写入采纳/证伪表。")
    print("=" * 78)


if __name__ == "__main__":
    main()
