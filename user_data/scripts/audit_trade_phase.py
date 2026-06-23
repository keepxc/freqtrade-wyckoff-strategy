"""
审计 V8 回测交易的入场 Phase 分布。
核心问题：策略的 E→D 入场占多少？B→D 的短窗口造成多少错失？

运行：python user_data/scripts/audit_trade_phase.py
"""

import sys, os, zipfile, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'strategies'))

import pandas as pd
import numpy as np
from v8_modules.sc_detector import add_v8_sc_detection
from v8_modules.phase_detector import add_v8_phase_detection

BACKTEST_ZIP  = os.path.join(
    os.path.dirname(__file__), '..', 'backtest_results',
    'backtest-result-2026-06-20_13-24-12.zip'
)
FEATHER_PATH = os.path.join(
    os.path.dirname(__file__), '..', 'data', 'binance', 'futures',
    'BTC_USDT_USDT-1d-futures.feather'
)


def load_trades() -> pd.DataFrame:
    with zipfile.ZipFile(BACKTEST_ZIP) as z:
        names = [n for n in z.namelist() if n.endswith('.json') and 'config' not in n]
        with z.open(names[0]) as f:
            raw = f.read()
        # freqtrade 用 ndjson（每行一个 JSON 对象）
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            lines = [l for l in raw.decode().splitlines() if l.strip()]
            d = json.loads(lines[0])
    strat = list(d['strategy'].keys())[0]
    trades = d['strategy'][strat]['trades']
    df = pd.DataFrame(trades)
    df['open_date'] = pd.to_datetime(df['open_date'], utc=True)
    df['close_date'] = pd.to_datetime(df['close_date'], utc=True)
    return df


def load_phase_series() -> pd.DataFrame:
    df = pd.read_feather(FEATHER_PATH)
    df['date'] = pd.to_datetime(df['date'], utc=True)
    df = add_v8_sc_detection(df)
    df = add_v8_phase_detection(df)
    return df[['date', 'v8_phase', 'v8_tr_high', 'v8_tr_low']].copy()


def get_phase_at(phase_df: pd.DataFrame, dt: pd.Timestamp) -> str:
    """取 dt 时刻对应的日线 phase（+1 day shift 已体现在 merge_asof 里）"""
    # 策略用 +1 day shift，所以用 dt 前一天的 phase
    sub = phase_df[phase_df['date'] <= dt]
    if sub.empty:
        return 'unknown'
    return sub.iloc[-1]['v8_phase']


def prev_phase_transition(phase_df: pd.DataFrame, idx: int) -> str:
    """找 idx 这天之前最近一次 phase 切换的来源 phase"""
    cur = phase_df['v8_phase'].iloc[idx]
    for j in range(idx - 1, max(idx - 60, -1), -1):
        if phase_df['v8_phase'].iloc[j] != cur:
            return phase_df['v8_phase'].iloc[j]
    return 'unknown'


def main():
    print("加载交易数据...")
    trades = load_trades()
    print(f"V8 交易数: {len(trades)}")

    print("计算 Phase 序列...")
    phase_df = load_phase_series()

    # ── 为每笔交易打上入场时的 Phase 标签 ──────────────────────────────────
    phase_at_entry = []
    prev_phase_at_entry = []

    for _, row in trades.iterrows():
        dt = row['open_date']
        # 找最近的日线 phase（策略用前一天收盘的 phase）
        sub = phase_df[phase_df['date'] <= dt]
        if sub.empty:
            phase_at_entry.append('unknown')
            prev_phase_at_entry.append('unknown')
            continue
        idx = sub.index[-1]
        phase_at_entry.append(phase_df['v8_phase'].iloc[idx])
        prev_phase_at_entry.append(prev_phase_transition(phase_df, idx))

    trades['phase_at_entry'] = phase_at_entry
    trades['prev_phase'] = prev_phase_at_entry
    trades['phase_transition'] = trades['prev_phase'] + '→' + trades['phase_at_entry']
    trades['profit_pct'] = trades['profit_ratio'] * 100
    trades['win'] = trades['profit_ratio'] > 0

    # ── 输出 ──────────────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("入场 Phase 分布")
    print("=" * 70)
    phase_stats = trades.groupby('phase_at_entry').agg(
        count=('profit_ratio', 'count'),
        win_rate=('win', 'mean'),
        avg_profit_pct=('profit_pct', 'mean'),
        total_profit_pct=('profit_pct', 'sum'),
    ).sort_values('count', ascending=False)
    phase_stats['win_rate'] = (phase_stats['win_rate'] * 100).round(1).astype(str) + '%'
    phase_stats['avg_profit_pct'] = phase_stats['avg_profit_pct'].round(3)
    phase_stats['total_profit_pct'] = phase_stats['total_profit_pct'].round(2)
    print(phase_stats.to_string())

    print("\n" + "=" * 70)
    print("Phase D 入场来源（前一个 Phase 是什么）")
    print("=" * 70)
    d_entries = trades[trades['phase_at_entry'] == 'D']
    if len(d_entries):
        trans_stats = d_entries.groupby('phase_transition').agg(
            count=('profit_ratio', 'count'),
            win_rate=('win', 'mean'),
            avg_profit_pct=('profit_pct', 'mean'),
        ).sort_values('count', ascending=False)
        trans_stats['win_rate'] = (trans_stats['win_rate'] * 100).round(1).astype(str) + '%'
        trans_stats['avg_profit_pct'] = trans_stats['avg_profit_pct'].round(3)
        print(trans_stats.to_string())
    else:
        print("无 Phase D 入场记录")

    print("\n" + "=" * 70)
    print("enter_tag × Phase 交叉分析")
    print("=" * 70)
    cross = trades.groupby(['enter_tag', 'phase_at_entry']).agg(
        count=('profit_ratio', 'count'),
        win_rate=('win', 'mean'),
        avg_profit_pct=('profit_pct', 'mean'),
    ).sort_values('count', ascending=False).head(20)
    cross['win_rate'] = (cross['win_rate'] * 100).round(1).astype(str) + '%'
    cross['avg_profit_pct'] = cross['avg_profit_pct'].round(3)
    print(cross.to_string())

    print("\n" + "=" * 70)
    print("关键结论")
    print("=" * 70)
    d_pct = len(d_entries) / len(trades) * 100
    e_entries = trades[trades['phase_at_entry'] == 'E']
    b_entries = trades[trades['phase_at_entry'] == 'B']
    ed_count = len(trades[trades['phase_transition'] == 'E→D'])
    bd_count = len(trades[trades['phase_transition'] == 'B→D'])
    print(f"  Phase D 入场: {len(d_entries)} 笔 ({d_pct:.1f}%)")
    print(f"  Phase E 入场: {len(e_entries)} 笔 ({len(e_entries)/len(trades)*100:.1f}%)")
    print(f"  Phase B 入场: {len(b_entries)} 笔 ({len(b_entries)/len(trades)*100:.1f}%)")
    print(f"  Phase D 中来自 E→D: {ed_count} 笔")
    print(f"  Phase D 中来自 B→D: {bd_count} 笔")
    if len(d_entries):
        print(f"  → E→D 占 Phase D 入场的 {ed_count/len(d_entries)*100:.1f}%")


if __name__ == '__main__':
    main()
