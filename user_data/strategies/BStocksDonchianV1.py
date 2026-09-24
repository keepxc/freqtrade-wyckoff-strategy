"""
BStocksDonchianV1 — Donchian 20/10 突破策略移植到 bStocks 候选正股资产类别
=============================================================
信号层与 DonchianV1 完全一致（Donchian 20/10 纯多头，通道退出，零调参，
TASK108 已在16支正股上完成前瞻收益预注册检验，✅ 采纳候选）。

仓位层沿用 DonchianV1 的入场波动率目标算法（TASK75b 封板的算法本身，
未改动公式），但 TARGET_VOL 针对股票资产重新校准：
16支标的近5年 realized_vol（rolling20 std * sqrt(365)）分布中位数 0.4539、
均值 0.5399，显著高于加密货币基线的 0.20（脚本 scripts/prepare_bstocks_data.py）。
TARGET_VOL 改为 0.45（约等于分布中位数），使常态波动下仓位接近满仓，
波动飙升（75/90分位 0.68/0.94）时按比例降仓，保留原算法"只降不加杠杆"
的风险节流意图，而非让仓位常年被腰斩。MAX_LEV 保持 1.0（不加杠杆，
与 DonchianV1 一致——bStocks 是否支持杠杆交易未经尽调，默认不使用）。

风险控制说明：不设价格止损（stoploss=-0.99 仅为框架占位），风险控制 =
通道退出（10日低点）+ 波动率目标仓位，与 DonchianV1 一致。

资产/执行范围：仅覆盖 TASK108 检验过的16支已知子集，非币安 bStocks 全部
46+支标的；回测费率占位为币安现货标准 taker 0.1%（bStocks 促销期0% maker
费率不作为长期假设，见 TASK108 后续记录）。回测口径为正股历史代理数据
（Nasdaq官方API），非 bStocks 自身实盘数据（产品仅2个月历史不足以回测）。
"""
import numpy as np
import pandas as pd
from pandas import DataFrame
from datetime import datetime
from typing import Optional

from freqtrade.strategy import IStrategy


class BStocksDonchianV1(IStrategy):
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

    # 信号参数 —— 与 DonchianV1 完全一致（零调参经典值，禁止 hyperopt）
    N_ENTRY = 20
    N_EXIT = 10
    # 仓位参数 —— 算法沿用 DonchianV1（TASK75b 封板），TARGET_VOL 按股票
    # realized_vol 分布重新校准（见模块 docstring）
    VOL_LOOKBACK = 20
    TARGET_VOL = 0.45
    MAX_LEV = 1.0

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['don_upper'] = dataframe['high'].rolling(self.N_ENTRY).max().shift(1)
        dataframe['don_exit'] = dataframe['low'].rolling(self.N_EXIT).min().shift(1)
        ret = dataframe['close'].pct_change()
        dataframe['don_realized_vol'] = ret.rolling(self.VOL_LOOKBACK).std().shift(1) * np.sqrt(365)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        entry_mask = dataframe['don_upper'].notna() & (dataframe['close'] > dataframe['don_upper'])
        dataframe.loc[entry_mask, 'enter_long'] = 1
        dataframe.loc[entry_mask, 'enter_tag'] = 'don_break'
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        exit_mask = dataframe['don_exit'].notna() & (dataframe['close'] < dataframe['don_exit'])
        dataframe.loc[exit_mask, 'exit_long'] = 1
        dataframe.loc[exit_mask, 'exit_tag'] = 'don_exit'
        return dataframe

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: Optional[float], max_stake: float,
                            leverage: float, entry_tag: Optional[str], side: str,
                            **kwargs) -> float:
        """入场时按波动率目标缩放名义仓位：lev = min(1, target_vol / realized_vol)。"""
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
