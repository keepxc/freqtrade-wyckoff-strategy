"""出场裁决夹具测试 —— 覆盖 2026-09-19 的 A 类修复（出场移入 custom_exit）。

两块内容：
  A. `custom_exit` 的真值表（首根「收盘严格晚于入场」的收阴 4h K 才平）
  B. **回归证明**：复刻 freqtrade 的信号冲突裁决，对照旧/新两版出场实现，
     证明旧版会同时吞掉入场与出场，新版不会。

不连交易所、不跑 freqtrade 引擎；策略对象用 `object.__new__` 绕开 `__init__`
（后者会初始化 ccxt 客户端）。
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd

# Windows 控制台默认 gbk，✅/❌ 会炸；Linux bot 机无此问题。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "user_data", "strategies"))

import importlib.util

SRC = os.path.join(REPO, "user_data", "strategies", "AI500V3ScoreHold.py")
_spec = importlib.util.spec_from_file_location("ai500v3", SRC)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
Strategy = _mod.AI500V3ScoreHold

UTC = timezone.utc
T0 = datetime(2026, 9, 19, 8, 0, tzinfo=UTC)   # 某根 4h K 的开盘时刻


def make_strategy(last_candle: dict | None):
    """造一个只有 dp.get_analyzed_dataframe 可用的策略实例。"""
    s = object.__new__(Strategy)
    s.timeframe = "4h"
    if last_candle is None:
        df = pd.DataFrame(columns=["date", "open", "close"])
    else:
        df = pd.DataFrame([last_candle])
    s.dp = type("DP", (), {"get_analyzed_dataframe": staticmethod(
        lambda pair, tf: (df, None))})()
    return s


class FakeTrade:
    def __init__(self, open_date_utc):
        self.open_date_utc = open_date_utc
        self.pair = "ZEC/USDT:USDT"


def run_custom_exit(candle, entry_at, now=None):
    s = make_strategy(candle)
    return s.custom_exit("ZEC/USDT:USDT", FakeTrade(entry_at),
                         now or T0 + timedelta(hours=4), 100.0, 0.0)


# --------------------------------------------------------------- A. 真值表
BEAR = {"date": pd.Timestamp(T0), "open": 100.0, "close": 98.0}
BULL = {"date": pd.Timestamp(T0), "open": 100.0, "close": 102.0}
DOJI = {"date": pd.Timestamp(T0), "open": 100.0, "close": 100.0}

# 该 K 收盘 = T0+4h。入场早于它 → 算「入场后」；入场晚于/等于它 → 不算。
BEFORE = T0 + timedelta(hours=1)      # 入场发生在这根 K 之内 → 该 K 收盘晚于入场 ✅
AFTER = T0 + timedelta(hours=4, minutes=1)   # 入场在该 K 收盘之后 → 不算

CASES = [
    ("阴线 + 收盘晚于入场",        BEAR, BEFORE, "4h_bearish_candle"),
    ("阴线 + 入场那根 K（收盘不晚于入场）", BEAR, AFTER,  None),
    ("阳线 + 收盘晚于入场",        BULL, BEFORE, None),
    ("平盘 close==open（非阴线）", DOJI, BEFORE, None),
    ("dataframe 为空",             None, BEFORE, None),
]

fails = 0
print("=== A. custom_exit 真值表 ===")
for name, candle, entry_at, want in CASES:
    got = run_custom_exit(candle, entry_at)
    ok = got == want
    fails += not ok
    print(f"{'✅' if ok else '❌'} {name:<38} → {got!r:<22} 期望 {want!r}")

# 边界：入场恰好等于该 K 收盘时刻 → 不算（要求「严格晚于」）
got = run_custom_exit(BEAR, T0 + timedelta(hours=4))
ok = got is None
fails += not ok
print(f"{'✅' if ok else '❌'} {'阴线 + 入场恰在收盘同一时刻（严格性边界）':<38} → {got!r:<22} 期望 None")

# D6 守卫：末根尚未收盘（current_time 早于 candle_close）→ 一律不出场
got = run_custom_exit(BEAR, BEFORE, now=T0 + timedelta(hours=2))
ok = got is None
fails += not ok
print(f"{'✅' if ok else '❌'} {'阴线 + 末根尚未收盘（D6 守卫）':<38} → {got!r:<22} 期望 None")

# D6 守卫不得误伤：恰好收盘那一刻应正常出场
got = run_custom_exit(BEAR, BEFORE, now=T0 + timedelta(hours=4))
ok = got == "4h_bearish_candle"
fails += not ok
print(f"{'✅' if ok else '❌'} {'阴线 + 恰好收盘那一刻（守卫不误伤）':<38} → {got!r:<22} 期望 '4h_bearish_candle'")

# --------------------------------------------------------------- B. 回归证明
print("\n=== B. 回归：freqtrade 信号冲突裁决（复刻 strategy/interface.py） ===")
print("    入场 L1372: enter_long == 1 and not any([exit_long, enter_short])")
print("    出场 L1462: if exit_ and not enter:  → EXIT_SIGNAL\n")


def freqtrade_verdict(enter_long, exit_long):
    """返回 (会不会入场, 会不会按出场信号平仓)。"""
    return (enter_long == 1 and not exit_long), (bool(exit_long) and not enter_long)


def old_exit_long(candle):
    """旧实现 populate_exit_trend：close<open 整段广播。"""
    return int(candle["close"] < candle["open"])


def new_exit_long(candle):
    """新实现：恒 0（出场改由 custom_exit 承担）。"""
    s = make_strategy(candle)
    df = s.populate_exit_trend(pd.DataFrame([candle]), {"pair": "ZEC/USDT:USDT"})
    return int(df.iloc[-1]["exit_long"])


# 持仓/候选标的仍在 top3 且 score>=80 → enter_long 恒为 1（标量广播）
ENTER_LONG = 1
SCEN = [
    ("阴线 K + 标的仍在榜", BEAR),
    ("阳线 K + 标的仍在榜", BULL),
]
for name, candle in SCEN:
    o_in, o_out = freqtrade_verdict(ENTER_LONG, old_exit_long(candle))
    n_in, _ = freqtrade_verdict(ENTER_LONG, new_exit_long(candle))
    n_out = run_custom_exit(candle, BEFORE) is not None
    print(f"  {name}")
    print(f"    旧：入场={'可' if o_in else '被吞'}   按阴线出场={'可' if o_out else '被吞'}")
    print(f"    新：入场={'可' if n_in else '被吞'}   按阴线出场={'可' if n_out else '不触发'}")

# 不变式 1：新实现下入场永不被出场信号吞掉
inv1 = all(freqtrade_verdict(1, new_exit_long(c))[0] for _, c in SCEN)
fails += not inv1
print(f"\n{'✅' if inv1 else '❌'} 不变式1：新实现下 exit_long 恒 0，入场不可能被吞")

# 不变式 2：旧实现在阴线上确实两头都吞（这就是被修的缺陷）
o_in, o_out = freqtrade_verdict(1, old_exit_long(BEAR))
inv2 = (not o_in) and (not o_out)
fails += not inv2
print(f"{'✅' if inv2 else '❌'} 不变式2：旧实现在「阴线 + 仍在榜」上入场与出场同时失效（缺陷复现）")

# 不变式 3：同一场景下新实现出场真的会触发
inv3 = run_custom_exit(BEAR, BEFORE) == "4h_bearish_candle"
fails += not inv3
print(f"{'✅' if inv3 else '❌'} 不变式3：同一场景下新实现按阴线平仓生效")

n_cases = len(CASES) + 3 + 3
print(f"\n=== {n_cases} 项检查，失败 {fails} ===")
sys.exit(1 if fails else 0)
