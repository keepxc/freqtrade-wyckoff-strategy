"""
§3c Phase 检测 — V8 原生 phase_tag 标签生成 + 分类器训练
=========================================================
用 V8 真实量价结构标签（phase_detector.py 输出）替换 TV RSI 代理标签，
验证 §3c 结论是否依赖 RSI 循环。

输出：
  user_data/scripts/v8_phase_labels.parquet   — 日线 + 1H merge 后标签
  user_data/scripts/v8phase_clf_out.txt       — 分类器结果
"""
import sys, os
BASE = '/freqtrade/user_data'
sys.stdout = open(f'{BASE}/scripts/v8phase_clf_out.txt', 'w', buffering=1)
sys.stderr = sys.stdout

import numpy as np
import pandas as pd
import warnings; warnings.filterwarnings('ignore')

# ── V8 模块：从 user_data/strategies 导入（容器内挂载路径）───────────────────
sys.path.insert(0, f'{BASE}/strategies')
from v8_modules.sc_detector import add_v8_sc_detection
from v8_modules.phase_detector import add_v8_phase_detection

DATA_1H  = f'{BASE}/data/binance/futures/BTC_USDT_USDT-1h-futures.feather'
DATA_1D  = f'{BASE}/data/binance/futures/BTC_USDT_USDT-1d-futures.feather'
OUT_FILE = f'{BASE}/scripts/v8_phase_labels.parquet'
SEQ_LEN  = 48

# ── 1. 加载日线数据，运行 SC + Phase 检测 ──────────────────────────────────────
print('加载日线数据...')
df_1d = pd.read_feather(DATA_1D)
df_1d['date'] = pd.to_datetime(df_1d['date'], utc=True)
df_1d = df_1d.sort_values('date').reset_index(drop=True)
print(f'日线: {len(df_1d)} 行  {df_1d["date"].iloc[0].date()} ~ {df_1d["date"].iloc[-1].date()}')

print('运行 SC 检测...')
df_1d = add_v8_sc_detection(df_1d)
sc_count = df_1d['v8_sc_final'].sum()
print(f'SC 确认: {sc_count} 次')

print('运行 Phase 检测...')
df_1d = add_v8_phase_detection(df_1d)
print('Phase 分布（日线）:')
print(df_1d['v8_phase'].value_counts().to_string())

# ── 2. 合并到 1H（日线 date+1d → 次日 1H bars 对齐，避免 lookahead）──────────
print('\n合并到 1H（日线 +1d lookahead 修复）...')
df_1h = pd.read_feather(DATA_1H)
df_1h['date'] = pd.to_datetime(df_1h['date'], utc=True)
df_1h = df_1h.sort_values('date').reset_index(drop=True)

# 日线 date 对应当天收盘后才可知，merge 时 +1d 避免 lookahead
df_1d_merge = df_1d[['date', 'v8_phase', 'v8_phase_allow_long',
                       'v8_tr_high', 'v8_tr_low']].copy()
df_1d_merge['date'] = df_1d_merge['date'] + pd.Timedelta(days=1)

df = df_1h.merge(df_1d_merge, on='date', how='left')
# 前向填充（日线 phase 在当天所有 1H bar 生效）
for col in ['v8_phase', 'v8_phase_allow_long', 'v8_tr_high', 'v8_tr_low']:
    df[col] = df[col].ffill()
df['v8_phase'] = df['v8_phase'].fillna('unknown')

df = df.sort_values('date').reset_index(drop=True)
print(f'1H 合并后: {len(df)} 行')
print('1H Phase 分布:')
print(df['v8_phase'].value_counts().to_string())

# ── 3. 简化标签：markup / markdown / trading_range / unknown ───────────────────
# V8 phase: A/B/C/D/E → trading_range; unknown → 排除; 其余（markup/markdown 结构外）
# 这里定义：A/B/C/D/E = trading_range，需要区分 markup/markdown 则看 V8 是否有该概念
# V8 没有显式 markup/markdown 标签，只有 phase A-E 和 unknown
# 因此重新设计标签：
#   trading_range = Phase B/C/D/E（TR 内部）
#   accumulation_edge = Phase D/E（入场窗口）
#   unknown = 排除
# 对比 TV 标签的三分类，这里用 V8 原生两分类：in_tr vs out_of_tr
# 但为了与 §3c 可比，将 A 归类为 early_tr，D/E 为 late_tr，B/C 为 mid_tr
# 最终三分类（对应实用价值）：
#   phase_de  = Phase D or E（入场窗口，最重要）
#   phase_abc = Phase A/B/C（结构形成中）
#   no_phase  = unknown（无结构）

def map_phase(p):
    if p in ('D', 'E'):
        return 'phase_de'
    elif p in ('A', 'B', 'C'):
        return 'phase_abc'
    else:
        return 'no_phase'

df['v8_phase_label'] = df['v8_phase'].map(map_phase)
print('\n简化标签分布（1H）:')
print(df['v8_phase_label'].value_counts().to_string())

# 保存
df[['date', 'open', 'high', 'low', 'close', 'volume',
    'v8_phase', 'v8_phase_label', 'v8_phase_allow_long',
    'v8_tr_high', 'v8_tr_low']].to_parquet(OUT_FILE)
print(f'\n已保存: {OUT_FILE}')

# ── 4. 构建特征矩阵（与 §3c 相同，但不含 RSI——消除循环）────────────────────────
print('\n构建特征矩阵（48-bar OHLCV，不含 RSI）...')

# 排除 no_phase（unknown）样本
df_labeled = df[df['v8_phase_label'] != 'no_phase'].copy().reset_index(drop=True)
print(f'有效样本: {len(df_labeled)} 行')

close_arr = df_labeled['close'].values
open_arr  = df_labeled['open'].values
high_arr  = df_labeled['high'].values
low_arr   = df_labeled['low'].values
vol_arr   = df_labeled['volume'].values

X_list, y_list, date_list = [], [], []

for i in range(SEQ_LEN, len(df_labeled)):
    ref = close_arr[i]
    if ref == 0:
        continue

    win_c = close_arr[i - SEQ_LEN: i]
    win_o = open_arr[i - SEQ_LEN: i]
    win_h = high_arr[i - SEQ_LEN: i]
    win_l = low_arr[i - SEQ_LEN: i]
    win_v = vol_arr[i - SEQ_LEN: i]
    vol_mean = win_v.mean() + 1e-9

    # 5维/bar × 48 = 240 维（无 RSI，消除循环）
    feat = np.empty(SEQ_LEN * 5)
    for j in range(SEQ_LEN):
        base = j * 5
        feat[base]     = (win_o[j] - ref) / ref
        feat[base + 1] = (win_h[j] - ref) / ref
        feat[base + 2] = (win_l[j] - ref) / ref
        feat[base + 3] = (win_c[j] - ref) / ref
        feat[base + 4] = win_v[j] / vol_mean

    X_list.append(feat)
    y_list.append(df_labeled['v8_phase_label'].iloc[i])
    date_list.append(df_labeled['date'].iloc[i])

X = np.array(X_list)
y = np.array(y_list)
dates = np.array(date_list)

valid = ~np.isnan(X).any(axis=1)
X, y, dates = X[valid], y[valid], dates[valid]

print(f'特征矩阵: {X.shape}  (NaN 过滤后)')
for cls in ['phase_de', 'phase_abc', 'no_phase']:
    n = (y == cls).sum()
    if n > 0:
        print(f'  {cls:<12}: {n:>6} ({n/len(y)*100:.1f}%)')

# ── 5. Walk-forward 切分 + 训练 ────────────────────────────────────────────────
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler, LabelEncoder
from sklearn.metrics import classification_report, f1_score

split = int(len(y) * 0.70)
X_tr, X_te = X[:split], X[split:]
y_tr, y_te = y[:split], y[split:]
dates_te = dates[split:]
print(f'\n训练集: {len(y_tr)}  测试集: {len(y_te)}')
print(f'测试集: {dates_te[0]} ~ {dates_te[-1]}')

le = LabelEncoder()
le.fit(y)

# ── Logistic Regression ─────────────────────────────────────────────────────
print('\n' + '='*60)
print('模型 1: Logistic Regression（无 RSI 特征）')
scaler = StandardScaler()
X_tr_s = scaler.fit_transform(X_tr)
X_te_s = scaler.transform(X_te)

lr = LogisticRegression(max_iter=500, class_weight='balanced', C=0.1)
lr.fit(X_tr_s, y_tr)
lr_pred = lr.predict(X_te_s)
print(classification_report(y_te, lr_pred, target_names=le.classes_, digits=3))
lr_f1 = f1_score(y_te, lr_pred, average='macro')
lr_tr_f1 = f1_score(y_tr, lr.predict(X_tr_s), average='macro')
print(f'Train macro-F1: {lr_tr_f1:.4f}  Test macro-F1: {lr_f1:.4f}')

# ── Hist Gradient Boosting（支持 class_weight='balanced'）──────────────────────
print('\n' + '='*60)
print('模型 2: HistGradientBoosting（class_weight=balanced，无 RSI 特征）')
gb = HistGradientBoostingClassifier(
    max_iter=100, max_depth=4, learning_rate=0.05,
    min_samples_leaf=10, class_weight='balanced', random_state=42
)
gb.fit(X_tr, y_tr)
gb_pred = gb.predict(X_te)
print(classification_report(y_te, gb_pred, target_names=sorted(set(y)), digits=3))
gb_f1 = f1_score(y_te, gb_pred, average='macro')
gb_tr_f1 = f1_score(y_tr, gb.predict(X_tr), average='macro')
print(f'Train macro-F1: {gb_tr_f1:.4f}  Test macro-F1: {gb_f1:.4f}')

# ── §3c 核心问题：phase_de（D/E）识别能力 ────────────────────────────────────
print('\n' + '='*60)
print('§3c v2 核心：phase_de（入场窗口）识别能力')
from sklearn.metrics import precision_score, recall_score

for model_name, pred in [('GB', gb_pred), ('LR', lr_pred)]:
    de_true = (y_te == 'phase_de').astype(int)
    de_pred = (pred == 'phase_de').astype(int)
    p = precision_score(de_true, de_pred)
    r = recall_score(de_true, de_pred)
    print(f'{model_name}  phase_de  Precision={p:.3f}  Recall={r:.3f}')

# ── 结论 ────────────────────────────────────────────────────────────────────
print('\n' + '='*60)
print('§3c v2 实验结论（V8 原生 phase_tag，无 RSI 特征）')
print('='*60)
best_f1 = max(lr_f1, gb_f1)
best_model = 'GB' if gb_f1 >= lr_f1 else 'LR'
print(f'最佳模型: {best_model}  macro-F1: {best_f1:.4f}')

if best_f1 >= 0.70:
    print('✅ Phase 可学（V8 原生标签）：纯 OHLCV 序列含结构信息，RSI 循环非必要条件')
elif best_f1 >= 0.55:
    print('⚠️ 弱信号：OHLCV 序列对 V8 phase_tag 有微弱预测力，不足以实用')
else:
    print('❌ Phase 不可学（V8 原生标签）：纯 OHLCV 无法预测 V8 量价结构 Phase')

# 过拟合检查
if gb_tr_f1 - gb_f1 > 0.20:
    print(f'⚠️ 过拟合: Train-Test 差 {gb_tr_f1-gb_f1:.3f}')

print('\n对比 §3c v1（TV RSI 代理标签）:')
print('  LR macro-F1: 0.8220  GB macro-F1: 0.9873')
print(f'  LR macro-F1: {lr_f1:.4f}  GB macro-F1: {gb_f1:.4f}  (v2，无 RSI)')
