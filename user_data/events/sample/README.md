# P5 Event System Quick Start

## 概述

P5 事件系统将策略运行时的关键事实记录为结构化 JSONL 事件流，用于后续分析、诊断和智能决策。

## 快速验证

### 1. 摄取示例事件
```bash
python user_data/ingest/ingest_jsonl.py \
    --db user_data/market.db \
    --events user_data/examples/sample_events.jsonl
```

### 2. 生成周报
```bash
python user_data/reports/build_weekly_report.py \
    --db user_data/market.db \
    --week-start 2026-03-10T00:00:00Z \
    --week-end 2026-03-17T00:00:00Z
```

### 3. 查看数据
```bash
python -c "
import sqlite3
conn = sqlite3.connect('user_data/market.db')
print('Runs:', conn.execute('SELECT COUNT(*) FROM experiment_runs').fetchone()[0])
print('Trades:', conn.execute('SELECT COUNT(*) FROM trades').fetchone()[0])
print('Signals:', conn.execute('SELECT COUNT(*) FROM signals').fetchone()[0])
conn.close()
"
```

## 在策略中集成事件发射器

### 基础集成

```python
from user_data.event_emitter import EventEmitter

class YourStrategy(IStrategy):
    def __init__(self, config: dict):
        super().__init__(config)
        
        # 初始化事件发射器
        self.event_emitter = EventEmitter(
            strategy_name=self.__class__.__name__,
            events_dir="user_data/events",
            mode="backtest"  # 或 "dry_run" / "live"
        )
        
        # 发送 run_start 事件
        self.event_emitter.emit_run_start(
            run_type="baseline",
            strategy_version="v7.3.0",
            timerange="20230101-20260228",
            pair_scope="BTC/USDT:USDT"
        )
```

### 记录信号事件

在 `populate_entry_trend` 中记录候选信号：

```python
def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
    # ... 你的信号逻辑 ...
    
    # 当检测到候选信号时
    if some_signal_condition:
        self.event_emitter.emit_signal(
            pair=metadata['pair'],
            timeframe=self.timeframe,
            side='long',
            signal_name='wyckoff_spring',
            candidate_enter_tag='wyckoff_spring',
            was_executed=True,  # 或 False 如果被拒绝
            signal_quality_before=1.0,
            signal_quality_after=1.15,
            market_env='markup',
            phase_context_tag='phase_d',
            daily_gate_score=1.0,
        )
    
    return dataframe
```

### 记录交易事件

在 `confirm_trade_entry` 中记录交易开仓：

```python
def confirm_trade_entry(self, pair: str, order_type: str, amount: float, 
                       rate: float, time_in_force: str, current_time: datetime,
                       entry_tag: Optional[str], side: str, **kwargs) -> bool:
    
    # ... 你的确认逻辑 ...
    
    if confirmed:
        # 生成 trade_id（或使用 Freqtrade 的 trade.id）
        trade_id = f"trade_{int(current_time.timestamp())}"
        
        self.event_emitter.emit_trade_open(
            trade_id=trade_id,
            pair=pair,
            side=side,
            enter_tag=entry_tag or 'unknown',
            entry_price=rate,
            stake_amount=amount * rate,
            leverage=self.leverage(...),
            signal_quality_enter=1.15,
            market_env_enter='markup',
            phase_context_tag_enter='phase_d',
        )
    
    return confirmed
```

### 记录 Detector 事件

在关键检测器触发时：

```python
def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
    # ... 计算指标 ...
    
    # 当检测到关键状态变化时
    if swing_down_er_exhaustion_detected:
        self.event_emitter.emit_detector_event(
            pair=metadata['pair'],
            timeframe=self.timeframe,
            event_family='wave_er',
            event_name='swing_down_er_exhaustion',
            event_value=True,
            event_confidence=1.0,
            metadata={'effort_ratio': 1.18, 'result_ratio': 0.82}
        )
    
    return dataframe
```

### 记录 Regime 快照

建议每根主周期 bar 记录一次：

```python
def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
    # ... 计算环境状态 ...
    
    # 在最后一根 bar 记录快照
    if len(dataframe) > 0:
        last_idx = len(dataframe) - 1
        self.event_emitter.emit_regime_snapshot(
            pair=metadata['pair'],
            timeframe=self.timeframe,
            market_env=dataframe.iloc[last_idx]['market_env'],
            htf_market_env=dataframe.iloc[last_idx]['htf_market_env'],
            macro_phase=dataframe.iloc[last_idx]['macro_phase'],
            phase_context_tag=dataframe.iloc[last_idx]['phase_context_tag'],
            daily_gate_score=dataframe.iloc[last_idx]['daily_gate_score'],
        )
    
    return dataframe
```

## 事件文件组织

事件会自动保存到：
```
user_data/events/
  backtest/
    run_20260314_123456_backtest_abc123/
      run.jsonl
      signals.jsonl
      trades.jsonl
      detectors.jsonl
      regimes.jsonl
  dry_run/
    run_20260315_090000_dry_run_def456/
      ...
```

## 后续流程

1. **运行策略** → 生成 JSONL 事件
2. **摄取事件** → 导入 SQLite 数据库
3. **生成周报** → 分析交易表现和环境变化
4. **AI 诊断** → 基于数据库生成洞察和建议

## 注意事项

- 事件发射器设计为"永不崩溃"：即使写入失败也不会影响策略执行
- 所有时间戳使用 UTC ISO8601 格式
- 事件文件按 run 维度隔离，便于重放和审计
- 可选字段可以传 `None`，会被正确处理

## 完整示例

参考 `user_data/examples/sample_events.jsonl` 查看完整的事件格式示例。
