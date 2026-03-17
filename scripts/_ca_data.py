#!/usr/bin/env python3
"""Core Audit 数据提取脚本"""
import json

path = "user_data/backtest_results/temp_extract_ca/backtest-result-2026-03-16_03-08-50.json"
with open(path, encoding='utf-8') as f:
    d = json.load(f)

s = d['strategy']['WyckoffV7_TrueMethod']
total = s.get('total_trades', 1)

print("=== 总体绩效 ===")
print("总交易: %d  多头: %d  空头: %d" % (total, s.get('trade_count_long',0), s.get('trade_count_short',0)))
print("总收益: %.2f%%  均收益: %.2f%%  最大回撤: %.2f%%" % (
    s.get('profit_total',0)*100,
    s.get('profit_mean',0)*100,
    s.get('max_drawdown_account',0)*100
))
winrate_raw = s.get('winrate', s.get('wins', 0)/total)
print("胜率: %.1f%%  平均持仓: %s" % (winrate_raw*100, s.get('holding_avg','')))

print()
print("=== CA-1: 入场标签统计 ===")
tags = s.get('results_per_enter_tag', [])
tags_s = sorted(tags, key=lambda x: x.get('profit_total',0), reverse=True)
header = "%-36s %5s %6s %7s %9s %8s" % ("标签", "笔数", "占比%", "胜率%", "均收益%", "总收益%")
print(header)
print("-"*75)
for t in tags_s:
    k = t.get('key', '?')[:36]
    n = t.get('trades', 0)
    pct = n / total * 100
    wr = t.get('winrate', 0) * 100
    pm = t.get('profit_mean', 0) * 100
    pt = t.get('profit_total_pct', 0)
    print("%-36s %5d %6.1f %7.1f %9.2f %8.2f" % (k, n, pct, wr, pm, pt))

print()
print("=== CA-2: 退出原因统计 ===")
exits = s.get('exit_reason_summary', [])
exits_s = sorted(exits, key=lambda x: x.get('profit_total_abs',0), reverse=True)
header2 = "%-35s %5s %6s %7s %9s %12s" % ("退出原因", "笔数", "占比%", "胜率%", "均收益%", "绝对收益")
print(header2)
print("-"*76)
for e in exits_s:
    k = e.get('key', '?')[:35]
    n = e.get('trades', 0)
    pct = n / total * 100
    wr = e.get('winrate', 0) * 100
    pm = e.get('profit_mean', 0) * 100
    ab = e.get('profit_total_abs', 0)
    print("%-35s %5d %6.1f %7.1f %9.2f %12.2f" % (k, n, pct, wr, pm, ab))

print()
print("=== 亏损退出路径（均收益<0）===")
losing = [e for e in exits if e.get('profit_mean',0) < 0]
losing_s = sorted(losing, key=lambda x: x.get('profit_total_abs',0))
for e in losing_s:
    k = e.get('key','?')
    n = e.get('trades',0)
    pm = e.get('profit_mean',0)*100
    ab = e.get('profit_total_abs',0)
    print("  [%s] %d笔, 均收益%.2f%%, 绝对亏损%.2f" % (k, n, pm, ab))
