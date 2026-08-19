"""
探索性——纯采集，无结论，禁止引用为证据。

采集 yss-signal.com（呱呱助手）"水平阻力突破"信号快照。

用途：为"突破信号首警胜率"及"与 AI500 资金流信号结合"的前瞻验证积累数据
      （同 TASK110 discovery_daily_snapshot 先例：先采集，4-8 周后预注册检验）。

数据源：https://ai.yss-signal.com/?data=1 （需 Bearer JWT，见 .token 文件）
关键字段：
  - first_price / first_timestamp：首警价格 / 首警时间戳（毫秒）
  - alert_count：警报次数（首警=1，随价格突破更高阻力位递增）
  - gain：自首警以来的涨幅%
  - vol24h / liquidity_level / zone：24h 成交额 / 流动性分级 / 分区

存储：追加到 user_data/yss_signal_snapshots/breakout_signals.csv
      每行 = 一个信号的一次快照（带 fetched_at），按 (symbol, first_timestamp)
      分组即可重建"首警→加警"完整轨迹。

部署：服务器 crontab 定期执行（建议 10~30 分钟一次，突破信号是事件驱动、
      分钟级变化；也可先 1 小时一次观察）。示例：
      */30 * * * * cd /path/freqtrade && python scripts/yss_signal_snapshot.py

Token：从 user_data/yss_signal_token.txt 读取（该文件已 gitignore，不提交）。
      登录后 JWT 约 7 天有效，过期需重新登录替换文件内容。
"""
import csv
import os
import sys
import time
from datetime import datetime, timezone

import requests

WORKER_URL = "https://ai.yss-signal.com"
TOKEN_FILE = "user_data/yss_signal_token.txt"
OUT_CSV = "user_data/yss_signal_snapshots/breakout_signals.csv"

FIELDS = [
    "fetched_at", "symbol", "price", "first_price", "first_timestamp",
    "first_alert_at", "alert_count", "vol24h", "liquidity_level",
    "zone", "zone_level", "gain", "updated_at", "ai_comment",
]


def load_token() -> str:
    # 优先环境变量，其次文件
    tok = os.environ.get("YSS_SIGNAL_TOKEN")
    if tok:
        return tok.strip()
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, encoding="utf-8") as f:
            return f.read().strip()
    print(f"[FATAL] 找不到 token：请设置环境变量 YSS_SIGNAL_TOKEN 或写入 {TOKEN_FILE}")
    sys.exit(1)


def fetch_breakout(token: str) -> list:
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "Mozilla/5.0",
    }
    url = f"{WORKER_URL}/?data=1&t={int(time.time() * 1000)}"
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("signals") or []


def ms_to_iso(ms) -> str:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return ""


def main() -> None:
    token = load_token()
    try:
        signals = fetch_breakout(token)
    except requests.HTTPError as e:
        # 401/403 多为 token 过期
        print(f"[FATAL] 抓取失败（可能 token 过期）：{e}")
        sys.exit(1)
    except Exception as e:
        print(f"[FATAL] 抓取失败：{e}")
        sys.exit(1)

    fetched_at = datetime.now(timezone.utc).isoformat()
    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    write_header = not os.path.exists(OUT_CSV)

    rows = []
    for s in signals:
        rows.append({
            "fetched_at": fetched_at,
            "symbol": s.get("symbol"),
            "price": s.get("price"),
            "first_price": s.get("first_price"),
            "first_timestamp": s.get("first_timestamp") or s.get("firstTimestamp"),
            "first_alert_at": ms_to_iso(s.get("first_timestamp") or s.get("firstTimestamp")),
            "alert_count": s.get("alert_count") or s.get("alertCount"),
            "vol24h": s.get("vol24h"),
            "liquidity_level": s.get("liquidity_level"),
            "zone": s.get("zone"),
            "zone_level": s.get("zone_level"),
            "gain": s.get("gain"),
            "updated_at": s.get("updated_at") or s.get("updatedAt"),
            "ai_comment": s.get("ai_comment"),
        })

    with open(OUT_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)

    print(f"[{fetched_at}] 采集 {len(rows)} 条突破信号 -> {OUT_CSV}")
    top = sorted(rows, key=lambda r: -(r.get("gain") or 0))[:3]
    for t in top:
        print(f"  {t['symbol']:<10} 首警后涨幅 {t['gain']:+.2f}%  警报次数 {t['alert_count']}  "
              f"流动性[{t['zone']}]")


if __name__ == "__main__":
    main()
