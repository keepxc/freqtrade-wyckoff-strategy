"""
Behavioral Cloning 验证实验
===========================
问题：神经网络能否仅从原始 OHLCV 序列（无手工特征）预测 V8 的入场决策？

结论判断标准：
  AUC > 0.70 → 形态信息在 OHLCV 里，ML 路径有地基
  AUC ~ 0.50 → V8 edge 来自规则编码的领域知识，OHLCV 里学不到
"""
import zipfile, json
import numpy as np
import pandas as pd
from pathlib import Path
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score, classification_report
from sklearn.model_selection import StratifiedKFold

BT_ZIP    = 'user_data/backtest_results/backtest-result-2026-06-26_00-42-22.zip'
DATA_FILE = 'user_data/data/binance/futures/BTC_USDT_USDT-1h-futures.feather'
SEQ_LEN   = 48   # 用前 48 根 bar 作为特征

# ── 加载 V8 入场时间戳 ────────────────────────────────────────────────────────
def load_entries(zf_path):
    with zipfile.ZipFile(zf_path) as z:
        fname = [n for n in z.namelist()
                 if n.endswith('.json') and 'config' not in n and '.py' not in n][0]
        data = json.loads(z.read(fname))
    trades = list(data['strategy'].values())[0]['trades']
    entries = {}
    for t in trades:
        ts  = pd.to_datetime(t['open_date'], utc=True)
        tag = t.get('enter_tag', 'unknown')
        entries[ts] = tag
    return entries

entries = load_entries(BT_ZIP)
print(f'V8 入场总笔数: {len(entries)}')
print('入场路径分布:')
tags = pd.Series(list(entries.values()))
print(tags.value_counts().to_string())

# ── 加载 1H K线 ───────────────────────────────────────────────────────────────
ohlcv = pd.read_feather(DATA_FILE)
ohlcv['date'] = pd.to_datetime(ohlcv['date'], utc=True)
ohlcv = ohlcv.sort_values('date').reset_index(drop=True)
print(f'\n1H K线: {len(ohlcv)} 根  ({ohlcv["date"].iloc[0]} ~ {ohlcv["date"].iloc[-1]})')

# ── 构建特征矩阵 ──────────────────────────────────────────────────────────────
# 对每根 bar i，特征 = 前 SEQ_LEN 根 bar 的 OHLCV 归一化到 bar[i] 的 close
entry_set = set(entries.keys())

X_list, y_list, dates_list, tags_list = [], [], [], []

for i in range(SEQ_LEN, len(ohlcv)):
    bar_date = ohlcv['date'].iloc[i]
    label    = 1 if bar_date in entry_set else 0

    # 只处理正样本 + 随机采样负样本（5% 负采样率，减少类不平衡）
    if label == 0 and np.random.rand() > 0.05:
        continue

    # 特征：SEQ_LEN 根 bar 的 OHLCV，归一化
    window = ohlcv.iloc[i - SEQ_LEN: i]
    ref    = ohlcv['close'].iloc[i]  # 当前 bar close 作为参考基准

    feat = []
    for _, row in window.iterrows():
        feat.append((row['open']   - ref) / ref)
        feat.append((row['high']   - ref) / ref)
        feat.append((row['low']    - ref) / ref)
        feat.append((row['close']  - ref) / ref)
        feat.append(row['volume'] / (window['volume'].mean() + 1e-9))  # 量比

    X_list.append(feat)
    y_list.append(label)
    dates_list.append(bar_date)
    tags_list.append(entries.get(bar_date, 'none'))

X = np.array(X_list)
y = np.array(y_list)
dates = np.array(dates_list)

print(f'\n样本构建完成: {len(y)} 条  正样本={y.sum()}  负样本={(y==0).sum()}')
print(f'正样本率: {y.mean()*100:.2f}%')

# ── Walk-Forward AUC 验证 ─────────────────────────────────────────────────────
# 按时间排序，用前 70% 训练，后 30% 测试（防 lookahead）
sort_idx = np.argsort(dates)
X, y, dates = X[sort_idx], y[sort_idx], dates[sort_idx]

split = int(len(y) * 0.70)
X_train, X_test = X[:split], X[split:]
y_train, y_test = y[:split], y[split:]
dates_test = dates[split:]

print(f'\n训练集: {len(y_train)} 条 (正={y_train.sum()})')
print(f'测试集: {len(y_test)} 条 (正={y_test.sum()})')

# ── 模型1：Logistic Regression（线性基线）────────────────────────────────────
scaler = StandardScaler()
X_tr_s = scaler.fit_transform(X_train)
X_te_s = scaler.transform(X_test)

lr = LogisticRegression(max_iter=1000, class_weight='balanced', C=0.1)
lr.fit(X_tr_s, y_train)
lr_proba = lr.predict_proba(X_te_s)[:, 1]
lr_auc   = roc_auc_score(y_test, lr_proba)
print(f'\n[Logistic Regression] AUC = {lr_auc:.4f}')

# ── 模型2：Gradient Boosting（非线性）───────────────────────────────────────
gb = GradientBoostingClassifier(
    n_estimators=200,
    max_depth=4,
    learning_rate=0.05,
    subsample=0.8,
    min_samples_leaf=10,
    random_state=42
)
gb.fit(X_train, y_train)
gb_proba = gb.predict_proba(X_test)[:, 1]
gb_auc   = roc_auc_score(y_test, gb_proba)
print(f'[Gradient Boosting]   AUC = {gb_auc:.4f}')

# ── 结果解读 ──────────────────────────────────────────────────────────────────
print('\n' + '='*60)
print('实验结论')
print('='*60)
best_auc = max(lr_auc, gb_auc)
if best_auc >= 0.75:
    verdict = '✅ 形态信息存在于 OHLCV 序列中，ML 路径有地基'
elif best_auc >= 0.65:
    verdict = '⚠️  弱信号：部分形态可学，但 V8 edge 主要来自领域规则'
else:
    verdict = '❌ V8 edge 来自结构规则编码，OHLCV 序列无法复现，ML 路径证伪'

print(f'最佳 AUC: {best_auc:.4f}')
print(f'结论: {verdict}')

# ── 分路径分析 ────────────────────────────────────────────────────────────────
print('\n=== 各入场路径分析 ===')
test_tags = np.array([entries.get(d, 'none') for d in dates_test])
for tag in sorted(set(entries.values())):
    mask = (test_tags == tag)
    if mask.sum() < 5:
        continue
    # 该路径 vs 所有负样本的 AUC
    idx = np.where(mask | (y_test == 0))[0]
    if len(idx) < 10:
        continue
    sub_y = y_test[idx]
    sub_p = gb_proba[idx]
    if sub_y.sum() == 0:
        continue
    try:
        sub_auc = roc_auc_score(sub_y, sub_p)
        print(f'  {tag:<30} n={mask.sum():>3}  AUC={sub_auc:.3f}')
    except Exception:
        pass

# ── 高置信度预测分析 ──────────────────────────────────────────────────────────
print('\n=== 高置信度预测（score > 0.5）命中分析 ===')
high_conf = gb_proba > 0.5
print(f'高置信度预测数: {high_conf.sum()}')
print(f'其中真实入场:   {(y_test[high_conf] == 1).sum()} '
      f'({(y_test[high_conf] == 1).mean()*100:.1f}%)')
print(f'其中非入场:     {(y_test[high_conf] == 0).sum()}')
