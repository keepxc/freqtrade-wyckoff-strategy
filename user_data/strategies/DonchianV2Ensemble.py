"""
DonchianV2Ensemble — Donchian 三窗口集成投票，纯多头，波动率目标仓位
=====================================================================
TASK 100 存活链的 Freqtrade 实现（组合级三层校验，待 OOS 复核后方可采纳）：
- 信号层：三组 Donchian 变体 (15,8)/(20,10)/(30,15) 独立因果状态机，
  ≥2/3 一致时进多头（TASK 100 审计脚本 audit_task100_donchian_ensemble.py：
  BTC-spot Calmar 0.67→0.72，futures 0.64→0.64持平，MaxDD 不劣化，半段一致，
  换手零增量）
- 仓位层：与 DonchianV1 完全一致，入场时波动率目标 lb=20d/target_vol=20%/
  封顶 1.0x（只降不加杠杆），持仓期间不再平衡（TASK 75b 选型，未改动）
- 与 DonchianV1 唯一差异：入场/出场判据从单窗口 20/10 换成三窗口多数表决

风险控制说明：本策略不设价格止损（stoploss=-0.99 仅为框架占位），
风险控制 = 通道退出（多数表决状态转 0）+ 波动率目标仓位。
"""
import numpy as np
import pandas as pd
from pandas import DataFrame
from datetime import datetime
from typing import Optional

from freqtrade.strategy import IStrategy


class DonchianV2Ensemble(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = '1d'
    can_short = False
    process_only_new_candles = True
    startup_candle_count = 50

    stoploss = -0.99
    minimal_roi = {"0": 100}
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = True
    trailing_stop = False

    # 三窗口变体（冻结，TASK 100 预注册，禁止 hyperopt）
    VARIANTS = [(15, 8), (20, 10), (30, 15)]
    VOTE_MIN = 2
    # 仓位参数（与 DonchianV1/TASK 75b 完全一致，未改动）
    VOL_LOOKBACK = 20
    TARGET_VOL = 0.20
    MAX_LEV = 1.0

    def _variant_state(self, dataframe: DataFrame, n_entry: int, n_exit: int) -> np.ndarray:
        """单变体因果状态机：与 audit_task100_donchian_ensemble.py 的
        donchian_positions 逐行同口径（rolling 全 shift(1)，无未来 bar 访问）。"""
        hi = dataframe['high'].rolling(n_entry).max().shift(1).values
        lo_exit = dataframe['low'].rolling(n_exit).min().shift(1).values
        close = dataframe['close'].values
        n = len(dataframe)
        pos = np.zeros(n)
        state = 0
        for i in range(n):
            if state == 1 and close[i] < lo_exit[i]:
                state = 0
            if state == 0 and not np.isnan(hi[i]) and close[i] > hi[i]:
                state = 1
            pos[i] = state
        return pos

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        votes = np.zeros(len(dataframe))
        for ne, nx in self.VARIANTS:
            votes += self._variant_state(dataframe, ne, nx)
        ensemble_state = (votes >= self.VOTE_MIN).astype(float)
        dataframe['ens_state'] = ensemble_state
        # 状态转移：0→1 触发入场，1→0 触发出场（状态本身已因果，转移判断不引入新前视）
        dataframe['ens_enter'] = (ensemble_state == 1) & (dataframe['ens_state'].shift(1).fillna(0) == 0)
        dataframe['ens_exit'] = (ensemble_state == 0) & (dataframe['ens_state'].shift(1).fillna(0) == 1)

        ret = dataframe['close'].pct_change()
        dataframe['don_realized_vol'] = ret.rolling(self.VOL_LOOKBACK).std().shift(1) * np.sqrt(365)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        entry_mask = dataframe['ens_enter']
        dataframe.loc[entry_mask, 'enter_long'] = 1
        dataframe.loc[entry_mask, 'enter_tag'] = 'ens_break'
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        exit_mask = dataframe['ens_exit']
        dataframe.loc[exit_mask, 'exit_long'] = 1
        dataframe.loc[exit_mask, 'exit_tag'] = 'ens_exit'
        return dataframe

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: Optional[float], max_stake: float,
                            leverage: float, entry_tag: Optional[str], side: str,
                            **kwargs) -> float:
        """入场时按波动率目标缩放名义仓位：lev = min(1, target_vol / realized_vol)。
        与 DonchianV1 完全一致，未改动。"""
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        hist = df.loc[df['date'] < current_time]
        if hist.empty:
            return proposed_stake
        rv = hist['don_realized_vol'].iloc[-1]
        if pd.isna(rv) or rv <= 0:
            lev = 1.0
        else:
            lev = min(self.MAX_LEV, self.TARGET_VOL / rv)
        stake = proposed_stake * lev
        if min_stake is not None and stake < min_stake:
            return min_stake
        return stake
