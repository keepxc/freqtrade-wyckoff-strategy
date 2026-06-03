"""
BE 宽松A 信号质量诊断

目标：对比 严格5日单调 vs 宽松4/5日递减 的增量信号，
      分析新增信号是否在结构性高位/早期下跌（有效），还是牛市噪音（无效）。

核心指标：
  1. 新增信号的EMA200位置分布（上方 vs 下方）
  2. 新增信号的DCS环境（True=分发期 vs False=再积累期）
  3. 新增信号距离近期cycle_high的距离（%）
  4. 新增信号后续价格走势（触发后N天平均跌幅）

运行：
  docker compose run --rm --entrypoint "" freqtrade \
    sh -c "python user_data/scripts/audit_be_loose_quality.py"
"""

import sys
sys.path.insert(0, '/freqtrade')

import pandas as pd
import numpy as np
from wyckoff.daily_event_detector import DailyEventDetector

# ─── 1. 加载日线数据 ──────────────────────────────────────────────────────────
df = pd.read_feather('user_data/data/binance/futures/BTC_USDT_USDT-1d-futures.feather')
df['date'] = pd.to_datetime(df['date'], utc=True)
df = df[df['date'] >= '2021-01-01'].sort_values('date').reset_index(drop=True)

# ─── 2. 运行 DET 获取 DCS、EMA200 等基础信号 ─────────────────────────────────
det = DailyEventDetector()
df = det.detect(df.copy())

# EMA200
df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
df['above_ema200'] = df['close'] > df['ema200']

# DCS（shift 1 = 当天可用的昨日信号）
df['dcs_avail'] = df['dist_cause_sufficient'].shift(1).fillna(False)

# ─── 3. 两种版本的条件② ──────────────────────────────────────────────────────

# 版本A：严格5日单调递减（当前设置）
vol_strict = (
    (df['volume'] < df['volume'].shift(1)) &
    (df['volume'].shift(1) < df['volume'].shift(2)) &
    (df['volume'].shift(2) < df['volume'].shift(3)) &
    (df['volume'].shift(3) < df['volume'].shift(4)) &
    (df['volume'].shift(4) < df['volume'].shift(5))
)

# 版本B：宽松4/5日递减（5日中至少4日递减）
def loose_vol_decline(df, n=5, k=4):
    """n日中至少k日量能递减"""
    declines = pd.DataFrame({
        f'd{i}': (df['volume'].shift(i-1) < df['volume'].shift(i)).astype(int)
        for i in range(1, n+1)
    })
    return declines.sum(axis=1) >= k

vol_loose = loose_vol_decline(df, n=5, k=4)

# ─── 4. 条件①③ ───────────────────────────────────────────────────────────────
# 条件①：10日无新高
cond1 = df['high'].rolling(10).max().shift(1) >= df['high']
# 条件③：3日收于EMA200下方
cond3 = (
    (df['close'] < df['ema200']) &
    (df['close'].shift(1) < df['ema200'].shift(1)) &
    (df['close'].shift(2) < df['ema200'].shift(2))
)

# 完整BE触发（日线层，未shift）
be_strict = cond1 & vol_strict & cond3
be_loose  = cond1 & vol_loose  & cond3

# ─── 5. 找出新增信号日期 ──────────────────────────────────────────────────────
# 只看触发首日（上升沿）
be_strict_start = be_strict & (~be_strict.shift(1).fillna(False))
be_loose_start  = be_loose  & (~be_loose.shift(1).fillna(False))

# 新增 = 宽松触发 但 严格未触发
be_added_start = be_loose_start & (~be_strict_start)
be_common_start = be_strict_start & be_loose_start  # 共同触发

df_strict = df[be_strict_start].copy()
df_added  = df[be_added_start].copy()
df_common = df[be_common_start].copy()

# ─── 6. 统计函数 ──────────────────────────────────────────────────────────────
def analyze_signals(sub, label, df_full):
    if len(sub) == 0:
        print(f"\n{label}: 0 次触发")
        return

    print(f"\n{'─'*65}")
    print(f"{label}  (n={len(sub)}次)")

    # EMA200 位置
    above = sub['above_ema200'].sum()
    below = len(sub) - above
    print(f"  EMA200 上方触发 : {above:3d}次 ({above/len(sub)*100:.1f}%)  ← 牛市噪音风险")
    print(f"  EMA200 下方触发 : {below:3d}次 ({below/len(sub)*100:.1f}%)  ← 结构性信号")

    # DCS 环境
    dcs_true  = sub['dcs_avail'].sum()
    dcs_false = len(sub) - dcs_true
    print(f"  DCS=True 触发   : {dcs_true:3d}次 ({dcs_true/len(sub)*100:.1f}%)  ← 分发期（理想）")
    print(f"  DCS=False 触发  : {dcs_false:3d}次 ({dcs_false/len(sub)*100:.1f}%)  ← 再积累期（噪音）")

    # 距离近期高点的距离（lookback 60天）
    sub = sub.copy()
    sub['recent_high'] = sub['close'].transform(
        lambda x: x  # placeholder
    )
    # 用全局df计算
    idx_list = sub.index.tolist()
    dist_pcts = []
    for idx in idx_list:
        window_start = max(0, idx - 60)
        recent_high = df_full.loc[window_start:idx, 'high'].max()
        dist_pct = (recent_high - df_full.loc[idx, 'close']) / recent_high * 100
        dist_pcts.append(dist_pct)
    sub = sub.copy()
    sub['dist_from_high_pct'] = dist_pcts

    print(f"  距近期高点距离  : 中位={np.median(dist_pcts):.1f}%  均值={np.mean(dist_pcts):.1f}%  "
          f"最小={np.min(dist_pcts):.1f}%  最大={np.max(dist_pcts):.1f}%")
    print(f"  距高点>10%      : {sum(d>10 for d in dist_pcts):3d}次  ← 已进入显著下跌（理想）")
    print(f"  距高点<5%       : {sum(d<5 for d in dist_pcts):3d}次  ← 可能是高位整理误报")

    # 触发后30天价格表现
    fwd_rets = []
    for idx in idx_list:
        fwd_idx = min(idx + 30, len(df_full) - 1)
        ret = (df_full.loc[fwd_idx, 'close'] - df_full.loc[idx, 'close']) / df_full.loc[idx, 'close'] * 100
        fwd_rets.append(ret)
    sub['fwd_30d'] = fwd_rets

    neg_count = sum(r < 0 for r in fwd_rets)
    print(f"  触发后30天收益  : 中位={np.median(fwd_rets):+.1f}%  均值={np.mean(fwd_rets):+.1f}%")
    print(f"  触发后30天下跌  : {neg_count:3d}次 ({neg_count/len(fwd_rets)*100:.1f}%)  ← 预测下跌准确率")

    # 逐条打印
    print(f"\n  {'日期':12s} {'收盘价':>9} {'EMA200':>9} {'位置':>6} {'DCS':>5} {'距高%':>7} {'30天%':>7}")
    for i, (idx, row) in enumerate(sub.sort_values('date').iterrows()):
        pos = '下方' if not row['above_ema200'] else '上方⚠'
        dcs = 'True' if row['dcs_avail'] else 'False'
        print(f"  {str(row['date'].date()):12s} {row['close']:>9,.0f} {row['ema200']:>9,.0f} "
              f"{pos:>6} {dcs:>5} {dist_pcts[list(idx_list).index(idx)]:>6.1f}% {fwd_rets[list(idx_list).index(idx)]:>+6.1f}%")

# ─── 7. 输出结果 ──────────────────────────────────────────────────────────────
print("=" * 65)
print("BE 条件② 信号质量诊断：严格版 vs 宽松A版（4/5日递减）")
print("=" * 65)
print(f"\n全区间 2021-2026 总触发次数（上升沿）：")
print(f"  严格版（5/5）: {be_strict_start.sum():3d} 次")
print(f"  宽松A（4/5）: {be_loose_start.sum():3d} 次  (+{be_added_start.sum()}次 新增)")
print(f"  两版共同触发: {be_common_start.sum():3d} 次")

analyze_signals(df_strict, "【严格版 专属信号（基准）】", df)
analyze_signals(df_added,  "【宽松A 新增信号（待审核）】", df)
analyze_signals(df_common, "【两版共同触发（重叠）】", df)

# ─── 8. 年度分布 ──────────────────────────────────────────────────────────────
print(f"\n{'─'*65}")
print("年度触发分布：")
print(f"  {'年份':6} {'严格版':>8} {'宽松A新增':>10} {'EMA200上方新增':>15} {'DCS=False新增':>14}")
for yr in range(2021, 2027):
    mask_yr = df['date'].dt.year == yr
    s_ct = be_strict_start[mask_yr].sum()
    l_ct = be_added_start[mask_yr].sum()
    if l_ct > 0:
        new_above = (be_added_start & mask_yr & df['above_ema200']).sum()
        new_dcs_false = (be_added_start & mask_yr & ~df['dcs_avail']).sum()
    else:
        new_above = new_dcs_false = 0
    print(f"  {yr}   {s_ct:>8} {l_ct:>10} {new_above:>15} {new_dcs_false:>14}")

# ─── 9. 裁决建议 ──────────────────────────────────────────────────────────────
added_above = (be_added_start & df['above_ema200']).sum()
added_total = be_added_start.sum()
added_dcs_false = (be_added_start & ~df['dcs_avail']).sum()

print(f"\n{'='*65}")
print("裁决依据：")
if added_total > 0:
    noise_rate = (added_above + added_dcs_false) / added_total / 2 * 100
    print(f"  新增信号 EMA200上方比例 : {added_above}/{added_total} = {added_above/added_total*100:.1f}%")
    print(f"  新增信号 DCS=False比例  : {added_dcs_false}/{added_total} = {added_dcs_false/added_total*100:.1f}%")

    if added_above / added_total > 0.5:
        print(f"\n  ✗ 超过50%新增信号在EMA200上方 → 高噪音风险，建议附加条件过滤")
    elif added_above / added_total > 0.3:
        print(f"\n  △ 约{added_above/added_total*100:.0f}%新增信号在EMA200上方 → 中等噪音，需交叉条件")
    else:
        print(f"\n  ✓ 仅{added_above/added_total*100:.0f}%新增信号在EMA200上方 → 信号质量较好，可考虑立项")
print("=" * 65)
