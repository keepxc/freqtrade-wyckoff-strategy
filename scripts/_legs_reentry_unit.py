#!/usr/bin/env python3
"""
v3_baseline_legs.py 的合成数据夹具 —— 2026-09-20 追加块（A/B/C/D）的校验
=========================================================================

**为什么必须有**：真实样本只有 2 个 episode、bear4h 出场 1~2 笔，
新加的四块（链式重入 / 重入统计 / bear4h 出场质量 / 二维格子）在真实数据上
**大部分分支不会被执行到**（卖飞 vs 锁利润、槽位被跳过、数据不足、双向波动）。
一个从不被执行的代码路径 = 一条未经检验的读数。

做法：不碰网络 —— `load_window` 被替换成按「折线价格函数」现场生成的 1m K，
score 快照与成交 DB 都是合成的。只测**逻辑**，不测行情。

运行：python3 scripts/_legs_reentry_unit.py   （全通过 → 退出码 0）
"""
import contextlib
import importlib.util
import io
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_spec = importlib.util.spec_from_file_location(
    "v3_baseline_legs", os.path.join(REPO, "scripts", "v3_baseline_legs.py"))
L = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(L)

U = timezone.utc
B0 = datetime(2026, 9, 1, 0, 0, tzinfo=U)          # 基准边界 00:00 UTC
B = lambda h: B0 + timedelta(hours=h)              # noqa: E731

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"   [{detail}]" if detail else ""))


def approx(a, b, tol=2e-3):
    return a is not None and abs(a - b) <= tol


# --------------------------------------------------------------- 合成价格路径
class Path:
    """折线价格：给定 (时刻, 价) 断点，线性插值，两端平推。"""

    def __init__(self, points):
        self.pts = sorted(points)

    def at(self, t):
        pts = self.pts
        if not pts:
            return 1.0
        if t <= pts[0][0]:
            return pts[0][1]
        if t >= pts[-1][0]:
            return pts[-1][1]
        for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
            if t0 <= t <= t1:
                if t1 == t0:
                    return v1
                f = (t - t0).total_seconds() / (t1 - t0).total_seconds()
                return v0 + (v1 - v0) * f
        return pts[-1][1]


PATHS = {
    # AAA：连跌三次，每次 −5% 止损（episode 压缩会只看第一笔）
    "AAA": Path([(B(0), 1.00), (B(1), 0.94), (B(4), 0.94), (B(5), 0.88),
                 (B(8), 0.88), (B(9), 0.826)]),
    # BBB：+10% MFE 后一根阴线出场，出场后继续涨到 +13% → 卖飞
    "BBB": Path([(B(0), 1.00), (B(3), 1.10), (B(4), 1.10), (B(8), 1.06),
                 (B(16), 1.14), (B(32), 1.20), (B(56), 1.15)]),
    # CCC：阴线出场时小亏，出场后继续跌 6% → 锁利润
    "CCC": Path([(B(0), 1.00), (B(4), 1.02), (B(8), 0.99),
                 (B(16), 0.96), (B(32), 0.93), (B(56), 0.95)]),
    # HHH：阴线出场后先涨 9% 再跌 8% → 双向
    "HHH": Path([(B(16), 1.00), (B(20), 1.02), (B(24), 0.99),
                 (B(30), 1.08), (B(44), 0.91), (B(60), 0.95)]),
}


def make_load_window(default=1.0):
    def _lw(sym, T, hours):
        p = PATHS.get(sym)
        out = {}
        for i in range(int(hours * 60)):
            t = T + timedelta(minutes=i)
            o = p.at(t) if p else default
            c = p.at(t + timedelta(minutes=1)) if p else default
            out[t] = (o, max(o, c) * 1.0005, min(o, c) * 0.9995, c)
        return out
    return _lw


# --------------------------------------------------------------- 合成 score 榜
def snap(rows, boundary):
    """rows: [(symbol, score, age_h, gain_pct, peak)] → 快照行列表。

    peak 默认写成 **`峰值 N`** 这种字面量 —— 复刻真实 CSV 的写入口径
    （`signal_peak` 列真的是「峰值 88」，旧版解析器因此让 score_over_peak 恒缺失）。
    """
    out = []
    for row in rows:
        sym, score, age, gain = row[:4]
        peak = row[4] if len(row) > 4 else f"峰值 {score + 2}"
        out.append(dict(symbol=sym, score=str(score), signal_peak=peak,
                        start_time=str(int((boundary - timedelta(hours=age))
                                           .timestamp())),
                        change_pct_value=str(gain)))
    return out


LOW = [("LOW1", 65, 1, 5)]          # 腿 L 名单（60~80，只观察不进场）
RANK = [("RNG1", 79, 1, 5)]         # 只是让榜深 >3，不进 top3


def synth_snaps():
    return {
        B(0): snap([("AAA", 90, 2, 10), ("BBB", 89, 60, 250), ("CCC", 88, 30, 70)], B(0)),
        B(4): snap([("AAA", 88, 6, 12)] + LOW, B(4)),
        B(8): snap([("AAA", 85, 10, 5)] + LOW, B(8)),
        B(12): snap([("DDD", 95, 1, 3), ("EEE", 94, 2, 4), ("FFF", 93, 3, 6),
                     ("GGG", 92, 4, 8)] + LOW + RANK, B(12)),
        B(16): snap([("HHH", 91, 5, 9)] + LOW, B(16)),
    }


# --------------------------------------------------------------- 合成成交 DB
def synth_db(path):
    con = sqlite3.connect(path)
    con.execute("""create table trades(
        id integer primary key, pair text, is_open integer,
        open_date text, close_date text, open_rate real, close_rate real,
        exit_reason text, close_profit real, close_profit_abs real,
        stake_amount real, max_rate real, min_rate real)""")
    rows = [
        # (id, open, close, open_rate, close_rate, reason, profit, abs, max, min)
        (1, "2026-09-18 16:01:52", "2026-09-18 23:03:12", 0.88993, 0.83548,
         "stop_loss", -0.06275, -16.25, 0.98535, 0.78772),
        (2, "2026-09-19 00:01:33", "2026-09-19 12:01:38", 0.80897, 0.95701,
         "4h_bearish_candle", 0.18028, 45.95, 0.96544, 0.78772),
        (3, "2026-09-19 16:19:45", "2026-09-19 20:22:10", 1.13625, 1.07916,
         "stop_loss", -0.05173, -13.82, 1.22982, 0.90704),
        (4, "2026-09-20 00:07:30", "2026-09-20 03:48:17", 1.13002, 1.05689,
         "stop_loss", -0.06565, -17.29, 1.15446, 1.05689),
    ]
    for r in rows:
        con.execute("insert into trades values(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (r[0], "AAA/USDT:USDT", 0, r[1], r[2], r[3], r[4], r[5],
                     r[6], r[7], 260.0, r[8], r[9]))
    con.commit()
    con.close()


# =============================================================== 单元断言
print("=" * 78)
print("part 1：纯函数（leg_strategy / chain_trades / reentry_stats / 出场质量 / 二维）")
print("=" * 78)
lw = make_load_window()

# --- 1. 止损腿本身
r1 = L.leg_strategy(lw("AAA", B(0), 48), B(0))
check("单腿止损：tag=stop 且净收益=−5.1%", r1["tag"] == "stop" and approx(r1["ret"], -0.051),
      f"tag={r1['tag']} ret={r1['ret']*100:.2f}%")
check("止损出场价 = 入场价×0.95（建模口径）", approx(r1["exit_px"], 0.95, 1e-6),
      f"exit_px={r1['exit_px']:.6f}")

# --- 2. 阴线出场
r2 = L.leg_strategy(lw("BBB", B(0), 48), B(0))
check("阴线出场：tag=bear4h，触发时 +5.9%，MFE +10.1%",
      r2["tag"] == "bear4h" and approx(r2["ret"], 0.059) and approx(r2["mfe"], 0.1006, 3e-3),
      f"tag={r2['tag']} ret={r2['ret']*100:.2f}% mfe={r2['mfe']*100:.2f}%")

# --- 3. 链式 vs episode 压缩
events = [(B(0), "AAA", 90), (B(4), "AAA", 88), (B(8), "AAA", 85)]
epi = L._er.to_episodes(events)
chain, diag = L.chain_trades(events, lw, max_open=0)
epi_sum = L.sum_arith([L.leg_strategy(lw(s, t, 48), t)["ret"] for t, s, _ in epi])
chain_sum = L.sum_arith([t["ret"] for t in chain])
check("episode 压缩：3 个事件 → 1 个 episode", len(epi) == 1 and len(chain) == 3,
      f"episode={len(epi)} chain={len(chain)}")
check("链式累计损失被 episode 口径隐藏（−15.3% vs −5.1%）",
      approx(chain_sum, -0.153, 1e-3) and approx(epi_sum, -0.051, 1e-3),
      f"chain={chain_sum*100:+.2f}% episode={epi_sum*100:+.2f}%")
check("隐藏量 = 链式 − episode < 0", chain_sum - epi_sum < 0,
      f"Δ={(chain_sum-epi_sum)*100:+.2f}pp")

# --- 4. 槽位上限
_, d_cap2 = L.chain_trades([(B(12), s, sc) for s, sc in
                            (("DDD", 95), ("EEE", 94), ("FFF", 93))], lw, max_open=2)
_, d_cap3 = L.chain_trades([(B(12), s, sc) for s, sc in
                            (("DDD", 95), ("EEE", 94), ("FFF", 93))], lw, max_open=3)
check("max_open=2 → 跳过 1 笔；max_open=3 → 跳过 0 笔",
      len(d_cap2["skipped_slots"]) == 1 and len(d_cap3["skipped_slots"]) == 0,
      f"cap2 跳过 {len(d_cap2['skipped_slots'])}，cap3 跳过 {len(d_cap3['skipped_slots'])}")

# --- 5. 重入统计
re = L.reentry_stats(chain)
check("重入统计：AAA 2 次重入、均标记上笔止损、再入价逐次更低",
      len(re) == 2 and all(x["after_stop"] for x in re)
      and re[0]["vs_exit"] < 0 and re[1]["vs_exit"] < re[0]["vs_exit"],
      "; ".join(f"gap={x['gap_h']:.2f}h vs={x['vs_exit']*100:+.2f}%" for x in re))
check("重入间隔 ≈ 3.2h（上笔止损后同一下个 4h 边界即再入）",
      all(approx(x["gap_h"], 3.2, 0.15) for x in re),
      f"gaps={[round(x['gap_h'], 2) for x in re]}")
check("链累计逐笔递减（−5.1 → −10.2 → −15.3）",
      approx(re[-1]["cum"], -0.153, 1e-3), f"cum={re[-1]['cum']*100:+.2f}%")

# --- 6. 出场质量三判
v = {}
for sym in ("BBB", "CCC", "HHH"):
    t = L.leg_strategy(lw(sym, B(16) if sym == "HHH" else B(0), 48), B(16) if sym == "HHH" else B(0))
    post = L.post_exit_stats(lw(sym, t["exit_t"], 48), t["exit_t"], t["exit_px"])
    v[sym] = L.exit_verdict(post)[0]
check("出场质量：BBB=卖飞 / CCC=锁利润 / HHH=双向",
      v == {"BBB": "卖飞", "CCC": "锁利润", "HHH": "双向"}, str(v))
t_late = L.leg_strategy(lw("BBB", B(0), 48), B(0))
trunc = {k: val for k, val in lw("BBB", t_late["exit_t"], 48).items()
         if k < t_late["exit_t"] + timedelta(hours=10)}
check("出场质量：覆盖 < 38.4h → 数据不足（不猜）",
      L.exit_verdict(L.post_exit_stats(trunc, t_late["exit_t"], t_late["exit_px"]))[0] == "数据不足")

# --- 7. 二维格子
rows2d = [dict(s=0.05, signal_age_h=2, prior_gain_pct=10),
          dict(s=-0.05, signal_age_h=60, prior_gain_pct=250),
          dict(s=0.02, signal_age_h=30, prior_gain_pct=70),
          dict(s=0.01, signal_age_h=None, prior_gain_pct=10)]
cell, miss = L.grid_counts(rows2d, "signal_age_h", L.AGE_BUCKETS, "prior_gain_pct",
                           L.GAIN_BUCKETS, "s")
check("二维格子：计入 3 笔、缺失 1 笔（合计 = n）",
      sum(len(v) for v in cell.values()) == 3 and miss == 1,
      f"计数={sum(len(v) for v in cell.values())} 缺失={miss}")

# --- 8. §2.1 口径：算术和 vs 复利
check("算术和 = Σr，复利 = Π(1+r)−1（两者必须分开报）",
      approx(L.sum_arith([0.1, -0.05]), 0.05, 1e-12)
      and approx(L.sum_compound([0.1, -0.05]), 0.045, 1e-12))

# --- 9. 协变量字面量容错（真实 CSV 的 signal_peak = "峰值 88"）
check("_f 容错解析 '峰值 88' → 88", L._f("峰值 88") == 88.0 and L._f("87.253") == 87.253)
check("_f 对纯垃圾返回 None（不瞎猜）", L._f("峰值") is None and L._f("") is None)
check("_f 记录非数值字面量（供输出报数据质量）", L.NONNUM_RAW.get("峰值 88") == 1)

# --- 10. 重入拆分：gap=0 与 gap>0 必须分开统计
g0 = [r for r in re if r["gap_h"] <= 1e-9]
check("重入拆分后 gap=0 的笔数为 0（本例全是跨边界止损再入）", len(g0) == 0)

# =============================================================== 端到端
print("\n" + "=" * 78)
print("part 2：main() 端到端（合成 K 线 + 合成榜 + 合成 DB，零网络）")
print("=" * 78)
tmpd = tempfile.mkdtemp(prefix="legs_synth_")
db = os.path.join(tmpd, "trades.sqlite")
synth_db(db)
# AAA 的 1m 在 09-18 12:00 之后没有数据 → 模拟「实盘 bear4h 出场撞上归档缺口」，
# 逼出 DB 兜底分支（否则该分支永远不被执行 = 未经检验）
_base_lw = make_load_window()
CUT = datetime(2026, 9, 18, 12, 0, tzinfo=U)


def lw_maybe_cut(sym, T, hours):
    d = _base_lw(sym, T, hours)
    if sym == "AAA":
        d = {k: v for k, v in d.items() if k < CUT}
    return d


L.load_window = lw_maybe_cut
L._er.load_snapshots = lambda path: synth_snaps()
buf = io.StringIO()
argv = sys.argv[:]
sys.argv = ["v3_baseline_legs.py", "--score-history", "unused.csv",
            "--trades-db", db, "--max-open", "3"]
try:
    with contextlib.redirect_stdout(buf):
        rc = L.main()
finally:
    sys.argv = argv
out = buf.getvalue()
print(out)

check("端到端退出码 0", rc == 0, f"rc={rc}")
for marker in ("【A】episode 级 vs trade 级", "【B】同标的重入统计",
               "【C】bear4h 出场质量", "【C2】实盘出场笔 vs 重放事件对齐",
               "【D】signal_age × prior_gain 二维", "【原】逐腿分布"):
    check(f"输出含区块 {marker}", marker in out)
check("A 块报出 episode 压缩的累计差与分标的明细",
      "episode 压缩带来的累计差" in out)
check("A 块同时给 ① episode / ② 链式 / ③ 实盘 三套口径",
      "① episode 级" in out and "② 链式 trade 级" in out and "③ 实盘 DB" in out)
check("实盘前向样本只含 ≥2026-09-19 的 3 笔",
      "③b 实盘 DB 前向样本" in out and "n=3 " in out)
check("B 块给出重入间隔与「止损后再入」分项", "其中「止损后立即再入」" in out)
check("C 块给出 卖飞 / 锁利润 / 双向 / 中性 四类判定",
      all(k in out for k in ("卖飞", "锁利润", "双向", "中性")))
check("C 块给出止损滑点观测（§6.11 B）", "附：止损滑点" in out and "预定止损位" in out)
check("反事实 Δ 用 pp 单位（BBB：+5.90% vs 持满 +16.57% → −10.67pp）",
      "Δ= -10.67pp" in out)
check("D 块二维格子每格带 n", "(n=" in out.split("【D】")[1])
check("A 块给出分标的 ①vs② 明细（Δ(②−①)）", "Δ(②−①)" in out)
check("A 块给出 ②c 同窗口链式口径或说明其无样本",
      "②c 链式 trade 级" in out or "口径声明" in out)
check("B 块把 gap=0 与 gap>0 拆开统计",
      "同边界再入(gap=0)" in out and "跨边界再入(gap>0)" in out)
check("数据质量段存在（归档缺口 / 协变量写入格式 / 信号年龄）",
      "-- 数据质量 / 覆盖" in out and "协变量写入格式" in out and "信号年龄：" in out)
check("实盘 bear4h 撞归档缺口时走 DB 兜底轨迹（#2 出场后 → #3/#4 成交）",
      "归档缺口兜底" in out and "下一笔#3" in out and "粗口径" in out)
check("score_over_peak 不再恒缺失（容错解析生效）",
      "s/pk=0.978" in out and "0.8~1        n=7" in out)
check("实盘台账用 CST 展示且标注重放侧为 UTC", "开仓CST" in out and "两套差 8h" in out)
check("未触碰策略参数（脚本内无参数改动标记）",
      "不改变 AI500_V3_GATE3_PREREG.md §2 已冻结" in out)

print("\n" + "=" * 78)
print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
if FAIL:
    print("失败项：" + "; ".join(FAIL))
    sys.exit(1)
print("全部通过；退出码 0")
sys.exit(0)
