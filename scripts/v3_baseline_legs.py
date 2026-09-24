#!/usr/bin/env python3
"""
V3 对照腿 + 协变量 + 真实成交对照 —— 「赚了钱是不是因为 AI500」
=====================================================

**探索性——非结论，禁止引用为证据。不改变 AI500_V3_GATE3_PREREG.md §2 已冻结的
主判据（前 30 笔累计净收益 > 0），只是给它加上「跟什么比」和「按什么分层」的读数。**

为什么要有这个（2026-09-19 立）：GATE3 的主判据只问「30 笔加起来是不是正的」。
但 `mean > 0` 不等于 AI500 score 有 alpha —— 大盘同期涨了、裸持不平仓更赚、
低分档一样赚，三种情形下「正收益」都不构成 score 的证据。

六条腿（同一批入场时刻，配对）
------------------------------
  腿 S  策略腿      修复后的真实规则：首根「收盘晚于入场」的收阴 4h K 平仓，
                    −5% 止损，48h 上限
  腿 X  止损+48h    保留 −5% 止损与 48h 上限，**只关掉阴线出场**。
                    **S − X = 4h 阴线出场的纯贡献**（止损 held constant）
  腿 H  裸持 48h    不止损、不看阴线，持满 48h。**X − H = 止损的贡献**
  腿 B  BTC 同窗口  与腿 S 完全相同的持有窗口内 BTC 的收益。**S − B = 剥掉 beta**
  腿 L  score 60~80 同一 4h 边界上榜单里 60 ≤ score < 80 的行，套腿 S 同一规则
                    等权。**S − L = `MIN_SCORE=80` 这条线是故事还是统计结论**（主对照）
  腿 R  rank 4~10   同一 4h 边界上榜单第 4~10 名，套腿 S 同一规则等权。
                    **S − R = top3 这个截断有没有意义**（次要腿，样本薄，必看 n）

### 为什么必须有腿 X（2026-09-19 加，AIN 实证）

腿 H 原本挂的解释是「S − H = 出场规则赚了还是亏了」——**这个解释是错的**，因为
H 同时去掉了阴线出场**和**止损，S − H 混了两者。首次真实跑出来的
`S − H = +85.85pp` 把这个缺陷暴露得很彻底：

    09-15 16:01:43  入场 0.18304
    09-15 20:00     反事实阴线出场 −2.07%   (+4.0h)
    09-15 20:28:39  实盘止损出场   −5.20%   (+4.5h)
    09-16 01:49     崩盘 −28.8%/分钟        (+9.8h)  ← 两条出场都已离场 5h+

AIN 的 −87.93% 经原始 1m 归档核验为**真实**（2880/2880 根无缺口，崩后 36h 一直
趴在 0.022~0.027）。但崩盘落在**两条出场规则的作用窗口之外** ⇒ `+85.85pp` 里
没有一分钱来自「阴线出场做对了」或「止损做对了」，它全部来自「任何出场都躲过了
一次与出场规则无关的特异性砸盘」。**n=1 且完全由尾部事件驱动。**

腿 X 把止损固定住，`S − X` 才是阴线出场自己的贡献，`X − H` 才是止损自己的贡献。

### ⚠️ 一处已更正的推断（2026-09-19，PREREG §6.10 同步更正）

本脚本初版**删掉了腿 R**，理由是下面这段推断：

    ai500_zombie_check.csv  3,721 行 / 1,481 个决策轮次   (PREREG §1.2)
    _refresh_entry_state 每轮写 min(3, 有分资产数) 行
    3,721 / 1,481 = 2.51 行/轮  ⇒「榜上同时只挂 2~3 个活信号」

**该推断是错的。** 实跑直接测量榜深度得到 `median=3 / mean=3.89 / >3 占 48.9%`
—— rank 4~10 **约一半快照是存在的**，腿 R 只是样本薄，不是不可能。

错在把 `2.51` 当成榜深度的代理：`ai500_zombie_check.csv` 自 **2026-09-11 16:53**
才开始覆盖，而分母那 1,481 轮跨了更长的窗口，**没有僵尸记录的轮次进了分母却没
贡献行数**，把每轮行数压低了。这正是 `v3_entry_replay.entry_events` 文档里警告过
的窗口不对齐陷阱的变体。

**教训：直接测量优先于间接推断。** 榜深度诊断（见 `main()` 开头输出）就是为此保留
并前置的——每次跑都复核这条前提，不再靠算术外推。

连带更正（原文过头，现收窄）：
  1. `门①(rank≤3) 通过率 100%` 仍**不能**当证据用，但理由只剩一条：重放只看
     top3 行，门① 按构造必然 100%。不能再说「榜上本来也就 2~3 个」。
  2. `max_open_trades=3` 的约束力取决于榜深度，**不是**「肯定不构成约束」。
  3. 「本策略实际上没有横截面选择」→ 应为「**横截面选择很弱**」（top3 of ~3.9）。
     `score≥80` 仍是策略最实质的那道门，故腿 L 保留为**主对照**、腿 R 为次要腿。

三个协变量（逐笔落盘 + 分层描述）
--------------------------------
  signal_age_hours  = 入场时刻 − start_time      ← 买的是「上涨第 1 小时」还是「第 5 天」
  prior_gain_pct    = change_pct_value           ← 入场时该信号已经涨了多少
  score_over_peak   = score / signal_peak        ← 入场时 score 距自身峰值衰减了多少

立项理由：V3 已成交样本里 #1 IOST 在 +91.58% 后入场、#3 BR 在 **+200.4%** 后入场
（signal age 54.9h，MFE **0.00%**）。若 30 笔全聚在 +100~300% 区间，那这 30 笔
测的其实是「买已经翻了两三倍的东西」，而该构造已被 `AI500_PRICEBOARD_PREREG`
在姊妹榜上以 n=6552 证伪（中位数恰好落在止损位）。**先记录，不进交易规则**
（两份外部评审与本项目判断一致：加信号年龄门属 B 类求 edge，本轮不做）。

数据来源全部是**决策时刻已存在**的：
  · `ai500_score_history_v3.csv` —— 每 5 分钟全榜快照（含 score/signal_peak/
    start_time/change_pct_value，即三个协变量与腿 L 名单的全部输入）
  · 币安官方逐日归档 1m K（`data.binance.vision`，可重放、不含事后修订）
  · **V3 dryrun 成交 DB**（`--trades-db`）—— 唯一能拿到「无冷却期重入」的源头
入场时刻重建与 `scripts/v3_entry_replay.py` 同口径（4h 边界 + top3 + score≥80
+ 可选门③），并复用其 K 线抓取与事件枚举函数 —— 那边改了这边跟着变。

═══════════════════════════════════════════════════════════════════════════════
 2026-09-20 追加四块（用户指派；仍属观测，不改判据、不改策略参数）
═══════════════════════════════════════════════════════════════════════════════

A. **episode 级 vs trade 级**（本文件局限 7 的正面处置）
   `to_episodes` 把同标的 4h 内连续合格的事件压成一个、只留首个。原版据此写
   「隐藏了止损后重入的累计损失、偏差对策略有利」——**2026-09-20 实测推翻**：
   偏差符号不定（本样本链式比 episode 好 +27.99pp，且全部由一笔驱动）。
   现在同时报三套口径：

     ① episode 级（腿 S）      —— 每个信号段只做第一笔（**笔数与累计都不等于实盘，
        偏差符号事先不可知** —— 原版写「偏差对策略有利」，2026-09-20 实测推翻，见局限 7）
     ② 链式 trade 级（腿 S'）  —— **不做 episode 压缩**：每个 4h 边界上凡合格且
        该标的当前无持仓就入场，出场后下一个边界若仍合格就再入（= 无冷却期），
        并同时报 `max_open_trades=3` 的槽位约束版与不限槽版
     ③ **实盘 DB trade 级**    —— bot 记账的 `profit_ratio`，含手续费，这是唯一
        不含建模假设的真相；并给出 `GATE3_C_RESTART_UTC=2026-09-19` 之后的前向样本

   口径与 §2.1 一致：等额仓位**算术求和**；同时报复利终值供对照（不作判据）。
   ⚠️ 三套数不一样是**正常且必须报告的**：① 与 ② 的差 = episode 压缩丢掉了什么
   （**符号不定**，见局限 7）；② 与 ③ 的差 = 规则随时间的变化 + 建模差（见局限 9）。

B. **重入统计（两级数据源都做）**
   同标的上笔以 `stop_loss` 平仓 → 本笔再入：次数、间隔小时、再入价 vs 上笔出场价、
   再入后收益、该链累计。实盘链来自 DB（PREREG §6.11 D 的 `re_entry_after_stop`），
   重放链来自腿 S'。

C. **bear4h 出场质量**（PREREG §6.11 C 的处置）
   每一笔 `bear4h` / `4h_bearish_candle` 出场记录四项：
     触发时收益 / 持仓期 MFE（回吐幅度）/ 出场后 48h 走势 / 反事实「不出场持满 48h」
   判读用两个显式阈值（出场后 48h 内）：
     · 最高价 ≥ 出场价 ×1.05 → **错过上行（卖飞）**
     · 最低价 ≤ 出场价 ×0.95 → **避开下行（锁利润）**
     · 两者同时 → 双向；都不满足 → 中性；覆盖不足 38.4h → 数据不足（不猜）
   出场后 48h 覆盖不到（归档未发布）时**必须标数据不足**，不得当成「没卖飞」。

D. **signal_age × prior_gain 二维**
   两个协变量此前只做一维分层。二维格子看的是「老信号 × 已大涨」这个立项假说
   （PREREG §6.10 C）是否真的聚成一块负收益区。**每格必须带 n**，空格显式打 0。

已知局限（必须连同结果一起读）
------------------------------
1. **样本上限由开采日 2026-09-05 硬性决定**，n 只有十几个 episode。故本脚本
   **先算 MDE 再报点估计**；CI 跨零只能写「无 ≥MDE 量级的效应」，不得写成
   「AI500 无效」或「80 这条线无意义」（CLAUDE.md 零结果解读边界）。
2. **同一标的共享同一条价格路径**（ZEC/BR 占绝大多数），名义 n ≠ 有效 n。
   故自举按**日区块**重采样（同一日所有事件一起抽），且强制报告标的集中度。
3. **腿 L 的僵尸门只对落在 top3 之内的行可得**（`ai500_zombie_check.csv` 只记
   top3）。因 top3≈全榜，绝大多数低分行其实有记录；取不到的标 `gate_unavailable`。
   `--zombie-gate` 给定时**同时报过门/不过门两版**（过门可比性更强，不过门对腿 L
   偏不利），不取其一。
4. 止损与出场信号落在同一根 1m bar 时**假定止损先触发**。⚠️ 这与引擎实际优先级
   **相反**：`should_exit` 的 append 顺序是 exit-signal → stoploss，`freqtradebot.py:1383`
   命中第一个即返回，故实盘同轮内 custom_exit 赢过 stoploss（按阴线收盘价平，
   通常优于止损价）。本脚本保留止损优先，作为**保守下界**。
5. 腿 H 无止损，尾部风险不可比；它只用来量「出场规则截掉了多少右尾」，
   **不是**一个可部署的替代方案。
6. 协变量分层是**描述性**的。n 十几个 episode 分 4 档后每档个位数，
   **不得**对分层差异做任何显著性声明。
7. ⚠️ **episode 压缩 ≠ 实盘笔数**（原文此处写「偏差方向对策略有利」，**2026-09-20
   实测推翻**）。`to_episodes` 把同标的 4h 内连续合格的事件压成一个、只留首个；
   但实盘**没有冷却期**（`config_ai500_v3_dryrun.json` 无 `protections`，已核实），
   止损平仓后只要标的仍在榜就**立刻重新开仓**（实证：BR #3 於 0.88993 入场、
   0.83548 止损出场，随后 #4 於 0.80897 再次买入同一标的）。
   **偏差符号不定**：丢掉的那个「首笔之后的重入」既可能更差也可能更好。09-05~09-20
   实测链式比 episode **好 +27.99pp**（+5.50% / 39 笔 vs −22.48% / 9 个 episode），
   且由 BULLA 09-05 的一次重入（+43.62%，上笔止损 −19.8% 后接回）与 ZEC 的多笔
   重入驱动；分标的则为 IOST/AIN/BR「隐藏亏损」、BULLA/ZEC「隐藏收益」。
   ⇒ **腿 S 既不代表真实笔数也不代表真实累计**，读它必须同时读 ② 链式与 ③ 实盘。
8. 腿 R 样本薄（约一半快照才有 rank 4~10），**任何腿 R 的读数必须连同 n 一起看**；
   n 个位数时不得做方向性陈述。
9. **② 链式是「修复后规则 + 门③关」外推到全窗口，不是 bot 当时的行为**：09-18 之前
   实盘门③开（ZEC/BR 通过率 0%）、09-19 之前实盘还受 §6.1 信号裁决缺陷影响。
   故 ② 与 ③ 的差**不是纯建模差**，含规则随时间的两次变化（输出里已声明）。
10. **归档缺口会静默截断窗口**（`data.binance.vision` 缺日 → 出场质量观察窗变短）。
    截断的笔标「数据不足」，**不得**读作「没卖飞/没亏损」；每次跑都报缺口清单，
    并用参考标的（BTC/ZEC）同日探一次，区分「全局未发布」与「该标的特有」。
    2026-09-20 实测：09-19/09-20 全标的 404（当日归档滞后），故所有 09-17 之后的
    出场笔观察窗必然残缺 —— 跑得越近当下越严重。

用法：
    python3 scripts/v3_baseline_legs.py --score-history user_data/ai500_score_history_v3.csv
    python3 scripts/v3_baseline_legs.py --score-history ... --trades-db user_data/tradesv3.ai500_v3_dryrun.sqlite
    python3 scripts/v3_baseline_legs.py --score-history ... --no-db --max-open 0   # 只看重放
只读；除 K 线缓存外不写任何文件（DB 先整库复制到临时目录再读，不碰运行中的库）。
"""
import argparse
import bisect
import collections
import importlib.util
import math
import os
import random
import re
import shutil
import sqlite3
import statistics
import sys
import tempfile
from datetime import datetime, timedelta, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 复用 v3_entry_replay 的 K 线抓取 / 快照载入 / 事件枚举（同口径铁律）
_spec = importlib.util.spec_from_file_location(
    "v3_entry_replay", os.path.join(REPO, "scripts", "v3_entry_replay.py"))
_er = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_er)

TIMEFRAME_H = _er.TIMEFRAME_H       # 4
STOPLOSS = _er.STOPLOSS             # -0.05
TOP_N = _er.TOP_N                   # 3
MIN_SCORE = _er.MIN_SCORE           # 80.0

MAX_HOLD_H = 48                     # minimal_roi {"2880": -1}
FEE_ROUNDTRIP = 0.001               # 往返 0.1%
LOW_SCORE_BAND = (60.0, MIN_SCORE)  # 腿 L：60 ≤ score < 80（策略只观察、不进场那一档）
RANK_BUCKET = (4, 10)               # 腿 R：榜单第 4~10 名（次要腿，约一半快照才有）
BOOT_N = 2000
Z_SUM = 1.96 + 0.8416               # MDE = Z_SUM × sd_自举

AGE_BUCKETS = [(0, 6), (6, 24), (24, 48), (48, math.inf)]
GAIN_BUCKETS = [(0, 20), (20, 50), (50, 100), (100, math.inf)]
SCORE_OVER_PEAK_BUCKETS = [(0, 0.5), (0.5, 0.8), (0.8, 1.0), (1.0, math.inf)]

# ---- 2026-09-20 追加块的常量 ----
CST = timezone(timedelta(hours=8))
TRADES_DB_DEFAULT = os.path.join(REPO, "user_data", "tradesv3.ai500_v3_dryrun.sqlite")
FORWARD_SINCE = datetime(2026, 9, 19, tzinfo=timezone.utc)   # GATE3_C_RESTART_UTC
MAX_OPEN_DEFAULT = 3                # config_ai500_v3_dryrun.json: max_open_trades=3
POST_EXIT_H = 48                    # 出场后观察窗
MIN_COVER_H = POST_EXIT_H * 0.8     # 覆盖率下限：不足则标「数据不足」，不猜
SELL_EARLY_UP = 0.05                # 出场后 48h 最高价 ≥ +5% → 卖飞
LOCK_AVOIDED_DOWN = -0.05           # 出场后 48h 最低价 ≤ −5% → 锁利润
# 实盘出场原因 → 重放口径（两套命名必须映射，否则统计会漏一半样本）
TAG_MAP = {"stop_loss": "stop", "4h_bearish_candle": "bear4h",
           "roi": "maxhold", "custom_exit": "custom"}

# ---- 数据质量计数（跑完必须报，别让缺数据伪装成结论）----
NUM_RE = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
FETCH_MISS = collections.Counter()      # (symbol, day) -> 取不到的次数
NONNUM_RAW = collections.Counter()      # 直接用 float() 解析失败的原始字面量


# ------------------------------------------------------------------ K 线
def load_window(symbol: str, T: datetime, hours: int) -> dict:
    """取 [T, T+hours] 覆盖到的全部 1m K，跨日自动拼。缺任一日返回已有的部分。

    ⚠️ 归档缺日会**静默缩短窗口**（fetch_1m 返回 {}），后果是「出场质量」的
    出场后观察窗被截断。故这里把缺日记进 FETCH_MISS，跑完必须报出来 ——
    否则「数据不足」会被误读成「没卖飞」。
    """
    bars = {}
    day = T.date()
    last_day = (T + timedelta(hours=hours)).date()
    while day <= last_day:
        got = _er.fetch_1m(symbol, day.isoformat())
        if not got:
            FETCH_MISS[(f"{symbol}USDT" if not symbol.endswith("USDT") else symbol,
                        day.isoformat())] += 1
        bars.update(got)
        day += timedelta(days=1)
    return bars


def _slice(bars: dict, T: datetime, hours: int):
    end = T + timedelta(hours=hours)
    return sorted((t, v) for t, v in bars.items() if T <= t < end)


# ------------------------------------------------------------------ 出场规则
def leg_strategy(bars: dict, T: datetime, use_bear_exit: bool = True):
    """腿 S：−5% 止损 / 首根收盘晚于入场的收阴 4h K / 48h 上限。

    `use_bear_exit=False` → **腿 X**：只关掉阴线出场，止损与 48h 上限原样保留。
    两者共用同一条价格路径与同一套判定顺序，故 `S − X` 就是阴线出场的纯贡献。

    返回 dict（ret/hold_h/tag/entry/exit_px/exit_t/mfe/mae）；数据不足返回 None。
    mfe/mae 只累计**出场之前**的 bar（止损那根不计入其 high/low —— 同一 bar 内的
    先后不可知，取保守侧 = MFE 偏低）。
    """
    window = _slice(bars, T, MAX_HOLD_H)
    if not window or window[0][0] != T:
        return None
    entry = window[0][1][0]                  # 入场价 = 边界那一分钟的开盘
    if entry <= 0:
        return None
    cur_open = None
    cur_close = None
    bucket_start = T
    hi = lo = entry
    for t, (o, h, l, c) in window:
        # 同 bar 内假定止损先触发（保守下界，见局限 4）
        if l / entry - 1.0 <= STOPLOSS:
            return dict(ret=STOPLOSS - FEE_ROUNDTRIP,
                        hold_h=(t - T).total_seconds() / 3600, tag="stop",
                        entry=entry, exit_px=entry * (1.0 + STOPLOSS), exit_t=t,
                        mfe=hi / entry - 1.0, mae=lo / entry - 1.0)
        if t >= bucket_start + timedelta(hours=TIMEFRAME_H):
            # 上一根 4h K 已收盘，且其收盘时刻严格晚于入场 → 可以发言
            if (use_bear_exit and cur_open is not None and cur_close is not None
                    and cur_close < cur_open):
                exit_t = bucket_start + timedelta(hours=TIMEFRAME_H)
                return dict(ret=cur_close / entry - 1.0 - FEE_ROUNDTRIP,
                            hold_h=(exit_t - T).total_seconds() / 3600, tag="bear4h",
                            entry=entry, exit_px=cur_close, exit_t=exit_t,
                            mfe=hi / entry - 1.0, mae=lo / entry - 1.0)
            # 推进到包含 t 的那个桶（容错 1m 归档缺口，否则缺 >4h 会错位分桶）
            while t >= bucket_start + timedelta(hours=TIMEFRAME_H):
                bucket_start += timedelta(hours=TIMEFRAME_H)
            cur_open = None
        if cur_open is None:
            cur_open = o
        cur_close = c
        hi = max(hi, h)
        lo = min(lo, l)
    last_c = window[-1][1][3]
    return dict(ret=last_c / entry - 1.0 - FEE_ROUNDTRIP, hold_h=MAX_HOLD_H,
                tag="maxhold", entry=entry, exit_px=last_c,
                exit_t=T + timedelta(hours=MAX_HOLD_H),
                mfe=hi / entry - 1.0, mae=lo / entry - 1.0)


def leg_hold(bars: dict, T: datetime, hours: int = MAX_HOLD_H):
    """腿 H：裸持 `hours` 小时，无止损无出场信号。"""
    window = _slice(bars, T, hours)
    if not window or window[0][0] != T:
        return None
    entry = window[0][1][0]
    if entry <= 0:
        return None
    return window[-1][1][3] / entry - 1.0 - FEE_ROUNDTRIP


# ------------------------------------------------- 2026-09-20 追加：链式入场
def chain_trades(events, bars_fn, max_open=MAX_OPEN_DEFAULT, use_bear_exit=True,
                 load_hours=MAX_HOLD_H):
    """腿 S'（trade 级）：**不做 episode 压缩**的链式入场。

    与实盘行为对齐的三条规则：
      1. 每个 4h 边界上凡「合格」的事件都考虑入场（不加冷却期）；
      2. 同一标的**持仓期间不重复入场**，出场后若下个边界仍合格 → 再入；
      3. `max_open`（None/0 = 不限）控制同时持仓数，同一边界内按 score 降序取。
         ⚠️ 引擎的真实排序不是 score（是 pair 遍历顺序），此处的降序是**建模选择**，
         只在槽位确实不够时才有影响，输出里会报「因槽位被跳过」的笔数。

    返回 (trades, diag)；trades 为 leg_strategy 的 dict + T/sym/score/src。
    """
    trades = []
    occupied = {}          # symbol -> 出场时刻（该时刻之后可再入）
    slots = []             # 各持仓的出场时刻
    skipped_slots, skipped_data = [], []
    for T, sym, sc in sorted(events, key=lambda e: (e[0], -e[2])):
        if occupied.get(sym) is not None and T < occupied[sym]:
            continue                                   # 该标的仍持仓
        cap = max_open if max_open else None
        if cap is not None and sum(1 for e in slots if e > T) >= cap:
            skipped_slots.append((T, sym, sc))
            continue
        r = leg_strategy(bars_fn(sym, T, load_hours), T, use_bear_exit)
        if r is None:
            skipped_data.append((T, sym))
            continue
        r.update(T=T, sym=sym, score=sc, src="chain")
        occupied[sym] = r["exit_t"]
        slots.append(r["exit_t"])
        trades.append(r)
    return trades, dict(skipped_slots=skipped_slots, skipped_data=skipped_data)


def sum_arith(rets):
    """§2.1 口径：等额仓位算术求和。"""
    return sum(rets) if rets else None


def sum_compound(rets):
    """复利终值，仅供对照（§2.1 明确不作判据）。"""
    if not rets:
        return None
    p = 1.0
    for r in rets:
        p *= (1.0 + r)
    return p - 1.0


# ------------------------------------------------- 2026-09-20 追加：重入统计
def reentry_stats(trades):
    """同 symbol 的链：上一笔出场 → 本笔入场。

    返回 list[dict]，每项 = 本笔相对上一笔的
      gap_h      间隔小时（本笔入场 − 上笔出场；为负说明重叠，属建模/数据问题）
      vs_exit    本笔入场价 / 上笔出场价 − 1
      after_stop 上笔是否 stop 出场
      cum        该链到本笔为止的算术累计（等额仓位）
    """
    out = []
    by_sym = collections.defaultdict(list)
    for t in trades:
        by_sym[t["sym"]].append(t)
    for sym, lst in by_sym.items():
        lst.sort(key=lambda t: t["T"])
        cum = 0.0
        for i, t in enumerate(lst):
            cum += t["ret"]
            if i == 0:
                continue
            prev = lst[i - 1]
            gap = (t["T"] - prev["exit_t"]).total_seconds() / 3600.0
            vs = (t["entry"] / prev["exit_px"] - 1.0) if prev.get("exit_px") else None
            out.append(dict(sym=sym, T=t["T"], prev_exit_t=prev["exit_t"],
                            prev_tag=prev["tag"], gap_h=gap, vs_exit=vs,
                            ret=t["ret"], cum=cum, tag=t["tag"],
                            after_stop=(prev["tag"] == "stop")))
    return out


# ------------------------------------------------- 2026-09-20 追加：出场质量
def post_exit_stats(bars: dict, exit_t: datetime, exit_px: float,
                    hours: int = POST_EXIT_H):
    """出场后 `hours` 小时的走势（基准 = 出场价）。覆盖不足也必须能报出来。"""
    w = _slice(bars, exit_t, hours)
    if not w or exit_px <= 0:
        return None
    have_h = (w[-1][0] - exit_t).total_seconds() / 3600.0 + 1 / 60.0

    def close_at(h):
        tgt = exit_t + timedelta(hours=h)
        c = [v[3] for t, v in w if t <= tgt]
        return c[-1] if c else None

    c24, c48 = close_at(24), close_at(48)
    return dict(have_h=have_h, n=len(w),
                post_hi=max(v[1] for _, v in w) / exit_px - 1.0,
                post_lo=min(v[2] for _, v in w) / exit_px - 1.0,
                c24=(c24 / exit_px - 1.0) if c24 else None,
                c48=(c48 / exit_px - 1.0) if c48 else None)


def exit_verdict(post):
    """把出场后走势翻成「卖飞 / 锁利润 / 双向 / 中性 / 数据不足」。

    阈值显式（见文件头 C 节）：+5% 上行、−5% 下行。
    ⚠️ 覆盖不足（< 80%）时**判定仍是「数据不足」**（门槛不因结果而调），
    但 `missed/avoided` 仍按**已覆盖窗口**算出来返回 —— 让读者看到部分证据的方向，
    而不是把它藏成 False。截断窗口的方向提示必须在输出里标明「可能仍在扩大」。
    """
    if post is None:
        return "数据不足", None, None
    missed = post["post_hi"] >= SELL_EARLY_UP
    avoided = post["post_lo"] <= LOCK_AVOIDED_DOWN
    if post["have_h"] < MIN_COVER_H:
        return "数据不足", missed, avoided
    if missed and avoided:
        return "双向", missed, avoided
    if missed:
        return "卖飞", missed, avoided
    if avoided:
        return "锁利润", missed, avoided
    return "中性", missed, avoided


# ------------------------------------------------------------------ 快照与协变量
def snapshot_at(snaps, sorted_ts, T):
    """返回边界 T 实际使用的那个快照的 (时刻, 行列表)；无可用快照返回 (None, None)。

    与 `v3_entry_replay.entry_events` 同口径：取 T 之前最近的一个快照，
    且距 T 不超过 1 小时（超过则视为该边界无有效快照）。
    """
    i = bisect.bisect_right(sorted_ts, T) - 1
    if i < 0 or (T - sorted_ts[i]).total_seconds() > 3600:
        return None, None
    return sorted_ts[i], snaps[sorted_ts[i]]


def _f(v):
    """数值解析（容忍 AI500 榜单把数字写成中文标签，见 NONNUM_RAW）。

    实证：`ai500_score_history_v3.csv` 的 `signal_peak` 列写的是 **`峰值 88`**
    这种字面量，不是数字。旧版 `_f()` 直接 `float()` → None，于是
    `score_over_peak = score/signal_peak` **在全部行上恒缺失**（协变量 #3 从来没
    有数据，腿 S 的逐笔里 s/pk 一直是空）。这里做一次容错抽取，并把这类行计数
    报出来 —— 抽取值可用，但**写入端仍应改成纯数值**（那属策略代码，本次不动）。
    """
    try:
        return float(v)
    except (TypeError, ValueError):
        pass
    if isinstance(v, str):
        m = NUM_RE.search(v)
        if m:
            try:
                out = float(m.group())
            except ValueError:
                return None
            if out != 0 or v.strip() in ("0", "0.0"):
                NONNUM_RAW[v.strip()[:24]] += 1
            return out
    return None


def covariates(row, T):
    """signal_age_hours / prior_gain_pct / score_over_peak。取不到的项为 None。"""
    age = None
    st = _f(row.get("start_time"))
    if st:
        age = (T - datetime.fromtimestamp(st, timezone.utc)).total_seconds() / 3600
    score, peak = _f(row.get("score")), _f(row.get("signal_peak"))
    return dict(
        signal_age_h=age,
        prior_gain_pct=_f(row.get("change_pct_value")),
        score_over_peak=(score / peak if score is not None and peak else None),
    )


def rank_bucket_symbols(rows):
    """腿 R 名单：按 score 降序的第 4~10 名（1-indexed，含两端）。

    约一半快照榜深度 ≤3 ⇒ 该名单常为空，这是腿 R「样本薄」的来源（局限 8）。
    """
    board = sorted((r for r in rows if _f(r.get("score")) is not None),
                   key=lambda r: -_f(r["score"]))
    lo, hi = RANK_BUCKET
    return [r["symbol"] for r in board[lo - 1:hi]]


def low_band_symbols(rows, zgate=None, snap_t=None):
    """腿 L 名单：60 ≤ score < 80。返回 (过门名单, 不过门名单, 门不可得数)。"""
    lo, hi = LOW_SCORE_BAND
    ungated, gated, unavail = [], [], 0
    for r in rows:
        s = _f(r.get("score"))
        if s is None or not (lo <= s < hi):
            continue
        ungated.append(r["symbol"])
        if zgate is None:
            continue
        key = (snap_t, r["symbol"])
        if key not in zgate:
            unavail += 1
        elif zgate[key]:
            gated.append(r["symbol"])
    return gated, ungated, unavail


# ------------------------------------------------------- 2026-09-20 追加：实盘 DB
def load_real_trades(db_path):
    """读 V3 dryrun 成交 DB。

    **先整库复制到临时目录再读** —— 库正在被 bot 以 WAL 模式写入，只读连接在
    WAL 下仍可能要求写 shm；复制一份零风险，代价几 MB。
    DB 内 `open_date`/`close_date` 是 **naive UTC**，此处显式 replace(tzinfo=utc)。
    返回 list[dict]，字段名与重放侧对齐（T/exit_t/sym/ret/tag/entry/exit_px/mfe/mae）。
    """
    if not os.path.exists(db_path):
        print(f"  [跳过] 找不到成交 DB：{db_path}")
        return []
    tmp = tempfile.mkdtemp(prefix="v3legs_db_")
    base = os.path.basename(db_path)
    for suf in ("", "-wal", "-shm"):
        if os.path.exists(db_path + suf):
            shutil.copy2(db_path + suf, os.path.join(tmp, base + suf))
    con = sqlite3.connect(os.path.join(tmp, base))
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "select id, pair, is_open, open_date, close_date, open_rate, close_rate,"
        " exit_reason, close_profit, close_profit_abs, stake_amount, max_rate, min_rate"
        " from trades order by id").fetchall()
    con.close()
    out = []
    for r in rows:
        if r["close_date"] is None:
            continue
        sym = r["pair"].split("/")[0]
        tag = TAG_MAP.get(r["exit_reason"], r["exit_reason"])
        entry = r["open_rate"]
        open_utc = datetime.fromisoformat(r["open_date"]).replace(tzinfo=timezone.utc)
        close_utc = datetime.fromisoformat(r["close_date"]).replace(tzinfo=timezone.utc)
        out.append(dict(
            id=r["id"], sym=sym, pair=r["pair"], is_open=bool(r["is_open"]),
            T=open_utc, exit_t=close_utc,
            hold_h=(close_utc - open_utc).total_seconds() / 3600.0,
            entry=entry, exit_px=r["close_rate"], tag=tag,
            raw_tag=r["exit_reason"], ret=r["close_profit"],
            profit_abs=r["close_profit_abs"], stake=r["stake_amount"],
            mfe=(r["max_rate"] / entry - 1.0) if r["max_rate"] else None,
            mae=(r["min_rate"] / entry - 1.0) if r["min_rate"] else None,
            src="real"))
    return out


def stop_slippage(t):
    """§6.11 B 的观测项：止损的**预定止损位 vs 实际成交价**偏离。"""
    if t["tag"] != "stop" or not t["entry"] or not t["exit_px"]:
        return None
    level = t["entry"] * (1.0 + STOPLOSS)
    return dict(level=level, fill=t["exit_px"], dev=t["exit_px"] / level - 1.0,
                booked=t["ret"])


# ------------------------------------------------------------------ 统计
def block_bootstrap(pairs, n=BOOT_N, seed=20260919):
    """日区块自举：pairs = [(日期, 值)]。返回 (点估计, 5%分位, 95%分位, sd_自举)。"""
    if not pairs:
        return None
    by_day = collections.defaultdict(list)
    for d, v in pairs:
        by_day[d].append(v)
    days = list(by_day)
    point = statistics.fmean(v for _, v in pairs)
    rng = random.Random(seed)
    means = []
    for _ in range(n):
        pool = []
        for _ in range(len(days)):
            pool.extend(by_day[rng.choice(days)])
        if pool:
            means.append(statistics.fmean(pool))
    if not means:
        return None
    means.sort()
    sd = statistics.pstdev(means) if len(means) > 1 else float("nan")
    return point, means[int(0.05 * len(means))], means[int(0.95 * len(means))], sd


def report_leg(name, vals):
    if not vals:
        print(f"  {name:<24} 无样本")
        return
    wins = sum(1 for v in vals if v > 0)
    print(f"  {name:<24} n={len(vals):<4} mean={statistics.fmean(vals)*100:+7.2f}%  "
          f"median={statistics.median(vals)*100:+7.2f}%  胜率={wins/len(vals)*100:5.1f}%")


def report_diff(label, pairs):
    r = block_bootstrap(pairs)
    if r is None:
        print(f"  {label:<24} 无样本")
        return
    point, lo, hi, sd = r
    verdict = "CI 下界>0" if lo > 0 else ("CI 上界<0" if hi < 0 else "跨零 → 不可判定")
    print(f"  {label:<24} Δ={point*100:+7.2f}%  90%CI=[{lo*100:+6.2f}, {hi*100:+6.2f}]  "
          f"MDE={Z_SUM*sd*100:5.2f}pp  {verdict}")


def report_buckets(title, rows, key, buckets, unit):
    print(f"\n  按 {title} 分层（**描述性，n 太小不得做显著性声明**）")
    for lo, hi in buckets:
        sel = [r["s"] for r in rows
               if r[key] is not None and lo <= r[key] < hi]
        span = f"{lo:g}~{'∞' if hi == math.inf else f'{hi:g}'}{unit}"
        if sel:
            print(f"    {span:<12} n={len(sel):<3} 腿S mean={statistics.fmean(sel)*100:+7.2f}%")
        else:
            print(f"    {span:<12} n=0")
    miss = sum(1 for r in rows if r[key] is None)
    if miss:
        print(f"    (协变量缺失 {miss} 笔)")


def grid_counts(rows, key_x, buckets_x, key_y, buckets_y, val="s"):
    """二维格子：{(xi, yi): (n, mean)}。空格不入 dict，报告时显式补 0。"""
    cell = collections.defaultdict(list)
    miss = 0
    for r in rows:
        x, y, v = r.get(key_x), r.get(key_y), r.get(val)
        if x is None or y is None or v is None:
            miss += 1
            continue
        xi = next((i for i, (lo, hi) in enumerate(buckets_x) if lo <= x < hi), None)
        yi = next((i for i, (lo, hi) in enumerate(buckets_y) if lo <= y < hi), None)
        if xi is None or yi is None:
            miss += 1
            continue
        cell[(xi, yi)].append(v)
    return cell, miss


def report_2d(title, rows, key_x, buckets_x, unit_x, key_y, buckets_y, unit_y,
              val="s", val_name="腿S"):
    print(f"\n  {title}（二维，**描述性；每格带 n，空格=0，n 个位数不得做方向性陈述**）")
    cell, miss = grid_counts(rows, key_x, buckets_x, key_y, buckets_y, val)
    lab = [f"{lo:g}~{'∞' if hi == math.inf else f'{hi:g}'}{unit_y}"
           for lo, hi in buckets_y]
    head = f"    {key_x:<16}" + "".join(f"{l:>16}" for l in lab)
    print(head)
    for xi, (lo, hi) in enumerate(buckets_x):
        row = f"    {lo:g}~{'∞' if hi == math.inf else f'{hi:g}'}{unit_x:<12}"
        for yi in range(len(buckets_y)):
            vals = cell.get((xi, yi))
            if vals:
                row += f"{statistics.fmean(vals)*100:+7.2f}%(n={len(vals)})".rjust(16)
            else:
                row += f"{'0':>8}(n=0)   ".rjust(16)
        print(row)
    tot = sum(len(v) for v in cell.values())
    print(f"    ↑ 计入格子的 {tot} 笔；协变量缺失/越界 {miss} 笔")


# ------------------------------------------------------------------ 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--score-history", required=True)
    ap.add_argument("--zombie-gate", default=None,
                    help="开门③ 对照；与 v3_entry_replay 同口径，须配 --since 对齐窗口")
    ap.add_argument("--since", default=None, help="ISO UTC，只取该时刻之后的事件")
    ap.add_argument("--trades-db", default=TRADES_DB_DEFAULT,
                    help="V3 成交 DB（默认 %s）" % TRADES_DB_DEFAULT)
    ap.add_argument("--no-db", action="store_true", help="不读成交 DB（只看重放）")
    ap.add_argument("--forward-since", default="2026-09-19",
                    help="30 笔前向样本起点（GATE3_C_RESTART_UTC），默认 2026-09-19")
    ap.add_argument("--max-open", type=int, default=MAX_OPEN_DEFAULT,
                    help="链式腿的同时持仓上限，0=不限（默认 %d）" % MAX_OPEN_DEFAULT)
    args = ap.parse_args()

    print(__doc__.split("用法：")[0].strip())
    print("=" * 78)

    snaps = _er.load_snapshots(args.score_history)
    if not snaps:
        print("score 快照为空，退出。")
        return 1
    sorted_ts = sorted(snaps)
    zgate = _er.load_zombie(args.zombie_gate) if args.zombie_gate else None
    since = (datetime.fromisoformat(args.since).astimezone(timezone.utc)
             if args.since else None)
    fwd_since = datetime.fromisoformat(args.forward_since).replace(tzinfo=timezone.utc)

    events = _er.entry_events(snaps, zgate, since)
    episodes = _er.to_episodes(events)
    print(f"\n事件 {len(events)} 个 → episode {len(episodes)} 个"
          f"（连续合格段只取首个；结论只能读 episode 那套）")

    # 榜深度：验证「top3 ≈ 全榜」这个前提（腿 L 取代腿 R 的依据）
    depths = [len([r for r in rows if _f(r.get("score")) is not None])
              for rows in snaps.values()]
    if depths:
        print(f"榜深度（每快照有分资产数）：中位 {statistics.median(depths):.0f}  "
              f"均值 {statistics.fmean(depths):.2f}  最大 {max(depths)}  "
              f"→ >3 的快照占 {sum(1 for d in depths if d > 3)/len(depths)*100:.1f}%")
        print("  ↑ 这是腿 R 可用性与「TOP_N=3 的横截面选择强度」的唯一直接读数。")
        print("    >3 占比 = 腿 R 有数据的快照比例；占比越低，TOP_N=3 越接近「榜上有啥买啥」。")
        print("    **禁止再用 zombie_check 的行/轮比外推榜深度**（窗口不对齐，已错过一次，见文件头）")

    conc = collections.Counter(sym for _, sym, _ in episodes)
    tot = sum(conc.values()) or 1
    print("标的集中度（episode）：" + "  ".join(
        f"{s}={c}({c/tot*100:.0f}%)" for s, c in conc.most_common()))

    def bars_fn(sym, T, hours):
        return load_window(sym, T, hours)

    btc_cache = {}
    rows_out = []
    lband_unavail = 0
    for T, sym, score in episodes:
        bars = bars_fn(sym, T, MAX_HOLD_H)
        s = leg_strategy(bars, T)
        if s is None:
            continue
        x = leg_strategy(bars, T, use_bear_exit=False)      # 腿 X：只关阴线出场
        h_ret = leg_hold(bars, T)

        # 腿 B：BTC 在腿 S 完全相同的持有窗口内
        if T not in btc_cache:
            btc_cache[T] = bars_fn("BTC", T, MAX_HOLD_H)
        b_ret = (leg_hold(btc_cache[T], T, max(1, int(round(s["hold_h"]))))
                 if btc_cache[T] else None)

        # 协变量 + 腿 L 名单，都取自该边界实际使用的那个快照
        snap_t, board = snapshot_at(snaps, sorted_ts, T)
        cov = dict(signal_age_h=None, prior_gain_pct=None, score_over_peak=None)
        l_gated_syms, l_ungated_syms, r_syms = [], [], []
        if board is not None:
            me = next((r for r in board if r["symbol"] == sym), None)
            if me:
                cov = covariates(me, T)
            l_gated_syms, l_ungated_syms, na = low_band_symbols(board, zgate, snap_t)
            lband_unavail += na
            r_syms = rank_bucket_symbols(board)

        def band_mean(syms):
            vals = []
            for ls in syms:
                lr = leg_strategy(bars_fn(ls, T, MAX_HOLD_H), T)
                if lr is not None:
                    vals.append(lr["ret"])
            return (statistics.fmean(vals) if vals else None), len(vals)

        l_un, l_un_n = band_mean(l_ungated_syms)
        l_ga, l_ga_n = band_mean(l_gated_syms) if zgate else (None, 0)
        r_ret, r_n = band_mean(r_syms)

        s_detail = dict(s)                      # 留全量明细（出场质量块要用）
        s_detail.update(T=T, sym=sym, score=score)
        rows_out.append(dict(T=T, sym=sym, score=score, s=s["ret"], hold=s["hold_h"],
                             tag=s["tag"], s_detail=s_detail,
                             x=(x["ret"] if x else None),
                             x_tag=(x["tag"] if x else None), h=h_ret, b=b_ret,
                             l_un=l_un, l_un_n=l_un_n,
                             l_ga=l_ga, l_ga_n=l_ga_n, r=r_ret, r_n=r_n, **cov))

    # ---------------- 2026-09-20 追加 A/B：链式 trade 级重放 ----------------
    chain_cap, diag_cap = chain_trades(events, bars_fn, args.max_open)
    chain_unlim, _ = chain_trades(events, bars_fn, 0)
    rc = reentry_stats(chain_cap)

    # ---------------- 2026-09-20 追加 A/B：实盘 DB ----------------
    real = [] if args.no_db else load_real_trades(args.trades_db)
    real_chain = reentry_stats(real)
    real_fwd = [t for t in real if t["T"] >= fwd_since]

    def _sum_line(label, rets, note=""):
        if not rets:
            print(f"  {label:<30} 无样本")
            return
        srt = sorted(rets)
        wo_max = sum(srt[:-1]) if len(srt) > 1 else None
        print(f"  {label:<30} n={len(rets):<3} 算术和={sum_arith(rets)*100:+8.2f}%  "
              f"复利={sum_compound(rets)*100:+8.2f}%  mean={statistics.fmean(rets)*100:+7.2f}%  "
              f"median={statistics.median(rets)*100:+7.2f}%")
        if wo_max is not None:
            print(f"  {'':<30} 去最大1笔={wo_max*100:+8.2f}%"
                  f"（最大一笔 {srt[-1]*100:+.2f}%，占算术和 "
                  f"{(srt[-1]/sum(rets)*100) if sum(rets) else float('nan'):+.0f}%）  {note}")

    print("\n" + "=" * 78)
    print("【A】episode 级 vs trade 级 —— 三套口径并列")
    print("=" * 78)
    if rows_out:
        _sum_line("① episode 级（腿 S，压缩后）", [r["s"] for r in rows_out],
                  "← 原口径：只含每段首个入场，笔数/累计都≠实盘")
    else:
        print("  ① episode 级（腿 S） 无样本")
    if chain_cap:
        _sum_line(f"② 链式 trade 级（腿 S'，槽≤{args.max_open}）",
                  [t["ret"] for t in chain_cap],
                  f"因槽位被跳过 {len(diag_cap['skipped_slots'])} 笔")
    _sum_line("②b 链式 trade 级（不限槽）", [t["ret"] for t in chain_unlim],
              "← 上界；实盘受 max_open_trades 限制")
    if real:
        _sum_line("③ 实盘 DB（全部已平仓）", [t["ret"] for t in real],
                  "← 唯一不含建模假设的口径")
        _sum_line(f"③b 实盘 DB 前向样本（≥{fwd_since:%Y-%m-%d}UTC）",
                  [t["ret"] for t in real_fwd], "← §2 主判据的样本")
    chain_fwd = [t for t in chain_cap if t["T"] >= fwd_since]
    if chain_fwd:
        _sum_line(f"②c 链式 trade 级（同窗口 ≥{fwd_since:%Y-%m-%d}UTC）", 
                  [t["ret"] for t in chain_fwd], "← 与 ③b 同窗口，口径唯一差别是建模")
    if rows_out and chain_cap:
        d = sum_arith([t["ret"] for t in chain_cap]) - sum_arith([r["s"] for r in rows_out])
        print(f"\n  ★ episode 压缩带来的累计差：链式 − episode = {d*100:+.2f}pp"
              f"（{len(chain_cap)} 笔 vs {len(rows_out)} 个 episode）")
        print("    差值符号不是先验的：本口径下若为负 = 原口径隐藏了重入的累计亏损，")
        print("    若为正 = 原口径反而丢掉了重入后的收益（首个入场未必是最好的一笔）。")
        print("    无论正负，① 都不代表「该策略真实会做多少笔、真实累计多少」。")
        # 分标的看方向：谁被隐藏了亏损、谁被隐藏了收益
        epi_by = collections.defaultdict(list)
        for r in rows_out:
            epi_by[r["sym"]].append(r["s"])
        ch_by = collections.defaultdict(list)
        for t in chain_cap:
            ch_by[t["sym"]].append(t["ret"])
        print(f"\n  分标的（① episode 只能读「首个入场」，② 链式含全部重入）：")
        print(f"    {'标的':<8}{'n_epi':>6}{'Σ_epi':>10}{'n_chain':>9}{'Σ_chain':>10}"
              f"{'Δ(②−①)':>10}   方向")
        for sym in sorted(ch_by, key=lambda s: -abs(sum(ch_by[s]) - sum(epi_by[s]))):
            if sym not in epi_by:
                continue
            se, sc = sum(epi_by[sym]), sum(ch_by[sym])
            arrow = "②更好" if sc > se else ("②更差" if sc < se else "相同")
            print(f"    {sym:<8}{len(epi_by[sym]):>6}{se*100:>9.2f}%{len(ch_by[sym]):>9}"
                  f"{sc*100:>9.2f}%{sc*100-se*100:>9.2f}pp   {arrow}")
    if diag_cap["skipped_data"]:
        u = sorted({(f"{t:%m-%d %H:%M}UTC", s) for t, s in diag_cap["skipped_data"]})
        print(f"  ⚠️ 链式口径另有 {len(diag_cap['skipped_data'])} 笔因 **1m 归档缺口**"
              f"无法测量（未计入上表，偏差方向不明）：{u[:8]}{' …' if len(u) > 8 else ''}")
    print(f"  口径声明：本次重放**未开门③**（= 方案C 之后的规则）；09-18 之前的实盘是"
          f"门③开（ZEC/BR 通过率 0%）。故 ①/② 与 ③ 的差里含「门③随时间变化」这一项，"
          f"\n            不是纯建模差；另 09-19 之前实盘还受 §6.1 信号裁决缺陷影响（重放用的是修复后规则）。")
    if real_fwd:
        sd = statistics.stdev([t["ret"] for t in real_fwd]) if len(real_fwd) > 1 else None
        if sd is not None:
            mde = Z_SUM * sd / math.sqrt(len(real_fwd))
            print(f"  前向样本 n={len(real_fwd)}、sd={sd*100:.2f}% → "
                  f"当前每笔 MDE≈±{mde*100:.2f}%（sd 本身由个位数样本估出，不可靠）")
            need = (Z_SUM * sd / 0.012) ** 2
            print(f"  按 sd 反推 §2.4「每笔 MDE≈1.2%」所需样本：≈{need:.0f} 笔"
                  f"（§2.4 的估法用的是 −5% 截尾后的 sd=2.6%，此处是实测量，仅作对照）")

    if real:
        print("\n  标的集中度（实盘）：" + "  ".join(
            f"{s}={c}({c/len(real)*100:.0f}%)"
            for s, c in collections.Counter(t["sym"] for t in real).most_common()))

    # ---------------- B：重入链 ----------------
    print("\n" + "=" * 78)
    print("【B】同标的重入统计")
    print("=" * 78)

    def _reentry_block(title, trades, stats_rows):
        print(f"\n  {title}")
        if not stats_rows:
            print("    无重入（该口径下每标的最多一笔）")
            return
        print(f"    重入笔数 {len(stats_rows)}   其中上笔为 stop 的 "
              f"{sum(1 for r in stats_rows if r['after_stop'])}")
        # 必须拆开：gap=0 是「阴线在边界收盘平仓 → 同一边界立刻再入」，
        # gap>0 才是「出场发生在窗口中间 → 等下一个合格边界」。混在一起中位数恒为 0。
        same = [r for r in stats_rows if r["gap_h"] <= 1e-9]
        later = [r for r in stats_rows if r["gap_h"] > 1e-9]
        for lab, grp in (("同边界再入(gap=0)", same), ("跨边界再入(gap>0)", later)):
            if not grp:
                print(f"    {lab}：0 笔")
                continue
            g = [r["gap_h"] for r in grp]
            rr = [r["ret"] for r in grp]
            print(f"    {lab}：{len(grp)} 笔  间隔中位 {statistics.median(g):6.2f}h "
                  f"(min {min(g):5.2f} max {max(g):5.2f})  "
                  f"再入后 mean {statistics.fmean(rr)*100:+7.2f}%  "
                  f"胜率 {sum(1 for v in rr if v>0)/len(rr)*100:5.1f}%  算术和 {sum(rr)*100:+7.2f}%")
        gaps = [r["gap_h"] for r in stats_rows]
        print(f"    全部重入：间隔 中位 {statistics.median(gaps):.2f}h  "
              f"最小 {min(gaps):.2f}  最大 {max(gaps):.2f}  "
              f"（<0 = 与上笔重叠，属建模/数据问题，须单独看）")
        rets = [r["ret"] for r in stats_rows]
        print(f"    再入后收益：mean {statistics.fmean(rets)*100:+.2f}%  "
              f"median {statistics.median(rets)*100:+.2f}%  "
              f"胜率 {sum(1 for v in rets if v>0)/len(rets)*100:.1f}%  "
              f"算术和 {sum(rets)*100:+.2f}%")
        stops = [r for r in stats_rows if r["after_stop"]]
        if stops:
            sr = [r["ret"] for r in stops]
            print(f"    其中「止损后立即再入」{len(stops)} 笔："
                  f"mean {statistics.fmean(sr)*100:+.2f}%  算术和 {sum(sr)*100:+.2f}%")
            vs = [r["vs_exit"] for r in stops if r["vs_exit"] is not None]
            if vs:
                print(f"      再入价 vs 上笔出场价：中位 {statistics.median(vs)*100:+.2f}%  "
                      f"正 {sum(1 for v in vs if v>0)}/{len(vs)} 笔（正 = 出场后原价接回）")
        print("    逐链：")
        for sym in sorted({r["sym"] for r in stats_rows}):
            sub = [t for t in trades if t["sym"] == sym]
            sub.sort(key=lambda t: t["T"])
            print(f"      {sym}（{len(sub)} 笔）：")
            for i, t in enumerate(sub):
                mark = ""
                if i:
                    m = next((r for r in stats_rows
                              if r["sym"] == sym and r["T"] == t["T"]), None)
                    if m:
                        mark = (f"   ← 重入 gap={m['gap_h']:+6.2f}h "
                                f"vs上笔出场={('' if m['vs_exit'] is None else format(m['vs_exit']*100, '+6.2f')+'%')}"
                                f" {'[上笔止损]' if m['after_stop'] else ''}")
                print(f"        #{i+1} {t['T']:%m-%d %H:%M}UTC 入场={t['entry']:<11.6g} "
                      f"收益={t['ret']*100:+7.2f}% 持有={t['hold_h']:5.1f}h {t['tag']:<7}"
                      f"链计={sum(x['ret'] for x in sub[:i+1])*100:+7.2f}%{mark}")

    _reentry_block("重放链式腿 S'（无冷却、同标的持仓期间不重复入场）",
                   chain_cap, rc)
    if real:
        _reentry_block(f"实盘 DB（{os.path.basename(args.trades_db)}）", real, real_chain)

    # ---------------- C：bear4h 出场质量 ----------------
    print("\n" + "=" * 78)
    print(f"【C】bear4h 出场质量（出场后 {POST_EXIT_H}h 走势；阈值 ±5%）")
    print("=" * 78)
    print("  判读：出场后 48h 最高价 ≥ 出场价×1.05 → 错过上行(卖飞)；")
    print("        出场后 48h 最低价 ≤ 出场价×0.95 → 避开下行(锁利润)；")
    print(f"        覆盖 < {MIN_COVER_H:.1f}h → 数据不足（不猜，归档未发布是常态）")

    def _quality_block(title, trades, counterfactual=None, fallback=None):
        bear = [t for t in trades if t["tag"] == "bear4h"]
        print(f"\n  {title}：bear4h 出场 {len(bear)} 笔 / 全部 {len(trades)} 笔")
        if not bear:
            print("    —— 一笔都没有。这本身是结论的一部分：该出场规则仍无发言机会。")
            return
        label_cnt = collections.Counter()
        for t in bear:
            bars = bars_fn(t["sym"], t["exit_t"], POST_EXIT_H)
            post = post_exit_stats(bars, t["exit_t"], t["exit_px"])
            verdict, missed, avoided = exit_verdict(post)
            label_cnt[verdict] += 1
            cf = None
            if counterfactual:
                cf = counterfactual(t)
            giveback = (t["mfe"] - t["ret"]) if t.get("mfe") is not None else None
            print(f"    {t['T']:%m-%d %H:%M}UTC {t['sym']:<7} 触发时={t['ret']*100:+7.2f}%  "
                  f"MFE={'' if t.get('mfe') is None else format(t['mfe']*100,'+7.2f')+'%'}  "
                  f"回吐={'' if giveback is None else format(giveback*100,'+7.2f')+'pp'}  "
                  f"持有={t['hold_h']:5.1f}h")
            if post is None:
                print("        出场后：无 K 线数据（归档未发布/该币无合约）→ 数据不足")
            else:
                print(f"        出场后 {post['have_h']:5.1f}h（覆盖 {post['n']} 根 1m）："
                      f"高 {post['post_hi']*100:+7.2f}%  低 {post['post_lo']*100:+7.2f}%  "
                      f"+24h {('' if post['c24'] is None else format(post['c24']*100,'+7.2f')+'%')}  "
                      f"+48h {('' if post['c48'] is None else format(post['c48']*100,'+7.2f')+'%')}")
            # 归档缺口下的兜底：用 **DB 里同标的后续成交** 拼一条粗轨迹（非 1m）
            if fallback and (post is None or post["have_h"] < MIN_COVER_H):
                nxt = [o for o in fallback if o["sym"] == t["sym"]
                       and t["exit_t"] < o["T"] <= t["exit_t"] + timedelta(hours=POST_EXIT_H)]
                if nxt:
                    nxt.sort(key=lambda o: o["T"])
                    print(f"        ↳ 归档缺口兜底（来自 DB 成交，**粗口径**、非 1m；"
                          f"缺分钟级极值，不能据此判「卖飞/锁利润」）：")
                    for o in nxt[:4]:
                        dh = (o["T"] - t["exit_t"]).total_seconds() / 3600
                        hi = lo = None
                        if o.get("mfe") is not None:
                            hi = o["entry"] * (1 + o["mfe"]) / t["exit_px"] - 1
                        if o.get("mae") is not None:
                            lo = o["entry"] * (1 + o["mae"]) / t["exit_px"] - 1
                        print(f"            +{dh:5.1f}h  下一笔#{o.get('id','?')} 入场价 = "
                              f"出场价×{o['entry']/t['exit_px']:.4f}"
                              f"（{o['entry']/t['exit_px']-1:+.2%}）"
                              f"  该笔期间最高 = 出场价×{('' if hi is None else format(1+hi,'.4f'))}"
                              f"（{'' if hi is None else format(hi,'+.2%')}）"
                              f"  最低 = 出场价×{('' if lo is None else format(1+lo,'.4f'))}"
                              f"（{'' if lo is None else format(lo,'+.2%')}）")
                    print(f"            ⇒ 该窗口内共 {len(nxt)} 笔后续成交；"
                          f"只能判「出场后价格是否曾高于出场价」，**幅度不可信**"
                          f"（只有成交时刻的点位）。")
            if cf is not None:
                print(f"        反事实「不出场、持满 {MAX_HOLD_H}h」={cf*100:+7.2f}%  "
                      f"实际 bear4h={t['ret']*100:+7.2f}%  "
                      f"Δ={(t['ret']-cf)*100:+7.2f}pp"
                      f"（{'出场保留了收益/避损' if t['ret']>cf else '出场损失了收益'}）")
            if verdict == "数据不足":
                print(f"        → 判定：**数据不足**（覆盖不足，门槛不因结果放宽）；"
                      f"**已覆盖窗口内** 错过上行={missed} 避开下行={avoided}"
                      f" —— 幅度可能仍在扩大，不作判决")
            else:
                print(f"        → 判定：{verdict}（错过上行={missed} 避开下行={avoided}）")
        print(f"    汇总：" + "  ".join(f"{k}={v}" for k, v in label_cnt.most_common()))

    def _cf(t):
        """反事实：不出场、裸持满 48h（与腿 H 同口径，无止损）。"""
        return leg_hold(bars_fn(t["sym"], t["T"], MAX_HOLD_H), t["T"])

    _quality_block("重放 episode 级（腿 S，结论只能读这一套）",
                   [r["s_detail"] for r in rows_out], _cf)
    _quality_block("链式 trade 级（腿 S'，含全部重入）", chain_cap, _cf)
    if real:
        _quality_block("实盘 DB", real, _cf, fallback=real)
        sl = [t for t in real if t["tag"] == "stop"]
        if sl:
            print(f"\n  附：止损滑点（§6.11 B 观测项，{len(sl)} 笔 stop）")
            for t in sl:
                s = stop_slippage(t)
                if not s:
                    continue
                print(f"    #{t['id']} {t['sym']:<6} 预定止损位={s['level']:<12.6g} "
                      f"实际成交={s['fill']:<12.6g} 偏离={s['dev']*100:+.2f}%  "
                      f"记账={s['booked']*100:+.2f}%")

    if True:      # 数据质量段与是否读 DB 无关（归档缺口来自重放侧）
        print("\n  -- 数据质量 / 覆盖（缺数据必须与结论一起读）--")
        if FETCH_MISS:
            print("  ⚠️ 币安归档缺口（data.binance.vision 逐日 1m 取不到）：")
            by_sym = collections.defaultdict(list)
            for (s, d), n in FETCH_MISS.items():
                by_sym[s].append(d)
            for s, days in sorted(by_sym.items()):
                print(f"     {s}: {', '.join(sorted(days))}"
                      f"（{len(days)} 天，共被请求 {sum(FETCH_MISS[(s,d)] for d in days)} 次）")
            # 判定是「全局滞后」还是「该标的特有」——直接拿参考标的同日试，不猜
            for d in sorted({d for _, d in FETCH_MISS}):
                got = [r for r in ("BTC", "ZEC") if _er.fetch_1m(r, d)]
                scope = ("全局（参考标的 BTC/ZEC 同日也取不到 → 当日归档尚未发布）"
                         if not got else
                         f"该标的特有（参考标的有数据：{got}）")
                print(f"     {d}: {scope}")
            print("     后果：出场/入场窗口被**静默截断** → 上面对应笔标「数据不足」或无法测量。")
            print("     ⚠️ 该缺口不等于「没卖飞/没亏」，是**看不到**。归档按天发布且当日常有滞后，")
            print("        跑得越近当下，出场后的观察窗越是天然残缺 —— 这是数据源性质，不是 bug。")
        else:
            print("  归档无缺口。")
        if NONNUM_RAW:
            tot = sum(NONNUM_RAW.values())
            ex = "、".join(f"'{k}'(×{v})" for k, v in NONNUM_RAW.most_common(3))
            print(f"  协变量写入格式：本次样本里 {tot} 行是**旧格式**（例：{ex}）")
            print("     写入端已于 2026-09-21 修复并上线（`_parse_signal_peak`，落纯数值）；"
                  "历史行保持原样，")
            print("     分析侧的容错解析保证两种格式都能吃 → score_over_peak 不再是空维度。")
        ages = [r["signal_age_h"] for r in rows_out if r["signal_age_h"] is not None]
        if ages:
            old = [a for a in ages if a > 720]
            print(f"  信号年龄：min {min(ages):.1f}h  max {max(ages):.1f}h  "
                  f">720h(30天) 的有 {len(old)} 笔")
            if old:
                print("     ↑ 这些行的 age 已失去「第 1 小时 vs 第 5 天」的含义"
                      "（ZEC 的 start_time 落在开采日之前 ~3.6 个月），"
                      "读二维格子时不要把 age 当作行情阶段。")

    # ---------------- C2：实盘 vs 重放的入场对齐 ----------------
    if real:
        print("\n" + "=" * 78)
        print("【C2】实盘出场笔 vs 重放事件对齐（找重放与 bot 的口径差）")
        print("=" * 78)
        for t in real:
            cands = [e for e in events if e[1] == t["sym"]
                     and abs((e[0] - t["T"]).total_seconds()) <= 3600]
            if cands:
                e = min(cands, key=lambda e: abs((e[0] - t["T"]).total_seconds()))
                dm = (t["T"] - e[0]).total_seconds() / 60
                print(f"  #{t['id']:<2} {t['T']:%m-%d %H:%M}UTC {t['sym']:<7} "
                      f"← 边界 {e[0]:%m-%d %H:%M}（滞后 {dm:+.1f} 分钟）")
            else:
                print(f"  #{t['id']:<2} {t['T']:%m-%d %H:%M}UTC {t['sym']:<7} "
                      f"← **重放该边界无合格事件**（快照缺失 / 未进 top{TOP_N} / "
                      f"score<{MIN_SCORE:g}{' / 被僵尸门拦' if zgate else ''}），须查事件枚举口径")

    if not rows_out and not real:
        print("\n无可测量的 episode（1m 归档缺失或窗口未走完）。")
        return 1

    if rows_out:
        print("\n" + "=" * 78)
        print("【原】逐腿分布 / 配对差 / 协变量")
        print("=" * 78)
        print(f"\n可测量 episode：{len(rows_out)}")
        print("\n=== 逐腿分布 ===")
        report_leg("腿 S 策略腿", [r["s"] for r in rows_out])
        report_leg("腿 X 止损+48h无阴线", [r["x"] for r in rows_out if r["x"] is not None])
        report_leg("腿 H 裸持48h", [r["h"] for r in rows_out if r["h"] is not None])
        report_leg("腿 B BTC 同窗口", [r["b"] for r in rows_out if r["b"] is not None])
        report_leg("腿 L score60-80 不过门", [r["l_un"] for r in rows_out if r["l_un"] is not None])
        if zgate:
            report_leg("腿 L score60-80 过门", [r["l_ga"] for r in rows_out if r["l_ga"] is not None])
            print(f"    (腿 L 僵尸门不可得 {lband_unavail} 行，见局限 3)")
        r_rows = [r for r in rows_out if r["r"] is not None]
        report_leg("腿 R rank4-10", [r["r"] for r in r_rows])
        print(f"    (腿 R 有数据的 episode {len(r_rows)}/{len(rows_out)}，"
              f"覆盖标的 {sum(r['r_n'] for r in r_rows)} 个次；**样本薄，n 个位数时不得做方向性陈述**)")

        print("\n=== 出场标记分布 ===")
        print("  腿 S：" + "  ".join(
            f"{tag}={c}({c/len(rows_out)*100:.0f}%)"
            for tag, c in collections.Counter(r["tag"] for r in rows_out).most_common()))
        x_tags = [r["x_tag"] for r in rows_out if r["x_tag"]]
        if x_tags:
            print("  腿 X：" + "  ".join(
                f"{tag}={c}({c/len(x_tags)*100:.0f}%)"
                for tag, c in collections.Counter(x_tags).most_common()))
        print("  ↑ 腿 S 的 bear4h 若为 0，说明阴线出场没有发言机会（止损先打掉），"
              "这是止损档与 timeframe 的结构性错配，另立预注册处理")
        print("  ↑ 腿 X 里 stop 的占比 = 关掉阴线出场后有多少笔改由止损接管")

        print("\n=== 配对差（日区块自举 2000 次，先看 MDE 再看点估计）===")
        report_diff("S − X（阴线出场纯贡献）", [(r["T"].date(), r["s"] - r["x"])
                                                for r in rows_out if r["x"] is not None])
        report_diff("X − H（止损贡献）", [(r["T"].date(), r["x"] - r["h"])
                                          for r in rows_out
                                          if r["x"] is not None and r["h"] is not None])
        report_diff("S − H（两者合计，勿单独解读）", [(r["T"].date(), r["s"] - r["h"])
                                                    for r in rows_out if r["h"] is not None])
        report_diff("S − B（剥 beta）", [(r["T"].date(), r["s"] - r["b"])
                                         for r in rows_out if r["b"] is not None])
        report_diff("S − L（80 这条线）", [(r["T"].date(), r["s"] - r["l_un"])
                                          for r in rows_out if r["l_un"] is not None])
        if zgate:
            report_diff("S − L 过门", [(r["T"].date(), r["s"] - r["l_ga"])
                                       for r in rows_out if r["l_ga"] is not None])
        report_diff("S − R（top3 截断）", [(r["T"].date(), r["s"] - r["r"])
                                          for r in rows_out if r["r"] is not None])

        print("\n=== 协变量分层（一维）===")
        report_buckets("signal_age", rows_out, "signal_age_h", AGE_BUCKETS, "h")
        report_buckets("prior_gain", rows_out, "prior_gain_pct", GAIN_BUCKETS, "%")
        report_buckets("score_over_peak", rows_out, "score_over_peak",
                       SCORE_OVER_PEAK_BUCKETS, "")

        # ---------------- D：二维 ----------------
        print("\n" + "=" * 78)
        print("【D】signal_age × prior_gain 二维（立项假说：老信号 × 已大涨）")
        print("=" * 78)
        report_2d("episode 级（腿 S）", rows_out,
                  "signal_age_h", AGE_BUCKETS, "h",
                  "prior_gain_pct", GAIN_BUCKETS, "%", "s", "腿S")

        print("\n=== 逐笔 ===")
        for r in sorted(rows_out, key=lambda x: x["T"]):
            f = lambda v: f"{v*100:+7.2f}%" if v is not None else "      -"
            g = lambda v, w=6, p=1: (f"{v:{w}.{p}f}" if v is not None else " " * w)
            print(f"  {r['T']:%m-%d %H:%M} {r['sym']:<7} score={r['score']:6.2f}  "
                  f"S={f(r['s'])} X={f(r['x'])} H={f(r['h'])} B={f(r['b'])} "
                  f"L={f(r['l_un'])}(n={r['l_un_n']}) R={f(r['r'])}(n={r['r_n']})  "
                  f"age={g(r['signal_age_h'])}h gain={g(r['prior_gain_pct'],7)}% "
                  f"s/pk={g(r['score_over_peak'],5,3)}  "
                  f"持有={r['hold']:5.1f}h {r['tag']}")

    if real:
        print("\n=== 实盘成交台账（DB=UTC，展示=CST；重放侧时间一律 UTC，两套差 8h 勿混读）===")
        print(f"  {'id':<3}{'标的':<9}{'开仓CST':<18}{'平仓CST':<18}{'出场':<18}"
              f"{'收益':>8}{'金额':>10}{'持有h':>7}  链计")
        cum = 0.0
        for t in real:
            cum += t["ret"]
            hold_h = (t["exit_t"] - t["T"]).total_seconds() / 3600
            print(f"  #{t['id']:<2}{t['sym']:<9}"
                  f"{t['T'].astimezone(CST):%m-%d %H:%M:%S}   "
                  f"{t['exit_t'].astimezone(CST):%m-%d %H:%M:%S}   "
                  f"{t['raw_tag']:<18}{t['ret']*100:+7.2f}%{t['profit_abs']:+10.2f}"
                  f"{hold_h:7.1f}  {cum*100:+7.2f}%")
        print(f"  合计（算术和，§2.1 口径）= {sum_arith([t['ret'] for t in real])*100:+.2f}%  "
              f"金额 {sum(t['profit_abs'] for t in real):+.2f} USDT")

    print("\n怎么读：任何 CI 跨零的行只能写「无 ≥MDE 量级的效应」。协变量分层是描述性的。")
    print("【A】三套口径的数**必须一起报**：① 只测每段首个入场（笔数与累计都不等于实盘，")
    print("     偏差符号不定）；② 建模假设（同 bar 止损优先、止损价成交、槽位按 score 降序）；")
    print("     ③ 唯一不含建模假设。★ 任何「② 是正的」结论先做「去最大1笔」再看。")
    print("【C】bear4h 出场质量：**重复出场共享同一条价格路径**，n_笔 ≠ n_有效；")
    print("     归档覆盖不足的笔只能标「数据不足」，不得当「中性」读。")
    print("本脚本是观测，不是判定 —— GATE3 §2 的主判据不因这里的结果改变。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
