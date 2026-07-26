"""
探索性——非结论，禁止引用为证据。不算 TASK，不进证伪表/采纳表。

背景：黑色星期四(2020-03-12)当天 Binance 尚未有任何 OI 归档数据（U本位2020-09才
有 metrics 归档，币本位永续8月才上线），已用新鲜 HTTP 请求二分验证死胡同，OI
无法用于该事件。用户认可"用 OI 检查 2020-09 之后几次崩盘前有没有预警信号"这个
退而求其次的方向，本脚本即为此诊断。

方法：复刻 explore_crash_trigger_speed.py 的崩盘事件识别逻辑（最大单日跌幅+5日
去重），限定在 OI 有数据覆盖的窗口(>=2020-09-01)内找 top 崩盘事件，展示事件前后
±10天的 OI 水平/变化、大户多空持仓比(toptrader position ratio)、吃单多空成交比
(taker buy/sell ratio)，纯描述性观察，不做任何回测/自举/判据。

数据对齐说明：OI 为小时频，取每日最后一根(23:00 UTC，最接近日线收盘)作为"当日
状态"，避免用到当日未走完的小时数据（因果对齐）。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
FUT_PATH = os.path.join(BASE, "data", "binance", "futures", "BTC_USDT_USDT-1d-futures.feather")
OI_PATH = os.path.join(BASE, "data", "oi_metrics", "BTC_USDT_USDT-1h-oi_metrics.feather")

TOP_N_CRASHES = 6
WINDOW_DAYS = 10
OI_START = pd.Timestamp("2020-09-01")


def load_price():
    df = pd.read_feather(FUT_PATH).sort_values("date").reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    return df


def load_oi_daily():
    df = pd.read_feather(OI_PATH).sort_values("date").reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    df["day"] = df["date"].dt.normalize()
    # 每日最后一根小时观测，代表当日收盘时刻状态（因果对齐，不用未来数据）
    daily = df.groupby("day").last().drop(columns=["date"]).reset_index().rename(columns={"day": "date"})
    return daily


def find_crash_events(df, ret, top_n, min_date):
    eligible = ret.index[df["date"] >= min_date]
    ret_elig = ret.loc[eligible]
    crash_idx = ret_elig.nsmallest(top_n * 3).index
    crash_idx_sorted = sorted(crash_idx)
    picked = []
    last = -100
    for i in crash_idx_sorted:
        if i - last > 5:
            picked.append(i)
            last = i
        else:
            if ret.iloc[i] < ret.iloc[picked[-1]]:
                picked[-1] = i
            last = i
    picked = sorted(picked, key=lambda i: ret.iloc[i])[:top_n]
    return sorted(picked)


def main():
    print("=" * 100)
    print("探索性——非结论，禁止引用为证据。OI/大户多空持仓比 崩盘前预警诊断（2020-09后样本）")
    print("=" * 100)

    price = load_price()
    ret = price["close"].pct_change()
    oi = load_oi_daily()

    merged = price[["date"]].merge(oi, on="date", how="left")

    events = find_crash_events(price, ret, TOP_N_CRASHES, OI_START)
    print(f"\nOI 数据范围: {oi['date'].min().date()} ~ {oi['date'].max().date()}")
    print(f"识别到 {len(events)} 次 2020-09 后的崩盘事件: "
          + ", ".join(str(price['date'].iloc[i].date()) for i in events))

    for i in events:
        d = price["date"].iloc[i].date()
        r = ret.iloc[i]
        print(f"\n--- 事件: BTC-futures {d}  当日跌幅 {r:+.2%} ---")
        print(f"  {'日期':<12} {'当日收益':>9} {'OI(张)':>14} {'OI变化%':>9} "
              f"{'大户多空持仓比':>14} {'吃单买卖比':>10}")
        lo = max(0, i - WINDOW_DAYS)
        hi = min(len(price), i + WINDOW_DAYS + 1)
        oi_window_start = merged["sum_open_interest"].iloc[lo]
        for j in range(lo, hi):
            dj = price["date"].iloc[j].date()
            rj = ret.iloc[j]
            oi_j = merged["sum_open_interest"].iloc[j]
            oi_pct = (oi_j / oi_window_start - 1) if pd.notna(oi_j) and pd.notna(oi_window_start) and oi_window_start > 0 else np.nan
            top_ratio = merged["sum_toptrader_long_short_ratio"].iloc[j]
            taker_ratio = merged["sum_taker_long_short_vol_ratio"].iloc[j]
            marker = " <== 事件日" if j == i else ""
            oi_str = f"{oi_j:,.0f}" if pd.notna(oi_j) else "NaN"
            oi_pct_str = f"{oi_pct:+.1%}" if pd.notna(oi_pct) else "NaN"
            top_str = f"{top_ratio:.3f}" if pd.notna(top_ratio) else "NaN"
            taker_str = f"{taker_ratio:.3f}" if pd.notna(taker_ratio) else "NaN"
            print(f"  {str(dj):<12} {rj:+8.2%} {oi_str:>14} {oi_pct_str:>9} "
                  f"{top_str:>14} {taker_str:>10}" + marker)

    print("\n" + "=" * 100)
    print("提醒：以上为探索性诊断，仅做事件前后OI/持仓比走势观察，不构成任何采纳/证伪结论。")
    print("=" * 100)


if __name__ == "__main__":
    main()
