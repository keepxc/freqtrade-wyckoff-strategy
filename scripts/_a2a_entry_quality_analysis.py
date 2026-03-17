"""
A-2a / A-2b：axis_support 入场质量分析
========================================
目标：
  A-2a：拆解 82 笔负利润 axis_support 样本，找共同弱特征
  A-2b：盈利 vs 亏损 axis_support 对照，找最可操作的区别

数据来源：
  - 回测 JSON（trades 列表）
  - BTC 1H OHLCV feather（计算入场时指标）

可计算的入场时指标：
  - close vs ema_50 / ema_200（价格位置）
  - close vs ema_50*0.98（贴近 ema_50 下方？）
  - volume vs volume_sma_20（入场量能）
  - 入场 K 线的收盘位置（close-low)/(high-low）
  - 入场前 3H 的价格动能（入场前 3 根 close 收益）
  - 入场 K 线的 K 线类型（多 / 空头 K）
  - ATR 归一化的入场波幅

用法：
    python scripts/_a2a_entry_quality_analysis.py
"""

import zipfile, json
from pathlib import Path
import pandas as pd
import numpy as np

# ── 配置 ──────────────────────────────────────────────────────────────────────
BASE     = Path(__file__).resolve().parent.parent
ZIP_PATH = BASE / "user_data/backtest_results/backtest-result-2026-03-17_08-04-46.zip"
OHLCV    = BASE / "user_data/data/binance/futures/BTC_USDT_USDT-1h-futures.feather"
EMA_FAST = 50
EMA_SLOW = 200
VOL_WIN  = 20
ATR_WIN  = 14
# ─────────────────────────────────────────────────────────────────────────────


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def atr(df: pd.DataFrame, period: int) -> pd.Series:
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df['close'].shift()).abs(),
        (df['low']  - df['close'].shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def load_trades(zip_path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(zip_path) as zf:
        json_files = [n for n in zf.namelist()
                      if n.endswith('.json') and 'config' not in n]
        with zf.open(json_files[0]) as f:
            data = json.load(f)
    strat  = list(data['strategy'].keys())[0]
    trades = pd.DataFrame(data['strategy'][strat]['trades'])
    trades['open_date']  = pd.to_datetime(trades['open_date'],  utc=True)
    trades['close_date'] = pd.to_datetime(trades['close_date'], utc=True)
    return trades


def build_indicators(ohlcv_path: Path) -> pd.DataFrame:
    df = pd.read_feather(ohlcv_path)
    df = df.sort_values('date').reset_index(drop=True)
    df['ema50']      = ema(df['close'], EMA_FAST)
    df['ema200']     = ema(df['close'], EMA_SLOW)
    df['vol_sma20']  = df['volume'].rolling(VOL_WIN).mean()
    df['atr14']      = atr(df, ATR_WIN)
    df['close_pos']  = (df['close'] - df['low']) / (df['high'] - df['low'] + 1e-9)  # 实体位置 0-1
    df['is_bull_candle'] = (df['close'] > df['open']).astype(int)
    # 入场前 3H 动能：过去 3 根 close 相对 3 根前 close 的涨跌
    df['momentum3h'] = df['close'].pct_change(3)
    df = df.set_index('date').sort_index()
    return df


def get_entry_indicators(open_date: pd.Timestamp, ind: pd.DataFrame) -> dict:
    """在所给时间戳查找 OHLCV+指标行（向后取最近的已完成 K 线）"""
    # 回测 open_date 是 K 线开盘时间 → 对应当根 K 线
    try:
        row = ind.iloc[ind.index.searchsorted(open_date)]
        return {
            'close'        : row['close'],
            'ema50'        : row['ema50'],
            'ema200'       : row['ema200'],
            'above_ema50'  : int(row['close'] > row['ema50']),
            'above_ema200' : int(row['close'] > row['ema200']),
            'ema50_dist_pct': (row['close'] - row['ema50']) / row['ema50'] * 100,
            'ema200_dist_pct': (row['close'] - row['ema200']) / row['ema200'] * 100,
            'vol_ratio'    : row['volume'] / row['vol_sma20'] if row['vol_sma20'] > 0 else np.nan,
            'close_pos'    : row['close_pos'],
            'is_bull'      : row['is_bull_candle'],
            'momentum3h'   : row['momentum3h'] * 100,  # %
            'atr_pct'      : row['atr14'] / row['close'] * 100,
        }
    except Exception:
        return {}


def fmt_pct(val: float) -> str:
    return f'{val:+.2f}%' if not np.isnan(val) else 'N/A'


def compare_groups(losing: pd.DataFrame, winning: pd.DataFrame, col: str,
                   name: str, fmt: str = '.2f') -> None:
    lv = losing[col].dropna()
    wv = winning[col].dropna()
    diff = wv.mean() - lv.mean()
    print(f"  {name:<35} 亏损均值 {lv.mean():{fmt}}  赢利均值 {wv.mean():{fmt}}  差 {diff:+.4f}")


def main():
    print(f"\n{'='*65}")
    print("  A-2a/A-2b：axis_support 入场质量对照分析")
    print(f"{'='*65}\n")

    trades = load_trades(ZIP_PATH)
    ind    = build_indicators(OHLCV)

    ax = trades[trades['enter_tag'] == 'wyckoff_axis_support'].copy()
    ax_dw = ax[ax['exit_reason'] == 'wyckoff_down_wave'].copy()

    # 负利润（亏损）vs 正利润（盈利）分组
    losing  = ax_dw[ax_dw['profit_ratio'] < 0].copy()   # 82 笔目标样本
    winning = ax_dw[ax_dw['profit_ratio'] > 0].copy()   # 162 笔对照

    print(f"axis_support × down_wave 共 {len(ax_dw)} 笔")
    print(f"  亏损退出（profit < 0）：{len(losing):>4} 笔  均利润 {losing['profit_ratio'].mean()*100:+.2f}%")
    print(f"  盈利退出（profit > 0）：{len(winning):>4} 笔  均利润 {winning['profit_ratio'].mean()*100:+.2f}%")
    print()

    # ── 附加入场指标 ──────────────────────────────────────────────────────────
    print("计算入场时指标...", end='', flush=True)
    for df_sub in [losing, winning]:
        rows = df_sub.apply(
            lambda r: pd.Series(get_entry_indicators(r['open_date'], ind)), axis=1
        )
        for col in rows.columns:
            df_sub[col] = rows[col].values
    print(" 完成\n")

    # ── 1. 核心指标对照表 ─────────────────────────────────────────────────────
    print("【1】入场时核心指标均值对照（亏损 vs 盈利）")
    compare_groups(losing, winning, 'ema50_dist_pct',  'close 距 ema50 百分比',  '+.2f')
    compare_groups(losing, winning, 'ema200_dist_pct', 'close 距 ema200 百分比', '+.2f')
    compare_groups(losing, winning, 'vol_ratio',       '成交量 / vol_sma20',     '.3f')
    compare_groups(losing, winning, 'close_pos',       'K线收盘位置 (0=下边 1=上边)', '.3f')
    compare_groups(losing, winning, 'momentum3h',      '入场前 3H 动能 %',       '+.3f')
    compare_groups(losing, winning, 'atr_pct',         'ATR/close %',            '.3f')
    print()

    # ── 2. 二值指标：站上 ema50 / ema200 比例 ─────────────────────────────────
    print("【2】站上 EMA 比例")
    for col, name in [('above_ema50', '站上 ema50'), ('above_ema200', '站上 ema200')]:
        lr = losing[col].mean()  * 100
        wr = winning[col].mean() * 100
        print(f"  {name:<20} 亏损：{lr:.1f}%  盈利：{wr:.1f}%  差：{wr-lr:+.1f}%")
    print()

    # ── 3. ema50 距离分桶：看哪段距离坏单最集中 ──────────────────────────────
    print("【3】close 距 ema50 分桶（亏损 vs 盈利）")
    bins  = [-999, -5, -2, -0.5, 0.5, 2, 5, 999]
    lbls  = ['<-5%', '-5~-2%', '-2~-0.5%', '±0.5%', '+0.5~2%', '+2~5%', '>+5%']
    l_cut = pd.cut(losing['ema50_dist_pct'],  bins=bins, labels=lbls)
    w_cut = pd.cut(winning['ema50_dist_pct'], bins=bins, labels=lbls)
    l_cnt = l_cut.value_counts().sort_index()
    w_cnt = w_cut.value_counts().sort_index()
    print(f"  {'区间':>10}  {'亏损':>6}  {'盈利':>6}  坏单比例")
    for lbl in lbls:
        lc = l_cnt.get(lbl, 0)
        wc = w_cnt.get(lbl, 0)
        total = lc + wc
        bad_ratio = lc / total * 100 if total > 0 else 0
        bar = '▓' * int(bad_ratio / 5)
        print(f"  {lbl:>10}  {lc:>6}  {wc:>6}  {bad_ratio:>5.1f}%  {bar}")
    print()

    # ── 4. 成交量比例分桶 ─────────────────────────────────────────────────────
    print("【4】成交量比例分桶（亏损 vs 盈利）")
    vbins = [0, 0.5, 0.8, 1.0, 1.5, 2.0, 999]
    vlbls = ['<0.5x', '0.5-0.8x', '0.8-1.0x', '1.0-1.5x', '1.5-2.0x', '>2.0x']
    lv_cut = pd.cut(losing['vol_ratio'],  bins=vbins, labels=vlbls)
    wv_cut = pd.cut(winning['vol_ratio'], bins=vbins, labels=vlbls)
    lv_cnt = lv_cut.value_counts().sort_index()
    wv_cnt = wv_cut.value_counts().sort_index()
    print(f"  {'区间':>10}  {'亏损':>6}  {'盈利':>6}  坏单比例")
    for lbl in vlbls:
        lc = lv_cnt.get(lbl, 0)
        wc = wv_cnt.get(lbl, 0)
        total = lc + wc
        bad_ratio = lc / total * 100 if total > 0 else 0
        bar = '▓' * int(bad_ratio / 5)
        print(f"  {lbl:>10}  {lc:>6}  {wc:>6}  {bad_ratio:>5.1f}%  {bar}")
    print()

    # ── 5. 入场前 3H 动能分桶 ─────────────────────────────────────────────────
    print("【5】入场前 3H 动能分桶（亏损 vs 盈利）")
    mbins = [-999, -3, -1, 0, 1, 3, 999]
    mlbls = ['<-3%', '-3~-1%', '-1~0%', '0~+1%', '+1~+3%', '>+3%']
    lm_cut = pd.cut(losing['momentum3h'],  bins=mbins, labels=mlbls)
    wm_cut = pd.cut(winning['momentum3h'], bins=mbins, labels=mlbls)
    lm_cnt = lm_cut.value_counts().sort_index()
    wm_cnt = wm_cut.value_counts().sort_index()
    print(f"  {'区间':>10}  {'亏损':>6}  {'盈利':>6}  坏单比例")
    for lbl in mlbls:
        lc  = lm_cnt.get(lbl, 0)
        wc  = wm_cnt.get(lbl, 0)
        total = lc + wc
        bad_ratio = lc / total * 100 if total > 0 else 0
        bar = '▓' * int(bad_ratio / 5)
        print(f"  {lbl:>10}  {lc:>6}  {wc:>6}  {bad_ratio:>5.1f}%  {bar}")
    print()

    # ── 6. K线收盘位置分桶 ────────────────────────────────────────────────────
    print("【6】入场 K 线收盘位置分桶（亏损 vs 盈利）")
    pbins = [0, 0.2, 0.4, 0.6, 0.8, 1.0]
    plbls = ['底部 0-20%', '20-40%', '40-60%', '60-80%', '顶部 80-100%']
    lp_cut = pd.cut(losing['close_pos'],  bins=pbins, labels=plbls)
    wp_cut = pd.cut(winning['close_pos'], bins=pbins, labels=plbls)
    lp_cnt = lp_cut.value_counts().sort_index()
    wp_cnt = wp_cut.value_counts().sort_index()
    print(f"  {'区间':>14}  {'亏损':>6}  {'盈利':>6}  坏单比例")
    for lbl in plbls:
        lc = lp_cnt.get(lbl, 0)
        wc = wp_cnt.get(lbl, 0)
        total = lc + wc
        bad_ratio = lc / total * 100 if total > 0 else 0
        bar = '▓' * int(bad_ratio / 5)
        print(f"  {lbl:>14}  {lc:>6}  {wc:>6}  {bad_ratio:>5.1f}%  {bar}")
    print()

    # ── 7. 组合条件：ema50 下方 + 熊烛 ───────────────────────────────────────
    print("【7】组合弱特征分析")
    conds = [
        ('below_ema50',          losing['above_ema50'] == 0,          winning['above_ema50'] == 0),
        ('below_ema200',         losing['above_ema200'] == 0,         winning['above_ema200'] == 0),
        ('bear_candle',          losing['is_bull'] == 0,              winning['is_bull'] == 0),
        ('below_ema50+bear',     (losing['above_ema50']==0)&(losing['is_bull']==0),
                                 (winning['above_ema50']==0)&(winning['is_bull']==0)),
        ('below_ema50+neg_mom',  (losing['above_ema50']==0)&(losing['momentum3h']<0),
                                 (winning['above_ema50']==0)&(winning['momentum3h']<0)),
        ('low_volume(<0.8x)',    losing['vol_ratio']<0.8,             winning['vol_ratio']<0.8),
    ]
    print(f"  {'条件':>25}  {'亏损中占比':>10}  {'盈利中占比':>10}  区分度")
    for name, l_mask, w_mask in conds:
        lr = l_mask.mean() * 100
        wr = w_mask.mean() * 100
        diff = lr - wr
        stars = '★' * int(abs(diff) / 5)
        print(f"  {name:>25}  {lr:>9.1f}%  {wr:>9.1f}%  {diff:+.1f}%  {stars}")
    print()

    # ── 8. 最强候选过滤条件评估 ───────────────────────────────────────────────
    print("【8】最强候选过滤条件评估（过滤掉该条件时的效果估算）")
    print()
    total_ax_trades = len(ax)
    total_ax_profit = ax['profit_ratio'].sum()

    filter_cands = []  # placeholder, replaced below

    # 重新给全量 axis_support 附加指标
    print("  计算全量 axis_support 入场指标...", end='', flush=True)
    ax_rows = ax.apply(
        lambda r: pd.Series(get_entry_indicators(r['open_date'], ind)), axis=1
    )
    for col in ax_rows.columns:
        ax[col] = ax_rows[col].values
    print(" 完成")
    print()

    candidates = [
        ('close < ema50',           ax['above_ema50'] == 0),
        ('close < ema200',          ax['above_ema200'] == 0),
        ('close < ema50 AND bear K', (ax['above_ema50']==0) & (ax['is_bull']==0)),
        ('close < ema50 AND neg mom',(ax['above_ema50']==0) & (ax['momentum3h']<0)),
        ('volume < 0.8x sma20',     ax['vol_ratio'] < 0.8),
    ]

    print(f"  {'条件':>30}  {'过滤笔数':>8}  {'坏单%':>8}  {'保留净利估算':>14}")
    for name, mask in candidates:
        filtered   = ax[mask]
        kept       = ax[~mask]
        bad_in_filter  = (filtered['profit_ratio'] < 0).mean() * 100
        kept_profit_sum = kept['profit_ratio'].sum()
        saved_loss_sum  = filtered[filtered['profit_ratio']<0]['profit_ratio'].sum()
        missed_gain_sum = filtered[filtered['profit_ratio']>0]['profit_ratio'].sum()
        net_change = (saved_loss_sum * -1 + missed_gain_sum * -1)  # 过滤后净变化（过滤=不进场）
        # 净收益估算：损失减少 - 盈利减少
        gain_saved = filtered[filtered['profit_ratio']<0]['profit_ratio'].sum() * -1  # 少亏
        gain_lost  = filtered[filtered['profit_ratio']>0]['profit_ratio'].sum()     # 少赚
        net = gain_saved - gain_lost
        print(f"  {name:>30}  {len(filtered):>8}  {bad_in_filter:>7.1f}%  净变化 {net*100:+.2f}%pts")
    print()

    # ── 9. 关键结论 ──────────────────────────────────────────────────────────
    print(f"{'='*65}")
    print("  关键结论 / A-2c 候选条件排序")
    print(f"{'='*65}")

    below50_losing  = (losing['above_ema50']  == 0).mean() * 100
    below50_winning = (winning['above_ema50'] == 0).mean() * 100
    below200_losing  = (losing['above_ema200']  == 0).mean() * 100
    below200_winning = (winning['above_ema200'] == 0).mean() * 100

    print(f"""
  EMA50 以下入场比例：  亏损单 {below50_losing:.1f}%  vs  盈利单 {below50_winning:.1f}%
  EMA200 以下入场比例：亏损单 {below200_losing:.1f}%  vs  盈利单 {below200_winning:.1f}%

  → 如果 EMA50 差距显著（>15%），首选候选条件：
      axis_support 必须 close > ema_50 才允许入场
  → 如果 momentum3h 差距显著，次选：
      axis_support 必须入场前 3H 已有上涨动能

  注意：
  - 这只是基础技术指标过滤，wyckoff 专属指标（phase_d、sos_bar_confirmed 等）
    需要导出 signals 数据后才能分析
  - 建议先试最简单的一个条件（ema50），再决定是否需要 signals 导出
""")


if __name__ == '__main__':
    main()
