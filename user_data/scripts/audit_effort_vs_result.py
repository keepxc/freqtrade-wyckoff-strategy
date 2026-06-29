"""
Filter Value 实验 — 多头路径（ws_e / ws_d）
==========================================
问题：在 V8 已成交的多头入场中，模型分数能否区分"最终盈利"和"最终亏损"？

标签：forward MAE（入场后 24h 内最大逆向波动）
  MAE >= -0.5% → 好入场（方向立即成立）
  MAE <  -0.5% → 坏入场（入场被立即证伪）

AUC > 0.65 → filter 有价值；AUC ~ 0.50 → 无增益，ML 方向终止
"""
import zipfile, json
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score

BT_ZIP    = 'user_data/backtest_results/backtest-result-2026-06-26_00-42-22.zip'
DATA_FILE = 'user_data/data/binance/futures/BTC_USDT_USDT-1h-futures.feather'
SEQ_LEN   = 48
MAE_BARS  = 24
MAE_THR   = -0.005

def load_trades(zf_path):
    with zipfile.ZipFile(zf_path) as z:
        fname = [n for n in z.namelist()
                 if n.endswith('.json') and 'config' not in n and '.py' not in n][0]
        data = json.loads(z.read(fname))
    return list(data['strategy'].values())[0]['trades']

trades = load_trades(BT_ZIP)
long_trades = [t for t in trades if t.get('enter_tag') in ('v8_ws_e', 'v8_ws_d')]
print(f'多头入场总笔数: {len(long_trades)}')
print(f'  v8_ws_e: {sum(1 for t in long_trades if t["enter_tag"]=="v8_ws_e")}')
print(f'  v8_ws_d: {sum(1 for t in long_trades if t["enter_tag"]=="v8_ws_d")}')

ohlcv = pd.read_feather(DATA_FILE)
ohlcv['date'] = pd.to_datetime(ohlcv['date'], utc=True)
ohlcv = ohlcv.sort_values('date').reset_index(drop=True)

records = []
for t in long_trades:
    open_date = pd.to_datetime(t['open_date'], utc=True)
    entry     = t['open_rate']
    profit    = t['profit_ratio'] * 100
    tag       = t['enter_tag']

    idx = ohlcv.index[ohlcv['date'] <= open_date]
    if len(idx) < SEQ_LEN + 1:
        continue
    i = idx[-1]

    window = ohlcv.iloc[i - SEQ_LEN: i]
    if len(window) < SEQ_LEN:
        continue

    feat = []
    vol_mean = window['volume'].mean() + 1e-9
    for _, row in window.iterrows():
        feat.append((row['open']  - entry) / entry)
        feat.append((row['high']  - entry) / entry)
        feat.append((row['low']   - entry) / entry)
        feat.append((row['close'] - entry) / entry)
        feat.append(row['volume'] / vol_mean)

    future = ohlcv.iloc[i: i + MAE_BARS + 1]
    if len(future) < 3:
        continue
    mae = (future['low'].min() - entry) / entry
    label = 1 if mae >= MAE_THR else 0

    records.append({
        'date': open_date, 'tag': tag,
        'profit': profit, 'mae': mae * 100,
        'label': label, 'feat': feat,
    })

df_meta = pd.DataFrame([{k: v for k, v in r.items() if k != 'feat'} for r in records])
X = np.array([r['feat'] for r in records])
y = np.array([r['label'] for r in records])
dates = np.array([r['date'] for r in records])

print(f'\n有效样本: {len(y)}  好入场={y.sum()}  坏入场={(y==0).sum()}')
print(f'MAE 中位={df_meta["mae"].median():.2f}%  均值={df_meta["mae"].mean():.2f}%')
print('\nMAE 分布:')
for thr in [-2.0, -1.0, -0.5, -0.3, 0.0]:
    n = (df_meta['mae'] < thr).sum()
    print(f'  MAE < {thr:.1f}%: {n} 笔 ({n/len(df_meta)*100:.1f}%)')

sort_idx = np.argsort(dates)
X, y = X[sort_idx], y[sort_idx]
df_meta = df_meta.iloc[sort_idx].reset_index(drop=True)

split = int(len(y) * 0.70)
X_tr, X_te = X[:split], X[split:]
y_tr, y_te = y[:split], y[split:]

print(f'\n训练集: {len(y_tr)} (好={y_tr.sum()} 坏={(y_tr==0).sum()})')
print(f'测试集: {len(y_te)} (好={y_te.sum()} 坏={(y_te==0).sum()})')

scaler = StandardScaler()
lr = LogisticRegression(max_iter=1000, class_weight='balanced', C=0.1)
lr.fit(scaler.fit_transform(X_tr), y_tr)
lr_auc = roc_auc_score(y_te, lr.predict_proba(scaler.transform(X_te))[:, 1])
print(f'\n[Logistic Regression] AUC = {lr_auc:.4f}')

gb = GradientBoostingClassifier(
    n_estimators=200, max_depth=3, learning_rate=0.05,
    subsample=0.8, min_samples_leaf=5, random_state=42)
gb.fit(X_tr, y_tr)
gb_proba = gb.predict_proba(X_te)[:, 1]
gb_auc = roc_auc_score(y_te, gb_proba)
print(f'[Gradient Boosting]   AUC = {gb_auc:.4f}')

best_auc = max(lr_auc, gb_auc)
print('\n' + '='*60)
if best_auc >= 0.65:
    verdict = '✅ Filter 有价值：可识别劣质入场，ML 门控方向可行'
elif best_auc >= 0.58:
    verdict = '⚠️  弱信号：需加入 Wyckoff 结构特征再确认'
else:
    verdict = '❌ 无 filter 价值：无法区分赢家/输家，ML 方向终止'
print(f'最佳 AUC: {best_auc:.4f}')
print(f'结论: {verdict}')

print('\n=== 分位数门控效果（测试集）===')
print(f'{"分数阈值":>10} {"拒绝":>6} {"拒绝率":>7} {"拒绝坏比":>9} {"保留好率":>9} {"保留avgMAE":>11}')
print('-' * 56)
meta_te = df_meta.iloc[split:].reset_index(drop=True)
for q in [0.2, 0.3, 0.4, 0.5]:
    thr  = np.quantile(gb_proba, q)
    keep = gb_proba >= thr
    rej  = ~keep
    if keep.sum() == 0:
        continue
    rej_bad  = (y_te[rej] == 0).mean() * 100 if rej.sum() > 0 else float('nan')
    keep_wr  = y_te[keep].mean() * 100
    keep_mae = meta_te.loc[keep, 'mae'].mean()
    print(f'{thr:>10.3f} {rej.sum():>6} {rej.sum()/len(y_te)*100:>6.1f}%'
          f' {rej_bad:>8.1f}% {keep_wr:>8.1f}% {keep_mae:>+10.2f}%')

corr = df_meta['mae'].corr(df_meta['profit'])
print(f'\nMAE vs profit 相关系数: {corr:.3f}  (|r|>0.3 = MAE 是 profit 的有效代理)')

bad  = df_meta[df_meta['mae'] < -1.0]
good = df_meta[df_meta['mae'] >= -0.5]
print(f'\n被证伪（MAE<-1%）{len(bad)} 笔: profit 均={bad["profit"].mean():+.2f}%  WR={(bad["profit"]>0).mean()*100:.1f}%')
print(f'好入场（MAE≥-0.5%）{len(good)} 笔: profit 均={good["profit"].mean():+.2f}%  WR={(good["profit"]>0).mean()*100:.1f}%')
