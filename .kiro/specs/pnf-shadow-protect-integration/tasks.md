# Implementation Plan: PNF Shadow Protect Integration

## Overview

This implementation transforms the PNF target from a hard exit into a warning zone that activates Shadow Protect monitoring with relaxed thresholds (4%/2%/2%). The feature includes a 48-hour timeout mechanism and maintains backward compatibility for non-eligible entry types.

**Key Components:**
- Instance variable initialization for feature flag
- Three new helper methods for warning zone activation, timeout check, and Shadow Protect PNF mode
- Modified Shadow Protect metadata builder with dual-mode support
- Exit priority modifications at 3.8, 3.85 (new), and 3.9 (new)
- Comprehensive unit and integration tests
- Backtest validation with A/B comparison

## Tasks

- [x] 1. Set up instance variables and initialization
  - Add `self.pnf_shadow_protect_enabled` instance variable in `__init__` method
  - Read from `WY_PNF_SHADOW_PROTECT` environment variable (default='1')
  - Add `self.pnf_timeout_hours` instance variable (default=48.0)
  - Add initialization logging at INFO level with Chinese labels
  - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5_

- [x] 2. Implement PNF warning zone activation helper
  - [x] 2.1 Create `_check_pnf_warning_zone_activation` method
    - Implement eligibility checks (entry_tag, WY_PNF_SHADOW_PROTECT)
    - Implement PNF target reached checks (long and short)
    - Store activation metadata in `_pnf_trades_metadata`
    - Add activation logging with pair, rate, target, profit, entry_tag
    - Return True for warning zone activation, False for hard exit
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 3.1, 3.2, 3.3, 3.4, 3.5, 7.1, 7.2, 7.3, 7.4, 9.1_

  - [x] 2.2 Write unit tests for PNF warning zone activation
    - Test eligible entry type with PNF target reached → activates warning zone
    - Test non-eligible entry type with PNF target reached → hard exit
    - Test WY_PNF_SHADOW_PROTECT=0 with eligible entry → hard exit
    - Test metadata correctly stored (timestamp, profit, price, target_reached)
    - Test PNF target = 0.0 → skip PNF logic
    - Test both long and short trade scenarios
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 3.1, 3.2, 3.3, 10.1, 10.2_

- [x] 3. Implement timeout check helper
  - [x] 3.1 Create `_check_pnf_timeout` method
    - Check if `pnf_warning_zone_activated` is True
    - Calculate elapsed time since `pnf_activation_timestamp`
    - Return 'pnf_timeout_exit' if >= 48 hours
    - Calculate and record `max_profit_after_pnf` in metadata
    - Add timeout logging with hold duration, activation profit, current profit, max profit
    - Handle invalid timestamp gracefully (return None, log warning)
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 7.6, 9.4_

  - [x] 3.2 Write unit tests for timeout mechanism
    - Test warning zone activated 49h ago → timeout triggers
    - Test warning zone activated 47h ago → timeout does not trigger
    - Test warning zone not activated → timeout check skipped
    - Test timeout exit includes correct metadata (activation_profit, max_profit_after_pnf)
    - Test invalid activation timestamp → skip timeout check, log warning
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_

- [x] 4. Implement Shadow Protect PNF mode helper
  - [x] 4.1 Create `_check_shadow_protect_pnf_mode` method
    - Check if `pnf_warning_zone_activated` is True
    - Get max_profit from `trade.max_rate`
    - Calculate max_profit_ratio and giveback
    - Apply PNF-mode thresholds (max_profit >= 4%, giveback >= 2%, current_profit >= 2%)
    - Return 'shadow_protect_exit' if all conditions met
    - Update metadata with `max_profit_after_pnf` and `shadow_protect_pnf_mode=True`
    - Add evaluation logging (DEBUG) and trigger logging (INFO)
    - Handle trade.max_rate = None gracefully (return None, log warning)
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 7.5, 9.2, 9.3_

  - [x] 4.2 Write unit tests for Shadow Protect PNF mode
    - Test PNF mode with max_profit=5%, giveback=2.5%, current=2.5% → triggers
    - Test PNF mode with max_profit=3%, giveback=2.5%, current=2.5% → does not trigger (max too low)
    - Test original mode with max_profit=5%, giveback=2.5%, current=2.5% → does not trigger (wrong thresholds)
    - Test warning zone not activated → Shadow Protect check skipped
    - Test trade.max_rate is None → skip Shadow Protect check, log warning
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_

- [x] 5. Modify Shadow Protect metadata builder
  - [x] 5.1 Add `pnf_mode` parameter to `_build_axis_shadow_profit_protect_metadata`
    - Add `pnf_mode: bool = False` parameter to method signature
    - Implement conditional threshold selection (4%/2%/2% for PNF, 6%/2.5%/3% for original)
    - Add `'shadow_protect_mode': 'pnf'` or `'original'` to returned metadata
    - Maintain backward compatibility (default pnf_mode=False)
    - _Requirements: 2.5, 2.6, 7.5, 10.4_

  - [x] 5.2 Write unit tests for metadata builder dual-mode
    - Test pnf_mode=True uses thresholds 4%/2%/2% and adds 'shadow_protect_mode': 'pnf'
    - Test pnf_mode=False uses thresholds 6%/2.5%/3% and adds 'shadow_protect_mode': 'original'
    - Test default parameter (pnf_mode not specified) uses original thresholds
    - _Requirements: 2.5, 2.6, 10.4_

- [x] 6. Checkpoint - Ensure all helper methods and tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Modify custom_exit priority 3.8 (PNF target check)
  - [x] 7.1 Replace hard exit with warning zone activation call
    - Replace `return 'pnf_target_1'` with call to `_check_pnf_warning_zone_activation`
    - If activation returns True, continue to next priority (no return)
    - If activation returns False, return 'pnf_target_1' (hard exit)
    - Handle both long and short trade scenarios
    - Add try-except wrapper with fallback to hard exit on error
    - _Requirements: 1.1, 1.2, 3.3, 10.1_

  - [x] 7.2 Write unit tests for priority 3.8 modification
    - Test eligible entry with PNF target reached → continues to next priority
    - Test non-eligible entry with PNF target reached → returns 'pnf_target_1'
    - Test exception during activation check → returns 'pnf_target_1' (fail-safe)
    - _Requirements: 1.1, 1.2, 3.3, 10.1_

- [x] 8. Add custom_exit priority 3.85 (timeout check)
  - [x] 8.1 Add timeout check after priority 3.8
    - Check if `pnf_warning_zone_activated` in metadata
    - Call `_check_pnf_timeout` method
    - If timeout exit returned, return 'pnf_timeout_exit'
    - If None returned, continue to priority 3.9
    - _Requirements: 5.1, 5.2, 5.3, 8.5_

  - [x] 8.2 Write unit tests for priority 3.85
    - Test timeout triggered → returns 'pnf_timeout_exit', skips down_wave
    - Test timeout not triggered → continues to priority 3.9
    - Test warning zone not activated → skips timeout check
    - _Requirements: 5.1, 5.2, 5.3, 8.5_

- [x] 9. Add custom_exit priority 3.9 (Shadow Protect PNF mode)
  - [x] 9.1 Add Shadow Protect PNF mode check after priority 3.85
    - Check if `pnf_warning_zone_activated` in metadata
    - Call `_check_shadow_protect_pnf_mode` method
    - If shadow_protect_exit returned, return 'shadow_protect_exit'
    - If None returned, continue to priority 4.0 (down_wave)
    - _Requirements: 2.1, 4.2, 4.3, 8.3_

  - [x] 9.2 Write unit tests for priority 3.9
    - Test Shadow Protect triggered → returns 'shadow_protect_exit', skips down_wave
    - Test Shadow Protect not triggered → continues to priority 4.0
    - Test warning zone not activated → skips Shadow Protect check
    - _Requirements: 2.1, 4.2, 4.3, 8.3_

- [x] 10. Checkpoint - Ensure all exit priority modifications and tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 11. Write integration tests for full trade lifecycle
  - [x] 11.1 Test Scenario 1: PNF activation → Shadow Protect triggers
    - Create mock trade with eligible entry_tag
    - Simulate PNF target reached → verify warning zone activated
    - Simulate profit conditions met → verify Shadow Protect triggers
    - Verify metadata stored correctly throughout lifecycle
    - _Requirements: 1.1, 2.1, 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8_

  - [x] 11.2 Test Scenario 2: PNF activation → 48h timeout triggers
    - Create mock trade with eligible entry_tag
    - Simulate PNF target reached → verify warning zone activated
    - Simulate 48h elapsed → verify timeout triggers
    - Verify max_profit_after_pnf recorded correctly
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 7.6_

  - [x] 11.3 Test Scenario 3: PNF activation → down_wave triggers
    - Create mock trade with eligible entry_tag
    - Simulate PNF target reached → verify warning zone activated
    - Simulate Shadow Protect not triggered → verify down_wave evaluates
    - Verify exit priority ordering (Shadow Protect before down_wave)
    - _Requirements: 4.2, 4.4, 8.1, 8.2, 8.4_

  - [x] 11.4 Test Scenario 4: Non-eligible entry → hard exit
    - Create mock trade with non-eligible entry_tag
    - Simulate PNF target reached → verify hard exit with 'pnf_target_1'
    - Verify warning zone not activated
    - _Requirements: 3.3, 10.1_

- [x] 12. Write integration tests for metadata persistence
  - [x] 12.1 Test metadata survives across multiple custom_exit calls
    - Simulate multiple candles after PNF activation
    - Verify metadata persists in `_pnf_trades_metadata`
    - _Requirements: 7.1, 7.2, 7.3, 7.4_

  - [x] 12.2 Test metadata key format works correctly
    - Verify `(pair, open_date_str)` key format
    - Test retrieval with correct and incorrect keys
    - _Requirements: 7.1_

- [x] 13. Write integration tests for A/B test compatibility
  - [x] 13.1 Test WY_PNF_SHADOW_PROTECT=0 produces baseline behavior
    - Set environment variable to '0'
    - Verify hard exit at PNF target for all entry types
    - Verify no warning zone activation
    - _Requirements: 11.1, 11.2_

  - [x] 13.2 Test WY_PNF_SHADOW_PROTECT=1 activates new behavior
    - Set environment variable to '1'
    - Verify warning zone activation for eligible entries
    - Verify Shadow Protect with PNF thresholds
    - _Requirements: 11.3_

  - [x] 13.3 Test environment variable checked at initialization
    - Verify `pnf_shadow_protect_enabled` set once at init
    - Verify consistent behavior across trade lifecycle
    - _Requirements: 11.5_

- [x] 14. Write integration tests for Profit Protect Exit interaction
  - [x] 14.1 Test Profit_Protect_Exit evaluates after Shadow Protect
    - Simulate PNF activation
    - Verify Shadow Protect evaluates first
    - Verify Profit_Protect_Exit can trigger if Shadow Protect doesn't
    - _Requirements: 6.1, 6.2, 6.3_

  - [x] 14.2 Test WY_PROFIT_PROTECT=1 honored regardless of PNF
    - Set WY_PROFIT_PROTECT=1 explicitly
    - Verify Profit_Protect_Exit available for all trades
    - _Requirements: 6.4, 10.3_

  - [x] 14.3 Test WY_PROFIT_PROTECT=0 default maintained
    - Verify default behavior for non-PNF-activated trades
    - _Requirements: 6.5_

- [x] 15. Checkpoint - Ensure all integration tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 16. Run baseline backtest validation
  - [x] 16.1 Run backtest with WY_PNF_SHADOW_PROTECT=0
    - Execute backtest command for period 2022-01-01 to 2026-05-02
    - Save results to `backtest_pnf_baseline.json`
    - _Requirements: 11.2_

  - [x] 16.2 Run backtest with WY_PNF_SHADOW_PROTECT=1
    - Execute backtest command for period 2022-01-01 to 2026-05-02
    - Save results to `backtest_pnf_shadow_protect.json`
    - _Requirements: 11.3_

- [ ] 17. Create backtest analysis script
  - [x] 17.1 Create `analyze_pnf_shadow_protect.py` script
    - Parse both backtest JSON outputs
    - Filter trades with PNF activation
    - Calculate metrics: timeout rate, profit improvement, giveback reduction, exit quality score
    - Generate comparison report with success metrics
    - _Requirements: Success Metrics 1, 2, 3, 4, 5_

  - [ ] 17.2 Run analysis and generate report
    - Execute analysis script on backtest results
    - Validate success criteria: profit capture +15%, giveback -30%, timeout <10%, exit quality >70%, total return >5%
    - Save report to `backtest_pnf_shadow_protect_analysis.md`
    - _Requirements: Success Metrics 1, 2, 3, 4, 5_

- [ ] 18. Update documentation
  - [ ] 18.1 Update TASKS_with_checklist.md
    - Document new exit priorities (3.85, 3.9)
    - Document PNF warning zone behavior
    - Document WY_PNF_SHADOW_PROTECT environment variable
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5_

  - [ ] 18.2 Create daily summary document
    - Document implementation completion
    - Include backtest validation results
    - Include success metrics achieved
    - Save to `DAILY_SUMMARY_PNF_SHADOW_PROTECT.md`

- [ ] 19. Final checkpoint - Verify all requirements met
  - Run syntax check: `python -m py_compile user_data/strategies/WyckoffV7_TrueMethod.py`
  - Verify all unit tests pass
  - Verify all integration tests pass
  - Verify backtest validation shows improvement
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation at reasonable breaks
- Unit tests validate individual helper methods with concrete examples
- Integration tests validate full trade lifecycle scenarios
- Backtest validation confirms real-world effectiveness with historical data
- All code examples use Python (matching the existing strategy implementation)
- Chinese labels maintained in logging for consistency with existing codebase

## Implementation Context

**File**: `user_data/strategies/WyckoffV7_TrueMethod.py`

**Key Locations**:
- `__init__` method (~line 700): Instance variable initialization
- `_custom_exit_impl` method (~line 5270): Exit priority modifications
- `_build_axis_shadow_profit_protect_metadata` (~line 5072): Metadata builder modification
- New helper methods: Add after `_build_axis_shadow_profit_protect_metadata`

**Environment Variables**:
- `WY_PNF_SHADOW_PROTECT`: '0' (disabled) or '1' (enabled, default)
- `WY_PROFIT_PROTECT`: Existing variable, honored regardless of PNF activation

**Exit Priority Order** (after implementation):
```
3.8  pnf_warning_zone_activation (CONTINUE, not exit)
3.85 pnf_timeout_exit (NEW - 48h timeout)
3.9  shadow_protect_exit (NEW - PNF mode with 4%/2%/2% thresholds)
4.0  wyckoff_down_wave (existing)
```
