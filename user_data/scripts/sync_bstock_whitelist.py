#!/usr/bin/env python3
"""
sync_bstock_whitelist.py —— 自动同步币安 bStock 白名单

原理：拉 Binance exchangeInfo → 筛出所有 *B 后缀的 USDT 交易对（bStock 正股代币）
→ 更新 config_bstocks_dryrun.json 的 exchange.pair_whitelist → 重启 bstocks bot。

币安会不断上新 bStock（目前 76 支，可能继续增长），硬编码白名单必然过时。
本脚本让白名单自动跟随币安上新，零手动维护。

用法：
  python3 sync_bstock_whitelist.py            # 同步并重启
  python3 sync_bstock_whitelist.py --dry      # 只打印将写入的白名单，不写文件不重启
  python3 sync_bstock_whitelist.py --min-vol 500000   # 最小日成交额过滤（USDT）

cron 建议（每天 09:30 美股开盘前同步一次）：
  30 9 * * * cd /home/gyan/freqtrade && .venv/bin/python user_data/scripts/sync_bstock_whitelist.py >> user_data/logs/sync_bstock_whitelist.log 2>&1
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.request

CONFIG_PATH = "/home/gyan/freqtrade/user_data/config_bstocks_dryrun.json"
EXCHANGE_INFO_URL = "https://api.binance.com/api/v3/exchangeInfo"
PROXY = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}


def fetch_bstocks(min_volume: float = 0.0) -> list[dict]:
    """拉 exchangeInfo，返回所有 bStock 交易对（含 24h 成交量过滤）。"""
    req = urllib.request.Request(EXCHANGE_INFO_URL, headers={"User-Agent": "Mozilla/5.0"})
    proxy_handler = urllib.request.ProxyHandler(PROXY)
    opener = urllib.request.build_opener(proxy_handler)
    with opener.open(req, timeout=20) as resp:
        data = json.loads(resp.read().decode())

    symbols = []
    for s in data.get("symbols", []):
        if s.get("status") != "TRADING":
            continue
        if s.get("quoteAsset") != "USDT":
            continue
        base = s.get("baseAsset", "")
        # bStock 特征：base 名以 B 结尾（PYPLB/MUB/SNDKB/SPYB...），排除 BNB/DGB/ARB 等原生 B 尾币
        if not base.endswith("B"):
            continue
        # 原生币排除（BNB 是平台币，不是 bStock；DGB/ARB/CKB/SHIB/TRB/YFI/BB 等是原生 B 尾币）
        native_b = {"BNB", "DGB", "ARB", "CKB", "SHIB", "TRB", "YFI", "BB", "YB"}
        if base in native_b:
            continue
        symbols.append(s)

    # 成交量过滤（exchangeInfo 不带 volume，需要 ticker；简单起见靠 24h 成交额 API）
    if min_volume > 0:
        ticker_req = urllib.request.Request(
            "https://api.binance.com/api/v3/ticker/24hr", headers={"User-Agent": "Mozilla/5.0"})
        with opener.open(ticker_req, timeout=20) as resp:
            tickers = {t["symbol"]: float(t["quoteVolume"] or 0) for t in json.loads(resp.read().decode())}
        symbols = [s for s in symbols if tickers.get(s["symbol"], 0) >= min_volume]

    return symbols


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true", help="只打印不写文件不重启")
    parser.add_argument("--min-vol", type=float, default=0.0, help="最小日成交额 USDT")
    args = parser.parse_args()

    # 1. 拉取 bStock
    bstocks = fetch_bstocks(args.min_vol)
    pairs = sorted([f"{s['baseAsset']}/USDT" for s in bstocks])
    print(f"[sync] 发现 {len(pairs)} 支 bStock")

    # 2. 读当前配置（blacklist 过滤，dry 模式同样应用——保证预览与真实一致）
    cfg = json.load(open(CONFIG_PATH))
    blacklist = set(cfg.get("exchange", {}).get("pair_blacklist", []))
    pairs = [p for p in pairs if p not in blacklist]
    if blacklist:
        print(f"[sync] 应用 blacklist 过滤后: {len(pairs)} 支")

    if args.dry:
        print("\n".join(pairs))
        return

    old_wl = cfg.get("exchange", {}).get("pair_whitelist", [])
    old_set = set(old_wl)
    new_set = set(pairs)

    added = sorted(new_set - old_set)
    removed = sorted(old_set - new_set)

    if not added and not removed:
        print(f"[sync] 白名单无变化（{len(pairs)} 支），跳过重启")
        return

    print(f"[sync] 新增 {len(added)}: {added}")
    print(f"[sync] 移除 {len(removed)}: {removed}")

    # 3. 更新配置（写过滤后的列表，保持顺序稳定）
    final_pairs = sorted(new_set)
    cfg["exchange"]["pair_whitelist"] = final_pairs
    json.dump(cfg, open(CONFIG_PATH, "w"), indent=4, ensure_ascii=False)
    print(f"[sync] 已写入 {len(final_pairs)} 支到 {CONFIG_PATH}")

    # 3.5 下载新增 pair 的数据（新币无数据 bot 会跳过）
    if added:
        print(f"[sync] 下载 {len(added)} 支新增 pair 数据...")
        cmd = [
            ".venv/bin/python", "-m", "freqtrade", "download-data",
            "--config", CONFIG_PATH,
            "--timeframe", "1d",
            "--timerange", "20250101-",
            "--pairs", ",".join(added),
        ]
        r = subprocess.run(cmd, cwd="/home/gyan/freqtrade", capture_output=True, text=True, timeout=600)
        if r.returncode == 0:
            print(f"[sync] ✅ 新增数据下载完成")
        else:
            print(f"[sync] ⚠️ 数据下载部分失败: {r.stderr[-300:] if r.stderr else 'unknown'}")

    # 4. 重启 bot
    print("[sync] 重启 freqtrade-bstocks.service...")
    r = subprocess.run(["systemctl", "--user", "restart", "freqtrade-bstocks.service"],
                       capture_output=True, text=True)
    if r.returncode == 0:
        print("[sync] ✅ 重启成功")
    else:
        print(f"[sync] ❌ 重启失败: {r.stderr}")
        sys.exit(1)


if __name__ == "__main__":
    main()
