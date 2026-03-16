"""
Trade Ledger - P5-A1
统一交易账本系统

功能：
- 记录每笔交易的完整生命周期
- 记录入场和出场时的关键状态字段快照
- 记录触发的门控/授权/multiplier/退出规则
- 支持 JSON/CSV 导出
- 支持按时间范围查询和统计
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict, field
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class StateSnapshot:
    """状态快照 - 记录关键字段"""
    # 市场环境
    market_env: Optional[str] = None
    htf_market_env: Optional[str] = None
    macro_phase: Optional[str] = None
    bear_market_mode: Optional[bool] = None
    
    # Phase 上下文
    phase_context_tag: Optional[str] = None
    
    # 信号质量
    signal_quality: Optional[float] = None
    composite_intent: Optional[str] = None
    
    # RR 标准
    rr_long_standard: Optional[float] = None
    rr_short_standard: Optional[float] = None
    
    # 其他关键字段
    extra_fields: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TradeRecord:
    """交易记录 - 完整生命周期"""
    # 基础信息
    pair: str
    side: str  # 'long' or 'short'
    enter_tag: Optional[str] = None
    exit_tag: Optional[str] = None
    
    # 时间信息
    open_time: Optional[datetime] = None
    close_time: Optional[datetime] = None
    holding_duration_hours: Optional[float] = None
    
    # 盈亏信息
    open_rate: Optional[float] = None
    close_rate: Optional[float] = None
    profit_abs: Optional[float] = None
    profit_ratio: Optional[float] = None
    r_multiple: Optional[float] = None  # R 倍数
    
    # 状态快照
    entry_snapshot: Optional[StateSnapshot] = None
    exit_snapshot: Optional[StateSnapshot] = None
    
    # 规则触发记录
    gates_triggered: List[str] = field(default_factory=list)
    authorizations: List[str] = field(default_factory=list)
    multipliers_applied: Dict[str, float] = field(default_factory=dict)
    exit_rules: List[str] = field(default_factory=list)
    
    # 元数据
    trade_id: Optional[int] = None
    strategy_version: Optional[str] = None
    recorded_at: datetime = field(default_factory=datetime.now)


class TradeLedger:
    """
    Trade Ledger - 交易账本
    
    职责：
    1. 记录每笔交易的完整信息
    2. 提供查询和统计接口
    3. 支持导出为 JSON/CSV
    4. 支持增量写入（避免内存溢出）
    """
    
    def __init__(self, ledger_dir: str = "user_data/ledger/data"):
        """
        初始化 Trade Ledger
        
        Args:
            ledger_dir: 账本数据存储目录
        """
        self.ledger_dir = Path(ledger_dir)
        self.ledger_dir.mkdir(parents=True, exist_ok=True)
        
        # 当前会话的交易记录（内存缓存）
        self.trades: List[TradeRecord] = []
        
        # 当前会话文件
        self.session_file = self.ledger_dir / f"trades_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
        
        logger.info(f"✅ TradeLedger initialized: {self.ledger_dir}")
    
    def record_trade_open(
        self,
        pair: str,
        side: str,
        open_time: datetime,
        open_rate: float,
        enter_tag: Optional[str] = None,
        entry_snapshot: Optional[StateSnapshot] = None,
        gates_triggered: Optional[List[str]] = None,
        authorizations: Optional[List[str]] = None,
        multipliers_applied: Optional[Dict[str, float]] = None,
        trade_id: Optional[int] = None,
        strategy_version: Optional[str] = None,
    ) -> TradeRecord:
        """
        记录交易开仓
        
        Args:
            pair: 交易对
            side: 方向 ('long' or 'short')
            open_time: 开仓时间
            open_rate: 开仓价格
            enter_tag: 入场标签
            entry_snapshot: 入场时状态快照
            gates_triggered: 触发的门控列表
            authorizations: 授权列表
            multipliers_applied: 应用的乘数
            trade_id: 交易ID
            strategy_version: 策略版本
        
        Returns:
            TradeRecord: 交易记录对象
        """
        record = TradeRecord(
            pair=pair,
            side=side,
            open_time=open_time,
            open_rate=open_rate,
            enter_tag=enter_tag,
            entry_snapshot=entry_snapshot,
            gates_triggered=gates_triggered or [],
            authorizations=authorizations or [],
            multipliers_applied=multipliers_applied or {},
            trade_id=trade_id,
            strategy_version=strategy_version,
        )
        
        self.trades.append(record)
        logger.debug(f"📝 Trade opened: {pair} {side} @ {open_rate} [{enter_tag}]")
        
        return record
    
    def record_trade_close(
        self,
        trade_record: TradeRecord,
        close_time: datetime,
        close_rate: float,
        profit_abs: float,
        profit_ratio: float,
        exit_tag: Optional[str] = None,
        exit_snapshot: Optional[StateSnapshot] = None,
        exit_rules: Optional[List[str]] = None,
    ) -> None:
        """
        记录交易平仓
        
        Args:
            trade_record: 交易记录对象
            close_time: 平仓时间
            close_rate: 平仓价格
            profit_abs: 绝对盈亏
            profit_ratio: 盈亏比例
            exit_tag: 出场标签
            exit_snapshot: 出场时状态快照
            exit_rules: 触发的退出规则
        """
        trade_record.close_time = close_time
        trade_record.close_rate = close_rate
        trade_record.profit_abs = profit_abs
        trade_record.profit_ratio = profit_ratio
        trade_record.exit_tag = exit_tag
        trade_record.exit_snapshot = exit_snapshot
        trade_record.exit_rules = exit_rules or []
        
        # 计算持仓时长
        if trade_record.open_time:
            duration = close_time - trade_record.open_time
            trade_record.holding_duration_hours = duration.total_seconds() / 3600
        
        # 计算 R 倍数（如果有止损信息）
        # TODO: 需要从 trade 对象获取止损信息
        
        # 持久化到文件
        self._append_to_file(trade_record)
        
        logger.debug(
            f"📝 Trade closed: {trade_record.pair} {trade_record.side} "
            f"P/L: {profit_ratio:.2%} [{exit_tag}]"
        )
    
    def _append_to_file(self, record: TradeRecord) -> None:
        """
        追加记录到文件（JSONL 格式）
        
        Args:
            record: 交易记录
        """
        try:
            with open(self.session_file, 'a', encoding='utf-8') as f:
                # 转换为字典
                record_dict = self._record_to_dict(record)
                # 写入 JSONL
                f.write(json.dumps(record_dict, ensure_ascii=False, default=str) + '\n')
        except Exception as e:
            logger.error(f"❌ Failed to append trade record to file: {e}")
    
    def _record_to_dict(self, record: TradeRecord) -> Dict:
        """
        将 TradeRecord 转换为字典（用于序列化）
        
        Args:
            record: 交易记录
        
        Returns:
            Dict: 字典表示
        """
        data = asdict(record)
        
        # 转换 datetime 为 ISO 格式字符串
        if data.get('open_time'):
            data['open_time'] = data['open_time'].isoformat()
        if data.get('close_time'):
            data['close_time'] = data['close_time'].isoformat()
        if data.get('recorded_at'):
            data['recorded_at'] = data['recorded_at'].isoformat()
        
        return data
    
    def get_trades(
        self,
        pair: Optional[str] = None,
        side: Optional[str] = None,
        enter_tag: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> List[TradeRecord]:
        """
        查询交易记录
        
        Args:
            pair: 交易对过滤
            side: 方向过滤
            enter_tag: 入场标签过滤
            start_time: 开始时间
            end_time: 结束时间
        
        Returns:
            List[TradeRecord]: 符合条件的交易记录
        """
        filtered = self.trades
        
        if pair:
            filtered = [t for t in filtered if t.pair == pair]
        if side:
            filtered = [t for t in filtered if t.side == side]
        if enter_tag:
            filtered = [t for t in filtered if t.enter_tag == enter_tag]
        if start_time:
            filtered = [t for t in filtered if t.open_time and t.open_time >= start_time]
        if end_time:
            filtered = [t for t in filtered if t.open_time and t.open_time <= end_time]
        
        return filtered
    
    def export_to_csv(self, output_path: str) -> None:
        """
        导出为 CSV 文件
        
        Args:
            output_path: 输出文件路径
        """
        if not self.trades:
            logger.warning("⚠️ No trades to export")
            return
        
        # 转换为 DataFrame
        records = [self._record_to_dict(t) for t in self.trades]
        df = pd.json_normalize(records)
        
        # 导出
        df.to_csv(output_path, index=False)
        logger.info(f"✅ Exported {len(self.trades)} trades to {output_path}")
    
    def export_to_json(self, output_path: str) -> None:
        """
        导出为 JSON 文件
        
        Args:
            output_path: 输出文件路径
        """
        if not self.trades:
            logger.warning("⚠️ No trades to export")
            return
        
        records = [self._record_to_dict(t) for t in self.trades]
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(records, f, ensure_ascii=False, indent=2, default=str)
        
        logger.info(f"✅ Exported {len(self.trades)} trades to {output_path}")
    
    def load_from_file(self, file_path: str) -> int:
        """
        从 JSONL 文件加载交易记录
        
        Args:
            file_path: 文件路径
        
        Returns:
            int: 加载的记录数
        """
        count = 0
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        data = json.loads(line)
                        # TODO: 反序列化为 TradeRecord 对象
                        count += 1
            
            logger.info(f"✅ Loaded {count} trades from {file_path}")
            return count
        except Exception as e:
            logger.error(f"❌ Failed to load trades from {file_path}: {e}")
            return 0
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        获取统计信息
        
        Returns:
            Dict: 统计数据
        """
        if not self.trades:
            return {}
        
        closed_trades = [t for t in self.trades if t.close_time is not None]
        
        if not closed_trades:
            return {
                'total_trades': len(self.trades),
                'open_trades': len(self.trades),
                'closed_trades': 0,
            }
        
        profits = [t.profit_ratio for t in closed_trades if t.profit_ratio is not None]
        winning_trades = [t for t in closed_trades if t.profit_ratio and t.profit_ratio > 0]
        losing_trades = [t for t in closed_trades if t.profit_ratio and t.profit_ratio <= 0]
        
        stats = {
            'total_trades': len(self.trades),
            'open_trades': len(self.trades) - len(closed_trades),
            'closed_trades': len(closed_trades),
            'winning_trades': len(winning_trades),
            'losing_trades': len(losing_trades),
            'win_rate': len(winning_trades) / len(closed_trades) if closed_trades else 0,
            'avg_profit': sum(profits) / len(profits) if profits else 0,
            'total_profit': sum(profits) if profits else 0,
        }
        
        # 按 enter_tag 分组统计
        enter_tags = {}
        for trade in closed_trades:
            tag = trade.enter_tag or 'unknown'
            if tag not in enter_tags:
                enter_tags[tag] = {'count': 0, 'wins': 0, 'total_profit': 0}
            enter_tags[tag]['count'] += 1
            if trade.profit_ratio and trade.profit_ratio > 0:
                enter_tags[tag]['wins'] += 1
            if trade.profit_ratio:
                enter_tags[tag]['total_profit'] += trade.profit_ratio
        
        for tag, data in enter_tags.items():
            data['win_rate'] = data['wins'] / data['count'] if data['count'] > 0 else 0
            data['avg_profit'] = data['total_profit'] / data['count'] if data['count'] > 0 else 0
        
        stats['by_enter_tag'] = enter_tags
        
        return stats
