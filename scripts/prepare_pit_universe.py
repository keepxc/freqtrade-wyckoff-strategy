"""
Point-in-Time 加密货币市值宇宙构造（TASK116 预注册配套脚本）
=============================================================
背景：TASK84 因"候选池只取当前存活标的"产生幸存者偏差而判⭕。本脚本重建
2021-2026 逐月点时 Top15 市值名单，候选池显式包含历史上曾进入 Top15 但
后续崩溃/退市的标的（LUNA、FTT等），避免同一偏差。

用户前置门控确认(2026-08-15)：
1. 候选池范围：2021-2026年间任何时点曾进入市值前30的标的（约40-60个），
   含已归零/退市标的
2. 月度重构（每月1号），严格用"T月1号00:00前可获得"的市值数据排序，
   不得用月内峰值市值追溯（防前视）
3. 数据源：原计划 CoinGecko 免费API，但实测其 `/coins/{id}/market_chart` 端点对免费/demo
   key 有硬性365天历史回溯限制（error_code 10012），2021-2026范围结构性不可行。改用
   cryptoCMD（抓取 coinmarketcap.com 历史数据页，非官方付费API，无该365天限制，实测
   BTC/FTT/CEL/LUNC 均可拿到完整历史含 Market Cap 列）。已知陷阱：LUNA 崩盘后 CMC 把
   `LUNA` 代号让渡给新发的 Terra 2.0（2022-05-28后），崩盘前的历史数据要用 `LUNC`
   （Luna Classic）代号才能拿到，候选池已按此修正——这是过proxy实测发现的真实陷阱，
   提醒：其余候选池标的若也有过改名/分叉，需类似核实，本次仅核实了三个已知"崩溃类"标的
   （LUNA→LUNC、FTT、CEL），未逐一核实全部50个候选是否有类似隐藏改名。
4. 稳定币不计入排名（USDT/USDC/DAI等，非趋势交易标的）
5. 输出 pit_universe_history.csv：date, rank, coingecko_id, symbol, market_cap

已知局限（需在TASK116预注册文档中声明）：
- 本脚本只产出"市值宇宙名单"，不产出可执行的期货OHLCV——回测执行价格层
  仍需后续脚本按"本地Binance期货数据优先，缺口用CoinGecko现货代理"的
  规则单独抓取，且现货代理价格不代表历史期货实际可执行价格/深度
- 候选池是人工预先列出的（下方 CANDIDATE_POOL），如果遗漏了某个历史上
  进入过Top15但本脚本作者未列入的标的，仍会造成局部幸存者偏差——这是
  人工候选池方法的结构性限制，不是本脚本可以自动解决的问题，需要人工
  抽样复核阶段重点检查（如用户要求的"2022-05名单含LUNA"这类已知锚点）
"""
import os
import time
from pathlib import Path
from datetime import datetime, timezone

import pandas as pd

os.environ.setdefault("HTTPS_PROXY", os.environ.get("LOCAL_PROXY", "http://127.0.0.1:10809"))
os.environ.setdefault("HTTP_PROXY", os.environ.get("LOCAL_PROXY", "http://127.0.0.1:10809"))

import cryptocmd.utils as _cmc_utils
# cryptoCMD 默认 UA 过短('Mozilla/5.0')会被 CMC 反爬拦截（实测复现：SSL/Proxy层报错，
# 换完整浏览器UA后同样的请求稳定成功）——这不是代理或网络问题，是请求指纹被拦截。
_cmc_utils._HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}
from cryptocmd import CmcScraper

OUT_PATH = Path(r"E:\freqtrade\user_data\pit_universe_history.csv")
FETCH_START = "01-01-2020"
FETCH_END = "16-08-2026"

# 候选池：2021-2026 曾进入市值前30量级的标的（人工列举，含已崩溃/退市标的）
# cmc_symbol -> 展示symbol（正常情况下两者相同，仅当CMC因改名/分叉导致symbol不再指向
# 历史资产时才不同——目前仅确认 LUNA 一例，见下方注释）
CANDIDATE_POOL = {
    "BTC": "BTC", "ETH": "ETH", "BNB": "BNB", "XRP": "XRP",
    "ADA": "ADA", "SOL": "SOL", "DOGE": "DOGE", "DOT": "DOT",
    "AVAX": "AVAX", "MATIC": "MATIC", "LINK": "LINK",
    "LTC": "LTC", "BCH": "BCH", "ATOM": "ATOM",
    "ETC": "ETC", "TRX": "TRX", "UNI": "UNI",
    "XLM": "XLM", "FIL": "FIL", "NEAR": "NEAR", "APT": "APT",
    "ARB": "ARB", "OP": "OP", "SUI": "SUI", "SHIB": "SHIB",
    "XMR": "XMR", "ALGO": "ALGO", "VET": "VET",
    "ICP": "ICP", "HBAR": "HBAR", "GRT": "GRT",
    "AAVE": "AAVE", "MKR": "MKR", "FTM": "FTM", "SAND": "SAND",
    "MANA": "MANA", "AXS": "AXS", "THETA": "THETA",
    "EGLD": "EGLD", "OKB": "OKB", "LEO": "LEO",
    # 已崩溃/退市标的——PIT无偏候选池的关键项，不能省略
    # 实测陷阱：CMC把"LUNA"代号让渡给崩盘后新发的Terra 2.0（2022-05-28后），
    # 崩盘前的旧链历史数据必须用"LUNC"(Luna Classic)代号才能取到，否则拿到的是
    # 新链回填的0市值垃圾数据（已实测复现并确认）。
    "LUNC": "LUNA",   # 2022-05 崩盘归零（展示为LUNA，抓取用LUNC代号）
    "FTT": "FTT",     # 2022-11 FTX 崩盘后归零
    "CEL": "CEL",     # 2022-06 Celsius 破产
    "WAVES": "WAVES",
    "MIOTA": "MIOTA",
    "XEM": "XEM",
    "EOS": "EOS",
    "ZEC": "ZEC",
    "DASH": "DASH",
    "CHZ": "CHZ",
    "HNT": "HNT",
}

STABLECOIN_IDS = set()  # 候选池本身已不含稳定币，此处留空作为显式排除位


def fetch_market_cap_history(cmc_symbol: str, retries: int = 4):
    """拉取单币种全历史日度市值序列（cryptoCMD/CoinMarketCap）。返回 DataFrame[date, market_cap] 或 None。"""
    for attempt in range(retries):
        try:
            scraper = CmcScraper(cmc_symbol, FETCH_START, FETCH_END)
            df_raw = scraper.get_dataframe()
        except Exception as e:
            print(f"  [{cmc_symbol}] error (attempt {attempt+1}): {repr(e)[:150]}")
            time.sleep(5)
            continue
        if df_raw is None or df_raw.empty:
            print(f"  [{cmc_symbol}] empty result (attempt {attempt+1})")
            time.sleep(5)
            continue
        df = df_raw[["Date", "Market Cap"]].rename(columns={"Date": "date", "Market Cap": "market_cap"})
        df["date"] = pd.to_datetime(df["date"], utc=True).dt.normalize()
        df = df.groupby("date", as_index=False)["market_cap"].last().sort_values("date").reset_index(drop=True)
        return df
    return None


def build_monthly_snapshots(all_history: dict, start="2021-01-01", end="2026-08-01"):
    """按月首(T月1号00:00前可得数据)重建Top15名单，严格因果——只用<=该月首日期的最后一条市值记录。"""
    month_starts = pd.date_range(start, end, freq="MS", tz="utc")
    rows = []
    for month_start in month_starts:
        snapshot = []
        for cmc_symbol, symbol in CANDIDATE_POOL.items():
            df = all_history.get(cmc_symbol)
            if df is None or df.empty:
                continue
            # 严格因果：只用严格早于月首的记录，不用月首当天或之后的数据
            hist = df.loc[df["date"] < month_start]
            if hist.empty:
                continue
            latest = hist.iloc[-1]
            # 数据过旧（超过35天无更新，如已下架）视为该月不在场，不参与排名
            if (month_start - latest["date"]).days > 35:
                continue
            snapshot.append((cmc_symbol, symbol, latest["market_cap"], latest["date"]))
        snapshot.sort(key=lambda x: x[2], reverse=True)
        for rank, (cmc_symbol, symbol, mcap, asof_date) in enumerate(snapshot[:15], start=1):
            rows.append({
                "date": month_start.date().isoformat(),
                "rank": rank,
                "cmc_symbol": cmc_symbol,
                "symbol": symbol,
                "market_cap": mcap,
                "data_asof": asof_date.date().isoformat(),
            })
    return pd.DataFrame(rows)


def main():
    print(f"候选池共 {len(CANDIDATE_POOL)} 个标的，开始逐个拉取市值历史（cryptoCMD/CoinMarketCap，经代理）...")
    all_history = {}
    ok, failed = [], []
    for cmc_symbol in CANDIDATE_POOL:
        df = fetch_market_cap_history(cmc_symbol)
        if df is None:
            print(f"  [FAIL] {cmc_symbol}")
            failed.append(cmc_symbol)
        else:
            print(f"  [OK] {cmc_symbol}: {len(df)} 天, {df['date'].min().date()} -> {df['date'].max().date()}")
            all_history[cmc_symbol] = df
            ok.append(cmc_symbol)
        time.sleep(2)  # 反爬限速保护

    print(f"\n成功 {len(ok)}/{len(CANDIDATE_POOL)}；失败：{failed}")
    if failed:
        print("失败标的需人工确认CMC symbol是否正确（是否发生过改名/分叉，如LUNA案例）。")

    if not all_history:
        print("\n全部标的拉取失败，跳过月度重建，不写出CSV。")
        return

    print("\n重建月度 Top15 点时名单...")
    universe_df = build_monthly_snapshots(all_history)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    universe_df.to_csv(OUT_PATH, index=False)
    if universe_df.empty or "date" not in universe_df.columns:
        print(f"写入 {OUT_PATH}，但结果为空（0行）——检查因果过滤窗口/候选池覆盖月份。")
        return
    print(f"写入 {OUT_PATH}，共 {len(universe_df)} 行（{universe_df['date'].nunique()} 个月度快照）")

    # 用户指定的人工抽样锚点：2022-05 名单应含 LUNA
    check = universe_df[(universe_df["date"] == "2022-05-01") & (universe_df["symbol"] == "LUNA")]
    if not check.empty:
        print(f"\n[抽样复核通过] 2022-05-01 名单含 LUNA，排名第 {int(check.iloc[0]['rank'])}")
    else:
        print("\n[抽样复核未通过] 2022-05-01 名单不含 LUNA —— 需人工排查（数据缺失/候选池遗漏/因果过滤过严）")


if __name__ == "__main__":
    main()
