"""
TASK 89（预研）：1H 参数时长边界定位——补 2.3天~10天未测区间

背景：用户对 TASK 88 提出"1H 参数需与 1D 区分"。事实纠正：TASK 88 的
参数单位是 bar（20h/55h 通道），已比 1D（20d/10d）快 10~24 倍。但换算
时间长度后参数轴存在真实缺口：<2.3天毛负（TASK 88）、≥10天正平台
（TASK 74 D1），2.3~10 天未测——突破期望的符号在此区间翻转。

申明单元（跑数前固定，两个，零搜索）：
  A (168h, 72h)：7天/3天通道——缺口中间带
  B (480h, 240h)：20天/10天通道搬到 1H 执行——检验日内反应 vs 日线收盘
    确认的增量（预测：日内毛刺触发更多 whipsaw，劣于 1D 版）
系统：对称双向 Donchian（同 TASK 88 实现），funding 入账，固定 1x。

预注册判据（边界测绘性质，采纳门槛预声明）：
  M1 净(0.11%)年化 > 0（信号净存活）
  M2 毛(零成本)年化 > 0（信号存在性——区分信号死/成本死）
  M3 采纳门槛：多头腿 Calmar ≥ DonchianV1 基线 0.74 + 0.05
     （TASK 85 已证混入弱速度只稀释——不过 M3 即使 M1/M2 过也仅记录边界）
预测申明：A 毛收益弱正或零（靠近翻转点）、净负；B 近似 1D 版但更差。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd

from audit_task88_1h_trend import BASE, FUT, run_speed, stats

CELLS = [(168, 72), (480, 240)]
COST_MAIN = 0.0011


def main():
    df = pd.read_feather(os.path.join(FUT, "BTC_USDT_USDT-1h-futures.feather"))
    df = df.sort_values('date').reset_index(drop=True)
    df['date'] = pd.to_datetime(df['date']).dt.tz_localize(None)
    f = pd.read_feather(os.path.join(FUT, "BTC_USDT_USDT-1h-funding_rate.feather"))
    f['date'] = pd.to_datetime(f['date']).dt.tz_localize(None)
    f_hour = f.set_index('date')['open'].astype(float).reindex(
        pd.DatetimeIndex(df['date'])).fillna(0.0).values
    print(f"BTC 1H: {df['date'].min()} ~ {df['date'].max()}  {len(df)} bars")

    for ne, nx in CELLS:
        out, n_rt = run_speed(df, f_hour, ne, nx)
        r_main = out[COST_MAIN]
        t, a, d = stats(r_main)
        tg, ag, dg = stats(out[0.0])
        years = r_main.groupby(r_main.index.year).apply(lambda s: (1 + s).prod() - 1)
        calmar = a / d if d > 0 else float('nan')
        m1, m2 = a > 0, ag > 0
        m3 = calmar >= 0.74 + 0.05
        print(f"\n===== ({ne}h,{nx}h) = {ne/24:.1f}d/{nx/24:.1f}d | rt {n_rt} ({n_rt/5.5:.0f}/年) =====")
        print(f"  净(0.11%): 累计 {t:+8.1%} | 年化 {a:+6.1%} | MaxDD {d:5.1%} | Calmar {calmar:.2f}")
        print(f"  毛(零成本): 累计 {tg:+8.1%} | 年化 {ag:+6.1%}")
        print("  逐年(净): " + "  ".join(f"{y}:{v:+.1%}" for y, v in years.items()))
        print(f"  M1 净年化>0: {'PASS' if m1 else 'FAIL'} | M2 毛年化>0: {'PASS' if m2 else 'FAIL'} | "
              f"M3 采纳门槛Calmar≥0.79: {'PASS' if m3 else 'FAIL'}")


if __name__ == "__main__":
    main()
