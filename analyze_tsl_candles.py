"""
逐根4h K线复盘：5笔大亏TSL单
精确计算从最大浮盈到触发止损用了几根K线（几小时）
"""
import zipfile, json
import pandas as pd
import numpy as np

# ── 加载4h K线数据 ────────────────────────────────────────────────
DATA_FILE = "user_data/data/binance/futures/BTC_USDT_USDT-4h-futures.feather"
df = pd.read_feather(DATA_FILE)
df['date'] = pd.to_datetime(df['date'], utc=True)
df = df.set_index('date').sort_index()
print(f"加载4h数据: {len(df)} 根K线  {df.index[0]} ~ {df.index[-1]}")

# ── 加载回测结果 ──────────────────────────────────────────────────
ZP = 'user_data/backtest_results/backtest-result-2026-03-23_14-38-40.zip'
with zipfile.ZipFile(ZP) as z:
    jname = [n for n in z.namelist() if n.endswith('.json') and 'meta' not in n][0]
    with z.open(jname) as fh:
        data = json.load(fh)
strat  = list(data['strategy'].values())[0]
trades = strat['trades']
tsl    = [t for t in trades if t['exit_reason'] == 'trailing_stop_loss']
big5   = sorted([t for t in tsl if t['profit_ratio']*100 < -8], key=lambda x: x['profit_ratio'])

print(f"\n{'='*70}")
print(f"5笔大亏TSL单：逐根4h K线路径复盘")
print(f"{'='*70}")

for i, t in enumerate(big5):
    open_rate   = t['open_rate']
    close_rate  = t['close_rate']
    stop_abs    = t['stop_loss_abs']     # 实际止损价
    profit_pct  = t['profit_ratio'] * 100
    open_dt     = pd.to_datetime(t['open_date'], utc=True)
    close_dt    = pd.to_datetime(t['close_date'], utc=True)
    pair        = t['pair']
    enter_tag   = t.get('enter_tag','?')

    print(f"\n{'─'*65}")
    print(f"#{i+1}  {pair}  [{enter_tag}]")
    print(f"  开仓: {open_dt.strftime('%Y-%m-%d %H:%M')}  @{open_rate:,.2f}")
    print(f"  平仓: {close_dt.strftime('%Y-%m-%d %H:%M')}  @{close_rate:,.2f}  利润={profit_pct:+.2f}%")
    print(f"  止损价: {stop_abs:,.2f}")

    # 取持仓期间所有K线
    candles = df.loc[open_dt:close_dt].copy()
    if len(candles) == 0:
        print("  [无K线数据，跳过]")
        continue

    # 逐根计算浮盈（以开仓价为基准）
    candles['float_pct_high'] = (candles['high'] - open_rate) / open_rate * 100
    candles['float_pct_low']  = (candles['low']  - open_rate) / open_rate * 100
    candles['float_pct_close']= (candles['close']- open_rate) / open_rate * 100

    # 找到最大浮盈K线（high最高的那根）
    peak_idx  = candles['high'].idxmax()
    peak_high = candles.loc[peak_idx, 'high']
    peak_float = (peak_high - open_rate) / open_rate * 100
    peak_candle_num = candles.index.get_loc(peak_idx) + 1  # 第几根K线

    # 找止损触发K线（第一根 low <= stop_abs 的K线）
    trigger_candles = candles[candles['low'] <= stop_abs]
    if len(trigger_candles) > 0:
        trigger_idx = trigger_candles.index[0]
        trigger_candle_num = candles.index.get_loc(trigger_idx) + 1
        candles_from_peak_to_trigger = trigger_candle_num - peak_candle_num
        hours_from_peak_to_trigger   = candles_from_peak_to_trigger * 4
    else:
        trigger_idx = close_dt
        trigger_candle_num = len(candles)
        candles_from_peak_to_trigger = len(candles) - peak_candle_num
        hours_from_peak_to_trigger   = candles_from_peak_to_trigger * 4

    print(f"\n  ── K线路径（共{len(candles)}根4h K线）──")
    # 打印每根K线的关键信息（精简）
    print(f"  {'#':>3} {'时间':>16} {'High':>10} {'Low':>10} {'浮盈High':>9} {'浮盈Low':>9}  备注")
    for j, (dt, row) in enumerate(candles.iterrows()):
        remark = ""
        if dt == peak_idx:
            remark = "◀ 最大浮盈"
        if dt == trigger_idx:
            remark += " ◀ 止损触发"
        if j == 0:
            remark += " (开仓K线)"
        print(f"  {j+1:>3} {dt.strftime('%m-%d %H:%M'):>16}  {row['high']:>10,.2f}  {row['low']:>10,.2f}"
              f"  {row['float_pct_high']:>+8.2f}%  {row['float_pct_low']:>+8.2f}%  {remark}")

    print(f"\n  【关键结论】")
    print(f"  最大浮盈时刻: {peak_idx.strftime('%Y-%m-%d %H:%M')}  高点={peak_high:,.2f}  浮盈={peak_float:+.2f}%")
    print(f"  最大浮盈出现在第 {peak_candle_num} 根K线 (开仓后 {(peak_candle_num-1)*4}h)")
    print(f"  止损触发K线: 第 {trigger_candle_num} 根 ({trigger_idx.strftime('%Y-%m-%d %H:%M') if hasattr(trigger_idx,'strftime') else trigger_idx})")
    print(f"  ⏱  从最大浮盈 → 止损触发: {candles_from_peak_to_trigger} 根4h K线 = {hours_from_peak_to_trigger}小时")
    if peak_float < 2.0:
        print(f"  🔴 从未实质浮盈(峰值仅{peak_float:.2f}%)：这是入场即下跌，不是急速崩跌")
        print(f"     → 无法用「急速崩跌检测」拦截，止损宽度问题")
    elif candles_from_peak_to_trigger <= 3:
        print(f"  ⚡ 极速崩跌！从浮盈{peak_float:.1f}%到触发止损仅{candles_from_peak_to_trigger}根K线({hours_from_peak_to_trigger}h)")
        print(f"     → 「急速下跌检测」可以介入（例如：1根4h K线跌幅>X%则提前出场）")
    elif candles_from_peak_to_trigger <= 8:
        print(f"  ⚠️  较快崩跌：{candles_from_peak_to_trigger}根K线({hours_from_peak_to_trigger}h)从浮盈到亏损")
        print(f"     → 考虑「多根K线累计跌幅」动态止损")
    else:
        print(f"  📉 慢速下跌：{candles_from_peak_to_trigger}根K线({hours_from_peak_to_trigger}h)，属于趋势反转，不是急速崩跌")

print(f"\n{'='*70}")
print("【汇总结论】")
print("="*70)
