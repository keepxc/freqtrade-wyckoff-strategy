#!/usr/bin/env python3
"""
【探索性——非结论，禁止引用为证据】
MACD whisper 对齐自检：时变 alpha（修复后）vs 定周期回溯（修复前）
==================================================================
用途：验证 AI500V1MacdExitDryRun 里自适应周期引擎的实现改动。

Pine 的 f_ema(src, length) 每根 bar 重算 alpha = 2/(length+1) 并携带 running
state；而 length 本身由自适应引擎逐 bar 产生。先前实现取最后一根的周期、用
固定 alpha 回溯整段历史 —— 那是另一个滤波器。本脚本用本地 OHLCV 直接比对两
种实现给出的 power / whisper。

数据来自 user_data/ai500_local/ohlcv/{SYMBOL}-{TF}.feather（由
scripts/ai500_fetch_ohlcv.py 拉取），只用已收盘 bar。

用法:
  python3 scripts/ai500_macd_align_check.py
  python3 scripts/ai500_macd_align_check.py --symbols BTCUSDT,ETHUSDT
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "user_data", "strategies"))

from AI500V1MacdExitDryRun import AI500V1MacdExitDryRun as S  # noqa: E402

OHLCV = os.path.join(_ROOT, "user_data", "ai500_local", "ohlcv")
TFS = ["15m", "1h", "4h", "1d"]
DEFAULT = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT",
           "XRPUSDT", "LINKUSDT", "ADAUSDT", "BNBUSDT"]


def load(sym, tf, limit):
    p = os.path.join(OHLCV, f"{sym}-{tf}.feather")
    if not os.path.exists(p):
        return None
    df = pd.read_feather(p).tail(limit)
    if len(df) < 40:
        return None
    return {k: df[k].to_numpy(dtype=float)
            for k in ("high", "low", "close", "volume")}


def old_fixed_period(s, o, base):
    """修复前的实现：末根周期 + 固定 alpha 回溯整段。"""
    f, sl, sg = (int(x[-1]) for x in s._adaptive_periods_series(o, base))

    def ema(x, per):
        a = 2.0 / (per + 1)
        out = np.empty(len(x))
        out[0] = x[0]
        for i in range(1, len(x)):
            out[i] = a * x[i] + (1 - a) * out[i - 1]
        return out

    c = o["close"]
    m = ema(c, f) - ema(c, sl)
    sig = ema(m, sg)
    return m, sig, m - sig, (f, sl, sg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", help="逗号分隔，缺省用内置 8 个")
    args = ap.parse_args()
    syms = ([x.strip() for x in args.symbols.split(",") if x.strip()]
            if args.symbols else DEFAULT)

    s = S.__new__(S)                      # 绕过 __init__，只用纯函数方法
    lim = S.MACD_FETCH_LIMIT

    print(f"{'symbol':<10} {'power(new)':<18} {'power(old)':<18} "
          f"{'whisper(new)':<14} {'whisper(old)':<14} diff")
    print("-" * 92)
    n_diff = 0
    period_rows = []
    for sym in syms:
        pn, po, ok = [], [], True
        for tf in TFS:
            o = load(sym, tf, lim)
            if o is None:
                ok = False
                break
            base = S.MACD_TF_PARAMS[tf]
            f, sl, sg = s._adaptive_periods_series(o, base)
            m, si, h = s._macd_series(o["close"], f, sl, sg)
            pn.append(s._power_from_series(m, si, h))
            mo, so, ho, per_o = old_fixed_period(s, o, base)
            po.append(s._power_from_series(mo, so, ho))
            period_rows.append((sym, tf, base, (int(f[-1]), int(sl[-1]), int(sg[-1])),
                                int(f.min()), int(f.max()),
                                int((np.diff(f[-60:]) != 0).sum())))
        if not ok:
            print(f"{sym:<10} [跳过：缺数据]")
            continue
        wn, wo = S._classify(pn), S._classify(po)
        d = "  <<< 分歧" if wn != wo else ""
        n_diff += wn != wo
        print(f"{sym:<10} {str(pn):<18} {str(po):<18} {wn:<14} {wo:<14}{d}")

    print(f"\nwhisper 分歧: {n_diff}/{len(syms)}")

    print(f"\n各周期实际用的 (fast,slow,signal) —— 与 TradingView 面板对账用")
    print(f"{'symbol':<10} {'tf':<5} {'base':<14} {'末根':<14} "
          f"{'fast范围':<12} {'近60根变动次数'}")
    print("-" * 78)
    for sym, tf, base, last, fmin, fmax, nch in period_rows:
        cap = " ★钳位顶" if last[0] == S.MACD_MAX_FAST and last[1] == S.MACD_MAX_SLOW else ""
        print(f"{sym:<10} {tf:<5} {str(base):<14} {str(last):<14} "
              f"{f'{fmin}~{fmax}':<12} {nch}{cap}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
