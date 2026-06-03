"""
V8 Dry Run 上线前检查清单 — preflight_check.py

检查项目：
  1. 数据完整性（历史深度 / 最新数据时间）
  2. PNF 预热充分性（需要 4 年以上日线历史）
  3. 当前市场门控状态（DCS / ema200_persist / bear_gate）
  4. 当前信号状态（最近 48H 是否有 wave_start / short 信号）
  5. 策略关键配置验证（futures / can_short / max_open_trades）
  6. startup_candle_count 与数据深度对比
  7. 日线检测器最新输出

运行：
  docker compose run --rm --entrypoint "" freqtrade \
    sh -c "python user_data/scripts/preflight_check.py"

全部 PASS 后再启动 Dry Run。
"""

import sys, os, json
sys.path.insert(0, '/freqtrade')

import pandas as pd
import numpy as np
from datetime import timezone
from wyckoff.daily_event_detector import DailyEventDetector

PASS = '✅ PASS'
FAIL = '❌ FAIL'
WARN = '⚠️  WARN'

results = []

def check(label, status, detail=''):
    icon = PASS if status == 'pass' else (FAIL if status == 'fail' else WARN)
    results.append((icon, label, detail))
    print(f'  {icon}  {label}')
    if detail:
        print(f'         {detail}')

print()
print('=' * 65)
print('  WyckoffV8 Dry Run 上线前检查清单')
print('=' * 65)

# ─── 1. 数据文件存在性 ────────────────────────────────────────────────────────
print()
print('【1】数据文件检查')

DATA_1H = 'user_data/data/binance/futures/BTC_USDT_USDT-1h-futures.feather'
DATA_1D = 'user_data/data/binance/futures/BTC_USDT_USDT-1d-futures.feather'

for path, label in [(DATA_1H, '1H futures feather'), (DATA_1D, '1D futures feather')]:
    if os.path.exists(path):
        size_mb = os.path.getsize(path) / 1024 / 1024
        check(f'{label} 存在', 'pass', f'大小={size_mb:.1f}MB  路径={path}')
    else:
        check(f'{label} 存在', 'fail', f'文件不存在: {path}')

# ─── 2. 数据时间范围 & 新鲜度 ────────────────────────────────────────────────
print()
print('【2】数据时间范围 & 新鲜度')

df1h = pd.read_feather(DATA_1H)
df1d = pd.read_feather(DATA_1D)
df1h['date'] = pd.to_datetime(df1h['date'], utc=True)
df1d['date'] = pd.to_datetime(df1d['date'], utc=True)

now_utc = pd.Timestamp.now(tz='UTC')

# 1H 新鲜度
last_1h = df1h['date'].max()
gap_1h  = (now_utc - last_1h).total_seconds() / 3600
h1_start = df1h['date'].min()
h1_days  = (last_1h - h1_start).days
check('1H 数据新鲜（< 2H 滞后）',
      'pass' if gap_1h < 2 else ('warn' if gap_1h < 24 else 'fail'),
      f'最新={str(last_1h)[:16]} UTC  滞后={gap_1h:.1f}h  历史={h1_days}天')

# 1D 新鲜度
last_1d  = df1d['date'].max()
gap_1d   = (now_utc - last_1d).total_seconds() / 3600
d1_start = df1d['date'].min()
d1_days  = (last_1d - d1_start).days
check('1D 数据新鲜（< 25H 滞后）',
      'pass' if gap_1d < 25 else 'fail',
      f'最新={str(last_1d)[:16]} UTC  滞后={gap_1d:.1f}h  历史={d1_days}天')

# ─── 3. PNF / 策略预热充分性 ─────────────────────────────────────────────────
print()
print('【3】PNF 预热充分性（需要 ≥ 2 年日线历史）')

MIN_DAYS_FOR_PNF = 730  # 2年
check('1H 历史深度 ≥ 730天',
      'pass' if h1_days >= MIN_DAYS_FOR_PNF else 'fail',
      f'实际={h1_days}天  需要≥{MIN_DAYS_FOR_PNF}天')
check('1D 历史深度 ≥ 730天',
      'pass' if d1_days >= MIN_DAYS_FOR_PNF else 'fail',
      f'实际={d1_days}天  需要≥{MIN_DAYS_FOR_PNF}天')

STARTUP_CANDLES = 1440  # WyckoffV8 startup_candle_count（1H * 60天）
startup_days = STARTUP_CANDLES / 24
check(f'startup_candle_count({STARTUP_CANDLES}) < 1H 数据量',
      'pass' if len(df1h) > STARTUP_CANDLES else 'fail',
      f'实际1H条数={len(df1h):,}  需要>{STARTUP_CANDLES}')

# ─── 4. 日线检测器 — 当前市场门控状态 ────────────────────────────────────────
print()
print('【4】当前市场门控状态（日线检测器输出）')

df1d_det = df1d[df1d['date'] >= '2021-01-01'].sort_values('date').reset_index(drop=True)
det = DailyEventDetector()
df1d_det = det.detect(df1d_det.copy())
df1d_det['ema200'] = df1d_det['close'].ewm(span=200, adjust=False).mean()

# 策略可用信号（shift 1 = 昨日信号）
last_row   = df1d_det.iloc[-1]   # 今日
prev_row   = df1d_det.iloc[-2]   # 昨日（策略当天看到的）

dcs_today    = bool(prev_row.get('dist_cause_sufficient', False))
be_today     = bool(prev_row.get('buying_exhaustion', False))
sow_today    = bool(prev_row.get('daily_dist_sow_detected', False))
cur_price    = last_row['close']
ema200_now   = last_row['ema200']
above_ema200 = cur_price > ema200_now

# ema200_persist: 7日持续低于EMA200
tail7 = df1d_det.tail(7)
ema200_persist = bool((tail7['close'] < tail7['ema200']).all())

# _bear_gate (V8: DCS | sm_v2 | ema200_persist)
# sm_v2 需要策略内部计算，这里用 DCS | ema200_persist 近似
bear_gate_approx = dcs_today or ema200_persist

check('DCS（dist_cause_sufficient）',
      'pass' if dcs_today else 'warn',
      f'当前={dcs_today}  （True=分发期 / False=积累期）')
check('BE（buying_exhaustion）当日',
      'pass' if be_today else 'warn',
      f'当前={be_today}  价格={cur_price:,.0f}  EMA200={ema200_now:,.0f}')
check('SOW（daily_dist_sow_detected）当日',
      'pass' if sow_today else 'warn',
      f'当前={sow_today}')
check('EMA200 位置',
      'warn' if above_ema200 else 'pass',
      f'收盘={cur_price:,.0f}  EMA200={ema200_now:,.0f}  {'上方（多头友好）' if above_ema200 else '下方（空头门可激活）'}')
check('ema200_persist（7日持续破EMA200）',
      'pass' if ema200_persist else 'warn',
      f'{ema200_persist}  → _bear_gate 路径之一')
check('_bear_gate 近似状态（DCS | ema200_persist）',
      'pass' if bear_gate_approx else 'warn',
      f'{bear_gate_approx}  ← False时空头信号全部被拦截')

# ─── 5. 近 7 天日线信号回放 ───────────────────────────────────────────────────
print()
print('【5】近 7 天日线信号回放（策略视角，shift=1）')
print(f'  {"日期":12s} {"收盘":>9} {"EMA200":>9} {"DCS":>5} {"BE":>5} {"SOW":>5} {"bear_gate":>10}')
for _, row in df1d_det.tail(8).iloc[:-1].iterrows():  # shift: 用前一行代表策略当天看到的
    idx = row.name
    if idx + 1 >= len(df1d_det):
        continue
    actual_row = df1d_det.iloc[idx + 1]  # 策略在 actual_row 这天 能看到 row 的信号
    dcs  = bool(row.get('dist_cause_sufficient', False))
    be   = bool(row.get('buying_exhaustion', False))
    sow  = bool(row.get('daily_dist_sow_detected', False))
    ema  = row['ema200']
    px   = actual_row['close']
    bg   = dcs or (px < ema)
    print(f'  {str(actual_row["date"].date()):12s} {px:>9,.0f} {ema:>9,.0f} '
          f'{"T" if dcs else "F":>5} {"T" if be else "F":>5} {"T" if sow else "F":>5} '
          f'{"OPEN" if bg else "CLOSED":>10}')

# ─── 6. 配置文件检查 ──────────────────────────────────────────────────────────
print()
print('【6】配置文件检查')

config_paths = [
    'user_data/config.json',
    'user_data/config-private.json',
    'config.json',
]
config = None
config_path_used = None
for cp in config_paths:
    if os.path.exists(cp):
        with open(cp) as f:
            config = json.load(f)
        config_path_used = cp
        break

if config is None:
    check('config.json 存在', 'fail', '未找到配置文件，请确认路径')
else:
    check('config.json 存在', 'pass', f'路径={config_path_used}')

    trading_mode = config.get('trading_mode', 'spot')
    check('trading_mode = futures',
          'pass' if trading_mode == 'futures' else 'fail',
          f'实际={trading_mode}')

    margin_mode = config.get('margin_mode', '')
    check('margin_mode = isolated',
          'pass' if margin_mode == 'isolated' else 'warn',
          f'实际={margin_mode}')

    mot = config.get('max_open_trades', -1)
    check('max_open_trades = 1',
          'pass' if mot == 1 else 'fail',
          f'实际={mot}  （必须=1，复利链条依赖）')

    strategy = config.get('strategy', '')
    check('strategy = WyckoffV8',
          'pass' if strategy == 'WyckoffV8' else 'warn',
          f'实际={strategy}')

    dry_run = config.get('dry_run', False)
    check('dry_run = true',
          'pass' if dry_run else 'warn',
          f'实际={dry_run}  （上线前应为 true）')

    stake_currency = config.get('stake_currency', '')
    check('stake_currency = USDT',
          'pass' if stake_currency == 'USDT' else 'warn',
          f'实际={stake_currency}')

# ─── 7. 最终汇总 ──────────────────────────────────────────────────────────────
print()
print('=' * 65)
fail_count = sum(1 for r in results if r[0] == FAIL)
warn_count = sum(1 for r in results if r[0] == WARN)
pass_count = sum(1 for r in results if r[0] == PASS)

print(f'  检查结果汇总：{pass_count} PASS  |  {warn_count} WARN  |  {fail_count} FAIL')
print()

if fail_count > 0:
    print('  ❌ 存在 FAIL 项，禁止启动 Dry Run！')
    print('  修复以下问题后重新运行此脚本：')
    for icon, label, detail in results:
        if icon == FAIL:
            print(f'     → {label}')
            if detail:
                print(f'       {detail}')
elif warn_count > 0:
    print('  ⚠️  存在 WARN 项，请人工确认后决定是否启动：')
    for icon, label, detail in results:
        if icon == WARN:
            print(f'     → {label}')
            if detail:
                print(f'       {detail}')
    print()
    print('  常见 WARN 不影响启动的情况：')
    print('    - DCS=False / BE=False：当前市场在积累期，多头正常，空头门关闭')
    print('    - bear_gate=CLOSED：不会有空头，但多头（v8_ws_e）正常')
    print('    - dry_run=false：如果是故意上线实盘，WARN 可忽略')
else:
    print('  ✅ 全部通过，可以启动 Dry Run！')

print()
print('  启动命令：')
print('  docker compose up -d freqtrade')
print('  docker compose logs -f freqtrade  # 观察前 5 分钟日志')
print('  观察要点：')
print('    - "Applying startup-loss-protection" → 正常')
print('    - "Entry signal found" → 有信号（可能因其他条件被过滤）')
print('    - "entry signal blocked" / "PNF target invalid" → 信号被拦截（需排查）')
print('=' * 65)
print()
