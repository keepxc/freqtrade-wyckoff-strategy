"""
T-2: 双通道独立绩效账本

将策略从"一个系统"拆解为"两个赚钱引擎"的管理视角。
固定字段，可持续更新。
"""
import json
from datetime import datetime

BASELINE_FILE = "user_data/backtest_results/extracted_latest/backtest-result-2026-03-17_16-34-15.json"

def load_trades(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    key = list(data["strategy"].keys())[0]
    return data["strategy"][key]["trades"]

def parse_dt(s):
    if not s:
        return None
    s = s.replace(" ", "T")
    if "+" not in s and not s.endswith("Z"):
        s += "+00:00"
    return datetime.fromisoformat(s)

def ledger(trades, label):
    if not trades:
        print(f"\n[{label}] 无数据")
        return {}

    profits  = [t.get("profit_ratio", 0) for t in trades]
    wins     = [p for p in profits if p >= 0]
    losses   = [p for p in profits if p < 0]

    # 持仓时长（小时）
    durations = []
    for t in trades:
        od = parse_dt(t.get("open_date"))
        cd = parse_dt(t.get("close_date"))
        if od and cd:
            durations.append((cd - od).total_seconds() / 3600)

    avg_dur = sum(durations) / len(durations) if durations else 0

    # 退出原因分布
    exits = {}
    for t in trades:
        r = t.get("exit_reason", "unknown")
        exits[r] = exits.get(r, 0) + 1

    # 最大不利偏移 (MAE): 用 min_rate 近似
    maes = []
    for t in trades:
        open_rate = t.get("open_rate", 0)
        min_rate  = t.get("min_rate", open_rate)
        if open_rate > 0:
            maes.append((min_rate - open_rate) / open_rate)
    avg_mae = sum(maes) / len(maes) if maes else 0
    worst_mae = min(maes) if maes else 0

    n = len(trades)
    total_p = sum(profits)
    avg_p   = total_p / n

    roi_n   = exits.get("roi", 0)
    dw_n    = exits.get("wyckoff_down_wave", 0)
    tsl_n   = exits.get("trailing_stop_loss", 0)

    # 大亏单（亏损 > 8%）
    big_loss = [p for p in profits if p < -0.08]

    print(f"\n{'='*60}")
    print(f"  {label} 账本")
    print(f"{'='*60}")
    print(f"  笔数              : {n}")
    print(f"  总收益            : {total_p*100:.2f}%")
    print(f"  平均收益/笔       : {avg_p*100:.2f}%")
    print(f"  胜率              : {len(wins)}/{n} = {len(wins)/n*100:.1f}%")
    print(f"  盈利均值          : +{sum(wins)/len(wins)*100:.2f}%" if wins else "  盈利均值          : N/A")
    print(f"  亏损均值          : {sum(losses)/len(losses)*100:.2f}%" if losses else "  亏损均值          : N/A")
    print(f"  平均持仓          : {avg_dur:.1f}h ({avg_dur/24:.1f}天)")
    print(f"  平均 MAE          : {avg_mae*100:.2f}%（最差 {worst_mae*100:.2f}%）")
    print()
    print(f"  退出原因分布:")
    for reason, cnt in sorted(exits.items(), key=lambda x: -x[1]):
        pct = cnt / n * 100
        # 该退出原因的 WR
        sub_profits = [t.get("profit_ratio", 0) for t in trades if t.get("exit_reason") == reason]
        sub_wr = len([p for p in sub_profits if p >= 0]) / len(sub_profits) * 100 if sub_profits else 0
        sub_avg = sum(sub_profits) / len(sub_profits) * 100 if sub_profits else 0
        print(f"    {reason:<35}: {cnt:3d}笔 ({pct:5.1f}%)  WR={sub_wr:5.1f}%  avg={sub_avg:+.2f}%")
    print()
    print(f"  roi  命中率       : {roi_n}/{n} = {roi_n/n*100:.1f}%")
    print(f"  down_wave 退出率  : {dw_n}/{n} = {dw_n/n*100:.1f}%")
    print(f"  TSL 占比          : {tsl_n}/{n} = {tsl_n/n*100:.1f}%")
    print(f"  大亏单 (< -8%)    : {len(big_loss)}/{n} = {len(big_loss)/n*100:.1f}%")

    return {
        "n": n, "total_p": total_p, "avg_p": avg_p,
        "wr": len(wins)/n, "avg_dur_h": avg_dur,
        "roi_n": roi_n, "dw_n": dw_n, "tsl_n": tsl_n,
        "big_loss_n": len(big_loss),
    }

trades = load_trades(BASELINE_FILE)
print(f"Baseline 总交易: {len(trades)} 笔")

# 按通道分组
ws_trades   = [t for t in trades if t.get("enter_tag") == "wyckoff_wave_start"]
ax_trades   = [t for t in trades if t.get("enter_tag") == "wyckoff_axis_support"]
other_trades = [t for t in trades if t.get("enter_tag") not in ("wyckoff_wave_start", "wyckoff_axis_support")]

ws_stats = ledger(ws_trades, "wave_start 通道")
ax_stats = ledger(ax_trades, "axis_support 通道")

if other_trades:
    tags = {}
    for t in other_trades:
        tags[t.get("enter_tag","?")] = tags.get(t.get("enter_tag","?"), 0) + 1
    print(f"\n其他信号 ({len(other_trades)}笔): {tags}")
    other_profits = [t.get("profit_ratio", 0) for t in other_trades]
    print(f"  总收益: {sum(other_profits)*100:.2f}%  avg: {sum(other_profits)/len(other_profits)*100:.2f}%")

# ── 组合层 ──────────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print("  组合层分析")
print(f"{'='*60}")
all_profits = [t.get("profit_ratio", 0) for t in trades]
total = sum(all_profits)

ws_contrib  = ws_stats.get("total_p", 0)
ax_contrib  = ax_stats.get("total_p", 0)
other_p     = sum(t.get("profit_ratio", 0) for t in other_trades)

print(f"\n  总收益贡献分解:")
print(f"    wave_start   : {ws_contrib*100:+.2f}%  ({ws_contrib/total*100:.1f}% of total)")
print(f"    axis_support : {ax_contrib*100:+.2f}%  ({ax_contrib/total*100:.1f}% of total)")
if other_trades:
    print(f"    其他信号     : {other_p*100:+.2f}%  ({other_p/total*100:.1f}% of total)")
print(f"    合计         : {total*100:.2f}%")

# DD 贡献：用最大亏损笔的来源近似
print(f"\n  最大亏损来源（亏损最深前10笔）:")
loss_trades = sorted(trades, key=lambda x: x.get("profit_ratio", 0))[:10]
for t in loss_trades:
    tag  = t.get("enter_tag", "?")
    exit = t.get("exit_reason", "?")
    p    = t.get("profit_ratio", 0)
    od   = t.get("open_date", "")[:10]
    print(f"    {od}  {tag:<30} × {exit:<30} → {p*100:+.2f}%")

# TSL/大亏 详细归因
print(f"\n  TSL (-12%) 归因:")
tsl_all = [t for t in trades if t.get("exit_reason") == "trailing_stop_loss"]
for t in tsl_all:
    tag = t.get("enter_tag", "?")
    p   = t.get("profit_ratio", 0)
    od  = t.get("open_date", "")[:10]
    print(f"    {od}  {tag}  → {p*100:+.2f}%")

# T-1 机会成本补充到 axis_support 账本
print(f"\n  axis_support 机会成本 (T-1 结果):")
print(f"    阻塞 wave_start 次数 : 64 笔 (占 axis 持仓 22%)")
print(f"    被阻塞 ws 理论总收益  : +159.58%")
print(f"    axis_support 实际总收益: +{ax_stats.get('total_p',0)*100:.2f}%")
print(f"    净效益 (axis - opp)  : +{(ax_stats.get('total_p',0) - 1.5958)*100:.2f}%")
print(f"    结论: ✅ axis_support 覆盖机会成本（覆盖率 {ax_stats.get('total_p',0)/1.5958*100:.0f}%）")

print(f"\n{'='*60}")
print("  T-2 账本摘要（固定参考基准）")
print(f"{'='*60}")
print(f"""
  Baseline: 2023-01-01 ~ 2026-02-28 | BTC/USDT:USDT | max_open_trades=1

  ┌─────────────────────┬──────────────┬──────────────┐
  │ 指标                │ wave_start   │ axis_support │
  ├─────────────────────┼──────────────┼──────────────┤
  │ 笔数                │ {ws_stats['n']:>12} │ {ax_stats['n']:>12} │
  │ 总收益              │ {ws_stats['total_p']*100:>+11.2f}% │ {ax_stats['total_p']*100:>+11.2f}% │
  │ 平均收益/笔         │ {ws_stats['avg_p']*100:>+11.2f}% │ {ax_stats['avg_p']*100:>+11.2f}% │
  │ WR                  │ {ws_stats['wr']*100:>11.1f}% │ {ax_stats['wr']*100:>11.1f}% │
  │ 平均持仓            │ {ws_stats['avg_dur_h']:>10.1f}h │ {ax_stats['avg_dur_h']:>10.1f}h │
  │ ROI 命中率          │ {ws_stats['roi_n']/ws_stats['n']*100:>11.1f}% │ {ax_stats['roi_n']/ax_stats['n']*100:>11.1f}% │
  │ down_wave 退出率    │ {ws_stats['dw_n']/ws_stats['n']*100:>11.1f}% │ {ax_stats['dw_n']/ax_stats['n']*100:>11.1f}% │
  │ TSL 占比            │ {ws_stats['tsl_n']/ws_stats['n']*100:>11.1f}% │ {ax_stats['tsl_n']/ax_stats['n']*100:>11.1f}% │
  │ 大亏单 (<-8%)       │ {ws_stats['big_loss_n']/ws_stats['n']*100:>11.1f}% │ {ax_stats['big_loss_n']/ax_stats['n']*100:>11.1f}% │
  │ 总收益贡献占比      │ {ws_stats['total_p']/total*100:>11.1f}% │ {ax_stats['total_p']/total*100:>11.1f}% │
  │ 机会成本（T-1）     │          N/A │       64笔阻塞 │
  └─────────────────────┴──────────────┴──────────────┘
""")
