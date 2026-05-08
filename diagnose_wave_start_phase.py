#!/usr/bin/env python3
"""
wave_start × 日线 Phase 机会空间诊断
======================================
目标：评估"用 Phase D 上下文提升 wave_start 频率"的可行性与增量空间

不修改策略代码，纯数据统计：
  1. 标记历史每日的 V8 Phase（C/D/E/A/B/unknown）
  2. 从基线回测中提取 wave_start 入场，统计各 Phase 的分布
  3. 统计各 Phase 中 wave_start 的胜率和均利润
  4. 估算 Phase D 期间有多少被过滤掉的潜在 wave_start 信号

输出：如果 Phase D 期间的 wave_start 胜率/利润不低于全局均值，
      且有显著数量的信号被 Phase D 过滤阻挡，则方案可行。
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'user_data', 'strategies'))

import json
import zipfile
import pandas as pd
import numpy as np
from pathlib import Path

# ── 导入 V8 模块 ──────────────────────────────────────────────
from v8_modules.sc_detector import add_v8_sc_detection
from v8_modules.phase_detector import add_v8_phase_detection

RESULTS_DIR = Path('user_data/backtest_results')
OHLCV_1D = 'user_data/data/binance/futures/BTC_USDT_USDT-1d-futures.feather'
OHLCV_1H = 'user_data/data/binance/futures/BTC_USDT_USDT-1h-futures.feather'
BASELINE_ZIP = 'backtest-result-2026-05-07_04-13-31.zip'  # 修复前基线


def load_1d_with_phases():
    """加载日线数据并运行 V8 Phase 检测"""
    print("📊 加载日线数据...")
    df_1d = pd.read_feather(OHLCV_1D)
    if 'date' in df_1d.columns:
        df_1d['date'] = pd.to_datetime(df_1d['date'], utc=True)
        df_1d = df_1d.set_index('date').sort_index()

    print(f"   日线: {len(df_1d)} 根  {df_1d.index[0].date()} ~ {df_1d.index[-1].date()}")

    # 运行 SC 检测
    print("🔬 运行 SC 检测...")
    df_1d = add_v8_sc_detection(df_1d)

    # 运行 Phase 检测
    print("🔬 运行 Phase 检测...")
    df_1d = add_v8_phase_detection(df_1d)

    # 统计各 Phase 天数
    print("\n📊 日线 Phase 分布:")
    for ph in ['A', 'B', 'C', 'D', 'E', 'unknown']:
        mask = df_1d['v8_phase'] == ph
        print(f"   Phase {ph}: {mask.sum()} 天 ({mask.sum()/len(df_1d)*100:.1f}%)")

    return df_1d


def merge_phases_to_1h(df_1d):
    """将日线 Phase 合并到 1h K线"""
    print("\n📊 加载 1h 数据...")
    df_1h = pd.read_feather(OHLCV_1H)
    if 'date' in df_1h.columns:
        df_1h['date'] = pd.to_datetime(df_1h['date'], utc=True)
        df_1h = df_1h.set_index('date').sort_index()

    # 为每根 1h K线分配当天的 Phase
    df_1h['date_day'] = df_1h.index.floor('D')
    phase_map = df_1d[['v8_phase', 'v8_phase_allow_long']].copy()
    phase_map['date_day'] = phase_map.index.floor('D')

    df_1h = df_1h.merge(
        phase_map[['date_day', 'v8_phase', 'v8_phase_allow_long']],
        on='date_day', how='left'
    )
    df_1h['v8_phase'] = df_1h['v8_phase'].fillna('unknown')
    df_1h['v8_phase_allow_long'] = df_1h['v8_phase_allow_long'].fillna(False)

    print(f"   1h: {len(df_1h)} 根  {df_1h.index[0]} ~ {df_1h.index[-1]}")
    return df_1h


def load_baseline_trades():
    """加载基线回测交易"""
    zip_path = RESULTS_DIR / BASELINE_ZIP
    if not zip_path.exists():
        # fallback to latest
        last_result = RESULTS_DIR / '.last_result.json'
        if last_result.exists():
            with open(last_result) as f:
                zip_path = RESULTS_DIR / json.load(f).get('latest_backtest', '')
        if not zip_path.exists():
            raise FileNotFoundError(f"未找到回测: {BASELINE_ZIP}")

    print(f"\n📂 加载回测: {zip_path.name}")
    with zipfile.ZipFile(zip_path, 'r') as zf:
        json_files = [n for n in zf.namelist()
                      if n.endswith('.json') and 'meta' not in n and 'config' not in n]
        if not json_files:
            raise FileNotFoundError("ZIP 中无 JSON")
        with zf.open(json_files[0]) as f:
            data = json.load(f)

    strategy_name = list(data['strategy'].keys())[0]
    trades = pd.DataFrame(data['strategy'][strategy_name]['trades'])
    print(f"   总交易: {len(trades)} 笔")
    return trades


def match_trades_to_phase(trades, df_1h):
    """将每笔交易的入场日期匹配到当天的 Phase"""
    trades['open_dt'] = pd.to_datetime(trades['open_date'], utc=True)

    # 对每笔交易，找入场当天的 phase
    phases = []
    for _, t in trades.iterrows():
        dt = t['open_dt']
        # 找当天或最近的 phase
        day = dt.floor('D')
        match = df_1h[df_1h['date_day'] == day]
        if len(match) > 0:
            phases.append(match['v8_phase'].iloc[0])
        else:
            phases.append('unknown')

    trades['entry_phase'] = phases
    return trades


def main():
    print("=" * 80)
    print("🔬 wave_start × Phase 机会空间诊断")
    print("=" * 80)

    # Step 1: 日线 Phase 标记
    df_1d = load_1d_with_phases()

    # Step 2: 合并到 1h
    df_1h = merge_phases_to_1h(df_1d)

    # Step 3: 加载回测交易
    trades = load_baseline_trades()

    # Step 4: 匹配 phase
    trades = match_trades_to_phase(trades, df_1h)

    # ── 分析 1: wave_start 交易的 Phase 分布 ──────────────────
    ws = trades[trades['enter_tag'] == 'wyckoff_wave_start'].copy()
    ws['profit_pct'] = ws['profit_ratio'] * 100

    print("\n" + "=" * 80)
    print("📊 分析 1: wave_start 入场 × Phase 分布")
    print("=" * 80)

    print(f"\nwave_start 总交易: {len(ws)} 笔")
    print(f"  胜率: {(ws['profit_ratio'] > 0).sum()/len(ws)*100:.1f}%")
    print(f"  均利润: {ws['profit_pct'].mean():.2f}%")
    print(f"  中位数利润: {ws['profit_pct'].median():.2f}%")
    print(f"  总净利润: {ws['profit_abs'].sum():,.0f} USDT")

    print(f"\n按 Phase 分布:")
    for ph in ['A', 'B', 'C', 'D', 'E', 'unknown']:
        subset = ws[ws['entry_phase'] == ph]
        if len(subset) == 0:
            continue
        wins = (subset['profit_ratio'] > 0).sum()
        print(f"  Phase {ph}: {len(subset):3d} 笔 | "
              f"胜率={wins/len(subset)*100:.0f}% | "
              f"均利润={subset['profit_pct'].mean():+.2f}% | "
              f"中位数={subset['profit_pct'].median():+.2f}% | "
              f"净利={subset['profit_abs'].sum():,.0f}")

    # ── 分析 2: Phase D 天数及年化 ────────────────────────────
    print("\n" + "=" * 80)
    print("📊 分析 2: Phase D 密度与年化估算")
    print("=" * 80)

    total_days = len(df_1d)
    phase_d_days = (df_1d['v8_phase'] == 'D').sum()
    phase_c_days = (df_1d['v8_phase'] == 'C').sum()
    phase_e_days = (df_1d['v8_phase'] == 'E').sum()

    # 回测覆盖的年数
    years = total_days / 365.25

    ws_in_d = ws[ws['entry_phase'] == 'D']
    ws_in_c = ws[ws['entry_phase'] == 'C']
    ws_in_e = ws[ws['entry_phase'] == 'E']

    print(f"\n回测覆盖: {total_days} 天 ({years:.1f} 年)")
    print(f"Phase D 天数: {phase_d_days} ({phase_d_days/total_days*100:.1f}%)")
    print(f"Phase C 天数: {phase_c_days} ({phase_c_days/total_days*100:.1f}%)")
    print(f"Phase E 天数: {phase_e_days} ({phase_e_days/total_days*100:.1f}%)")

    print(f"\n年化 wave_start 交易:")
    print(f"  总年化: {len(ws)/years:.1f} 笔/年")
    print(f"  Phase D 年化: {len(ws_in_d)/years:.1f} 笔/年")
    print(f"  Phase C 年化: {len(ws_in_c)/years:.1f} 笔/年")
    print(f"  Phase E 年化: {len(ws_in_e)/years:.1f} 笔/年")

    # 信号密度 = 交易数 / Phase 天数
    print(f"\n信号密度 (笔/100天):")
    for ph, days, subset in [
        ('C', phase_c_days, ws_in_c),
        ('D', phase_d_days, ws_in_d),
        ('E', phase_e_days, ws_in_e),
    ]:
        if days > 0:
            density = len(subset) / days * 100
            print(f"  Phase {ph}: {density:.1f} 笔/100天 ({len(subset)}笔 / {days}天)")

    # ── 分析 3: 被 V8 Phase 过滤掉的信号估算 ──────────────────
    print("\n" + "=" * 80)
    print("📊 分析 3: Phase 过滤影响估算")
    print("=" * 80)

    # V8 Phase 过滤器: 只允许 C/D/E 入场 (v8_phase_allow_long)
    # 已在 Phase A/B/unknown 的 wave_start 的就是被挡掉的
    ws_blocked = ws[~ws['entry_phase'].isin(['C', 'D', 'E'])]

    print(f"\n被 V8 Phase 过滤阻挡的 wave_start:")
    print(f"  总数: {len(ws_blocked)} 笔")
    if len(ws_blocked) > 0:
        wins = (ws_blocked['profit_ratio'] > 0).sum()
        print(f"  胜率: {wins/len(ws_blocked)*100:.0f}%")
        print(f"  均利润: {ws_blocked['profit_pct'].mean():+.2f}%")
        print(f"  净利: {ws_blocked['profit_abs'].sum():,.0f} USDT")
        print(f"\n  ⚠️ 这些是被 Phase 过滤挡掉的信号。如果 Phase D 放松后能释放")
        print(f"     一部分，年化可增加约 {len(ws_blocked)/years:.0f} 笔 wave_start。")

    # ── 分析 4: 非 C/D/E 的 wave_start 质量 ──────────────────
    print("\n" + "=" * 80)
    print("📊 分析 4: 被过滤信号的 Phase 细分")
    print("=" * 80)

    for ph in ['A', 'B', 'unknown']:
        subset = ws_blocked[ws_blocked['entry_phase'] == ph]
        if len(subset) == 0:
            continue
        wins = (subset['profit_ratio'] > 0).sum()
        print(f"  Phase {ph}: {len(subset):3d} 笔 | "
              f"胜率={wins/len(subset)*100:.0f}% | "
              f"均利润={subset['profit_pct'].mean():+.2f}% | "
              f"中位数={subset['profit_pct'].median():+.2f}%")

    # ── 最终结论 ──────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("📋 诊断结论")
    print("=" * 80)

    # Phase D 中的 wave_start 质量
    ws_d_winrate = (ws_in_d['profit_ratio'] > 0).sum() / max(len(ws_in_d), 1) * 100
    ws_d_avg = ws_in_d['profit_pct'].mean() if len(ws_in_d) > 0 else 0

    # 全局 wave_start 质量
    ws_global_winrate = (ws['profit_ratio'] > 0).sum() / len(ws) * 100

    print(f"\nPhase D 中的 wave_start 质量:")
    print(f"  胜率: {ws_d_winrate:.0f}% vs 全局{ws_global_winrate:.0f}%")
    print(f"  均利润: {ws_d_avg:+.2f}% vs 全局{ws['profit_pct'].mean():+.2f}%")

    print(f"\n被 Phase 过滤阻挡的优质信号: {len(ws_blocked[ws_blocked['profit_ratio'] > 0])} 笔")

    if len(ws_blocked) > 0 and ws_blocked['profit_ratio'].mean() > 0:
        print(f"\n✅ 结论: 被过滤的信号质量为正 (均利润{ws_blocked['profit_pct'].mean():+.2f}%)")
        print(f"   → Phase D 放松可能释放约 {len(ws_blocked)/years:.0f} 笔/年的 wave_start")
        print(f"   → 方案可行，建议下一步做具体方案设计")
    else:
        print(f"\n⚠️ 结论: 被过滤的信号质量为负或不足")
        print(f"   → Phase D 放松风险较高，需谨慎")

    print("\n" + "=" * 80)


if __name__ == '__main__':
    main()
