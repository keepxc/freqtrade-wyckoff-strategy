"""
TASK 102 L4 复核补充：盘中口径尾部敞口（2026-07-26）

【背景】`audit_task102_l4_recheck.py` 修正了 L4 算式（封顶→实际杠杆），结论全历史最差
隐含单日名义亏损 -13.1%/-13.2%，L4 PASS。但该复核**按日线收盘口径**计算，而真实的
强平/追加保证金发生在盘中而非收盘——该脚本已在方法论备注里如实标注此局限。本脚本
补上这一层：用小时线量化"日线收盘口径低估了多少真实敞口"。

【口径】沿用 L4 修正后的保守精神：假设当日满仓持有（不用"实际空仓"开脱），
杠杆用机制真实值 lev = min(MAX_LEV, TARGET_VOL/vol_20)，vol 为因果计算。
参考价 = 前一日收盘价（持仓自前收盘延续）。
  - 收盘口径亏损 = (close[t]/close[t-1] - 1) * lev[t]
  - 盘中口径亏损(MAE) = (当日小时线最低价 / close[t-1] - 1) * lev[t]
盘中口径取当日所有小时 bar 的 low 最小值，即最大不利偏移。

【持续性诊断】强平看的是盘中触及，但"触及即穿仓"与"瞬时插针后立即收回"风险性质
不同。对最差若干天，额外统计当日有多少根小时 bar 的**收盘**低于若干亏损档位，
用以区分插针与持续性下跌。

【数据局限（如实标注）】futures 小时线仅 2021-01-01 起，不覆盖 2020-03-12 黑色星期四；
该事件只能用 spot 小时线（2018-01-01 起）评估。spot 小时线存在少量缺口，脚本显式
检查关键日期附近是否落在缺口内。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
ASSETS = [
    ("BTC-spot",
     os.path.join(BASE, "data", "binance", "BTC_USDT-1d.feather"),
     os.path.join(BASE, "data", "binance", "BTC_USDT-1h.feather")),
    ("BTC-futures",
     os.path.join(BASE, "data", "binance", "futures", "BTC_USDT_USDT-1d-futures.feather"),
     os.path.join(BASE, "data", "binance", "futures", "BTC_USDT_USDT-1h-futures.feather")),
]

# 与 DonchianV1.py 逐行一致
VOL_LOOKBACK = 20
TARGET_VOL = 0.20
MAX_LEV_GRID = [1.0, 1.5, 2.0]

BASELINE = (20, 10)
L4_THRESHOLD = 0.50  # 冻结
TOP_N = 6
PERSIST_LEVELS = [0.20, 0.30, 0.40]


def load(path):
    df = pd.read_feather(path).sort_values("date").reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    return df


def don_realized_vol(close, window=VOL_LOOKBACK):
    ret = close.pct_change()
    return ret.rolling(window).std().shift(1) * np.sqrt(365)


def donchian_positions(df, n_entry, n_exit):
    hi = df["high"].rolling(n_entry).max().shift(1)
    lo_exit = df["low"].rolling(n_exit).min().shift(1)
    c = df["close"]
    pos = np.zeros(len(df))
    state = 0
    for i in range(len(df)):
        if state == 1 and c.iloc[i] < lo_exit.iloc[i]:
            state = 0
        if state == 0 and not np.isnan(hi.iloc[i]) and c.iloc[i] > hi.iloc[i]:
            state = 1
        pos[i] = state
    return pos


def main():
    print("=" * 108)
    print("TASK 102 L4 复核补充：盘中口径尾部敞口（小时线）vs 日线收盘口径")
    print(f"否决阈值冻结 = {L4_THRESHOLD:.0%}；VOL_LOOKBACK={VOL_LOOKBACK}, TARGET_VOL={TARGET_VOL}")
    print("=" * 108)

    for tag, d_path, h_path in ASSETS:
        d = load(d_path)
        h = load(h_path)
        h["day"] = h["date"].dt.normalize()

        # 当日小时线最低价 / 小时 bar 数（用于识别缺口日）
        agg = h.groupby("day").agg(intraday_low=("low", "min"), n_bars=("low", "size"))
        d = d.merge(agg, left_on="date", right_index=True, how="left")

        ret = d["close"].pct_change()
        prev_close = d["close"].shift(1)
        vol = don_realized_vol(d["close"])
        pos_eff = pd.Series(donchian_positions(d, *BASELINE)).shift(1).fillna(0.0)

        h_start = h["date"].min().normalize()
        covered = d["date"] >= h_start
        print(f"\n{'=' * 108}\n{tag}: 日线 {d['date'].iloc[0].date()}~{d['date'].iloc[-1].date()}  "
              f"小时线自 {h_start.date()} 起（覆盖 {covered.sum()}/{len(d)} 个交易日）")
        if not covered.all():
            print(f"  ⚠️ 小时线不覆盖 {d['date'][~covered].iloc[0].date()}~{d['date'][~covered].iloc[-1].date()}，"
                  f"该区间事件（含 2020-03-12 黑色星期四如在其中）本数据集无法评估盘中口径")

        for m in MAX_LEV_GRID:
            lev = np.minimum(m, TARGET_VOL / vol)
            lev = lev.where(vol.notna() & (vol > 0))

            close_loss = ret * lev
            intraday_ret = d["intraday_low"] / prev_close - 1
            intraday_loss = intraday_ret * lev

            valid = intraday_loss.notna() & covered
            if not valid.any():
                print(f"\n  --- MAX_LEV={m} --- 无可用盘中数据，跳过")
                continue

            worst_intra_idx = intraday_loss[valid].idxmin()
            worst_intra = intraday_loss.loc[worst_intra_idx]
            worst_close = close_loss[valid].min()

            fail = abs(worst_intra) > L4_THRESHOLD
            print(f"\n  --- MAX_LEV={m} ---")
            print(f"    收盘口径最差: {worst_close:+.2%}   盘中口径最差: {worst_intra:+.2%} "
                  f"@ {d['date'].iloc[worst_intra_idx].date()}  "
                  f"（低估 {abs(worst_intra) - abs(worst_close):+.2%}）")
            print(f"    L4（阈值{L4_THRESHOLD:.0%}，盘中口径）: {'FAIL（一票否决）' if fail else 'PASS'}")

            worst_days = intraday_loss[valid].nsmallest(TOP_N)
            print(f"    盘中口径最差{TOP_N}天:")
            for j in worst_days.index:
                gap_note = ""
                nb = d["n_bars"].iloc[j]
                if pd.notna(nb) and nb < 24:
                    gap_note = f"  ⚠️小时线仅{int(nb)}根(缺口，盘中低点可能失真)"
                print(f"      {d['date'].iloc[j].date()}  收盘跌幅{ret.iloc[j]:+7.2%} "
                      f"盘中跌幅{intraday_ret.iloc[j]:+7.2%}  lev{lev.iloc[j]:5.2f}  "
                      f"→ 收盘亏损{close_loss.iloc[j]:+7.2%} / 盘中亏损{intraday_loss.iloc[j]:+7.2%}"
                      + ("  [实际持仓]" if pos_eff.iloc[j] == 1 else "  [实际空仓]") + gap_note)

            # 持续性诊断：最差那天，多少根小时 bar 收盘低于各亏损档
            wd = d["date"].iloc[worst_intra_idx]
            pc = prev_close.iloc[worst_intra_idx]
            lv = lev.iloc[worst_intra_idx]
            bars = h[h["day"] == wd]
            print(f"    持续性诊断 @ {wd.date()}（共{len(bars)}根小时bar，lev={lv:.2f}）:")
            for L in PERSIST_LEVELS:
                thr_price = pc * (1 - L / lv)
                n_below = (bars["close"] < thr_price).sum()
                print(f"      小时收盘对应亏损超 {L:.0%} 的 bar 数: {n_below} 根 "
                      f"（价格阈值 {thr_price:,.0f}）")

    print("\n" + "=" * 108)
    print("说明：盘中口径为最大不利偏移(MAE)，是强平/追保的相关口径；收盘口径不足以替代。")
    print("=" * 108)


if __name__ == "__main__":
    main()
