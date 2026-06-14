#!/usr/bin/env python3
"""
V8 Dry Run 快速状态脚本
用法: python3 dryrun_status.py
输出: 紧凑状态报告（<10行）
"""
import sqlite3, os, subprocess, sys
from datetime import datetime, timezone, timedelta

FREQTRADE = "/home/gyan/freqtrade"
DB = f"{FREQTRADE}/user_data/tradesv3.v8dryrun.sqlite"
LOG = f"{FREQTRADE}/logs/dryrun.log"
FEATHER = f"{FREQTRADE}/user_data/data/binance/futures/BTC_USDT_USDT-1h-futures.feather"

def get_feather_latest():
    try:
        import pandas as pd
        df = pd.read_feather(FEATHER)
        df['date'] = pd.to_datetime(df['date'], utc=True)
        last = df.iloc[-1]
        now = datetime.now(timezone.utc)
        lag = (now - last['date']).total_seconds() / 3600
        return last['date'], last['close'], len(df), lag
    except:
        return None, None, None, None

def get_btc_price():
    try:
        import urllib.request, json
        req = urllib.request.Request(
            'https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT',
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            return float(json.loads(r.read())['price'])
    except:
        return None

def get_diag():
    """Returns: enter_short, entry_funnel, wave_analyzer, daily_info, ema200_persist, sm_v2"""
    try:
        with open(LOG) as f:
            lines = f.readlines()
        def ts_key(l):
            return l[:23]
        lines_sorted = sorted(lines, key=ts_key, reverse=True)
        enter_short_lines = [l for l in lines_sorted if '[V8-DIAG]' in l and 'enter_short:' in l]
        entry_funnel_lines = [l for l in lines_sorted if '[V8-DIAG]' in l and 'entry funnel:' in l]
        wave_analyzer_lines = [l for l in lines_sorted if '[V8-DIAG]' in l and 'WaveAnalyzer' in l]
        daily_lines = [l for l in lines_sorted if '[V8-DIAG]' in l and 'daily:' in l]
        persist_lines = [l for l in lines_sorted if '[V8-DIAG]' in l and 'TASK14:' in l]
        sm_lines = [l for l in lines_sorted if '[V8-DIAG]' in l and 'TASK13:' in l]
        gate_lines = [l for l in lines_sorted if '[V8-DIAG]' in l and 'TASK23:' in l]
        enter_short = enter_short_lines[0].split('enter_short:')[1].strip() if enter_short_lines else None
        entry_funnel = entry_funnel_lines[0].split('entry funnel:')[1].strip() if entry_funnel_lines else None
        wave_analyzer = wave_analyzer_lines[0].split('WaveAnalyzer')[1].strip() if wave_analyzer_lines else None
        daily_info = daily_lines[0].split('daily:')[1].strip() if daily_lines else None
        ema200_persist = persist_lines[0].split('TASK14:')[1].strip() if persist_lines else None
        sm_v2 = sm_lines[0].split('TASK13:')[1].strip() if sm_lines else None
        fti_gate = gate_lines[0].split('TASK23:')[1].strip() if gate_lines else None
        return enter_short, entry_funnel, wave_analyzer, daily_info, ema200_persist, sm_v2, fti_gate
    except:
        return None, None, None, None, None, None, None

def get_regime():
    """Read current regime phase from events"""
    try:
        import json
        runs = sorted([d for d in os.listdir(f'{FREQTRADE}/user_data/events/dry_run') 
                       if d.startswith('run_') and os.path.isdir(f'{FREQTRADE}/user_data/events/dry_run/{d}')])
        if not runs:
            return None
        latest = runs[-1]
        rg = f'{FREQTRADE}/user_data/events/dry_run/{latest}/regimes.jsonl'
        if not os.path.exists(rg):
            return None
        with open(rg) as f:
            last_line = f.readlines()[-1]
        d = json.loads(last_line)
        return d.get('macro_phase', ''), d.get('market_env', ''), d.get('daily_gate_score', '')
    except:
        return None, None, None

def get_trades():
    try:
        conn = sqlite3.connect(DB)
        open_t = conn.execute('SELECT id, is_short, open_rate, stop_loss, enter_tag FROM trades WHERE is_open=1').fetchall()
        closed = conn.execute('SELECT COUNT(*), SUM(CASE WHEN close_profit>0 THEN 1 ELSE 0 END), ROUND(SUM(close_profit)*100,2) FROM trades WHERE is_open=0').fetchone()
        conn.close()
        return open_t, closed
    except:
        return [], (0, 0, 0.0)

def get_process_info():
    try:
        r = subprocess.run(['systemctl', '--user', 'status', 'freqtrade-dryrun', '--no-pager'],
                          capture_output=True, text=True)
        for line in r.stdout.split('\n'):
            if 'Active:' in line:
                return line.strip()
        return None
    except:
        return None

def main():
    ts = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M CST')

    # Feather data
    ft_date, ft_close, ft_rows, ft_lag = get_feather_latest()
    ft_str = f"{ft_date.strftime('%m-%d %H:%M UTC')}" if ft_date else "N/A"
    ft_lag_str = f"{ft_lag:.1f}h lag" if ft_lag else ""

    # BTC price
    btc = get_btc_price()
    btc_str = f"{btc:,.0f}" if btc else "N/A"

    # DIAG (7 fields: enter_short, entry_funnel, wave_analyzer, daily, ema200_persist, sm_v2, fti_gate)
    es, ef, wa, daily, persist, sm, gate = get_diag()

    # Regime
    phase, env, gate_score = get_regime()

    # Trades
    open_t, closed = get_trades()

    # Process
    proc = get_process_info()

    # Output compact format
    print(f"=== Dry Run === [{ts}]")
    if proc:
        proc_short = proc.replace('Active: ', '').strip()
        print(f"【进程】{proc_short[:70]}")
    print(f"【数据】{ft_str} ({ft_rows}行) {ft_lag_str}")
    print(f"【BTC】{btc_str}")
    if phase:
        print(f"【Phase】{phase} | env={env} | gate={gate_score}")
    print(f"【持仓】{len(open_t)} | 已平 {closed[0]}笔 | 胜率 {closed[1]}/{closed[0]} | 总损益 {closed[2]:.2f}%")
    for t in open_t:
        print(f"  Trade#{t[0]}: {'Short' if t[1] else 'Long'} @{t[2]:.0f} SL={t[3]:.0f} {t[4]}")
    if es:
        print(f"【DIAG】{es}")
    if ef:
        print(f"【漏斗】{ef[:80]}")
    if wa:
        print(f"【Wave】{wa}")
    if daily:
        print(f"【日线】{daily}")
    if persist:
        print(f"【persist】{persist} | sm_v2={sm if sm else 'N/A'} | fti_gate={gate if gate else 'N/A'}")


if __name__ == '__main__':
    main()