# -*- coding: utf-8 -*-
"""ask_vision.py「失败再识别一次」策略的确定性测试。

不靠运气等真实模型违规：用一个脚本化的假 HTTP 层，逐次喂预置响应，
断言重试轮数、换候选顺序、first_parse_ok 与 attempts 是否正确。

跑法： .venv/bin/python selftest_vision.py      （期望末行「全部通过」）
"""
import datetime
import json
import os
import sys
import tempfile

import ask_ai          # noqa: F401  仅为让 ask_vision 的 import 链一致
import ask_vision as V

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

FAIL = []
GOOD = '1. 趋势：上升。\nDIRECTION_JSON: {"dir_7d": "多", "dir_30d": "中性", "p_up_7d": 60}'
BAD_FMT = '1. 趋势：上升。\n2. 支撑 80,000。\n多 60'
BAD_STRUCT = '{"error": {"message": "upstream temporarily rate-limited"}}'


def check(name, cond, extra=""):
    print(f"  {'✓' if cond else '✗'} {name}{('  ' + extra) if extra else ''}")
    if not cond:
        FAIL.append(name)


def fake_call(script, log):
    """script: 每次调用依次取的响应，(status, raw_text)；用完重复最后一个。"""
    def _call(model, prompt, img_b64, key, op, max_tokens):
        i = len(log)
        status, text = script[i] if i < len(script) else script[-1]
        log.append({"model": model, "status": status})
        return status, text.encode("utf-8"), 1.0
    return _call


def ok200(text):
    return (200, json.dumps({"model": "fake/model", "provider": "FakeProv",
                             "usage": {"cost": 0, "prompt_tokens": 10, "completion_tokens": 20},
                             "choices": [{"finish_reason": "stop",
                                          "message": {"content": text}}]}))


def run(script, tries=2, models=("A", "B")):
    log = []
    win, att = V.run_rounds(models, tries, "p", "b64", "k", None, 4000,
                            call_fn=fake_call(script, log))
    return win, att, log


def main():
    # 隔离副作用：别覆盖真实 ai_raw_response.txt
    V.RAW = os.path.join(tempfile.mkdtemp(), "raw.txt")
    # run_rounds 会打印图大小：给个假图，不依赖 out/ 下有真实产物
    V.IMG = os.path.join(tempfile.mkdtemp(), "chart.png")
    open(V.IMG, "wb").write(b"x")

    print("失败再识别一次（run_rounds）")

    # 1) 首轮候选1 格式违规 → 候选2 合规：不进入第二轮
    win, att, log = run([ok200(BAD_FMT), ok200(GOOD)])
    check("首轮违规 → 换候选后成功，共 2 次", win and len(log) == 2, f"实际 {len(log)} 次")
    check("首轮不合规被如实记录", att[0]["parse_ok"] == 0 and att[0]["note"] == "format")
    check("最终交付合规", att[-1]["parse_ok"] == 1)

    # 2) 首轮两个候选都异常 → 第二轮候选1 合规
    # 注意：模拟「无 choices」必须把错误体当原始响应体；(200, BAD_STRUCT) 不能包 ok200()
    win, att, log = run([(200, BAD_STRUCT), (500, "boom"), ok200(GOOD)])
    check("整轮失败 → 再识别一轮，共 3 次", win and len(log) == 3, f"实际 {len(log)} 次")
    check("第二次尝试记 struct、第三次记 http",
          att[0]["note"] == "struct" and att[1]["note"] == "http")
    check("轮次编号正确", [a["round"] for a in att] == [1, 1, 2])

    # 3) 截断 / 空回复同样触发重试
    win, att, log = run([ok200("半句话"), ok200(""), ok200(GOOD)])
    bad_notes = [a.get("note") for a in att if a["parse_ok"] == 0]
    check("空回复也触发重试", win and bad_notes == ["format", "empty"], str(bad_notes))

    trunc = (200, json.dumps({"model": "m", "usage": {}, "choices": [
        {"finish_reason": "length", "message": {"content": GOOD}}]}))
    win, att, log = run([trunc, ok200(GOOD)])
    check("finish_reason=length 即使内容合规也重试",
          win and att[0]["note"] == "trunc" and len(log) == 2)

    # 4) 全部不合规 → 照旧失败，且不无限试（次数 = 候选数 × 轮数）
    win, att, log = run([ok200(BAD_FMT)], tries=2)
    check("一直违规就照旧记失败", win is None)
    check("尝试次数封顶 = 候选数 × 轮数", len(log) == 4, f"实际 {len(log)}")
    check("全部尝试都有留痕", len(att) == 4 and all(a["parse_ok"] == 0 for a in att))

    # 5) 首轮就合规 → 不重试
    win, att, log = run([ok200(GOOD)])
    check("一次就对时只调 1 次", win and len(log) == 1)
    check("first_parse_ok 语义可用", att[0]["parse_ok"] == 1)

    # 6) tries=1 时不重试（可配置回旧行为）
    win, att, log = run([ok200(BAD_FMT)], tries=1)
    check("VISION_TRIES=1 → 退回不重试", win is None and len(log) == 2, f"实际 {len(log)}")

    # 7) 解析口径：末行合规与否由 parse_ai_dirs 判定（含 v1 兼容）
    import forward_log as FL
    check("合规 v2 末行可解析", FL.parse_ai_dirs(GOOD) is not None)
    check("无 JSON 的回复判为不合规", FL.parse_ai_dirs(BAD_FMT) is None)

    print("\n首轮口径拆分（summarize）")

    # 8) 上游故障不算模型不守格式：struct → 候选2 合规
    win, att, log = run([(200, BAD_STRUCT), ok200(GOOD)])
    ff, fr = V.summarize(att)
    check("首轮失败类型记为 struct", ff == "struct", ff)
    check("上游故障后模型首答合规 → first_resp_ok=1", fr == 1, repr(fr))

    # 9) http 之后模型首答就违规 → first_resp_ok=0
    win, att, log = run([(429, "rl"), ok200(BAD_FMT), ok200(GOOD)])
    ff, fr = V.summarize(att)
    check("http 后首个真实回答违规 → first_resp_ok=0", ff == "http" and fr == 0,
          f"{ff} {fr!r}")

    # 10) 首次成功 → first_fail 为空
    win, att, log = run([ok200(GOOD)])
    check("首次成功 → first_fail 空、first_resp_ok=1", V.summarize(att) == ("", 1))

    # 11) 全程只有上游故障 → 不可判定，不进格式合规率分母
    win, att, log = run([(503, "x")])
    check("全程上游故障 → first_resp_ok 不可判定", V.summarize(att) == ("http", ""))

    print("\n逐次明细落盘（log_attempts）")

    # 12) 全部失败时明细也要落盘（ai_*.json 不会生成，这是唯一留痕）
    p = os.path.join(tempfile.mkdtemp(), "att.jsonl")
    win, att, log = run([ok200(BAD_FMT)], tries=2)
    V.log_attempts("20260928_x", "x", att, path=p)
    rows = [json.loads(line) for line in open(p, encoding="utf-8")]
    check("全部失败也写满 4 行明细", win is None and len(rows) == 4, f"实际 {len(rows)}")
    check("明细含模型/失败类型/run_key",
          all(r["model"] in ("A", "B") and r["note"] == "format"
              and r["run_key"] == "20260928_x" for r in rows))
    win, att, log = run([ok200(GOOD)])
    V.log_attempts("20260928_y", "y", att, path=p)
    rows = [json.loads(line) for line in open(p, encoding="utf-8")]
    check("追加写不覆盖", len(rows) == 5 and rows[-1]["note"] == "ok")

    print()
    if FAIL:
        print(f"失败 {len(FAIL)} 项：" + "、".join(FAIL))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
