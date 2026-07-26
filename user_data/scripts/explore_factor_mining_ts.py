"""
探索性——非结论，禁止引用为证据（2026-07-22）

问题：Phase 0——单币时间序列因子挖掘可行性筛选（转换思路，放弃配对交易线，见
explore_pairs_cointegration.py 的负面结果）。不采用 microsoft/RD-Agent 框架（该
框架仅支持 Linux、需持续付费调用 LLM API、底层绑定 Qlib 股票市场数据格式，与本项目
Windows+Docker+Binance合约数据栈不匹配，详见对话记录），改为手写固定因子库 + 代码
系统扫描，零 LLM 依赖，复用本地已有 feather 数据。

本脚本只回答："在本地已有的 6 个币种（BTC/ETH/SOL/ADA/DOT/BNB）1h 合约数据上，
有没有任何候选因子对未来收益表现出跨时间窗口稳定、跨币种一致的预测力？"
不写入任何策略设计文档的判定表，不构成采纳依据。

方法（与 explore_pairs_cointegration.py 同构，保持方法论一致）：
1. 因子必须因果计算（只用截至 t 时刻已收盘的数据），预测目标是 t 之后 K 根K线的
   前向收益 —— 目标本身允许"看未来"，因子输入不允许，两者严格分离。
2. 每个币种独立做 train(60%)/holdout(40%) 时间切分：
   - train 窗口内计算 Spearman IC（因子值与前向收益的秩相关），同时按月分组算
     IC 均值/标准差/ICIR，观察 train 窗口内部的稳定性
   - 用 train 窗口"发现"的方向（IC 符号），去 holdout 窗口验证是否同号 —— 防止
     "同一窗口选因子+测因子"的选择偏差（配对交易那次探索已经踩过这个坑）
3. 跨币种一致性：因子是否在多个币种上同号且稳定，比单币"跑出一个好看的数字"更
   有方向性证据价值（但 6 个币高度相关，不是独立样本，只能算弱证据，不能当statistical
   significance用）
4. 因子库覆盖：动量、波动率、量能、均值回归、资金费率（6币全覆盖）+ 持仓量/大户
   多空比（仅 BTC/ETH 有本地数据，作为补充）

数据：user_data/data/binance/futures/{SYM}_USDT_USDT-1h-{futures,funding_rate}.feather
      user_data/data/oi_metrics/{SYM}_USDT_USDT-1h-oi_metrics.feather（仅BTC/ETH）
"""
import numpy as np
import pandas as pd

DATA_DIR = "user_data/data/binance/futures"
OI_DIR = "user_data/data/oi_metrics"

COINS = ["BTC", "ETH", "SOL", "ADA", "DOT", "BNB"]
OI_COINS = {"BTC", "ETH"}

HORIZONS = {"24h": 24, "72h": 72}  # 预测未来 24 根 / 72 根 1h K线的收益
TRAIN_FRAC = 0.6


# ── 数据加载 ──────────────────────────────────────────────

def load_ohlcv(sym):
    df = pd.read_feather(f"{DATA_DIR}/{sym}_USDT_USDT-1h-futures.feather")
    return df[["date", "open", "high", "low", "close", "volume"]]


def load_funding(sym):
    df = pd.read_feather(f"{DATA_DIR}/{sym}_USDT_USDT-1h-funding_rate.feather")
    return df[["date", "open"]].rename(columns={"open": "funding_rate"})


def load_oi(sym):
    df = pd.read_feather(f"{OI_DIR}/{sym}_USDT_USDT-1h-oi_metrics.feather")
    return df[["date", "sum_open_interest", "sum_toptrader_long_short_ratio",
                "sum_taker_long_short_vol_ratio"]]


# ── 因子（全部因果：只用截至 t 的数据）──────────────────────

def rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def build_factors(sym):
    ohlcv = load_ohlcv(sym)
    funding = load_funding(sym)
    df = pd.merge_asof(ohlcv.sort_values("date"), funding.sort_values("date"),
                        on="date", direction="backward")

    if sym in OI_COINS:
        oi = load_oi(sym)
        df = pd.merge_asof(df.sort_values("date"), oi.sort_values("date"),
                            on="date", direction="backward")

    close, volume = df["close"], df["volume"]
    ret1 = close.pct_change()

    factors = pd.DataFrame(index=df.index)
    factors["date"] = df["date"]

    # 动量
    factors["mom_24h"] = close.pct_change(24)
    factors["mom_72h"] = close.pct_change(72)
    factors["mom_168h"] = close.pct_change(168)
    factors["rsi_14_centered"] = rsi(close, 14) - 50

    # 波动率
    vol_24 = ret1.rolling(24).std()
    vol_168 = ret1.rolling(168).std()
    factors["realized_vol_24h"] = vol_24
    factors["vol_ratio_24_168"] = vol_24 / vol_168

    # 量能
    volume_ma168 = volume.rolling(168).mean()
    volume_std168 = volume.rolling(168).std()
    factors["volume_z_168h"] = (volume - volume_ma168) / volume_std168
    obv = (np.sign(close.diff()) * volume).fillna(0).cumsum()
    factors["obv_chg_24h"] = obv.diff(24) / volume.rolling(24).sum().replace(0, np.nan)
    amihud = (ret1.abs() / volume.replace(0, np.nan))
    factors["amihud_illiq_24h"] = amihud.rolling(24).mean()

    # 均值回归
    ma50 = close.rolling(50).mean()
    std50 = close.rolling(50).std()
    factors["price_ma_z_50h"] = (close - ma50) / std50

    # 分布形态
    factors["skew_ret_168h"] = ret1.rolling(168).skew()
    hl_range = (df["high"] - df["low"]) / close
    hl_ma168 = hl_range.rolling(168).mean()
    hl_std168 = hl_range.rolling(168).std()
    factors["hl_range_z_168h"] = (hl_range - hl_ma168) / hl_std168

    # 资金费率（全币种）
    factors["funding_rate_level"] = df["funding_rate"]
    factors["funding_rate_chg_24h"] = df["funding_rate"].diff(24)

    # 持仓量/大户多空比（仅 BTC/ETH）
    if sym in OI_COINS:
        factors["oi_chg_24h"] = df["sum_open_interest"].pct_change(24)
        factors["taker_ls_ratio_centered"] = df["sum_taker_long_short_vol_ratio"] - 1.0
        factors["top_trader_ls_chg_24h"] = df["sum_toptrader_long_short_ratio"].diff(24)

    # 前向收益目标（允许看未来——这是预测目标，不是因子输入）
    for name, k in HORIZONS.items():
        factors[f"fwd_ret_{name}"] = close.shift(-k) / close - 1

    return factors


# ── IC 评估 ────────────────────────────────────────────────

def spearman_ic(x, y):
    d = pd.concat([x, y], axis=1).dropna()
    if len(d) < 30:
        return np.nan
    return d.iloc[:, 0].corr(d.iloc[:, 1], method="spearman")


def monthly_ic_stats(x, y, dates):
    d = pd.DataFrame({"x": x, "y": y, "date": dates}).dropna()
    if d.empty:
        return np.nan, np.nan, np.nan, np.nan
    d["month"] = d["date"].dt.to_period("M")
    monthly = d.groupby("month").apply(
        lambda g: g["x"].corr(g["y"], method="spearman") if len(g) >= 30 else np.nan
    )
    monthly = monthly.dropna()
    if monthly.empty:
        return np.nan, np.nan, np.nan, np.nan
    ic_mean = monthly.mean()
    ic_std = monthly.std()
    icir = ic_mean / ic_std if ic_std and not np.isnan(ic_std) and ic_std > 0 else np.nan
    pct_pos = (monthly > 0).mean() if ic_mean >= 0 else (monthly < 0).mean()
    return ic_mean, ic_std, icir, pct_pos


def evaluate_factor(df, factor_col, target_col, train_frac=TRAIN_FRAC):
    n = len(df)
    split = int(n * train_frac)
    train, holdout = df.iloc[:split], df.iloc[split:]

    train_ic_full = spearman_ic(train[factor_col], train[target_col])
    ic_mean, ic_std, icir, pct_pos = monthly_ic_stats(
        train[factor_col], train[target_col], train["date"]
    )
    holdout_ic_full = spearman_ic(holdout[factor_col], holdout[target_col])

    sign_consistent = (
        not np.isnan(train_ic_full) and not np.isnan(holdout_ic_full)
        and np.sign(train_ic_full) == np.sign(holdout_ic_full)
        and abs(train_ic_full) > 0.02 and abs(holdout_ic_full) > 0.02
    )

    return {
        "train_ic": round(train_ic_full, 4) if not np.isnan(train_ic_full) else np.nan,
        "train_ic_monthly_mean": round(ic_mean, 4) if not np.isnan(ic_mean) else np.nan,
        "train_icir": round(icir, 2) if not np.isnan(icir) else np.nan,
        "train_pct_months_same_sign": round(pct_pos, 2) if not np.isnan(pct_pos) else np.nan,
        "holdout_ic": round(holdout_ic_full, 4) if not np.isnan(holdout_ic_full) else np.nan,
        "sign_consistent_train_holdout": sign_consistent,
    }


# ── 主流程 ─────────────────────────────────────────────────

def main():
    print("=" * 90)
    print("探索性分析 —— 非结论，禁止引用为证据")
    print("单币时间序列因子挖掘筛选（Phase 0，转换思路：不用 RD-Agent，手写因子库扫描）")
    print("=" * 90)

    all_rows = []
    factor_cols_cache = None

    for sym in COINS:
        print(f"\n加载并计算因子: {sym} ...")
        df = build_factors(sym)
        target_cols = [f"fwd_ret_{h}" for h in HORIZONS]
        factor_cols = [c for c in df.columns if c not in (["date"] + target_cols)]
        factor_cols_cache = factor_cols

        for factor_col in factor_cols:
            for h_name in HORIZONS:
                target_col = f"fwd_ret_{h_name}"
                res = evaluate_factor(df, factor_col, target_col)
                res.update({"coin": sym, "factor": factor_col, "horizon": h_name})
                all_rows.append(res)

    result = pd.DataFrame(all_rows)

    print(f"\n{'=' * 90}")
    print("逐币逐因子逐窗口明细（前 40 行按 |train_icir| 排序，完整数据见输出文件）")
    print(f"{'=' * 90}")
    detail = result.copy()
    detail["abs_icir"] = detail["train_icir"].abs()
    detail_sorted = detail.sort_values("abs_icir", ascending=False).drop(columns="abs_icir")
    print(detail_sorted.head(40).to_string(index=False))

    # 跨币种一致性汇总：同一因子+horizon 在多少个币种上 train/holdout 同号且稳定
    print(f"\n{'=' * 90}")
    print("跨币种一致性汇总（因子+horizon 维度，coverage 按该因子实际可算的币种数分母）")
    print(f"{'=' * 90}")
    summary_rows = []
    for (factor, horizon), g in result.groupby(["factor", "horizon"]):
        n_coins = len(g)
        n_sign_consistent = int(g["sign_consistent_train_holdout"].sum())
        # 跨币种 train IC 同号计数（弱一致性指标，币种间不独立，仅供方向参考）
        signs = np.sign(g["train_ic"].dropna())
        n_same_sign = int((signs == signs.mode().iloc[0]).sum()) if not signs.empty else 0
        summary_rows.append({
            "factor": factor,
            "horizon": horizon,
            "coins_tested": n_coins,
            "coins_train_holdout_sign_consistent": n_sign_consistent,
            "coins_train_ic_same_sign": n_same_sign,
            "median_train_icir": round(g["train_icir"].median(), 2),
        })
    summary = pd.DataFrame(summary_rows).sort_values(
        ["coins_train_holdout_sign_consistent", "coins_train_ic_same_sign"], ascending=False
    )
    print(summary.to_string(index=False))

    print(f"\n{'=' * 90}")
    print("多重比较提示：本次共测试", len(factor_cols_cache), "个因子 x", len(HORIZONS),
          "个horizon x", len(COINS), "(或2)个币种 =", len(result), "组合。")
    print("按 p<0.05 惯例，纯噪声下预期约 5% 的检验会偶然“显著”——")
    print("上方 sign_consistent_train_holdout=True 且跨多币种同时成立的因子才值得关注，")
    print("单一币种单一 horizon 显著不构成任何证据。")
    print("以上全部为探索性输出，未过预注册/三层终止校验，禁止写入采纳/证伪表。")
    print("=" * 90)

    result.to_csv("user_data/scripts/explore_factor_mining_ts_detail.csv", index=False)
    summary.to_csv("user_data/scripts/explore_factor_mining_ts_summary.csv", index=False)
    print("\n明细已存: user_data/scripts/explore_factor_mining_ts_detail.csv")
    print("汇总已存: user_data/scripts/explore_factor_mining_ts_summary.csv")


if __name__ == "__main__":
    main()
