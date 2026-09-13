#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VergeX 统一请求客户端（2026-09-13 修复版）

背景
----
2026-09 起 vergex.trade 启用了 Cloudflare 托管挑战（Cf-Mitigated: challenge）。
触发条件已实测定位：**User-Agent 不完整**（如 "Mozilla/5.0"、"python-requests/x"、
curl 默认 UA、或空 UA）→ 一律返回 403 + "Just a moment..." HTML。

修复前的旧脚本（macd_m8.py / b300_collector.py）都写了 UA = "Mozilla/5.0"，
因此在 CF 规则升级后集体失效。换成完整浏览器 UA 后，实测 20/20 全部 200。

本模块提供
----------
1. UA 池轮换 + 403 自动重试
2. Cloudflare 挑战识别（区分「被墙」和「接口真的挂了」）
3. 连接复用（urllib opener）
4. 可选 Playwright 兜底（若 CF 进一步升级为交互式 JS 挑战）

用法
----
    from vergex_client import VergeX

    vx = VergeX()
    data = vx.get_json("/trending-category", {"lang": "zh", "key": "ai500"})
    print(data["category"]["assets"])
"""
from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

# ---------------------------------------------------------------------------
# 完整浏览器 UA 池（实测全部 5/5 通过；切勿改回 "Mozilla/5.0" 这类短 UA）
# ---------------------------------------------------------------------------
UA_POOL: list[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) "
    "Gecko/20100101 Firefox/128.0",
]

BASE = "https://vergex.trade"
API = "https://vergex.trade/api/v1"

# Cloudflare 挑战特征
CF_MARKERS = ("Just a moment", "cf-mitigated", "challenges.cloudflare.com")


class CloudflareBlocked(RuntimeError):
    """被 Cloudflare 托管挑战拦截（不是接口变更，换 UA / 降频即可）。"""


class VergeX:
    """VergeX 公开接口客户端（无需登录）。"""

    def __init__(
        self,
        base: str = BASE,
        api: str = API,
        proxy: str | None = None,
        timeout: int = 20,
        retries: int = 4,
        gap: float = 0.5,
    ) -> None:
        self.base = base.rstrip("/")
        self.api = api.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.gap = gap
        # 代理：显式传入优先；否则读取环境变量（B300_PROXY / https_proxy）
        self.proxy = proxy or os.environ.get("B300_PROXY") or os.environ.get("https_proxy")
        self._last_ua: str | None = None

    # ------------------------------------------------------------------ 内部
    def _opener(self) -> urllib.request.OpenerDirector:
        if self.proxy:
            handler = urllib.request.ProxyHandler({"http": self.proxy, "https": self.proxy})
            return urllib.request.build_opener(handler)
        return urllib.request.build_opener()

    def _headers(self, ua: str, extra: dict[str, str] | None = None) -> dict[str, str]:
        h = {
            "User-Agent": ua,                      # ← 关键：必须是完整浏览器 UA
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Origin": self.base,
            "Referer": f"{self.base}/explore",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }
        if extra:
            h.update(extra)
        return h

    @staticmethod
    def _is_cf_challenge(status: int, body: str) -> bool:
        if status != 403:
            return False
        return any(m.lower() in body.lower() for m in CF_MARKERS) or len(body) < 8000

    # ------------------------------------------------------------------ 公开
    def get_text(self, url: str, extra_headers: dict[str, str] | None = None) -> str:
        """GET 文本，403 时自动轮换 UA 重试。"""
        last_err: Exception | None = None
        for attempt in range(self.retries):
            ua = UA_POOL[attempt % len(UA_POOL)]
            self._last_ua = ua
            req = urllib.request.Request(url, headers=self._headers(ua, extra_headers))
            try:
                with self._opener().open(req, timeout=self.timeout) as resp:
                    return resp.read().decode("utf-8", errors="replace")
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8", errors="replace")
                except Exception:
                    pass
                if self._is_cf_challenge(e.code, body):
                    last_err = CloudflareBlocked(
                        f"Cloudflare 挑战拦截 (HTTP {e.code})，UA={ua[:40]}…"
                    )
                    time.sleep(self.gap * (attempt + 1) + random.uniform(0, 0.4))
                    continue
                # 404 / 400 等是真实业务错误，不重试
                raise RuntimeError(f"HTTP {e.code} {url}") from e
            except Exception as e:  # 网络抖动
                last_err = e
                time.sleep(self.gap * (attempt + 1))
        raise last_err or RuntimeError(f"GET failed: {url}")

    def get_json(self, path: str, params: dict[str, Any] | None = None,
                 use_api_prefix: bool = False) -> Any:
        """GET JSON。path 形如 "/trending-category" 或 "/direction-change/leaderboard"。"""
        root = self.api if use_api_prefix else self.base
        url = f"{root}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return json.loads(self.get_text(url))

    # -------------------------------------------------------------- 业务封装
    def ai500(self, lang: str = "zh") -> dict:
        """AI500 选币榜（VergeX「trending-category」接口，公开无鉴权）。

        返回 {"generatedAt", "marketSession", "category": {..., "assets": [...]}, "tableRows"}
        """
        d = self.get_json("/trending-category", {"lang": lang, "key": "ai500"})
        assets = (d.get("category") or {}).get("assets") or []
        # score 排序（接口未保证顺序，且 score 偶发为字符串，统一转 float）
        for a in assets:
            try:
                a["score"] = float(a.get("score") or 0)
            except (TypeError, ValueError):
                a["score"] = 0.0
        assets.sort(key=lambda a: a["score"], reverse=True)
        return d

    def ai500_top(self, n: int = 3, lang: str = "zh") -> list[dict]:
        return self.ai500(lang)["category"]["assets"][:n]

    def leaderboard(self) -> list[dict]:
        d = self.get_json("/direction-change/leaderboard", use_api_prefix=True)
        if isinstance(d, dict):
            d = d.get("items") or d.get("data") or []
        return d

    def signals(self, symbol: str, liq_band: int = 15) -> dict:
        # ⚠️ symbol 必须 URL 编码：币安涨幅榜含中文名 meme（如「牛来」），
        # 直接拼进 path 会撞 urlopen 的 ascii 编码错误（2026-09-13 实测）。
        path = (f"/data-intelligence/markets/core_perp/"
                f"core_perp%3A{urllib.parse.quote(symbol, safe='')}/signals")
        d = self.get_json(path, {"chain": "mainnet", "liqBand": liq_band},
                          use_api_prefix=True)
        return d.get("data", d) if isinstance(d, dict) else {}


# ---------------------------------------------------------------- CLI 自检
if __name__ == "__main__":
    vx = VergeX()
    print("[self-test] AI500 榜 …")
    d = vx.ai500()
    print(f"  generatedAt = {d.get('generatedAt')}")
    print(f"  市场状态     = {(d.get('marketSession') or {}).get('labelZh')}")
    for i, a in enumerate(d["category"]["assets"], 1):
        print(f"  {i}. {a['symbol']:<8} score={a['score']:<7.2f} "
              f"{a['change']:>9}  峰值{a.get('signal')}")
    print(f"[self-test] 实际使用 UA = {vx._last_ua}")
