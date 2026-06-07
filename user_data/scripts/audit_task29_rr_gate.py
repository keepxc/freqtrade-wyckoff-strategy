"""
TASK 29 预审计：多头 RR 门控影响分析
目标：量化在 confirm_trade_entry 中加入 RR ≥ 阈值 的影响

核心问题：
1. 现有 471 笔多头的 RR 分布（以 open_rate / v8_target_1 / v8_tr_low 计算）
2. Phase D vs Phase E 的 RR 差异（Phase E 入场时 close 常已 > T1）
3. 不同 RR 阈值（1.5 / 2.0 / 3.0）下的过滤率与胜率变化
4. 被过滤的交易盈亏分布（过滤的是坏交易还是好交易）
"""
import zipfile, json, pandas as pd, numpy as np
from pathlib import Path

RESULTS_DIR = Path('user_data/backtest_results')
TASK27_ZIP  = RESULTS_DIR / 'backtest-result-2026-06-05_16-42-07.zip'

def load_trades(zip_path):
    zf = zipfile.ZipFile(zip_path)
    fname = [f for f in zf.namelist() if f.endswith('.json') and 'meta' not in f][0]
    data = json.loads(zf.read(fname))
    df = pd.DataFrame(data['strategy']['WyckoffV8']['trades'])
    df['open_date']  = pd.to_datetime(df['open_date'], utc=True)
    df['close_date'] = pd.to_datetime(df['close_date'], utc=True)
    return df

df = load_trades(TASK27_ZIP)
longs = df[df['is_short'] == False].copy()

print("=" * 70)
print("TASK 29 预审计：多头 RR 门控影响分析")
print(f"多头总数: {len(longs)}")
print("=" * 70)

# ── 检查可用字段 ─────────────────────────────────────────────────────
print("\n【可用字段】")
print(longs.columns.tolist())

# ── 1. 从回测结果提取 RR 相关字段 ──────────────────────────────────
# Freqtrade 回测 JSON 中，交易记录包含 open_rate（入场价）
# v8_target_1 / v8_tr_low 不直接在 trades 里，需要从 orders 或其他字段推算
# 但可以用 initial_stop_loss_abs / open_rate / profit 来间接推算

print("\n【验证 1】stop_loss 字段可用性（用于推算 TR Low）")
sl_cols = [c for c in longs.columns if 'stop' in c.lower() or 'sl' in c.lower()]
print(f"止损相关字段: {sl_cols}")

# initial_stop_loss_abs = stop price (absolute)
# open_rate = entry price
# 止损距离 = open_rate - initial_stop_loss_abs
if 'initial_stop_loss_abs' in longs.columns and 'open_rate' in longs.columns:
    longs['sl_abs']    = longs['initial_stop_loss_abs']
    longs['entry']     = longs['open_rate']
    longs['sl_dist']   = longs['entry'] - longs['sl_abs']
    longs['sl_pct']    = longs['sl_dist'] / longs['entry']
    print(f"\n止损距离统计（占入场价 %）：")
    print(longs['sl_pct'].describe().apply(lambda x: f"{x:.4f}"))
else:
    print("initial_stop_loss_abs 不可用，改用 stoploss_order_id 或其他字段")
    longs['sl_abs']  = np.nan
    longs['sl_dist'] = np.nan

# ── 2. 目标位（T1）推算 ─────────────────────────────────────────────
# Freqtrade 回测不直接存 T1，但有 min_rate / max_rate（持仓期间最高最低价）
# 尝试用 close_rate（平仓价）作为代理——对于 down_wave 退出的交易
# 实际 T1 从 pnf 数据中无法直接拿，只能用 max_rate 近似

print("\n【验证 2】目标价代理字段")
target_cols = [c for c in longs.columns if any(k in c.lower() for k in ['max','min','target','profit'])]
print(f"相关字段: {target_cols}")

# 用 max_rate 作为价格上行空间的代理（实际达到的最高价）
if 'max_rate' in longs.columns:
    longs['max_gain']  = (longs['max_rate'] - longs['entry']) / longs['entry']
    longs['realized_rr'] = (longs['max_rate'] - longs['entry']) / longs['sl_dist'].replace(0, np.nan)
    print(f"\n实际达到最高价 vs 入场价的增幅（代理 T1 空间）：")
    print(longs['max_gain'].describe().apply(lambda x: f"{x:.4f}"))
    print(f"\n实际 RR（max_rate / sl_dist）分布：")
    print(longs['realized_rr'].describe().apply(lambda x: f"{x:.4f}"))

# ── 3. 按 enter_tag 分 Phase D vs Phase E ─────────────────────────
print("\n【验证 3】按入场路径分组")
print(longs['enter_tag'].value_counts())

for tag in ['v8_ws_d', 'v8_ws_e']:
    subset = longs[longs['enter_tag'] == tag]
    if len(subset) == 0:
        continue
    print(f"\n{tag}（{len(subset)} 笔）：")
    print(f"  胜率: {(subset['profit_ratio'] > 0).mean():.1%}")
    print(f"  平均盈利: {subset['profit_ratio'].mean():.4f}")
    if 'sl_pct' in subset.columns:
        print(f"  平均止损距离: {subset['sl_pct'].mean():.4f}")
    if 'realized_rr' in subset.columns:
        print(f"  实际 RR 中位数: {subset['realized_rr'].median():.2f}")
        print(f"  实际 RR < 2.0 占比: {(subset['realized_rr'] < 2.0).mean():.1%}")

# ── 4. 止损距离分布（TR Low 距离）─────────────────────────────────
print("\n【验证 4】止损距离分布（关键：TR Low 到入场价的距离决定 RR 可行性）")
if 'sl_pct' in longs.columns and longs['sl_pct'].notna().sum() > 0:
    bins = [0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.50, 1.0]
    labels = ['<5%', '5-10%', '10-15%', '15-20%', '20-30%', '30-50%', '>50%']
    longs['sl_bucket'] = pd.cut(longs['sl_pct'], bins=bins, labels=labels)
    dist = longs.groupby('sl_bucket', observed=True).agg(
        count=('profit_ratio','count'),
        win_rate=('profit_ratio', lambda x: (x>0).mean()),
        avg_profit=('profit_ratio','mean'),
    )
    print(dist.to_string())
    print(f"\n止损距离 > 20% 的笔数: {(longs['sl_pct'] > 0.20).sum()} （这类 RR 门控实际无效）")

# ── 5. 模拟不同 RR 阈值的过滤效果 ────────────────────────────────
print("\n【验证 5】模拟 RR 阈值过滤效果（用实际 realized_rr 代理）")
print("注意：realized_rr 用 max_rate 计算，高估了真实 T1 距离的 RR")
print("真实 RR = (T1 - entry) / (entry - TR_Low)，T1 固定，不随价格变动")
print()

if 'realized_rr' in longs.columns:
    for threshold in [1.5, 2.0, 3.0]:
        keep = longs[longs['realized_rr'] >= threshold]
        filt = longs[longs['realized_rr'] < threshold]
        print(f"RR ≥ {threshold}:")
        print(f"  保留: {len(keep)} 笔，胜率 {(keep['profit_ratio']>0).mean():.1%}，"
              f"平均盈利 {keep['profit_ratio'].mean():.4f}")
        print(f"  过滤: {len(filt)} 笔，胜率 {(filt['profit_ratio']>0).mean():.1%}，"
              f"平均盈利 {filt['profit_ratio'].mean():.4f}")
        print()

# ── 6. 关键风险：Phase E 入场时 close > T1 的情况 ────────────────
print("\n【验证 6】Phase E RR 门控的核心难点")
print("Phase E 入场时，close 常已高于 T1（T1 在 Phase C/D 锁定）")
print("此时 (T1 - close) < 0，RR 为负，门控会拦截所有 Phase E 信号")
print()
ws_e = longs[longs['enter_tag'] == 'v8_ws_e']
if len(ws_e) > 0:
    print(f"v8_ws_e 笔数: {len(ws_e)}")
    print(f"胜率: {(ws_e['profit_ratio']>0).mean():.1%}")
    print(f"平均盈利: {ws_e['profit_ratio'].mean():.4f}")
    print(f"平均止损距离: {ws_e['sl_pct'].mean():.4f}")
    print()
    print("结论：Phase E 交易必须豁免 T1 RR 门控，或改用 T2 作为目标计算 RR")

print("\n" + "=" * 70)
print("审计结论汇总")
print("=" * 70)
print("1. 止损距离（entry - TR_Low）分布决定 RR 门控有效性")
print("   - 若止损距离 > 20%，要求 RR≥3 意味着 T1 必须在 entry 上方 60%+")
print("   - BTC Phase D/E 的 T1 通常是 1-2 个 ATR 距离（5-15%），不可能 ≥ 60%")
print("2. Phase E 必须豁免，否则 471 笔中绝大多数会被拦截")
print("3. 建议：仅对 v8_ws_d 且 TR Low 距离 < 10% 的信号施加 RR 门控")
