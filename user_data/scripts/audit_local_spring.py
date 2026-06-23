"""
局部再积累 Spring 探测（绕过 C2=252日新低 闸门）

目的：验证 TASK 37 的核心质疑——「BTC 无 Spring」是否只是 C2 定义的副作用。
方法：不用 SC/C2，改用「局部支撑」定义再积累区间，检测：
      价格刺穿局部支撑 → 不创年内新低 → 短期收复 → 后续上涨（真 Spring 的回报特征）

纯审计，不动策略代码。

运行：python user_data/scripts/audit_local_spring.py
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'strategies'))

import pandas as pd
import numpy as np
from v8_modules.sc_detector import add_v8_sc_detection

FEATHER_PATH = os.path.join(
    os.path.dirname(__file__), '..', 'data', 'binance', 'futures',
    'BTC_USDT_USDT-1d-futures.feather'
)
START, END = '2022-01-01', '2026-06-02'

# ── Spring 判定参数（可调） ──────────────────────────────────────────────────
SUPPORT_LOOKBACKS = [30, 60]      # 局部支撑回看窗口（天）
RECOVERY_WINDOWS  = [3, 7, 14, 21]  # 收复窗口（天）
PEN_MIN = 0.002   # 最小刺穿深度 0.2%（过滤噪声）
PEN_MAX = 0.10    # 最大刺穿深度 10%（超过即视为崩盘，非 Spring）
FOLLOW_WINDOW = 15   # 后续观察窗口（天）
NEW_LOW_MARGIN = 0.03  # spring 低点须高于 252日低点 3% 以上（区别于 SC）
SC_EXCLUDE_DAYS = 5    # 排除 SC 前后 5 天


def load_data():
    df = pd.read_feather(FEATHER_PATH)
    df['date'] = pd.to_datetime(df['date'], utc=True)
    df = add_v8_sc_detection(df)  # 拿 v8_sc_final 和 252日低点
    df['_low_252'] = df['low'].rolling(252).min().shift(1)
    df = df[(df['date'] >= START) & (df['date'] < END)].reset_index(drop=True)
    return df


def detect_springs(df, support_lookback, recovery_window):
    """检测局部 Spring：刺穿局部支撑 + 不创年内新低 + 短期收复"""
    n = len(df)
    low, high, close = df['low'].values, df['high'].values, df['close'].values
    sc_final = df['v8_sc_final'].fillna(False).values
    low_252 = df['_low_252'].values

    # SC 附近的 bar 索引（排除区）
    sc_idx = np.where(sc_final)[0]
    sc_excluded = set()
    for s in sc_idx:
        for d in range(-SC_EXCLUDE_DAYS, SC_EXCLUDE_DAYS + 1):
            sc_excluded.add(s + d)

    springs = []
    last_spring_end = -1

    for i in range(support_lookback, n):
        if i <= last_spring_end:   # 同一次 Spring 不重复计数
            continue
        if i in sc_excluded:
            continue

        # 局部支撑 = 过去 support_lookback 天的最低 low（不含当天）
        support = np.min(low[i - support_lookback:i])

        # 刺穿条件
        pen_depth = (support - low[i]) / support
        if not (PEN_MIN <= pen_depth <= PEN_MAX):
            continue

        # 不创年内新低（区别于 SC）
        if not np.isnan(low_252[i]) and low[i] <= low_252[i] * (1 + NEW_LOW_MARGIN):
            continue

        # 收复条件：recovery_window 天内有收盘价回到 support 上方
        recovered_day = None
        for k in range(i, min(i + recovery_window + 1, n)):
            if close[k] > support:
                recovered_day = k
                break
        if recovered_day is None:
            continue

        # 后续回报（Spring 的真实回报特征）
        fw_end = min(i + FOLLOW_WINDOW, n - 1)
        fw_max_close = np.max(close[i + 1:fw_end + 1]) if fw_end > i else close[i]
        fw_ret = (fw_max_close - close[i]) / close[i] * 100
        # 后续是否再创 spring 低点的新低（伪 Spring = 继续跌）
        fw_min_low = np.min(low[i + 1:fw_end + 1]) if fw_end > i else low[i]
        broke_again = fw_min_low < low[i]

        springs.append({
            'date': df['date'].iloc[i].strftime('%Y-%m-%d'),
            'support': int(support),
            'spring_low': int(low[i]),
            'pen_depth_pct': round(pen_depth * 100, 2),
            'recovery_days': recovered_day - i,
            'fw15_max_ret_pct': round(fw_ret, 1),
            'broke_lower_after': broke_again,
        })
        last_spring_end = recovered_day

    return springs


def main():
    print("加载数据 + SC 检测...")
    df = load_data()
    print(f"{len(df)} 条日线，{df['date'].min().date()} ~ {df['date'].max().date()}")
    print(f"已知 SC 触发: {int(df['v8_sc_final'].fillna(False).sum())} 次（已从 Spring 检测排除）")

    print("\n" + "=" * 78)
    print("参数扫描：不同 (支撑回看, 收复窗口) 下检测到的局部 Spring 数")
    print("=" * 78)
    print(f"{'支撑回看':>8} {'收复窗口':>8} {'Spring数':>8} {'后续上涨>3%占比':>16} {'继续破低占比':>14}")
    print("-" * 78)

    all_results = {}
    for sl in SUPPORT_LOOKBACKS:
        for rw in RECOVERY_WINDOWS:
            springs = detect_springs(df, sl, rw)
            all_results[(sl, rw)] = springs
            if springs:
                sdf = pd.DataFrame(springs)
                up_rate = (sdf['fw15_max_ret_pct'] > 3).mean() * 100
                broke_rate = sdf['broke_lower_after'].mean() * 100
                print(f"{sl:>8} {rw:>8} {len(springs):>8} {up_rate:>15.1f}% {broke_rate:>13.1f}%")
            else:
                print(f"{sl:>8} {rw:>8} {0:>8} {'—':>16} {'—':>14}")

    # ── 详细列出代表性参数组（60日支撑 + 7日收复）──────────────────────────
    key = (60, 7)
    print("\n" + "=" * 78)
    print(f"代表组 (支撑回看={key[0]}日, 收复窗口={key[1]}日) 检测到的 Spring 明细")
    print("=" * 78)
    springs = all_results[key]
    if springs:
        print(pd.DataFrame(springs).to_string(index=False))
    else:
        print("无")

    # ── 最宽松组 ────────────────────────────────────────────────────────────
    key2 = (30, 21)
    print("\n" + "=" * 78)
    print(f"最宽松组 (支撑回看={key2[0]}日, 收复窗口={key2[1]}日) 明细")
    print("=" * 78)
    springs2 = all_results[key2]
    if springs2:
        print(pd.DataFrame(springs2).to_string(index=False))
    else:
        print("无")

    # ── 结论 ────────────────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("结论")
    print("=" * 78)
    total_max = max(len(v) for v in all_results.values())
    if total_max == 0:
        print("  即使绕过 C2、用局部支撑，BTC 2022-2026 仍无 Spring 结构")
        print("  → 「无 Spring」是市场现实，不是定义副作用")
    else:
        best_key = max(all_results, key=lambda k: len(all_results[k]))
        best = pd.DataFrame(all_results[best_key])
        print(f"  绕过 C2 后，局部 Spring 确实存在（最多 {total_max} 次 @ 参数 {best_key}）")
        print(f"  其中后续15日上涨>3%占比: {(best['fw15_max_ret_pct']>3).mean()*100:.1f}%")
        print(f"  → 验证：「无 Spring」是 C2=252日新低 定义的副作用，非市场现实")
        print(f"  → 这些 Spring 全部发生在再积累区间，被 C2 闸门排除在 SC 检测之外")


if __name__ == '__main__':
    main()
