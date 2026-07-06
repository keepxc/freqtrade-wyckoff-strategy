"""
TASK 63：Phase D 多头流量法医审计（观测性，不动策略代码）

背景：TASK 60/61 后唯一实证种子 v8_ws_d 仅 18 笔/4.5 年，而 Phase D 上下文
有双数据集显著 edge（fwd20d Δ+3.7pp）。本审计复现多头入场漏斗，定位饿死环节：
  1H choch（因果 wave_start up + efficiency）→ phase ∉ {A,B} → PNF 目标存在
  → [confirm 层 / mot=1 槽位]（用基线回测交易反推）

输出：分 Phase 漏斗表 + Phase D 窗口逐段明细（该窗口有无信号/PNF/成交）。
"""
import os
import sys
import json
import zipfile
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import numpy as np
import pandas as pd

from user_data.strategies.v8_modules.sc_detector import add_v8_sc_detection
from user_data.strategies.v8_modules.phase_detector import add_v8_phase_detection
from user_data.strategies.v8_modules.pnf_calculator import add_v8_pnf_targets
from wyckoff.wave_analyzer import WaveAnalyzer

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
H1_PATH = os.path.join(BASE, "data", "binance", "futures", "BTC_USDT_USDT-1h-futures.feather")
D1_PATH = os.path.join(BASE, "data", "binance", "futures", "BTC_USDT_USDT-1d-futures.feather")
BT_ZIP = os.path.join(BASE, "backtest_results", "backtest-result-2026-07-06_16-21-11.zip")
WIN_START, WIN_END = "2022-01-01", "2026-07-04"


class _P:
    def __init__(self, v): self.value = v


class _Stub:
    """WaveAnalyzer 参数来源（与 WyckoffV8.json 封板参数一致）"""
    wave_min_percent = _P(0.008)
    wave_atr_multiplier = _P(1.5)
    wave_min_bars = _P(3)
    waves_cache = []


@dataclass
class _Wave:
    start_idx: int; end_idx: int; start_price: float; end_price: float
    high_price: float; low_price: float; total_volume: float; bar_count: int; direction: str


def main():
    # ── 日线上下文（与策略同序：SC → Phase → PNF 多头目标）──
    d1 = pd.read_feather(D1_PATH).sort_values("date").reset_index(drop=True)
    d1 = add_v8_sc_detection(d1)
    d1 = add_v8_phase_detection(d1)
    d1 = add_v8_pnf_targets(d1)

    # ── 1H 因果波段 ──
    h1 = pd.read_feather(H1_PATH).sort_values("date").reset_index(drop=True)
    tr = np.maximum(h1["high"] - h1["low"],
                    np.maximum((h1["high"] - h1["close"].shift(1)).abs(),
                               (h1["low"] - h1["close"].shift(1)).abs()))
    h1["atr_20"] = tr.rolling(20).mean()
    h1 = WaveAnalyzer(_Stub(), _Wave)._segment_weis_waves(h1)

    # ── merge（与策略一致：日线 date +1 天防未来函数，backward asof）──
    d1m = d1[["date", "v8_phase", "v8_target_1"]].copy()
    d1m["date"] = d1m["date"] + pd.Timedelta(days=1)
    h1 = pd.merge_asof(h1.sort_values("date"), d1m.sort_values("date"),
                       on="date", direction="backward")

    win = (h1["date"] >= WIN_START) & (h1["date"] <= WIN_END)
    h1w = h1[win].copy()
    h1w["v8_phase"] = h1w["v8_phase"].fillna("unknown")

    choch = (h1w["is_wave_start"].fillna(False).astype(bool)
             & (h1w["wave_direction"] == "up")
             & (h1w["wave_efficiency"] > 0.0001))
    pnf_ok = h1w["v8_target_1"].notna()

    # ── 基线回测交易（实际成交 + 槽位占用区间）──
    with zipfile.ZipFile(BT_ZIP) as z:
        name = [n for n in z.namelist() if n.endswith(".json") and "meta" not in n][0]
        bt = json.load(z.open(name))
    trades = pd.DataFrame(bt["strategy"]["WyckoffV8"]["trades"])
    trades["open_date"] = pd.to_datetime(trades["open_date"], utc=True)
    trades["close_date"] = pd.to_datetime(trades["close_date"], utc=True)
    intervals = trades[["open_date", "close_date"]].values

    def slot_busy(ts):
        return any((o <= ts) and (ts < c) for o, c in intervals)

    print(f"=== 多头漏斗（{WIN_START}~{WIN_END}，1H bars={len(h1w)}）===")
    print(f"{'phase':>8s} {'1H bars':>8s} {'choch↑':>7s} {'+PNF':>6s} {'槽位空闲':>8s} {'实际成交':>8s}")
    tag_map = {"D": "v8_ws_d", "E": "v8_ws_e", "C": "v8_ws_c", "unknown": "v8_ws_unknown"}
    for ph in ["A", "B", "C", "D", "E", "unknown"]:
        m = h1w["v8_phase"] == ph
        sig = choch & m
        sig_pnf = sig & pnf_ok
        free = sum(1 for ts in h1w.loc[sig_pnf, "date"] if not slot_busy(ts))
        actual = int((trades["enter_tag"] == tag_map.get(ph, "-")).sum())
        print(f"{ph:>8s} {int(m.sum()):>8d} {int(sig.sum()):>7d} {int(sig_pnf.sum()):>6d} "
              f"{free:>8d} {actual:>8d}")

    # ── Phase D 窗口逐段明细 ──
    print("\n=== Phase D 窗口明细（日线连续段）===")
    d1w = d1[(d1["date"] >= WIN_START) & (d1["date"] <= WIN_END)].copy()
    is_d = (d1w["v8_phase"] == "D").values
    dates = d1w["date"].values
    tgt = d1w["v8_target_1"].values
    segs, start = [], None
    for i in range(len(d1w)):
        if is_d[i] and start is None:
            start = i
        elif not is_d[i] and start is not None:
            segs.append((start, i - 1)); start = None
    if start is not None:
        segs.append((start, len(d1w) - 1))

    ws_d_opens = trades.loc[trades["enter_tag"] == "v8_ws_d", "open_date"]
    print(f"{'起':>12s} {'止':>12s} {'天数':>4s} {'PNF有效天':>8s} {'choch↑':>7s} {'成交':>4s}")
    for s, e in segs:
        t0, t1 = pd.Timestamp(dates[s]).tz_localize("UTC"), \
                 (pd.Timestamp(dates[e]) + pd.Timedelta(days=2)).tz_localize("UTC")
        pnf_days = int(pd.notna(tgt[s:e + 1]).sum())
        seg_mask = (h1w["date"] >= t0) & (h1w["date"] < t1) & (h1w["v8_phase"] == "D")
        n_sig = int((choch & seg_mask).sum())
        n_tr = int(((ws_d_opens >= t0) & (ws_d_opens < t1)).sum())
        print(f"{str(pd.Timestamp(dates[s]).date()):>12s} {str(pd.Timestamp(dates[e]).date()):>12s} "
              f"{e - s + 1:>4d} {pnf_days:>8d} {n_sig:>7d} {n_tr:>4d}")


if __name__ == "__main__":
    main()
