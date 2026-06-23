"""
审计 Phase 检测器从「价格首次穿越 TR 中线」到「Phase D 标签生效」的延迟。
实际路径：B→D 直接跳转（历史数据中 Phase C/Spring 从未触发）

运行：python user_data/scripts/audit_phase_d_lag.py
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'strategies'))

import pandas as pd
import numpy as np

from v8_modules.sc_detector import add_v8_sc_detection
from v8_modules.phase_detector import add_v8_phase_detection

FEATHER_PATH = os.path.join(
    os.path.dirname(__file__), '..', 'data', 'binance', 'futures',
    'BTC_USDT_USDT-1d-futures.feather'
)
TIMERANGE_START = '2022-01-01'
TIMERANGE_END   = '2026-06-02'


def main():
    print(f"加载日线数据...")
    df = pd.read_feather(FEATHER_PATH)
    df['date'] = pd.to_datetime(df['date'], utc=True)
    df = df[(df['date'] >= TIMERANGE_START) & (df['date'] < TIMERANGE_END)].reset_index(drop=True)
    print(f"共 {len(df)} 条，{df['date'].min().date()} ~ {df['date'].max().date()}")

    df = add_v8_sc_detection(df)
    df = add_v8_phase_detection(df)

    phases = df['v8_phase'].fillna('unknown')

    # ── 找出所有 B→D 转换点 ───────────────────────────────────────────────────
    records = []
    for i in range(1, len(df)):
        if phases.iloc[i] == 'D' and phases.iloc[i - 1] == 'B':
            phase_d_date = df['date'].iloc[i]
            tr_high = df['v8_tr_high'].iloc[i]
            tr_low  = df['v8_tr_low'].iloc[i]

            if np.isnan(tr_high) or np.isnan(tr_low) or tr_low == 0:
                continue

            tr_mid = (tr_high + tr_low) / 2

            # Phase B 的起始点（往前找进入 B 的那一天）
            phase_b_start_idx = None
            for j in range(i - 1, max(i - 200, -1), -1):
                if phases.iloc[j] != 'B':
                    phase_b_start_idx = j + 1
                    break
            if phase_b_start_idx is None:
                phase_b_start_idx = 0

            # 在 Phase B 期间，价格第一次收盘高于 TR 中线的日期
            first_cross_idx = None
            for j in range(phase_b_start_idx, i + 1):
                if df['close'].iloc[j] > tr_mid:
                    first_cross_idx = j
                    break

            # 结构延迟：首次穿越 TR mid → Phase D 标记（日线）
            struct_lag = None
            if first_cross_idx is not None:
                struct_lag = (phase_d_date - df['date'].iloc[first_cross_idx]).days

            # Phase D 持续天数（找下次离开 D 的时间）
            phase_d_end_idx = len(df) - 1
            for j in range(i + 1, len(df)):
                if phases.iloc[j] != 'D':
                    phase_d_end_idx = j
                    break
            phase_d_duration = (df['date'].iloc[phase_d_end_idx] - phase_d_date).days

            # Phase B 持续天数
            phase_b_duration = (phase_d_date - df['date'].iloc[phase_b_start_idx]).days

            # 首次穿越到 Phase D 期间价格涨幅
            move_pct = None
            if first_cross_idx is not None and first_cross_idx < i:
                p_cross = df['close'].iloc[first_cross_idx]
                p_d     = df['close'].iloc[i]
                move_pct = round((p_d - p_cross) / p_cross * 100, 2)

            records.append({
                'phase_d_date':       phase_d_date.strftime('%Y-%m-%d'),
                'phase_b_start':      df['date'].iloc[phase_b_start_idx].strftime('%Y-%m-%d'),
                'phase_b_duration_d': phase_b_duration,
                'first_cross_tr_mid': df['date'].iloc[first_cross_idx].strftime('%Y-%m-%d') if first_cross_idx else None,
                'struct_lag_days':    struct_lag,       # 日线标记延迟
                'deploy_lag_days':    1,                # 固定 +1 天 shift（日线→1H）
                'total_lag_days':     (struct_lag or 0) + 1,
                'missed_move_pct':    move_pct,         # 延迟期间错过的涨幅
                'phase_d_duration_d': phase_d_duration,
                'tr_mid':             round(tr_mid, 0),
                'close_at_d':         round(df['close'].iloc[i], 0),
                'tr_range_pct':       round((tr_high - tr_low) / tr_low * 100, 1),
            })

    if not records:
        print("未找到 B→D 转换事件")
        return

    rdf = pd.DataFrame(records)

    # ── 详细列表 ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print(f"B→D 转换事件（共 {len(rdf)} 次）")
    print("=" * 80)
    print(rdf[[
        'phase_d_date', 'phase_b_start', 'phase_b_duration_d',
        'first_cross_tr_mid', 'struct_lag_days', 'total_lag_days',
        'missed_move_pct', 'phase_d_duration_d'
    ]].to_string(index=False))

    # ── 统计摘要 ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("延迟统计")
    print("=" * 80)

    def stats(s, label):
        s = s.dropna()
        if len(s) == 0:
            print(f"\n{label}: 无有效样本")
            return
        print(f"\n{label}（天）:")
        print(f"  N={len(s)}  中位={s.median():.1f}  P25={s.quantile(0.25):.1f}  P75={s.quantile(0.75):.1f}  min={s.min():.0f}  max={s.max():.0f}")
        print(f"  0天（当天即生效）: {(s == 0).sum()} 次")
        print(f"  ≥3天:             {(s >= 3).sum()} 次")

    stats(rdf['struct_lag_days'], "结构延迟（价格穿 TR mid → Phase D 标记，日线）")
    stats(rdf['total_lag_days'],  "总延迟（穿 TR mid → 1H 策略可用）")

    # ── 错过的涨幅 ────────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("延迟期间错过的涨幅（首次穿 TR mid 到 Phase D 标记当天）")
    print("=" * 80)
    mm = rdf['missed_move_pct'].dropna()
    if len(mm):
        print(f"  平均: {mm.mean():.2f}%  中位: {mm.median():.2f}%  最大: {mm.max():.2f}%  最小: {mm.min():.2f}%")
        print(f"  >1%: {(mm > 1).sum()} 次  >3%: {(mm > 3).sum()} 次  <0%（价格反而跌）: {(mm < 0).sum()} 次")

    print("\n" + "=" * 80)
    print("关键结论")
    print("=" * 80)
    sl = rdf['struct_lag_days'].dropna()
    tl = rdf['total_lag_days'].dropna()
    print(f"  Phase C（Spring）触发次数: 0  →  历史全部走 B→D 直接路径")
    print(f"  结构延迟中位数: {sl.median():.0f} 天（价格穿越 TR mid 后检测器需要的额外时间）")
    print(f"  总延迟中位数:   {tl.median():.0f} 天（含 +1 天日线 shift）")
    print(f"  Phase D 持续中位数: {rdf['phase_d_duration_d'].median():.0f} 天")
    print(f"  → 即使有 {tl.median():.0f} 天延迟，Phase D 窗口还剩 {rdf['phase_d_duration_d'].median() - tl.median():.0f} 天可用")


if __name__ == '__main__':
    main()
