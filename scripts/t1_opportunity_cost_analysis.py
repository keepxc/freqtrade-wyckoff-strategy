"""
T-1: 通道机会成本分析

核心问题：axis_support 持仓期间，阻塞了多少 wave_start 信号？
         这些被阻塞的 wave_start 的收益如何？
         axis_support 实际收益是否覆盖这些机会成本？

方法：
  1. Baseline JSON → axis_support 持仓时间窗 [open_date, close_date]
  2. C-1 JSON     → wave_start 全量交易（含备 baseline 阻塞的）
  3. 交叉分析：C-1 中哪些 wave_start 落在 axis_support 持仓窗内
              baseline 中哪些 wave_start 实际成功进入
              差集 = 被阻塞的 wave_start
"""
import json
from datetime import datetime, timezone

BASELINE_FILE = "user_data/backtest_results/extracted_latest/backtest-result-2026-03-17_16-34-15.json"
C1_FILE       = "user_data/backtest_results/c1_extracted/backtest-result-2026-03-17_15-03-43.json"

def load_trades(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    key = list(data["strategy"].keys())[0]
    return data["strategy"][key]["trades"]

def parse_dt(s):
    if s is None:
        return None
    # Freqtrade 格式: "2023-01-15 06:00:00+00:00" 或 "2023-01-15T06:00:00"
    s = s.replace(" ", "T")
    if "+" not in s and not s.endswith("Z"):
        s += "+00:00"
    return datetime.fromisoformat(s)

baseline = load_trades(BASELINE_FILE)
c1       = load_trades(C1_FILE)

print(f"Baseline 总交易: {len(baseline)} 笔")
print(f"C-1 (wave_start-only) 总交易: {len(c1)} 笔")

# ── 1. 提取 axis_support 持仓窗口 ──────────────────────────────────────
axis_windows = []
for t in baseline:
    if t.get("enter_tag") == "wyckoff_axis_support":
        od = parse_dt(t.get("open_date"))
        cd = parse_dt(t.get("close_date"))
        if od and cd:
            axis_windows.append({
                "open":   od,
                "close":  cd,
                "profit": t.get("profit_ratio", 0),
                "exit":   t.get("exit_reason", ""),
                "dur_h":  (cd - od).total_seconds() / 3600,
            })

print(f"\naxis_support 持仓窗口数: {len(axis_windows)}")
print(f"  平均持仓时长: {sum(w['dur_h'] for w in axis_windows)/len(axis_windows):.1f}h")

# ── 2. Baseline 中实际执行的 wave_start 交易 ────────────────────────────
baseline_ws = {
    parse_dt(t["open_date"]): t
    for t in baseline
    if t.get("enter_tag") == "wyckoff_wave_start"
}
print(f"\nBaseline 中 wave_start 实际执行: {len(baseline_ws)} 笔")

# ── 3. C-1 中每笔 wave_start 是否落在某个 axis_support 窗口内 ─────────
def overlaps_any_axis(ws_open, ws_close, windows):
    """检查 wave_start [ws_open, ws_close] 是否与任一 axis_support 窗口重叠"""
    matched = []
    for w in windows:
        # 重叠条件：两区间有交集
        if ws_open < w["close"] and ws_close > w["open"]:
            matched.append(w)
    return matched

blocked_ws   = []  # C-1 中被 axis_support 阻塞的 wave_start
unblocked_ws = []  # C-1 中未被阻塞的（应该与 baseline 执行的一致）

for t in c1:
    ws_open  = parse_dt(t.get("open_date"))
    ws_close = parse_dt(t.get("close_date"))
    if ws_open is None or ws_close is None:
        continue
    matched = overlaps_any_axis(ws_open, ws_close, axis_windows)
    if matched:
        blocked_ws.append({
            "ws_open":   ws_open,
            "ws_close":  ws_close,
            "profit":    t.get("profit_ratio", 0),
            "exit":      t.get("exit_reason", ""),
            "dur_h":     (ws_close - ws_open).total_seconds() / 3600,
            "blocking_axis": matched,  # 哪几个 axis_support 在占仓
        })
    else:
        unblocked_ws.append(t)

print(f"\nC-1 中 wave_start 分布:")
print(f"  被 axis_support 时间窗阻塞: {len(blocked_ws)} 笔")
print(f"  未被阻塞 (应与 baseline 吻合): {len(unblocked_ws)} 笔")
print(f"  (Baseline 实际执行: {len(baseline_ws)} 笔，差值可能因 max_open_trades 竞争)")

# ── 4. 被阻塞的 wave_start 质量分析 ───────────────────────────────────
if blocked_ws:
    profits  = [w["profit"] for w in blocked_ws]
    wins     = [p for p in profits if p >= 0]
    losses   = [p for p in profits if p < 0]
    avg_p    = sum(profits) / len(profits)
    total_p  = sum(profits)

    print(f"\n{'='*60}")
    print(f"被阻塞 wave_start 质量分析 ({len(blocked_ws)} 笔)")
    print(f"{'='*60}")
    print(f"  WR         : {len(wins)}/{len(blocked_ws)} = {len(wins)/len(blocked_ws)*100:.1f}%")
    print(f"  平均收益   : {avg_p*100:.2f}%")
    print(f"  总收益合计 : {total_p*100:.2f}%")
    if wins:
        print(f"  盈利平均   : +{sum(wins)/len(wins)*100:.2f}%")
    if losses:
        print(f"  亏损平均   : {sum(losses)/len(losses)*100:.2f}%")

    # Exit reason 分布
    exits = {}
    for w in blocked_ws:
        exits[w["exit"]] = exits.get(w["exit"], 0) + 1
    print(f"  退出原因   : {exits}")

    # 平均持仓时长
    avg_dur = sum(w["dur_h"] for w in blocked_ws) / len(blocked_ws)
    print(f"  平均持仓   : {avg_dur:.1f}h ({avg_dur/24:.1f}天)")

# ── 5. 对比：阻塞它的 axis_support 本身收益 ───────────────────────────
print(f"\n{'='*60}")
print("机会成本账本（逐笔对照）")
print(f"{'='*60}")

# 对每个"被阻塞的 wave_start"，找对应的 axis_support
conflict_pairs = []
for bws in blocked_ws:
    for ax in bws["blocking_axis"]:
        conflict_pairs.append({
            "axis_profit":  ax["profit"],
            "axis_exit":    ax["exit"],
            "axis_dur_h":   ax["dur_h"],
            "ws_profit":    bws["profit"],
            "ws_exit":      bws["exit"],
            "ws_dur_h":     bws["dur_h"],
            "net_diff":     bws["profit"] - ax["profit"],  # ws 比 axis 多赚/少赚
        })

if conflict_pairs:
    net_diffs = [p["net_diff"] for p in conflict_pairs]
    ws_profits = [p["ws_profit"] for p in conflict_pairs]
    ax_profits = [p["axis_profit"] for p in conflict_pairs]

    print(f"  冲突对数 (axis × blocked_ws): {len(conflict_pairs)}")
    print(f"  被阻塞 wave_start 平均收益   : {sum(ws_profits)/len(ws_profits)*100:.2f}%")
    print(f"  占仓 axis_support 平均收益   : {sum(ax_profits)/len(ax_profits)*100:.2f}%")
    print(f"  净机会成本 (ws - axis) 平均  : {sum(net_diffs)/len(net_diffs)*100:.2f}%")
    print(f"  净机会成本总量               : {sum(net_diffs)*100:.2f}%")

    pos_opp = [d for d in net_diffs if d > 0]  # wave_start 比 axis 更好
    neg_opp = [d for d in net_diffs if d < 0]  # axis 比 wave_start 更好
    print(f"  wave_start 占优 (ws>axis)    : {len(pos_opp)} 对，avg 差值 +{sum(pos_opp)/len(pos_opp)*100:.2f}%")
    print(f"  axis 占优 (axis>ws)          : {len(neg_opp)} 对，avg 差值 {sum(neg_opp)/len(neg_opp)*100:.2f}%")

# ── 6. 三个核心输出数字 ────────────────────────────────────────────────
print(f"\n{'='*60}")
print("T-1 核心结论")
print(f"{'='*60}")

total_axis = len(axis_windows)
freq = len(blocked_ws) / total_axis if total_axis else 0
print(f"\n[1] axis_support 持仓期间阻塞 wave_start 的频率:")
print(f"    每笔 axis_support 平均阻塞 {len(blocked_ws)}/{total_axis} = {freq:.2f} 个 wave_start")
print(f"    ({len(blocked_ws)} 个被阻塞 / {total_axis} 个 axis_support 持仓窗口)")

if blocked_ws:
    total_ws_profit = sum(w["profit"] for w in blocked_ws)
    avg_ws_profit   = total_ws_profit / len(blocked_ws)
    print(f"\n[2] 被阻塞 wave_start 的收益:")
    print(f"    理论总收益: {total_ws_profit*100:.2f}%")
    print(f"    平均每笔  : {avg_ws_profit*100:.2f}%")
    print(f"    WR        : {len([w for w in blocked_ws if w['profit']>=0])}/{len(blocked_ws)} = {len([w for w in blocked_ws if w['profit']>=0])/len(blocked_ws)*100:.1f}%")

total_axis_profit = sum(w["profit"] for w in axis_windows)
print(f"\n[3] axis_support 实际总收益 vs 机会成本:")
print(f"    axis_support 实际总收益: {total_axis_profit*100:.2f}%")
if blocked_ws:
    total_ws_profit = sum(w["profit"] for w in blocked_ws)
    coverage = total_axis_profit / total_ws_profit if total_ws_profit != 0 else float('inf')
    net = total_axis_profit - total_ws_profit
    print(f"    被阻塞 wave_start 理论总收益: {total_ws_profit*100:.2f}%")
    print(f"    净差值 (axis - ws_opportunity): {net*100:.2f}%")
    if net > 0:
        print(f"    ✅ axis_support 覆盖了机会成本，净多赚 {net*100:.2f}%")
    else:
        print(f"    ❌ axis_support 未覆盖机会成本，净少赚 {abs(net)*100:.2f}%")
    print(f"    覆盖率: {coverage*100:.1f}%")
