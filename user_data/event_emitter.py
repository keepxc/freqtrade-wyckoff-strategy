#!/usr/bin/env python3
"""JSONL Event Emitter for Freqtrade Strategy Integration.

Usage in strategy:
    from user_data.event_emitter import EventEmitter
    
    def __init__(self, config: dict):
        super().__init__(config)
        self.event_emitter = EventEmitter(
            strategy_name=self.__class__.__name__,
            events_dir="user_data/events"
        )
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# V1B-8: 事件 schema 版本，用于历史兼容和 schema 演化
EVENT_SCHEMA_VERSION = "1.1.0"

# V1B-9: 严格的 run_type 枚举
VALID_RUN_TYPES = frozenset({"baseline", "candidate", "dry_run", "live", "analysis"})


class EventEmitter:
    """Emit structured JSONL events for strategy intelligence data pipeline."""
    
    def __init__(
        self,
        strategy_name: str,
        events_dir: str = "user_data/events",
        run_id: Optional[str] = None,
        mode: str = "backtest",
    ):
        self.strategy_name = strategy_name
        self.events_dir = Path(events_dir)
        self.mode = mode
        self.run_id = run_id or self._generate_run_id()
        self.run_dir = self.events_dir / self.mode / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        
        # Event file handles
        self._files: Dict[str, Any] = {}
        self._event_counts: Dict[str, int] = {}
        # V1B-8: 在 emit_run_start 时设置，默认值
        self._strategy_version: str = "unknown"
        self._git_commit: Optional[str] = None
        
    def _generate_run_id(self) -> str:
        """Generate unique run ID."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        return f"run_{ts}_{self.mode}_{uuid.uuid4().hex[:8]}"
    
    def __getstate__(self):
        state = self.__dict__.copy()
        for fh in state.get('_files', {}).values():
            try:
                fh.close()
            except Exception:
                pass
        state['_files'] = {}
        return state

    def __setstate__(self, state):
        self.__dict__.update(state)

    def _get_file_handle(self, event_category: str):
        """Get or create file handle for event category."""
        if event_category not in self._files:
            filepath = self.run_dir / f"{event_category}.jsonl"
            self._files[event_category] = filepath.open("a", encoding="utf-8")
            self._event_counts[event_category] = 0
        return self._files[event_category]
    
    def _emit(self, event_category: str, event: Dict[str, Any]) -> None:
        """Write event to JSONL file."""
        try:
            fh = self._get_file_handle(event_category)
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
            fh.flush()
            self._event_counts[event_category] += 1
        except Exception as e:
            # Never crash strategy execution due to logging failure
            print(f"[EventEmitter] Failed to emit {event_category} event: {e}")
    
    def emit_run_start(
        self,
        run_type: str = "baseline",
        strategy_version: str = "v1",
        git_commit: Optional[str] = None,
        timerange: Optional[str] = None,
        pair_scope: Optional[str] = None,
        config_hash: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> None:
        """Emit run_event with subtype run_start."""
        # V1B-9: 校验 run_type 枚举
        if run_type not in VALID_RUN_TYPES:
            raise ValueError(f"Invalid run_type '{run_type}'. Must be one of: {sorted(VALID_RUN_TYPES)}")
        self._strategy_version = strategy_version
        self._git_commit = git_commit
        event = {
            "event_type": "run_event",
            "event_schema_version": EVENT_SCHEMA_VERSION,
            "event_subtype": "run_start",
            "run_id": self.run_id,
            "ts": datetime.now(timezone.utc).isoformat(),
            "run_type": run_type,
            "mode": self.mode,
            "strategy_name": self.strategy_name,
            "strategy_version": strategy_version,
            "git_commit": git_commit,
            "timerange": timerange,
            "pair_scope": pair_scope,
            "config_hash": config_hash,
            "status": "running",
            "notes": notes,
        }
        self._emit("run", event)
    
    def emit_run_end(self, status: str = "completed", notes: Optional[str] = None) -> None:
        """Emit run_event with subtype run_end."""
        event = {
            "event_type": "run_event",
            "event_subtype": "run_end",
            "run_id": self.run_id,
            "ts": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "notes": notes,
        }
        self._emit("run", event)
    
    def emit_signal(
        self,
        pair: str,
        timeframe: str,
        side: str,
        signal_name: str,
        candidate_enter_tag: str,
        was_executed: bool,
        signal_quality_before: float,
        signal_quality_after: float,
        ts: Optional[datetime] = None,
        rejection_reason: Optional[str] = None,
        rr_estimate: Optional[float] = None,
        market_env: Optional[str] = None,
        htf_market_env: Optional[str] = None,
        macro_phase: Optional[str] = None,
        bear_market_mode: bool = False,
        phase_context_tag: Optional[str] = None,
        daily_gate_score: Optional[float] = None,
        daily_tr_exists: bool = False,
        composite_intent: Optional[str] = None,
        **kwargs,
    ) -> None:
        """Emit signal_event."""
        signal_id = f"sig_{uuid.uuid4().hex[:12]}"
        event = {
            "event_type": "signal_event",
            "signal_id": signal_id,
            "run_id": self.run_id,
            "ts": (ts or datetime.now(timezone.utc)).isoformat(),
            "pair": pair,
            "timeframe": timeframe,
            "side": side,
            "signal_name": signal_name,
            "candidate_enter_tag": candidate_enter_tag,
            "was_executed": was_executed,
            "rejection_reason": rejection_reason,
            "signal_quality_before": signal_quality_before,
            "signal_quality_after": signal_quality_after,
            "rr_estimate": rr_estimate,
            "market_env": market_env,
            "htf_market_env": htf_market_env,
            "macro_phase": macro_phase,
            "bear_market_mode": bear_market_mode,
            "phase_context_tag": phase_context_tag,
            "daily_gate_score": daily_gate_score,
            "daily_tr_exists": daily_tr_exists,
            "composite_intent": composite_intent,
        }
        # Add any extra kwargs
        event.update(kwargs)
        self._emit("signals", event)
    
    def emit_trade_open(
        self,
        trade_id: str,
        pair: str,
        side: str,
        enter_tag: str,
        entry_price: float,
        stake_amount: float,
        leverage: float,
        ts: Optional[datetime] = None,
        signal_quality_enter: Optional[float] = None,
        market_env_enter: Optional[str] = None,
        htf_market_env_enter: Optional[str] = None,
        macro_phase_enter: Optional[str] = None,
        bear_market_mode_enter: bool = False,
        phase_context_tag_enter: Optional[str] = None,
        composite_intent_enter: Optional[str] = None,
        daily_gate_score_enter: Optional[float] = None,
        daily_tr_exists_enter: bool = False,
        **kwargs,
    ) -> None:
        """Emit trade_event with subtype trade_open."""
        event = {
            "event_type": "trade_event",
            "event_subtype": "trade_open",
            "trade_id": trade_id,
            "run_id": self.run_id,
            "ts": (ts or datetime.now(timezone.utc)).isoformat(),
            "pair": pair,
            "side": side,
            "enter_tag": enter_tag,
            "entry_price": entry_price,
            "stake_amount": stake_amount,
            "leverage": leverage,
            "signal_quality_enter": signal_quality_enter,
            "market_env_enter": market_env_enter,
            "htf_market_env_enter": htf_market_env_enter,
            "macro_phase_enter": macro_phase_enter,
            "bear_market_mode_enter": bear_market_mode_enter,
            "phase_context_tag_enter": phase_context_tag_enter,
            "composite_intent_enter": composite_intent_enter,
            "daily_gate_score_enter": daily_gate_score_enter,
            "daily_tr_exists_enter": daily_tr_exists_enter,
        }
        event.update(kwargs)
        self._emit("trades", event)
    
    def emit_trade_close(
        self,
        trade_id: str,
        pair: str,
        side: str,
        exit_reason: str,
        exit_tag: Optional[str],
        exit_price: float,
        profit_pct: float,
        profit_abs: float,
        duration_minutes: int,
        ts: Optional[datetime] = None,
        mfe_pct: Optional[float] = None,
        mae_pct: Optional[float] = None,
        **kwargs,
    ) -> None:
        """Emit trade_event with subtype trade_close."""
        event = {
            "event_type": "trade_event",
            "event_subtype": "trade_close",
            "trade_id": trade_id,
            "run_id": self.run_id,
            "ts": (ts or datetime.now(timezone.utc)).isoformat(),
            "pair": pair,
            "side": side,
            "exit_reason": exit_reason,
            "exit_tag": exit_tag,
            "exit_price": exit_price,
            "profit_pct": profit_pct,
            "profit_abs": profit_abs,
            "duration_minutes": duration_minutes,
            "mfe_pct": mfe_pct,
            "mae_pct": mae_pct,
        }
        event.update(kwargs)
        self._emit("trades", event)
    
    def emit_detector_event(
        self,
        pair: str,
        timeframe: str,
        event_family: str,
        event_name: str,
        event_value: Any,
        ts: Optional[datetime] = None,
        event_confidence: Optional[float] = None,
        metadata: Optional[Dict] = None,
    ) -> None:
        """Emit detector_event."""
        event_id = f"det_{uuid.uuid4().hex[:12]}"
        event = {
            "event_type": "detector_event",
            "event_schema_version": EVENT_SCHEMA_VERSION,
            "event_id": event_id,
            "run_id": self.run_id,
            "ts": (ts or datetime.now(timezone.utc)).isoformat(),
            "pair": pair,
            "timeframe": timeframe,
            "event_family": event_family,
            "event_name": event_name,
            "event_value": str(event_value),
            "event_confidence": event_confidence,
            "metadata": metadata,
        }
        self._emit("detectors", event)

    def emit_exit_decision_event(
        self,
        trade_id: str,
        pair: str,
        exit_logic_module: str,
        candidate_reason: str,
        triggered: bool,
        ts: Optional[datetime] = None,
        phase_context_tag: Optional[str] = None,
        blocked_reason: Optional[str] = None,
        confidence: Optional[float] = None,
        metadata: Optional[Dict] = None,
    ) -> None:
        """V1B-3: Emit exit_decision_event."""
        event_id = f"exit_{uuid.uuid4().hex[:12]}"
        event = {
            "event_type": "exit_decision_event",
            "event_schema_version": EVENT_SCHEMA_VERSION,
            "event_id": event_id,
            "run_id": self.run_id,
            "ts": (ts or datetime.now(timezone.utc)).isoformat(),
            "trade_id": trade_id,
            "pair": pair,
            "phase_context_tag": phase_context_tag,
            "exit_logic_module": exit_logic_module,
            "candidate_reason": candidate_reason,
            "triggered": triggered,
            "blocked_reason": blocked_reason,
            "confidence": confidence,
            "metadata": metadata,
        }
        self._emit("exit_decisions", event)
    
    def emit_regime_snapshot(
        self,
        pair: str,
        timeframe: str,
        ts: Optional[datetime] = None,
        market_env: Optional[str] = None,
        htf_market_env: Optional[str] = None,
        macro_phase: Optional[str] = None,
        bear_market_mode: bool = False,
        phase_context_tag: Optional[str] = None,
        daily_choch_confirmed: bool = False,
        dist_cause_sufficient: bool = False,
        daily_tr_exists: bool = False,
        daily_gate_score: Optional[float] = None,
        daily_sm_v2: bool = False,
    ) -> None:
        """Emit regime_snapshot."""
        snapshot_id = f"snap_{uuid.uuid4().hex[:12]}"
        event = {
            "event_type": "regime_snapshot",
            "event_schema_version": EVENT_SCHEMA_VERSION,
            "snapshot_id": snapshot_id,
            "run_id": self.run_id,
            "ts": (ts or datetime.now(timezone.utc)).isoformat(),
            "pair": pair,
            "timeframe": timeframe,
            "market_env": market_env,
            "htf_market_env": htf_market_env,
            "macro_phase": macro_phase,
            "bear_market_mode": bear_market_mode,
            "phase_context_tag": phase_context_tag,
            "daily_choch_confirmed": daily_choch_confirmed,
            "dist_cause_sufficient": dist_cause_sufficient,
            "daily_tr_exists": daily_tr_exists,
            "daily_gate_score": daily_gate_score,
            "daily_sm_v2": daily_sm_v2,
        }
        self._emit("regimes", event)
    
    def close(self) -> None:
        """Close all file handles."""
        for fh in self._files.values():
            try:
                fh.close()
            except Exception:
                pass
        self._files.clear()
        
        # Print summary
        total = sum(self._event_counts.values())
        print(f"[EventEmitter] Emitted {total} events to {self.run_dir}")
        for category, count in self._event_counts.items():
            print(f"  - {category}: {count}")
    
    def __del__(self):
        """Ensure files are closed on cleanup."""
        self.close()
