#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""门② 观测探针夹具（A 类·纯观测的不变式，2026-09-25）

守三件事：
  A. **行为不变式**：`populate_entry_trend` 的 `enter_long` / `enter_tag` 与
     「独立复算的 mask」逐行一致 —— 探针绝不改变入场裁决。
  B. **落盘范围**：只对当前在 `_entry_state`（top3）内的标的落盘；非 top3 不写文件。
  C. **字段语义**：candle_age_s / state_age_s / gate_open / in_trade / df_rows 取值正确，
     且 `_append_csv` 抛异常时**不影响**入场裁决（观测失败不得改变行为）。

不装 freqtrade、不连网络：用 stubs 注入 freqtrade 子模块后 exec 策略源文件。

运行：.venv/bin/python scripts/_gate_probe_unit.py   （全通过 → 退出码 0）
"""
import os
import sys
import tempfile
import types

import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "user_data", "strategies", "AI500V3ScoreHold.py")
SRC_TEXT = open(SRC, encoding="utf-8").read()
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"   [{detail}]" if detail else ""))


# ---------- stubs：只够 exec 本策略 ----------
fr = types.ModuleType("freqtrade")
ex = types.ModuleType("freqtrade.exchange")
ex.timeframe_to_minutes = lambda tf: {"1h": 60, "4h": 240, "1d": 1440}[tf]


class _Trade:
    OPEN = set()

    @classmethod
    def get_trades_proxy(cls, pair=None, is_open=False, **kw):
        return [object()] if (is_open and pair in cls.OPEN) else []


pe = types.ModuleType("freqtrade.persistence")
pe.Trade = _Trade
st = types.ModuleType("freqtrade.strategy")


class _IStrategy:
    def __init__(self, config):
        self.config = config


st.IStrategy = _IStrategy
sys.modules.update({"freqtrade": fr, "freqtrade.exchange": ex,
                    "freqtrade.persistence": pe, "freqtrade.strategy": st})

ns = {"__name__": "ai500v3_probe_test", "__file__": SRC}
exec(compile(SRC_TEXT, SRC, "exec"), ns)
cls = ns["AI500V3ScoreHold"]
cls._init_binance = lambda self: None          # 不建 ccxt 客户端

probe = tempfile.mktemp(suffix=".csv")
ns["GATE_PROBE_PATH"] = probe                  # 探针写临时文件，不碰真实 data 目录


def make_strategy(entry_state, fetched_at=1000.0, loop_now=None):
    s = cls({"exchange": {"ccxt_config": {"proxies": {}}}})
    s._entry_state = entry_state
    s._entry_state_fetched_at = fetched_at
    s._loop_now = loop_now
    return s


CANDLE_OPEN = pd.Timestamp("2026-09-25 00:00:00", tz="UTC")   # 收盘 = +4h
CLOSE_S = CANDLE_OPEN.timestamp() + 240 * 60


def make_df(symbol_row, n=30, tz=True):
    """合成 analyzed dataframe（末行携带入场判定列）。"""
    d = pd.DataFrame({
        "date": pd.date_range("2026-09-20", periods=n, freq="4h", tz="UTC" if tz else None),
        "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
        "enter_long": 0, "enter_tag": "",
        "score": -1.0, "score_rank": 99, "tradable": 0, "is_zombie": 0,
    })
    # 模拟 populate_indicators 的**标量广播**（生产里 score/rank/tradable/is_zombie
    # 由 _entry_state 单值广播到全部行 —— 这正是「历史行也被标记」的原因）
    for k, v in symbol_row.items():
        d[k] = v
    d["date"] = d["date"].astype("datetime64[ns, UTC]")
    d.loc[d.index[-1], "date"] = CANDLE_OPEN
    return d


def probe_rows(path=probe):
    if not os.path.exists(path):
        return pd.DataFrame()
    return pd.read_csv(path)


def rm_probe():
    for p in (probe,):
        if os.path.exists(p):
            os.unlink(p)


print("=" * 78)
print("A. 行为不变式：探针不得改变 enter_long / enter_tag")
print("=" * 78)
rows = [{"score": 92.0, "score_rank": 1, "tradable": 1, "is_zombie": 0},   # 开门
        {"score": 79.9, "score_rank": 1, "tradable": 1, "is_zombie": 0},   # score 差一点
        {"score": 95.0, "score_rank": 4, "tradable": 1, "is_zombie": 0},   # rank 超
        {"score": 95.0, "score_rank": 1, "tradable": 0, "is_zombie": 0},   # 不可交易
        {"score": 95.0, "score_rank": 1, "tradable": 1, "is_zombie": 1}]   # 僵尸
for i, r in enumerate(rows, 1):
    rm_probe()
    s = make_strategy({"BR": {"rank": r["score_rank"], "score": r["score"],
                              "tradable": r["tradable"], "is_zombie": r["is_zombie"],
                              "reason": "ok"}}, loop_now=CLOSE_S + 42)
    df = make_df(r)
    out = s.populate_entry_trend(df, {"pair": "BR/USDT:USDT"})
    want = int(r["score"] >= 80 and r["score_rank"] <= 3 and r["tradable"] == 1
               and r["is_zombie"] == 0)
    got = int(out["enter_long"].iloc[-1])
    tag = out["enter_tag"].iloc[-1] if want else ""
    got_tag = out["enter_tag"].iloc[-1]
    check(f"A{i} enter_long 与独立复算一致 (score={r['score']}, rank={r['score_rank']}, "
          f"tradable={r['tradable']}, zombie={r['is_zombie']})", got == want, f"want={want} got={got}")
    check(f"A{i} enter_tag 正确", (got_tag == "ai500_score_top3") == bool(want), f"tag={got_tag!r}")
    # score/rank 是标量广播 → 门开时**所有行** enter_long=1（策略原设计，探针不得改变）
    check(f"A{i} 全列与广播复算一致（含历史行）",
          bool((out["enter_long"] == want).all()),
          f"unique={sorted(out['enter_long'].unique())}")
    check(f"A{i} 探针确实落盘 1 行（top3 且末行）", len(probe_rows()) == 1,
          f"rows={len(probe_rows())}")

print()
print("=" * 78)
print("B. 落盘范围：非 top3 标的不得落盘")
print("=" * 78)
rm_probe()
s = make_strategy({"BR": {"rank": 1, "score": 92.0, "tradable": 1, "is_zombie": 0}},
                  loop_now=CLOSE_S + 42)
s.populate_entry_trend(make_df(rows[0]), {"pair": "XYZ/USDT:USDT"})
check("B1 非 top3 不写文件", not os.path.exists(probe))
s.populate_entry_trend(make_df(rows[0]), {"pair": "BR/USDT:USDT"})
check("B2 top3 写文件", len(probe_rows()) == 1)

print()
print("=" * 78)
print("C. 字段语义")
print("=" * 78)
rm_probe()
s = make_strategy({"BR": {"rank": 1, "score": 92.0, "tradable": 1, "is_zombie": 0}},
                  fetched_at=CLOSE_S + 10, loop_now=CLOSE_S + 42)
s.populate_entry_trend(make_df(rows[0]), {"pair": "BR/USDT:USDT"})
r = probe_rows().iloc[0]
check("C1 candle_age_s = 42", abs(r["candle_age_s"] - 42) < 0.01, f"{r['candle_age_s']}")
check("C2 state_age_s = 32", abs(r["state_age_s"] - 32) < 0.01, f"{r['state_age_s']}")
check("C3 gate_open=1 & enter_long=1", r["gate_open"] == 1 and r["enter_long"] == 1)
check("C4 score/rank/tradable/zombie 落盘正确",
      (r["score"], r["score_rank"], r["tradable"], r["is_zombie"]) == (92.0, 1, 1, 0),
      f"{(r['score'], r['score_rank'], r['tradable'], r['is_zombie'])}")
check("C5 df_rows=30", r["df_rows"] == 30, f"{r['df_rows']}")
check("C6 in_trade=0", r["in_trade"] == 0)
# in_trade=1 分支
rm_probe()
_Trade.OPEN = {"BR/USDT:USDT"}
s2 = make_strategy({"BR": {"rank": 1, "score": 92.0, "tradable": 1, "is_zombie": 0}},
                   loop_now=CLOSE_S + 42)
s2.populate_entry_trend(make_df(rows[0]), {"pair": "BR/USDT:USDT"})
check("C7 in_trade=1（有在途仓时）", probe_rows().iloc[0]["in_trade"] == 1)
_Trade.OPEN = set()
# gate 关闭时也应落盘（这正是诊断缺口所需）
rm_probe()
s3 = make_strategy({"BR": {"rank": 1, "score": 79.9, "tradable": 1, "is_zombie": 0}},
                   loop_now=CLOSE_S + 42)
s3.populate_entry_trend(make_df(rows[1]), {"pair": "BR/USDT:USDT"})
r3 = probe_rows().iloc[0]
check("C8 gate 关闭也落盘（gate_open=0, enter_long=0）",
      r3["gate_open"] == 0 and r3["enter_long"] == 0)

print()
print("=" * 78)
print("D. 观测失败不得影响入场裁决")
print("=" * 78)
rm_probe()


def boom(*a, **kw):
    raise OSError("disk full (simulated)")


_append_ref = cls._append_csv
cls._append_csv = staticmethod(boom)     # 方法内 self._append_csv 走类属性查找
s4 = make_strategy({"BR": {"rank": 1, "score": 92.0, "tradable": 1, "is_zombie": 0}},
                   loop_now=CLOSE_S + 42)
out = s4.populate_entry_trend(make_df(rows[0]), {"pair": "BR/USDT:USDT"})
check("D1 _append_csv 抛错时仍返回 dataframe", isinstance(out, pd.DataFrame))
check("D2 且 enter_long 仍为 1（裁决未被影响）", int(out["enter_long"].iloc[-1]) == 1)
cls._append_csv = _append_ref

print()
print("=" * 78)
print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
if FAIL:
    print("失败项: " + ", ".join(FAIL))
print("=" * 78)
rm_probe()
sys.exit(1 if FAIL else 0)
