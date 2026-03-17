"""
A-1b 下降波段退出质量分析
=========================
目标：剖析 axis_support × wyckoff_down_wave 的 244 笔退出。
核心问题：有多少是"真下跌"（退出后价格继续跌），有多少是"误退"（退出后价格继续涨）？

分析维度：
  1. 退出后 5/10/20 根 1H K 线的价格变化（missed_return）
  2. 利润区间分布（低利润 vs 中/高利润退出）
  3. 持仓时长分布
  4. 误退比例 + 平均"错过收益"
  5. axis_support × roi 退出对比（25 笔均 +7.92%）

用法：
    python scripts/_a1b_downwave_postmove_analysis.py
"""

import zipfile, json, sys
from pathlib import Path
import pandas as pd
import numpy as np

# ── 配置 ──────────────────────────────────────────────────────────────────────
BASE     = Path(__file__).resolve().parent.parent
ZIP_PATH = BASE / "user_data/backtest_results/backtest-result-2026-03-17_08-04-46.zip"
OHLCV    = BASE / "user_data/data/binance/futures/BTC_USDT_USDT-1h-futures.feather"
LOOKAHEAD_BARS = [5, 10, 20]   # 退出后观察 N 根 1H K 线
# ─────────────────────────────────────────────────────────────────────────────

def load_trades(zip_path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(zip_path) as zf:
        json_files = [n for n in zf.namelist() if n.endswith('.json') and 'config' not in n]
        with zf.open(json_files[0]) as f:
            data = json.load(f)
    strat = list(data['strategy'].keys())[0]
    trades = pd.DataFrame(data['strategy'][strat]['trades'])
    trades['open_date']  = pd.to_datetime(trades['open_date'],  utc=True)
    trades['close_date'] = pd.to_datetime(trades['close_date'], utc=True)
    return trades


def load_ohlcv(ohlcv_path: Path) -> pd.DataFrame:
    df = pd.read_feather(ohlcv_path)
    df = df.set_index('date').sort_index()
    return df


def get_post_exit_return(close_date: pd.Timestamp, close_rate: float,
                          ohlcv: pd.DataFrame, n_bars: int) -> float | None:
    """退出后第 n 根 K 线的收盘价相对退出价的变化率（正 = 涨，负 = 跌）"""
    # 找 close_date 之后第一根完整 K 线的索引
    try:
        future = ohlcv.loc[close_date:]
        if len(future) <= n_bars:
            return None
        future_close = future.iloc[n_bars]['close']
        return (future_close - close_rate) / close_rate
    except Exception:
        return None


def main():
    print(f"\n{'='*65}")
    print("  A-1b：axis_support × down_wave 退出质量深度分析")
    print(f"{'='*65}\n")

    trades = load_trades(ZIP_PATH)
    ohlcv  = load_ohlcv(OHLCV)

    # 按 enter_tag × exit_reason 分桶
    ax_dw  = trades[(trades['enter_tag'] == 'wyckoff_axis_support') &
                    (trades['exit_reason'] == 'wyckoff_down_wave')].copy()
    ax_roi = trades[(trades['enter_tag'] == 'wyckoff_axis_support') &
                    (trades['exit_reason'] == 'roi')].copy()
    ax_ts  = trades[(trades['enter_tag'] == 'wyckoff_axis_support') &
                    (trades['exit_reason'] == 'trailing_stop_loss')].copy()

    total_ax = len(trades[trades['enter_tag'] == 'wyckoff_axis_support'])
    print(f"axis_support 总交易：{total_ax} 笔")
    print(f"  via down_wave  ：{len(ax_dw):>4} 笔  均利润 {ax_dw['profit_ratio'].mean()*100:+.2f}%")
    print(f"  via roi        ：{len(ax_roi):>4} 笔  均利润 {ax_roi['profit_ratio'].mean()*100:+.2f}%")
    print(f"  via trailing   ：{len(ax_ts):>4} 笔  均利润 {ax_ts['profit_ratio'].mean()*100:+.2f}%")
    print()

    # ── 1. 持仓时长分布 ───────────────────────────────────────────────────────
    ax_dw['duration_h'] = (ax_dw['close_date'] - ax_dw['open_date']).dt.total_seconds() / 3600
    print("【1】持仓时长分布（axis_support × down_wave）")
    bins_h = [0, 6, 12, 24, 48, 72, 120, 9999]
    labels = ['≤6h','6-12h','12-24h','1-2d','2-3d','3-5d','>5d']
    ax_dw['dur_bucket'] = pd.cut(ax_dw['duration_h'], bins=bins_h, labels=labels)
    dur_dist = ax_dw['dur_bucket'].value_counts().sort_index()
    for label, cnt in dur_dist.items():
        bar = '█' * int(cnt / max(cnt, 1) * 30)
        print(f"  {label:>7}: {cnt:>4} 笔  {bar}")
    print()

    # ── 2. 利润区间分布 ───────────────────────────────────────────────────────
    print("【2】利润区间分布（axis_support × down_wave）")
    bins_p = [-999, -0.05, -0.02, 0, 0.01, 0.02, 0.03, 0.05, 999]
    labels_p = ['<-5%','-5%~-2%','-2%~0%','0%~1%','1%~2%','2%~3%','3%~5%','>5%']
    ax_dw['profit_bucket'] = pd.cut(ax_dw['profit_ratio'] * 100,
                                    bins=[-999,-5,-2,0,1,2,3,5,999],
                                    labels=labels_p)
    p_dist = ax_dw['profit_bucket'].value_counts().sort_index()
    for label, cnt in p_dist.items():
        avg_p = ax_dw[ax_dw['profit_bucket'] == label]['profit_ratio'].mean() * 100
        print(f"  {label:>10}: {cnt:>4} 笔  均利润 {avg_p:+.2f}%")
    print(f"\n  负利润退出：{(ax_dw['profit_ratio'] < 0).sum()} 笔 / {len(ax_dw)} 笔 = {(ax_dw['profit_ratio'] < 0).mean()*100:.1f}%")
    print(f"  微利退出(0~1%)：{((ax_dw['profit_ratio'] >= 0) & (ax_dw['profit_ratio'] < 0.01)).sum()} 笔")
    print()

    # ── 3. 退出后价格走势（核心！）────────────────────────────────────────────
    print("【3】退出后价格走势（误退 = 退出后价格上涨 = 错杀）")
    print("  计算中...", end='', flush=True)
    for n in LOOKAHEAD_BARS:
        ax_dw[f'post{n}h'] = ax_dw.apply(
            lambda r: get_post_exit_return(r['close_date'], r['close_rate'], ohlcv, n),
            axis=1
        )
    print(" 完成\n")

    for n in LOOKAHEAD_BARS:
        col = f'post{n}h'
        valid = ax_dw[ax_dw[col].notna()]
        false_exit = valid[valid[col] > 0.005]   # 退出后 +0.5% 以上 = 误退
        true_exit  = valid[valid[col] < -0.005]  # 退出后 -0.5% 以下 = 正确退
        neutral    = valid[(valid[col].abs()) <= 0.005]

        print(f"  退出后 {n:>2} 根 K 线 (共 {len(valid)} 笔有效):")
        print(f"    ✅ 正确退出(继续跌 >-0.5%)：{len(true_exit):>4} 笔 ({len(true_exit)/len(valid)*100:.1f}%)"
              f"  平均跌幅 {true_exit[col].mean()*100:+.2f}%")
        print(f"    ❌ 误退    (继续涨 >+0.5%)：{len(false_exit):>4} 笔 ({len(false_exit)/len(valid)*100:.1f}%)"
              f"  平均错过 {false_exit[col].mean()*100:+.2f}%")
        print(f"    ⚖️  中性    (±0.5% 以内)  ：{len(neutral):>4} 笔 ({len(neutral)/len(valid)*100:.1f}%)")

        # 误退中，当时利润在哪？
        if len(false_exit) > 0:
            fe_profit_pct = false_exit['profit_ratio'].mean() * 100
            fe_dur_h      = false_exit['duration_h'].mean()
            print(f"    误退的平均退出利润：{fe_profit_pct:+.2f}%  平均持仓时长：{fe_dur_h:.1f}h")
        print()

    # ── 4. 误退细分：低利润误退 vs 高利润误退 ────────────────────────────────
    print("【4】误退细分（退出后 10h 为基准）")
    col10 = 'post10h'
    valid10 = ax_dw[ax_dw[col10].notna()]
    false10 = valid10[valid10[col10] > 0.005]

    if len(false10) > 0:
        # 按退出利润分组
        print(f"  共 {len(false10)} 笔误退（退出后 10h 涨 >+0.5%）")
        false10 = false10.copy()
        false10['profit_pct'] = false10['profit_ratio'] * 100
        false10['missed_pct'] = false10[col10] * 100

        # 分桶：退出利润 <0%、0-1%、1-2%、2-3%、>3%
        for lo, hi, name in [(-999,0,'负利润退出'), (0,1,'微利退出 0-1%'),
                              (1,2,'小利退出 1-2%'), (2,3,'中利退出 2-3%'), (3,999,'较好退出 >3%')]:
            sub = false10[(false10['profit_pct'] >= lo) & (false10['profit_pct'] < hi)]
            if len(sub) > 0:
                print(f"  [{name}] {len(sub)} 笔：退出均利 {sub['profit_pct'].mean():+.2f}%，"
                      f"后续错过 {sub['missed_pct'].mean():+.2f}%，"
                      f"持仓均 {sub['duration_h'].mean():.0f}h")
    print()

    # ── 5. axis_support × roi 对照 ────────────────────────────────────────────
    print("【5】axis_support × roi 对照（相对 down_wave 的差异）")
    if len(ax_roi) > 0:
        ax_roi_c = ax_roi.copy()
        ax_roi_c['duration_h'] = (ax_roi_c['close_date'] - ax_roi_c['open_date']).dt.total_seconds() / 3600
        print(f"  roi 退出笔数：{len(ax_roi_c)}")
        print(f"  均利润：{ax_roi_c['profit_ratio'].mean()*100:+.2f}%"
              f"  中位利润：{ax_roi_c['profit_ratio'].median()*100:+.2f}%")
        print(f"  均持仓：{ax_roi_c['duration_h'].mean():.1f}h  中位持仓：{ax_roi_c['duration_h'].median():.1f}h")
        print(f"  down_wave vs roi 均持仓差：{ax_dw['duration_h'].mean() - ax_roi_c['duration_h'].mean():+.1f}h")
    print()

    # ── 6. 高价值潜力分析：错过多少利润？ ────────────────────────────────────
    print("【6】潜在收益估算（如果 down_wave 误退被延迟）")
    col20 = 'post20h'
    valid20 = ax_dw[ax_dw[col20].notna()]
    false20 = valid20[valid20[col20] > 0.005]
    print(f"  退出后 20h 内仍在上涨的误退笔数：{len(false20)} / {len(valid20)}")
    if len(false20) > 0:
        avg_missed = false20[col20].mean() * 100
        total_missed_approx = avg_missed * len(false20)
        print(f"  平均每笔错过：{avg_missed:+.2f}%")
        print(f"  如全部延迟退出，粗估总收益增量：≈{total_missed_approx:+.1f}% (复利效应未计)")

    # ── 7. 关键结论总结 ───────────────────────────────────────────────────────
    print()
    print(f"{'='*65}")
    print("  关键结论")
    print(f"{'='*65}")

    col10_false_rate = len(false10) / len(valid10) * 100 if len(valid10) > 0 else 0
    print(f"""
  1. axis_support × down_wave：{len(ax_dw)} 笔，均利润 {ax_dw['profit_ratio'].mean()*100:+.2f}%
  2. 其中退出后 10h 继续上涨（误退）：{len(false10)} 笔（{col10_false_rate:.1f}%）
  3. 误退平均错过收益：{false10['post10h'].mean()*100:+.2f}% per trade（退出后10h）
  4. axis_support × roi 对照：{len(ax_roi)} 笔，均利润 {ax_roi['profit_ratio'].mean()*100:+.2f}%
  5. 持仓时长差：roi 退出比 down_wave 平均多持仓 {ax_roi_c['duration_h'].mean() - ax_dw['duration_h'].mean():+.0f}h

  → 误退率（10h基准）：{col10_false_rate:.0f}%
  → 这意味着大约 {len(false10)} 笔应该被视为"正常 pullback 误退"
  → 下一步修正方向：基于【退出后价格方向 + 利润分桶 + 持仓时长】三维对比，
     找出可用于区分"真下跌"与"误退 pullback"的特征组合。
""")


if __name__ == '__main__':
    main()
