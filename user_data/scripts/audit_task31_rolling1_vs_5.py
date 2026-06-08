"""
TASK 31 法医审计：rolling=1 vs rolling=5 新增 35 笔空头分析
用法：
  python user_data/scripts/audit_task31_rolling1_vs_5.py <result_rolling1.json> <result_rolling5.json>
或直接传入两个 backtest_results ZIP 解压后的 .json 文件路径
"""
import sys
import json
import os
from datetime import datetime

def load_trades(path):
    """从 backtest 结果 JSON 中提取所有 trades"""
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    strategy_data = data.get('strategy', {})
    # 支持多种格式
    for key in strategy_data:
        trades = strategy_data[key].get('trades', [])
        if trades:
            return trades
    # 直接是 trades 列表
    if isinstance(data, list):
        return data
    return data.get('trades', [])


def parse_trade(t):
    """提取关键字段"""
    return {
        'pair': t.get('pair', ''),
        'open_date': t.get('open_date', ''),
        'close_date': t.get('close_date', ''),
        'profit_ratio': t.get('profit_ratio', 0),
        'profit_abs': t.get('profit_abs', 0),
        'trade_duration': t.get('trade_duration', 0),
        'enter_tag': t.get('enter_tag', ''),
        'exit_reason': t.get('exit_reason', ''),
        'is_short': t.get('is_short', False),
        'open_rate': t.get('open_rate', 0),
        'close_rate': t.get('close_rate', 0),
    }


def trade_key(t):
    return (t['pair'], t['open_date'], t['is_short'])


def analyze(r1_path, r5_path):
    t1_raw = load_trades(r1_path)
    t5_raw = load_trades(r5_path)

    t1 = [parse_trade(t) for t in t1_raw]
    t5 = [parse_trade(t) for t in t5_raw]

    keys1 = {trade_key(t): t for t in t1}
    keys5 = {trade_key(t): t for t in t5}

    # 新增（rolling=1 有，rolling=5 没有）
    new_in_1 = [t for k, t in keys1.items() if k not in keys5]
    # 消失（rolling=5 有，rolling=1 没有）
    lost_in_1 = [t for k, t in keys5.items() if k not in keys1]

    print("=" * 70)
    print("rolling=1 vs rolling=5 差异分析")
    print("=" * 70)
    print(f"rolling=1 总交易: {len(t1)}  空头: {sum(1 for t in t1 if t['is_short'])}")
    print(f"rolling=5 总交易: {len(t5)}  空头: {sum(1 for t in t5 if t['is_short'])}")
    print(f"新增交易: {len(new_in_1)}  消失交易: {len(lost_in_1)}")
    print()

    # 新增空头分析
    new_shorts = [t for t in new_in_1 if t['is_short']]
    new_longs  = [t for t in new_in_1 if not t['is_short']]
    print(f"新增交易中：空头 {len(new_shorts)} 笔，多头 {len(new_longs)} 笔")
    print()

    if new_shorts:
        print("-" * 70)
        print(f"新增空头明细（{len(new_shorts)} 笔）")
        print("-" * 70)

        wins = [t for t in new_shorts if t['profit_ratio'] > 0]
        losses = [t for t in new_shorts if t['profit_ratio'] <= 0]
        wr = len(wins) / len(new_shorts) * 100
        avg_profit = sum(t['profit_ratio'] for t in new_shorts) / len(new_shorts) * 100
        avg_duration = sum(t['trade_duration'] for t in new_shorts) / len(new_shorts)

        print(f"  胜率: {wr:.1f}%  ({len(wins)}W/{len(losses)}L)")
        print(f"  平均收益: {avg_profit:.2f}%")
        print(f"  平均持仓: {avg_duration:.0f} 分钟 ({avg_duration/60:.1f}h)")
        print()

        # 按年份分布
        from collections import Counter
        year_dist = Counter()
        for t in new_shorts:
            try:
                yr = t['open_date'][:4]
                year_dist[yr] += 1
            except:
                pass
        print("  年份分布（是否集中在牛市区间？）:")
        for yr in sorted(year_dist):
            print(f"    {yr}: {year_dist[yr]} 笔")
        print()

        # 按入场标签分布
        tag_dist = Counter(t['enter_tag'] for t in new_shorts)
        print("  入场标签分布:")
        for tag, cnt in tag_dist.most_common():
            print(f"    {tag}: {cnt} 笔")
        print()

        # 详细列表
        new_shorts_sorted = sorted(new_shorts, key=lambda x: x['open_date'])
        print(f"  {'日期':<20} {'收益%':>8} {'持仓h':>7} {'标签':<30} {'退出'}")
        print(f"  {'-'*20} {'-'*8} {'-'*7} {'-'*30} {'-'*20}")
        for t in new_shorts_sorted:
            dur_h = t['trade_duration'] / 60
            pct = t['profit_ratio'] * 100
            win_mark = "✓" if pct > 0 else "✗"
            print(f"  {t['open_date']:<20} {pct:>+8.2f}% {dur_h:>6.1f}h {t['enter_tag']:<30} {t['exit_reason']} {win_mark}")

    if losses := [t for t in new_shorts if t['profit_ratio'] <= 0]:
        print()
        print("-" * 70)
        print(f"亏损笔详情（{len(losses)} 笔）")
        print("-" * 70)
        for t in sorted(losses, key=lambda x: x['profit_ratio']):
            print(f"  {t['open_date']}  收益 {t['profit_ratio']*100:+.2f}%  "
                  f"持仓 {t['trade_duration']/60:.1f}h  标签: {t['enter_tag']}  退出: {t['exit_reason']}")

    # 牛市假跌破检测（2023年末-2024年为牛市）
    bull_period = {'2023', '2024', '2025'}
    bull_shorts = [t for t in new_shorts if t['open_date'][:4] in bull_period]
    if bull_shorts:
        print()
        print("-" * 70)
        print(f"牛市区间（2023-2025）新增空头：{len(bull_shorts)} 笔")
        print("-" * 70)
        bull_wins = [t for t in bull_shorts if t['profit_ratio'] > 0]
        bull_wr = len(bull_wins) / len(bull_shorts) * 100 if bull_shorts else 0
        print(f"  牛市空头胜率: {bull_wr:.1f}%")
        for t in sorted(bull_shorts, key=lambda x: x['open_date']):
            pct = t['profit_ratio'] * 100
            mk = "✓" if pct > 0 else "✗"
            print(f"  {t['open_date']}  {pct:>+7.2f}%  {t['enter_tag']}  {mk}")

    print()
    print("=" * 70)
    print("结论判断标准：")
    print("  - 新增空头胜率 ≥ 85%：信号质量合格")
    print("  - 牛市区间亏损 ≤ 2 笔：假突破风险可控")
    print("  - 无单笔 < -5% 深度亏损：风险边界合理")
    print("=" * 70)


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print("用法: python audit_task31_rolling1_vs_5.py <rolling1.json> <rolling5.json>")
        print()
        print("提示: 需要先从 backtest_results/*.zip 中解压对应的 .json 文件")
        sys.exit(1)
    analyze(sys.argv[1], sys.argv[2])
