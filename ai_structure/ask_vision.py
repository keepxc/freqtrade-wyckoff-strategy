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
import forward_log as FL           # 复用 parse_ai_dirs 做「末行是否合规」校验（noqa: E402）

RESULT = os.path.join(C.OUT_DIR, "watch_latest.json")
INDEX = os.path.join(C.OUT_DIR, "ai_index.json")
RAW = os.path.join(C.OUT_DIR, "ai_raw_response.txt")
IMG = os.path.join(C.OUT_DIR, "chart_dual.png")
# 逐次调用明细（成功/失败都记，追加）。全部失败时 ai_*.json 不会生成，这里是唯一留痕；
# 也是「每个候选累计 30 次调用后回看候选顺序」的数据源
ATTEMPT_LOG = os.path.join(C.OUT_DIR, "ai_attempts.jsonl")
# 模型根本没给出回答（基础设施侧）——不能算进「模型守不守格式」
INFRA_NOTES = ("http", "struct")
API = "https://openrouter.ai/api/v1/chat/completions"

# 已实测：这两个免费候选真能读图（随机图逐字读对），且 cost=0
#
# 顺序依据 2026-09-28 全天实测（v2 提示词 + temperature=0）：
#   nemotron 从 14:00Z 起变成「间歇性故障」——连续 5 次响应结构异常
#   （其中一次是响应体在 ~1100 字节处被切断：Expecting value: line 201 char 1100），
#   但 14:31Z 又成功过一次 160s，所以是退化而非死亡 → 降级为备胎；
#   dots-3 同期 4 次调用 3 次合规（52~62s，另 1 次撞 max_tokens 截断），
#   且同一张图两次交付结果完全一致（多/多 p=60）→ 提到首位。
# 每次调用的 model/provider 都逐行落盘，候选顺序变化可追溯，不是黑箱。
DEFAULT_MODELS = ("dots-studio/dots-3-note-preview:free",
                  "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free")


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


def try_once(model, prompt, img_b64, key, op, max_tokens, call_fn=None):
    """调用一次，并跑完所有「这次回复能不能用」的检查。

    返回 (kind, payload)：
      ("ok",  (model, d, reply, dt, status, finish))
      ("bad", (note, dt, status))，note ∈ http / struct / empty / trunc / format

    format 是新增的一条：HTTP 200、非空、未截断，但末行没有合规的 DIRECTION_JSON。
    2026-09-28 dense_12 就是这种情况——以前会直接落进 forward_log 记成「AI 没按格式
    输出」（算解析失败），现在改为「再识别一次」。
    """
    call_fn = call_fn or call
    status, raw, dt = call_fn(model, prompt, img_b64, key, op, max_tokens)
    text = raw.decode("utf-8", "replace")
    open(RAW, "w", encoding="utf-8").write(text)
    if status != 200:
        print(f"  [!] HTTP {status}  {dt}s  {text[:200]}", file=sys.stderr)
        return "bad", ("http", dt, status)
    try:
        d = json.loads(text)
        reply = (d["choices"][0]["message"].get("content") or "").strip()
        finish = d["choices"][0].get("finish_reason")
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        print(f"  [!] 响应结构异常：{e}", file=sys.stderr)
        return "bad", ("struct", dt, status)
    used = d.get("usage") or {}
    print(f"  HTTP 200  {dt}s  model={d.get('model')}  provider={d.get('provider')}"
          f"  cost={used.get('cost')}  tokens={used.get('prompt_tokens')}"
          f"+{used.get('completion_tokens')}  finish={finish}")
    if not reply:
        print("  [!] 回复为空（推理 token 吃光预算？）", file=sys.stderr)
        return "bad", ("empty", dt, status)
    if finish == "length":
        print("  [!] 回复被截断（finish_reason=length）", file=sys.stderr)
        return "bad", ("trunc", dt, status)
    if FL.parse_ai_dirs(reply) is None:
        print("  [!] 末行没有合规的 DIRECTION_JSON（模型没守格式）", file=sys.stderr)
        return "bad", ("format", dt, status)
    return "ok", (model, d, reply, dt, status, finish)


def run_rounds(models, tries, prompt, img_b64, key, op, max_tokens, call_fn=None):
    """候选轮询 + 失败再识别一轮。返回 (winner, attempts)。

    winner 为 None 表示所有尝试都不合规（照旧记失败，不会无限试）。
    attempts 逐次落盘：检查点的解析成功率判据用「交付结果」，但首轮合规率必须
    单独可查——否则重试就把「AI 不守格式」洗白了。
    """
    attempts = []
    for rnd in range(1, max(1, tries) + 1):
        if rnd > 1:
            print(f"[重试] 第 {rnd} 轮识别（上一轮没有合规回复）", file=sys.stderr)
        for model in models:
            print(f"[发送] r{rnd} {model}  图 {os.path.getsize(IMG):,} 字节  "
                  f"max_tokens={max_tokens}")
            kind, payload = try_once(model, prompt, img_b64, key, op, max_tokens, call_fn)
            if kind == "ok":
                m, d, reply, dt, status, finish = payload
                attempts.append({"round": rnd, "model": m, "parse_ok": 1, "note": "ok",
                                 "http_status": status,
                                 "provider": d.get("provider"), "finish_reason": finish,
                                 "elapsed_s": round(dt, 1)})
                return payload, attempts
            note, dt, status = payload
            attempts.append({"round": rnd, "model": model, "parse_ok": 0, "note": note,
                             "http_status": status, "elapsed_s": round(dt, 1)})
    return None, attempts


def summarize(attempts):
    """把 attempts 拆成两个口径，避免把上游故障记成「模型不守格式」。

    first_fail     ：首次尝试的失败类型（http/struct/empty/trunc/format），首次成功为 ""
    first_resp_ok  ：首个「模型真的回答了」（非 http/struct）的尝试是否合规；
                     全程没拿到过模型回答时为 ""（不可判定，不计入格式合规率分母）
    """
    first_fail = "" if not attempts or attempts[0]["parse_ok"] else attempts[0].get("note", "")
    first_resp_ok = next((a["parse_ok"] for a in attempts
                          if a["parse_ok"] or a.get("note") not in INFRA_NOTES), "")
    return first_fail, first_resp_ok


def log_attempts(run_key, slot, attempts, path=None):
    path = path or ATTEMPT_LOG
    at = datetime.datetime.now(datetime.UTC).isoformat()
    with open(path, "a", encoding="utf-8") as fh:
        for a in attempts:
            fh.write(json.dumps({"run_key": run_key, "slot": slot, "at": at,
                                 "prompt_version": ask_ai.PROMPT_VERSION, **a},
                                ensure_ascii=False) + "\n")


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
    # 4000 会把 dots-3 的推理 token 卡死（实测 3 次里 1 次 completion 正好撞 4000
    # → finish_reason=length → 判失败）。它的成功调用 completion 是 3516/3624/3507，
    # 紧贴上限，所以给 6000 留余量；免费档 cost=0，加大不花钱。
    max_tokens = int(os.environ.get("VISION_MAX_TOKENS", "6000"))
    # VISION_TRIES=2：首轮 + 至多 1 次重试（用户 2026-09-28 指令「失败就再识别一次」）
    tries = max(1, int(os.environ.get("VISION_TRIES", "2")))
    winner, attempts = run_rounds(_models(), tries, prompt, img_b64, key, op, max_tokens)
    log_attempts(key_id, args.slot, attempts)
    first_fail, first_resp_ok = summarize(attempts)
    if not winner:
        print(f"[失败] {len(attempts)} 次尝试都没有合规回复（上限 {tries} 轮）。",
              file=sys.stderr)
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
                   "attempt_count": len(attempts),
                   "first_parse_ok": attempts[0]["parse_ok"] if attempts else 0,
                   "first_fail": first_fail,
                   "first_resp_ok": first_resp_ok,
                   "attempts": attempts,
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
