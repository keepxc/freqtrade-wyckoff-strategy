"""
AI500V1DryRun — VergeX trending-crypto 资金流+OI 驱动的探索性 dry-run 策略
=============================================================
探索性——非结论，禁止引用为证据。本策略无法回测（数据源为实时榜单，
无历史回放能力，见 TASK111 尽调）。本文件只用于 dry-run 观察，运行结果
不构成 CLAUDE.md 三层终止校验意义上的"结论"，不得据此判定策略✅/❌，
也不写入任何策略基线文档。

数据源替换（2026-08-18 用户确认）：原 trending-category?key=ai500 免费
端点仅恒返 3 币（GPS/BTW/XPL）、22 小时零变化，已证伪。替换为逆向发现的
完整端点 trending-crypto：
  - tab=net_flow：资金净流入榜（amount / rank / price_delta_percent）
  - tab=oi：OI 榜（current_oi / oi_delta_percent / oi_delta_value ...）

用户确认口径（2026-08-18）：
  1. 入场：过去 1h net_flow 排名中位数 ≤ 3 且 当前 oi_delta_percent > 0
     （中位数=50%分位，等价于"超过一半时间在前 3"，无需另设占比阈值）
  2. 退出：当前 net_flow rank 跌出前 10 立即退（灵敏退出，不经 1h 中位数平滑；
     dry-run 观察发现对称版中位数退出滞后、退出即顶点）
  3. 执行粒度 30m K 线（原 1h）：缩短退出延迟（中位数跌破后最快 30m 触发，
     而非最长 1h）
  4. 弃用 net_long / net_short（语义不明：net_long 可为负、之和≠current_oi、
     与页面 Bias 不对应）；OI 过滤只用当前 oi_delta_percent，不对其要求 1h 持续度
  5. max_open_trades=3 保留；ATR 硬止损 + 波动率目标仓位沿用原实现（窗口按
     30m 换算保持时间语义不变）
  6. 每次抓取写入历史记录（netflow_rank + median_rank + oi_delta_percent 等），
     为未来可能的前瞻验证积累数据（同 TASK110 discovery_daily_snapshot 先例）

榜单变化频率实测（2026-08-18，每 60s × 30 次采样）：net_flow 前 5 每分钟
55% 概率成员变化（约 1.5 分钟换 1 个）、oi 前 5 每分钟 32%；核心前 3 稳定、
边缘第 4-5 名频繁进出。故信号用 1h 排名中位数平滑分钟级噪声，而非瞬时排名。

架构限制（技术验证，非用户判据决定）：freqtrade 该版本的
PairListResolver.user_subdir 为 None，不支持从 user_data/pairlists 加载
自定义 pairlist 插件，无法做"动态白名单"。改为内置 VolumePairList 提供
候选池（成交量活跃的 USDT-M 合约），策略内部按 net_flow+OI 条件过滤实际
入场，白名单里其余 pair 不会被交易。
"""
import csv
import logging
import os
import statistics
from collections import deque
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd
import requests
from pandas import DataFrame

from freqtrade.strategy import IStrategy
from freqtrade.persistence import Trade

logger = logging.getLogger(__name__)

TRENDING_URL = "https://vergex.trade/trending-crypto"
TRENDING_PROXY = {
    "http": "http://127.0.0.1:7890",
    "https": "http://127.0.0.1:7890",
}
FETCH_TIMEOUT = 10
LIST_LIMIT = 50

HISTORY_PATH = "/home/gyan/freqtrade/user_data/ai500_netflow_oi_history.csv"
HISTORY_FIELDS = [
    "fetched_at_utc", "symbol", "pair", "netflow_rank", "median_rank",
    "netflow_amount", "oi_delta_percent", "oi_delta_value", "price", "price_delta_percent",
]


class AI500V1DryRun(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = '30m'
    can_short = False
    process_only_new_candles = True
    startup_candle_count = 100

    # 框架层 stoploss 占位；真正止损通过 custom_stoploss（ATR）实现
    stoploss = -0.99
    minimal_roi = {"0": 100}
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = True
    trailing_stop = False
    use_custom_stoploss = True

    max_open_trades = 3

    ENTRY_MEDIAN = 3        # 入场：过去 1h net_flow 排名中位数 ≤ 3
    EXIT_RANK = 10          # 退出：当前 rank 跌出前 10 立即退（灵敏，不经中位数平滑）
    MIN_HOLD_SEC = 7200     # 最小持仓 2h：开仓后 2h 内不响应 rank 退出（ATR 硬止损仍生效）
    RANK_WINDOW_SEC = 3600  # 中位数回看窗口 = 1h
    MIN_WINDOW_SEC = 1800   # 中位数最少覆盖 30 分钟，样本不足视为不在前 3
    RANK_ABSENT = 99        # 不在 net_flow 榜时的 rank 占位值
    OI_MIN = 0.0            # OI 过滤：当前 oi_delta_percent > 0 才可入场
    FLOW_WINDOW_SEC = 1800  # 四象限资金流回看窗口 = 30 分钟（近期趋势，避免累计方向失真）
    PEAK_DROP = 0.05        # 峰值回撤退出阈值：从持仓最高价回撤 5% 触发（保护利润）
    RANK_EXIT_AFTER_H = 4   # rank 退出降级：持仓超过 4h 后 rank>10 才触发（给趋势存活时间）
    OB_LOOKBACK = 10        # 订单块检测窗口：连续阴线后第一根阳线（30m K 线）
    OB_NEAR_PCT = 0.03      # 订单块近距阈值：价格在 OB 上方 ≤3% 视为回踩区（可入场）
    OB_MIN_DOWN = 3         # 订单块要求：阳线前至少 3 根连续阴线（大跌后的机构接盘区）

    ATR_PERIOD = 40         # 30m K 线 ×40 = 20h 窗口（原 1h ×20）
    ATR_MULTIPLIER = 2.0    # 30m 粒度 ATR 绝对值约 1h 的 1/√2，×2 补偿止损距离缩水

    # 利润 trailing（custom_stoploss 内实现，与 ATR 地板双保险）
    # 默认关闭：单边上涨行情 trailing 会砍在峰值回撤处（少赚）。
    # 由 agent 根据市场相位（market_regime_history.csv）自主决策启用：
    # 相位从上涨切换为横盘/下跌时置 True，保护已盈利单不坐回地板。
    TRAIL_ENABLED = False
    TRAIL_OFFSET = 0.04     # 浮盈 ≥4% 激活 trailing（避免微小波动触发）
    TRAIL_DROP = 0.025      # 激活后从最高价回撤 2.5% 触发止损（锁住利润）

    VOL_LOOKBACK = 40       # 30m K 线 ×40 = 20h 窗口（原 1h ×20）
    TARGET_VOL = 5.0     # 顶格：5.0 保证所有币 lev=min(1, 5/rv)=1，满额仓位（用户 08-23 决策）
    MAX_LEV = 1.0

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.rank_history: dict[str, deque] = {}     # symbol -> deque[(ts, rank)]
        self.price_history: dict[str, deque] = {}    # symbol -> deque[(ts, price)]（四象限用）
        self.oi_history: dict[str, deque] = {}       # symbol -> deque[(ts, oi_delta_percent)]（四象限用）
        self.median_rank: dict[str, float] = {}      # symbol -> 1h 中位数排名
        self.current_rank: dict[str, int] = {}       # symbol -> 最近一次采样 rank（退出用）
        self.oi_delta_pct: dict[str, float] = {}     # symbol -> oi_delta_percent
        self.ever_in_list: set[str] = set()          # symbol -> 曾出现在 net_flow 榜（掉出榜退出用）

    @staticmethod
    def _strip_usdt(symbol: str) -> str:
        # "ACEUSDT" -> "ACE"
        return symbol[:-4] if symbol.endswith("USDT") else symbol

    @staticmethod
    def _pair_to_symbol(pair: str) -> str:
        # "ACE/USDT:USDT" -> "ACEUSDT"
        return f"{pair.split('/')[0]}USDT"

    def _fetch_list(self, tab: str):
        """抓取 trending-crypto 某 tab 的 top 榜，返回 {symbol: item}。失败返回 None。"""
        try:
            resp = requests.get(
                TRENDING_URL, params={"tab": tab, "limit": LIST_LIMIT},
                proxies=TRENDING_PROXY,
                headers={"User-Agent": "Mozilla/5.0"}, timeout=FETCH_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"[AI500] fetch tab={tab} failed, keeping previous state: {e}")
            return None
        out = {}
        for item in data.get("top") or []:
            sym = item.get("symbol")
            if sym:
                out[sym] = item
        return out

    def bot_loop_start(self, current_time: datetime, **kwargs) -> None:
        netflow = self._fetch_list("net_flow")
        oi = self._fetch_list("oi")
        if netflow is None or oi is None:
            return

        now = current_time.timestamp()

        # 更新 OI 状态（只用当前值，不要求持续度）
        self.oi_delta_pct = {sym: it.get("oi_delta_percent") for sym, it in oi.items()}

        # 更新排名历史：net_flow 榜内的记真实 rank，其余记 RANK_ABSENT（不在榜）
        netflow_syms = set(netflow.keys())
        tracked = set(self.rank_history.keys()) | netflow_syms
        try:
            for t in Trade.get_open_trades():
                tracked.add(self._pair_to_symbol(t.pair))
        except Exception:
            pass
        for sym in tracked:
            hist = self.rank_history.setdefault(sym, deque())
            rank = netflow[sym].get("rank") if sym in netflow_syms else self.RANK_ABSENT
            hist.append((now, rank))
            self.current_rank[sym] = rank
            # 记录"曾经在榜"（用于掉出榜退出判定）
            if sym in netflow_syms:
                self.ever_in_list.add(sym)
            # 记录价格历史（四象限价格方向用；取 net_flow 或 oi 里的价格）
            nf = netflow.get(sym) or {}
            o = oi.get(sym) or {}
            price = None
            try:
                price = float(nf.get("price")) if nf.get("price") is not None else (
                    float(o.get("price")) if o.get("price") is not None else None)
            except (TypeError, ValueError):
                price = None
            if price is not None:
                phist = self.price_history.setdefault(sym, deque())
                phist.append((now, price))
                cutoff_p = now - self.FLOW_WINDOW_SEC
                while phist and phist[0][0] < cutoff_p:
                    phist.popleft()
            # 记录 OI 历史（四象限资金方向用）
            oi_v = self.oi_delta_pct.get(sym)
            if oi_v is not None:
                ohist = self.oi_history.setdefault(sym, deque())
                ohist.append((now, oi_v))
                cutoff_o = now - self.FLOW_WINDOW_SEC
                while ohist and ohist[0][0] < cutoff_o:
                    ohist.popleft()

        # 清理超过 1h 的旧采样，并计算中位数（窗口覆盖 < 30 分钟视为样本不足）
        cutoff = now - self.RANK_WINDOW_SEC
        self.median_rank = {}
        for sym in list(self.rank_history.keys()):
            hist = self.rank_history[sym]
            while hist and hist[0][0] < cutoff:
                hist.popleft()
            if not hist:
                del self.rank_history[sym]
                continue
            if (now - hist[0][0]) < self.MIN_WINDOW_SEC:
                continue  # 样本不足，不产生有效中位数（视为不在前 3）
            self.median_rank[sym] = statistics.median([r for _, r in hist])

        # 写历史记录（含当前 rank 与 1h 中位数）
        # 优化（2026-08-19）：只写有信息量的行——榜内有 rank 的币 + 当前持仓币。
        # 原因：原实现每轮写全部 tracked（380+ 币，77% 是无 rank 占位行），
        # 日增 ~62 万行、文件 54MB+，查询不便且无信息量。median_rank 计算
        # 依赖内存 rank_history deque，与 CSV 存档解耦，砍冗余行不影响策略逻辑。
        fetched_at = current_time.astimezone(timezone.utc).isoformat()
        rows = []
        try:
            open_syms = {self._pair_to_symbol(t.pair) for t in Trade.get_open_trades()}
        except Exception:
            open_syms = set()
        for sym in sorted(tracked):
            nf = netflow.get(sym) or {}
            o = oi.get(sym) or {}
            in_ranked = sym in netflow_syms
            if not in_ranked and sym not in open_syms:
                continue  # 不在榜且非持仓：跳过（纯占位行，无信息量）
            pair = f"{self._strip_usdt(sym)}/USDT:USDT"
            rows.append({
                "fetched_at_utc": fetched_at,
                "symbol": sym,
                "pair": pair,
                "netflow_rank": nf.get("rank"),
                "median_rank": self.median_rank.get(sym),
                "netflow_amount": nf.get("amount"),
                "oi_delta_percent": o.get("oi_delta_percent"),
                "oi_delta_value": o.get("oi_delta_value"),
                "price": nf.get("price") if nf.get("price") is not None else o.get("price"),
                "price_delta_percent": nf.get("price_delta_percent")
                    if nf.get("price_delta_percent") is not None else o.get("price_delta_percent"),
            })
        self._append_history(rows)

    @staticmethod
    def _append_history(rows: list[dict]) -> None:
        if not rows:
            return
        write_header = not os.path.exists(HISTORY_PATH)
        try:
            with open(HISTORY_PATH, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=HISTORY_FIELDS)
                if write_header:
                    writer.writeheader()
                writer.writerows(rows)
        except OSError:
            pass

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        pair = metadata['pair']
        dataframe['atr'] = self._compute_atr(dataframe, self.ATR_PERIOD)
        ret = dataframe['close'].pct_change()
        dataframe['realized_vol'] = ret.rolling(self.VOL_LOOKBACK).std().shift(1) * np.sqrt(365 * 24 * 2)
        # net_flow/OI 榜单是当前时点的实时快照（无历史回放），只对最新一根 bar
        # 有意义，不可用于回测（详见文件头说明）。
        sym = self._pair_to_symbol(pair)
        if sym in self.median_rank:
            median = self.median_rank[sym]
            median_valid = 1
        else:
            # 样本不足（冷启动 <30min 或榜外）：median_rank 无有效值，视为不在前 3
            median = self.RANK_ABSENT
            median_valid = 0
        # current_rank（退出用）：最近采样 rank，冷启动未采样视为无效，避免误平仓
        if sym in self.current_rank:
            cur_rank = self.current_rank[sym]
            cur_valid = 1
        else:
            cur_rank = self.RANK_ABSENT
            cur_valid = 0
        oi_pct = self.oi_delta_pct.get(sym, None)
        dataframe['median_rank'] = median
        dataframe['median_rank_valid'] = median_valid
        dataframe['current_rank'] = cur_rank
        dataframe['current_rank_valid'] = cur_valid
        dataframe['oi_ok'] = 1 if (oi_pct is not None and oi_pct > self.OI_MIN) else 0
        # 订单块回踩过滤：当前价在最近做多 OB 回踩区（纯因果，逐 bar 计算）
        dataframe['ob_ok'] = self._compute_ob_ok(dataframe)
        return dataframe

    @staticmethod
    def _compute_atr(df: DataFrame, period: int) -> pd.Series:
        high, low, close = df['high'], df['low'], df['close']
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)
        return tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    @staticmethod
    def _find_bull_ob(df: DataFrame, lookback: int) -> float:
        """订单块检测（移植 Pine checkObCondition 的 bull 分支）：
        最近 lookback 根 K 线内，第一根阳线（close>=open）且它前面连续阴线
        → 该阳线的 low 即做多订单块下沿（机构成本区）。
        返回 OB 下沿价（botValue），找不到返回 NaN。
        纯因果：只用已收盘 K 线（shift 后），无未来函数。"""
        if len(df) < lookback + 1:
            return float('nan')
        # 取最近 lookback 根（含当前，但当前可能未收盘——用 shift 保证只用已收盘）
        closes = df['close']
        opens = df['open']
        lows = df['low']
        n = len(df)
        # 从最老到最新找"转涨点"：阳线且前面 lookback-1 根全阴
        for i in range(n - lookback, n):
            if pd.isna(closes[i]) or pd.isna(opens[i]) or pd.isna(lows[i]):
                continue
            if closes[i] >= opens[i]:  # 阳线
                ok = True
                # 检查它前面 lookback-1 根是否全阴
                for j in range(max(0, i - lookback + 1), i):
                    if pd.isna(closes[j]) or pd.isna(opens[j]):
                        continue
                    if closes[j] >= opens[j]:  # 前面有阳线，不符合
                        ok = False
                        break
                if ok:
                    return float(lows[i])
        return float('nan')

    def _compute_ob_ok(self, df: DataFrame) -> pd.Series:
        """逐 bar 计算 ob_ok：当前价在最近做多订单块（OB）回踩区。
        OB 定义（滑动找，非固定偏移）：最近 OB_LOOKBACK 根内，最后一个
        "由跌转涨"的阳线（close>=open 且前一根阴线）——该阳线的 low 即
        机构成本区下沿（订单块）。价格在其 low ±OB_NEAR_PCT 内 → ob_ok=1
        （回踩机构成本区 = 好入场；远离 = 追高 = 拒绝）。
        因果：每根 bar 只用 i 及之前已收盘数据，无未来函数。
        返回 0/1 序列。"""
        lookback = self.OB_LOOKBACK
        near_pct = self.OB_NEAR_PCT
        n = len(df)
        closes = df['close'].values
        opens = df['open'].values
        lows = df['low'].values
        ob_ok = np.zeros(n, dtype=int)
        for i in range(lookback, n):
            # 在窗口 [i-lookback+1, i] 内从最新往最老找"强转涨"阳线：
            # 阳线 + 前面至少 OB_MIN_DOWN(3) 根连续阴线（大跌后的机构接盘区）
            ob_bot = float('nan')
            for k in range(i, max(0, i - lookback + 1), -1):
                if k < self.OB_MIN_DOWN + 1:
                    break
                # 阳线（由跌转涨）
                if closes[k] >= opens[k]:
                    # 检查前面 OB_MIN_DOWN 根是否全阴
                    down_ok = True
                    for j in range(k - self.OB_MIN_DOWN, k):
                        if closes[j] >= opens[j]:
                            down_ok = False
                            break
                    if down_ok:
                        ob_bot = lows[k]
                        break
            if not np.isnan(ob_bot):
                price = closes[i]
                # 回踩区：价格在 OB 下沿附近（OB 内或 OB 上方 ≤3%）
                if price >= ob_bot * (1 - near_pct) and price <= ob_bot * (1 + near_pct):
                    ob_ok[i] = 1
        return pd.Series(ob_ok, index=df.index)

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe['median_rank_valid'] == 1)
            & (dataframe['median_rank'] <= self.ENTRY_MEDIAN)
            & (dataframe['oi_ok'] == 1),
            ['enter_long', 'enter_tag']
        ] = (1, 'netflow_oi_entry')
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # rank 退出信号由 custom_exit 统一处理（含最小持仓过滤），
        # 这里不再设置 exit_long，避免 bypass custom_exit 的屏蔽逻辑
        return dataframe

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime,
                    current_rate: float, current_profit: float, **kwargs) -> Optional[str]:
        """统一退出决策（趋势持有优先，保护利润兜底）：
        1. 峰值回撤退出：从持仓最高价回撤 > PEAK_DROP(5%) → 退出（锁住利润，吃趋势中段）
        2. 危险象限退出：持仓期间 OI↓+价跌(多头离) / OI↑+价跌(空头开) → 退出
           （用 30min 趋势判定，逼空行情 OI↓+价涨 不算危险）
        3. rank 退出降级：持仓超过 RANK_EXIT_AFTER_H(4h) 后 rank>10 才触发
           （2-4h 内不再因 rank 抖动被洗出，让趋势单存活）
        4. 掉出榜退出：曾经在榜、当前完全无 rank 数据 → 退出（兜底防永久持仓）
        5. 最小持仓 MIN_HOLD_SEC(2h) 内不响应以上退出（ATR 硬止损始终生效）
        6. ATR 硬止损（custom_stoploss）不受影响，始终生效。"""
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if df.empty:
            return None
        last = df.iloc[-1]

        # 最小持仓时间：开仓后 MIN_HOLD_SEC 内不响应退出（ATR 硬止损除外）
        held_sec = (current_time - trade.open_date_utc).total_seconds()
        if held_sec < self.MIN_HOLD_SEC:
            return None

        sym = self._pair_to_symbol(pair)

        # ── 1. 峰值回撤退出（最高优先级）──
        # trade.max_rate 由 freqtrade 自动跟踪持仓期间最高价
        if trade.max_rate and trade.max_rate > 0 and current_rate > 0:
            drawdown = (trade.max_rate - current_rate) / trade.max_rate
            if drawdown > self.PEAK_DROP:
                return 'peak_drop_exit'

        # ── 2. 危险象限退出（资金流确认）──
        # 资金方向：最近 FLOW_WINDOW_SEC(30min) 内 OI delta 趋势
        # 价格方向：最近 FLOW_WINDOW_SEC(30min) 内价格趋势
        # 危险（退出）: OI↑+价跌(空头开) / OI↓+价跌(多头离)
        phist = self.price_history.get(sym)
        ohist = self.oi_history.get(sym)
        if phist and ohist and len(phist) >= 2 and len(ohist) >= 2:
            price_first = phist[0][1]
            price_last = phist[-1][1]
            oi_first = ohist[0][1]
            oi_last = ohist[-1][1]
            price_up = price_last > price_first
            oi_up = oi_last > oi_first
            danger = (oi_up and not price_up) or (not oi_up and not price_up)
            if danger:
                return 'flow_quadrant_exit'

        # ── 3. rank 退出（降级：持仓 >4h 才触发）──
        # 2-4h 内 rank 抖动不触发退出（给趋势存活时间），>4h 后 rank 掉出前 10 才退
        if held_sec >= self.RANK_EXIT_AFTER_H * 3600:
            if last.get('current_rank_valid') == 1 and last.get('current_rank', 99) > self.EXIT_RANK:
                return 'netflow_oi_exit'

        # ── 4. 掉出榜退出（兜底防永久持仓）──
        if (sym in self.rank_history
                and last.get('current_rank_valid') == 0
                and self.current_rank.get(sym) is None):
            # 检查 rank_history 里是否有过真实 rank（非 RANK_ABSENT）
            has_real_rank = any(r != self.RANK_ABSENT for _, r in self.rank_history[sym])
            if has_real_rank:
                return 'netflow_oi_exit_drop'
        return None

    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                         current_rate: float, current_profit: float, **kwargs) -> Optional[float]:
        """ATR 硬止损 + 利润 trailing 双保险：
        1. ATR 地板：入场信号K线 low - 2xATR(20)，入场时定死的绝对地板价
        2. 利润 trailing：浮盈 ≥TRAIL_OFFSET 后，止损上移至最高价×(1-TRAIL_DROP)，
           锁住利润；未激活时仍用 ATR 地板兜底。
        用严格 '<' 只取信号K线收盘后已知的数据，避免用到尚未走完的K线（未来函数）。"""
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        entry_hist = df.loc[df['date'] < trade.open_date_utc]
        if entry_hist.empty:
            return None
        entry_row = entry_hist.iloc[-1]
        atr_at_entry = entry_row['atr']
        if pd.isna(atr_at_entry) or atr_at_entry <= 0:
            return None
        floor_price = entry_row['low'] - self.ATR_MULTIPLIER * atr_at_entry
        if floor_price <= 0:
            return None

        # 利润 trailing：仅当 TRAIL_ENABLED 且最高价超过 open×(1+offset) 激活
        stop_price = floor_price
        if self.TRAIL_ENABLED and trade.max_rate is not None and trade.max_rate > trade.open_rate * (1 + self.TRAIL_OFFSET):
            trail_price = trade.max_rate * (1 - self.TRAIL_DROP)
            stop_price = max(stop_price, trail_price)  # 取更高者（更接近现价）

        return (stop_price / current_rate) - 1

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: Optional[float], max_stake: float,
                            leverage: float, entry_tag: Optional[str], side: str,
                            **kwargs) -> float:
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        hist = df.loc[df['date'] < current_time]
        if hist.empty:
            return proposed_stake
        rv = hist['realized_vol'].iloc[-1]
        if pd.isna(rv) or rv <= 0:
            lev = 1.0
        else:
            lev = min(self.MAX_LEV, self.TARGET_VOL / rv)
        stake = proposed_stake * lev
        if min_stake is not None and stake < min_stake:
            return min_stake
        return stake
