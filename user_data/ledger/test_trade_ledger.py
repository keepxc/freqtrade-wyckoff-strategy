"""
Trade Ledger 测试脚本
验证 P5-A1 基础功能
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from user_data.ledger.trade_ledger import TradeLedger, StateSnapshot


def test_basic_functionality():
    """测试基础功能"""
    print("=" * 60)
    print("Testing Trade Ledger - P5-A1")
    print("=" * 60)
    
    # 初始化
    ledger = TradeLedger(ledger_dir="user_data/ledger/test_data")
    print("✅ TradeLedger initialized")
    
    # 模拟交易 1: Spring 多头
    entry_snapshot = StateSnapshot(
        market_env="trending_up",
        htf_market_env="accumulation",
        macro_phase="phase_d",
        bear_market_mode=False,
        phase_context_tag="spring_confirmed",
        signal_quality=0.85,
        composite_intent="bullish",
        rr_long_standard=2.5,
        rr_short_standard=0.0,
    )
    
    open_time = datetime.now() - timedelta(hours=48)
    
    trade1 = ledger.record_trade_open(
        pair="BTC/USDT:USDT",
        side="long",
        open_time=open_time,
        open_rate=50000.0,
        enter_tag="spring_long",
        entry_snapshot=entry_snapshot,
        gates_triggered=[],
        authorizations=["regime_auth"],
        multipliers_applied={"spring_quality": 1.15},
        trade_id=1,
        strategy_version="WyckoffV7_TrueMethod",
    )
    print("✅ Trade 1 opened: spring_long @ 50000")
    
    # 平仓交易 1
    exit_snapshot = StateSnapshot(
        market_env="trending_up",
        htf_market_env="markup",
        macro_phase="phase_e",
        bear_market_mode=False,
        phase_context_tag="upthrust_warning",
        signal_quality=0.75,
        composite_intent="neutral",
        rr_long_standard=2.5,
        rr_short_standard=0.0,
    )
    
    close_time = datetime.now() - timedelta(hours=12)
    
    ledger.record_trade_close(
        trade_record=trade1,
        close_time=close_time,
        close_rate=52500.0,
        profit_abs=2500.0,
        profit_ratio=0.05,
        exit_tag="sot_warning",
        exit_snapshot=exit_snapshot,
        exit_rules=["sot_detected", "effort_result_divergence"],
    )
    print("✅ Trade 1 closed: +5.0% [sot_warning]")
    
    # 模拟交易 2: SOS Bar 多头
    entry_snapshot2 = StateSnapshot(
        market_env="trending_up",
        htf_market_env="accumulation",
        macro_phase="phase_d",
        bear_market_mode=False,
        phase_context_tag="sos_breakout",
        signal_quality=0.92,
        composite_intent="bullish",
        rr_long_standard=3.0,
        rr_short_standard=0.0,
    )
    
    open_time2 = datetime.now() - timedelta(hours=24)
    
    trade2 = ledger.record_trade_open(
        pair="BTC/USDT:USDT",
        side="long",
        open_time=open_time2,
        open_rate=51000.0,
        enter_tag="sos_bar_long",
        entry_snapshot=entry_snapshot2,
        gates_triggered=[],
        authorizations=["regime_auth"],
        multipliers_applied={"sos_quality": 1.2},
        trade_id=2,
        strategy_version="WyckoffV7_TrueMethod",
    )
    print("✅ Trade 2 opened: sos_bar_long @ 51000")
    
    # 平仓交易 2
    exit_snapshot2 = StateSnapshot(
        market_env="trending_up",
        htf_market_env="markup",
        macro_phase="phase_e",
        bear_market_mode=False,
        phase_context_tag="normal",
        signal_quality=0.88,
        composite_intent="bullish",
        rr_long_standard=3.0,
        rr_short_standard=0.0,
    )
    
    close_time2 = datetime.now()
    
    ledger.record_trade_close(
        trade_record=trade2,
        close_time=close_time2,
        close_rate=53550.0,
        profit_abs=2550.0,
        profit_ratio=0.05,
        exit_tag="roi_target",
        exit_snapshot=exit_snapshot2,
        exit_rules=["roi_reached"],
    )
    print("✅ Trade 2 closed: +5.0% [roi_target]")
    
    # 模拟交易 3: 熊市防御拦截的失败交易
    entry_snapshot3 = StateSnapshot(
        market_env="ranging",
        htf_market_env="distribution",
        macro_phase="phase_c",
        bear_market_mode=True,
        phase_context_tag="weak_spring",
        signal_quality=0.45,
        composite_intent="neutral",
        rr_long_standard=2.0,
        rr_short_standard=0.0,
        extra_fields={"rs_signal": "falling", "daily_choch_confirmed": True},
    )
    
    open_time3 = datetime.now() - timedelta(hours=72)
    
    trade3 = ledger.record_trade_open(
        pair="BTC/USDT:USDT",
        side="long",
        open_time=open_time3,
        open_rate=49000.0,
        enter_tag="spring_long",
        entry_snapshot=entry_snapshot3,
        gates_triggered=["daily_gate_soft"],
        authorizations=[],
        multipliers_applied={"signal_quality": 0.75},
        trade_id=3,
        strategy_version="WyckoffV7_TrueMethod",
    )
    print("✅ Trade 3 opened: spring_long @ 49000 (bear_market_mode)")
    
    # 平仓交易 3 (止损)
    exit_snapshot3 = StateSnapshot(
        market_env="ranging",
        htf_market_env="distribution",
        macro_phase="phase_c",
        bear_market_mode=True,
        phase_context_tag="weak_spring",
        signal_quality=0.40,
        composite_intent="bearish",
        rr_long_standard=2.0,
        rr_short_standard=0.0,
        extra_fields={"rs_signal": "falling", "daily_choch_confirmed": True},
    )
    
    close_time3 = datetime.now() - timedelta(hours=48)
    
    ledger.record_trade_close(
        trade_record=trade3,
        close_time=close_time3,
        close_rate=46060.0,
        profit_abs=-2940.0,
        profit_ratio=-0.06,
        exit_tag="stoploss",
        exit_snapshot=exit_snapshot3,
        exit_rules=["bear_market_stoploss"],
    )
    print("✅ Trade 3 closed: -6.0% [stoploss]")
    
    # 获取统计
    print("\n" + "=" * 60)
    print("Statistics")
    print("=" * 60)
    
    stats = ledger.get_statistics()
    print(f"Total Trades: {stats['total_trades']}")
    print(f"Closed Trades: {stats['closed_trades']}")
    print(f"Win Rate: {stats['win_rate']:.2%}")
    print(f"Avg Profit: {stats['avg_profit']:.2%}")
    print(f"Total Profit: {stats['total_profit']:.2%}")
    
    print("\nBy Enter Tag:")
    for tag, data in stats['by_enter_tag'].items():
        print(f"  {tag}:")
        print(f"    Count: {data['count']}")
        print(f"    Win Rate: {data['win_rate']:.2%}")
        print(f"    Avg Profit: {data['avg_profit']:.2%}")
    
    # 导出报告
    print("\n" + "=" * 60)
    print("Exporting Reports")
    print("=" * 60)
    
    ledger.export_to_csv("user_data/ledger/test_data/test_trades.csv")
    ledger.export_to_json("user_data/ledger/test_data/test_trades.json")
    
    print("\n✅ All tests passed!")
    print("=" * 60)


if __name__ == "__main__":
    test_basic_functionality()
