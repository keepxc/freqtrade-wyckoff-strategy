"""
TASK 91（预研）：bStocks 唐奇安趋势跟随——币安可执行标的的预注册电池

背景：用户提出"Yahoo拉数据研究信号，币安bStocks实盘执行"。TASK 90 已把加密域
本身的地图翻完，币安 2026-06 上线的 bStocks（代币化美股，1:1实股支撑，现货
API 直接可下单）是一类新执行标的，不在旧地图内。零原则：接活钱前必须先跑
预注册电池，不能凭"唐奇安在加密上有效"（TASK 72/74）就直接迁移假设成立。

标的范围（声明：仅测币安现货API实测可交易的5个bStocks对应的正股，
不测其余未上代币的7000只——那部分只能走网页券商腿，非本电池范围）：
  NVDA TSLA CRCL(Circle) MU(Micron) SNDK(Sandisk)
系统：与 TASK 72/74/84 完全同款方法论，零调参迁移（禁止在新资产上重新搜参）：
  Donchian(20,10) 双向对称，vol-target（lb20/tv20 封顶1x，入场锁定，TASK 75b）
成本口径（不用回测常用的0.11%期货成本，改用bStocks实测代理，如实标注）：
  点差：NVDAB 实测 0.025%、TSLAB 实测 0.106%（2026-07-09 API 快照，样本=1）
  预注册统一取 0.15%/边（含点差+滑点缓冲，五只票取上限档，保守估计）
  声明：无法获取历史bStocks点差（代币29天新品无历史），此为已知的口径弱点，
  不能证伪，只能作为敏感性检验的诚实前提
数据源：yfinance（正股，2020-01-01起或以上市日为准），非bStocks代币本身
  （代币仅29天历史，无法回测；用正股回测隐含"代币价格==正股价格"假设，
  基差/周末风险在此电池外，另行声明为未测风险）

预注册判据（对齐TASK 84组合判据，单资产同时要求样本与效应量）：
  P1 组合（5只等权）Calmar ≥ 0.5（低于加密基线0.74的门槛——单股肥尾/跳空
     风险更高，效应量下限主动调高要求）
  P2 组合累计净收益 > 0（成本后）
  P3 单资产层面：至少3/5只 Calmar>0（分散不能靠1-2只输血）
  P4 对半切：组合两半 Calmar 同号
  P5 财报跳空敏感性（诊断，不判定）：剔除财报日±1日的收益 vs 保留，
     差异>2pp 记为"跳空依赖"警示
全过 → 小仓位试点候选（仍需先纸面dry run一段时间，不直接接活钱）；
任一败 → 方向关闭，不接execution。
跑数前预测：肥尾个股+成本更高（点差0.15% vs 加密0.11%）+ 样本仅5只，
先验低于加密基线；财报跳空是趋势系统在个股上的独有杀手，预测P5触发警示。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
os.environ.setdefault("HTTPS_PROXY", "http://127.0.0.1:7890")
os.environ.setdefault("HTTP_PROXY", "http://127.0.0.1:7890")

import numpy as np
import pandas as pd
import yfinance as yf

TICKERS = ["NVDA", "TSLA", "CRCL", "MU", "SNDK"]
START = "2018-01-01"
N_ENTRY, N_EXIT = 20, 10
VOL_LB, TARGET_VOL, MAX_LEV = 20, 0.20, 1.0
COST_SIDE = 0.0015


def load(ticker):
    df = yf.download(ticker, start=START, interval="1d", progress=False, auto_adjust=True)
    if df.empty:
        return None
    df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    df = df.reset_index()
    df.columns = [str(c).lower() for c in df.columns]
    df = df.sort_values("date").reset_index(drop=True)
    return df


def build_positions(df, n_entry, n_exit):
    hi_ent = df["high"].rolling(n_entry).max().shift(1).values
    lo_ent = df["low"].rolling(n_entry).min().shift(1).values
    hi_ex = df["high"].rolling(n_exit).max().shift(1).values
    lo_ex = df["low"].rolling(n_exit).min().shift(1).values
    c = df["close"].values
    n = len(df)
    pos = np.zeros(n)
    state = 0
    for i in range(n):
        if state == 1 and c[i] < lo_ex[i]:
            state = 0
        elif state == -1 and c[i] > hi_ex[i]:
            state = 0
        if state == 0 and not np.isnan(hi_ent[i]):
            if c[i] > hi_ent[i]:
                state = 1
            elif c[i] < lo_ent[i]:
                state = -1
        pos[i] = state
    return pos


def sleeve_returns(df):
    pos = build_positions(df, N_ENTRY, N_EXIT)
    c = df["close"]
    ret = c.pct_change().values
    rv = pd.Series(ret, index=df.index).rolling(VOL_LB).std().shift(1) * np.sqrt(252)
    n = len(df)
    lev = np.zeros(n)
    cur = 0.0
    for i in range(n):
        if pos[i] == 0:
            cur = 0.0
        elif cur == 0.0:
            r = rv.iloc[i]
            cur = 1.0 if (np.isnan(r) or r <= 0) else min(MAX_LEV, TARGET_VOL / r)
        lev[i] = cur
    sized = pos * lev
    pos_eff = np.roll(sized, 1)
    pos_eff[0] = 0
    dpos = np.abs(np.diff(np.concatenate([[0], sized])))
    dpos_eff = np.roll(dpos, 1)
    dpos_eff[0] = 0
    r = pos_eff * np.nan_to_num(ret) - dpos_eff * COST_SIDE
    return pd.Series(r, index=df["date"].values)


def stats(r):
    eq = (1 + r).cumprod()
    dd = (1 - eq / eq.cummax()).max()
    yrs = len(r) / 252
    total = eq.iloc[-1] - 1
    ann = (1 + total) ** (1 / yrs) - 1 if yrs > 0 else np.nan
    calmar = ann / dd if dd > 0 else np.nan
    return total, ann, dd, calmar


def main():
    frames = {}
    for t in TICKERS:
        df = load(t)
        if df is None or len(df) < 300:
            print(f"{t}: 数据不足或下载失败，跳过")
            continue
        frames[t] = df
        print(f"{t}: {df['date'].min().date()} ~ {df['date'].max().date()}  n={len(df)}")

    rets = {}
    for t, df in frames.items():
        r = sleeve_returns(df)
        rets[t] = r
        t_, a, d, cal = stats(r)
        print(f"\n{t:<6s} 累计 {t_:+8.1%} | 年化 {a:+6.1%} | MaxDD {d:5.1%} | Calmar {cal:.2f}")

    mat = pd.DataFrame(rets).dropna(how="all").fillna(0)
    port = mat.mean(axis=1)
    t_, a, d, cal = stats(port)
    print(f"\n===== 组合（{len(rets)}只等权） =====")
    print(f"累计 {t_:+8.1%} | 年化 {a:+6.1%} | MaxDD {d:5.1%} | Calmar {cal:.2f}")

    mid = len(port) // 2
    _, _, _, cal1 = stats(port.iloc[:mid])
    _, _, _, cal2 = stats(port.iloc[mid:])
    print(f"对半切 Calmar: H1 {cal1:.2f} / H2 {cal2:.2f}")

    n_pos = sum(1 for t in rets if stats(rets[t])[3] > 0)
    p1 = cal >= 0.5
    p2 = t_ > 0
    p3 = n_pos >= 3
    p4 = (cal1 > 0) == (cal2 > 0) if not (np.isnan(cal1) or np.isnan(cal2)) else False
    print("\n" + "=" * 60)
    print(f"P1 组合Calmar≥0.5: {'PASS' if p1 else 'FAIL'} ({cal:.2f})")
    print(f"P2 组合净收益>0: {'PASS' if p2 else 'FAIL'} ({t_:+.1%})")
    print(f"P3 ≥3/5只Calmar>0: {'PASS' if p3 else 'FAIL'} ({n_pos}/{len(rets)})")
    print(f"P4 对半切同号: {'PASS' if p4 else 'FAIL'}")
    verdict = "小仓位试点候选" if (p1 and p2 and p3 and p4) else "方向关闭，不接execution"
    print(f"总判定: {verdict}")


if __name__ == "__main__":
    main()
