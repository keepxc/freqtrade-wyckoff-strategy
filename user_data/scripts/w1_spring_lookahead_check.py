"""
W1 第一步：用 W0 截断复算测试检查 Spring/Test 定义的因果性
（见 TASK_NEW/wyckoff_rebuild_tasks.md 原则 W-A、W1）

被测定义来自探索脚本 `explore_spring_test.py`（探索性、非结论），逐参数照搬：
  1. climax C：close[t] 是过去 N_CLIMAX=20 日最低收盘
  2. test t2：t+1..t+T_WINDOW(15) 内，|low[t2]-low[t]|/low[t] <= TOL(3%)，
     且（主定义）vol[t2] < vol[t]*VOL_TEST_RATIO(0.7)
  3. 确认 E：t2 之后 T_CONFIRM(5) 天内首次 close 突破 t2 前 5 日高点

改写要点：原脚本返回事件索引列表，这里改为**指标列**形态（在确认点 E 置 True），
以符合 populate_indicators 的签名，从而可被 W0 检测。改写只改输出形态，
不改任何判定逻辑与参数。

本步骤只回答一个二元问题：**这个定义是否满足 append-only 不变量**。
不涉及任何收益评估——那属于 W1 后续步骤（对照组 + 功效分析），
且必须在因果性通过之后才有意义。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
from lookahead_truncation_test import truncation_test

sys.stdout.reconfigure(encoding="utf-8")

# 与 explore_spring_test.py 逐字一致
N_CLIMAX = 20
T_WINDOW = 15
VOL_TEST_RATIO = 0.7
TOL = 0.03
T_CONFIRM = 5


def _spring_columns(df, require_dry_volume):
    """扫描 Spring 事件，返回 (climax 标记, test 标记, 确认点标记) 三个布尔数组。

    逻辑与 explore_spring_test.py 的 find_patterns() 逐行同源，仅把
    "收集 confirm_e 到列表"改为"在数组对应位置置 True"。
    """
    n = len(df)
    close = df["close"].values
    low = df["low"].values
    high = df["high"].values
    vol = df["volume"].values
    roll_low = df["close"].rolling(N_CLIMAX).min().values

    is_climax = np.zeros(n, dtype=bool)
    is_test = np.zeros(n, dtype=bool)
    is_confirm = np.zeros(n, dtype=bool)

    t = N_CLIMAX
    while t < n:
        if not (close[t] <= roll_low[t]):
            t += 1
            continue
        is_climax[t] = True

        found_test = None
        for t2 in range(t + 1, min(t + 1 + T_WINDOW, n)):
            if abs(low[t2] - low[t]) / low[t] > TOL:
                continue
            if require_dry_volume:
                if vol[t2] < vol[t] * VOL_TEST_RATIO:
                    found_test = t2
                    break
            else:
                found_test = t2
                break
        if found_test is None:
            t += 1
            continue

        t2 = found_test
        if t2 < 5:
            t += 1
            continue
        is_test[t2] = True

        swing_high = high[max(0, t2 - 5):t2].max()
        for e in range(t2 + 1, min(t2 + 1 + T_CONFIRM, n)):
            if close[e] > swing_high:
                is_confirm[e] = True
                break
        t += 1

    return is_climax, is_test, is_confirm


def spring_indicators(df):
    """主定义（要求缩量测试）——待检对象。"""
    out = df.copy()
    c, t, e = _spring_columns(out, require_dry_volume=True)
    out["spring_climax"] = c
    out["spring_test"] = t
    out["spring_confirm"] = e
    return out


def spring_indicators_control(df):
    """对照定义（不要求缩量，原则 W-D 的对照组）——同样需通过因果检查。"""
    out = df.copy()
    c, t, e = _spring_columns(out, require_dry_volume=False)
    out["ctrl_climax"] = c
    out["ctrl_test"] = t
    out["ctrl_confirm"] = e
    return out


def main():
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    datasets = [
        ("BTC-spot", os.path.join(base, "data", "binance", "BTC_USDT-1d.feather")),
        ("BTC-futures", os.path.join(base, "data", "binance", "futures",
                                     "BTC_USDT_USDT-1d-futures.feather")),
    ]

    print("=" * 96)
    print("W1 第一步：Spring/Test 定义的截断复算测试（因果性二元检查）")
    print(f"参数与 explore_spring_test.py 一致：N_CLIMAX={N_CLIMAX} T_WINDOW={T_WINDOW} "
          f"TOL={TOL} VOL_RATIO={VOL_TEST_RATIO} T_CONFIRM={T_CONFIRM}")
    print("=" * 96)

    all_pass = True
    for tag, path in datasets:
        df = pd.read_feather(path).sort_values("date").reset_index(drop=True)
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
        print(f"\n{'=' * 96}\n{tag}: {df['date'].iloc[0].date()} ~ {df['date'].iloc[-1].date()} "
              f" n={len(df)}")

        for name, fn in [("主定义(要求缩量)", spring_indicators),
                         ("对照定义(不要求缩量)", spring_indicators_control)]:
            print(f"\n  --- {name} ---")
            rep = truncation_test(fn, df)
            rep.print_summary()
            all_pass = all_pass and rep.passed

    print("\n" + "=" * 96)
    print("W1 第一步结论")
    print("=" * 96)
    if all_pass:
        print("  ✅ Spring 定义（主定义与对照定义，双数据集）满足 append-only 不变量。")
        print("     可进入 W1 后续步骤：对照组收益评估 + 功效分析（原则 W-D / W-F）。")
    else:
        print("  ❌ 存在违反 —— 该定义在实盘上算不出回测里的值，禁止进入收益评估。")
        print("     须先按报出的确认延迟 d 改写定义（事件 + 显式确认延迟，原则 W-A）。")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
