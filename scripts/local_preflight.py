#!/usr/bin/env python3
"""
V8 Dry Run 启动前检查（本地 OKX 版）
嵌入 start_dryrun.sh 的 preflight 步骤。
全部 PASS 或仅 WARN 才允许启动。
"""
import sys, os, json, subprocess
sys.path.insert(0, '/home/gyan/freqtrade')

import pandas as pd
import numpy as np
from datetime import timezone, timedelta
from wyckoff.daily_event_detector import DailyEventDetector

FREQTRADE_DIR = '/home/gyan/freqtrade'
PASS = '✅ PASS'
FAIL = '❌ FAIL'
WARN = '⚠️  WARN'

results = []
fail_count = 0
warn_count = 0

def check(label, status, detail=''):
    global fail_count, warn_count
    icon = PASS if status == 'pass' else (FAIL if status == 'fail' else WARN)
    results.append((icon, label, detail))
    if status == 'fail': fail_count += 1
    if status == 'warn': warn_count += 1
    print(f'  {icon}  {label}')
    if detail: print(f'         {detail}')

DATA_DIR = os.path.join(FREQTRADE_DIR, 'user_data/data/binance/futures')
DATA_1H  = os.path.join(DATA_DIR, 'BTC_USDT_USDT-1h-futures.feather')
DATA_1D  = os.path.join(DATA_DIR, 'BTC_USDT_USDT-1d-futures.feather')
CONFIG   = os.path.join(FREQTRADE_DIR, 'user_data/config_dryrun.json')
CONFIG_V8 = os.path.join(FREQTRADE_DIR, 'user_data/config_v8_dryrun.json')

NOW_UTC = pd.Timestamp.now(tz='UTC')

print()
print('=' * 65)
print('  WyckoffV8 Dry Run 启动前检查（本地 OKX）')
print('=' * 65)

# ─── 1. 数据文件存在性 ──────────────────────────────
print(); print('【1】数据文件检查')
if os.path.exists(DATA_1H):
    sz = os.path.getsize(DATA_1H) / 1024 / 1024
    check('1H futures feather', 'pass', f'{sz:.1f}MB')
else:
    check('1H futures feather', 'fail', f'不存在: {DATA_1H}')
if os.path.exists(DATA_1D):
    sz = os.path.getsize(DATA_1D) / 1024 / 1024
    check('1D futures feather', 'pass', f'{sz:.1f}MB')
else:
    check('1D futures feather', 'fail', f'不存在: {DATA_1D}')

# ─── 2. 数据新鲜度 ──────────────────────────────────
print(); print('【2】数据新鲜度')
df1h = pd.read_feather(DATA_1H)
df1d = pd.read_feather(DATA_1D)
df1h['date'] = pd.to_datetime(df1h['date'], utc=True)
df1d['date'] = pd.to_datetime(df1d['date'], utc=True)

last_1h = df1h['date'].max(); gap_1h = (NOW_UTC - last_1h).total_seconds() / 3600
last_1d = df1d['date'].max(); gap_1d = (NOW_UTC - last_1d).total_seconds() / 3600
h1_days = (last_1h - df1h['date'].min()).days
d1_days = (last_1d - df1d['date'].min()).days

check('1H 新鲜（< 2h）', 'pass' if gap_1h < 2 else ('warn' if gap_1h < 24 else 'fail'),
      f'最新={str(last_1h)[:16]}  滞后{gap_1h:.1f}h  历史{h1_days}天')
check('1D 新鲜（< 48h）', 'pass' if gap_1d < 48 else 'fail',
      f'最新={str(last_1d)[:16]}  滞后{gap_1d:.1f}h  历史{d1_days}天')

# ─── 2b. 自动补下载（滞后时触发）─────────────────────
if gap_1h >= 2 or gap_1d >= 48:
    print()
    print('【2b】数据滞后，自动补下载...')
    from_date_1h = (last_1h - timedelta(days=1)).strftime('%Y%m%d')
    from_date_1d = (last_1d - timedelta(days=2)).strftime('%Y%m%d')
    # 下载 1H + 1D
    for tf, from_date in [('1h', from_date_1h), ('1d', from_date_1d)]:
        cmd = [
            sys.executable, '-m', 'freqtrade', 'download-data',
            '--config', CONFIG,
            '--timerange', f'{from_date}-',
            '--timeframe', tf,
            '--exchange', 'okx',
        ]
        print(f'  下载 {tf}（{from_date}-）...')
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            check(f'  {tf} 下载失败', 'fail', r.stderr[:200])
    # 重读 & 重新检查新鲜度
    df1h = pd.read_feather(DATA_1H)
    df1d = pd.read_feather(DATA_1D)
    df1h['date'] = pd.to_datetime(df1h['date'], utc=True)
    df1d['date'] = pd.to_datetime(df1d['date'], utc=True)
    last_1h = df1h['date'].max(); gap_1h = (NOW_UTC - last_1h).total_seconds() / 3600
    last_1d = df1d['date'].max(); gap_1d = (NOW_UTC - last_1d).total_seconds() / 3600
    check('  1H 新鲜度（补后）', 'pass' if gap_1h < 2 else 'fail',
          f'最新={str(last_1h)[:16]}  滞后{gap_1h:.1f}h')
    check('  1D 新鲜度（补后）', 'pass' if gap_1d < 48 else 'fail',
          f'最新={str(last_1d)[:16]}  滞后{gap_1d:.1f}h')
    # 更新 h1_days / d1_days 供后续 PNF 检查使用
    h1_days = (last_1h - df1h['date'].min()).days
    d1_days = (last_1d - df1d['date'].min()).days

# ─── 3. PNF 预热 ─────────────────────────────────────
print(); print('【3】PNF 预热（≥ 2 年数据）')
MIN_DAYS = 730
check('1H 深度 ≥730天', 'pass' if h1_days >= MIN_DAYS else 'fail',
      f'{h1_days}天 / 需{MIN_DAYS}天')
check('1D 深度 ≥730天', 'pass' if d1_days >= MIN_DAYS else 'fail',
      f'{d1_days}天 / 需{MIN_DAYS}天')
STARTUP_CANDLES = 300   # WyckoffV8 startup_candle_count
check('startup_candle 够用', 'pass' if len(df1h) > STARTUP_CANDLES else 'fail',
      f'1H {len(df1h):,}条  > {STARTUP_CANDLES}')

# ─── 4. 市场门控状态 ─────────────────────────────────
print(); print('【4】市场门控状态')
df1d_det = df1d.sort_values('date').reset_index(drop=True)
det = DailyEventDetector()
df1d_det = det.detect(df1d_det.copy())
df1d_det['ema200'] = df1d_det['close'].ewm(span=200, adjust=False).mean()

last_row = df1d_det.iloc[-1]
prev_row = df1d_det.iloc[-2]

dcs_today    = bool(prev_row.get('dist_cause_sufficient', False))
be_today     = bool(prev_row.get('buying_exhaustion', False))
sow_today    = bool(prev_row.get('daily_dist_sow_detected', False))
cur_price    = last_row['close']
ema200_now   = last_row['ema200']
above_ema200 = cur_price > ema200_now
tail7 = df1d_det.tail(7)
ema200_persist = bool((tail7['close'] < tail7['ema200']).all())
bear_gate_approx = dcs_today or ema200_persist

check('DCS', 'pass' if dcs_today else 'warn', f'当前={dcs_today}')
check('BE',  'pass' if be_today  else 'warn', f'当前={be_today}  价格={cur_price:,.0f}  EMA200={ema200_now:,.0f}')
check('SOW', 'pass' if sow_today else 'warn', f'当前={sow_today}')
check('EMA200 位置', 'warn' if above_ema200 else 'pass',
      f'收盘={cur_price:,.0f}  EMA200={ema200_now:,.0f}')
check('ema200_persist（7日破）', 'pass' if ema200_persist else 'warn', str(ema200_persist))
check('bear_gate（DCS|persist）', 'pass' if bear_gate_approx else 'warn', str(bear_gate_approx))

# ─── 5. 近 7 天信号回放 ─────────────────────────────
print(); print('【5】近 7 天信号回放（shift=1）')
print(f'  {"日期":12s} {"收盘":>9} {"EMA200":>9} {"DCS":>5} {"BE":>5} {"SOW":>5} {"gate":>7}')
for _, row in df1d_det.tail(8).iloc[:-1].iterrows():
    idx = row.name
    if idx + 1 >= len(df1d_det): continue
    ar = df1d_det.iloc[idx + 1]
    d = bool(row.get('dist_cause_sufficient', False))
    b = bool(row.get('buying_exhaustion', False))
    s = bool(row.get('daily_dist_sow_detected', False))
    e = row['ema200']
    p = ar['close']
    g = d or (p < e)
    print(f'  {str(ar["date"].date()):12s} {p:>9,.0f} {e:>9,.0f} '
          f'{"T" if d else "F":>5} {"T" if b else "F":>5} {"T" if s else "F":>5} '
          f'{"OPEN" if g else "CLSD":>7}')

# ─── 6. 配置文件检查 ─────────────────────────────────────
print(); print('【6】配置文件检查')
cfg_ok = False

# 只检查主 config（config_dryrun.json），v8 override 是 partial config 不单独检查
def check_config(cfg, label):
    trading_mode = cfg.get('trading_mode', 'spot')
    margin_mode  = cfg.get('margin_mode', '')
    mot = cfg.get('max_open_trades', -1)
    stake = cfg.get('stake_currency', '')
    check(f'{label}: 存在', 'pass')
    check(f'  trading_mode=futures', 'pass' if trading_mode == 'futures' else 'warn',
          f'实际={trading_mode}（override 文件允许 spot）')
    check(f'  margin_mode=isolated', 'pass' if margin_mode == 'isolated' else 'warn',
          f'实际={margin_mode}')
    check(f'  max_open_trades=1', 'pass' if mot == 1 else 'fail',
          f'实际={mot}（override -1 继承主配置，正常）' if mot == -1 else f'实际={mot}')
    check(f'  stake_currency=USDT', 'pass' if stake == 'USDT' else 'warn',
          f'实际={stake}')

if os.path.exists(CONFIG):
    cfg_ok = True
    with open(CONFIG) as f: check_config(json.load(f), 'config_dryrun.json')
else:
    check('config_dryrun.json', 'fail', '文件不存在')

if os.path.exists(CONFIG_V8):
    with open(CONFIG_V8) as f:
        v8cfg = json.load(f)
    check('config_v8_dryrun.json', 'pass', 'override 配置文件（跳过独立校验）')
else:
    check('config_v8_dryrun.json', 'warn', '文件不存在，可能未使用 V8')

# ─── 汇总（重新统计，Section 2b 可能已修复 Section 2 的 FAIL）─
print(); print('=' * 65)
final_fail = sum(1 for r in results if r[0] == FAIL)
final_warn = sum(1 for r in results if r[0] == WARN)
final_pass = len(results) - final_fail - final_warn
print(f'  检查结果：{final_pass} PASS  |  {final_warn} WARN  |  {final_fail} FAIL')
print()
if final_fail > 0:
    print('  ❌ 有 FAIL 项，禁止启动！')
    for icon, label, detail in results:
        if icon == FAIL: print(f'     → {label}  {detail}')
    sys.exit(1)
elif warn_count > 0:
    print('  ⚠️  有 WARN 项，继续启动（常见 WARN 不影响运行）')
else:
    print('  ✅ 全部通过，启动 Dry Run！')
print('=' * 65)
print()
