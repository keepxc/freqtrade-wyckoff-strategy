#!/usr/bin/env python3
"""
Strategy Event Bridge - P5 事件系统与 Freqtrade 策略的桥接层

设计目标：
1. 最小侵入：策略只需在关键位置调用简单的桥接方法
2. 容错性：事件发射失败不影响策略执行
3. 可扩展：未来可以轻松添加新的事件类型
4. 可维护：事件逻辑与策略逻辑分离

使用方式：
    from user_data.strategy_event_bridge import StrategyEventBridge
    
    class YourStrategy(IStrategy):
        def __init__(self, config: dict):
            super().__init__(config)
            self.event_bridge = StrategyEventBridge(self, config)
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Optional

import pandas as pd
from pandas import DataFrame

if TYPE_CHECKING:
    from freqtrade.strategy import IStrategy

logger = logging.getLogger(__name__)


class StrategyEventBridge:
    """策略事件桥接器 - 连接策略与 P5 事件系统"""
    
    # V1B-1: 需要追踪状态变化的 detector 字段（标量值，逐 bar 比较）
    _DETECTOR_STATE_FIELDS = [
        "daily_choch_confirmed",
        "dist_cause_sufficient",
        "bear_market_mode",
        "phase_context_tag",
        "p3_swing_up_er_weak",
        "p3_swing_down_er_exhaustion",
    ]

    # V1B-1: detector_family 映射
    _DETECTOR_FAMILY = {
        "daily_choch_confirmed": "daily_detector",
        "dist_cause_sufficient": "daily_detector",
        "bear_market_mode": "regime",
        "phase_context_tag": "phase",
        "p3_swing_up_er_weak": "wave_er",
        "p3_swing_down_er_exhaustion": "wave_er",
    }
    
    def __init__(self, strategy: 'IStrategy', config: dict):
        """
        初始化事件桥接器
        
        Args:
            strategy: Freqtrade 策略实例
            config: Freqtrade 配置字典
        """
        self.strategy = strategy
        self.config = config
        self.event_emitter = None
        
        # V1B-1: 每个 (pair, field) 的上一次状态，用于检票状态变化
        self._detector_prev_state: dict = {}
        
        # 尝试初始化事件发射器
        self._init_event_emitter()
    
    def _init_event_emitter(self) -> None:
        """初始化事件发射器"""
        try:
            from user_data.event_emitter import EventEmitter, VALID_RUN_TYPES
            
            # 检测运行模式并映射到合规 run_type（V1B-9）
            mode = "backtest"
            run_type = "baseline"
            if self.config.get('runmode'):
                runmode = str(self.config['runmode'])  
                if 'dry' in runmode.lower():
                    mode = "dry_run"
                    run_type = "dry_run"
                elif 'live' in runmode.lower():
                    mode = "live"
                    run_type = "live"
            
            self.event_emitter = EventEmitter(
                strategy_name=self.strategy.__class__.__name__,
                events_dir="user_data/events",
                mode=mode
            )
            
            # 发送 run_start 事件
            timerange = self.config.get('timerange', 'unknown')
            pair_scope = 'unknown'
            if self.config.get('exchange', {}).get('pair_whitelist'):
                whitelist = self.config['exchange']['pair_whitelist']
                pair_scope = str(whitelist[0]) if whitelist else 'unknown'
            
            self.event_emitter.emit_run_start(
                run_type=run_type,
                strategy_version=str(getattr(self.strategy, 'version', 'v1.0.0')),
                git_commit=getattr(self.strategy, '_p5_git_commit', None),
                timerange=str(timerange),
                pair_scope=str(pair_scope),
                notes=f"{self.strategy.__class__.__name__} run in {mode} mode"
            )
            
            logger.info(f'✅ StrategyEventBridge initialized (run_id: {self.event_emitter.run_id})')
        except Exception as e:
            self.event_emitter = None
            logger.warning(f'⚠️ StrategyEventBridge: EventEmitter not available: {e}')
    
    def emit_signals_from_dataframe(self, dataframe: DataFrame, metadata: dict) -> None:
        """
        从 dataframe 中提取并发射信号事件
        
        在 populate_entry_trend 方法的最后调用此方法
        
        Args:
            dataframe: 包含信号的 dataframe
            metadata: 包含 pair 等信息的字典
        """
        if self.event_emitter is None:
            return
        
        try:
            pair = metadata['pair']
            timeframe = self.strategy.timeframe
            
            # 提取所有入场信号
            entry_signals = dataframe[
                (dataframe.get('enter_long', 0) == 1) | 
                (dataframe.get('enter_short', 0) == 1)
            ]
            
            if len(entry_signals) == 0:
                return
            
            for idx in entry_signals.index:
                row = entry_signals.loc[idx]

                # 确定方向
                side = 'long' if row.get('enter_long', 0) == 1 else 'short'
                enter_tag = row.get('enter_tag', 'unknown')

                # 提取信号质量
                signal_quality_before = 1.0
                signal_quality_after = row.get('signal_quality', 1.0)

                # 提取时间戳
                ts = None
                if 'date' in row:
                    ts = pd.to_datetime(row['date']).to_pydatetime()

                # wsdown 路径：捕获波段质量字段
                extra_kwargs = {}
                if enter_tag and 'wsdown' in enter_tag:
                    wb_eff = row.get('wave_efficiency', None)
                    wb_disp = row.get('wave_displacement', None)
                    if wb_eff is not None and not pd.isna(wb_eff):
                        extra_kwargs['wb_wave_efficiency'] = float(wb_eff)
                    if wb_disp is not None and not pd.isna(wb_disp):
                        extra_kwargs['wb_wave_displacement'] = float(wb_disp)

                # 发射信号事件
                # was_executed=False：此处为候选信号（populate_entry_trend 输出）
                # 真正成交与否需通过 confirm_trade_entry reject 事件 + trades 表 JOIN 判断
                self.event_emitter.emit_signal(
                    pair=pair,
                    timeframe=timeframe,
                    side=side,
                    signal_name=enter_tag,
                    candidate_enter_tag=enter_tag,
                    was_executed=False,
                    signal_quality_before=signal_quality_before,
                    signal_quality_after=signal_quality_after,
                    ts=ts,
                    market_env=row.get('market_env'),
                    htf_market_env=row.get('htf_market_env'),
                    macro_phase=row.get('macro_phase'),
                    bear_market_mode=bool(row.get('bear_market_mode', False)),
                    phase_context_tag=row.get('phase_context_tag'),
                    daily_gate_score=row.get('daily_gate_score'),
                    daily_tr_exists=bool(row.get('daily_tr_exists', False)),
                    composite_intent=row.get('composite_intent'),
                    p3_swing_down_er_exhaustion=bool(row.get('p3_swing_down_er_exhaustion', False)),
                    p3_swing_up_er_weak=bool(row.get('p3_swing_up_er_weak', False)),
                    wyckoff_warning=bool(row.get('wyckoff_warning', False)),
                    above_t1=bool(row.get('v8_above_t1', False)),
                    t2_exists=bool(row.get('v8_t2_exists', False)),
                    **extra_kwargs,
                )
            
            logger.debug(f'Emitted {len(entry_signals)} signal events for {pair}')
        except Exception as e:
            logger.warning(f'Failed to emit signal events: {e}')
    
    def emit_trade_open(
        self,
        pair: str,
        trade_id: str,
        side: str,
        enter_tag: str,
        entry_price: float,
        stake_amount: float,
        leverage: float,
        current_time: datetime,
        dataframe: Optional[DataFrame] = None,
    ) -> None:
        """
        发射交易开仓事件
        
        在 confirm_trade_entry 中调用此方法
        
        Args:
            pair: 交易对
            trade_id: 交易ID
            side: 方向 (long/short)
            enter_tag: 入场标签
            entry_price: 入场价格
            stake_amount: 仓位金额
            leverage: 杠杆
            current_time: 当前时间
            dataframe: 可选的 dataframe，用于提取环境快照
        """
        if self.event_emitter is None:
            return
        
        try:
            # 从 dataframe 提取入场时的环境快照
            signal_quality_enter = None
            market_env_enter = None
            htf_market_env_enter = None
            macro_phase_enter = None
            bear_market_mode_enter = False
            phase_context_tag_enter = None
            composite_intent_enter = None
            daily_gate_score_enter = None
            daily_tr_exists_enter = False
            
            if dataframe is not None and len(dataframe) > 0:
                last_row = dataframe.iloc[-1]
                signal_quality_enter = last_row.get('signal_quality')
                market_env_enter = last_row.get('market_env')
                htf_market_env_enter = last_row.get('htf_market_env')
                macro_phase_enter = last_row.get('macro_phase')
                bear_market_mode_enter = bool(last_row.get('bear_market_mode', False))
                phase_context_tag_enter = last_row.get('phase_context_tag')
                composite_intent_enter = last_row.get('composite_intent')
                daily_gate_score_enter = last_row.get('daily_gate_score')
                daily_tr_exists_enter = bool(last_row.get('daily_tr_exists', False))
            
            self.event_emitter.emit_trade_open(
                trade_id=trade_id,
                pair=pair,
                side=side,
                enter_tag=enter_tag,
                entry_price=entry_price,
                stake_amount=stake_amount,
                leverage=leverage,
                ts=current_time,
                signal_quality_enter=signal_quality_enter,
                market_env_enter=market_env_enter,
                htf_market_env_enter=htf_market_env_enter,
                macro_phase_enter=macro_phase_enter,
                bear_market_mode_enter=bear_market_mode_enter,
                phase_context_tag_enter=phase_context_tag_enter,
                composite_intent_enter=composite_intent_enter,
                daily_gate_score_enter=daily_gate_score_enter,
                daily_tr_exists_enter=daily_tr_exists_enter,
            )
            
            logger.debug(f'Emitted trade_open event: {trade_id} ({pair})')
        except Exception as e:
            logger.warning(f'Failed to emit trade_open event: {e}')
    
    def emit_trade_close(
        self,
        pair: str,
        trade_id: str,
        side: str,
        exit_reason: str,
        exit_tag: Optional[str],
        exit_price: float,
        profit_pct: float,
        profit_abs: float,
        duration_minutes: int,
        current_time: datetime,
        mfe_pct: Optional[float] = None,
        mae_pct: Optional[float] = None,
        **extra,
    ) -> None:
        """
        发射交易平仓事件

        在 custom_exit 或 custom_stoploss 中调用此方法

        Args:
            pair: 交易对
            trade_id: 交易ID
            side: 方向
            exit_reason: 退出原因
            exit_tag: 退出标签
            exit_price: 退出价格
            profit_pct: 收益百分比
            profit_abs: 绝对收益
            duration_minutes: 持仓时长（分钟）
            current_time: 当前时间
            mfe_pct: 最大有利偏移
            mae_pct: 最大不利偏移
            **extra: 透传字段（signal_bar_close, t1_price, t1_triggered 等）
        """
        if self.event_emitter is None:
            return

        try:
            self.event_emitter.emit_trade_close(
                trade_id=trade_id,
                pair=pair,
                side=side,
                exit_reason=exit_reason,
                exit_tag=exit_tag,
                exit_price=exit_price,
                profit_pct=profit_pct,
                profit_abs=profit_abs,
                duration_minutes=duration_minutes,
                ts=current_time,
                mfe_pct=mfe_pct,
                mae_pct=mae_pct,
                **extra,
            )

            logger.debug(f'Emitted trade_close event: {trade_id} ({pair})')
        except Exception as e:
            logger.warning(f'Failed to emit trade_close event: {e}')
    
    def emit_detector_event(
        self,
        pair: str,
        timeframe: str,
        event_family: str,
        event_name: str,
        event_value: any,
        current_time: Optional[datetime] = None,
        event_confidence: Optional[float] = None,
        metadata: Optional[dict] = None,
    ) -> None:
        """
        发射检测器事件
        
        在关键检测器触发时调用此方法
        
        Args:
            pair: 交易对
            timeframe: 时间周期
            event_family: 事件家族 (如 'wave_er', 'daily_detector')
            event_name: 事件名称
            event_value: 事件值
            current_time: 当前时间
            event_confidence: 事件置信度
            metadata: 额外元数据
        """
        if self.event_emitter is None:
            return
        
        try:
            self.event_emitter.emit_detector_event(
                pair=pair,
                timeframe=timeframe,
                event_family=event_family,
                event_name=event_name,
                event_value=event_value,
                ts=current_time,
                event_confidence=event_confidence,
                metadata=metadata,
            )
            
            logger.debug(f'Emitted detector event: {event_family}.{event_name} ({pair})')
        except Exception as e:
            logger.warning(f'Failed to emit detector event: {e}')
    
    def emit_regime_snapshot(
        self,
        pair: str,
        timeframe: str,
        dataframe: DataFrame,
        current_time: Optional[datetime] = None,
    ) -> None:
        """
        V1B-2: 发射主执行周期 bar 级 regime 快照

        遍历 dataframe 全部行，每行发射一条 regime_snapshot。
        覆盖字段：market_env / htf_market_env / macro_phase / bear_market_mode /
                  daily_tr_exists / daily_gate_score / daily_choch_confirmed /
                  dist_cause_sufficient / phase_context_tag
        """
        if self.event_emitter is None or len(dataframe) == 0:
            return

        try:
            emitted = 0
            for _, row in dataframe.iterrows():
                ts = current_time or (
                    pd.to_datetime(row['date']).to_pydatetime() if 'date' in row.index else None
                )
                self.event_emitter.emit_regime_snapshot(
                    pair=pair,
                    timeframe=timeframe,
                    ts=ts,
                    market_env=row.get('market_env'),
                    htf_market_env=row.get('htf_market_env'),
                    macro_phase=row.get('macro_phase'),
                    bear_market_mode=bool(row.get('bear_market_mode', False)),
                    phase_context_tag=row.get('phase_context_tag'),
                    daily_choch_confirmed=bool(row.get('daily_choch_confirmed', False)),
                    dist_cause_sufficient=bool(row.get('dist_cause_sufficient', False)),
                    daily_tr_exists=bool(row.get('daily_tr_exists', False)),
                    daily_gate_score=row.get('daily_gate_score'),
                    daily_sm_v2=bool(row.get('daily_sm_v2', False)),
                )
                emitted += 1
            logger.debug(f'[V1B-2] Emitted {emitted} regime snapshots for {pair}')
        except Exception as e:
            logger.warning(f'Failed to emit regime snapshot: {e}')

    def emit_detector_events_from_dataframe(
        self,
        pair: str,
        timeframe: str,
        dataframe: DataFrame,
        current_time: Optional[datetime] = None,
    ) -> None:
        """
        V1B-1: 从 dataframe 最新 bar 中提取 detector 状态，仅在状态变化时发射事件。

        覆盖字段（只要值发生变化即发射）：
          - daily_choch_confirmed
          - dist_cause_sufficient
          - bear_market_mode
          - phase_context_tag
          - p3_swing_up_er_weak
          - p3_swing_down_er_exhaustion

        Args:
            pair: 交易对
            timeframe: 时间周期
            dataframe: 含 detector 列的 dataframe
            current_time: 当前时间（可选，默认从 dataframe 的 date 列读取）
        """
        if self.event_emitter is None or len(dataframe) == 0:
            return

        # 仅保留 dataframe 中实际存在的 detector 字段
        active_fields = [f for f in self._DETECTOR_STATE_FIELDS if f in dataframe.columns]
        if not active_fields:
            return

        try:
            emitted = 0
            for _, row in dataframe.iterrows():
                ts = current_time or (
                    pd.to_datetime(row['date']).to_pydatetime() if 'date' in row.index else None
                )
                for field in active_fields:
                    current_val = row.get(field)
                    if current_val is None:
                        continue

                    state_key = f"{pair}_{field}"
                    prev_val = self._detector_prev_state.get(state_key, object())  # sentinel

                    # 仅在值发生变化时发射
                    if current_val != prev_val:
                        self._detector_prev_state[state_key] = current_val
                        family = self._DETECTOR_FAMILY.get(field, "detector")
                        self.event_emitter.emit_detector_event(
                            pair=pair,
                            timeframe=timeframe,
                            event_family=family,
                            event_name=field,
                            event_value=current_val,
                            ts=ts,
                        )
                        emitted += 1
                        logger.debug(f'[V1B-1] detector state change: {pair} {field}: {prev_val!r} -> {current_val!r}')

            logger.debug(f'[V1B-1] Emitted {emitted} detector state change events for {pair}')
        except Exception as e:
            logger.warning(f'Failed to emit detector events from dataframe: {e}')

    def emit_exit_decision(
        self,
        trade_id: str,
        pair: str,
        exit_logic_module: str,
        candidate_reason: str,
        triggered: bool,
        current_time: datetime,
        phase_context_tag: Optional[str] = None,
        blocked_reason: Optional[str] = None,
        confidence: Optional[float] = None,
        metadata: Optional[dict] = None,
    ) -> None:
        """
        V1B-3: 发射退出决策事件

        在 custom_exit / custom_stoploss 中，每次评估退出逻辑时调用。
        不论 triggered 是否为 True 均发射，以便分析 fallback 情况。

        Args:
            trade_id: 交易 ID
            pair: 交易对
            exit_logic_module: 退出逻辑模块名（如 'phase_aware_exit', 'p3_swing_up_er_weak'）
            candidate_reason: 候选退出原因
            triggered: 是否真正触发退出
            current_time: 当前时间
            phase_context_tag: 当前 phase 标签
            blocked_reason: 被阻断的原因（未触发时填写）
            confidence: 本次退出决策的置信度
            metadata: 额外元数据
        """
        if self.event_emitter is None:
            return

        try:
            self.event_emitter.emit_exit_decision_event(
                trade_id=trade_id,
                pair=pair,
                exit_logic_module=exit_logic_module,
                candidate_reason=candidate_reason,
                triggered=triggered,
                ts=current_time,
                phase_context_tag=phase_context_tag,
                blocked_reason=blocked_reason,
                confidence=confidence,
                metadata=metadata,
            )
            logger.debug(f'Emitted exit_decision: {pair} {exit_logic_module} triggered={triggered}')
        except Exception as e:
            logger.warning(f'Failed to emit exit_decision event: {e}')
    
    def close(self) -> None:
        """关闭事件发射器"""
        if self.event_emitter is not None:
            try:
                self.event_emitter.emit_run_end(status="completed")
                self.event_emitter.close()
                logger.info('✅ StrategyEventBridge closed successfully')
            except Exception as e:
                logger.warning(f'⚠️ StrategyEventBridge cleanup error: {e}')
    
    def __del__(self):
        """析构函数：确保资源被释放"""
        self.close()
