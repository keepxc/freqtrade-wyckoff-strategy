"""
分析5笔大亏TSL单：从浮盈到崩溃的时间与幅度
"""
import zipfile, json

# 用最新回测结果
ZIPFILES = [
    ('user_data/backtest_results/backtest-result-2026-03-23_14-38-40.zip', '03-23 最新'),
    ('user_data/backtest_results/backtest-result-2026-03-23_14-19-33.zip', '03-23 SOW-fix'),
    ('user_data/backtest_results/backtest-result-2026-03-17_16-34-15.zip', '03-17 965%'),
]

for zp, label in ZIPFILES:
    try:
        with zipfile.ZipFile(zp, 'r') as z:
            names = z.namelist()
            json_name = [n for n in names if n.endswith('.json') and 'meta' not in n][0]
            with z.open(json_name) as fh:
                data = json.load(fh)
    except Exception as e:
        print(f"跳过 {zp}: {e}")
        continue

    strat = list(data['strategy'].values())[0]
    trades = strat.get('trades', [])
    tsl = [t for t in trades if t.get('exit_reason') == 'trailing_stop_loss']
    big_losers = sorted([t for t in tsl if t.get('profit_ratio', 0) * 100 < -8],
                        key=lambda x: x['profit_ratio'])

    print(f"\n{'='*70}")
    print(f"=== {label}  |  TSL总计={len(tsl)}笔  |  亏损>-8%共{len(big_losers)}笔 ===")
    print(f"{'='*70}")

    if not big_losers:
        # 看所有TSL
        big_losers = sorted(tsl, key=lambda x: x['profit_ratio'])[:5]
        print("（无>-8%亏损，改用最差5笔TSL）")

    for i, t in enumerate(big_losers):
        open_rate    = t['open_rate']
        close_rate   = t['close_rate']
        max_rate     = t['max_rate']
        min_rate     = t['min_rate']
        profit_pct   = t['profit_ratio'] * 100
        duration     = t['trade_duration']   # 分钟
        open_date    = t['open_date']
        close_date   = t['close_date']
        pair         = t['pair']
        exit_reason  = t['exit_reason']
        enter_tag    = t.get('enter_tag', '?')

        max_float_pct  = (max_rate  - open_rate) / open_rate * 100
        drop_from_max  = (close_rate - max_rate)  / max_rate  * 100

        # 回测里 trade_duration 是整体持仓时长(分钟)
        # min_rate 是整个持仓期间的最低价 = 触发止损的价
        # 我们需要推算"最高浮盈时刻到止损触发"的时间
        # 回测只给总时长，但可以分析K线时框
        # 该回测用4h图，每根K线=240分钟
        # 以 trade_duration 和 max_rate 出现位置推算

        print(f"\n  ── #{i+1}: {pair} ({enter_tag}) ──")
        print(f"  开仓: {open_date}  @{open_rate:,.2f}")
        print(f"  平仓: {close_date}  @{close_rate:,.2f}  [{exit_reason}]")
        print(f"  总持仓时长: {duration//60}h {duration%60}min  ({duration}分钟 ≈ {duration/240:.1f}根4h K线)")
        print(f"  最大浮盈点: {max_rate:,.2f}  (浮盈: {max_float_pct:+.2f}%)")
        print(f"  止损出场价: {close_rate:,.2f}  (从最高点跌: {drop_from_max:+.2f}%)")
        print(f"  最终利润:   {profit_pct:+.2f}%")

        # 关键判断：是否满足"急速崩跌"特征
        # 判据：max_float > 3% 且 drop_from_max < -10%（即大浮盈后快速崩）
        if max_float_pct > 3.0 and drop_from_max < -10.0:
            print(f"  ⚡ 急速崩跌特征: 曾浮盈 {max_float_pct:.1f}% → 从高点跌 {drop_from_max:.1f}%")
            # 用4h维度估算：TSL从12%以内，若从最高跌超12%触发
            # 推算崩跌区间（粗略，K线粒度为4h）
            tsl_trigger_drop = t.get('stop_loss_ratio', -0.12) * 100
            print(f"  ⚡ TSL触发跌幅: {tsl_trigger_drop:.1f}%（从最高点起算约{(-tsl_trigger_drop + max_float_pct):.1f}%总跌幅）")
        elif max_float_pct < 2.0:
            print(f"  ℹ️  从未浮盈>2%：属于入场即下跌，非急速崩跌")
        else:
            print(f"  ⚠️  中等浮盈({max_float_pct:.1f}%)后回撤，未达急速崩跌门槛")

        # 用K线推算崩跌估计时间
        # 4h回测，1根K线=240分钟
        # 假设最高浮盈发生在持仓中段，剩余时间=崩跌时间（粗略上界）
        k4_candles_total = duration / 240
        # 最乐观：最高价发生在开仓后第1根K线结束时
        crash_time_max_h = duration / 60  # 最长崩跌时间（整个持仓）
        crash_time_min_h = 4             # 最短（1根4h K线）
        if max_float_pct > 1.0:
            print(f"  ⏱  崩跌时间估算（4h图粒度）: 1root4h={crash_time_min_h}h ~ 总持仓{crash_time_max_h:.0f}h")
            print(f"     ↳ 无法精确确定最高点时刻，但止损幅度{abs(tsl_trigger_drop if max_float_pct>3 else 12):.0f}% 需要 ≥1根4h K线")

    print()

# 总结：急速崩跌检测的可行性分析
print("\n" + "="*70)
print("【急速崩跌检测方案可行性分析】")
print("="*70)
print("""
回测数据限制：
  - 4h 时间框架，每根K线=240分钟，price path 无法还原到分钟级
  - max_rate / min_rate 是整个持仓期的历史最高/低，没有时间戳
  - 无法直接从回测JSON确定"从浮盈5%到-8%用了多少分钟"

可替代的分析方法（4h粒度估算）：
  - 如果总持仓时长 < 24h（<6根4h K线），且浮盈后大亏
    → 说明整体是"快速往上、快速下来"的 V形走势
  - 如果总持仓时长 > 72h（>18根4h K线），且浮盈后大亏
    → 说明先震荡上行、再缓慢下跌，不是急速崩跌

结论：
  需要用 data/BTC_USDT-4h.feather 对这5笔交易做K线逐根复盘
  才能精确回答"从浮盈到触发止损用了几根K线（几小时）"
""")
