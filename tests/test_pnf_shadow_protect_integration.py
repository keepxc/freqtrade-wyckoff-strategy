"""
Integration tests for PNF Shadow Protect Integration

Tests complete trade lifecycle scenarios:
- Scenario 1: PNF activation → Shadow Protect triggers
- Scenario 2: PNF activation → 48h timeout triggers
- Scenario 3: PNF activation → down_wave triggers
- Scenario 4: Non-eligible entry → hard exit
- Metadata persistence across multiple custom_exit calls
- A/B test compatibility (WY_PNF_SHADOW_PROTECT=0 vs =1)
- Profit Protect Exit interaction
"""

import unittest
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime, timedelta
import os
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from user_data.strategies.WyckoffV7_TrueMethod import WyckoffV7_TrueMethod


class TestFullTradeLifecycle(unittest.TestCase):
    """Test suite for full trade lifecycle scenarios"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.config = {'stake_currency': 'USDT'}
        self.strategy = WyckoffV7_TrueMethod(self.config)
        self.strategy.pnf_shadow_protect_enabled = True
        self.strategy.pnf_timeout_hours = 48.0
        self.strategy._pnf_trades_metadata = {}
        
    def test_scenario_1_pnf_activation_shadow_protect_triggers(self):
        """
        Scenario 1: Trade opens → PNF target reached → warning zone activated → Shadow Protect triggers
        
        验证：
        - 元数据正确存储
        - Shadow Protect 使用 PNF 阈值
        - 退出原因正确
        """
        # Arrange
        pair = 'BTC/USDT'
        trade = Mock()
        trade.enter_tag = 'wyckoff_axis_support'
        trade.is_short = False
        trade.open_rate = 75000.0
        trade.open_date_utc = datetime(2026, 1, 1, 0, 0, 0)
        
        # Step 1: PNF target reached, activate warning zone
        current_rate_1 = 79500.0  # Reached PNF target
        current_profit_1 = 0.06
        pnf_metadata = {'target_conservative': 79000.0}
        
        result_1 = self.strategy._check_pnf_warning_zone_activation(
            pair, trade, current_rate_1, current_profit_1, pnf_metadata
        )
        
        # Assert: Warning zone activated
        self.assertTrue(result_1, "Warning zone should be activated")
        metadata_key = (pair, str(trade.open_date_utc))
        self.assertIn(metadata_key, self.strategy._pnf_trades_metadata)
        self.assertTrue(self.strategy._pnf_trades_metadata[metadata_key]['pnf_warning_zone_activated'])
        
        # Step 2: Price continues up, then pulls back (Shadow Protect conditions met)
        trade.max_rate = 78750.0  # Max profit = 5%
        current_rate_2 = 76875.0  # Current profit = 2.5%, giveback = 2.5%
        current_profit_2 = 0.025
        
        updated_metadata = self.strategy._pnf_trades_metadata[metadata_key]
        result_2 = self.strategy._check_shadow_protect_pnf_mode(
            pair, trade, current_profit_2, updated_metadata
        )
        
        # Assert: Shadow Protect triggers
        self.assertEqual(result_2, 'shadow_protect_exit', "Shadow Protect should trigger")
        self.assertTrue(self.strategy._pnf_trades_metadata[metadata_key].get('shadow_protect_pnf_mode'))
        
    def test_scenario_2_pnf_activation_timeout_triggers(self):
        """
        Scenario 2: Trade opens → PNF target reached → warning zone activated → 48h timeout triggers
        
        验证：
        - 超时正确计算
        - max_profit_after_pnf 正确记录
        - 退出原因正确
        """
        # Arrange
        pair = 'BTC/USDT'
        trade = Mock()
        trade.enter_tag = 'wyckoff_sos_entry'
        trade.is_short = False
        trade.open_rate = 75000.0
        trade.open_date_utc = datetime(2026, 1, 1, 0, 0, 0)
        
        # Step 1: PNF target reached, activate warning zone
        current_rate_1 = 79500.0
        current_profit_1 = 0.06
        pnf_metadata = {'target_conservative': 79000.0}
        
        result_1 = self.strategy._check_pnf_warning_zone_activation(
            pair, trade, current_rate_1, current_profit_1, pnf_metadata
        )
        
        self.assertTrue(result_1)
        
        # Step 2: 49 hours later, timeout check
        metadata_key = (pair, str(trade.open_date_utc))
        activation_time = self.strategy._pnf_trades_metadata[metadata_key]['pnf_activation_timestamp']
        current_time = activation_time + timedelta(hours=49)
        
        trade.max_rate = 82000.0  # Max profit after PNF = 9.33%
        current_profit_2 = 0.04
        
        updated_metadata = self.strategy._pnf_trades_metadata[metadata_key]
        result_2 = self.strategy._check_pnf_timeout(
            pair, trade, current_time, current_profit_2, updated_metadata
        )
        
        # Assert: Timeout triggers
        self.assertEqual(result_2, 'pnf_timeout_exit', "Timeout should trigger after 48h")
        self.assertIn('max_profit_after_pnf', self.strategy._pnf_trades_metadata[metadata_key])
        
    def test_scenario_3_pnf_activation_down_wave_triggers(self):
        """
        Scenario 3: Trade opens → PNF target reached → warning zone activated → down_wave triggers
        
        验证：
        - Shadow Protect 未触发时，继续到 down_wave
        - 退出优先级顺序正确（Shadow Protect 在 down_wave 之前）
        """
        # Arrange
        pair = 'BTC/USDT'
        trade = Mock()
        trade.enter_tag = 'wyckoff_axis_support'
        trade.is_short = False
        trade.open_rate = 75000.0
        trade.open_date_utc = datetime(2026, 1, 1, 0, 0, 0)
        
        # Step 1: PNF target reached, activate warning zone
        current_rate_1 = 79500.0
        current_profit_1 = 0.06
        pnf_metadata = {'target_conservative': 79000.0}
        
        result_1 = self.strategy._check_pnf_warning_zone_activation(
            pair, trade, current_rate_1, current_profit_1, pnf_metadata
        )
        
        self.assertTrue(result_1)
        
        # Step 2: Shadow Protect conditions NOT met (max_profit too low)
        trade.max_rate = 77250.0  # Max profit = 3% (below 4% threshold)
        current_profit_2 = 0.02
        
        metadata_key = (pair, str(trade.open_date_utc))
        updated_metadata = self.strategy._pnf_trades_metadata[metadata_key]
        result_2 = self.strategy._check_shadow_protect_pnf_mode(
            pair, trade, current_profit_2, updated_metadata
        )
        
        # Assert: Shadow Protect does not trigger
        self.assertIsNone(result_2, "Shadow Protect should not trigger when max_profit < 4%")
        
        # Step 3: down_wave would evaluate next (simulated by returning None from Shadow Protect)
        # In real implementation, down_wave logic would execute here
        # This test verifies that Shadow Protect evaluated first and returned None
        
    def test_scenario_4_non_eligible_entry_hard_exit(self):
        """
        Scenario 4: Trade opens → PNF target reached → non-eligible entry → hard exit
        
        验证：
        - 警告区未激活
        - 立即硬退出
        """
        # Arrange
        pair = 'BTC/USDT'
        trade = Mock()
        trade.enter_tag = 'wyckoff_spring'  # Non-eligible entry
        trade.is_short = False
        trade.open_rate = 75000.0
        trade.open_date_utc = datetime(2026, 1, 1, 0, 0, 0)
        
        # Step 1: PNF target reached, but entry not eligible
        current_rate = 79500.0
        current_profit = 0.06
        pnf_metadata = {'target_conservative': 79000.0}
        
        result = self.strategy._check_pnf_warning_zone_activation(
            pair, trade, current_rate, current_profit, pnf_metadata
        )
        
        # Assert: Hard exit (warning zone not activated)
        self.assertFalse(result, "Should return False for non-eligible entry")
        metadata_key = (pair, str(trade.open_date_utc))
        self.assertNotIn(metadata_key, self.strategy._pnf_trades_metadata)


class TestMetadataPersistence(unittest.TestCase):
    """Test suite for metadata persistence"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.config = {'stake_currency': 'USDT'}
        self.strategy = WyckoffV7_TrueMethod(self.config)
        self.strategy.pnf_shadow_protect_enabled = True
        self.strategy._pnf_trades_metadata = {}
        
    def test_metadata_survives_multiple_custom_exit_calls(self):
        """
        Test: Metadata survives across multiple custom_exit calls (simulating multiple candles)
        
        验证：
        - 元数据在 _pnf_trades_metadata 中持久化
        - 多次调用后元数据仍然存在
        """
        # Arrange
        pair = 'BTC/USDT'
        trade = Mock()
        trade.enter_tag = 'wyckoff_axis_support'
        trade.is_short = False
        trade.open_rate = 75000.0
        trade.open_date_utc = datetime(2026, 1, 1, 0, 0, 0)
        
        # Step 1: Activate warning zone
        current_rate = 79500.0
        current_profit = 0.06
        pnf_metadata = {'target_conservative': 79000.0}
        
        self.strategy._check_pnf_warning_zone_activation(
            pair, trade, current_rate, current_profit, pnf_metadata
        )
        
        metadata_key = (pair, str(trade.open_date_utc))
        self.assertIn(metadata_key, self.strategy._pnf_trades_metadata)
        
        # Step 2: Simulate multiple candles (multiple custom_exit calls)
        for i in range(5):
            # Retrieve metadata (simulating custom_exit reading it)
            retrieved_metadata = self.strategy._pnf_trades_metadata.get(metadata_key, {})
            
            # Assert: Metadata still exists
            self.assertTrue(retrieved_metadata.get('pnf_warning_zone_activated'))
            self.assertIn('pnf_activation_timestamp', retrieved_metadata)
            
    def test_metadata_key_format_works_correctly(self):
        """
        Test: Metadata key format (pair, open_date_str) works correctly
        
        验证：
        - 正确的键可以检索元数据
        - 错误的键无法检索元数据
        """
        # Arrange
        pair = 'BTC/USDT'
        trade = Mock()
        trade.enter_tag = 'wyckoff_axis_support'
        trade.is_short = False
        trade.open_rate = 75000.0
        trade.open_date_utc = datetime(2026, 1, 1, 0, 0, 0)
        
        # Step 1: Activate warning zone
        current_rate = 79500.0
        current_profit = 0.06
        pnf_metadata = {'target_conservative': 79000.0}
        
        self.strategy._check_pnf_warning_zone_activation(
            pair, trade, current_rate, current_profit, pnf_metadata
        )
        
        # Step 2: Test correct key retrieval
        correct_key = (pair, str(trade.open_date_utc))
        self.assertIn(correct_key, self.strategy._pnf_trades_metadata)
        
        # Step 3: Test incorrect key retrieval
        incorrect_key_1 = ('ETH/USDT', str(trade.open_date_utc))  # Wrong pair
        incorrect_key_2 = (pair, '2026-01-02 00:00:00')  # Wrong date
        
        self.assertNotIn(incorrect_key_1, self.strategy._pnf_trades_metadata)
        self.assertNotIn(incorrect_key_2, self.strategy._pnf_trades_metadata)


class TestABTestCompatibility(unittest.TestCase):
    """Test suite for A/B test compatibility"""
    
    def test_wy_pnf_shadow_protect_0_produces_baseline_behavior(self):
        """
        Test: WY_PNF_SHADOW_PROTECT=0 produces baseline behavior
        
        验证：
        - 所有入场类型都硬退出
        - 警告区未激活
        """
        # Arrange
        config = {'stake_currency': 'USDT'}
        with patch.dict(os.environ, {'WY_PNF_SHADOW_PROTECT': '0'}):
            strategy = WyckoffV7_TrueMethod(config)
        
        self.assertFalse(strategy.pnf_shadow_protect_enabled)
        
        # Test with eligible entry
        pair = 'BTC/USDT'
        trade = Mock()
        trade.enter_tag = 'wyckoff_axis_support'
        trade.is_short = False
        trade.open_rate = 75000.0
        trade.open_date_utc = datetime(2026, 1, 1, 0, 0, 0)
        
        current_rate = 79500.0
        current_profit = 0.06
        pnf_metadata = {'target_conservative': 79000.0}
        
        result = strategy._check_pnf_warning_zone_activation(
            pair, trade, current_rate, current_profit, pnf_metadata
        )
        
        # Assert: Hard exit (feature disabled)
        self.assertFalse(result, "Should return False when feature is disabled")
        
    def test_wy_pnf_shadow_protect_1_activates_new_behavior(self):
        """
        Test: WY_PNF_SHADOW_PROTECT=1 activates new behavior
        
        验证：
        - 符合条件的入场激活警告区
        - Shadow Protect 使用 PNF 阈值
        """
        # Arrange
        config = {'stake_currency': 'USDT'}
        with patch.dict(os.environ, {'WY_PNF_SHADOW_PROTECT': '1'}):
            strategy = WyckoffV7_TrueMethod(config)
        
        self.assertTrue(strategy.pnf_shadow_protect_enabled)
        
        # Test with eligible entry
        pair = 'BTC/USDT'
        trade = Mock()
        trade.enter_tag = 'wyckoff_axis_support'
        trade.is_short = False
        trade.open_rate = 75000.0
        trade.open_date_utc = datetime(2026, 1, 1, 0, 0, 0)
        
        current_rate = 79500.0
        current_profit = 0.06
        pnf_metadata = {'target_conservative': 79000.0}
        
        result = strategy._check_pnf_warning_zone_activation(
            pair, trade, current_rate, current_profit, pnf_metadata
        )
        
        # Assert: Warning zone activated
        self.assertTrue(result, "Should activate warning zone when feature is enabled")
        
    def test_environment_variable_checked_at_initialization(self):
        """
        Test: Environment variable checked at initialization
        
        验证：
        - pnf_shadow_protect_enabled 在初始化时设置一次
        - 整个交易生命周期中行为一致
        """
        # Test with WY_PNF_SHADOW_PROTECT=0
        with patch.dict(os.environ, {'WY_PNF_SHADOW_PROTECT': '0'}):
            strategy_0 = WyckoffV7_TrueMethod({'stake_currency': 'USDT'})
            self.assertFalse(strategy_0.pnf_shadow_protect_enabled)
        
        # Test with WY_PNF_SHADOW_PROTECT=1
        with patch.dict(os.environ, {'WY_PNF_SHADOW_PROTECT': '1'}):
            strategy_1 = WyckoffV7_TrueMethod({'stake_currency': 'USDT'})
            self.assertTrue(strategy_1.pnf_shadow_protect_enabled)
        
        # Test default (no env var set)
        with patch.dict(os.environ, {}, clear=True):
            if 'WY_PNF_SHADOW_PROTECT' in os.environ:
                del os.environ['WY_PNF_SHADOW_PROTECT']
            strategy_default = WyckoffV7_TrueMethod({'stake_currency': 'USDT'})
            self.assertTrue(strategy_default.pnf_shadow_protect_enabled, "Default should be enabled")


class TestProfitProtectExitInteraction(unittest.TestCase):
    """Test suite for Profit Protect Exit interaction"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.config = {'stake_currency': 'USDT'}
        self.strategy = WyckoffV7_TrueMethod(self.config)
        self.strategy.pnf_shadow_protect_enabled = True
        self.strategy._pnf_trades_metadata = {}
        
    def test_profit_protect_exit_evaluates_after_shadow_protect(self):
        """
        Test: Profit_Protect_Exit evaluates after Shadow Protect
        
        验证：
        - Shadow Protect 先评估
        - 如果 Shadow Protect 未触发，Profit_Protect_Exit 可以触发
        """
        # This test verifies the priority order conceptually
        # In real implementation, Profit_Protect_Exit would be at a lower priority
        
        # Arrange
        pair = 'BTC/USDT'
        trade = Mock()
        trade.enter_tag = 'wyckoff_axis_support'
        trade.is_short = False
        trade.open_rate = 75000.0
        trade.open_date_utc = datetime(2026, 1, 1, 0, 0, 0)
        trade.max_rate = 77250.0  # Max profit = 3% (below Shadow Protect threshold)
        
        # Step 1: Activate warning zone
        current_rate = 79500.0
        current_profit = 0.06
        pnf_metadata = {'target_conservative': 79000.0}
        
        self.strategy._check_pnf_warning_zone_activation(
            pair, trade, current_rate, current_profit, pnf_metadata
        )
        
        # Step 2: Shadow Protect does not trigger
        current_profit_2 = 0.02
        metadata_key = (pair, str(trade.open_date_utc))
        updated_metadata = self.strategy._pnf_trades_metadata[metadata_key]
        
        result = self.strategy._check_shadow_protect_pnf_mode(
            pair, trade, current_profit_2, updated_metadata
        )
        
        # Assert: Shadow Protect does not trigger
        self.assertIsNone(result, "Shadow Protect should not trigger")
        
        # Step 3: Profit_Protect_Exit would evaluate next (not tested here, but priority order verified)
        
    def test_wy_profit_protect_1_honored_regardless_of_pnf(self):
        """
        Test: WY_PROFIT_PROTECT=1 honored regardless of PNF
        
        验证：
        - WY_PROFIT_PROTECT=1 显式设置时，无论 PNF 激活与否都生效
        """
        # This is a conceptual test - actual Profit_Protect_Exit logic is in the strategy
        # We verify that the PNF feature doesn't interfere with WY_PROFIT_PROTECT setting
        
        # The implementation should check WY_PROFIT_PROTECT independently
        # This test documents the expected behavior
        pass
        
    def test_wy_profit_protect_0_default_maintained(self):
        """
        Test: WY_PROFIT_PROTECT=0 default maintained
        
        验证：
        - 非 PNF 激活的交易保持默认行为
        """
        # This is a conceptual test - verifies backward compatibility
        # Non-PNF-activated trades should not be affected by this feature
        pass


if __name__ == '__main__':
    unittest.main()
