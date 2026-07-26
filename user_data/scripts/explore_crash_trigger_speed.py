"""
探索性——非结论，禁止引用为证据。不算 TASK，不进证伪表/采纳表。

目的：诊断 TASK102 L4 一票否决指出的问题的真实结构——现有 vol-target 仓位公式
(VOL_LOOKBACK=20d) 在历史闪崩(黑色星期四等)发生时反应是否"太慢"，以及换成
更短窗口(3d)的纯价格波动率是否真的能提前压低杠杆。不引入 CVD/OBV/订单簿等
新数据源(见对话中的理由：新数据源要么历史不可得，要么比直接看价格更慢)。

方法：复刻 DonchianV1.py 的 don_realized_vol 公式（rolling(N).std().shift(1)*sqrt(365)，
纯因果，不引入前视），分别用 N=20（现有）和 N=3（快速候选）算 lev=min(MAX_LEV,0.20/vol)，
在历史最大单日跌幅事件前后±5天逐日展示两者轨迹，直接回答："快窗口是否提前反应"。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
ASSETS = [
    ("BTC-spot", os.path.join(BASE, "data", "binance", "BTC_USDT-1d.feather")),
    ("BTC-futures", os.path.join(BASE, "data", "binance", "futures", "BTC_USDT_USDT-1d-futures.feather")),
]

TARGET_VOL = 0.20
MAX_LEV_GRID = [1.0, 1.5, 2.0]
VOL_SLOW = 20   # 现有 DonchianV1.py 公式
VOL_FAST = 3    # 快速候选
TOP_N_CRASHES = 8


def load(path):
    df = pd.read_feather(path).sort_values('date').reset_index(drop=True)
    df['date'] = pd.to_datetime(df['date']).dt.tz_localize(None)
    return df


def don_realized_vol(close, window):
    ret = close.pct_change()
    return ret.rolling(window).std().shift(1) * np.sqrt(365)


def main():
    print("=" * 100)
    print("探索性——非结论，禁止引用为证据。闪崩发生时 vol-target 仓位反应速度诊断")
    print(f"慢窗口(现有)={VOL_SLOW}d  快窗口(候选)={VOL_FAST}d  MAX_LEV档={MAX_LEV_GRID}")
    print("=" * 100)

    for tag, path in ASSETS:
        df = load(path)
        close = df['close']
        ret = close.pct_change()
        vol_slow = don_realized_vol(close, VOL_SLOW)
        vol_fast = don_realized_vol(close, VOL_FAST)

        print(f"\n{'=' * 100}\n{tag}: {df['date'].iloc[0].date()} ~ {df['date'].iloc[-1].date()}")

        # 历史最大单日跌幅事件（跌幅 top N，不去重相邻日，先看原始排名）
        crash_idx = ret.nsmallest(TOP_N_CRASHES * 3).index  # 多取一些，去重相邻日后截断
        crash_idx_sorted = sorted(crash_idx)
        picked = []
        last = -100
        for i in crash_idx_sorted:
            if i - last > 5:  # 相邻5日内的算同一次事件，只保留跌幅最大的代表日
                picked.append(i)
                last = i
            else:
                # 同一簇内比较跌幅，保留更大的
                if ret.iloc[i] < ret.iloc[picked[-1]]:
                    picked[-1] = i
                last = i
        picked = sorted(picked, key=lambda i: ret.iloc[i])[:TOP_N_CRASHES]
        picked = sorted(picked)

        for i in picked:
            d = df['date'].iloc[i].date()
            print(f"\n--- 事件: {tag} {d}  当日跌幅 {ret.iloc[i]:+.2%} ---")
            print(f"  {'日期':<12} {'当日收益':>9} {'vol20(慢)':>10} {'vol3(快)':>10} "
                  + "".join(f"lev20_L{m:.1f}".rjust(11) + f"lev3_L{m:.1f}".rjust(11) for m in MAX_LEV_GRID))
            for j in range(max(0, i - 5), min(len(df), i + 6)):
                dj = df['date'].iloc[j].date()
                rj = ret.iloc[j]
                vs = vol_slow.iloc[j]
                vf = vol_fast.iloc[j]
                marker = " <== 事件日" if j == i else ""
                row = f"  {str(dj):<12} {rj:+8.2%} {vs:9.1%} {vf:9.1%} "
                for m in MAX_LEV_GRID:
                    l20 = min(m, TARGET_VOL / vs) if pd.notna(vs) and vs > 0 else 1.0
                    l3 = min(m, TARGET_VOL / vf) if pd.notna(vf) and vf > 0 else 1.0
                    row += f"{l20:10.2f} {l3:10.2f} "
                print(row + marker)

            # 当日名义亏损（若按该 lev 持有多头仓位）
            vs_i, vf_i = vol_slow.iloc[i], vol_fast.iloc[i]
            print(f"  事件日名义亏损（若持仓）: ", end="")
            for m in MAX_LEV_GRID:
                l20 = min(m, TARGET_VOL / vs_i) if pd.notna(vs_i) and vs_i > 0 else 1.0
                l3 = min(m, TARGET_VOL / vf_i) if pd.notna(vf_i) and vf_i > 0 else 1.0
                print(f"MAX_LEV={m}: vol20版{ret.iloc[i]*l20:+.1%} / vol3版{ret.iloc[i]*l3:+.1%}   ", end="")
            print()

    print("\n" + "=" * 100)
    print("提醒：以上为探索性诊断，仅回答'快窗口是否提前反应'，不构成任何采纳/证伪结论。")
    print("=" * 100)


if __name__ == "__main__":
    main()
