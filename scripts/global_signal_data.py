"""
全球AI产业链信号抓取
美股信号：SMH / TSM / MU
A股/场内ETF信号：516850 / 513500 / 501225 / 513310
场外QDII：限购状态需人工确认
"""

import ast
import json
import sys
import datetime
import urllib.request


def _get(url, headers=None):
    h = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        )
    }
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read()


def fetch_us_stock(ticker, range_="1y"):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range={range_}"
    d = json.loads(_get(url))
    closes = d["chart"]["result"][0]["indicators"]["quote"][0]["close"]
    return [c for c in closes if c is not None]


def fetch_cn_etf(symbol, days=120):
    """新浪财经 A股/场内QDII ETF 日K，symbol 如 sh513300"""
    url = (
        f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php"
        f"/CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={days}"
    )
    raw = _get(url, {"Referer": "https://finance.sina.com.cn/"}).decode("gbk")
    return [float(d["close"]) for d in ast.literal_eval(raw)]


def fetch_nav(fund_code):
    """东方财富：场内ETF最新净值，用于计算溢价率"""
    url = (
        f"https://api.fund.eastmoney.com/f10/lsjz"
        f"?fundCode={fund_code}&pageIndex=1&pageSize=1&startDate=&endDate=&_=1"
    )
    try:
        d = json.loads(_get(url, {
            "Referer": f"https://fund.eastmoney.com/{fund_code}.html",
            "Accept": "application/json, text/javascript",
        }))
        lst = d.get("Data", {}).get("LSJZList", [])
        if lst:
            return float(lst[0]["DWJZ"])
    except Exception:
        pass
    return None


def ma(closes, n):
    return round(sum(closes[-n:]) / n, 4) if len(closes) >= n else None


def vs_ma(closes, n=20):
    m = ma(closes, n)
    if not m:
        return "未知", None, None
    price = closes[-1]
    pct = round((price - m) / m * 100, 2)
    pos = "上方" if price > m else "下方"
    return pos, pct, m


def mom(closes, n):
    if len(closes) < n:
        return None
    return round((closes[-1] - closes[-n]) / closes[-n] * 100, 2)


def etf_status(closes, nav=None):
    """返回场内ETF完整状态，nav 不为 None 时计算溢价率"""
    pos20, pct20, m20 = vs_ma(closes, 20)
    pos60, pct60, m60 = vs_ma(closes, 60)
    peak60 = max(closes[-60:]) if len(closes) >= 60 else max(closes)
    dd60 = round((closes[-1] - peak60) / peak60 * 100, 2)

    # 溢价率
    price = closes[-1]
    premium = round((price - nav) / nav * 100, 2) if nav else None

    # 买入适宜度：溢价优先判断
    if premium is not None and premium > 3:
        suitability = "禁止"
        suit_note   = f"溢价 +{premium}% > 3%，禁止买入"
    elif premium is not None and premium > 1:
        suitability = "偏高"
        suit_note   = f"溢价 +{premium}%，减半买入或等回落"
    elif pos20 == "下方":
        suitability = "等待"
        suit_note   = "价格低于MA20，等待重新站上"
    elif pct20 > 25:
        suitability = "偏高"
        suit_note   = f"距MA20 +{pct20}%，短期过热，谨慎追高"
    elif pct20 > 12:
        suitability = "偏高"
        suit_note   = f"距MA20 +{pct20}%，可小仓位，等回调更佳"
    else:
        suitability = "适宜"
        suit_note   = f"距MA20 +{pct20}%，位置合理"

    return {
        "price":       price,
        "nav":         nav,
        "premium":     premium,
        "ma20":        m20,
        "ma60":        m60,
        "vs_ma20":     pos20,
        "vs_ma20_pct": pct20,
        "dd_60d":      dd60,
        "suitability": suitability,
        "suit_note":   suit_note,
    }


# ── 主快照 ──────────────────────────────────────────────────

def get_snapshot():
    result = {"fetched_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}

    # 美股信号
    for ticker in ["SMH", "TSM", "MU"]:
        print(f"  抓取 {ticker}...")
        try:
            closes = fetch_us_stock(ticker)
            pos, pct, m20 = vs_ma(closes, 20)
            result[ticker] = {
                "price":       round(closes[-1], 2),
                "ma20":        m20,
                "vs_ma20":     pos,
                "vs_ma20_pct": pct,
                "mom_1m":      mom(closes, 20),
                "mom_3m":      mom(closes, 60),
            }
        except Exception as e:
            print(f"    失败: {e}")
            result[ticker] = None

    # 场内ETF / A股ETF（含溢价率）
    cn_etfs = {
        "A516850": ("sh516850", "516850"),
        "A513300": ("sh513300", "513300"),
        "A513500": ("sh513500", "513500"),
        "A501225": ("sh501225", "501225"),
        "A513310": ("sh513310", "513310"),
    }
    for key, (symbol, fund_code) in cn_etfs.items():
        print(f"  抓取 {symbol}...")
        try:
            closes = fetch_cn_etf(symbol)
            print(f"  抓取 {fund_code} 净值...")
            nav = fetch_nav(fund_code)
            result[key] = etf_status(closes, nav)
        except Exception as e:
            print(f"    失败: {e}")
            result[key] = None

    return result


# ── 信号评分 ────────────────────────────────────────────────

def score_signals(s):
    smh = s.get("SMH")
    tsm = s.get("TSM")
    mu  = s.get("MU")

    # US_AI：SMH + TSM 同时在MA20上方
    us_ai, us_notes = 0, []
    if smh and smh["vs_ma20"] == "上方":
        us_ai += 1
        us_notes.append(f"SMH MA20上方 +{smh['vs_ma20_pct']}%")
    if tsm and tsm["vs_ma20"] == "上方":
        us_ai += 1
        us_notes.append(f"TSM MA20上方 +{tsm['vs_ma20_pct']}%")

    # STORAGE：MU 在MA20上方 + 3月动量正
    stor, stor_notes = 0, []
    if mu and mu["vs_ma20"] == "上方":
        stor += 1
        stor_notes.append(f"MU MA20上方 +{mu['vs_ma20_pct']}%")
    if mu and mu.get("mom_3m") and mu["mom_3m"] > 0:
        stor += 1
        stor_notes.append(f"MU 3月动量 +{mu['mom_3m']}%")

    # CN_POWER：516850 在MA20上方 + 60日回撤>-10%
    cn, cn_notes = 0, []
    a516 = s.get("A516850")
    if a516 and a516["vs_ma20"] == "上方":
        cn += 1
        cn_notes.append(f"516850 MA20上方 +{a516['vs_ma20_pct']}%")
    if a516 and a516["dd_60d"] > -10:
        cn += 1
        cn_notes.append(f"60日回撤 {a516['dd_60d']}%，未深度调整")

    return {
        "US_AI":   {"score": us_ai,  "max": 2, "notes": us_notes},
        "STORAGE": {"score": stor,   "max": 2, "notes": stor_notes},
        "CN_POWER":{"score": cn,     "max": 2, "notes": cn_notes},
    }


# ── QDII工具清单 ─────────────────────────────────────────────
# type: listed=场内可追踪  unlisted=场外需人工确认

TOOLS = {
    "US_AI": [
        {"code": "513300", "name": "华夏纳指100 ETF",       "type": "listed",   "snap_key": "A513300",
         "desc": "NVDA/AVGO/TSM，流动性最好"},
        {"code": "501225", "name": "景顺全球半导体芯片LOF", "type": "listed",   "snap_key": "A501225",
         "desc": "覆盖整个SOXX，半导体纯度更高"},
        {"code": "513500", "name": "南方标普500 ETF",        "type": "listed",   "snap_key": "A513500",
         "desc": "EQIX/DLR/ANET/ETN，覆盖更广"},
        {"code": "270042", "name": "广发纳指100联接A",       "type": "unlisted", "snap_key": None,
         "desc": "场外，支付宝可买，查当日限购"},
        {"code": "005698", "name": "华夏全球科技先锋A",      "type": "unlisted", "snap_key": None,
         "desc": "场外，覆盖光互联COHR/LITE+电力基建，查限购"},
    ],
    "STORAGE": [
        {"code": "017730", "name": "嘉实全球产业升级A",      "type": "unlisted", "snap_key": None,
         "desc": "场外，MU/AVGO/MRVL，查当日限购"},
        {"code": "513310", "name": "中韩半导体ETF",          "type": "listed",   "snap_key": "A513310",
         "desc": "含SK Hynix HBM，⚠️ 溢价风险高"},
        {"code": "100055", "name": "富国全球科技互联网A",    "type": "unlisted", "snap_key": None,
         "desc": "场外，2026Q1增配存储/光通信，查限购"},
    ],
    "CN_POWER": [
        {"code": "516850", "name": "中证电力指数ETF",        "type": "listed",   "snap_key": "A516850",
         "desc": "国内AI基建电力受益，场内直接买"},
    ],
}

ICON = {2: "🟢", 1: "🟡", 0: "🔴"}
SUIT_ICON = {"适宜": "✅", "偏高": "⚠️", "等待": "⏳", "禁止": "🚫"}
THEME_NAME = {
    "US_AI":    "美国AI算力/半导体",
    "STORAGE":  "存储/HBM (MU)",
    "CN_POWER": "中国AI基建/电力",
}


def format_output(s, signals):
    lines = [
        "",
        "=" * 56,
        "  全球AI产业链 QDII 买入巡检",
        f"  {s['fetched_at']}",
        "=" * 56,
    ]

    for theme, sig in signals.items():
        sc = sig["score"]
        lines += [
            "",
            f"  {ICON[sc]} {THEME_NAME[theme]}  [{sc}/2]",
        ]
        for n in sig["notes"]:
            lines.append(f"     · {n}")

        if sc == 0:
            lines.append("     → 无信号，不操作")
            continue

        lines.append("     → 可关注工具：")
        for t in TOOLS[theme]:
            snap = s.get(t["snap_key"]) if t["snap_key"] else None

            if t["type"] == "listed" and snap:
                si = SUIT_ICON[snap["suitability"]]
                prem_str = (f"  溢价:{snap['premium']:+.2f}%" if snap["premium"] is not None else "")
                lines.append(
                    f"       {si} [{t['code']}] {t['name']}"
                    f"  ¥{snap['price']}{prem_str}"
                    f"  MA20:{snap['vs_ma20']}{snap['vs_ma20_pct']:+.1f}%"
                    f"  → {snap['suit_note']}"
                )
            else:
                lines.append(f"       📋 [{t['code']}] {t['name']}  {t['desc']}")

    # 原始数据
    lines += [
        "",
        "─" * 56,
        "  美股信号原始数据",
    ]
    for tk in ["SMH", "TSM", "MU"]:
        d = s.get(tk)
        if d:
            lines.append(
                f"  {tk:<4} ${d['price']:<9} MA20上方:{d['vs_ma20_pct']:>+6.1f}%"
                f"   1M:{d['mom_1m']:>+6.1f}%   3M:{d['mom_3m']:>+6.1f}%"
            )

    lines += ["─" * 56, ""]
    return "\n".join(lines)


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("正在抓取数据...")
    s = get_snapshot()
    sig = score_signals(s)
    print(format_output(s, sig))
