#!/usr/bin/env python3
"""
OI 异动检测器 — 找"资金涌入但价格没动"的标的
逻辑：OI 涨幅 / 价格涨幅 = ratio，ratio 越大 = 聪明钱越活跃

用法：
  # 单次扫描（无历史数据，用 24h 价格变化估算）
  python oi_anomaly.py

  # 定时扫描（每 5 分钟存快照，计算 OI 变化）
  python oi_anomaly.py --watch --interval 300

  # 只看前 30 名
  python oi_anomaly.py --top 30
"""

import requests
import json
import time
import os
import sys
import argparse
from datetime import datetime, timezone

# ============================================================
# 配置
# ============================================================
PROXY = {'http': 'http://127.0.0.1:7890', 'https': 'http://127.0.0.1:7890'}
OKX_BASE = "https://www.okx.com"
SNAPSHOT_DIR = "/tmp/oi_snapshots"
MIN_OI_USD = 10_000_000  # 最小 OI 1000 万 USD，过滤小币种
RATIO_THRESHOLD = 5.0     # OI变化/价格变化 比率阈值
PRICE_CHANGE_MIN = 0.5    # 最小价格变化（%），避免除以近零

# 稳定币噪音过滤
STABLECOINS = {'USDC', 'BUSD', 'DAI', 'TUSD', 'USDP', 'FRAX', 'USDD'}


def api_get(path, params=None):
    """OKX 公共 API 请求"""
    url = f"{OKX_BASE}{path}"
    r = requests.get(url, params=params or {}, proxies=PROXY, timeout=15)
    data = r.json()
    if data.get('code') != '0':
        raise Exception(f"OKX API error: {data.get('msg', 'unknown')}")
    return data['data']


def get_swap_tickers():
    """获取所有 USDT 本位永续合约的 ticker"""
    data = api_get('/api/v5/market/tickers', {'instType': 'SWAP'})
    results = []
    for item in data:
        inst_id = item['instId']
        if not inst_id.endswith('-USDT-SWAP'):
            continue
        symbol = inst_id.replace('-USDT-SWAP', '')
        if symbol in STABLECOINS:
            continue
        try:
            last = float(item['last'])
            open24 = float(item['open24h'])
            vol24 = float(item['vol24h'])
            price_chg_pct = (last - open24) / open24 * 100 if open24 > 0 else 0
            results.append({
                'symbol': symbol,
                'instId': inst_id,
                'price': last,
                'price_chg_24h': price_chg_pct,
                'vol_24h': vol24,
            })
        except (ValueError, ZeroDivisionError):
            continue
    return results


def get_oi_all():
    """获取所有 USDT 本位永续合约的 OI"""
    data = api_get('/api/v5/public/open-interest', {'instType': 'SWAP'})
    oi_map = {}
    for item in data:
        inst_id = item['instId']
        if not inst_id.endswith('-USDT-SWAP'):
            continue
        symbol = inst_id.replace('-USDT-SWAP', '')
        try:
            oi_usd = float(item['oiUsd'])
            oi_map[symbol] = {
                'oi_usd': oi_usd,
                'oi_contracts': float(item['oi']),
                'oi_ccy': float(item['oiCcy']),
            }
        except (ValueError, KeyError):
            continue
    return oi_map


def load_snapshot():
    """加载最近一次快照"""
    path = os.path.join(SNAPSHOT_DIR, 'latest.json')
    if not os.path.exists(path):
        return None
    with open(path, 'r') as f:
        return json.load(f)


def save_snapshot(tickers, oi_map):
    """保存当前快照"""
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    snapshot = {'ts': ts, 'data': {}}
    for t in tickers:
        sym = t['symbol']
        if sym in oi_map:
            snapshot['data'][sym] = {
                'oi_usd': oi_map[sym]['oi_usd'],
                'price': t['price'],
                'price_chg_24h': t['price_chg_24h'],
                'vol_24h': t['vol_24h'],
            }
    # 保存
    path = os.path.join(SNAPSHOT_DIR, 'latest.json')
    with open(path, 'w') as f:
        json.dump(snapshot, f)
    # 也存一份带时间戳的历史
    hist_path = os.path.join(SNAPSHOT_DIR, f"snapshot_{int(time.time())}.json")
    with open(hist_path, 'w') as f:
        json.dump(snapshot, f)
    return ts


def analyze_with_snapshot(current_data, prev_snapshot):
    """用历史快照计算 OI 变化"""
    results = []
    prev_data = prev_snapshot.get('data', {})
    prev_ts = prev_snapshot.get('ts', 'unknown')

    for sym, cur in current_data.items():
        if sym not in prev_data:
            continue
        prev = prev_data[sym]
        if prev['oi_usd'] <= 0 or prev['price'] <= 0:
            continue

        oi_chg_pct = (cur['oi_usd'] - prev['oi_usd']) / prev['oi_usd'] * 100
        price_chg_pct = (cur['price'] - prev['price']) / prev['price'] * 100

        if cur['oi_usd'] < MIN_OI_USD:
            continue

        # 计算 ratio
        if abs(price_chg_pct) < PRICE_CHANGE_MIN:
            # 价格几乎没动但 OI 在变 → 非常有趣
            ratio = abs(oi_chg_pct) / PRICE_CHANGE_MIN if oi_chg_pct != 0 else 0
            ratio_label = f">{ratio:.1f}"
        else:
            ratio = abs(oi_chg_pct) / abs(price_chg_pct)
            ratio_label = f"{ratio:.1f}"

        results.append({
            'symbol': sym,
            'price': cur['price'],
            'price_chg': price_chg_pct,
            'oi_usd': cur['oi_usd'],
            'oi_chg': oi_chg_pct,
            'ratio': ratio,
            'ratio_label': ratio_label,
            'vol_24h': cur.get('vol_24h', 0),
        })

    return sorted(results, key=lambda x: x['ratio'], reverse=True), prev_ts


def analyze_no_snapshot(current_data):
    """无历史快照时，用 24h 价格变化 + 当前 OI 估算"""
    results = []
    for sym, cur in current_data.items():
        if cur['oi_usd'] < MIN_OI_USD:
            continue
        price_chg = cur['price_chg_24h']
        if abs(price_chg) < PRICE_CHANGE_MIN:
            continue
        # 这里没有 OI 历史，只能用 24h 价格变化做粗筛
        # 真正的 OI 变化需要快照对比
        results.append({
            'symbol': sym,
            'price': cur['price'],
            'price_chg': price_chg,
            'oi_usd': cur['oi_usd'],
            'oi_chg': None,  # 无历史数据
            'ratio': None,
            'ratio_label': 'N/A',
            'vol_24h': cur.get('vol_24h', 0),
        })

    # 按 OI 大小排序（无 ratio 时）
    return sorted(results, key=lambda x: x['oi_usd'], reverse=True), None


def print_results(results, prev_ts=None, top_n=20):
    """格式化输出"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"\n{'='*70}")
    print(f"  🔍 OI 异动扫描  {now}")
    if prev_ts:
        print(f"  📊 对比快照: {prev_ts}")
    else:
        print(f"  ⚠️  无历史快照，用 24h 数据粗筛（首次运行，等下一轮出 ratio）")
    print(f"{'='*70}\n")

    if not results:
        print("  没有发现符合条件的标的")
        return

    # 分类
    bullish = [r for r in results if r['oi_chg'] and r['oi_chg'] > 0 and r['price_chg'] < 3]
    bearish = [r for r in results if r['oi_chg'] and r['oi_chg'] < 0 and r['price_chg'] > -3]

    if bullish:
        print(f"  🟢 资金涌入（OI↑ 价格滞后）— {len(bullish)} 个")
        print(f"  {'Symbol':<12} {'Price':>10} {'Price%':>8} {'OI($M)':>10} {'OI%':>8} {'Ratio':>8} {'Vol24h':>10}")
        print(f"  {'-'*66}")
        for r in bullish[:top_n]:
            oi_m = r['oi_usd'] / 1e6
            print(f"  {r['symbol']:<12} {r['price']:>10.4f} {r['price_chg']:>+7.2f}% {oi_m:>9.1f}M {r['oi_chg']:>+7.2f}% {r['ratio_label']:>8} {r['vol_24h']:>10.0f}")
        print()

    if bearish:
        print(f"  🔴 资金撤离（OI↓ 价格还没跌）— {len(bearish)} 个")
        print(f"  {'Symbol':<12} {'Price':>10} {'Price%':>8} {'OI($M)':>10} {'OI%':>8} {'Ratio':>8}")
        print(f"  {'-'*56}")
        for r in bearish[:top_n]:
            oi_m = r['oi_usd'] / 1e6
            print(f"  {r['symbol']:<12} {r['price']:>10.4f} {r['price_chg']:>+7.2f}% {oi_m:>9.1f}M {r['oi_chg']:>+7.2f}% {r['ratio_label']:>8}")
        print()

    # 其他（无 ratio 的首次扫描）
    others = [r for r in results if r['oi_chg'] is None]
    if others:
        print(f"  📋 OI 排行（首次扫描，等快照出 ratio）— 显示前 {top_n}")
        print(f"  {'Symbol':<12} {'Price':>10} {'24h%':>8} {'OI($M)':>10} {'Vol24h':>10}")
        print(f"  {'-'*50}")
        for r in others[:top_n]:
            oi_m = r['oi_usd'] / 1e6
            print(f"  {r['symbol']:<12} {r['price']:>10.4f} {r['price_chg']:>+7.2f}% {oi_m:>9.1f}M {r['vol_24h']:>10.0f}")
        print()

    # 提示
    ratio_results = [r for r in results if r['ratio'] and r['ratio'] >= RATIO_THRESHOLD]
    if ratio_results:
        print(f"  ⚡ 异动信号（ratio ≥ {RATIO_THRESHOLD}）— 这些是重点观察对象:")
        for r in ratio_results[:5]:
            direction = "🟢 看多" if r['oi_chg'] > 0 else "🔴 看空"
            print(f"     {r['symbol']}: ratio={r['ratio_label']} {direction} OI{r['oi_chg']:+.1f}% Price{r['price_chg']:+.2f}%")
        print()


def main():
    parser = argparse.ArgumentParser(description='OI 异动检测器')
    parser.add_argument('--watch', action='store_true', help='持续监控模式')
    parser.add_argument('--interval', type=int, default=300, help='扫描间隔（秒），默认 300')
    parser.add_argument('--top', type=int, default=20, help='显示前 N 个，默认 20')
    args = parser.parse_args()

    if args.watch:
        print(f"🔄 持续监控模式，每 {args.interval}s 扫描一次")
        print(f"   快照保存在: {SNAPSHOT_DIR}")
        print(f"   Ctrl+C 停止\n")
        while True:
            try:
                run_once(args.top)
                time.sleep(args.interval)
            except KeyboardInterrupt:
                print("\n停止监控")
                break
            except Exception as e:
                print(f"⚠️  扫描出错: {e}")
                time.sleep(30)  # 出错后等 30s 再试
    else:
        run_once(args.top)


def run_once(top_n):
    """执行一次扫描"""
    # 1. 获取数据
    tickers = get_swap_tickers()
    oi_map = get_oi_all()

    # 2. 合并
    current_data = {}
    for t in tickers:
        sym = t['symbol']
        if sym in oi_map:
            current_data[sym] = {
                'oi_usd': oi_map[sym]['oi_usd'],
                'price': t['price'],
                'price_chg_24h': t['price_chg_24h'],
                'vol_24h': t['vol_24h'],
            }

    # 3. 加载历史快照
    prev = load_snapshot()

    # 4. 分析
    if prev:
        results, prev_ts = analyze_with_snapshot(current_data, prev)
    else:
        results, prev_ts = analyze_no_snapshot(current_data)

    # 5. 输出
    print_results(results, prev_ts, top_n)

    # 6. 保存当前快照
    ts = save_snapshot(tickers, oi_map)
    print(f"  💾 快照已保存: {ts}")


if __name__ == '__main__':
    main()
