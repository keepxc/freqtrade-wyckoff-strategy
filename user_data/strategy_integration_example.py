#!/usr/bin/env python3
"""
Minimal example showing how to integrate EventEmitter into a Freqtrade strategy.

This is NOT a complete strategy - it's a template showing integration points.
"""
from datetime import datetime
from typing import Optional

import pandas as pd
from pandas import DataFrame
from freqtrade.strategy import IStrategy

from user_data.event_emitter import EventEmitter


class StrategyWithEvents(IStrategy):
    """Example strategy with event emission."""
    
    # Strategy parameters
    timeframe = '1h'
    
    def __init__(self, config: dict):
        super().__init__(config)
        
        # Initialize event emitter
        mode = "backtest"  # Detect from config if needed
        if config.get('runmode'):
            mode = str(config['runmode'])
        
        self.event_emitter = EventEmitter(
            strategy_name=self.__class__.__name__,
            events_dir="user_data/events",
            mode=mode
        )
        
        # Emit run start
        timerange = config.get('timerange', 'unknown')
        self.event_emitter.emit_run_start(
            run_type="baseline",
            strategy_version="v1.0.0",
            timerange=timerange,
            pair_scope="BTC/USDT:USDT",
            notes="Example run with event emission"
        )
        
        print(f"[EventEmitter] Initialized for run: {self.event_emitter.run_id}")
    
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Calculate indicators and emit regime snapshots."""
        
        # Your indicator calculations here
        dataframe['market_env'] = 'markup'  # Example
        dataframe['phase_context_tag'] = 'phase_d'  # Example
        
        # Emit regime snapshot for last bar (optional: do this periodically)
        if len(dataframe) > 0 and len(dataframe) % 24 == 0:  # Every 24 bars
            last = dataframe.iloc[-1]
            self.event_emitter.emit_regime_snapshot(
                pair=metadata['pair'],
                timeframe=self.timeframe,
                ts=pd.to_datetime(last['date']).to_pydatetime() if 'date' in last else None,
                market_env=last.get('market_env'),
                phase_context_tag=last.get('phase_context_tag'),
                daily_gate_score=last.get('daily_gate_score', 0.0),
            )
        
        # Emit detector events when key conditions change
        # Example: detect a specific pattern
        if len(dataframe) > 1:
            last = dataframe.iloc[-1]
            prev = dataframe.iloc[-2]
            
            # Example: detect when market_env changes
            if last.get('market_env') != prev.get('market_env'):
                self.event_emitter.emit_detector_event(
                    pair=metadata['pair'],
                    timeframe=self.timeframe,
                    event_family='market_structure',
                    event_name='market_env_change',
                    event_value=last.get('market_env', 'unknown'),
                    event_confidence=1.0,
                    metadata={
                        'from': prev.get('market_env', 'unknown'),
                        'to': last.get('market_env', 'unknown')
                    }
                )
        
        return dataframe
    
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Generate entry signals and emit signal events."""
        
        # Example signal logic
        conditions = []
        
        # Your signal conditions here
        signal_condition = (
            (dataframe['close'] > dataframe['close'].shift(1))
            # Add more conditions
        )
        conditions.append(signal_condition)
        
        if conditions:
            dataframe.loc[
                pd.concat(conditions, axis=1).all(axis=1),
                'enter_long'
            ] = 1
            
            # Emit signal event for each triggered signal
            for idx in dataframe[dataframe['enter_long'] == 1].index:
                row = dataframe.loc[idx]
                
                # Determine if this signal will actually execute
                # (simplified - in reality check max_open_trades, etc.)
                was_executed = True  # Placeholder
                
                self.event_emitter.emit_signal(
                    pair=metadata['pair'],
                    timeframe=self.timeframe,
                    side='long',
                    signal_name='example_signal',
                    candidate_enter_tag='example_entry',
                    was_executed=was_executed,
                    signal_quality_before=1.0,
                    signal_quality_after=1.1,
                    ts=pd.to_datetime(row['date']).to_pydatetime() if 'date' in row else None,
                    market_env=row.get('market_env'),
                    phase_context_tag=row.get('phase_context_tag'),
                )
        
        return dataframe
    
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Generate exit signals."""
        # Your exit logic here
        return dataframe
    
    def confirm_trade_entry(
        self,
        pair: str,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        current_time: datetime,
        entry_tag: Optional[str],
        side: str,
        **kwargs
    ) -> bool:
        """Confirm trade entry and emit trade_open event."""
        
        # Your confirmation logic
        confirmed = True  # Placeholder
        
        if confirmed:
            # Generate trade_id (in real strategy, use trade.id from kwargs if available)
            trade_id = f"trade_{int(current_time.timestamp())}"
            
            self.event_emitter.emit_trade_open(
                trade_id=trade_id,
                pair=pair,
                side=side,
                enter_tag=entry_tag or 'unknown',
                entry_price=rate,
                stake_amount=amount * rate,
                leverage=1.0,  # Adjust based on your strategy
                ts=current_time,
                signal_quality_enter=1.1,
                market_env_enter='markup',  # Get from dataframe if available
                phase_context_tag_enter='phase_d',
            )
        
        return confirmed
    
    def custom_exit(
        self,
        pair: str,
        trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs
    ) -> Optional[str]:
        """Custom exit logic and emit trade_close event."""
        
        # Your exit logic
        should_exit = False  # Placeholder
        exit_reason = None
        
        if should_exit:
            exit_reason = 'custom_exit_example'
            
            # Calculate trade metrics
            duration_minutes = int((current_time - trade.open_date_utc).total_seconds() / 60)
            
            self.event_emitter.emit_trade_close(
                trade_id=str(trade.id),
                pair=pair,
                side='long',  # or 'short'
                exit_reason=exit_reason,
                exit_tag=exit_reason,
                exit_price=current_rate,
                profit_pct=current_profit,
                profit_abs=trade.calc_profit(current_rate),
                duration_minutes=duration_minutes,
                ts=current_time,
            )
        
        return exit_reason
    
    def __del__(self):
        """Cleanup: emit run_end and close event emitter."""
        try:
            self.event_emitter.emit_run_end(status="completed")
            self.event_emitter.close()
        except Exception as e:
            print(f"[EventEmitter] Cleanup error: {e}")


# Note: In a real integration with WyckoffV7_TrueMethod:
# 1. Add event_emitter initialization in __init__
# 2. Emit signals in populate_entry_trend when conditions are met
# 3. Emit trade_open in confirm_trade_entry
# 4. Emit trade_close in custom_exit or custom_stoploss
# 5. Emit detector events when key detectors trigger
# 6. Emit regime snapshots periodically in populate_indicators
