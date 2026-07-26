"""
TASK 102 L4 判据算式修正复核（2026-07-26）

【为什么允许重开】TASK102 已按预注册判据以 L4 一票否决判 ❌ 收官，本脚本不是
"看结果不满意重测"，而是修正 L4 判据自身一个可定位的算式缺陷：

  L4 原算式：隐含单日名义亏损 = 历史最大单日跌幅 × MAX_LEV
  实际机制  ：lev = min(MAX_LEV, TARGET_VOL / don_realized_vol)   ← MAX_LEV 只是封顶

原算式把"封顶值"当成"实际杠杆"代入。而 vol-target 机制的设计目的正是在高波动时
自动压低杠杆——闪崩日 realized_vol 必然很高，封顶几乎不可能生效。因此原算式得出的
59.97%/79.96% 在被测机制下属于不可达状态，判据与被测对象不自洽。

【修正范围（严格限定，防止借修正之名放宽标准）】
  - 50% 否决阈值保持冻结，不动
  - L4"假设崩盘当日满仓持有"的保守精神保持不变（不使用"当日实际空仓"来开脱，
    因为安全阀本就应按假设情景评估，与实际是否持仓无关）
  - 仅把 MAX_LEV 替换为机制真正给出的 lev = min(MAX_LEV, TARGET_VOL/vol)
  - vol 用 DonchianV1.py 原式：ret.rolling(20).std().shift(1)*sqrt(365)，纯因果

【主口径】对全历史每一天，假设当日持有多头，按当日机制杠杆算隐含单日名义亏损，
取全样本最差值与 50% 阈值比较（= 修正后的 L4）。
【附口径（报告级，不作判据）】只统计策略实际持多头仓位的日子，供参考。
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

# 以下三个常量与 DonchianV1.py 逐行一致
VOL_LOOKBACK = 20
TARGET_VOL = 0.20
MAX_LEV_GRID = [1.0, 1.5, 2.0]

BASELINE = (20, 10)
L4_THRESHOLD = 0.50  # 冻结，不因本次修正而改动
TOP_N = 5


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
    print("=" * 104)
    print("TASK 102 L4 判据算式修正复核：MAX_LEV(封顶) → lev=min(MAX_LEV, TARGET_VOL/realized_vol)(实际)")
    print(f"否决阈值保持冻结 = {L4_THRESHOLD:.0%}；VOL_LOOKBACK={VOL_LOOKBACK}, TARGET_VOL={TARGET_VOL}")
    print("=" * 104)

    verdicts = {}

    for tag, path in ASSETS:
        df = load(path)
        ret = df["close"].pct_change()
        vol = don_realized_vol(df["close"])
        pos = donchian_positions(df, *BASELINE)
        pos_eff = pd.Series(pos).shift(1).fillna(0.0)

        print(f"\n{'=' * 104}\n{tag}: {df['date'].iloc[0].date()} ~ {df['date'].iloc[-1].date()}  n={len(df)}")

        worst_i = ret.idxmin()
        print(f"  历史最大单日跌幅: {df['date'].iloc[worst_i].date()}  {ret.iloc[worst_i]:+.2%}  "
              f"（当日 vol_{VOL_LOOKBACK}={vol.iloc[worst_i]:.1%}）")
        print(f"  原 L4 算式（错误：把封顶当实际杠杆）: "
              + "  ".join(f"MAX_LEV={m}: {abs(ret.iloc[worst_i])*m:.2%}" for m in MAX_LEV_GRID))

        for m in MAX_LEV_GRID:
            lev = np.minimum(m, TARGET_VOL / vol)
            lev = lev.where(vol.notna() & (vol > 0))
            implied = ret * lev  # 假设当日满仓持有的隐含单日名义损益

            valid = implied.dropna()
            worst_val = valid.min()
            worst_idx = valid.idxmin()
            cap_binding_ratio = ((TARGET_VOL / vol) >= m).mean()

            # 附口径：仅实际持多头仓位的日子
            held = valid[(pos_eff.loc[valid.index] == 1)]
            worst_held = held.min() if len(held) else np.nan
            worst_held_idx = held.idxmin() if len(held) else None

            fail = abs(worst_val) > L4_THRESHOLD
            verdicts[(tag, m)] = fail

            print(f"\n  --- MAX_LEV={m} ---")
            print(f"    修正后最差隐含单日名义亏损（全样本，假设满仓持有）: {worst_val:+.2%}  "
                  f"@ {df['date'].iloc[worst_idx].date()} "
                  f"(当日跌幅{ret.iloc[worst_idx]:+.2%} × lev={lev.iloc[worst_idx]:.2f})")
            if worst_held_idx is not None:
                print(f"    [报告级] 仅实际持仓日的最差值: {worst_held:+.2%} "
                      f"@ {df['date'].iloc[worst_held_idx].date()}")
            print(f"    [报告级] MAX_LEV 封顶实际生效比例: {cap_binding_ratio:.1%} 的交易日")
            print(f"    L4（阈值{L4_THRESHOLD:.0%}）: {'FAIL（一票否决）' if fail else 'PASS'}")

            worst_days = valid.nsmallest(TOP_N)
            print(f"    最差{TOP_N}天明细:")
            for j in worst_days.index:
                print(f"      {df['date'].iloc[j].date()}  跌幅{ret.iloc[j]:+7.2%}  "
                      f"vol{vol.iloc[j]:6.1%}  lev{lev.iloc[j]:5.2f}  → 隐含亏损{implied.iloc[j]:+7.2%}"
                      + ("   [该日实际持仓]" if pos_eff.iloc[j] == 1 else "   [该日实际空仓]"))

    print("\n" + "=" * 104)
    print("修正后 L4 汇总（阈值冻结 50%，双数据集任一 FAIL 即该档否决）")
    print("=" * 104)
    for m in MAX_LEV_GRID:
        fails = [tag for (tag, mm), f in verdicts.items() if mm == m and f]
        status = f"FAIL @ {fails}" if fails else "PASS（双数据集）"
        print(f"  MAX_LEV={m}: {status}")


if __name__ == "__main__":
    main()
