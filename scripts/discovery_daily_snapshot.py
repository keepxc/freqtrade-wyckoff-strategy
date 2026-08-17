"""
探索性数据采集脚本——非结论，禁止引用为证据。

每日定时拉取 https://stock-map-rust.vercel.app/ 的"主动发现"数据
（discovery-candidates.csv / arxiv-papers.csv / discovery-signals.csv），
按日期存档到本地。该网站本身不提供逐个股的历史打分（discovery-history.csv
只有每日汇总计数），要验证"打分高的股票后续是否真的涨"，必须自己每天
存一份快照，靠 candidates.csv 里自带的 price/quote_timestamp 字段积累
时间序列（今天的候选打分 + 未来某天快照里同一支股票的价格 = 前瞻收益，
不需要额外拉价格数据）。

用户确认口径（2026-08-07）：
  1. 部署在 dry-run 服务器上用系统定时任务（crontab）跑，每日一次，
     不消耗 agent token
  2. candidates/papers/signals 三个文件都存档（papers/signals 是打分
     的论据，不参与验证计算，只为复盘留存）
  3. candidates 只保留 market == "美股" 的行，A股直接丢弃不存
     （bStocks 不支持A股交易，存了也用不上）
  4. 至少积累 4-8 周数据后才做第一次预注册验证，此脚本本身不做任何
     打分有效性的判断，纯采集

用法：
  python scripts/discovery_daily_snapshot.py
crontab 示例（每天 UTC 22:00，即美股收盘后跑，具体时区按服务器本地时间调整）：
  0 22 * * * cd /path/to/freqtrade && python3 scripts/discovery_daily_snapshot.py >> user_data/logs/discovery_snapshot.log 2>&1
"""
import csv
import io
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen, Request

BASE_URL = "https://stock-map-rust.vercel.app"
FILES = {
    "candidates": "discovery-candidates.csv",
    "papers": "arxiv-papers.csv",
    "signals": "discovery-signals.csv",
}
OUT_ROOT = Path(__file__).resolve().parent.parent / "user_data" / "discovery_snapshots"
HEADERS = {"User-Agent": "Mozilla/5.0"}
TIMEOUT = 20


def fetch(path: str) -> str:
    req = Request(f"{BASE_URL}/{path}", headers=HEADERS)
    with urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read().decode("utf-8")


def filter_us_market(csv_text: str) -> str:
    reader = csv.DictReader(io.StringIO(csv_text))
    fieldnames = reader.fieldnames
    rows = [row for row in reader if row.get("market") == "美股"]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue(), len(rows)


def main():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    saved = []

    for key, fname in FILES.items():
        out_dir = OUT_ROOT / key
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{key}_{today}.csv"
        if out_path.exists():
            print(f"[SKIP] {out_path.name} 今日已存在，跳过（避免同日多次运行覆盖）")
            continue
        try:
            text = fetch(fname)
        except Exception as e:
            print(f"[FAIL] 拉取 {fname} 失败: {e}")
            continue

        if key == "candidates":
            text, n = filter_us_market(text)
            note = f"美股行 {n} 条（已剔除A股）"
        else:
            note = "原样存档，不过滤"

        out_path.write_text(text, encoding="utf-8")
        saved.append(f"{out_path.name} ({note})")

    if saved:
        print(f"[OK] {today} 存档完成: " + "; ".join(saved))
    else:
        print(f"[OK] {today} 无新增存档（可能已运行过或全部失败）")


if __name__ == "__main__":
    main()
