#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B3.0 爆发线结案统计（探索性）。

问题：币安 24h 涨幅榜 ∩ VergeX 结构分类 里被判为 candidate_minor（核心结构完好、
但 state=degraded）的那批，扫描后 24h/48h 收益是否高于"随便一个涨幅榜币"？

设计：
  · 入场价 = 记录里的 lastPrice（币安现货扫描时刻价，与涨幅榜同源）
  · 前向收益 = 币安现货 1h K 线 close：ts→ts+24h / ts+48h（只取已收盘 bar）
  · 对照组 = 同榜其它 tier；基准 = 同期 BTC（同窗口，起始取 ts 前最后一根收盘）
  · 重叠窗口单独计数（同一币连续上榜 → 有效样本 < 名义 n）
每个币种只拉一次全区间 K 线（避免 1000+ 次请求）。
"""
import collections
import csv
import datetime as dt
import json
import os
import statistics as st
import time
import urllib.request

ROOT = os.path.expanduser('~/freqtrade')
BO = os.path.join(ROOT, 'scripts', 'b300_breakout.jsonl')
OUT_CSV = os.path.join(ROOT, 'research', 'ai500_h1', 'b300_breakout_events.csv')
PROXY = 'http://127.0.0.1:7890'
H = 3600_000


def get_json(url, retries=3):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({'http': PROXY, 'https': PROXY}))
    last = None
    for i in range(retries):
        try:
            with op.open(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}),
                         timeout=25) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            last = e
            time.sleep(1.2 * (i + 1))
    raise RuntimeError(f'{url} -> {last}')


def to_ms(iso):
    s = iso if len(iso) > 16 else iso + ':00'
    d = dt.datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone(dt.timedelta(hours=8)))
    return int(d.timestamp() * 1000)


def fetch_series(sym, start_ms, end_ms):
    """返回 [(open_ms, close)]，分页。失败返回 []。"""
    out, t = [], start_ms
    while t < end_ms:
        url = (f'https://api.binance.com/api/v3/klines?symbol={sym}USDT&interval=1h'
               f'&startTime={t}&endTime={end_ms}&limit=1000')
        try:
            rows = get_json(url)
        except Exception as e:
            print(f'    [warn] {sym}: {str(e)[:70]}')
            return out
        if not rows:
            break
        out += [(r[0], float(r[4])) for r in rows]
        nxt = rows[-1][0] + H
        if nxt <= t:
            break
        t = nxt
        if len(rows) < 1000:
            break
        time.sleep(0.15)
    return out


def close_at(series, target_ms):
    """target 时刻（含）之前最后一根已收盘 1h close。"""
    cand = [c for (o, c) in series if o + H <= target_ms]
    return cand[-1] if cand else None


def main():
    rows = [json.loads(l) for l in open(BO, encoding='utf-8') if l.strip()]
    print(f'爆发线 {len(rows)} 行 / {len({r["ts"][:10] for r in rows})} 个扫描日 '
          f'({min(r["ts"] for r in rows)[:10]} → {max(r["ts"] for r in rows)[:10]})')

    ev = []
    for r in rows:
        sym = r.get('symbol', '')
        if not sym.isascii():
            continue
        e = r.get('lastPrice') or r.get('markPrice')
        ts = to_ms(r['ts'])
        if not e:
            continue
        ev.append({'ts_ms': ts, 'ts': r['ts'], 'symbol': sym, 'tier': r.get('tier'),
                   'pct': r.get('pct'), 'entry': e, 'z': r.get('z'),
                   'gLong': r.get('gLong'), 'gShort': r.get('gShort'),
                   'magnet': r.get('magnet'), 'markPrice': r.get('markPrice')})

    syms = sorted({e['symbol'] for e in ev})
    print(f'币种 {len(syms)} 个，开始拉 K 线（每币一次全区间）…')
    series = {}
    for i, s in enumerate(syms):
        lo = min(e['ts_ms'] for e in ev if e['symbol'] == s) - 2 * H
        hi = max(e['ts_ms'] for e in ev if e['symbol'] == s) + 50 * H
        series[s] = fetch_series(s, lo, hi)
        print(f'  [{i+1}/{len(syms)}] {s:<10} {len(series[s])} 根')
    btc_all = fetch_series('BTC', min(e['ts_ms'] for e in ev) - 2 * H,
                           max(e['ts_ms'] for e in ev) + 50 * H)
    print(f'  BTC 基准 {len(btc_all)} 根')

    for e in ev:
        s = series.get(e['symbol']) or []
        for h in (24, 48):
            c = close_at(s, e['ts_ms'] + h * H)
            b0 = close_at(btc_all, e['ts_ms'])
            b1 = close_at(btc_all, e['ts_ms'] + h * H)
            e[f'ret_{h}h'] = (c / e['entry'] - 1) * 100 if c else None
            e[f'btc_{h}h'] = (b1 / b0 - 1) * 100 if (b0 and b1) else None
            e[f'excess_{h}h'] = (e[f'ret_{h}h'] - e[f'btc_{h}h']
                                 if e[f'ret_{h}h'] is not None and e[f'btc_{h}h'] is not None else None)

    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    cols = ['ts', 'symbol', 'tier', 'pct', 'entry', 'z', 'gLong', 'gShort', 'magnet',
            'ret_24h', 'btc_24h', 'excess_24h', 'ret_48h', 'btc_48h', 'excess_48h']
    with open(OUT_CSV, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction='ignore')
        w.writeheader()
        w.writerows(ev)
    print(f'\n明细已写 {OUT_CSV}（{len(ev)} 行）')

    def summ(sub, label):
        print(f'\n--- {label}  (n={len(sub)}) ---')
        for h in (24, 48):
            a = [r[f'ret_{h}h'] for r in sub if r.get(f'ret_{h}h') is not None]
            x = [r[f'excess_{h}h'] for r in sub if r.get(f'excess_{h}h') is not None]
            if not a:
                print(f'  {h}h: 无样本')
                continue
            win = sum(1 for v in a if v > 0) / len(a) * 100
            line = f'  {h}h 绝对 n={len(a)} 均值 {st.mean(a):+.2f}% 中位 {st.median(a):+.2f}% 胜率 {win:.0f}%'
            if x:
                xw = sum(1 for v in x if v > 0) / len(x) * 100
                line += (f' | 超额vsBTC n={len(x)} 均值 {st.mean(x):+.2f}% '
                         f'中位 {st.median(x):+.2f}% 胜率 {xw:.0f}%')
            print(line)

    tiers = collections.Counter(r['tier'] for r in ev)
    print(f'\n=== tier 分布 === {dict(tiers)}')
    summ([r for r in ev if r['tier'] == 'candidate_minor'], '① candidate_minor 准候选')
    summ([r for r in ev if r['tier'] == 'candidate'], '①b candidate')
    summ([r for r in ev if r['tier'] in ('watch', 'degraded')], '② watch/degraded 同榜对照')
    summ([r for r in ev if r['tier'] == 'no_market'], '③ no_market')
    summ(ev, '④ 全部涨幅榜记录（基准率）')

    cm = [r for r in ev if r['tier'] == 'candidate_minor']
    print(f'\ncandidate_minor 名义 n={len(cm)}；唯一(币,日)={len({(r["symbol"], r["ts"][:10]) for r in cm})}；'
          f'币种={sorted({r["symbol"] for r in cm})}')
    print('明细（准候选逐笔）：')
    for r in sorted(cm, key=lambda x: x['ts']):
        r24 = f"{r['ret_24h']:+.2f}%" if r['ret_24h'] is not None else '  n/a'
        r48 = f"{r['ret_48h']:+.2f}%" if r['ret_48h'] is not None else '  n/a'
        print(f"  {r['ts'][:16]} {r['symbol']:<7} +{r['pct']:.1f}%  24h {r24:>8}  48h {r48:>8}")


if __name__ == '__main__':
    main()
