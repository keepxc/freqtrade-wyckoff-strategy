"""
TASK 44 预研：wsdown 入场前 bar 叙事形态审计
=============================================
目标：分析 v8_short_wsdown 亏损笔 vs 盈利笔在入场 bar 及前 2 根 bar 的形态差异

Bar 分类（威科夫叙事）：
  impulse    : 宽实体阴线 + 量放大 + CIR 低（空头主导）
  continuation: 中等阴线 + 量平 + CIR 偏低（空头延续）
  absorption : 高量 + 价差收窄 + CIR 偏高（买方吸筹，叙事破坏信号）
  exhaustion : 量缩 + 窄实体（空头衰竭，不宜入场）
  reversal   : 阳线 + 高量（买方反攻）
  neutral    : 其他

审计维度：
  1. 入场 bar (bar[0]) 的叙事分类分布
  2. bar[-1](前1根) 的叙事分类
  3. 入场前 6 根内是否有 impulse bar
  4. 入场前 3 根内是否有 absorption bar
  5. 亏损 vs 盈利的形态差异
"""
import zipfile, json
import pandas as pd
import numpy as np
from pathlib import Path

BT_ZIP    = 'user_data/backtest_results/backtest-result-2026-06-26_00-42-22.zip'
DATA_FILE = 'user_data/data/binance/futures/BTC_USDT_USDT-1h-futures.feather'

# ── 加载回测交易 ─────────────────────────────────────────────────────────────
def load_trades(zf_path):
    with zipfile.ZipFile(zf_path) as z:
        fname = [n for n in z.namelist()
                 if n.endswith('.json') and 'config' not in n and '.py' not in n][0]
        data = json.loads(z.read(fname))
    return list(data['strategy'].values())[0]['trades']

trades = load_trades(BT_ZIP)
wsdown = [t for t in trades if t.get('enter_tag') == 'v8_short_wsdown']
print(f'v8_short_wsdown 总笔数: {len(wsdown)}')

# ── 加载 1H K线 ───────────────────────────────────────────────────────────────
ohlcv = pd.read_feather(DATA_FILE)
ohlcv['date'] = pd.to_datetime(ohlcv['date'], utc=True)
ohlcv = ohlcv.sort_values('date').reset_index(drop=True)

# 预计算特征
ohlcv['body_pct']   = (ohlcv['open'] - ohlcv['close']).abs() / ohlcv['open'] * 100
ohlcv['range_pct']  = (ohlcv['high'] - ohlcv['low']) / ohlcv['open'] * 100
ohlcv['CIR']        = (ohlcv['close'] - ohlcv['low']) / (ohlcv['high'] - ohlcv['low']).replace(0, np.nan)
ohlcv['is_bear']    = ohlcv['close'] < ohlcv['open']
ohlcv['vol_ma20']   = ohlcv['volume'].rolling(20).mean()
ohlcv['vol_ratio']  = ohlcv['volume'] / ohlcv['vol_ma20']

def classify_bar(row):
    """威科夫叙事分类"""
    cir      = row['CIR'] if not np.isnan(row['CIR']) else 0.5
    body     = row['body_pct']
    rng      = row['range_pct']
    vol_r    = row['vol_ratio'] if not np.isnan(row['vol_ratio']) else 1.0
    is_bear  = row['is_bear']

    # 阳线反转
    if not is_bear and body > 0.3 and vol_r > 1.2:
        return 'reversal'

    # 吸筹：高量但价差收窄且收盘偏高
    if vol_r > 1.5 and rng < 0.4 and cir > 0.45:
        return 'absorption'

    # 冲击棒：宽实体阴线 + 量放大 + 低收
    if is_bear and body > 0.3 and vol_r > 1.1 and cir < 0.40:
        return 'impulse'

    # 衰竭：量缩 + 窄实体
    if vol_r < 0.7 and body < 0.25:
        return 'exhaustion'

    # 延续：阴线 + 量平 + 中低收
    if is_bear and body > 0.1 and cir < 0.55:
        return 'continuation'

    return 'neutral'

ohlcv['bar_type'] = ohlcv.apply(classify_bar, axis=1)

print(f'1H K线范围: {ohlcv["date"].iloc[0]} ~ {ohlcv["date"].iloc[-1]}  共 {len(ohlcv)} 根')
print(f'\n全局 bar_type 分布:')
print(ohlcv['bar_type'].value_counts().to_string())

# ── 对每笔 wsdown 提取入场前 bar 序列 ────────────────────────────────────────
records = []
for t in wsdown:
    open_date = pd.to_datetime(t['open_date'], utc=True)
    profit    = t['profit_ratio'] * 100
    is_win    = profit > 0
    exit_rsn  = t['exit_reason']

    idx = ohlcv.index[ohlcv['date'] <= open_date]
    if len(idx) < 8:
        continue
    i = idx[-1]  # 入场 bar

    # 提取入场 bar 和前 6 根
    bars = ohlcv.iloc[max(0, i-5):i+1].copy()  # bar[-5] ~ bar[0]
    if len(bars) < 3:
        continue

    b0  = bars.iloc[-1]   # 入场 bar
    bm1 = bars.iloc[-2]   # 前 1 根
    bm2 = bars.iloc[-3]   # 前 2 根

    # 入场前 6 根内有 impulse
    has_impulse_6 = (bars['bar_type'] == 'impulse').any()
    # 入场前 3 根（含入场 bar）有 absorption
    has_absorption_3 = (bars.iloc[-3:]['bar_type'] == 'absorption').any()
    # 入场前 3 根内有 reversal
    has_reversal_3 = (bars.iloc[-3:]['bar_type'] == 'reversal').any()

    # 叙事完整性评分：impulse 存在 + 无吸筹 + 无反转
    narrative_intact = has_impulse_6 and not has_absorption_3 and not has_reversal_3

    records.append({
        'open_date':          open_date,
        'profit':             profit,
        'is_win':             is_win,
        'exit_rsn':           exit_rsn,
        'b0_type':            b0['bar_type'],
        'b0_CIR':             b0['CIR'],
        'b0_body':            b0['body_pct'],
        'b0_vol_ratio':       b0['vol_ratio'],
        'b0_is_bear':         b0['is_bear'],
        'bm1_type':           bm1['bar_type'],
        'bm1_CIR':            bm1['CIR'],
        'bm2_type':           bm2['bar_type'],
        'has_impulse_6':      has_impulse_6,
        'has_absorption_3':   has_absorption_3,
        'has_reversal_3':     has_reversal_3,
        'narrative_intact':   narrative_intact,
    })

df = pd.DataFrame(records)
wins   = df[df['is_win']]
losses = df[~df['is_win']]
print(f'\n有效分析笔数: {len(df)}  盈利: {len(wins)}  亏损: {len(losses)}')

# ── 1. 入场 bar 类型分布 ──────────────────────────────────────────────────────
print('\n=== 入场 bar (b0) 类型分布 ===')
print(f'{"类型":<14} {"全部":>6} {"盈利":>6} {"亏损":>6} {"亏损率":>8}')
print('-' * 44)
for bt in ['impulse','continuation','absorption','exhaustion','reversal','neutral']:
    n_all  = (df['b0_type'] == bt).sum()
    n_win  = (wins['b0_type'] == bt).sum()
    n_loss = (losses['b0_type'] == bt).sum()
    loss_r = n_loss / n_all * 100 if n_all > 0 else 0
    print(f'{bt:<14} {n_all:>6} {n_win:>6} {n_loss:>6} {loss_r:>7.1f}%')

# ── 2. 前 1 根 bar 类型分布 ──────────────────────────────────────────────────
print('\n=== 前 1 根 bar (bm1) 类型分布 ===')
for bt in ['impulse','continuation','absorption','exhaustion','reversal','neutral']:
    n_all  = (df['bm1_type'] == bt).sum()
    n_win  = (wins['bm1_type'] == bt).sum()
    n_loss = (losses['bm1_type'] == bt).sum()
    loss_r = n_loss / n_all * 100 if n_all > 0 else 0
    print(f'{bt:<14} {n_all:>6} {n_win:>6} {n_loss:>6} {loss_r:>7.1f}%')

# ── 3. 叙事完整性门控效果 ────────────────────────────────────────────────────
print('\n=== 叙事完整性门控（impulse_6 且无 absorption_3 且无 reversal_3）===')
intact = df[df['narrative_intact']]
broken = df[~df['narrative_intact']]
print(f'叙事完整 {len(intact)} 笔: WR={intact["is_win"].mean()*100:.1f}%  avg={intact["profit"].mean():+.2f}%')
print(f'叙事破坏 {len(broken)} 笔: WR={broken["is_win"].mean()*100:.1f}%  avg={broken["profit"].mean():+.2f}%')

# ── 4. 单条件拆分效果 ────────────────────────────────────────────────────────
print('\n=== 单条件拆分效果 ===')
for col, label in [
    ('has_impulse_6',    '有 impulse(前6根)'),
    ('has_absorption_3', '有 absorption(前3根)'),
    ('has_reversal_3',   '有 reversal(前3根)'),
]:
    yes = df[df[col]]
    no  = df[~df[col]]
    print(f'\n{label}:')
    print(f'  是 {len(yes):>3} 笔: WR={yes["is_win"].mean()*100:.1f}%  avg={yes["profit"].mean():+.2f}%')
    print(f'  否 {len(no):>3} 笔: WR={no["is_win"].mean()*100:.1f}%   avg={no["profit"].mean():+.2f}%')

# ── 5. b0 CIR 分布对比 ───────────────────────────────────────────────────────
print('\n=== 入场 bar CIR 分布（盈利 vs 亏损）===')
print(f'亏损 {len(losses)} 笔: CIR 中位={losses["b0_CIR"].median():.3f}  '
      f'均值={losses["b0_CIR"].mean():.3f}  '
      f'p75={losses["b0_CIR"].quantile(0.75):.3f}')
print(f'盈利 {len(wins)} 笔:  CIR 中位={wins["b0_CIR"].median():.3f}  '
      f'均值={wins["b0_CIR"].mean():.3f}  '
      f'p75={wins["b0_CIR"].quantile(0.75):.3f}')

bins_cir   = [0, 0.2, 0.35, 0.5, 0.65, 0.8, 1.01]
labels_cir = ['0-0.2','0.2-0.35','0.35-0.5','0.5-0.65','0.65-0.8','0.8-1.0']
df['cir_bucket'] = pd.cut(df['b0_CIR'], bins=bins_cir, labels=labels_cir)
print(f'\n{"CIR 区间":<12} {"全部":>5} {"WR":>7} {"avg":>9}')
print('-' * 36)
for lb in labels_cir:
    g = df[df['cir_bucket'] == lb]
    if len(g) == 0: continue
    print(f'{lb:<12} {len(g):>5} {g["is_win"].mean()*100:>6.1f}% {g["profit"].mean():>+8.2f}%')

# ── 6. vol_ratio 分布对比 ────────────────────────────────────────────────────
print('\n=== 入场 bar 量比（vol/vol_ma20）分布（盈利 vs 亏损）===')
print(f'亏损 {len(losses)} 笔: vol_ratio 中位={losses["b0_vol_ratio"].median():.2f}  '
      f'均值={losses["b0_vol_ratio"].mean():.2f}')
print(f'盈利 {len(wins)} 笔:  vol_ratio 中位={wins["b0_vol_ratio"].median():.2f}  '
      f'均值={wins["b0_vol_ratio"].mean():.2f}')

# ── 7. 亏损笔的 bar 序列（最差 20 笔）────────────────────────────────────────
print(f'\n=== 最差 20 笔的 bar 序列 ===')
print(f'{"open_date":<26} {"profit":>8} {"b0":>14} {"bm1":>14} {"bm2":>14} {"intact":>8}')
print('-' * 90)
worst20 = df.nsmallest(20, 'profit')
for _, r in worst20.iterrows():
    print(f'{str(r["open_date"]):<26} {r["profit"]:>+7.2f}% {r["b0_type"]:>14} '
          f'{r["bm1_type"]:>14} {r["bm2_type"]:>14} {str(r["narrative_intact"]):>8}')
