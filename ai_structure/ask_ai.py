# -*- coding: utf-8 -*-
"""把当前双栏图 + 中性提问发给外部图表分析 AI，并归档结果。

接口形态（本项目踩过的坑，务必保留这段注释）
------------------------------------------
· 2026-08 抓取的前端快照里用 res.json() 取 data.reply；
· 2026-09 实测该接口已改为 **SSE 流式**（`data: {"type":"delta","text":"…"}`）。
  直接 json.load 会抛 JSONDecodeError —— **而这一次调用已经扣了额度**。
  因此本脚本同时解析 JSON 与 SSE，并把原始字节落盘，避免结果和额度一起丢。

额度：免费额度按账号每日限额（实测 2 次/日），北京时间 08:00（= 00:00 UTC）重置。
本脚本在调用前先查额度，额度为 0 时直接退出，不浪费请求。
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

IMG = os.path.join(C.OUT_DIR, "chart_dual.png")
RESULT = os.path.join(C.OUT_DIR, "watch_latest.json")
RAW = os.path.join(C.OUT_DIR, "ai_raw_response.txt")
INDEX = os.path.join(C.OUT_DIR, "ai_index.json")

# 提示词版本。改动 PROMPT_* 任何一个字 = 换了前提条件，必须升版本号并补协议修订；
# score.py 只统计 prompt_version == 当前版本的样本（前后不混池）。
PROMPT_VERSION = "v2-20260928"

PROMPT_HEAD = """这是 BTCUSDT 的图：上栏为日线（最近约 14 个月），下栏为 4H（最近约 2 个月）。
数据截至 {as_of} UTC（最后一根已收盘 1h K 线），最新收盘 {close}。
注意：两栏最右侧那根 K 线（当天日线、当前 4H）尚未走完，不要当作完整 K 线解读。
"""

# 仅当 AI_PROMPT_STATE=1 时拼入。默认不拼：前向观察要测「程序与 AI 是否一致」，
# 把程序状态喂给 AI 会让一致率虚高（AI 在复述程序，不是独立判断）。
PROMPT_STATE = """
当前客观状态（由程序按已确认摆动点算出，供你参考，不是结论）：
· 处于{phase}，自最近确认枢轴 {pivot_price} 起 {move}、历时 {days} 天
· 是否已突破上一枢轴：{breakout}
"""

# 方向定义与打分口径逐字对齐：score.py 以「as_of + h 天那根收盘 > 当前收盘」计为涨。
PROMPT_BODY = """
请只基于图上的价格结构回答，正文不超过 300 字：
1. 当前趋势阶段（上升 / 下降 / 区间 / 转换）及依据。
2. 关键支撑位与阻力位。
3. 未来 7 天的判断：**以下栏 4H 为主**，上栏日线只用于确认大背景。给出成立条件与作废价位。
4. 未来 30 天的判断：**以上栏日线为主**。给出成立条件与作废价位。

方向的定义（请严格按此口径，不要用「当前趋势是什么」代替）：
· 多  = 你认为 N 天后的收盘价高于当前收盘 {close} 的概率 > 55%
· 空  = 该概率 < 45%
· 中性 = 该概率在 45%～55% 之间

最后，请在回答的**最后一行**严格按下面格式输出（不要加其他文字）：
<方向> 替换为 多、空、中性 三者之一；<概率> 替换为 0～100 的整数，
表示你认为 7 天后收盘高于 {close} 的概率，且须与 dir_7d 的定义一致。
DIRECTION_JSON: {{"dir_7d": "<方向>", "dir_30d": "<方向>", "p_up_7d": <概率>}}"""

PROMPT_TMPL = PROMPT_HEAD + PROMPT_STATE + PROMPT_BODY


def headers(token=None, json_body=False):
    h = {
        "User-Agent": C.AI_UA,
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Origin": C.AI_ORIGIN,
        "Referer": C.AI_ORIGIN + "/chart-agent.html",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
    }
    if json_body:
        h["Content-Type"] = "application/json"
    if token:
        h["Authorization"] = "Bearer " + token
    return h


def parse_body(raw):
    """同时支持 JSON 与 SSE。返回 (dict, 说明)。"""
    text = raw.decode("utf-8", "replace")
    try:
        return json.loads(text), "json"
    except json.JSONDecodeError:
        pass
    deltas, full, types = [], None, {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        chunk = line[5:].strip()
        if not chunk or chunk == "[DONE]":
            continue
        try:
            ev = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        t = ev.get("type", "?")
        types[t] = types.get(t, 0) + 1
        if t == "delta":
            deltas.append(ev.get("text", ""))
        else:
            for k in ("reply", "text", "content", "message"):
                v = ev.get(k)
                if isinstance(v, str) and len(v) > 20:
                    full = v
    reply = full or "".join(deltas)
    d = {"success": bool(reply), "reply": reply, "streamed": True,
         "event_types": types}
    if types:
        d["model"] = types.get("__model__")
    return d, f"sse({types})"


def call(path, payload=None, token=None, timeout=300, method=None, raw_path=None):
    if C.PROXY:
        op = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": C.PROXY, "https": C.PROXY}))
    else:
        op = urllib.request.build_opener()
    m = method or ("POST" if payload is not None else "GET")
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(C.AI_BASE + path, data=data,
                                 headers=headers(token, data is not None),
                                 method=m)
    try:
        with op.open(req, timeout=timeout) as r:
            raw, ctype, status = r.read(), r.headers.get("Content-Type", ""), r.status
    except urllib.error.HTTPError as e:
        raw, ctype, status = e.read(), e.headers.get("Content-Type", ""), e.code
    if raw_path:
        os.makedirs(os.path.dirname(raw_path), exist_ok=True)
        with open(raw_path, "wb") as fh:
            fh.write(raw)
    d, how = parse_body(raw)
    return status, d, how, ctype, len(raw)


def read_token():
    if not os.path.exists(C.TOKEN_FILE):
        return None
    t = open(C.TOKEN_FILE, encoding="utf-8").read().strip()
    return t or None


def build_prompt():
    """从结构分析结果动态生成提示词。缺结果时退化为通用问法。"""
    if not os.path.exists(RESULT):
        return ("这是 BTCUSDT 的日线与 4H 图。"
                + PROMPT_BODY.format(close="图上最新收盘价"))
    r = json.load(open(RESULT, encoding="utf-8"))
    p = r.get("position", {})
    tmpl = PROMPT_TMPL if C.AI_PROMPT_STATE else PROMPT_HEAD + PROMPT_BODY
    return tmpl.format(
        as_of=r.get("as_of", "?"),
        close=f"{r.get('last_close', 0):,.0f}",
        phase="上行腿" if p.get("phase") == "up" else "下行腿",
        pivot_price=f"{p.get('pivot_price', 0):,.0f}",
        move=f"{p.get('move', 0) * 100:+.1f}%",
        days=f"{p.get('days', 0):.0f}",
        breakout="是" if p.get("breakout") else "否")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", default="manual",
                    help="槽位名（asia / euus / manual），用于归档与幂等")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="同一槽位同日已有结果时仍重调（会消耗额度）")
    args = ap.parse_args()

    os.makedirs(C.OUT_DIR, exist_ok=True)
    tok = read_token()
    if not tok:
        print(f"[中止] 未找到 token：{C.TOKEN_FILE}\n"
              f"        先运行 python3 ai_login.py", file=sys.stderr)
        return 2

    prompt = build_prompt()
    if not os.path.exists(IMG):
        print(f"[中止] 未找到图：{IMG}\n"
              f"        先运行 python3 make_chart.py", file=sys.stderr)
        return 2
    b64 = base64.b64encode(open(IMG, "rb").read()).decode()
    today = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d")
    rid = f"req_{args.slot}_{today}"
    payload = {"message": prompt, "images": ["data:image/png;base64," + b64],
               "history": [], "requestId": rid}

    # 幂等：同一槽位同日已有结果就不再调
    idx = {}
    if os.path.exists(INDEX):
        try:
            idx = json.load(open(INDEX, encoding="utf-8"))
        except json.JSONDecodeError:
            idx = {}
    key = f"{today}_{args.slot}"
    if key in idx and not args.force:
        print(f"[跳过] 槽位 {args.slot} 今日已调用过 → {idx[key]['file']}"
              f"（--force 可强制重调）")
        return 0

    print(f"槽位 {args.slot}   图 {os.path.getsize(IMG):,} 字节   "
          f"请求体 {len(json.dumps(payload).encode()):,} 字节")
    if args.dry_run:
        print("\n[dry-run] 提示词：\n" + prompt + "\n[dry-run] 未发送。")
        return 0

    # ---- 额度检查：为 0 直接退出，不浪费请求
    try:
        _, q, _, _, _ = call("/api/chart-agent/quota", token=tok, timeout=30)
        quota = q.get("quota", q)
        print(f"[额度] {json.dumps(quota, ensure_ascii=False)}")
        if quota.get("remaining") == 0 and not quota.get("credits"):
            print("[跳过] 额度为 0，等下一次重置。", file=sys.stderr)
            return 3
    except Exception as e:
        print(f"[警告] 额度查询失败（继续尝试）：{e}", file=sys.stderr)

    print("[发送] 请求图表分析 …")
    t0 = datetime.datetime.now()
    status, d, how, ctype, nraw = call("/api/chart-agent", payload,
                                       token=tok, timeout=300, raw_path=RAW)
    dt = (datetime.datetime.now() - t0).total_seconds()
    print(f"  HTTP {status}  {dt:.1f}s  {ctype}  {nraw:,} 字节  解析={how}")

    if status != 200 or not d.get("success"):
        err = d.get("error") or d.get("reply") or ""
        print(f"  [!] 未成功：{err[:300]}", file=sys.stderr)
        print(f"  原始报文已存 {RAW}", file=sys.stderr)
        if status == 402:
            print("  → 402：今日免费额度已用完。", file=sys.stderr)
        return 1

    outfile = os.path.join(C.OUT_DIR, f"ai_{today}_{args.slot}.json")
    with open(outfile, "w", encoding="utf-8") as fh:
        json.dump({"as_of": datetime.datetime.now(datetime.UTC).isoformat(),
                   "slot": args.slot, "request_id": rid,
                   "prompt": prompt, "prompt_version": PROMPT_VERSION, "image": os.path.basename(IMG),
                   "elapsed_s": dt, "http_status": status,
                   "parse_mode": how, "response": d},
                  fh, ensure_ascii=False, indent=2)
    idx[key] = {"file": os.path.basename(outfile), "slot": args.slot,
                "at": datetime.datetime.now(datetime.UTC).isoformat()}
    with open(INDEX, "w", encoding="utf-8") as fh:
        json.dump(idx, fh, ensure_ascii=False, indent=2)
    print(f"  已写 {outfile}")

    print("\n" + "=" * 78)
    print(f"槽位 {args.slot}   模型 {d.get('model')}")
    print("=" * 78)
    print(d.get("reply"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
