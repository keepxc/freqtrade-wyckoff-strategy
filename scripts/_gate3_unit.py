"""方案C 分支夹具测试：不装 freqtrade、不连交易所，直接借用两个未绑定方法。

覆盖 AI500_V3_GATE3_PREREG.md §1.1 决策表的每一行。
"""
import ast, sys, types, os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "user_data", "strategies", "AI500V3ScoreHold.py")

# 只把这两个方法摘出来编译，绕开 freqtrade / ccxt 依赖
tree = ast.parse(open(SRC, encoding="utf-8").read())
cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
want = {"_check_tradable", "_record_ref_diag"}
funcs = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in want]
assert len(funcs) == 2, f"没摘全: {[f.name for f in funcs]}"
mod = ast.Module(body=funcs, type_ignores=[])
ast.fix_missing_locations(mod)


class FakeCcxtError(Exception):
    pass


ns = {
    "ZOMBIE_PREFILTER_PP": 1.0,
    "ccxt": types.SimpleNamespace(BadSymbol=type("BadSymbol", (Exception,), {}),
                                  RateLimitExceeded=type("RateLimitExceeded", (Exception,), {})),
}
exec(compile(mod, SRC, "exec"), ns)


class Stub:
    _check_tradable = ns["_check_tradable"]
    _record_ref_diag = ns["_record_ref_diag"]

    def __init__(self, last=None, exc=None, ref=None, ref_exc=None, open1h=None,
                 has_client=True):
        self._last, self._exc, self._ref = last, exc, ref
        self._ref_exc, self._open1h = ref_exc, open1h
        self._has_client = has_client
        self.binance = self

    def _ensure_binance(self):
        return self._has_client

    def fetch_ticker(self, pair):
        if self._exc:
            raise self._exc
        return {"last": self._last}

    def _start_price_1m(self, pair, ts):
        if self._ref_exc:
            raise self._ref_exc
        return self._ref

    def _start_open_1h(self, pair, ts):
        return self._open1h


ROW = {"score": 90.0, "price": 100.0, "change_pct_value": 50.0,
       "start_time": 1779137400, "start_price": 66.0}

CASES = [
    # (说明, stub, row覆盖, 期望tradable, 期望is_zombie, 期望reason前缀)
    ("实时价一致 + 起始价也一致 → 放行",
     Stub(last=100.2, ref=66.1, open1h=65.0), {}, 1, 0, "ok"),
    ("实时价一致 + 起始价偏离 10.7%（ZEC 情形）→ **新版放行**",
     Stub(last=100.2, ref=59.6, open1h=58.0), {}, 1, 0, "ok"),
    ("实时价脱节 2.5pp → 仍拒（真阳性通道）",
     Stub(last=102.5, ref=66.1), {}, 0, 1, "prefilter_price_mismatch"),
    ("实时价脱节恰好 1.0pp（不 >1）→ 放行（边界）",
     Stub(last=101.0, ref=66.1), {}, 1, 0, "ok"),
    ("币安无此合约 → 拒",
     Stub(exc=ns["ccxt"].BadSymbol()), {}, 0, 0, "no_contract"),
    ("ticker 异常 → 拒（没法校验不等于放行）",
     Stub(exc=FakeCcxtError()), {}, 0, 0, "ticker_error"),
    ("ticker 返回无 last → 拒",
     Stub(last=None), {}, 0, 0, "ticker_no_last"),
    ("币安客户端拉不起来 → 拒",
     Stub(has_client=False), {}, 0, 0, "no_binance_client"),
    ("缺 start_time → **新版放行**，reason 带标注",
     Stub(last=100.2, ref=66.1), {"start_time": None}, 1, 0, "ok:no_start_time"),
    ("缺 start_price 且无法反推 → **新版放行**",
     Stub(last=100.2, ref=66.1), {"start_price": None, "change_pct_value": None},
     1, 0, "ok:no_start_price"),
    ("取 1m 参考价抛异常 → **新版放行**",
     Stub(last=100.2, ref_exc=FakeCcxtError()), {}, 1, 0, "ok:ohlcv_error"),
    ("1m 参考价取不到(None) → **新版放行**",
     Stub(last=100.2, ref=None), {}, 1, 0, "ok:ref_1m_unavailable"),
]

fails = 0
print(f"{'':2}{'用例':<46}{'tradable':>9}{'zombie':>8}{'reason':<26}{'ref_diff_pp':>12}")
for desc, stub, over, want_t, want_z, want_r in CASES:
    row = dict(ROW, **over)
    st = stub._check_tradable("TEST", row)
    ok = (st["tradable"] == want_t and st["is_zombie"] == want_z
          and st["reason"].startswith(want_r))
    fails += not ok
    rd = st["ref_diff_pp"]
    print(f"{'✅' if ok else '❌'}{desc:<44}{st['tradable']:>9}{st['is_zombie']:>8}  "
          f"{st['reason']:<24}{(f'{rd:.2f}' if rd is not None else '-'):>12}")
    if not ok:
        print(f"   期望 tradable={want_t} zombie={want_z} reason^={want_r}")

# 关键不变式：观测函数绝不改判
st = Stub(last=100.2, ref=59.6, open1h=58.0)._check_tradable("TEST", ROW)
inv = st["tradable"] == 1 and st["ref_diff_pp"] > 3.0
print(f"\n{'✅' if inv else '❌'} 不变式：ref_diff_pp={st['ref_diff_pp']:.2f} > 3 "
      f"但 tradable={st['tradable']} —— 观测量不再改判")
print(f"{'✅' if st['diff_pp'] is not None else '❌'} 旧观测列仍在落盘："
      f"diff_pp={st['diff_pp']:.2f} recomputed_pct={st['recomputed_pct']:.2f}")

fails += not inv
print(f"\n=== {len(CASES)} 个用例，失败 {fails} ===")
sys.exit(1 if fails else 0)
