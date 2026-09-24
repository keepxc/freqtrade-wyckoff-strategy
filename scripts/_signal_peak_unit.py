#!/usr/bin/env python3
"""
`_parse_signal_peak` 夹具 + 不变式（不装 freqtrade、不连网络）
==============================================================

背景（2026-09-20）：`ai500_score_history_v3.csv` 的 `signal_peak` 列写的是接口原样字符串
**`峰值 88`**，于是分析侧 `float("峰值 88")` → None，派生协变量
`score_over_peak = score / signal_peak` **在全部 15,119 行上恒缺失**，
而脚本只显示「无样本」，不报错 —— 一个分析维度悄悄消失了两周。

本夹具守两件事：
  A. 解析行为正确（含「不猜」边界：抽不到就 None，宁可缺不编）
  B. **该改动不碰决策路径** —— 用 AST 证明 `signal_peak` 在策略里没有任何「被读取」
     的位置（只有 CSV 字段名常量 + 构造行时的键 + 本 helper 的名字）。

运行：python3 scripts/_signal_peak_unit.py   （全通过 → 退出码 0）
"""
import ast
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "user_data", "strategies", "AI500V3ScoreHold.py")
SRC_TEXT = open(SRC, encoding="utf-8").read()
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"   [{detail}]" if detail else ""))


# ---------- A. 摘出方法单独编译（绕开 freqtrade / ccxt / pandas 依赖）----------
tree = ast.parse(SRC_TEXT)
cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
want = {"_parse_signal_peak", "_to_float"}
funcs = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in want]
assert len(funcs) == 2, f"没摘全：{[f.name for f in funcs]}"
mod = ast.Module(body=funcs, type_ignores=[])
ast.fix_missing_locations(mod)
ns = {"re": __import__("re")}      # 与策略模块级 import 对齐（AST 摘取不带 import）
exec(compile(mod, SRC, "exec"), ns)
p = ns["_parse_signal_peak"]

print("=" * 78)
print("A. 解析行为（真实字面量 → 数值）")
print("=" * 78)
cases = [
    ("峰值 88", 88.0),          # ← 实测的真实字面量
    ("峰值 88.5", 88.5),
    ("峰值92", 92.0),           # 无空格变体
    ("88", 88.0),
    ("88.0", 88.0),
    (88, 88.0),
    (88.5, 88.5),
    ("峰值 -3", -3.0),
    ("峰值", None),             # 无数字 → 不猜
    ("", None),
    (None, None),
    (True, None),               # bool 不是峰值（防 True→1.0 的静默错值）
    ([88], None),               # 非 str/数值类型 → 不猜（str([88]) 里也有数字）
    ({"v": 88}, None),
]
for raw, want_v in cases:
    got = p(raw)
    check(f"parse({raw!r}) == {want_v!r}", got == want_v, f"got={got!r}")

print()
print("=" * 78)
print("B. 不变式：只进观测落盘，不进决策路径")
print("=" * 78)

# 不变式 1：所有对 "signal_peak" 字符串的下标读取必须是 0（即没人读它）
reads = []
for node in ast.walk(tree):
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) \
            and node.slice.value == "signal_peak":
        reads.append(node.lineno)
check("策略内不存在 row['signal_peak'] 之类的**读取**点（决策路径未被触碰）",
      reads == [], f"读取行号={reads}")

# 不变式 2：字段常量与构造键仍在（否则 CSV 会变形/丢列）
#   SCORE_HISTORY_FIELDS 是**模块级**常量（不在 class 体内）
fields = next((n for n in tree.body if isinstance(n, ast.Assign)
               and any(getattr(t, "id", None) == "SCORE_HISTORY_FIELDS" for t in n.targets)), None)
assert fields is not None, "找不到 SCORE_HISTORY_FIELDS"
field_names = [e.value for e in fields.value.elts]
check("SCORE_HISTORY_FIELDS 仍含 signal_peak 且列序未变（不触发表头轮转）",
      field_names == ["fetched_at_utc", "symbol", "pair", "score", "signal_peak",
                      "price", "start_price", "start_time", "change_pct_value"],
      f"{field_names}")

# 不变式 3：构造行时确实用了本 helper（防止有人改回 a.get("signal")）
row_call = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
            and getattr(n.func, "attr", None) == "_parse_signal_peak"]
check("构造 score 榜行时走 _parse_signal_peak（没有裸 a.get(\"signal\") 残留）",
      len(row_call) == 1 and 'a.get("signal")' not in SRC_TEXT.replace(
          'self._parse_signal_peak(a.get("signal"))', ""),
      f"调用点 {len(row_call)} 个")

# 不变式 4：ai500_scores 只有写入、没有读取（值出口只有 CSV）
writes = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Attribute)
          and n.attr == "ai500_scores" and isinstance(n.ctx, ast.Store)]
reads_a = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Attribute)
           and n.attr == "ai500_scores" and isinstance(n.ctx, ast.Load)]
check("self.ai500_scores 有写入、0 处读取（该值唯一出口是 CSV）",
      len(writes) >= 1 and reads_a == [], f"写入行={writes} 读取行={reads_a}")

# 不变式 5：import re 已加（否则 helper 运行时 NameError）
check("策略顶部已 import re", "import re" in SRC_TEXT.split("class ")[0])

print()
print("=" * 78)
print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
if FAIL:
    print("失败项：" + "; ".join(FAIL))
    sys.exit(1)
print("全部通过；退出码 0")
sys.exit(0)
