"""
TASK 23 前置诊断 v2：FTI（Fall Through the Ice / 跌破冰层）结构识别
                    + 横盘前置条件（Consolidation Pre-condition）

核心逻辑（三段式状态机）：
  前置：横盘结构确认 — MSOW 触发前，近 CONSOL_WINDOW 日区间 <= CONSOL_ATR_MAX × ATR
  阶段①：MSOW 破位  — close < ice_line（横盘低点）+ 扩量（>1.5x）+ 宽价差（>1.5xATR）
  阶段②：回抽触及冰线 — MSOW 后 N 日内，high >= ice * 0.98
  阶段③：FTI 确认入场 — 收盘仍在冰线下 + 窄价差（<0.7xATR）+ 缩量（<0.9x）

冰线定义（Ice Line）修正 v2：
  横盘窗口内的滚动最低 low（shift(1)，无未来函数）
  → 代表真实的"平衡支撑"，而非随动的移动阴影

v1 问题：rolling(14).min(close) 无法区分横盘支撑 vs 趋势惯性低点
v2 修复：加入 CONSOL_ATR_MAX 横盘约束 + 用 low.rolling 替代 close.rolling 做冰线

关键验证节点：
  2022-05 LUNA 崩盘  → 应触发（30k 横盘3个月，冰线=30k）
  2022-11 FTX 崩盘   → 应触发（18k-21k 横盘，冰线=18k）
  2025-04 假跌破      → 不应触发（V型反转，回抽强势）
  2024-09 噪音        → 应消失（单向下跌中短暂企稳，无横盘结构）
  2025-11 至今        → 统计覆盖率
"""
import sys
sys.path.insert(0, '/freqtrade')
import pandas as pd
import numpy as np

# ── 数据准备 ────────────────────────────────────────────────────────────────────
df = pd.read_feather('user_data/data/binance/futures/BTC_USDT_USDT-1d-futures.feather')
df['date'] = pd.to_datetime(df['date'], utc=True)
df = df.sort_values('date').reset_index(drop=True)

# ── 基础指标 ────────────────────────────────────────────────────────────────────
df['ema200']   = df['close'].ewm(span=200, adjust=False).mean()
df['atr14']    = (df['high'] - df['low']).rolling(14, min_periods=7).mean()
df['vol_ma20'] = df['volume'].rolling(20, min_periods=10).mean()
df['spread']   = df['high'] - df['low']

# ── v4：横盘前置改用价格百分比（规避ATR相对性在高波动熊市的失真）──────────────────
# v3根因：2022 ATR极高，横盘区间/ATR > 2.5 → 真正的熊市横盘反被过滤
# v4修复：用 range/close 百分比代替 range/ATR，价格无关（10日区间 ≤ 15%）
CONSOL_WINDOW  = 14    # 横盘检测窗口（日，稍长捕捉更稳定的结构）
CONSOL_PCT_MAX = 0.15  # 横盘要求：14日内 (high-low)/close <= 15%（价格相对）

# 横盘区间
df['range_14d'] = (df['high'].rolling(CONSOL_WINDOW, min_periods=8).max()
                 - df['low'].rolling(CONSOL_WINDOW, min_periods=8).min())
# 百分比标志（shift(1) 防未来函数）
df['consol_ok'] = ((df['range_14d'] / df['close']) <= CONSOL_PCT_MAX).shift(1)
df['consol_ok'] = df['consol_ok'].infer_objects(copy=False).fillna(False)

# v4 新增：EMA200 熊市强制前置（消除牛市假信号）
df['bear_ok'] = (df['close'] < df['ema200']).shift(1)
df['bear_ok']  = df['bear_ok'].infer_objects(copy=False).fillna(False)

# 冰线 v4：横盘窗口内的最低 low（真实平衡支撑）
df['ice_line'] = df['low'].rolling(CONSOL_WINDOW, min_periods=8).min().shift(1)

# ── 诊断参数 ────────────────────────────────────────────────────────────────────
MSOW_SPREAD_MULT = 1.5   # MSOW：价差 >= ATR × 1.5（扩张）
MSOW_VOL_MULT    = 1.5   # MSOW：成交量 >= vol_ma × 1.5（放量）
# v3：FTI 确认改用 VSA 收盘位置（Close Location）作为主要信号
# 物理逻辑：回抽触及冰线，但收盘在当日区间的低位区 = 卖方守住了冰线，需求失败
FTI_CLOSE_LOC    = 0.40  # FTI确认：收盘位置 <= 40%（(close-low)/(high-low) ≤ 0.4）
FTI_VOL_MULT     = 1.50  # 辅助过滤：量 <= vol_ma × 1.5（允许放量但收在低位 = 供应压制）
PULLBACK_WINDOW  = 7     # MSOW 后等待回抽的最大天数（放宽：允许更深的 LPSY）
TOUCH_RATIO      = 0.985 # 触及冰线：high >= ice × 0.985

STUDY_START = '2022-01-01'
COOLDOWN    = 7          # 触发后最短冷却天数

# ── 状态机扫描（日线级） ────────────────────────────────────────────────────────
study = df[df['date'] >= STUDY_START].copy().reset_index(drop=True)

events = []
state           = 'IDLE'
msow_bar        = None
ice_at_msow     = None
cooldown_until  = -1

for i in range(CONSOL_WINDOW + 1, len(study) - 31):
    if i < cooldown_until:
        continue

    row    = study.loc[i]
    atr    = row['atr14']
    vol_ma = row['vol_ma20']
    ice    = row['ice_line']

    if any(np.isnan(v) or v <= 0 for v in [atr, vol_ma, ice]):
        continue

    spread = row['spread']
    vol    = row['volume']
    close  = row['close']
    high   = row['high']

    consol = bool(row['consol_ok'])  # 前置：横盘结构已形成（价格百分比）
    bear   = bool(row['bear_ok'])   # 前置：EMA200 熊市确认

    if state == 'IDLE':
        # 检测 MSOW：【横盘前置】+【EMA200熊市】+ 收盘跌破冰线 + 扩量 + 宽价差
        if (consol and bear and                     # v4：横盘 + EMA200熊市双前置
                close < ice and
                spread >= atr * MSOW_SPREAD_MULT and
                vol    >= vol_ma * MSOW_VOL_MULT):
            state       = 'MSOW_FIRED'
            msow_bar    = i
            ice_at_msow = ice

    elif state == 'MSOW_FIRED':
        days_since = i - msow_bar
        if days_since > PULLBACK_WINDOW:
            # 等待超时，回到 IDLE（无回抽 = 垂直崩跌，非 FTI 结构）
            state       = 'IDLE'
            msow_bar    = None
            ice_at_msow = None
            # 当前 bar 重新判定（含横盘前置 + EMA200）
            if (consol and bear and
                    close < ice and
                    spread >= atr * MSOW_SPREAD_MULT and
                    vol    >= vol_ma * MSOW_VOL_MULT):
                state       = 'MSOW_FIRED'
                msow_bar    = i
                ice_at_msow = ice
            continue

        # 检测回抽触及冰线
        if high >= ice_at_msow * TOUCH_RATIO:
            # 量价指纹判定
            spread_ratio = spread / atr
            vol_ratio    = vol / vol_ma

            # v3：VSA 收盘位置 = (close - low) / (high - low)
            # 物理含义：收盘在当日低位区 → 卖方守住冰线，需求失败
            bar_range   = high - row['low']
            close_loc   = (close - row['low']) / bar_range if bar_range > 0 else 0.5

            fti_ok = (
                close < ice_at_msow and              # 收盘仍在冰线下（未收复）
                close_loc <= FTI_CLOSE_LOC and        # VSA：收盘在日线低位区（需求失败）
                vol_ratio <= FTI_VOL_MULT             # 辅助：量 <= 1.5×MA20
            )

            msow_row = study.loc[msow_bar]

            # 后续表现
            f14 = i + 14
            f30 = i + 30
            ret14 = (close / study.loc[f14, 'close'] - 1) * 100 if f14 < len(study) else np.nan
            ret30 = (close / study.loc[f30, 'close'] - 1) * 100 if f30 < len(study) else np.nan

            consol_range = msow_row['range_14d'] / msow_row['close'] * 100 if msow_row['close'] > 0 else float('nan')
            events.append({
                'msow_date':      str(msow_row['date'])[:10],
                'fti_date':       str(row['date'])[:10],
                'msow_price':     msow_row['close'],
                'fti_price':      close,
                'ice_level':      ice_at_msow,
                'consol_atr':     consol_range,
                'days_pullback':  days_since,
                'close_loc':      close_loc,       # VSA收盘位置（0=最低，1=最高）
                'spread_ratio':   spread_ratio,
                'vol_ratio':      vol_ratio,
                'fti_ok':         fti_ok,
                'below_ema200':   close < row['ema200'],
                'ret14':          ret14,
                'ret30':          ret30,
            })

            state          = 'IDLE'
            msow_bar       = None
            ice_at_msow    = None
            cooldown_until = i + COOLDOWN

# ── 汇总输出 ─────────────────────────────────────────────────────────────────────
SEP = '=' * 110

print(SEP)
print('  TASK 23 诊断 — FTI（跌破冰层）结构识别')
print(f'  横盘窗口={CONSOL_WINDOW}日(<={CONSOL_PCT_MAX*100:.0f}%价格) + EMA200熊市 | MSOW: 价差>={MSOW_SPREAD_MULT}xATR, 量>={MSOW_VOL_MULT}xMA20')
print(f'  FTI确认: 收盘位置<={FTI_CLOSE_LOC}, 量<={FTI_VOL_MULT}xMA20 | 回抽窗口={PULLBACK_WINDOW}日')
print(SEP)

if not events:
    print('  ⚠️  0 个事件，参数过严，请调整后重试')
    sys.exit(0)

df_ev = pd.DataFrame(events)

# ── 全量明细 ─────────────────────────────────────────────────────────────────────
print(f'\n识别到回抽事件（含 FTI 确认/否认）：{len(df_ev)} 笔\n')
hdr = (f'{"MSOW日":12} {"FTI日":12} {"MSOW价":>9} {"FTI价":>9} {"冰线":>9} '
       f'{"横盘/ATR":>9} {"回抽天":>6} {"收盘位置":>8} {"量/MA20":>8} '
       f'{"EMA下":>6} {"FTI?":>5} {"14D%":>8} {"30D%":>8}  判定')
print(hdr)
print('-' * 115)

for _, ev in df_ev.iterrows():
    r14  = f'{ev["ret14"]:+.1f}%' if not np.isnan(ev['ret14']) else 'N/A'
    r30  = f'{ev["ret30"]:+.1f}%' if not np.isnan(ev['ret30']) else 'N/A'
    ema_ok = 'Y' if ev['below_ema200'] else 'N'
    fti_s  = 'YES' if ev['fti_ok'] else 'no'
    ca     = f'{ev["consol_atr"]:.1f}%' if not np.isnan(ev['consol_atr']) else 'N/A'
    cl_s   = f'{ev["close_loc"]:.2f}' if not np.isnan(ev['close_loc']) else 'N/A'
    is_true  = not np.isnan(ev['ret14']) and ev['ret14'] > 3
    is_false = not np.isnan(ev['ret14']) and ev['ret14'] < -5
    verdict  = 'TRUE_SHORT' if is_true else ('V_REVERSAL' if is_false else 'NEUTRAL')
    fti_mark = ('✅' if ev['fti_ok'] else '○') + ' ' + verdict

    print(f'{ev["msow_date"]:12} {ev["fti_date"]:12} {ev["msow_price"]:>9,.0f} '
          f'{ev["fti_price"]:>9,.0f} {ev["ice_level"]:>9,.0f} '
          f'{ca:>9} {int(ev["days_pullback"]):>6} {cl_s:>8} {ev["vol_ratio"]:>8.2f} '
          f'{ema_ok:>6} {fti_s:>5} {r14:>8} {r30:>8}  {fti_mark}')

# ── 分期统计 ─────────────────────────────────────────────────────────────────────
print(f'\n{SEP}')
print('  分期统计')
print(SEP)

def period_stats(label, mask):
    sub = df_ev[mask]
    if len(sub) == 0:
        print(f'  {label:35s}: 0 笔')
        return
    fti_sub  = sub[sub['fti_ok']]
    no_fti   = sub[~sub['fti_ok']]
    valid    = sub['ret14'].dropna()
    fti_v    = fti_sub['ret14'].dropna()
    true_c   = (fti_v > 3).sum()
    false_c  = (fti_v < -5).sum()
    avg14    = fti_v.mean() if len(fti_v) else float('nan')
    avg14_s  = f'{avg14:+.1f}%' if not np.isnan(avg14) else 'N/A'
    print(f'  {label:35s}: 回抽{len(sub):2d}笔 | FTI确认{len(fti_sub):2d}笔 | '
          f'真空头(>+3%): {true_c} | V型(<-5%): {false_c} | 均14D={avg14_s}')

period_stats('2022 全年（熊市）',      df_ev['fti_date'].str[:4] == '2022')
period_stats('2022-05 LUNA 崩盘',      df_ev['fti_date'].str[:7] == '2022-05')
period_stats('2022-11 FTX 崩盘',       df_ev['fti_date'].str[:7].isin(['2022-11','2022-12']))
period_stats('2024-01~2025-10 牛市区间', (df_ev['fti_date'] >= '2024-01-01') & (df_ev['fti_date'] < '2025-11-01'))
period_stats('2025-03/04 假跌破',      df_ev['fti_date'].str[:7].isin(['2025-03','2025-04']))
period_stats('2025-11 至今',           df_ev['fti_date'] >= '2025-11-01')

# ── 关键验证：2025-03/04 零触发 ──────────────────────────────────────────────────
print(f'\n{SEP}')
print('  关键验证：2025-03/04 假跌破期 FTI 触发情况')
print(SEP)
fp = df_ev[(df_ev['fti_date'].str[:7].isin(['2025-03','2025-04'])) & df_ev['fti_ok']]
if len(fp) == 0:
    print('\n  ✅ 2025-03/04：FTI 确认 = 0 笔  → 回抽强势，被窄价差+缩量过滤器正确拦截')
else:
    print(f'\n  ❌ 2025-03/04：FTI 确认 = {len(fp)} 笔（需追加过滤）')
    for _, r in fp.iterrows():
        print(f'     {r["fti_date"]}  fti_price={r["fti_price"]:,.0f}  '
              f'close_loc={r["close_loc"]:.2f}  vol_ratio={r["vol_ratio"]:.2f}')

# ── 参数敏感度：VSA 收盘位置分布 ─────────────────────────────────────────────────
print(f'\n{SEP}')
print('  参数敏感度：回抽事件的 VSA 收盘位置分布（所有回抽，含未确认）')
print(SEP)
print(f'\n  收盘位置分布（0=当日最低，1=当日最高；FTI要求 <= {FTI_CLOSE_LOC}）：')
for pct in [10, 25, 50, 75, 90]:
    val = df_ev['close_loc'].quantile(pct / 100)
    tag = ' <-- FTI 阈值附近' if abs(val - FTI_CLOSE_LOC) < 0.08 else ''
    print(f'    P{pct:2d}: {val:.2f}{tag}')
print(f'\n  量/MA20 分布（FTI 确认要求 <= {FTI_VOL_MULT}）：')
for pct in [25, 50, 75, 90]:
    val = df_ev['vol_ratio'].quantile(pct / 100)
    tag = ' <-- FTI 阈值' if abs(val - FTI_VOL_MULT) < 0.1 else ''
    print(f'    P{pct:2d}: {val:.2f}x MA20{tag}')

# ── 真 FTI 事件详情（仅 fti_ok=True） ────────────────────────────────────────────
confirmed = df_ev[df_ev['fti_ok']]
print(f'\n{SEP}')
print(f'  FTI 已确认事件详情（共 {len(confirmed)} 笔）')
print(SEP)
if len(confirmed) == 0:
    print('\n  ⚠️  0 笔 FTI 确认 — 阈值偏严，建议放宽 FTI_CLOSE_LOC 或 PULLBACK_WINDOW')
else:
    for _, ev in confirmed.iterrows():
        r14 = f'{ev["ret14"]:+.1f}%' if not np.isnan(ev['ret14']) else 'N/A'
        r30 = f'{ev["ret30"]:+.1f}%' if not np.isnan(ev['ret30']) else 'N/A'
        outcome = 'TRUE' if (not np.isnan(ev['ret14']) and ev['ret14'] > 3) else (
                  'FAIL' if (not np.isnan(ev['ret14']) and ev['ret14'] < -5) else 'NEUTRAL')
        ema_note = '（EMA200下方）' if ev['below_ema200'] else '（EMA200上方！）'
        print(f'\n  [{outcome}] MSOW: {ev["msow_date"]} @ {ev["msow_price"]:,.0f}'
              f'  →  FTI: {ev["fti_date"]} @ {ev["fti_price"]:,.0f} {ema_note}')
        print(f'         冰线={ev["ice_level"]:,.0f}  回抽第{int(ev["days_pullback"])}天'
              f'  收盘位置={ev["close_loc"]:.2f}  量={ev["vol_ratio"]:.2f}xMA20'
              f'  → 14D: {r14}  30D: {r30}')

# ── 当前 BTC 状态评估 ─────────────────────────────────────────────────────────────
print(f'\n{SEP}')
print('  当前 BTC 状态（最后20日）')
print(SEP)
tail = study.tail(20)
latest = study.iloc[-1]
print(f'\n  最新日期: {str(latest["date"])[:10]}')
print(f'  收盘价:   {latest["close"]:,.0f}')
print(f'  EMA200:  {latest["ema200"]:,.0f}  ({(latest["close"]/latest["ema200"]-1)*100:.1f}%)')
print(f'  冰线:    {latest["ice_line"]:,.0f}  ({(latest["close"]/latest["ice_line"]-1)*100:.1f}%)')
print(f'  ATR14:   {latest["atr14"]:,.0f}')
print(f'  当前价/冰线 倍差: {(latest["close"] - latest["ice_line"]) / latest["atr14"]:.2f}x ATR')

below_ice = latest['close'] < latest['ice_line']
print(f'\n  {"⚠️  已跌破冰线，MSOW 窗口开启" if below_ice else "  价格仍在冰线上方，等待 MSOW"}')

# ── 结论 ──────────────────────────────────────────────────────────────────────────
print(f'\n{SEP}')
print('  TASK 23 诊断结论')
print(SEP)
fti_bear_2022 = df_ev[(df_ev['fti_date'].str[:4] == '2022') & df_ev['fti_ok']]
fti_bull_false = df_ev[(df_ev['fti_date'].str[:7].isin(['2025-03','2025-04'])) & df_ev['fti_ok']]
fti_all        = df_ev[df_ev['fti_ok']]
fti_true       = fti_all[fti_all['ret14'] > 3]
fti_false      = fti_all[fti_all['ret14'] < -5]

print(f'\n  FTI 触发总数：{len(fti_all)} 笔')
print(f'  真空头（14D > +3%）：{len(fti_true)} 笔  ({len(fti_true)/max(1,len(fti_all))*100:.0f}%)')
print(f'  V型反弹（14D < -5%）：{len(fti_false)} 笔  ({len(fti_false)/max(1,len(fti_all))*100:.0f}%)')
print(f'  2022 熊市覆盖：{len(fti_bear_2022)} 笔')
print(f'  2025-03/04 误触发：{len(fti_bull_false)} 笔')

if len(fti_bull_false) == 0 and len(fti_bear_2022) >= 2:
    print('\n  ✅ 通过：FTI 在 2022 熊市触发，2025-04 无误触发 → 立项 TASK 23 值得推进')
elif len(fti_bull_false) == 0 and len(fti_bear_2022) < 2:
    print('\n  ⚠️  中性：无误触发但熊市覆盖不足 → 放宽参数后再评估')
else:
    print(f'\n  ❌ 有风险：{len(fti_bull_false)} 笔误触发 → 追加过滤条件')
print(SEP)
