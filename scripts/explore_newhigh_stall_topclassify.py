"""
探索性/确认性检验（判据已冻结，见 CLAUDE.md 前置门控确认记录 2026-08-10）——
"创新高后长期未再突破" 事件，历史上演变为"周期顶部"还是"中继整理"的比例。

冻结判据：
- 新高定义：日线收盘价创历史新高（各币种独立计算，从各自数据起始日算起）
- 候选事件：创新高后180天内，收盘价从未再创新高（不管回撤多少）
- 顶部判定：候选事件里，180天内最大收盘价回撤 >= 40% -> "顶部"；< 40% -> "中继整理"
- 样本：BTC(2018-01-01起,spot)/ETH(2020-01-01起,futures)/SOL(2020-09-14起,futures) 日线全历史
- 事件去重：同一波连续新高只取"最后一次新高"（此后180天真正未再破）作为事件起点
- 统计：事件数预计个位数~十几个，不做跨资产合并样本量计算，只列全部事件明细+描述性比例
- censored：数据截止日往前180天内的新高事件，180天窗口未走完，标记"观测未满，暂不分类"

数据局限（如实声明）：
- BTC 缺 2013/2017 两轮历史顶部（无该时段数据），样本天然更少
- ETH/SOL 只有2020年后数据，缺早期周期
"""
import pandas as pd

DD_THRESHOLD = 0.40
WINDOW_DAYS = 180

FILES = {
    "BTC": "user_data/data/binance/BTC_USDT-1d.feather",
    "ETH": "user_data/data/binance/futures/ETH_USDT_USDT-1d-futures.feather",
    "SOL": "user_data/data/binance/futures/SOL_USDT_USDT-1d-futures.feather",
}


def find_events(df):
    df = df.sort_values("date").reset_index(drop=True)
    close = df["close"].values
    dates = df["date"].values
    n = len(df)
    running_max = -1.0
    new_high_idx = []
    for i in range(n):
        if close[i] > running_max:
            running_max = close[i]
            new_high_idx.append(i)

    # 事件 = 一波连续新高序列里的"最后一个"，且此后180天内没有再创新高
    events = []
    last_data_date = dates[-1]
    for idx in new_high_idx:
        end_date = pd.Timestamp(dates[idx]) + pd.Timedelta(days=WINDOW_DAYS)
        future_mask = (pd.to_datetime(dates) > pd.Timestamp(dates[idx])) & (pd.to_datetime(dates) <= end_date)
        future_close = close[future_mask]
        broke_again = (future_close > close[idx]).any() if len(future_close) else False
        if broke_again:
            continue  # 180天内又破新高，不算"未再突破"事件

        # 是否观测已满180天
        censored = pd.Timestamp(last_data_date) < end_date
        window_close = close[(pd.to_datetime(dates) >= pd.Timestamp(dates[idx])) & (pd.to_datetime(dates) <= end_date)]
        if len(window_close) == 0:
            continue
        max_dd = 1 - window_close.min() / close[idx]

        events.append({
            "date": pd.Timestamp(dates[idx]).date(),
            "price": close[idx],
            "max_dd_180d": max_dd,
            "censored": censored,
            "label": ("观测未满" if censored else ("顶部" if max_dd >= DD_THRESHOLD else "中继整理")),
        })
    return events


def dedupe_consecutive(events):
    # 相邻事件如果间隔很近（说明是同一波顶部区域反复触发），只保留区域内第一个
    out = []
    for e in events:
        if out and (pd.Timestamp(e["date"]) - pd.Timestamp(out[-1]["date"])).days <= WINDOW_DAYS:
            continue
        out.append(e)
    return out


def main():
    for sym, path in FILES.items():
        df = pd.read_feather(path)
        events = find_events(df)
        events = dedupe_consecutive(events)
        print(f"\n=== {sym}  数据范围 {df['date'].min().date()} -> {df['date'].max().date()}  共{len(events)}个候选事件 ===")
        for e in events:
            print(f"  {e['date']}  price={e['price']:.2f}  180d最大回撤={e['max_dd_180d']*100:.1f}%  判定={e['label']}")

        classified = [e for e in events if e["label"] != "观测未满"]
        n_top = sum(1 for e in classified if e["label"] == "顶部")
        n_mid = sum(1 for e in classified if e["label"] == "中继整理")
        n_cen = len(events) - len(classified)
        total = n_top + n_mid
        if total > 0:
            print(f"  已分类{total}个: 顶部{n_top}({n_top/total*100:.0f}%)  中继整理{n_mid}({n_mid/total*100:.0f}%)  观测未满{n_cen}个(不计入比例)")
        else:
            print("  无已分类事件")


if __name__ == "__main__":
    main()
