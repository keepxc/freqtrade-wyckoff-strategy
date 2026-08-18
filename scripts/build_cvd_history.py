"""
数据工程——将 data.binance.vision 月度K线归档聚合为逐币 CVD 基础数据。

输入: user_data/data/cvd_raw/{SYM}-1h-{YYYY}-{MM}.zip
      (币安官方月度归档, CSV列: open_time,open,high,low,close,volume,close_time,
       quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore)
输出: user_data/data/cvd_history/{SYM}-1h-cvd.feather
      列: date(UTC), close, volume, taker_buy_vol, volume_delta
      volume_delta = 2*taker_buy_vol - volume (与 prefetch_cvd.py 同定义)

不预计算 cvd 累计/滚动值——窗口长度是研究自由度，留到分析脚本里定，
避免把参数固化进数据文件。

校验: 逐币打印时间范围、行数、缺口(相邻bar间隔>1h的数量)、与现有
      {SYM}_USDT_USDT-1h-futures.feather 价格数据的重叠期 close 抽样对比。
"""
import glob
import io
import os
import zipfile

import numpy as np
import pandas as pd

RAW_DIR = "./user_data/data/cvd_raw"
OUT_DIR = "./user_data/data/cvd_history"
PRICE_DIR = "./user_data/data/binance/futures"

COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore"]


def load_month(zip_path):
    with zipfile.ZipFile(zip_path) as zf:
        name = zf.namelist()[0]
        raw = zf.read(name)
    df = pd.read_csv(io.BytesIO(raw), header=None, names=COLS, skiprows=0)
    # 有些月份文件带表头，有些不带：仅当首行 open_time 无法解析为数值时才视为表头去掉。
    # 2026-08-17 修复：旧写法 str(...).isdigit() 在 pandas 把该列解析成 float 时
    # 得到 "1580515200000.0"（含小数点，isdigit=False），把合法首行数据误删——
    # 这是此前每月第一根bar缺失的根源（解析bug，非源数据缺失）。
    first = pd.to_numeric(pd.Series([df.iloc[0]["open_time"]]), errors="coerce").iloc[0]
    if pd.isna(first):
        df = df.iloc[1:].copy()
    df["open_time"] = df["open_time"].astype("int64")
    for c in ("close", "volume", "taker_buy_volume"):
        df[c] = df[c].astype(float)
    return df[["open_time", "close", "volume", "taker_buy_volume"]]


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    syms = sorted({os.path.basename(f).split("-1h-")[0] for f in glob.glob(f"{RAW_DIR}/*-1h-*.zip")})
    print(f"发现 {len(syms)} 个币种的月度归档")

    for sym in syms:
        files = sorted(glob.glob(f"{RAW_DIR}/{sym}-1h-*.zip"))
        parts = []
        for f in files:
            try:
                parts.append(load_month(f))
            except Exception as e:
                print(f"  ⚠️ {os.path.basename(f)} 解析失败: {e}")
        if not parts:
            print(f"{sym}: 无有效数据")
            continue
        df = pd.concat(parts, ignore_index=True).drop_duplicates("open_time").sort_values("open_time")
        df["date"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        df["taker_buy_vol"] = df["taker_buy_volume"]
        df["volume_delta"] = 2.0 * df["taker_buy_vol"] - df["volume"]
        out = df[["date", "close", "volume", "taker_buy_vol", "volume_delta"]].reset_index(drop=True)
        out_path = f"{OUT_DIR}/{sym}-1h-cvd.feather"
        out.to_feather(out_path)

        gaps = (out["date"].diff() > pd.Timedelta(hours=1)).sum()
        # 与现有价格数据抽样对比（同时间戳 close 相对偏差）
        price_path = f"{PRICE_DIR}/{sym.replace('USDT', '')}_USDT_USDT-1h-futures.feather"
        check = ""
        if os.path.exists(price_path):
            ref = pd.read_feather(price_path)[["date", "close"]]
            merged = out.merge(ref, on="date", suffixes=("_cvd", "_ref"))
            if len(merged) > 100:
                sample = merged.sample(min(500, len(merged)), random_state=42)
                max_dev = (sample["close_cvd"] / sample["close_ref"] - 1).abs().max()
                check = f"  价格对比: 重叠{len(merged)}bar, 抽样最大偏差{max_dev:.2e}"
        print(f"{sym}: {out['date'].min():%Y-%m-%d} -> {out['date'].max():%Y-%m-%d}  "
              f"{len(out)}bar  缺口{gaps}处{check}")

    print("\n完成。输出目录:", OUT_DIR)


if __name__ == "__main__":
    main()
