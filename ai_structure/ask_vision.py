# -*- coding: utf-8 -*-
"""外部 AI 读图（OpenRouter 免费视觉模型）—— ask_ai.py 的替代实现。

与 ask_ai.py 的契约完全一致：写 out/ai_<日期>_<槽位>.json（含 prompt / response.reply），
供 forward_log.py 解析 DIRECTION_JSON。run_session.sh 按 AI_SOURCE 分派到本脚本。

设计要点：
  · 提示词复用 ask_ai.build_prompt()，保证与站点源逐字一致（可比性）。
  · 候选模型列表，429/403/空回复 → 立刻换下一个候选（不原地重试）。
  · max_tokens 默认 4000：免费候选里多个是推理模型，给少了会把预算烧在 reasoning 上，
    HTTP 200 但 reply 为空——必须当失败处理，不能记成"AI 没按格式输出"。
  · 记录响应里的 model / provider / cost：协议规定模型变更须标注，而 OpenRouter 的
    provider 字段正是会变的那一项。
  · 幂等索引按 (日期, 槽位, 源) 记录，站点源与本地源可各自留一条读数。

用法（由 run_session.sh 调用）：
    python ask_vision.py --slot asia
"""
import argparse
import base64
import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

import config as C

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import ask_ai                      # 复用同一份提示词构造（noqa: E402）

RESULT = os.path.join(C.OUT_DIR, "watch_latest.json")
INDEX = os.path.join(C.OUT_DIR, "ai_index.json")
RAW = os.path.join(C.OUT_DIR, "ai_raw_response.txt")
IMG = os.path.join(C.OUT_DIR, "chart_dual.png")
API = "https://openrouter.ai/api/v1/chat/completions"

# 已实测：这两个免费候选真能读图（随机图逐字读对），且 cost=0
DEFAULT_MODELS = ("nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
                  "dots-studio/dots-3-note-preview:free")


def _models():
    v = os.environ.get("VISION_MODELS", "").strip()
    return tuple(m.strip() for m in v.split(",") if m.strip()) if v else DEFAULT_MODELS


def read_key():
    """API key 来源优先级：环境变量 / .env（run_session 已 set -a 载入）→ Hermes config 兜底。"""
    k = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if k:
        return k, "env"
    try:
        cfg = open(os.path.expanduser("~/.hermes/config.yaml"), encoding="utf-8").read()
        m = re.search(r"openrouter:\s*\n\s*api_key:\s*([^\s]+)", cfg)
        if m:
            return m.group(1).strip().strip("'\""), "~/.hermes/config.yaml"
    except OSError:
        pass
    return "", ""


def opener():
    if C.PROXY:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": C.PROXY, "https": C.PROXY}))
    return urllib.request.build_opener()


def call(model, prompt, img_b64, key, op, max_tokens):
    # temperature=0：密集层要测的是「图变了 AI 才翻」，默认温度下的翻转大半是抽样噪声
    body = {"model": model, "max_tokens": max_tokens, "temperature": 0,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url",
                 "image_url": {"url": "data:image/png;base64," + img_b64}}]}]}
    req = urllib.request.Request(API, data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + key,
                                          "Content-Type": "application/json",
                                          "HTTP-Referer": "https://localhost",
                                          "X-Title": "btc-swing-watch"})
    t0 = time.time()
    try:
        with op.open(req, timeout=int(os.environ.get("VISION_TIMEOUT", "300"))) as r:
            raw = r.read()
            return r.status, raw, round(time.time() - t0, 1)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), round(time.time() - t0, 1)
    except Exception as e:                      # 网络/超时：换候选，不原地重试
        return None, f"{type(e).__name__}: {e}".encode(), round(time.time() - t0, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", default="manual")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="同一槽位同日已有本源结果时仍重调")
    args = ap.parse_args()

    os.makedirs(C.OUT_DIR, exist_ok=True)
    if not os.path.exists(RESULT):
        print(f"[中止] 未找到 {RESULT}，先跑 swing_watch.py", file=sys.stderr)
        return 2
    if not os.path.exists(IMG):
        print(f"[中止] 未找到图：{IMG}，先跑 make_chart.py", file=sys.stderr)
        return 2

    prompt = ask_ai.build_prompt()
    if args.dry_run:
        print(f"[dry-run] 源=openrouter  候选={_models()}")
        print(f"[dry-run] 提示词长度 {len(prompt)}，末行：")
        print(prompt.strip().splitlines()[-1])
        print("[dry-run] 未发送。")
        return 0

    key, src = read_key()
    if not key:
        print("[中止] 没有 OpenRouter API key：把 OPENROUTER_API_KEY 写进 .env，"
              "或确认 ~/.hermes/config.yaml 里有 providers.openrouter.api_key", file=sys.stderr)
        return 2
    print(f"OpenRouter key 来源：{src}")

    today = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d")
    key_id = f"{today}_{args.slot}"
    idx = {}
    if os.path.exists(INDEX):
        try:
            idx = json.load(open(INDEX, encoding="utf-8"))
        except json.JSONDecodeError:
            idx = {}
    prev = idx.get(key_id) or {}
    if prev.get("source") == "openrouter" and not args.force:
        print(f"[跳过] 槽位 {args.slot} 今日已用本源调用过 → {prev.get('file')}"
              f"（--force 可强制重调）")
        return 0

    img_b64 = base64.b64encode(open(IMG, "rb").read()).decode()
    op = opener()
    max_tokens = int(os.environ.get("VISION_MAX_TOKENS", "4000"))
    winner = None
    for model in _models():
        print(f"[发送] {model}  图 {os.path.getsize(IMG):,} 字节  max_tokens={max_tokens}")
        status, raw, dt = call(model, prompt, img_b64, key, op, max_tokens)
        text = raw.decode("utf-8", "replace")
        open(RAW, "w", encoding="utf-8").write(text)
        if status != 200:
            print(f"  [!] HTTP {status}  {dt}s  {text[:200]}", file=sys.stderr)
            continue
        try:
            d = json.loads(text)
            reply = (d["choices"][0]["message"].get("content") or "").strip()
            finish = d["choices"][0].get("finish_reason")
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            print(f"  [!] 响应结构异常：{e}", file=sys.stderr)
            continue
        used = d.get("usage") or {}
        print(f"  HTTP 200  {dt}s  model={d.get('model')}  provider={d.get('provider')}"
              f"  cost={used.get('cost')}  tokens={used.get('prompt_tokens')}"
              f"+{used.get('completion_tokens')}  finish={finish}")
        if not reply:
            # 推理模型把预算烧在 reasoning 上会走到这里：算失败，换候选
            print("  [!] 回复为空（推理 token 吃光预算？）→ 换下一个候选", file=sys.stderr)
            continue
        if finish == "length":
            # 被 max_tokens 截断：末行 DIRECTION_JSON 大概率丢了。这是调用失败，
            # 不能落到 forward_log 里记成「AI 没按格式输出」（失败率会算错维度）
            print("  [!] 回复被截断（finish_reason=length）→ 换下一个候选", file=sys.stderr)
            continue
        winner = (model, d, reply, dt, status, finish)
        break

    if not winner:
        print("[失败] 所有候选都没拿到有效回复。", file=sys.stderr)
        return 1

    model, d, reply, dt, status, finish = winner
    outfile = os.path.join(C.OUT_DIR, f"ai_{today}_{args.slot}.json")
    with open(outfile, "w", encoding="utf-8") as fh:
        json.dump({"as_of": datetime.datetime.now(datetime.UTC).isoformat(),
                   "slot": args.slot, "source": "openrouter",
                   "request_id": f"or_{args.slot}_{today}",
                   "prompt": prompt, "prompt_version": ask_ai.PROMPT_VERSION, "image": os.path.basename(IMG),
                   "elapsed_s": dt, "http_status": status,
                   "parse_mode": "openrouter",
                   "model": d.get("model"), "provider": d.get("provider"),
                   "usage": d.get("usage"), "finish_reason": finish,
                   "temperature": 0,
                   "response": {"reply": reply, "model": d.get("model"),
                                "provider": d.get("provider"), "success": True}},
                  fh, ensure_ascii=False, indent=2)
    idx[key_id] = {"file": os.path.basename(outfile), "slot": args.slot,
                   "source": "openrouter", "model": d.get("model"),
                   "provider": d.get("provider"),
                   "at": datetime.datetime.now(datetime.UTC).isoformat()}
    with open(INDEX, "w", encoding="utf-8") as fh:
        json.dump(idx, fh, ensure_ascii=False, indent=2)
    print(f"  已写 {outfile}")

    print("\n" + "=" * 78)
    print(f"槽位 {args.slot}   源 openrouter   模型 {d.get('model')}   提供方 {d.get('provider')}")
    print("=" * 78)
    print(reply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
