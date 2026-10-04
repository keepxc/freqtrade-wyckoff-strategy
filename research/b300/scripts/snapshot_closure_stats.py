#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B3.0 主流快照线结案统计（探索性）。

本地 feather 只覆盖到 2026-09-06 ⇒ 现成的 b300_validate.py 算不出前向收益。
本脚本**复用** b300_validate 的门槛判定与统计逻辑（只替换 K 线来源为币安 API），
不复制实现——判据必须与冻结版本一致。
"""
import importlib.util
import os
import sys
import json
import time
import urllib.request

import pandas as pd

ROOT = os.path.expanduser('~/freqtrade')
PROXY = 'http://127.0.0.1:7890'

spec = importlib.util.spec_from_file_location(
    'b300v', os.path.join(ROOT, 'scripts', 'b300_validate.py'))
V = importlib.util.module_from_spec(spec)
spec.loader.exec_module(V)

_cache = {}


def api_4h(symbol, _klines_dir=None):
    if symbol in _cache:
        return _cache[symbol]
    op = urllib.request.build_opener(urllib.request.ProxyHandler({'http': PROXY, 'https': PROXY}))
    url = (f'https://api.binance.com/api/v3/klines?symbol={symbol}USDT&interval=1h'
           f'&startTime={int(pd.Timestamp("2026-08-25", tz="UTC").timestamp()*1000)}'
           f'&limit=1000&endTime={int(time.time()*1000)}')
    try:
        with op.open(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}),
                     timeout=25) as r:
            rows = json.loads(r.read().decode())
    except Exception as e:
        print(f'  [warn] {symbol}: {str(e)[:70]}')
        rows = []
    if not rows:
        _cache[symbol] = pd.DataFrame()
        return _cache[symbol]
    df = pd.DataFrame({'date': pd.to_datetime([r[0] for r in rows], unit='ms', utc=True),
                       'open': [float(r[1]) for r in rows],
                       'high': [float(r[2]) for r in rows],
                       'low': [float(r[3]) for r in rows],
                       'close': [float(r[4]) for r in rows],
                       'volume': [float(r[5]) for r in rows]}).set_index('date')
    o4 = df.resample('4h').agg({'open': 'first', 'high': 'max', 'low': 'min',
                                'close': 'last', 'volume': 'sum'}).dropna(subset=['close'])
    _cache[symbol] = o4
    return o4


V.load_4h = api_4h          # 只替换 K 线来源，门槛/统计逻辑保持原样


def main():
    snaps = V.load_snapshots(os.path.join(ROOT, 'scripts', 'b300_snapshots.jsonl'))
    print(f'快照 {len(snaps)} 行，'
          f'{min(s["ts"] for s in snaps)[:16]} → {max(s["ts"] for s in snaps)[:16]}')
    ev = V.extract_events(snaps)
    ev = V.attach_forward(ev, None)
    V.summarize(ev)
    out = os.path.join(ROOT, 'research', 'ai500_h1', 'b300_snapshot_events.csv')
    ev.to_csv(out, index=False, encoding='utf-8-sig')
    print(f'\n明细已写 {out}')

    # 补充：过门槛事件的样本量与 MDE 粗略估计（供判读用，不是判据）
    import numpy as np
    for name, sub in (('过门槛', ev[ev['has_data'] & ev['gate_ok']]),
                      ('过门槛且4h收阳', ev[ev['has_data'] & ev['gate_ok'] & ev['candle_bull']])):
        m = sub['mn_ret_24h'].dropna()
        if len(m) > 1:
            sd = float(np.std(m, ddof=1))
            mde = 2.8 * sd / (len(m) ** 0.5)          # 80% power, 5% two-sided
            print(f'  [{name}] n={len(m)} 中性24h均值 {m.mean()*100:+.2f}% '
                  f'sd={sd*100:.2f}% → MDE(80%功效) ≈ {mde*100:.2f}%')


if __name__ == '__main__':
    main()
