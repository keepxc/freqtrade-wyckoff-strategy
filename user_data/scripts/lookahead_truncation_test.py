"""
W0：截断复算测试框架（威科夫重建，见 TASK_NEW/wyckoff_rebuild_tasks.md 原则 W-A）

检测的不变量：**指标发布即不可变（append-only）**

    ∀ t, ∀ 列 c:   f(data[:t+1])[c].iloc[t] == f(data)[c].iloc[t]

物理含义不是抽象的"未来函数"，而是 **backtest/live 一致性**：Freqtrade 回测对全量
dataframe 调一次 populate_indicators，实盘对滚动窗口反复调用。违反该不变量 ⇔
该指标实盘算出的值与回测不同 ⇔ 回测收益不可实现（V8 即为此损失 98.5% 的利润）。

四个关键设计决定（详见 wyckoff_rebuild_tasks.md W0）：

1. **只比 .iloc[t]，但每个 t 用它自己的截断**。整段前缀比对会让一个回溯标记在所有
   t 上刷屏，淹没定位。逐 t 截断仍能抓住回溯：ZigZag 在 t 确认发生于 t-5 的转折，
   当抽样点轮到 t-5 时，f(df[:t-4]).iloc[t-5]=False 与 f(df).iloc[t-5]=True 不等。

2. **抽样必须分层**。威科夫事件稀疏（3000 根日线可能仅 ~40 个 Spring），而未来函数
   只在事件 bar 上暴露（非事件 bar 上两边都是 False，怎么截断都相等）。均匀抽样会
   大概率全绿通过并给出虚假安全感。故：布尔/事件列全量 True 的 bar 一个不漏地纳入。

3. **顺带测出确认延迟 d**：让截断点后推，f(df[:t+1+d]).iloc[t]，找最小的 d 使该值
   此后不再变化。d 即该列的确认延迟下界，直接回答"shift 几根"，无需拍脑袋
   （V8 的 shift(3) 即为拍出来的，事后证明补偿不足）。

4. **验收双向**：只测通过项证明不了工具有检出能力。必须同时有阳性对照（已知因果的
   实现，须全绿）与阴性对照（故意植入未来函数，须报出违反）。见文件末自检。

实现说明（两处对设计稿的偏离，均为实现中发现问题后的修正）：

  (a) 设计稿提到 d 用二分搜索，实际改为线性扫描——"值随截断点后推而稳定"并非严格
      单调，二分可能在非单调情形下给出错误的 d。d_max 较小（默认 20）且只对已违反的
      单元格测 d，线性扫描成本可接受，正确性更有保障。

  (b) **违反判定沿截断长度阶梯比对，而非只比 f(df[:t+1]) 与 f(df) 两个点。**
      首版只比这两点，自检时发现阴性对照的检出方向与预测相反（预测"截断=False /
      全量=True，d≈5"，实测全是"截断=True / 全量=False，d=1~2"）——根因是比对点 t
      恒为截断前缀的最后一根，此时前向窗口退化为空，回溯型指标在截断时可能与全量
      **偶然相等**。对"粘性"未来函数（一旦标记 True 便不再改变，V8 的 ZigZag 正属
      此类）这会造成**漏检**。故改为沿阶梯 d ∈ {0,1,2,3,5,8,13,21,34}∩[0,d_max]
      逐个比对，任一不等即判违反。首版"通过"是运气而非能力，此为必要修正。

用法：
    python user_data/scripts/lookahead_truncation_test.py          # 跑自检（双向验收）

    # 或作为库使用：
    from lookahead_truncation_test import truncation_test
    report = truncation_test(my_populate_indicators, df)
    report.print_summary()
    assert report.passed
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

FLOAT_RTOL = 1e-9
FLOAT_ATOL = 1e-12
DEFAULT_N_SAMPLES = 200
DEFAULT_D_MAX = 20


class Violation:
    def __init__(self, column, t, date, truncated_value, full_value, delay=None, delay_note=""):
        self.column = column
        self.t = t
        self.date = date
        self.truncated_value = truncated_value
        self.full_value = full_value
        self.delay = delay           # 确认延迟下界；None 表示未测/未在 d_max 内稳定
        self.delay_note = delay_note


class TruncationReport:
    def __init__(self, n_samples, columns_checked, exempted, violations, deterministic):
        self.n_samples = n_samples
        self.columns_checked = columns_checked
        self.exempted = exempted
        self.violations = violations
        self.deterministic = deterministic

    @property
    def passed(self):
        return self.deterministic and not self.violations

    @property
    def violated_columns(self):
        seen = []
        for v in self.violations:
            if v.column not in seen:
                seen.append(v.column)
        return seen

    def print_summary(self, max_detail=10):
        print(f"  抽样时点数: {self.n_samples}   受检列: {len(self.columns_checked)} "
              f"{self.columns_checked}")
        if self.exempted:
            print(f"  ⚠️ 显式豁免列（必须有理由）: {self.exempted}")
        if not self.deterministic:
            print("  ❌ 函数非确定性：f(df) 两次调用结果不一致。")
            print("     所有'违反'均不可信，请先排除随机性/系统时间依赖后重测。")
            return
        if not self.violations:
            print("  ✅ 通过：所有受检列在所有抽样时点上满足 append-only 不变量")
            return

        print(f"  ❌ 违反 append-only 不变量：{len(self.violations)} 处，"
              f"涉及列 {self.violated_columns}")
        by_col = {}
        for v in self.violations:
            by_col.setdefault(v.column, []).append(v)
        for col, vs in by_col.items():
            delays = [v.delay for v in vs if v.delay is not None]
            if delays:
                dtxt = (f"确认延迟下界 d: 最小{min(delays)} / 中位{int(np.median(delays))} "
                        f"/ 最大{max(delays)} 根 bar")
            else:
                dtxt = "确认延迟 d: 未能在 d_max 内稳定（该列可能依赖任意远的未来）"
            print(f"\n    列 [{col}]：{len(vs)} 处违反，{dtxt}")
            for v in vs[:max_detail]:
                extra = f"  d={v.delay}" if v.delay is not None else f"  d>{v.delay_note}"
                print(f"      t={v.t} ({v.date})  截断时={v.truncated_value!r}  "
                      f"全量时={v.full_value!r}{extra}")
            if len(vs) > max_detail:
                print(f"      ...另有 {len(vs) - max_detail} 处")


def _stability_ladder(d_max):
    """截断长度阶梯：稀疏覆盖 0..d_max，兼顾成本与非单调情形的检出。"""
    ladder = [0, 1, 2, 3, 5, 8, 13, 21, 34]
    out = [d for d in ladder if d <= d_max]
    if d_max not in out:
        out.append(d_max)
    return out


def _values_equal(a, b):
    """NaN==NaN 视为相等；浮点用容差；布尔/整数/对象用严格相等。"""
    a_na = a is None or (isinstance(a, float) and np.isnan(a)) or a is pd.NaT
    b_na = b is None or (isinstance(b, float) and np.isnan(b)) or b is pd.NaT
    try:
        a_na = a_na or bool(pd.isna(a))
        b_na = b_na or bool(pd.isna(b))
    except (TypeError, ValueError):
        pass
    if a_na or b_na:
        return a_na and b_na
    if isinstance(a, (bool, np.bool_)) or isinstance(b, (bool, np.bool_)):
        return bool(a) == bool(b)
    if isinstance(a, (int, np.integer)) and isinstance(b, (int, np.integer)):
        return int(a) == int(b)
    if isinstance(a, (float, np.floating)) and isinstance(b, (float, np.floating)):
        return bool(np.isclose(a, b, rtol=FLOAT_RTOL, atol=FLOAT_ATOL))
    return a == b


def _select_sample_points(full_out, columns, n_samples, min_t, n_bars, seed):
    """分层抽样（设计决定 2）：事件 bar 一个不漏，其余按波动与随机补足。"""
    rng = np.random.RandomState(seed)
    picked = set()

    # 1) 布尔/低基数列的全部 True/事件 bar —— 未来函数只在这些 bar 上暴露
    for c in columns:
        s = full_out[c]
        is_boolish = s.dtype == bool or (
            s.dropna().isin([0, 1, True, False]).all() and s.dropna().nunique() <= 2
        )
        if not is_boolish:
            continue
        event_idx = np.flatnonzero(s.fillna(False).astype(bool).values)
        picked.update(int(i) for i in event_idx if i >= min_t)

    # 2) 数值列的局部极值/高波动时点
    if "close" in full_out.columns:
        ret = full_out["close"].pct_change().abs()
        vol_idx = ret.iloc[min_t:].nlargest(max(1, n_samples // 4)).index
        picked.update(int(i) for i in vol_idx)

    # 3) 随机补足
    pool = np.arange(min_t, n_bars)
    if len(pool) > 0:
        need = max(0, n_samples - len(picked))
        if need > 0:
            extra = rng.choice(pool, size=min(need, len(pool)), replace=False)
            picked.update(int(i) for i in extra)

    return sorted(picked)


def truncation_test(f, df, n_samples=DEFAULT_N_SAMPLES, d_max=DEFAULT_D_MAX,
                    exempt_columns=None, min_t=None, seed=20260726, verbose=False):
    """
    对指标函数 f 做截断复算测试。

    f            : df -> df 的纯函数（形如 populate_indicators），必须确定性
    df           : 输入数据（含 date/OHLCV）
    n_samples    : 目标抽样时点数（事件 bar 会全量纳入，实际数量可能超过此值）
    d_max        : 确认延迟 d 的最大搜索深度
    exempt_columns: 显式豁免列名（必须有理由；不允许默认豁免）
    min_t        : 最小抽样起点（跳过预热期），默认取输入长度的 10%
    """
    exempt = set(exempt_columns or [])
    n_bars = len(df)
    if min_t is None:
        min_t = max(1, n_bars // 10)

    # 前置：确定性检查。若 f 非确定性，一切"违反"都是噪声
    out_a = f(df.copy())
    out_b = f(df.copy())
    new_cols = [c for c in out_a.columns if c not in df.columns]
    deterministic = True
    for c in new_cols:
        if not out_a[c].equals(out_b[c]):
            deterministic = False
            break
    if not deterministic:
        return TruncationReport(0, [], sorted(exempt), [], deterministic=False)

    full_out = out_a
    columns = [c for c in new_cols if c not in exempt]
    if not columns:
        raise ValueError("f 未产生任何受检的新列（或全部被豁免）")

    sample_ts = _select_sample_points(full_out, columns, n_samples, min_t, n_bars, seed)

    cache = {}

    def run_prefix(length):
        if length not in cache:
            cache[length] = f(df.iloc[:length].copy())
        return cache[length]

    violations = []
    for k, t in enumerate(sample_ts):
        if verbose and k % 50 == 0:
            print(f"    进度 {k}/{len(sample_ts)}...")
        ladder = [d for d in _stability_ladder(d_max) if t + 1 + d <= n_bars]
        for c in columns:
            v_full = full_out[c].iloc[t]

            # 沿截断长度阶梯比对（见文件头实现说明 5）：只比 t+1 与全量两个点会漏检
            # "粘性"未来函数——t 处若恰为前缀末根，前向窗口退化，可能与全量偶然相等。
            first_bad_d, v_trunc = None, None
            for d in ladder:
                out_d = run_prefix(t + 1 + d)
                if c not in out_d.columns:
                    first_bad_d, v_trunc = d, "<列不存在>"
                    break
                if not _values_equal(out_d[c].iloc[t], v_full):
                    first_bad_d, v_trunc = d, out_d[c].iloc[t]
                    break
            if first_bad_d is None:
                continue

            # 确认延迟 d：最小的 d 使该值此后不再变化（线性扫描，见实现说明）
            delay, note = None, str(d_max)
            for d in range(first_bad_d, d_max + 1):
                if t + 1 + d > n_bars:
                    note = f"{d - 1}(数据末端)"
                    break
                if _values_equal(run_prefix(t + 1 + d)[c].iloc[t], v_full):
                    delay = d
                    break
            violations.append(Violation(c, t, df["date"].iloc[t], v_trunc, v_full,
                                        delay=delay, delay_note=note))

    return TruncationReport(len(sample_ts), columns, sorted(exempt), violations,
                            deterministic=True)


# ============================================================================
# 自检：双向验收（设计决定 4）
# 只测通过项证明不了工具有检出能力，阳性/阴性对照缺一不可。
# ============================================================================

def positive_control(df):
    """阳性对照：DonchianV1 的因果实现（通道 + vol-target 波动率），须全绿。"""
    out = df.copy()
    out["don_upper"] = out["high"].rolling(20).max().shift(1)
    out["don_lower_exit"] = out["low"].rolling(10).min().shift(1)
    ret = out["close"].pct_change()
    out["don_realized_vol"] = ret.rolling(20).std().shift(1) * np.sqrt(365)
    out["don_break"] = out["close"] > out["don_upper"]
    return out


def negative_control(df):
    """阴性对照：最小 ZigZag 复现，回溯标记局部低点（模拟 V8 的 WaveAnalyzer）。

    is_pivot_low[i] = (low[i] == min(low[i-5 : i+6]))  ← i+6 即未来函数
    期望：报出违反，且确认延迟 d ≈ 5。
    """
    out = df.copy()
    w = 5
    lows = out["low"].values
    pivot = np.zeros(len(out), dtype=bool)
    for i in range(len(out)):
        lo = max(0, i - w)
        hi = min(len(out), i + w + 1)
        pivot[i] = lows[i] == lows[lo:hi].min()
    out["zz_pivot_low"] = pivot
    return out


def negative_control_sticky(df):
    """阴性对照 2：**粘性回溯标记**——V8 WaveAnalyzer/ZigZag 的真实形态。

    与 negative_control 的区别（两者是不同形态的未来函数，签名不同，都要测）：
      negative_control        : min over [i-5, i+5]。真 pivot 在截断时也为 True
                                （窗口变小不改变"我是最小"），违反表现为"截断时误判"
      negative_control_sticky : 在 bar i 确认后，**回溯**把 i-LAG 置 True。截断到
                                i-LAG 时尚未确认（False），全量时为 True —— 这正是
                                V8 "回测虚高 75,687%/99%WR" 的机制

    期望：报出违反，方向为「截断=False / 全量=True」，且确认延迟 d ≈ LAG=5。
    """
    out = df.copy()
    lag = 5
    lows = out["low"].values
    marked = np.zeros(len(out), dtype=bool)
    for i in range(lag, len(out)):
        j = i - lag
        # 在 i 时刻回看：j 是否为已过去窗口的最低点 → 确认后回溯标记 j
        if lows[j] == lows[max(0, j - lag):i + 1].min():
            marked[j] = True
    out["zz_wave_start"] = marked
    return out


def main():
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    path = os.path.join(base, "data", "binance", "BTC_USDT-1d.feather")
    df = pd.read_feather(path).sort_values("date").reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)

    print("=" * 96)
    print("W0 截断复算测试框架 —— 自检（双向验收）")
    print(f"数据: BTC-spot 日线 {df['date'].iloc[0].date()} ~ {df['date'].iloc[-1].date()}  n={len(df)}")
    print("=" * 96)

    print("\n--- 阳性对照：DonchianV1 因果实现（期望 ✅ 全绿）---")
    rep_pos = truncation_test(positive_control, df)
    rep_pos.print_summary()

    print("\n--- 阴性对照 1：窗口型未来函数 min over [i-5,i+5]（期望 ❌ 报出违反）---")
    rep_neg = truncation_test(negative_control, df)
    rep_neg.print_summary()

    print("\n--- 阴性对照 2：粘性回溯标记（V8 ZigZag 形态，期望 ❌ 违反且 d≈5）---")
    rep_sticky = truncation_test(negative_control_sticky, df)
    rep_sticky.print_summary()

    print("\n" + "=" * 96)
    print("验收结论")
    print("=" * 96)
    ok_pos = rep_pos.passed
    ok_neg = (not rep_neg.passed) and ("zz_pivot_low" in rep_neg.violated_columns)
    sticky_vs = [v for v in rep_sticky.violations if v.column == "zz_wave_start"]
    # V8 形态的判别特征：方向为 截断=False / 全量=True
    sticky_dir = [v for v in sticky_vs if (not bool(v.truncated_value)) and bool(v.full_value)]
    sticky_delays = [v.delay for v in sticky_vs if v.delay is not None]
    ok_sticky = bool(sticky_dir)
    print(f"  阳性对照全绿（工具不误报）          : {'PASS' if ok_pos else 'FAIL'}")
    print(f"  阴性1 窗口型未来函数被检出          : {'PASS' if ok_neg else 'FAIL'}")
    print(f"  阴性2 粘性回溯标记被检出(V8 形态)   : {'PASS' if ok_sticky else 'FAIL'}"
          f"   [方向'截断=False/全量=True' {len(sticky_dir)}/{len(sticky_vs)} 处"
          + (f"，d 中位 {int(np.median(sticky_delays))}]" if sticky_delays else "]"))
    all_ok = ok_pos and ok_neg and ok_sticky
    if all_ok:
        print("\n  ✅ W0 验收通过：工具不误报，且对两种不同形态的未来函数均有检出力。")
    else:
        print("\n  ❌ W0 验收未通过——工具本身不可用，此时任何'全绿'结果均无意义。")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
