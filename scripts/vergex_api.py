#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Vergex Open API 客户端（2026-09-13）

背景：vergex.trade 网页域 2026-09-12 起被 Cloudflare 拦（403 挑战），
      所有程序化访问失效。官方 Open API 域 `openapi.vergex.trade` **不受 CF 拦**，
      用 client_credentials（HTTP Basic）+ resource 换 Bearer token 即可访问。

认证事实（2026-09-13 实测）：
  - 域名：openapi.vergex.trade（api.vergex.trade 仍被 CF 拦）
  - token 端点：https://auth.vergex.trade/oauth/token
  - 认证方式：**HTTP Basic**（client_id:client_secret）+ resource 参数
      ⚠️ 用 form 字段传 client_id/client_secret 会 invalid_client（必须 Basic）
  - resource：https://openapi.vergex.trade/api/v1（写错报 invalid_target）
  - token 有效期 86400s；本模块缓存并自动刷新（提前 300s）
  - 已确认可用的端点：/api/v1/direction-change/leaderboard
  - ⚠️ 未暴露：/data-intelligence/.../signals（404）、trending-category（404，score 榜仅网页端）

凭据文件：~/.hermes/vergex_api_creds.json（600）
token 缓存：~/.hermes/vergex_openapi_token.json（600）
"""
import base64
import json
import os
import time
import urllib.parse
import urllib.request

CRED_PATH = os.path.expanduser("~/.hermes/vergex_api_creds.json")
TOKEN_PATH = os.path.expanduser("~/.hermes/vergex_openapi_token.json")
OPENAPI = "https://openapi.vergex.trade"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
# 代理：优先专用变量，其次复用既有约定（cron 里已注入）
PROXY = (os.environ.get("VERGEX_PROXY") or os.environ.get("B300_PROXY")
         or os.environ.get("LOCAL_PROXY") or "").strip() or None


def _opener():
    if PROXY:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))
    return urllib.request.build_opener()


def _load_creds() -> dict:
    with open(CRED_PATH) as f:
        return json.load(f)


def get_token(force: bool = False) -> str:
    """取 Bearer token（缓存复用，提前 300s 刷新）。"""
    if not force and os.path.exists(TOKEN_PATH):
        try:
            with open(TOKEN_PATH) as f:
                c = json.load(f)
            if c.get("access_token") and c.get("_expires_at", 0) - 300 > time.time():
                return c["access_token"]
        except (json.JSONDecodeError, OSError):
            pass

    creds = _load_creds()
    basic = base64.b64encode(
        f"{creds['client_id']}:{creds['client_secret']}".encode()).decode()
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "scope": "market:read account:read agent:read",
        "resource": creds["resource"],
    }).encode()
    req = urllib.request.Request(
        creds["token_endpoint"], data=body,
        headers={"Authorization": f"Basic {basic}",
                 "Content-Type": "application/x-www-form-urlencoded",
                 "User-Agent": UA})
    with _opener().open(req, timeout=30) as r:
        d = json.loads(r.read().decode())
    if "access_token" not in d:
        raise RuntimeError(f"token endpoint 未返回 access_token: {str(d)[:200]}")
    d["_expires_at"] = time.time() + int(d.get("expires_in", 3600))
    os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
    with open(TOKEN_PATH, "w") as f:
        json.dump(d, f, indent=1)
    os.chmod(TOKEN_PATH, 0o600)
    return d["access_token"]


def get_json(path: str, retries: int = 3):
    """GET openapi 域（path 以 / 开头，如 /api/v1/direction-change/leaderboard）。"""
    last = None
    for i in range(retries):
        try:
            tok = get_token(force=(i > 0))
            req = urllib.request.Request(
                f"{OPENAPI}{path}",
                headers={"Authorization": f"Bearer {tok}", "User-Agent": UA,
                         "Accept": "application/json"})
            with _opener().open(req, timeout=25) as r:
                return json.loads(r.read().decode())
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"GET failed after {retries} retries: {path} ({last})")


if __name__ == "__main__":
    import sys
    t = get_token()
    print(f"[ok] token 前缀 {t[:16]}... 长度 {len(t)}")
    if len(sys.argv) > 1:
        print(json.dumps(get_json(sys.argv[1]), ensure_ascii=False)[:600])
