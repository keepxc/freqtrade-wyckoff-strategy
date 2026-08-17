"""
AI500V1DryRun — VergeX AI500 评分驱动的探索性 dry-run 策略
=============================================================
探索性——非结论，禁止引用为证据。本策略无法回测（AI500 score 依赖
后端实时计算的 compositeZ，历史输入数据不可获取，详见对话记录 2026-08-17
的 AI500 算法逆向分析）。本文件只用于 dry-run 观察，运行结果不构成
CLAUDE.md 三层终止校验意义上的"结论"，不得据此判定策略✅/❌，也不
写入任何策略基线文档。

用户确认口径（2026-08-17）：
  1. 定位 = 先跑，记录数据，表现不好再优化；不是一次性通过/淘汰的验证
  2. dry_run 强制，不上实盘
  3. 只用免费端点 vergex.trade/trending-category?key=ai500（不接入付费
     Claw402 API），接口经实测无 limit/top_n 参数，返回的即为当前达标
     候选（score>=80 附近），数量随行情浮动
  4. 仓位管理沿用现有 Donchian 系策略的波动率目标仓位算法（见
     DonchianV1AtrMaExit.py），不采用原 NoFx config 里的
     btc_eth_max_leverage/altcoin_max_position_value_ratio 等参数
  5. score 顶替原 config 的 min_confidence 字段用途（选币兼信心过滤）
  6. 不设固定止盈：score < 60（原 config"强制平仓"阈值）触发退出，
     让强势币继续持有到信号本身转弱为止；止损用 ATR 硬止损防极端下跌
  7. 无论是否触发交易，每次抓取都写入历史记录（
     user_data/ai500_score_history.csv），为未来可能的历史回测积累数据

架构限制（技术验证，非用户判据决定）：freqtrade 该版本的
PairListResolver.user_subdir 为 None，不支持从 user_data/pairlists 加载
自定义 pairlist 插件，因此无法做"AI500 动态生成白名单"。改为用内置
VolumePairList 提供候选池（成交量活跃的 USDT-M 合约），策略内部按
AI500 score 门槛过滤实际入场，白名单里其余 pair 不会被交易。
"""
import csv
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd
import requests
from pandas import DataFrame

from freqtrade.strategy import IStrategy
from freqtrade.persistence import Trade

logger = logging.getLogger(__name__)

AI500_URL = "https://vergex.trade/trending-category"
AI500_PARAMS = {"lang": "zh", "key": "ai500"}
AI500_PROXY = {
    "http": "http://host.docker.internal:7890",
    "https": "http://host.docker.internal:7890",
}
AI500_FETCH_TIMEOUT = 10

SCORE_HISTORY_PATH = "/freqtrade/user_data/ai500_score_history.csv"
SCORE_HISTORY_FIELDS = [
    "fetched_at_utc", "symbol", "pair", "score", "signal_peak",
    "price", "start_price", "start_time", "change_pct_value",
]


class AI500V1DryRun(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = '1h'
    can_short = False
    process_only_new_candles = True
    startup_candle_count = 50

    # 框架层 stoploss 占位；真正止损通过 custom_stoploss（ATR）实现
    stoploss = -0.99
    minimal_roi = {"0": 100}
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = True
    trailing_stop = False
    use_custom_stoploss = True

    max_open_trades = 3

    ENTRY_SCORE = 80.0
    EXIT_SCORE = 60.0

    ATR_PERIOD = 20
    ATR_MULTIPLIER = 1.0

    VOL_LOOKBACK = 20
    TARGET_VOL = 0.20
    MAX_LEV = 1.0

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.ai500_scores: dict[str, float] = {}

    def bot_loop_start(self, current_time: datetime, **kwargs) -> None:
        try:
            resp = requests.get(
                AI500_URL, params=AI500_PARAMS, proxies=AI500_PROXY,
                headers={"User-Agent": "Mozilla/5.0"}, timeout=AI500_FETCH_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"[AI500] fetch failed, keeping previous scores: {e}")
            return

        assets = (data.get("category") or {}).get("assets") or []
        fetched_at = current_time.astimezone(timezone.utc).isoformat()
        rows = []
        new_scores: dict[str, float] = {}
        for a in assets:
            symbol = a.get("symbol")
            score = a.get("score")
            if not symbol or score is None:
                continue
            pair = f"{symbol}/USDT:USDT"
            new_scores[pair] = float(score)
            rows.append({
                "fetched_at_utc": fetched_at,
                "symbol": symbol,
                "pair": pair,
                "score": score,
                "signal_peak": a.get("signal"),
                "price": a.get("price"),
                "start_price": a.get("startPrice"),
                "start_time": a.get("startTime"),
                "change_pct_value": a.get("changePctValue"),
            })

        self.ai500_scores = new_scores
        self._append_score_history(rows)

    @staticmethod
    def _append_score_history(rows: list[dict]) -> None:
        if not rows:
            return
        write_header = not os.path.exists(SCORE_HISTORY_PATH)
        try:
            with open(SCORE_HISTORY_PATH, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=SCORE_HISTORY_FIELDS)
                if write_header:
                    writer.writeheader()
                writer.writerows(rows)
        except OSError:
            pass

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        pair = metadata['pair']
        dataframe['atr'] = self._compute_atr(dataframe, self.ATR_PERIOD)
        ret = dataframe['close'].pct_change()
        dataframe['realized_vol'] = ret.rolling(self.VOL_LOOKBACK).std().shift(1) * np.sqrt(365 * 24)
        # AI500 score 是当前时点的实时值，非历史序列——只对最新一根 bar 有意义，
        # 不可用于回测（详见文件头说明）。
        dataframe['ai500_score'] = self.ai500_scores.get(pair, np.nan)
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
            dataframe['ai500_score'] >= self.ENTRY_SCORE,
            ['enter_long', 'enter_tag']
        ] = (1, 'ai500_score_ge_80')
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            dataframe['ai500_score'] < self.EXIT_SCORE,
            ['exit_long', 'exit_tag']
        ] = (1, 'ai500_score_lt_60')
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
