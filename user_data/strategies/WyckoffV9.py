"""
WyckoffV9 — Phase D 日线信号多头单引擎（MVP）
============================================================
设计文档：TASK_NEW/wyckoff_v9_design.md（TASK 70，验收判据预注册于实现之前）

设计公理（出处见设计文档）：
- 在 edge 所在的尺度上交易：信号 = 日线 Phase D（TASK 61/63/67 三重验证），
  运行 timeframe 仍为 1H（执行与止损管理粒度）
- 1H 波段确认层退役（TASK 60：因果化后零 edge）——入场不等 choch，
  Phase D 合并落地后的第一根 1H bar 即入场
- 无空头（TASK 29/57/69：量/funding/OI 日线方向信号三连灭）
- 不加未验证门控（TASK 45/64/66 教训）

入场：v8_phase == 'D'（日线 +1 天 merge，防泄露机制复用 V8）且空仓
退出优先级：
  1. 结构失效：TR Low 止损（custom_stoploss 复用 V8 多头分支：TR Low buffer
     + T1 保本 ratchet + -12% 硬止损兜底）
  2. 相位降级：v8_phase ∈ {A, B} → 'v9_phase_demote'（积累论题失效）
  3. 测量视野：持有 ≥ 480h（20 交易日，fwd20d 测量对象的直接转写，
     显式标注为时间上限而非结构判断）→ 'v9_horizon'
  D→E 升级不退出（新鲜 E 携带 D 级 edge，TASK 63；陈旧 E 由 20 日上限清算）

继承说明：populate_indicators 完整复用 V8（日线检测器链 + merge；1H 波段列
仍被计算但本策略不消费）；populate_entry_trend / populate_exit_trend /
custom_exit 全量覆写；confirm_trade_entry 继承（D 相 PNF 存在率 100%，
TASK 63，检查为无害冗余）。
"""

import pandas as pd
from pandas import DataFrame

from WyckoffV8 import WyckoffV8


class WyckoffV9(WyckoffV8):
    can_short = False

    # V9 无向量化退出信号（退出全部走 custom_exit / custom_stoploss）
    use_exit_signal = False

    HORIZON_HOURS = 480  # 20 交易日 = fwd20d 测量视野（时间上限，非结构判断）

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['enter_long'] = 0
        dataframe['enter_short'] = 0
        v8_phase = dataframe.get(
            'v8_phase', pd.Series('unknown', index=dataframe.index)).fillna('unknown')
        # v9.1：入场 = D 相变迁 bar（每情节一次），与被测量对象（TASK 61/63
        # "D 日 → fwd20d"）对齐。v9.0 的"空仓且在 D 即入场"在 2026-06 情节
        # 产生 34 笔止损重入循环（-2,923 USDT），入场定义与测量对象不匹配，
        # 属机械缺陷修正，验收判据未动
        entry_mask = (v8_phase == 'D') & (v8_phase.shift(1) != 'D')
        dataframe.loc[entry_mask, 'enter_long'] = 1
        dataframe.loc[entry_mask, 'enter_tag'] = 'v9_d'
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['exit_long'] = 0
        dataframe['exit_short'] = 0
        return dataframe

    def custom_exit(self, pair: str, trade, current_time, current_rate: float,
                    current_profit: float, **kwargs):
        if trade.is_short:
            return None
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe is None or len(dataframe) == 0:
            return None
        last = dataframe.iloc[-1]

        ph = last.get('v8_phase', 'unknown')
        if isinstance(ph, str) and ph in ('A', 'B'):
            return 'v9_phase_demote'

        open_date = trade.open_date_utc
        held_hours = (current_time - open_date).total_seconds() / 3600.0
        if held_hours >= self.HORIZON_HOURS:
            return 'v9_horizon'
        return None
