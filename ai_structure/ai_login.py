# -*- coding: utf-8 -*-
"""登录外部平台换取 JWT，写入 .token（约 7 天有效，剩余 <24h 自动续期）。

凭据来源优先级：环境变量 AI_EMAIL / AI_PASSWORD（推荐，见 .env.example）→
命令行 --email/--password。**不要把凭据写进代码或提交到版本库。**

Cloudflare 会按 UA/指纹拦截裸 urllib（error code 1010），所以必须带浏览器请求头。
"""
import argparse
import base64
import datetime
import json
import os
import sys
import urllib.error
import urllib.request

import config as C

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def headers(json_body=False):
    h = {
        "User-Agent": C.AI_UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Origin": C.AI_ORIGIN,
        "Referer": C.AI_ORIGIN + "/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
    }
    if json_body:
        h["Content-Type"] = "application/json"
    return h


def opener():
    if C.PROXY:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": C.PROXY, "https": C.PROXY}))
    return urllib.request.build_opener()


def jwt_exp(tok):
    """解析 JWT 的 exp（毫秒时间戳），失败返回 None。不打印 token 本身。"""
    try:
        p = tok.split(".")[1]
        p += "=" * (-len(p) % 4)
        pay = json.loads(base64.urlsafe_b64decode(p))
        e = pay.get("exp")
        if isinstance(e, (int, float)):
            if e > 1e11:            # 毫秒
                e /= 1000
            return datetime.datetime.fromtimestamp(e, datetime.UTC)
    except Exception:
        pass
    return None


def check_existing():
    if not os.path.exists(C.TOKEN_FILE):
        return None
    tok = open(C.TOKEN_FILE, encoding="utf-8").read().strip()
    if not tok:
        return None
    exp = jwt_exp(tok)
    now = datetime.datetime.now(datetime.UTC)
    # token 约 7 天有效：剩余不足 TOKEN_RENEW_H 小时就提前续期，
    # 避免在两次排班之间过期导致 AI 调用 401。解析不出 exp 的一律重登。
    if exp and exp - datetime.timedelta(hours=C.TOKEN_RENEW_H) > now:
        return tok, exp
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", default=C.AI_EMAIL)
    ap.add_argument("--password", default=C.AI_PASSWORD)
    ap.add_argument("--force", action="store_true", help="即使现有 token 未过期也重新登录")
    args = ap.parse_args()

    if not args.force:
        cur = check_existing()
        if cur:
            print(f"[跳过] 现有 token 有效期至 {cur[1]:%Y-%m-%d %H:%M} UTC，仍可用。")
            return 0

    if not args.email or not args.password:
        print("[中止] 缺少凭据。请设置环境变量 AI_EMAIL / AI_PASSWORD，"
              "或用 --email/--password 传入。", file=sys.stderr)
        return 2

    body = json.dumps({"email": args.email,
                       "password": args.password}).encode()
    req = urllib.request.Request(C.AI_LOGIN, data=body,
                                 headers=headers(True), method="POST")
    try:
        with opener().open(req, timeout=60) as r:
            d = json.load(r)
    except urllib.error.HTTPError as e:
        print(f"[失败] HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}",
              file=sys.stderr)
        return 1
    except Exception as e:
        print(f"[失败] {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    if not d.get("success") or not d.get("token"):
        print(f"[失败] {json.dumps(d, ensure_ascii=False)[:400]}", file=sys.stderr)
        return 1

    with open(C.TOKEN_FILE, "w", encoding="utf-8") as fh:
        fh.write(d["token"])
    os.chmod(C.TOKEN_FILE, 0o600)
    exp = jwt_exp(d["token"])
    print(f"[成功] token 已写入 {C.TOKEN_FILE}"
          + (f"，有效期至 {exp:%Y-%m-%d %H:%M} UTC" if exp else ""))

    # 顺带报一下额度
    try:
        q = urllib.request.Request(C.AI_QUOTA, headers={
            **headers(False), "Authorization": "Bearer " + d["token"]},
            method="GET")
        with opener().open(q, timeout=30) as r:
            qd = json.load(r)
        print("[额度] " + json.dumps(qd.get("quota", qd), ensure_ascii=False))
    except Exception as e:
        print(f"[警告] 额度查询失败：{e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
