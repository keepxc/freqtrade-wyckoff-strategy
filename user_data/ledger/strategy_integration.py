"""
Strategy Integration Example - 策略集成示例
展示如何在 WyckoffV7_TrueMethod 中集成 Trade Ledger

集成点：
1. __init__: 初始化 TradeLedger
2. confirm_trade_entry: 记录开仓 + 状态快照
3. custom_exit: 记录平仓 + 状态快照
4. 回测结束: 导出统计报告
"""

from datetime import datetime
from typing import Optional
import logging

from .trade_ledger import TradeLedger, StateSnapshot, TradeRecord

logger = logging.getLogger(__name__)


class TradeLedgerMixin:
    """
    Trade Ledger 集成 Mixin
    
    使用方式：
    1. 在策略类中继承此 Mixin
    2. 在 __init__ 中调用 _init_trade_ledger()
    3. 在 confirm_trade_entry 中调用 _ledger_record_entry()
    4. 在 custom_exit 中调用 _ledger_record_exit()
    """
    
    def _init_trade_ledger(self, enable: bool = True):
        """
        初始化 Trade Ledger
        
        Args:
            enable: 是否启用 Trade Ledger
        """
        self.trade_ledger_enabled = enable
        
        if enable:
            self.trade_ledger = TradeLedger()
            # 用于跟踪开仓记录（key: (pair, open_date_str), value: TradeRecord）
            self._ledger_open_trades = {}
            logger.info("✅ Trade Ledger enabled")
        else:
            self.trade_ledger = None
            self._ledger_open_trades = {}
            logger.info("⚠️ Trade Ledger disabled")
    
    def _ledger_record_entry(
        self,
        pair: str,
        side: str,
        current_time: datetime,
        rate: float,
        entry_tag: Optional[str],
        dataframe,
        **kwargs
    ) -> None:
        """
        记录入场（在 confirm_trade_entry 中调用）
        
        Args:
            pair: 交易对
            side: 方向
            current_time: 当前时间
            rate: 入场价格
            entry_tag: 入场标签
            dataframe: 当前数据帧（用于提取状态快照）
            **kwargs: 其他参数
        """
        if not self.trade_ledger_enabled or self.trade_ledger is None:
            return
        
        try:
            # 提取状态快照
            snapshot = self._extract_state_snapshot(dataframe, current_time)
            
            # 提取门控和授权信息
            gates_triggered = kwargs.get('gates_triggered', [])
            authorizations = kwargs.get('authorizations', [])
            multipliers_applied = kwargs.get('multipliers_applied', {})
            
            # 记录开仓
            record = self.trade_ledger.record_trade_open(
                pair=pair,
                side=side,
                open_time=current_time,
                open_rate=rate,
                enter_tag=entry_tag,
                entry_snapshot=snapshot,
                gates_triggered=gates_triggered,
                authorizations=authorizations,
                multipliers_applied=multipliers_applied,
                strategy_version=self.__class__.__name__,
            )
            
            # 缓存记录（用于后续平仓时更新）
            key = (pair, current_time.isoformat())
            self._ledger_open_trades[key] = record
            
        except Exception as e:
            logger.error(f"❌ Failed to record entry in ledger: {e}")
    
    def _ledger_record_exit(
        self,
        pair: str,
        trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        exit_tag: Optional[str],
        dataframe,
        **kwargs
    ) -> None:
        """
        记录出场（在 custom_exit 中调用）
        
        Args:
            pair: 交易对
            trade: Trade 对象
            current_time: 当前时间
            current_rate: 当前价格
            current_profit: 当前盈亏
            exit_tag: 出场标签
            dataframe: 当前数据帧（用于提取状态快照）
            **kwargs: 其他参数
        """
        if not self.trade_ledger_enabled or self.trade_ledger is None:
            return
        
        try:
            # 查找对应的开仓记录
            key = (pair, trade.open_date_utc.isoformat())
            record = self._ledger_open_trades.get(key)
            
            if record is None:
                logger.warning(f"⚠️ Trade record not found for {pair} @ {trade.open_date_utc}")
                return
            
            # 提取状态快照
            snapshot = self._extract_state_snapshot(dataframe, current_time)
            
            # 提取退出规则
            exit_rules = kwargs.get('exit_rules', [])
            
            # 计算绝对盈亏
            profit_abs = (current_rate - trade.open_rate) * trade.amount
            if trade.is_short:
                profit_abs = -profit_abs
            
            # 记录平仓
            self.trade_ledger.record_trade_close(
                trade_record=record,
                close_time=current_time,
                close_rate=current_rate,
                profit_abs=profit_abs,
                profit_ratio=current_profit,
                exit_tag=exit_tag,
                exit_snapshot=snapshot,
                exit_rules=exit_rules,
            )
            
            # 从缓存中移除
            del self._ledger_open_trades[key]
            
        except Exception as e:
            logger.error(f"❌ Failed to record exit in ledger: {e}")
    
    def _extract_state_snapshot(self, dataframe, current_time: datetime) -> StateSnapshot:
        """
        从 dataframe 提取状态快照
        
        Args:
            dataframe: 数据帧
            current_time: 当前时间
        
        Returns:
            StateSnapshot: 状态快照
        """
        # 找到当前时间对应的行
        if dataframe.empty:
            return StateSnapshot()
        
        # 获取最后一行（当前状态）
        current_row = dataframe.iloc[-1]
        
        # 提取关键字段
        snapshot = StateSnapshot(
            market_env=current_row.get('market_env'),
            htf_market_env=current_row.get('htf_market_env'),
            macro_phase=current_row.get('macro_phase'),
            bear_market_mode=bool(current_row.get('bear_market_mode', False)),
            phase_context_tag=current_row.get('phase_context_tag'),
            signal_quality=float(current_row.get('signal_quality', 1.0)),
            composite_intent=current_row.get('composite_intent'),
            rr_long_standard=float(current_row.get('rr_long_standard', 0)),
            rr_short_standard=float(current_row.get('rr_short_standard', 0)),
        )
        
        # 提取其他可能有用的字段
        extra_fields = {}
        for field in ['daily_choch_confirmed', 'dist_cause_sufficient', 'rs_signal']:
            if field in current_row:
                extra_fields[field] = current_row[field]
        
        snapshot.extra_fields = extra_fields
        
        return snapshot
    
    def _ledger_export_reports(self, output_dir: str = "user_data/ledger/reports"):
        """
        导出 Ledger 报告
        
        Args:
            output_dir: 输出目录
        """
        if not self.trade_ledger_enabled or self.trade_ledger is None:
            return
        
        from pathlib import Path
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        # 导出 CSV
        csv_path = output_path / f"trades_{timestamp}.csv"
        self.trade_ledger.export_to_csv(str(csv_path))
        
        # 导出 JSON
        json_path = output_path / f"trades_{timestamp}.json"
        self.trade_ledger.export_to_json(str(json_path))
        
        # 导出统计报告
        stats = self.trade_ledger.get_statistics()
        stats_path = output_path / f"stats_{timestamp}.json"
        
        import json
        with open(stats_path, 'w', encoding='utf-8') as f:
            json.dump(stats, f, ensure_ascii=False, indent=2, default=str)
        
        logger.info(f"✅ Ledger reports exported to {output_dir}")
        
        # 打印统计摘要
        self._print_ledger_summary(stats)
    
    def _print_ledger_summary(self, stats: dict):
        """
        打印 Ledger 统计摘要
        
        Args:
            stats: 统计数据
        """
        logger.info("=" * 60)
        logger.info("Trade Ledger Summary")
        logger.info("=" * 60)
        logger.info(f"Total Trades: {stats.get('total_trades', 0)}")
        logger.info(f"Closed Trades: {stats.get('closed_trades', 0)}")
        logger.info(f"Open Trades: {stats.get('open_trades', 0)}")
        logger.info(f"Win Rate: {stats.get('win_rate', 0):.2%}")
        logger.info(f"Avg Profit: {stats.get('avg_profit', 0):.2%}")
        logger.info(f"Total Profit: {stats.get('total_profit', 0):.2%}")
        
        # 按 enter_tag 统计
        by_tag = stats.get('by_enter_tag', {})
        if by_tag:
            logger.info("\nBy Enter Tag:")
            for tag, data in sorted(by_tag.items(), key=lambda x: x[1]['count'], reverse=True):
                logger.info(
                    f"  {tag}: {data['count']} trades, "
                    f"WR={data['win_rate']:.2%}, "
                    f"Avg={data['avg_profit']:.2%}"
                )
        
        logger.info("=" * 60)


# ============================================================================
# 集成示例代码片段
# ============================================================================

"""
在 WyckoffV7_TrueMethod 中集成 Trade Ledger 的步骤：

1. 在 __init__ 中初始化：

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        
        # ... 现有初始化代码 ...
        
        # P5-A1: 初始化 Trade Ledger
        from user_data.ledger.strategy_integration import TradeLedgerMixin
        self._init_trade_ledger(enable=True)


2. 在 confirm_trade_entry 中记录开仓：

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float, 
                           rate: float, time_in_force: str, current_time: datetime,
                           entry_tag: Optional[str], side: str, **kwargs) -> bool:
        
        # ... 现有逻辑 ...
        
        # P5-A1: 记录入场
        if hasattr(self, '_ledger_record_entry'):
            dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            self._ledger_record_entry(
                pair=pair,
                side=side,
                current_time=current_time,
                rate=rate,
                entry_tag=entry_tag,
                dataframe=dataframe,
                gates_triggered=['daily_gate'] if self.use_daily_gate else [],
                authorizations=['regime_auth'] if hasattr(self, 'regime_authorizer') else [],
            )
        
        return True


3. 在 custom_exit 中记录平仓：

    def custom_exit(self, pair: str, trade: 'Trade', current_time: datetime,
                   current_rate: float, current_profit: float, **kwargs) -> Optional[str]:
        
        # ... 现有退出逻辑 ...
        
        if should_exit:
            # P5-A1: 记录出场
            if hasattr(self, '_ledger_record_exit'):
                dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
                self._ledger_record_exit(
                    pair=pair,
                    trade=trade,
                    current_time=current_time,
                    current_rate=current_rate,
                    current_profit=current_profit,
                    exit_tag=exit_reason,
                    dataframe=dataframe,
                    exit_rules=[exit_reason],
                )
            
            return exit_reason
        
        return None


4. 在回测结束后导出报告（可选，通过脚本调用）：

    # 在回测脚本中
    strategy._ledger_export_reports()
"""
