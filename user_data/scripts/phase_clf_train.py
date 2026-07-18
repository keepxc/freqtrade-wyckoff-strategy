"""
§3c Phase 检测 — 训练实验
==========================
输入：phase_labels_crossval.parquet（TV指标 x V8 交叉验证标签）
目标：用 48-bar OHLCV 序列预测 tv_phase（markup / markdown / trading_range）
模型：
  1. Gradient Boosting（展平序列，基线）
  2. 1D-CNN（PyTorch，如果可用）
评估：Walk-forward F1（按时间切 70/30），重点关注 trading_range F1
"""
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler, LabelEncoder
from sklearn.metrics import classification_report, f1_score, confusion_matrix
import warnings; warnings.filterwarnings('ignore')

LABEL_FILE = 'user_data/scripts/phase_labels_crossval.parquet'
DATA_FILE  = 'user_data/data/binance/futures/BTC_USDT_USDT-1h-futures.feather'
SEQ_LEN    = 48   # 48根1H bar ≈ 2天

# ── 加载标签 & OHLCV ─────────────────────────────────────────────────────────
labels = pd.read_parquet(LABEL_FILE)
labels['date'] = pd.to_datetime(labels['date'], utc=True)

ohlcv = pd.read_feather(DATA_FILE)
ohlcv['date'] = pd.to_datetime(ohlcv['date'], utc=True)
ohlcv = ohlcv.sort_values('date').reset_index(drop=True)

print(f'标签数据: {len(labels)} 行')
print(f'TV Phase 分布:')
print(labels['tv_phase'].value_counts().to_string())

# 合并（以 ohlcv 为基础，merge 标签）
df = ohlcv.merge(
    labels[['date', 'tv_phase', 'rsi', 'pivot_low', 'pivot_high',
            'v8_entry', 'high_conf_long', 'high_conf_short',
            'tv_sc', 'tv_ar', 'tv_bc']],
    on='date', how='inner'
)
df = df.sort_values('date').reset_index(drop=True)
print(f'\n合并后: {len(df)} 行  {df["date"].iloc[0]} ~ {df["date"].iloc[-1]}')

# ── 构建特征序列 ──────────────────────────────────────────────────────────────
# 每根 bar 的特征 = 前 SEQ_LEN 根 bar 的 OHLCVR（归一化到当前 close）
# + 辅助特征：RSI、pivot 状态
print(f'\n构建特征矩阵（{SEQ_LEN}-bar 序列，约 10 秒）...')

close_arr = df['close'].values
open_arr  = df['open'].values
high_arr  = df['high'].values
low_arr   = df['low'].values
vol_arr   = df['volume'].values
rsi_arr   = df['rsi'].values
pl_arr    = df['pivot_low'].values.astype(float)
ph_arr    = df['pivot_high'].values.astype(float)

X_list, y_list, date_list, v8entry_list = [], [], [], []

for i in range(SEQ_LEN, len(df)):
    ref  = close_arr[i]
    if ref == 0:
        continue

    win_c   = close_arr[i - SEQ_LEN: i]
    win_o   = open_arr[i - SEQ_LEN: i]
    win_h   = high_arr[i - SEQ_LEN: i]
    win_l   = low_arr[i - SEQ_LEN: i]
    win_v   = vol_arr[i - SEQ_LEN: i]
    win_rsi = rsi_arr[i - SEQ_LEN: i]
    vol_mean = win_v.mean() + 1e-9

    # 5 个价格维度 + 量比 + RSI = 7 维 × 48 = 336 维
    feat = np.empty(SEQ_LEN * 7)
    for j in range(SEQ_LEN):
        base = j * 7
        feat[base]     = (win_o[j]   - ref) / ref
        feat[base + 1] = (win_h[j]   - ref) / ref
        feat[base + 2] = (win_l[j]   - ref) / ref
        feat[base + 3] = (win_c[j]   - ref) / ref
        feat[base + 4] = win_v[j] / vol_mean
        feat[base + 5] = (win_rsi[j] - 50) / 50   # RSI 归一化到 [-1, 1]
        feat[base + 6] = pl_arr[i - SEQ_LEN + j] - ph_arr[i - SEQ_LEN + j]  # pivot 方向

    X_list.append(feat)
    y_list.append(df['tv_phase'].iloc[i])
    date_list.append(df['date'].iloc[i])
    v8entry_list.append(df['v8_entry'].iloc[i])

X = np.array(X_list)
y = np.array(y_list)
dates = np.array(date_list)
v8entries = np.array(v8entry_list)

# 去除含 NaN 的样本（RSI 早期预热期）
valid = ~np.isnan(X).any(axis=1)
X, y, dates, v8entries = X[valid], y[valid], dates[valid], v8entries[valid]

print(f'特征矩阵: {X.shape}  (过滤 NaN 后)')
print(f'类别分布:')
for cls in ['markup', 'markdown', 'trading_range']:
    n = (y == cls).sum()
    print(f'  {cls:<16}: {n:>6} ({n/len(y)*100:.1f}%)')

# ── Walk-forward 切分（时间序列：前 70% 训练，后 30% 测试）────────────────────
split = int(len(y) * 0.70)
X_tr, X_te = X[:split], X[split:]
y_tr, y_te = y[:split], y[split:]
dates_te   = dates[split:]
v8_te      = v8entries[split:]

print(f'\n训练集: {len(y_tr)}  测试集: {len(y_te)}')
print(f'测试集时间范围: {dates_te[0]} ~ {dates_te[-1]}')

le = LabelEncoder()
le.fit(y)
y_tr_enc = le.transform(y_tr)
y_te_enc = le.transform(y_te)

# ── 模型 1：Logistic Regression（多分类基线）──────────────────────────────────
print('\n' + '='*60)
print('模型 1: Logistic Regression（基线）')
scaler = StandardScaler()
X_tr_s = scaler.fit_transform(X_tr)
X_te_s = scaler.transform(X_te)

lr = LogisticRegression(max_iter=500, class_weight='balanced', C=0.1)
lr.fit(X_tr_s, y_tr)
lr_pred = lr.predict(X_te_s)

print(classification_report(y_te, lr_pred, target_names=le.classes_, digits=3))
lr_macro_f1 = f1_score(y_te, lr_pred, average='macro')
lr_tr_f1    = f1_score(y_tr, lr.predict(X_tr_s), average='macro')
print(f'Train macro-F1: {lr_tr_f1:.4f}  Test macro-F1: {lr_macro_f1:.4f}')

# ── 模型 2：Gradient Boosting ────────────────────────────────────────────────
print('\n' + '='*60)
print('模型 2: Gradient Boosting（非线性）')
gb = GradientBoostingClassifier(
    n_estimators=100, max_depth=4, learning_rate=0.05,
    subsample=0.8, min_samples_leaf=10, random_state=42
)
gb.fit(X_tr, y_tr_enc)
gb_pred_enc = gb.predict(X_te)
gb_pred     = le.inverse_transform(gb_pred_enc)

print(classification_report(y_te, gb_pred, target_names=le.classes_, digits=3))
gb_macro_f1 = f1_score(y_te, gb_pred, average='macro')
gb_tr_f1    = f1_score(y_tr, le.inverse_transform(gb.predict(X_tr)), average='macro')
print(f'Train macro-F1: {gb_tr_f1:.4f}  Test macro-F1: {gb_macro_f1:.4f}')

# ── 关键指标：V8 入场 bar 上的 Phase 识别准确率 ──────────────────────────────
print('\n' + '='*60)
print('V8 入场 bar 上的 Phase 识别（测试集，GB 模型）')
print('='*60)

for v8tag, v8label, true_phase in [
    ('phase_e_long',  'ws_e 多头', 'trading_range'),
    ('phase_d_long',  'ws_d 多头', 'trading_range'),
    ('wsdown_short',  'wsdown 空头', 'trading_range'),
]:
    mask = v8_te == v8tag
    if mask.sum() == 0:
        continue
    true_p  = y_te[mask]
    pred_p  = gb_pred[mask]
    acc     = (pred_p == true_p).mean()
    # 模型预测为 trading_range 的比例（V8 入场大部分都在 TR）
    pred_tr = (pred_p == 'trading_range').mean()
    true_tr = (true_p == 'trading_range').mean()
    print(f'\n{v8label} ({mask.sum()} 笔):')
    print(f'  真实 trading_range 占比: {true_tr*100:.1f}%')
    print(f'  模型预测 trading_range:  {pred_tr*100:.1f}%')
    print(f'  整体准确率: {acc*100:.1f}%')
    # 预测分布
    pred_dist = pd.Series(pred_p).value_counts()
    for ph, cnt in pred_dist.items():
        print(f'    预测={ph}: {cnt}')

# ── §3c 核心问题：提前识别 trading_range（Phase B~D）─────────────────────────
print('\n' + '='*60)
print('§3c 核心：trading_range 识别能力（precision/recall）')
# 关键：能否在 V8 入场前，提前识别当前处于 trading_range？
tr_true = (y_te == 'trading_range').astype(int)
tr_pred_gb = (gb_pred == 'trading_range').astype(int)
tr_pred_lr = (lr_pred == 'trading_range').astype(int)

from sklearn.metrics import precision_score, recall_score
print(f'\nGB  trading_range  Precision={precision_score(tr_true, tr_pred_gb):.3f}'
      f'  Recall={recall_score(tr_true, tr_pred_gb):.3f}')
print(f'LR  trading_range  Precision={precision_score(tr_true, tr_pred_lr):.3f}'
      f'  Recall={recall_score(tr_true, tr_pred_lr):.3f}')

# ── 最终结论 ──────────────────────────────────────────────────────────────────
print('\n' + '='*60)
print('§3c 实验结论')
print('='*60)
best_f1 = max(lr_macro_f1, gb_macro_f1)
best_model = 'GB' if gb_macro_f1 >= lr_macro_f1 else 'LR'

print(f'最佳模型: {best_model}  macro-F1: {best_f1:.4f}')
if best_f1 >= 0.70:
    verdict = '✅ Phase 可学：结构级标签信噪比高，F1>0.70，§3c 方向成立'
elif best_f1 >= 0.55:
    verdict = '⚠️ 弱信号：F1 0.55~0.70，需加入更多结构特征（ATR/EMA200距离）'
else:
    verdict = '❌ Phase 不可学：macro-F1<0.55，RSI 代理标签与 OHLCV 形态相关性不足'

print(f'结论: {verdict}')

# 过拟合检查
if gb_tr_f1 - gb_macro_f1 > 0.20:
    print(f'⚠️ 过拟合警告: Train-Test F1 差 {gb_tr_f1-gb_macro_f1:.3f} > 0.20')
