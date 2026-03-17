"""
A-1a: axis_support × trailing_stop_loss 复盘
==============================================
逐笔还原 5 笔大亏单：开仓价、最高浮盈、出场价、持仓过程中的 bear_market_mode 情况。

用法：
    python scripts/_a1a_trailing_analysis.py
"""
import json
import pathlib
import statistics

# 使用最新回测结果（Fix Pack A 后，已解压到 temp_extract_fixpack）
NEW_JSON_DIR = pathlib.Path("temp_extract_fixpack")
BASE_JSON    = pathlib.Path("temp_extract_ca/backtest-result-2026-03-16_03-08-50.json")

def find_latest_json(directory: pathlib.Path):
    files = [f for f in directory.glob("backtest-result-*.json")
             if "meta" not in f.name and "config" not in f.name]
    if not files:
        return None
    return max(files, key=lambda f: f.stat().st_mtime)

def load_trades(path: pathlib.Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    sk = next(iter(data["strategy"]))
    return data["strategy"][sk]["trades"]

def analyze_trade(t: dict, idx: int):
    entry_tag   = t.get("enter_tag", "?")
    exit_reason = t.get("exit_reason", "?")
    profit_pct  = t.get("profit_ratio", 0) * 100
    profit_abs  = t.get("profit_abs", 0)
    open_rate   = t.get("open_rate", 0)
    close_rate  = t.get("close_rate", 0)
    max_rate    = t.get("max_rate", open_rate)
    min_rate    = t.get("min_rate", open_rate)
    duration    = t.get("trade_duration", 0)
    open_date   = t.get("open_date", "?")
    close_date  = t.get("close_date", "?")

    # 最大浮盈（从 open_rate 计算）
    max_float_pct  = (max_rate - open_rate) / open_rate * 100
    # 出场价回落（从最高点到出场点）
    drop_from_max  = (close_rate - max_rate) / max_rate * 100

    print(f"\n  ── 亏单 #{idx+1} ──────────────────────────────────────────")
    print(f"  入场:  {open_date}  @{open_rate:,.2f}  tag={entry_tag}")
    print(f"  出场:  {close_date}  @{close_rate:,.2f}  reason={exit_reason}")
    print(f"  利润:  {profit_pct:+.2f}%  ({profit_abs:+,.0f} USDT)")
    print(f"  持仓:  {duration // 60}h {duration % 60}min")
    print(f"  最高价: {max_rate:,.2f}  最大浮盈: {max_float_pct:+.2f}%")
    print(f"  最低价: {min_rate:,.2f}")
    print(f"  从最高点到出场跌幅: {drop_from_max:+.2f}%")

    if max_float_pct > 2.0:
        print(f"  ⚠️  曾有 >2% 浮盈 → bear_mode 下止损可能已收紧至 -6%")
    else:
        print(f"  ℹ️  最大浮盈 <2%，止损全程为 -12% from entry（从未收紧）")

    if max_float_pct > 5.0:
        print(f"  ❗ 曾有大幅浮盈({max_float_pct:.1f}%)后完全回吐 → 应早于 trailing 触发出场")

    return {
        "max_float": max_float_pct,
        "drop_from_max": drop_from_max,
        "duration_h": duration // 60,
        "never_above_2pct": max_float_pct < 2.0,
    }


def main():
    new_json = find_latest_json(NEW_JSON_DIR)
    if new_json is None:
        print("ERROR: 未找到新回测 JSON")
        return
    print(f"分析文件: {new_json.name}")

    trades = load_trades(new_json)
    trailing = [t for t in trades if t.get("exit_reason") == "trailing_stop_loss"]

    print(f"\n{'='*60}")
    print(f"  trailing_stop_loss 共 {len(trailing)} 笔（全部来自 axis_support）")
    print(f"{'='*60}")

    results = []
    for i, t in enumerate(trailing):
        r = analyze_trade(t, i)
        results.append(r)

    # 汇总
    print(f"\n{'='*60}")
    print("  汇总分析")
    print(f"{'='*60}")
    never_above_2 = sum(1 for r in results if r["never_above_2pct"])
    print(f"  从未浮盈 >2%（止损全程 -12%）: {never_above_2}/{len(results)}")
    had_profit = [r for r in results if not r["never_above_2pct"]]
    if had_profit:
        print(f"  曾有 >2% 浮盈后回撤: {len(had_profit)}/{len(results)}")
        for r in had_profit:
            print(f"    - 最大浮盈 {r['max_float']:.1f}%, 从高点下跌 {r['drop_from_max']:.1f}%")
    max_floats = [r["max_float"] for r in results]
    print(f"\n  最大浮盈分布: min={min(max_floats):.1f}%  max={max(max_floats):.1f}%  均={statistics.mean(max_floats):.1f}%")

    print(f"\n{'='*60}")
    print("  根因分类与修正建议")
    print(f"{'='*60}")

    # 根据最大浮盈分类
    never_above_2_n = sum(1 for r in results if r["max_float"] < 2.0)
    between_2_5_n   = sum(1 for r in results if 2.0 <= r["max_float"] < 5.0)
    above_5_n       = sum(1 for r in results if r["max_float"] >= 5.0)

    print(f"  A类（最大浮盈<2%，止损原生 -12%）: {never_above_2_n}笔")
    print(f"    → 入场即下行，属于 axis_support 时机/质量问题")
    print(f"    → 可考虑：更早止损出场（缩短 stoploss），或加严入场闸门")
    print(f"  B类（2%≤最大浮盈<5%，曾盈利后回撤）: {between_2_5_n}笔")
    print(f"    → 浮盈有限，bear_mode 下止损可能改善")
    print(f"    → 可考虑：更早 break-even（+0.5%→保本）或收紧 trailing")
    print(f"  C类（最大浮盈≥5%，大浮盈后完全回吐）: {above_5_n}笔")
    print(f"    → 最可惜情形：理应保住利润")
    print(f"    → 可考虑：浮盈 >3% 后切换到更紧 trailing，或 break-even 保护")

    if never_above_2_n >= 3:
        print(f"\n  → 主要是 A 类（{never_above_2_n}笔）：建议方向 B（更早 break-even）")
        print(f"    实施：达到 +0% → +0.5% 时 custom_stoploss 提至 break-even（-0.5% 入场源）")
    if above_5_n >= 2:
        print(f"\n  → 主要是 C 类（{above_5_n}笔）：建议方向 C（浮盈锁定）")
        print(f"    实施：达到 +3% 后 trailing 从 -12% 收紧至 -4%（保住大部分利润）")


if __name__ == "__main__":
    main()
