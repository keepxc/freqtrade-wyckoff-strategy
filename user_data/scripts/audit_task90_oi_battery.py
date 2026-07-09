"""
TASK 90（预研）：OI 四象限盘内尺度 + funding 变化率 + OI×funding 联合——衍生品维度收官电池

背景：TASK 69 已判 BTC **日线** OI 四象限三判据全灭；其"未探索余量"记录了
盘内尺度（1H/4H）与 OI×funding 联合。TASK 57 判 funding **绝对分位** regime
剥离不过，但明示"funding 变化率 / funding×OI 联合仍是未证伪的独立候选"。
本电池按 TASK 80/81 格式一次跑完全部余量，BTC+ETH 双资产复制内建——
这是威科夫"努力 vs 结果"在未污染数据维度（OI 区分开仓/平仓，成交量不能）
的最后一组未翻格子，跑完即衍生品维度整体收官。

数据（声明）：
  OI: data/oi_metrics/{BTC,ETH}_USDT_USDT-1h-oi_metrics.feather
      BTC 2020-09-01 起；ETH 仅 2021-12-01 起（Binance 归档起点，样本少 15 个月）
  价格: futures 1h/4h/1d feather；funding: 1h funding_rate feather（8h 事件）
  因果性：OI 每小时取该小时内最后一条 5min 读数 = bar 收盘时点状态；
  4h/1d 同法重采样（每桶最后读数）。前瞻收益自同 bar close 起算。

测试单元（跑数前固定，零调参）：
  A 四象限（帧内 1-bar Δclose 符号 × ΔOI 符号）× {1h, 4h, 1d}：
      Q1 涨+OI涨=新多进场(预期+)   Q2 涨+OI跌=空头回补虚涨(预期-)
      Q3 跌+OI涨=新空进场(预期-)   Q4 跌+OI跌=多头投降衰竭(预期+)
      1d×BTC = TASK 69 复现单元（预测：不过）；1d×ETH / 1h / 4h = 新格子
  B funding 变化率（1d）：日 funding 结算和的日差 Δfund，
      对过去 365d（min 180d）滚动分位 shift(1)（因果，无全样本分位未来函数）：
      >q80 = 拥挤加速(预期-)   <q20 = 拥挤消退(预期+)
  C OI×funding 联合（1d，符号象限）：仅对 OI 增的两格设判据：
      OI涨+Δfund涨 = 杠杆多头拥挤堆积(预期-)；OI涨+Δfund跌 = 新空进场(预期-)
      OI 降两格 = 去杠杆语义含混，报告不判定（声明）
  视界：fwd 5 / 20 bar（帧内单位，同 TASK 80）

预注册判据（同 TASK 80，全过才算候选）：
  E1 edge（条件均值−无条件基线）块自助 95%CI 不含 0
     （块长 = 视界 h 的循环块自助——稠密状态前瞻窗口重叠，普通自助会虚增显著性）
  E2 |edge| ≥ 0.3%（可操作性下限，往返成本 0.22%）
  E3 时间对半切同号
  E4 BTC 与 ETH 同单元同时通过 E1-E3 + 预期符号（复制内建）
  样本门槛：条件 bar 数 ≥ 100/单元，不足记"样本不足"不判方向
多重检验申明：判定单元 = A:4象限×3帧×2视界(24) + B:2状态×2视界(4) +
  C:2状态×2视界(4) = 32/资产；E4 双资产联合把单元级假阳性压到 (0.05)² 量级。
跑数前预测申明：A-1d-BTC 复现 TASK 69 应不过；其余先验已被 TASK 29/57/69
  连续三击调低——预期全灭，跑数是为了合法收官而非期待翻盘。
范围外（声明不测）：toptrader_long_short_ratio 系列列（TASK 69 余量另册，
  非本轮共识范围）。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import numpy as np
import pandas as pd

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
FUT = os.path.join(BASE, "data", "binance", "futures")
OIM = os.path.join(BASE, "data", "oi_metrics")
ASSETS = ["BTC", "ETH"]
TIMEFRAMES = ["1h", "4h", "1d"]
HORIZONS = [5, 20]
EFFECT_FLOOR = 0.003
N_BOOT = 2000
SEED = 20260709
MIN_N = 100

QUAD_SIGN = {"Q1_涨OI涨": +1, "Q2_涨OI跌": -1, "Q3_跌OI涨": -1, "Q4_跌OI跌": +1}
FUND_SIGN = {"dfund_top": -1, "dfund_bot": +1}
JOINT_SIGN = {"OI涨_fund涨": -1, "OI涨_fund跌": -1}  # OI降两格报告不判定


def strip_tz(s):
    s = pd.to_datetime(s)
    return s.dt.tz_convert(None) if s.dt.tz is not None else s


def load_px(asset, tf):
    df = pd.read_feather(os.path.join(FUT, f"{asset}_USDT_USDT-{tf}-futures.feather"))
    df = df.sort_values("date").reset_index(drop=True)
    df["date"] = strip_tz(df["date"])
    return df


def load_oi(asset, tf):
    oi = pd.read_feather(os.path.join(OIM, f"{asset}_USDT_USDT-1h-oi_metrics.feather"))
    oi["date"] = strip_tz(oi["date"])
    oi = oi.sort_values("date")
    freq = {"1h": "h", "4h": "4h", "1d": "D"}[tf]
    oi["bucket"] = oi["date"].dt.floor(freq)
    out = oi.groupby("bucket")["sum_open_interest"].last().reset_index()
    out.columns = ["date", "oi"]
    return out


def daily_funding(asset):
    f = pd.read_feather(os.path.join(FUT, f"{asset}_USDT_USDT-1h-funding_rate.feather"))
    f["date"] = strip_tz(f["date"])
    ev = f.set_index("date")["open"].astype(float)
    day = ev.groupby(ev.index.floor("D")).sum()
    day.index.name = "date"
    return day


def block_boot_ci(rets, block_len, rng):
    """循环块自助 95%CI（对条件收益均值）。"""
    n = len(rets)
    L = max(1, min(block_len, n))
    nblocks = int(np.ceil(n / L))
    means = np.empty(N_BOOT)
    offs = np.arange(L)
    for b in range(N_BOOT):
        starts = rng.integers(0, n, nblocks)
        idx = ((starts[:, None] + offs) % n).ravel()[:n]
        means[b] = rets[idx].mean()
    return np.percentile(means, 2.5), np.percentile(means, 97.5)


def judge_cell(df, mask, h, want_sign, rng, label, results, key):
    """单元判定：E1 块自助 CI、E2 效应量、E3 对半切、符号。"""
    fwd = df["close"].shift(-h) / df["close"] - 1
    uncond = fwd.dropna().mean()
    sel = mask & fwd.notna()
    rets = fwd[sel].values
    n = len(rets)
    if n < MIN_N:
        print(f"  {label:<22s} fwd{h:>2d}: 样本不足 (n={n})")
        results[key] = None
        return
    edge = rets.mean() - uncond
    lo, hi = block_boot_ci(rets, h, rng)
    lo, hi = lo - uncond, hi - uncond
    e1 = (lo > 0) or (hi < 0)
    e2 = abs(edge) >= EFFECT_FLOOR
    mid = df.index[len(df) // 2]
    r1 = fwd[sel & (df.index < mid)].values
    r2 = fwd[sel & (df.index >= mid)].values
    e3 = (len(r1) > 0 and len(r2) > 0
          and np.sign(r1.mean() - uncond) == np.sign(r2.mean() - uncond) == np.sign(edge))
    sign_ok = (want_sign is not None) and (np.sign(edge) == want_sign)
    ok = bool(e1 and e2 and e3 and sign_ok)
    if want_sign is None:
        results[key] = None  # 报告不判定
        flag = "（报告不判定）"
    else:
        results[key] = (edge, ok)
        flag = "** 单资产过 **" if ok else ""
    print(f"  {label:<22s} fwd{h:>2d}: n={n:5d} edge {edge:+.3%} "
          f"CI[{lo:+.3%},{hi:+.3%}] "
          f"E1{'√' if e1 else '×'} E2{'√' if e2 else '×'} "
          f"E3{'√' if e3 else '×'} 号{'√' if sign_ok else '×'} {flag}")


def main():
    rng = np.random.default_rng(SEED)
    results = {}

    # ---- A: 四象限 × 3 帧 ----
    for asset in ASSETS:
        for tf in TIMEFRAMES:
            px = load_px(asset, tf)
            oi = load_oi(asset, tf)
            df = px.merge(oi, on="date", how="inner").sort_values("date").reset_index(drop=True)
            d_px = df["close"].pct_change()
            d_oi = df["oi"].pct_change()
            quad = np.select(
                [(d_px > 0) & (d_oi > 0), (d_px > 0) & (d_oi <= 0),
                 (d_px <= 0) & (d_oi > 0), (d_px <= 0) & (d_oi <= 0)],
                ["Q1_涨OI涨", "Q2_涨OI跌", "Q3_跌OI涨", "Q4_跌OI跌"], default="NA")
            quad[d_px.isna().values | d_oi.isna().values] = "NA"
            print(f"\n========== A 四象限 {asset} {tf}: n={len(df)} "
                  f"({df['date'].min().date()}~{df['date'].max().date()}) ==========")
            for qname, sign in QUAD_SIGN.items():
                for h in HORIZONS:
                    judge_cell(df, pd.Series(quad == qname, index=df.index), h,
                               sign, rng, qname, results, (asset, f"A_{tf}", qname, h))

    # ---- B: funding 变化率（1d） + C: OI×funding 联合（1d） ----
    for asset in ASSETS:
        px = load_px(asset, "1d")
        oi = load_oi(asset, "1d")
        fund = daily_funding(asset)
        df = px.merge(oi, on="date", how="inner")
        df = df.merge(fund.rename("fund"), left_on="date", right_index=True, how="left")
        df["fund"] = df["fund"].fillna(0.0)
        df = df.sort_values("date").reset_index(drop=True)
        dfund = df["fund"].diff()
        d_oi = df["oi"].pct_change()
        q80 = dfund.rolling(365, min_periods=180).quantile(0.80).shift(1)
        q20 = dfund.rolling(365, min_periods=180).quantile(0.20).shift(1)
        print(f"\n========== B funding变化率 {asset} 1d: n={len(df)} ==========")
        for name, mask in [("dfund_top", dfund > q80), ("dfund_bot", dfund < q20)]:
            for h in HORIZONS:
                judge_cell(df, mask & q80.notna(), h, FUND_SIGN[name], rng,
                           name, results, (asset, "B_1d", name, h))
        print(f"\n========== C OI×funding联合 {asset} 1d ==========")
        cells = [("OI涨_fund涨", (d_oi > 0) & (dfund > 0), JOINT_SIGN["OI涨_fund涨"]),
                 ("OI涨_fund跌", (d_oi > 0) & (dfund <= 0), JOINT_SIGN["OI涨_fund跌"]),
                 ("OI降_fund涨", (d_oi <= 0) & (dfund > 0), None),
                 ("OI降_fund跌", (d_oi <= 0) & (dfund <= 0), None)]
        for name, mask, sign in cells:
            for h in HORIZONS:
                judge_cell(df, mask & dfund.notna(), h, sign, rng,
                           name, results, (asset, "C_1d", name, h))

    # ---- E4 联合 ----
    print("\n" + "=" * 70)
    print("E4 双资产联合通过（同单元 BTC+ETH 均单资产过）:")
    n_joint = 0
    units = sorted({(k[1], k[2], k[3]) for k in results})
    for (blk, name, h) in units:
        a = results.get(("BTC", blk, name, h))
        b = results.get(("ETH", blk, name, h))
        if a and b and a[1] and b[1]:
            n_joint += 1
            print(f"  ✅ {blk} {name} fwd{h}: BTC {a[0]:+.3%} / ETH {b[0]:+.3%}")
    if n_joint == 0:
        print("  （无）")
    print(f"\n总判定: {n_joint} 个单元联合通过 → "
          f"{'进入策略层评估' if n_joint > 0 else 'OI/funding 衍生品维度整体关闭（TASK 29/57/69 后第四击，收官）'}")


if __name__ == "__main__":
    main()
