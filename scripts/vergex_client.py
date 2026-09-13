#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VergeX 统一请求客户端（2026-09-13 修复版）

背景
----
2026-09 起 vergex.trade 启用了 Cloudflare 托管挑战（Cf-Mitigated: challenge）。
CF 判的是**整体客户端指纹**，两个条件**缺一不可**（2026-09-13 实测）：

  1. **完整浏览器头**——短 UA（"Mozilla/5.0"、"python-requests/x"、curl 默认 UA、
     空 UA）一律 403 + "Just a moment..." HTML。
  2. **HTTP 客户端必须是 urllib**——⚠️ 这一条最容易被漏掉：`requests` **即使带上
     完全相同的完整头**（默认 / Session / identity-encoding 三种变体都试过）仍然
     403（6007B 挑战页），urllib 同头稳定 200（1470B JSON）。差异来自 TLS/HTTP2
     指纹，不在 header 内容里。

**所以"把 UA 补全就行"是错的结论**——修复前的旧脚本（macd_m8.py / b300_collector.py）
确实都写了 UA = "Mozilla/5.0"，但只补 UA 而继续用 requests 会重新撞 403。
本模块两条都满足，实测 20/20 全部 200。**勿把底层换回 requests。**

本模块提供
----------
1. UA 池轮换 + CF 挑战自动重试（只对**确认是 CF** 的 403 重试，业务错误直接抛）
2. Cloudflare 挑战识别（区分「被墙」和「接口真的挂了」——判据见 _is_cf_challenge）
3. 统一的完整浏览器头 + urllib 传输层（两条件同时满足才能过 CF）
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

# Cloudflare 托管挑战的**正特征**（必须命中其一才判 CF）
#   - 响应头 Cf-Mitigated: challenge —— CF 自己打的标，最可靠
#   - 挑战页 body 特征串
CF_MITIGATED_HEADER = "Cf-Mitigated"
CF_BODY_MARKERS = ("just a moment", "challenges.cloudflare.com", "cf-browser-verification")


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
    def _is_cf_challenge(status: int, body: str, headers=None) -> bool:
        """只在命中 CF 正特征时判「被墙」。

        ⚠️ 曾经的实现是 `命中特征串 or len(body) < 8000`，等价于**所有 403 都算 CF**——
        而实测 CF 挑战页 6007B、正常 JSON 1470B，这个尺寸阈值根本分不开两者。
        后果是限频/权限/标的下架类的真 403 会被烧掉 4 次重试，再报成 CloudflareBlocked，
        调用方（ai500_fetch.py）还会打印"这是 Cloudflare 挑战，不是接口下线"——
        给出一个方向相反的诊断。本模块声明的职责就是**区分被墙与接口真的挂了**，
        宽容的兜底规则恰好取消了这个能力，故删除，宁可漏判也不误判。
        """
        if status != 403:
            return False
        if headers is not None:
            mitigated = headers.get(CF_MITIGATED_HEADER) or ""
            if "challenge" in mitigated.lower():
                return True
        low = body.lower()
        return any(m in low for m in CF_BODY_MARKERS)

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
                if self._is_cf_challenge(e.code, body, e.headers):
                    last_err = CloudflareBlocked(
                        f"Cloudflare 挑战拦截 (HTTP {e.code})，UA={ua[:40]}…"
                    )
                    time.sleep(self.gap * (attempt + 1) + random.uniform(0, 0.4))
                    continue
                # 非 CF 的 403、以及 404/400 等，都是真实业务错误 —— 不重试、不伪装成被墙
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
