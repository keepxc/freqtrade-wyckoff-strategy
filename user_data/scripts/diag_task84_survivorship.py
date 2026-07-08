"""TASK 84 附带诊断：幸存者偏差压力测试（剔除 2020-10 时点的后视赢家）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd

from audit_task84_breadth import FUT, START, UNIVERSE, sleeve_returns, stats


def main():
    sleeves = {}
    for sym in UNIVERSE:
        df = pd.read_feather(os.path.join(FUT, f"{sym}_USDT_USDT-1d-futures.feather"))
        df = df.sort_values('date').reset_index(drop=True)
        df['date'] = pd.to_datetime(df['date']).dt.tz_localize(None)
        r = sleeve_returns(df)
        sleeves[sym] = r[r.index >= pd.Timestamp(START)]
    mat = pd.DataFrame(sleeves).dropna()
    btc = mat['BTC']
    # 诊断1：剔除 2020-10 非头部的后视赢家 SOL/AVAX/DOGE
    core12 = mat.drop(columns=['SOL', 'AVAX', 'DOGE']).mean(axis=1)
    # 诊断2：仅 2020-10 当时真实市值头部币（数据可得的 11 个）
    top11 = mat[['BTC', 'ETH', 'XRP', 'BCH', 'BNB', 'LINK', 'DOT', 'ADA',
                 'LTC', 'TRX', 'ETC']].mean(axis=1)
    mid = len(mat) // 2
    for name, r in [("BTC-only", btc), ("12coin_ex_winners", core12),
                    ("11coin_2020top", top11)]:
        t, a, d, c = stats(r)
        _, a1, d1, c1 = stats(r.iloc[:mid])
        _, a2, d2, c2 = stats(r.iloc[mid:])
        print(f"{name:<18s}: ann {a:+.1%}  MaxDD {d:.1%}  Calmar {c:.2f}  "
              f"| halfCalmar {c1:.2f}/{c2:.2f}")


if __name__ == "__main__":
    main()
