"""
TASK 29 预审计 Part 3：空头量价不协调（九大检验 Sell Test 2）
目标：量化 108 笔空头入场前，上涨缩量/下跌放量的量价背离是否与胜率相关

量价背离定义（两种方式）：
  方式 A（简单）：入场前 N 根 bar 中，上涨 bar 均量 < 下跌 bar 均量
  方式 B（精细）：上涨 bar 量/量MA 的比值 < 下跌 bar 量/量MA 的比值

分析维度：
1. 有量价背离 vs 无量价背离 → 胜率 / 平均盈利差异
2. 不同回望窗口（24H / 48H / 72H）敏感度
3. 按空头路径分组（wave_breakdown / FTI / REDIST 等）
"""
import zipfile, json, pandas as pd, numpy as np
from pathlib import Path

RESULTS_DIR = Path('user_data/backtest_results')
TASK27_ZIP  = RESULTS_DIR / 'backtest-result-2026-06-05_16-42-07.zip'
DATA_1H     = Path('user_data/data/binance/futures/BTC_USDT_USDT-1h-futures.feather')

def load_trades(zip_path):
    zf = zipfile.ZipFile(zip_path)
    fname = [f for f in zf.namelist() if f.endswith('.json') and 'meta' not in f][0]
    data = json.loads(zf.read(fname))
    df = pd.DataFrame(data['strategy']['WyckoffV8']['trades'])
    df['open_date']  = pd.to_datetime(df['open_date'], utc=True)
    df['close_date'] = pd.to_datetime(df['close_date'], utc=True)
    return df

df_trades = load_trades(TASK27_ZIP)
shorts = df_trades[df_trades['is_short'] == True].copy()
print(f"空头总数: {len(shorts)}")
print(f"路径分布:\n{shorts['enter_tag'].value_counts()}\n")

# ── 加载 1H 数据 ────────────────────────────────────────────────────
df_1h = pd.read_feather(DATA_1H)
df_1h['date'] = pd.to_datetime(df_1h['date'], utc=True)
df_1h = df_1h.sort_values('date').reset_index(drop=True)

# 预计算成交量 MA（20 bar）
df_1h['vol_ma20'] = df_1h['volume'].rolling(20, min_periods=5).mean()
df_1h['is_up_bar'] = df_1h['close'] > df_1h['open']
df_1h['vol_ratio'] = df_1h['volume'] / df_1h['vol_ma20'].replace(0, np.nan)

print(f"1H 数据范围: {df_1h['date'].iloc[0]} ~ {df_1h['date'].iloc[-1]}")

# ── 量价背离检测函数 ────────────────────────────────────────────────
def check_vol_divergence(entry_date, lookback_bars):
    """
    检测入场前 lookback_bars 根 bar 内的量价背离。
    返回:
      bearish_divergence_A: 上涨均量 < 下跌均量（简单方式）
      bearish_divergence_B: 上涨量比 < 下跌量比（相对 MA）
      up_vol_mean, down_vol_mean, up_vol_ratio, down_vol_ratio
      n_up_bars, n_down_bars
    """
    idx = df_1h['date'].searchsorted(entry_date, side='right') - 1
    if idx < lookback_bars:
        return None

    window = df_1h.iloc[max(0, idx - lookback_bars): idx]
    if len(window) < 5:
        return None

    up_bars   = window[window['is_up_bar'] == True]
    down_bars = window[window['is_up_bar'] == False]

    if len(up_bars) == 0 or len(down_bars) == 0:
        return None

    up_vol_mean   = up_bars['volume'].mean()
    down_vol_mean = down_bars['volume'].mean()
    up_vol_ratio  = up_bars['vol_ratio'].mean()
    down_vol_ratio= down_bars['vol_ratio'].mean()

    # 方式 A：绝对量——上涨均量 < 下跌均量 = 看跌背离
    div_A = up_vol_mean < down_vol_mean

    # 方式 B：相对量（排除整体量能变化的干扰）
    div_B = up_vol_ratio < down_vol_ratio

    return {
        'div_A': div_A,
        'div_B': div_B,
        'up_vol': up_vol_mean,
        'dn_vol': down_vol_mean,
        'up_ratio': up_vol_ratio,
        'dn_ratio': down_vol_ratio,
        'n_up': len(up_bars),
        'n_dn': len(down_bars),
        'vol_imbalance': down_vol_mean / up_vol_mean,  # > 1 = 下跌量更大
    }

# ── 对每笔空头跑检测 ────────────────────────────────────────────────
print("检测每笔空头入场前的量价背离...")

for lookback in [24, 48, 72]:
    results = []
    for _, row in shorts.iterrows():
        r = check_vol_divergence(row['open_date'], lookback)
        if r is None:
            r = {'div_A': np.nan, 'div_B': np.nan, 'vol_imbalance': np.nan,
                 'n_up': 0, 'n_dn': 0}
        r['profit_ratio'] = row['profit_ratio']
        r['enter_tag']    = row['enter_tag']
        r['exit_reason']  = row['exit_reason']
        results.append(r)

    df_r = pd.DataFrame(results)
    df_r['is_win'] = df_r['profit_ratio'] > 0

    print(f"\n{'='*60}")
    print(f"回望窗口 {lookback}H")
    print(f"{'='*60}")

    # 方式 A
    has_A   = df_r[df_r['div_A'] == True]
    no_A    = df_r[df_r['div_A'] == False]
    print(f"\n方式A（上涨均量 < 下跌均量）：")
    print(f"  有背离: {len(has_A):3d} 笔  胜率 {has_A['is_win'].mean():.1%}  均盈 {has_A['profit_ratio'].mean():+.4f}")
    print(f"  无背离: {len(no_A):3d} 笔  胜率 {no_A['is_win'].mean():.1%}  均盈 {no_A['profit_ratio'].mean():+.4f}")
    print(f"  背离存在率: {df_r['div_A'].mean():.1%}  胜率差: {has_A['is_win'].mean()-no_A['is_win'].mean():+.1%}")

    # 方式 B
    has_B   = df_r[df_r['div_B'] == True]
    no_B    = df_r[df_r['div_B'] == False]
    print(f"\n方式B（相对量比：上涨量比 < 下跌量比）：")
    print(f"  有背离: {len(has_B):3d} 笔  胜率 {has_B['is_win'].mean():.1%}  均盈 {has_B['profit_ratio'].mean():+.4f}")
    print(f"  无背离: {len(no_B):3d} 笔  胜率 {no_B['is_win'].mean():.1%}  均盈 {no_B['profit_ratio'].mean():+.4f}")
    print(f"  背离存在率: {df_r['div_B'].mean():.1%}  胜率差: {has_B['is_win'].mean()-no_B['is_win'].mean():+.1%}")

    # 量失衡比分布
    print(f"\n量失衡比（下跌量/上涨量）分布：")
    print(f"  中位数: {df_r['vol_imbalance'].median():.3f}  均值: {df_r['vol_imbalance'].mean():.3f}")
    print(f"  > 1.0（下跌量更大）: {(df_r['vol_imbalance']>1).mean():.1%}")
    print(f"  > 1.5: {(df_r['vol_imbalance']>1.5).mean():.1%}")
    print(f"  > 2.0: {(df_r['vol_imbalance']>2.0).mean():.1%}")

    # 亏损空头的量价背离情况
    losers = df_r[df_r['is_win'] == False]
    if len(losers) > 0:
        print(f"\n亏损空头({len(losers)}笔)的背离率: A={losers['div_A'].mean():.1%}  B={losers['div_B'].mean():.1%}")

# ── 按路径分组（48H）──────────────────────────────────────────────
print(f"\n{'='*60}")
print("按空头路径分组（48H，方式B）")
print(f"{'='*60}")

results_48 = []
for _, row in shorts.iterrows():
    r = check_vol_divergence(row['open_date'], 48)
    if r is None:
        r = {'div_A': np.nan, 'div_B': np.nan, 'vol_imbalance': np.nan, 'n_up':0,'n_dn':0}
    r['profit_ratio'] = row['profit_ratio']
    r['enter_tag']    = row['enter_tag']
    results_48.append(r)

df_48 = pd.DataFrame(results_48)
df_48['is_win'] = df_48['profit_ratio'] > 0

for tag in df_48['enter_tag'].unique():
    sub = df_48[df_48['enter_tag'] == tag]
    div_rate = sub['div_B'].mean()
    wr_div   = sub[sub['div_B']==True]['is_win'].mean() if sub['div_B'].sum() > 0 else np.nan
    wr_nodiv = sub[sub['div_B']==False]['is_win'].mean() if (~sub['div_B']).sum() > 0 else np.nan
    print(f"{tag:<30} {len(sub):3d}笔  背离率{div_rate:.1%}  "
          f"有背离WR:{wr_div:.1%}  无背离WR:{wr_nodiv:.1%}" if not np.isnan(wr_div) and not np.isnan(wr_nodiv)
          else f"{tag:<30} {len(sub):3d}笔  背离率{div_rate:.1%}")

print(f"\n{'='*60}")
print("核心结论")
print(f"{'='*60}")
df_48_clean = df_48.dropna(subset=['div_B'])
print(f"有量价背离(B)空头: {df_48_clean['div_B'].sum()} 笔  "
      f"WR {df_48_clean[df_48_clean['div_B']==True]['is_win'].mean():.1%}")
print(f"无量价背离(B)空头: {(~df_48_clean['div_B']).sum()} 笔  "
      f"WR {df_48_clean[df_48_clean['div_B']==False]['is_win'].mean():.1%}")
