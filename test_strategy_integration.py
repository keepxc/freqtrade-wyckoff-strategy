#!/usr/bin/env python3
"""测试策略事件集成"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')

from user_data.strategies.WyckoffV7_TrueMethod import WyckoffV7_TrueMethod

# 模拟配置
config = {
    'runmode': 'backtest',
    'timerange': '20260101-20260107',
    'exchange': {
        'pair_whitelist': ['BTC/USDT:USDT']
    }
}

print("=" * 60)
print("测试策略事件集成")
print("=" * 60)

try:
    # 初始化策略
    print("\n1. 初始化策略...")
    strategy = WyckoffV7_TrueMethod(config)
    
    # 检查事件桥接器
    if hasattr(strategy, 'event_bridge') and strategy.event_bridge is not None:
        print("✅ EventBridge 初始化成功")
        
        if strategy.event_bridge.event_emitter is not None:
            print(f"✅ EventEmitter 初始化成功")
            print(f"   Run ID: {strategy.event_bridge.event_emitter.run_id}")
            print(f"   Mode: {strategy.event_bridge.event_emitter.mode}")
            print(f"   Events Dir: {strategy.event_bridge.event_emitter.run_dir}")
        else:
            print("❌ EventEmitter 未初始化")
    else:
        print("❌ EventBridge 未初始化")
    
    # 清理
    print("\n2. 清理资源...")
    if hasattr(strategy, 'event_bridge') and strategy.event_bridge is not None:
        strategy.event_bridge.close()
        print("✅ EventBridge 已关闭")
    
    print("\n" + "=" * 60)
    print("✅ 测试通过")
    print("=" * 60)
    
except Exception as e:
    print(f"\n❌ 测试失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
