#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI500 选币榜采集器（VergeX）

数据源: GET https://vergex.trade/trending-category?lang=zh&key=ai500
        —— 公开接口，无需登录、无需 token；2026-09 起受 Cloudflare 托管挑战保护，
           需**完整浏览器头 + urllib 传输层**两者同时满足（只补 UA 而用 requests
           仍 403），故一律经 vergex_client.VergeX 走，详见该模块 docstring。

⚠️ 定位：**人工查看用的 CLI，不是判据来源。** 本脚本 --fresh 输出的
   stale / suspect 字段仅供肉眼扫一眼，**禁止引用为证据**、禁止喂给统计脚本。
   策略侧的僵尸判定以 AI500V3ScoreHold._check_tradable 为唯一真相源（口径不同，
   见下方 freshness_check() 的口径差异说明）。

用法:
    python ai500_fetch.py                    # 抓取并打印当前榜
    python ai500_fetch.py --json             # 仅输出 JSON
    python ai500_fetch.py --top 3            # 只显示前 3
    python ai500_fetch.py --fresh            # 启用数据新鲜度校验（需币安可达）
    python ai500_fetch.py --save             # 追加落盘到 ai500_history.jsonl
    python ai500_fetch.py --diff             # 与上次落盘对比，输出变动

数据新鲜度校验:
    榜内行可能不刷新。原设计的「币安 1h K 复算累计涨幅」口径不可靠，改用
    「接口价 vs 币安现价」一致性作为主判据，详见 freshness_check()。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from vergex_client import VergeX, CloudflareBlocked, UA_POOL

HERE = Path(__file__).resolve().parent
HIST_FILE = HERE / "ai500_history.jsonl"

UA_FULL = UA_POOL[0]     # 币安请求同样需要完整 UA（Cloudflare 无关，但保持一致）

# 仓库约定：采集脚本的代理默认值一律 7890（bot 机器上活着的端口；
# macd_m8.py / AI500V3ScoreHold 同）。10809 是开发机的端口，不能作为默认值。
BINANCE_PROXY = os.environ.get("B300_PROXY", "http://127.0.0.1:7890")
PRICE_TOL_PCT = 1.0      # 接口价 vs 币安现价，差 > 1% → stale（真·僵尸行）
PCT_SUSPECT_PP = 15.0    # 累计涨幅复算差 > 15pp → suspect（**纯展示**，见 freshness_check）


# ------------------------------------------------------------ 币安辅助（可选）
def _binance_get(url: str) -> object:
    op = urllib.request.build_opener(urllib.request.ProxyHandler(
        {"http": BINANCE_PROXY, "https": BINANCE_PROXY}))
    req = urllib.request.Request(url, headers={"User-Agent": UA_FULL})
    with op.open(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def _base_open(pair: str, start_ms: int) -> float | None:
    """取包含 startTime 的那根 1h K 的 open（用 startTime 参数精确定位，不受 1000 根窗口限制）。"""
    url = (f"https://api.binance.com/api/v3/klines?symbol={pair}&interval=1h"
           f"&startTime={max(0, start_ms - 3600_000)}&limit=3")
    kl = _binance_get(url)
    for k in kl:
        if k[0] <= start_ms < k[6]:
            return float(k[1])
    return float(kl[0][1]) if kl else None


def _last_price(pair: str) -> float | None:
    """币安现货最新价。"""
    t = _binance_get(f"https://api.binance.com/api/v3/ticker/price?symbol={pair}")
    return float(t["price"])


def freshness_check(assets: list[dict]) -> list[dict]:
    """数据新鲜度校验 —— **仅供人工查看，不构成判据**。

      1. price_diff_pct —— 接口 price vs 币安现货最新价（都是"当前价"，口径一致）
                          差 > 1%  → stale=True，说明该行未刷新（真·僵尸行）
      2. pct_diff_pp    —— 1h 开盘复算的累计涨幅差（**纯展示**，阈值 15pp）

    ⚠️ 维度 2 的口径已被项目内部证据否定，保留只为肉眼对照，任何结论不得引用它
    ---------------------------------------------------------------------------
    这里比的是**派生百分比**，而 RESEARCH_FRAMEWORK 教训 8 / DRYRUN_STATUS §七 已量化：
        diff_pp ≈ 参考价相对误差 × (1 + 累计涨幅)
    同样的参考价口径差，标的涨得越多被放得越大 → 系统性掐掉涨幅最大的那批
    （实测 corr(diff_pp, 参考价差)=0.955，被拒组里真正"实时价过期"的占 0.0%）。
    本函数另有两处与策略侧不一致，**不要**把两边的数字放在一起比：
        - 参考价取 **1h 开盘**（策略侧已改为 1m 开盘；1h 最多早 59 分钟，
          在快拉标的上就是纯噪声）
        - 数据源是**现货** api.binance.com（策略侧交易的是 core_perp 合约）。
          现货侧缺 8/34 个 score 榜标的（USELESS/BULLA/1000BONK 等无交易对），
          这些行的 --fresh 恒为「无数据」——与 M8 现货→合约切换同源，
          见 DRYRUN_STATUS §八-6。改合约口径是**未决项**，未经拍板不擅自改。
    下面这组实测差值正是上述口径差的表现，不是 VergeX 数据有错：
        ZEC   vx 612.52  vs 币安 1h open 555.89  / 1m open 553.61
        LSK   vx 0.18293 vs 币安 1h open 0.16950 / 1m open 0.18990
        FF    vx 0.14447 vs 币安 1h open 0.13114 / 1m open 0.14142
    唯一真相源：`AI500V3ScoreHold._check_tradable`（1m 开盘直接比价格，
    ZOMBIE_REF_PP=3.0）。要改判据去改那里，不要在本文件里另立一套。
    """
    for a in assets:
        pair = a.get("pair")
        st = a.get("startTime")
        claimed = a.get("changePctValue")
        a.setdefault("stale", None)

        if not pair:
            continue
        # ---- 维度1：现价一致性 ----
        try:
            last = _last_price(pair)
            a["binance_last"] = last
            vx_price = float(str(a.get("price", "")).replace("$", "").replace(",", "") or 0)
            if last and vx_price:
                dp = abs(vx_price - last) / last * 100
                a["price_diff_pct"] = round(dp, 3)
                a["stale"] = dp > PRICE_TOL_PCT
        except urllib.error.HTTPError as e:
            a["source_err"] = f"no_source(HTTP {e.code})"
            continue
        except Exception as e:
            a["source_err"] = str(e)[:60]
            continue

        # ---- 维度2：累计涨幅复算（参考项） ----
        if st and claimed is not None:
            try:
                base = _base_open(pair, int(st) * 1000)
                if base and a.get("binance_last"):
                    rc = (a["binance_last"] / base - 1) * 100
                    a["recomputed_pct"] = round(rc, 2)
                    a["pct_diff_pp"] = round(abs(rc - claimed), 2)
                    a["suspect"] = a["pct_diff_pp"] > PCT_SUSPECT_PP
            except Exception as e:
                a["zombie_err"] = str(e)[:60]
        time.sleep(0.3)
    return assets


# ------------------------------------------------------------------ 展示
def render(d: dict, top: int | None = None) -> str:
    sess = (d.get("marketSession") or {})
    assets = d["category"]["assets"]
    if top:
        assets = assets[:top]
    lines = []
    lines.append(f"AI500 选币榜  |  数据时间 {d.get('generatedAt')}  |  "
                 f"市场 {sess.get('labelZh', '?')}")
    lines.append("-" * 78)
    lines.append(f"{'#':<3}{'币种':<9}{'评分':>7}{'峰值':>6}{'累计涨幅':>12}{'现价':>14}{'状态':>8}")
    lines.append("-" * 78)
    for i, a in enumerate(assets, 1):
        sig = str(a.get("signal") or "").replace("峰值", "")
        if a.get("source_err"):
            tag = "无数据"
        elif a.get("stale"):
            tag = "不刷新"
        elif a.get("suspect"):
            tag = "存疑"
        elif a.get("stale") is False:
            tag = "正常"
        else:
            tag = ""
        lines.append(f"{i:<3}{a['symbol']:<9}{a['score']:>7.2f}{sig:>6}"
                     f"{a.get('change', ''):>12}{a.get('price', ''):>14}{tag:>8}")
    lines.append("-" * 78)
    return "\n".join(lines)


def load_last() -> dict | None:
    if not HIST_FILE.exists():
        return None
    last = None
    with open(HIST_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    last = json.loads(line)
                except json.JSONDecodeError:
                    pass
    return last


def diff_report(cur: dict, prev: dict | None) -> str:
    if not prev:
        return "（无历史快照，本次为首次落盘）"
    cur_syms = [a["symbol"] for a in cur["category"]["assets"]]
    prev_syms = [a["symbol"] for a in prev["category"]["assets"]]
    added = [s for s in cur_syms if s not in prev_syms]
    removed = [s for s in prev_syms if s not in cur_syms]
    out = [f"对比上次快照（{prev.get('generatedAt')}）:"]
    out.append(f"  新进榜: {', '.join(added) if added else '无'}")
    out.append(f"  掉出榜: {', '.join(removed) if removed else '无'}")
    prev_map = {a["symbol"]: a for a in prev["category"]["assets"]}
    for a in cur["category"]["assets"]:
        p = prev_map.get(a["symbol"])
        if p:
            ds = a["score"] - p["score"]
            if abs(ds) >= 0.5:
                arrow = "↑" if ds > 0 else "↓"
                out.append(f"  {a['symbol']:<8} 评分 {p['score']:.2f} → "
                           f"{a['score']:.2f}  {arrow}{abs(ds):.2f}")
    return "\n".join(out)


# ------------------------------------------------------------------ 主流程
def main() -> int:
    ap = argparse.ArgumentParser(description="VergeX AI500 选币榜采集器")
    ap.add_argument("--top", type=int, default=None, help="只显示前 N")
    ap.add_argument("--json", action="store_true", help="仅输出原始 JSON")
    ap.add_argument("--fresh", action="store_true",
                    help="启用数据新鲜度校验（需币安可达；比对现价一致性）")
    ap.add_argument("--save", action="store_true", help="追加落盘 ai500_history.jsonl")
    ap.add_argument("--diff", action="store_true", help="与上次落盘对比")
    ap.add_argument("--lang", default="zh", help="语言 zh/en")
    ap.add_argument("--retries", type=int, default=4, help="403 重试次数")
    args = ap.parse_args()

    vx = VergeX(retries=args.retries)
    try:
        d = vx.ai500(lang=args.lang)
    except CloudflareBlocked as e:
        print(f"[CF-BLOCKED] {e}", file=sys.stderr)
        print("提示：已确认命中 Cloudflare 挑战特征（Cf-Mitigated 头或挑战页），"
              "不是接口下线。检查完整浏览器头 + urllib 两个条件，或降低请求频率后重试。",
              file=sys.stderr)
        return 2
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1

    if args.fresh:
        d["category"]["assets"] = freshness_check(d["category"]["assets"])

    prev = load_last() if (args.diff or args.save) else None

    if args.json:
        print(json.dumps(d, ensure_ascii=False, indent=1))
    else:
        print(render(d, args.top))
        if args.diff:
            print()
            print(diff_report(d, prev))

    if args.save:
        rec = {"fetchedAt": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
               **d}
        with open(HIST_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"\n[落盘] {HIST_FILE.name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
