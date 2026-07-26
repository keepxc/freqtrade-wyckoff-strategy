"""
W2 候选摸底：威科夫命题的功效可行性筛查（原则 W-F / W-C）

**纪律声明**：本脚本只计算 ①事件数 ②因果性(W0) ③最小可检测效应(MDE)，
**绝不计算任何事件组的收益**。否则就变成"挑一个结果好看的命题去立项"——
最典型的事后选择。选哪个命题进 W2，只能依据"可机械定义 + 因果 + 功效足够"，
不能依据"哪个看起来赚钱"。

候选命题（每个都是威科夫体系里可独立证伪的论断，均按 bar t 及之前的数据定义）：
  C1 Selling Climax   放量下跌但收盘拉回上半区 —— "抛压高潮，大户承接"
  C2 SOS 放量突破     突破前20日高点且放量 —— "力量信号"
  C3 UTAD 假突破      盘中破前20日高点但收盘跌回区间内 —— "多头陷阱"(看空)
  C4 No Supply        窄幅+缩量+下跌 —— "供给枯竭"
  C5 Effort vs Result 巨量但价格几乎不动 —— "努力与结果背离"
  C6 区间收缩后突破   20日区间宽度处于历史低位后向上突破 —— "因果法则:横有多长竖有多高"

参数一律取整数量级默认值，**不做任何调优**（调参即过拟合，且此处无收益可供调）。

MDE 口径与 W1 完全一致：市场中性 fwd10，按日期区块的跨资产联合自举。
参考基准：W1 的 Spring 在 n=258 时 MDE=2.52%。MDE ∝ 1/√n，故
  n≈258 → 2.5% ; n≈700 → ~1.5% ; n≈1600 → ~1.0%
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
from lookahead_truncation_test import truncation_test
from w1_power_analysis_multi import load_universe

sys.stdout.reconfigure(encoding="utf-8")

FWD = 10
BLOCK_LEN = 20
N_BOOT = 1500
SEED = 20260726
Z_ALPHA2 = 1.959964
Z_BETA = 0.841621
W = 20  # 统一回看窗口


def _base(df):
    """公共中间量，全部 shift(1) 或只用 t 及之前，无前视。"""
    out = df.copy()
    out["_rng"] = out["high"] - out["low"]
    out["_vol_ma"] = out["volume"].rolling(W).mean().shift(1)
    out["_rng_ma"] = out["_rng"].rolling(W).mean().shift(1)
    out["_prior_high"] = out["high"].rolling(W).max().shift(1)
    out["_prior_low"] = out["low"].rolling(W).min().shift(1)
    out["_absret_ma"] = out["close"].pct_change().abs().rolling(W).mean().shift(1)
    return out


def c1_selling_climax(df):
    o = _base(df)
    pos_in_bar = (o["close"] - o["low"]) / o["_rng"].replace(0, np.nan)
    o["c1_selling_climax"] = (
        (o["volume"] >= 3.0 * o["_vol_ma"])
        & (o["close"] < o["open"])
        & (pos_in_bar >= 0.5)
    ).fillna(False)
    return o[[c for c in o.columns if not c.startswith("_")]]


def c2_sos_breakout(df):
    o = _base(df)
    o["c2_sos_breakout"] = (
        (o["close"] > o["_prior_high"]) & (o["volume"] >= 1.5 * o["_vol_ma"])
    ).fillna(False)
    return o[[c for c in o.columns if not c.startswith("_")]]


def c3_utad_upthrust(df):
    o = _base(df)
    o["c3_utad_upthrust"] = (
        (o["high"] > o["_prior_high"]) & (o["close"] < o["_prior_high"])
    ).fillna(False)
    return o[[c for c in o.columns if not c.startswith("_")]]


def c4_no_supply(df):
    o = _base(df)
    o["c4_no_supply"] = (
        (o["_rng"] <= 0.5 * o["_rng_ma"])
        & (o["volume"] <= 0.5 * o["_vol_ma"])
        & (o["close"] < o["close"].shift(1))
    ).fillna(False)
    return o[[c for c in o.columns if not c.startswith("_")]]


def c5_effort_vs_result(df):
    o = _base(df)
    absret = o["close"].pct_change().abs()
    o["c5_effort_vs_result"] = (
        (o["volume"] >= 2.0 * o["_vol_ma"]) & (absret <= 0.3 * o["_absret_ma"])
    ).fillna(False)
    return o[[c for c in o.columns if not c.startswith("_")]]


def c6_squeeze_breakout(df):
    o = _base(df)
    width = (o["high"].rolling(W).max() - o["low"].rolling(W).min()) / o["close"]
    width_rank = width.rolling(252).rank(pct=True).shift(1)
    o["c6_squeeze_breakout"] = (
        (width_rank <= 0.20) & (o["close"] > o["_prior_high"])
    ).fillna(False)
    return o[[c for c in o.columns if not c.startswith("_")]]


CANDIDATES = [
    ("C1 Selling Climax", c1_selling_climax, "c1_selling_climax", "放量下跌但收盘拉回上半区"),
    ("C2 SOS 放量突破", c2_sos_breakout, "c2_sos_breakout", "突破前20日高点且放量"),
    ("C3 UTAD 假突破", c3_utad_upthrust, "c3_utad_upthrust", "盘中破前高但收盘跌回(看空)"),
    ("C4 No Supply", c4_no_supply, "c4_no_supply", "窄幅+缩量+下跌"),
    ("C5 Effort vs Result", c5_effort_vs_result, "c5_effort_vs_result", "巨量但价格几乎不动"),
    ("C6 区间收缩后突破", c6_squeeze_breakout, "c6_squeeze_breakout", "20日区间宽度历史低位后突破"),
]


def date_block_mde(panel, ev_by_date_pos, calendar, n_boot, seed):
    """按日期区块重采样事件均值 → 自举 sd → MDE。不使用事件组真实收益。"""
    rng = np.random.RandomState(seed)
    n_dates = len(calendar)
    n_blocks = int(np.ceil(n_dates / BLOCK_LEN))
    max_start = max(0, n_dates - BLOCK_LEN)
    means = np.full(n_boot, np.nan)
    for b in range(n_boot):
        pos = []
        for _ in range(n_blocks):
            s = rng.randint(0, max_start + 1) if max_start > 0 else 0
            pos.extend(range(s, min(s + BLOCK_LEN, n_dates)))
        vals = [ev_by_date_pos[p] for p in pos if p in ev_by_date_pos]
        if vals:
            means[b] = np.concatenate(vals).mean()
    means = means[np.isfinite(means)]
    return (Z_ALPHA2 + Z_BETA) * means.std(ddof=1) if len(means) > 2 else np.nan


def main():
    universe, _ = load_universe()
    syms = sorted(universe)

    print("=" * 104)
    print("W2 候选摸底：威科夫命题功效可行性筛查")
    print("⚠️ 本脚本只算 事件数 / 因果性 / MDE，**不计算任何事件组收益**")
    print(f"   （避免'挑结果好看的命题去立项'。选题依据只能是可定义性+因果性+功效）")
    print(f"宇宙 {len(syms)} 品种；MDE 口径与 W1 一致：市场中性 fwd{FWD}，日期区块跨资产联合自举")
    print(f"参考基准：W1 Spring 在 n=258 时 MDE=2.52%（MDE ∝ 1/√n）")
    print("=" * 104)

    raw = pd.DataFrame({s: universe[s].set_index("date")[f"fwd{FWD}"] for s in syms}).sort_index()
    mn = raw.sub(raw.mean(axis=1), axis=0)
    calendar = list(mn.index)
    date_pos = {d: i for i, d in enumerate(calendar)}

    results = []
    for name, fn, col, desc in CANDIDATES:
        # 因果性（W0）：逐币种抽查，任一失败即整体失败
        causal_ok, fail_sym = True, None
        for s in syms:
            rep = truncation_test(fn, universe[s], n_samples=60)
            if not rep.passed:
                causal_ok, fail_sym = False, s
                break

        # 事件收集
        per_sym, ev_pos = {}, {}
        total = 0
        for s in syms:
            flags = fn(universe[s])[col].values
            idx = np.flatnonzero(flags)
            per_sym[s] = len(idx)
            total += len(idx)
            dates = universe[s]["date"].values[idx]
            for d in dates:
                d = pd.Timestamp(d)
                p = date_pos.get(d)
                if p is None or s not in mn.columns:
                    continue
                v = mn.at[d, s]
                if np.isfinite(v):
                    ev_pos.setdefault(p, []).append(v)
        ev_pos = {k: np.array(v) for k, v in ev_pos.items()}
        n_used = sum(len(v) for v in ev_pos.values())

        mde = date_block_mde(mn, ev_pos, calendar, N_BOOT, SEED) if n_used > 10 else np.nan
        concentration = max(per_sym.values()) / total if total else np.nan

        results.append(dict(name=name, desc=desc, causal=causal_ok, fail_sym=fail_sym,
                            total=total, n_used=n_used, mde=mde, conc=concentration,
                            per_sym=per_sym))

        print(f"\n--- {name}：{desc} ---")
        print(f"  因果性(W0): {'✅ PASS' if causal_ok else f'❌ FAIL @ {fail_sym}'}")
        print(f"  事件数: 合计 {total}（有效收益样本 {n_used}），单币最高占比 {concentration:.1%}")
        print(f"  MDE(市场中性 fwd{FWD}): {mde:.2%}" if np.isfinite(mde) else "  MDE: 样本过少无法估计")

    print("\n" + "=" * 104)
    print("汇总（按 MDE 升序 —— 越小越有可能给出结论）")
    print("=" * 104)
    print(f"  {'命题':<22}{'因果':>7}{'事件数':>8}{'MDE':>9}{'单币集中度':>11}")
    for r in sorted(results, key=lambda x: (np.inf if not np.isfinite(x["mde"]) else x["mde"])):
        mde_s = f"{r['mde']:.2%}" if np.isfinite(r["mde"]) else "N/A"
        print(f"  {r['name']:<22}{'✅' if r['causal'] else '❌':>7}{r['total']:>8}"
              f"{mde_s:>9}{r['conc']:>10.1%}")

    print("\n" + "=" * 104)
    print("选题建议依据（不看收益）")
    print("=" * 104)
    print("  1. 因果性必须 PASS —— 否则实盘算不出回测的值，一票否决")
    print("  2. MDE 越小越好 —— MDE 大于合理效应量(约 2~3%)的命题，跑了也无法下结论")
    print("  3. 单币集中度不宜过高 —— 集中在一两个币意味着实际独立样本更少")
    print("  4. 命题本身要可证伪且与 Donchian 机制不同 —— 否则只是通道突破的换皮")
    return 0


if __name__ == "__main__":
    sys.exit(main())
