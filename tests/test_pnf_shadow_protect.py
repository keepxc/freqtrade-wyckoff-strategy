"""
Unit tests for PNF Shadow Protect Integration

Tests the three new helper methods:
- _check_pnf_warning_zone_activation
- _check_pnf_timeout
- _check_shadow_protect_pnf_mode

And the modified metadata builder:
- _build_axis_shadow_profit_protect_metadata (with pnf_mode parameter)
"""

import unittest
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime, timedelta
import os
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from user_data.strategies.WyckoffV7_TrueMethod import WyckoffV7_TrueMethod


class TestPNFWarningZoneActivation(unittest.TestCase):
    """Test suite for _check_pnf_warning_zone_activation method"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.config = {'stake_currency': 'USDT'}
        self.strategy = WyckoffV7_TrueMethod(self.config)
        self.strategy.pnf_shadow_protect_enabled = True
        self.strategy._pnf_trades_metadata = {}
        
    def test_eligible_entry_pnf_target_reached_activates_warning_zone(self):
        """Test: Eligible entry type with PNF target reached → activates warning zone"""
        # Arrange
        pair = 'BTC/USDT'
        trade = Mock()
        trade.enter_tag = 'wyckoff_axis_support'
        trade.is_short = False
        trade.open_date_utc = datetime(2026, 1, 1, 0, 0, 0)
        
        current_rate = 80000.0
        current_profit = 0.05
        pnf_metadata = {'target_conservative': 79000.0}
        
        # Act
        result = self.strategy._check_pnf_warning_zone_activation(
            pair, trade, current_rate, current_profit, pnf_metadata
        )
        
        # Assert
        self.assertTrue(result, "Should activate warning zone for eligible entry")
        metadata_key = (pair, str(trade.open_date_utc))
        self.assertIn(metadata_key, self.strategy._pnf_trades_metadata)
        self.assertTrue(self.strategy._pnf_trades_metadata[metadata_key]['pnf_warning_zone_activated'])
        
    def test_non_eligible_entry_pnf_target_reached_hard_exit(self):
        """Test: Non-eligible entry type with PNF target reached → hard exit"""
        # Arrange
        pair = 'BTC/USDT'
        trade = Mock()
        trade.enter_tag = 'wyckoff_spring'  # Non-eligible
        trade.is_short = False
        trade.open_date_utc = datetime(2026, 1, 1, 0, 0, 0)
        
        current_rate = 80000.0
        current_profit = 0.05
        pnf_metadata = {'target_conservative': 79000.0}
        
        # Act
        result = self.strategy._check_pnf_warning_zone_activation(
            pair, trade, current_rate, current_profit, pnf_metadata
        )
        
        # Assert
        self.assertFalse(result, "Should return False for non-eligible entry")
        
    def test_feature_disabled_hard_exit(self):
        """Test: WY_PNF_SHADOW_PROTECT=0 with eligible entry → hard exit"""
        # Arrange
        self.strategy.pnf_shadow_protect_enabled = False
        pair = 'BTC/USDT'
        trade = Mock()
        trade.enter_tag = 'wyckoff_axis_support'
        trade.is_short = False
        trade.open_date_utc = datetime(2026, 1, 1, 0, 0, 0)
        
        current_rate = 80000.0
        current_profit = 0.05
        pnf_metadata = {'target_conservative': 79000.0}
        
        # Act
        result = self.strategy._check_pnf_warning_zone_activation(
            pair, trade, current_rate, current_profit, pnf_metadata
        )
        
        # Assert
        self.assertFalse(result, "Should return False when feature is disabled")
        
    def test_metadata_correctly_stored(self):
        """Test: Metadata correctly stored (timestamp, profit, price, target_reached)"""
        # Arrange
        pair = 'BTC/USDT'
        trade = Mock()
        trade.enter_tag = 'wyckoff_sos_entry'
        trade.is_short = False
        trade.open_date_utc = datetime(2026, 1, 1, 0, 0, 0)
        
        current_rate = 80000.0
        current_profit = 0.05
        pnf_metadata = {'target_conservative': 79000.0}
        
        # Act
        result = self.strategy._check_pnf_warning_zone_activation(
            pair, trade, current_rate, current_profit, pnf_metadata
        )
        
        # Assert
        self.assertTrue(result)
        metadata_key = (pair, str(trade.open_date_utc))
        metadata = self.strategy._pnf_trades_metadata[metadata_key]
        
        self.assertEqual(metadata['pnf_activation_profit'], current_profit)
        self.assertEqual(metadata['pnf_activation_price'], current_rate)
        self.assertEqual(metadata['pnf_target_reached'], 'conservative')
        self.assertIn('pnf_activation_timestamp', metadata)
        
    def test_pnf_target_zero_skip_logic(self):
        """Test: PNF target = 0.0 → skip PNF logic"""
        # Arrange
        pair = 'BTC/USDT'
        trade = Mock()
        trade.enter_tag = 'wyckoff_axis_support'
        trade.is_short = False
        trade.open_date_utc = datetime(2026, 1, 1, 0, 0, 0)
        
        current_rate = 80000.0
        current_profit = 0.05
        pnf_metadata = {'target_conservative': 0.0}
        
        # Act
        result = self.strategy._check_pnf_warning_zone_activation(
            pair, trade, current_rate, current_profit, pnf_metadata
        )
        
        # Assert
        self.assertFalse(result, "Should skip PNF logic when target is 0.0")
        
    def test_short_trade_scenario(self):
        """Test: Short trade with PNF target reached"""
        # Arrange
        pair = 'BTC/USDT'
        trade = Mock()
        trade.enter_tag = 'wyckoff_axis_support'
        trade.is_short = True
        trade.open_date_utc = datetime(2026, 1, 1, 0, 0, 0)
        
        current_rate = 70000.0
        current_profit = 0.05
        pnf_metadata = {'target_short_conservative': 71000.0}
        
        # Act
        result = self.strategy._check_pnf_warning_zone_activation(
            pair, trade, current_rate, current_profit, pnf_metadata
        )
        
        # Assert
        self.assertTrue(result, "Should activate warning zone for short trade")


class TestPNFTimeout(unittest.TestCase):
    """Test suite for _check_pnf_timeout method"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.config = {'stake_currency': 'USDT'}
        self.strategy = WyckoffV7_TrueMethod(self.config)
        self.strategy.pnf_timeout_hours = 48.0
        
    def test_timeout_triggered_49h_ago(self):
        """Test: Warning zone activated 49h ago → timeout triggers"""
        # Arrange
        pair = 'BTC/USDT'
        trade = Mock()
        trade.is_short = False
        trade.open_rate = 75000.0
        trade.max_rate = 82000.0
        trade.open_date_utc = datetime(2026, 1, 1, 0, 0, 0)
        
        current_time = datetime(2026, 1, 3, 2, 0, 0)  # 50 hours later
        current_profit = 0.04
        activation_time = datetime(2026, 1, 1, 1, 0, 0)
        
        pnf_metadata = {
            'pnf_warning_zone_activated': True,
            'pnf_activation_timestamp': activation_time,
            'pnf_activation_profit': 0.05
        }
        
        # Act
        result = self.strategy._check_pnf_timeout(
            pair, trade, current_time, current_profit, pnf_metadata
        )
        
        # Assert
        self.assertEqual(result, 'pnf_timeout_exit', "Should trigger timeout after 49h")
        
    def test_timeout_not_triggered_47h_ago(self):
        """Test: Warning zone activated 47h ago → timeout does not trigger"""
        # Arrange
        pair = 'BTC/USDT'
        trade = Mock()
        trade.is_short = False
        trade.open_rate = 75000.0
        trade.max_rate = 82000.0
        trade.open_date_utc = datetime(2026, 1, 1, 0, 0, 0)
        
        current_time = datetime(2026, 1, 2, 23, 0, 0)  # 47 hours later
        current_profit = 0.04
        activation_time = datetime(2026, 1, 1, 0, 0, 0)
        
        pnf_metadata = {
            'pnf_warning_zone_activated': True,
            'pnf_activation_timestamp': activation_time,
            'pnf_activation_profit': 0.05
        }
        
        # Act
        result = self.strategy._check_pnf_timeout(
            pair, trade, current_time, current_profit, pnf_metadata
        )
        
        # Assert
        self.assertIsNone(result, "Should not trigger timeout before 48h")
        
    def test_warning_zone_not_activated_skip_check(self):
        """Test: Warning zone not activated → timeout check skipped"""
        # Arrange
        pair = 'BTC/USDT'
        trade = Mock()
        current_time = datetime(2026, 1, 3, 0, 0, 0)
        current_profit = 0.04
        
        pnf_metadata = {'pnf_warning_zone_activated': False}
        
        # Act
        result = self.strategy._check_pnf_timeout(
            pair, trade, current_time, current_profit, pnf_metadata
        )
        
        # Assert
        self.assertIsNone(result, "Should skip timeout check when warning zone not activated")
        
    def test_invalid_timestamp_skip_check(self):
        """Test: Invalid activation timestamp → skip timeout check, log warning"""
        # Arrange
        pair = 'BTC/USDT'
        trade = Mock()
        current_time = datetime(2026, 1, 3, 0, 0, 0)
        current_profit = 0.04
        
        pnf_metadata = {
            'pnf_warning_zone_activated': True,
            'pnf_activation_timestamp': None
        }
        
        # Act
        with patch('user_data.strategies.WyckoffV7_TrueMethod.logger') as mock_logger:
            result = self.strategy._check_pnf_timeout(
                pair, trade, current_time, current_profit, pnf_metadata
            )
        
        # Assert
        self.assertIsNone(result, "Should skip timeout check with invalid timestamp")
        mock_logger.warning.assert_called_once()


class TestShadowProtectPNFMode(unittest.TestCase):
    """Test suite for _check_shadow_protect_pnf_mode method"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.config = {'stake_currency': 'USDT'}
        self.strategy = WyckoffV7_TrueMethod(self.config)
        self.strategy._pnf_trades_metadata = {}
        
    def test_pnf_mode_triggers_with_valid_conditions(self):
        """Test: PNF mode with max_profit=5%, giveback=2.5%, current=2.5% → triggers"""
        # Arrange
        pair = 'BTC/USDT'
        trade = Mock()
        trade.is_short = False
        trade.open_rate = 75000.0
        trade.max_rate = 78750.0  # 5% profit
        trade.open_date_utc = datetime(2026, 1, 1, 0, 0, 0)
        
        current_profit = 0.025  # 2.5%
        pnf_metadata = {'pnf_warning_zone_activated': True}
        
        # Act
        result = self.strategy._check_shadow_protect_pnf_mode(
            pair, trade, current_profit, pnf_metadata
        )
        
        # Assert
        self.assertEqual(result, 'shadow_protect_exit', "Should trigger Shadow Protect in PNF mode")
        
    def test_pnf_mode_max_profit_too_low(self):
        """Test: PNF mode with max_profit=3%, giveback=2.5%, current=2.5% → does not trigger"""
        # Arrange
        pair = 'BTC/USDT'
        trade = Mock()
        trade.is_short = False
        trade.open_rate = 75000.0
        trade.max_rate = 77250.0  # 3% profit (below 4% threshold)
        trade.open_date_utc = datetime(2026, 1, 1, 0, 0, 0)
        
        current_profit = 0.005  # 0.5% (giveback = 2.5%)
        pnf_metadata = {'pnf_warning_zone_activated': True}
        
        # Act
        result = self.strategy._check_shadow_protect_pnf_mode(
            pair, trade, current_profit, pnf_metadata
        )
        
        # Assert
        self.assertIsNone(result, "Should not trigger when max_profit < 4%")
        
    def test_warning_zone_not_activated_skip_check(self):
        """Test: Warning zone not activated → Shadow Protect check skipped"""
        # Arrange
        pair = 'BTC/USDT'
        trade = Mock()
        trade.is_short = False
        trade.open_rate = 75000.0
        trade.max_rate = 78750.0
        
        current_profit = 0.025
        pnf_metadata = {'pnf_warning_zone_activated': False}
        
        # Act
        result = self.strategy._check_shadow_protect_pnf_mode(
            pair, trade, current_profit, pnf_metadata
        )
        
        # Assert
        self.assertIsNone(result, "Should skip Shadow Protect check when warning zone not activated")
        
    def test_max_rate_none_skip_check(self):
        """Test: trade.max_rate is None → skip Shadow Protect check, log warning"""
        # Arrange
        pair = 'BTC/USDT'
        trade = Mock()
        trade.is_short = False
        trade.open_rate = 75000.0
        trade.max_rate = None
        
        current_profit = 0.025
        pnf_metadata = {'pnf_warning_zone_activated': True}
        
        # Act
        with patch('user_data.strategies.WyckoffV7_TrueMethod.logger') as mock_logger:
            result = self.strategy._check_shadow_protect_pnf_mode(
                pair, trade, current_profit, pnf_metadata
            )
        
        # Assert
        self.assertIsNone(result, "Should skip Shadow Protect check when max_rate is None")
        mock_logger.warning.assert_called_once()


class TestMetadataBuilderDualMode(unittest.TestCase):
    """Test suite for _build_axis_shadow_profit_protect_metadata with pnf_mode parameter"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.config = {'stake_currency': 'USDT'}
        self.strategy = WyckoffV7_TrueMethod(self.config)
        
    def test_pnf_mode_true_uses_pnf_thresholds(self):
        """Test: pnf_mode=True uses thresholds 4%/2%/2% and adds 'shadow_protect_mode': 'pnf'"""
        # Arrange
        trade = Mock()
        trade.is_short = False
        trade.enter_tag = 'wyckoff_axis_support'
        
        current_profit = 0.025
        max_profit_seen = 0.05
        
        # Act
        result = self.strategy._build_axis_shadow_profit_protect_metadata(
            trade, current_profit, max_profit_seen, pnf_mode=True
        )
        
        # Assert
        self.assertEqual(result['shadow_protect_mode'], 'pnf')
        self.assertIn('max>=0.04', result['shadow_protect_rule'])
        self.assertIn('giveback>=0.02', result['shadow_protect_rule'])
        self.assertIn('current>=0.02', result['shadow_protect_rule'])
        
    def test_pnf_mode_false_uses_original_thresholds(self):
        """Test: pnf_mode=False uses thresholds 6%/2.5%/3% and adds 'shadow_protect_mode': 'original'"""
        # Arrange
        trade = Mock()
        trade.is_short = False
        trade.enter_tag = 'wyckoff_axis_support'
        
        current_profit = 0.035
        max_profit_seen = 0.07
        
        # Act
        result = self.strategy._build_axis_shadow_profit_protect_metadata(
            trade, current_profit, max_profit_seen, pnf_mode=False
        )
        
        # Assert
        self.assertEqual(result['shadow_protect_mode'], 'original')
        self.assertIn('max>=0.06', result['shadow_protect_rule'])
        self.assertIn('giveback>=0.025', result['shadow_protect_rule'])
        self.assertIn('current>=0.03', result['shadow_protect_rule'])
        
    def test_default_parameter_uses_original_thresholds(self):
        """Test: default parameter (pnf_mode not specified) uses original thresholds"""
        # Arrange
        trade = Mock()
        trade.is_short = False
        trade.enter_tag = 'wyckoff_axis_support'
        
        current_profit = 0.035
        max_profit_seen = 0.07
        
        # Act
        result = self.strategy._build_axis_shadow_profit_protect_metadata(
            trade, current_profit, max_profit_seen
        )
        
        # Assert
        self.assertEqual(result['shadow_protect_mode'], 'original')


if __name__ == '__main__':
    unittest.main()
