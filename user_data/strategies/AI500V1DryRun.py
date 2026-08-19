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
    RANK_WINDOW_SEC = 3600  # 中位数回看窗口 = 1h
    MIN_WINDOW_SEC = 1800   # 中位数最少覆盖 30 分钟，样本不足视为不在前 3
    RANK_ABSENT = 99        # 不在 net_flow 榜时的 rank 占位值
    OI_MIN = 0.0            # OI 过滤：当前 oi_delta_percent > 0 才可入场

    ATR_PERIOD = 40         # 30m K 线 ×40 = 20h 窗口（原 1h ×20）
    ATR_MULTIPLIER = 2.0    # 30m 粒度 ATR 绝对值约 1h 的 1/√2，×2 补偿止损距离缩水

    VOL_LOOKBACK = 40       # 30m K 线 ×40 = 20h 窗口（原 1h ×20）
    TARGET_VOL = 0.20
    MAX_LEV = 1.0

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.rank_history: dict[str, deque] = {}     # symbol -> deque[(ts, rank)]
        self.median_rank: dict[str, float] = {}      # symbol -> 1h 中位数排名
        self.current_rank: dict[str, int] = {}       # symbol -> 最近一次采样 rank（退出用）
        self.oi_delta_pct: dict[str, float] = {}     # symbol -> oi_delta_percent

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

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe['median_rank_valid'] == 1)
            & (dataframe['median_rank'] <= self.ENTRY_MEDIAN)
            & (dataframe['oi_ok'] == 1),
            ['enter_long', 'enter_tag']
        ] = (1, 'netflow_oi_entry')
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 仅当 current_rank 有效（已采样）且 >10 才退出；冷启动未采样时保持持有
        dataframe.loc[
            (dataframe['current_rank_valid'] == 1)
            & (dataframe['current_rank'] > self.EXIT_RANK),
            ['exit_long', 'exit_tag']
        ] = (1, 'netflow_oi_exit')
        return dataframe

    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                         current_rate: float, current_profit: float, **kwargs) -> Optional[float]:
        """ATR 硬止损：入场信号K线 low - 1xATR(20)，入场时定死的绝对地板价。
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
        return (floor_price / current_rate) - 1

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
