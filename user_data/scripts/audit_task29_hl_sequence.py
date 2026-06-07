"""
TASK 29 预审计 Part 2：HH/HL 序列检测（九大检验 Buy Test 5/6）
目标：量化 471 笔多头入场时，入场前 N 根 1H bar 内是否存在 Higher Low 序列

Higher Low (HL) 定义：
  在入场前回望窗口内，至少出现 2 个局部低点，且后低点 > 前低点
  局部低点 = 该 bar 的 low < 左右各 K 根 bar 的 low（简单摆动低点）

Higher High (HH) 定义：
  在入场前回望窗口内，至少出现 2 个局部高点，且后高点 > 前高点

分析维度：
1. 有 HL 序列 vs 无 HL 序列 → 胜率 / 平均盈利差异
2. 有 HH 序列 vs 无 HH 序列 → 同上
3. 回望窗口敏感度（24h / 48h / 72h）
4. Phase D vs Phase E 分层
"""
import zipfile, json, pandas as pd, numpy as np
from pathlib import Path

RESULTS_DIR = Path('user_data/backtest_results')
TASK27_ZIP  = RESULTS_DIR / 'backtest-result-2026-06-05_16-42-07.zip'
DATA_FILE   = Path('user_data/data/binance/futures/BTC_USDT_USDT-1h-futures.feather')

# ── 加载回测交易 ────────────────────────────────────────────────────
def load_trades(zip_path):
    zf = zipfile.ZipFile(zip_path)
    fname = [f for f in zf.namelist() if f.endswith('.json') and 'meta' not in f][0]
    data = json.loads(zf.read(fname))
    df = pd.DataFrame(data['strategy']['WyckoffV8']['trades'])
    df['open_date']  = pd.to_datetime(df['open_date'], utc=True)
    df['close_date'] = pd.to_datetime(df['close_date'], utc=True)
    return df

df_trades = load_trades(TASK27_ZIP)
longs = df_trades[df_trades['is_short'] == False].copy()
print(f"多头总数: {len(longs)}")

# ── 加载 1H OHLCV ───────────────────────────────────────────────────
df_1h = pd.read_feather(DATA_FILE)
df_1h['date'] = pd.to_datetime(df_1h['date'], utc=True)
df_1h = df_1h.sort_values('date').reset_index(drop=True)
print(f"1H 数据范围: {df_1h['date'].iloc[0]} ~ {df_1h['date'].iloc[-1]}")
print(f"1H 总 bar 数: {len(df_1h)}")

# ── 摆动低点 / 高点检测 ─────────────────────────────────────────────
def find_swing_lows(series, wing=3):
    """返回局部低点的布尔 Series（左右各 wing 根 bar 的最低值）"""
    result = pd.Series(False, index=series.index)
    for i in range(wing, len(series) - wing):
        window = series.iloc[i-wing:i+wing+1]
        if series.iloc[i] == window.min():
            result.iloc[i] = True
    return result

def find_swing_highs(series, wing=3):
    result = pd.Series(False, index=series.index)
    for i in range(wing, len(series) - wing):
        window = series.iloc[i-wing:i+wing+1]
        if series.iloc[i] == window.max():
            result.iloc[i] = True
    return result

# ── 预计算全量摆动点（避免每次交易重复计算）────────────────────────
WING = 3
print("\n计算全量摆动点（wing=3）...")
df_1h['is_swing_low']  = find_swing_lows(df_1h['low'],   wing=WING)
df_1h['is_swing_high'] = find_swing_highs(df_1h['high'], wing=WING)
print(f"摆动低点数: {df_1h['is_swing_low'].sum()}")
print(f"摆动高点数: {df_1h['is_swing_high'].sum()}")

# 建立 date → index 的快速映射
date_to_idx = {d: i for i, d in enumerate(df_1h['date'])}

# ── 对每笔多头检测入场前的 HL/HH 序列 ────────────────────────────
def check_hl_hh(entry_date, lookback_bars, min_pivots=2):
    """
    在 entry_date 前 lookback_bars 根 bar 内检测 HL/HH 序列。
    HL: 至少 min_pivots 个摆动低点，且后低点 > 前低点（单调递增）
    HH: 至少 min_pivots 个摆动高点，且后高点 > 前高点
    返回 (has_hl, has_hh, n_swing_lows, n_swing_highs, last_hl_gap, last_hh_gap)
    """
    if entry_date not in date_to_idx:
        # 找最近的 bar
        idx = df_1h['date'].searchsorted(entry_date, side='right') - 1
    else:
        idx = date_to_idx[entry_date]

    if idx < lookback_bars:
        return False, False, 0, 0, np.nan, np.nan

    window = df_1h.iloc[idx - lookback_bars: idx]

    # 摆动低点序列
    sl = window[window['is_swing_low']]['low'].values
    has_hl = False
    if len(sl) >= min_pivots:
        # 检查是否严格递增（至少相邻两个满足）
        for i in range(len(sl) - 1):
            if sl[i+1] > sl[i]:
                has_hl = True
                break

    # 摆动高点序列
    sh = window[window['is_swing_high']]['high'].values
    has_hh = False
    if len(sh) >= min_pivots:
        for i in range(len(sh) - 1):
            if sh[i+1] > sh[i]:
                has_hh = True
                break

    # 最近一个摆动低点距入场的距离（bar 数）
    sl_bars = window[window['is_swing_low']]
    last_hl_gap = (idx - sl_bars.index[-1]) if len(sl_bars) > 0 else np.nan
    sh_bars = window[window['is_swing_high']]
    last_hh_gap = (idx - sh_bars.index[-1]) if len(sh_bars) > 0 else np.nan

    return has_hl, has_hh, len(sl), len(sh), last_hl_gap, last_hh_gap

print("\n检测每笔多头入场前的 HL/HH 序列...")

results = []
for lookback in [24, 48, 72]:
    col_hl  = f'has_hl_{lookback}h'
    col_hh  = f'has_hh_{lookback}h'
    col_nsl = f'n_sl_{lookback}h'
    col_nsh = f'n_sh_{lookback}h'

    hl_list, hh_list, nsl_list, nsh_list = [], [], [], []
    for _, row in longs.iterrows():
        has_hl, has_hh, nsl, nsh, _, _ = check_hl_hh(
            row['open_date'], lookback_bars=lookback
        )
        hl_list.append(has_hl)
        hh_list.append(has_hh)
        nsl_list.append(nsl)
        nsh_list.append(nsh)

    longs[col_hl]  = hl_list
    longs[col_hh]  = hh_list
    longs[col_nsl] = nsl_list
    longs[col_nsh] = nsh_list

print("完成。")

# ── 输出分析 ────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("分析结果")
print("=" * 70)

for lookback in [24, 48, 72]:
    col_hl = f'has_hl_{lookback}h'
    col_hh = f'has_hh_{lookback}h'

    print(f"\n【回望窗口 {lookback}H（约 {lookback//24} 天）】")
    print("-" * 50)

    for col, label in [(col_hl, 'Higher Low (HL)'), (col_hh, 'Higher High (HH)')]:
        has     = longs[longs[col] == True]
        has_not = longs[longs[col] == False]
        print(f"\n{label}：")
        print(f"  有序列: {len(has):3d} 笔  胜率 {(has['profit_ratio']>0).mean():.1%}  "
              f"均盈 {has['profit_ratio'].mean():+.4f}  "
              f"均亏笔均 {has[has['profit_ratio']<0]['profit_ratio'].mean():+.4f}" if len(has[has['profit_ratio']<0])>0 else
              f"  有序列: {len(has):3d} 笔  胜率 {(has['profit_ratio']>0).mean():.1%}  均盈 {has['profit_ratio'].mean():+.4f}")
        print(f"  无序列: {len(has_not):3d} 笔  胜率 {(has_not['profit_ratio']>0).mean():.1%}  "
              f"均盈 {has_not['profit_ratio'].mean():+.4f}")
        print(f"  有/总: {len(has)/len(longs):.1%}  胜率差: {(has['profit_ratio']>0).mean()-(has_not['profit_ratio']>0).mean():+.1%}")

# ── Phase D vs Phase E 分层（48H 窗口）───────────────────────────
print("\n【Phase D vs Phase E 分层（48H 窗口）】")
print("-" * 50)
for tag in ['v8_ws_d', 'v8_ws_e']:
    subset = longs[longs['enter_tag'] == tag]
    if len(subset) == 0:
        continue
    hl_rate = subset['has_hl_48h'].mean()
    hh_rate = subset['has_hh_48h'].mean()
    print(f"\n{tag}（{len(subset)}笔）：")
    print(f"  HL 存在率: {hl_rate:.1%}  HH 存在率: {hh_rate:.1%}")
    for col, label in [('has_hl_48h','HL'), ('has_hh_48h','HH')]:
        has     = subset[subset[col] == True]
        has_not = subset[subset[col] == False]
        if len(has) > 0 and len(has_not) > 0:
            print(f"  {label} 有({len(has)}): WR {(has['profit_ratio']>0).mean():.1%} avg {has['profit_ratio'].mean():+.4f}"
                  f"  vs 无({len(has_not)}): WR {(has_not['profit_ratio']>0).mean():.1%} avg {has_not['profit_ratio'].mean():+.4f}")

# ── 亏损交易中有无 HL 序列 ──────────────────────────────────────────
print("\n【亏损交易中 HL/HH 序列分布（48H 窗口）】")
print("-" * 50)
losers = longs[longs['profit_ratio'] < 0]
winners = longs[longs['profit_ratio'] > 0]
print(f"亏损笔数: {len(losers)}")
print(f"  有 HL 序列: {losers['has_hl_48h'].sum()} ({losers['has_hl_48h'].mean():.1%})")
print(f"  有 HH 序列: {losers['has_hh_48h'].sum()} ({losers['has_hh_48h'].mean():.1%})")
print(f"盈利笔数: {len(winners)}")
print(f"  有 HL 序列: {winners['has_hl_48h'].sum()} ({winners['has_hl_48h'].mean():.1%})")
print(f"  有 HH 序列: {winners['has_hh_48h'].sum()} ({winners['has_hh_48h'].mean():.1%})")

print("\n" + "=" * 70)
print("关键结论")
print("=" * 70)
for lookback in [24, 48, 72]:
    hl_rate = longs[f'has_hl_{lookback}h'].mean()
    hh_rate = longs[f'has_hh_{lookback}h'].mean()
    hl_wr   = longs[longs[f'has_hl_{lookback}h']==True]['profit_ratio'].apply(lambda x: x>0).mean()
    no_hl_wr= longs[longs[f'has_hl_{lookback}h']==False]['profit_ratio'].apply(lambda x: x>0).mean()
    print(f"{lookback}H: HL存在率={hl_rate:.1%}  HH存在率={hh_rate:.1%}  "
          f"有HL胜率={hl_wr:.1%}  无HL胜率={no_hl_wr:.1%}  差={hl_wr-no_hl_wr:+.1%}")
