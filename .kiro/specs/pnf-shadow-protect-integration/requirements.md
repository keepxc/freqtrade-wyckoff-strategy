# Requirements Document: PNF Shadow Protect Integration

## Introduction

This feature transforms the PNF (Point & Figure) target from a "hard exit line" into a "warning zone" that activates profit protection mechanisms. Currently, when price reaches the PNF target, the trade exits immediately via a hard return. The new behavior will allow market structure to determine the optimal exit point by activating Shadow Protect monitoring when the PNF target is reached.

The goal is to capture additional profit when momentum continues beyond the PNF target, while protecting against givebacks through intelligent exit monitoring.

## Glossary

- **PNF_Target**: Point & Figure calculated price target based on Trading Range (TR) boundaries and box size
- **Shadow_Protect**: Profit protection mechanism that monitors max profit and giveback to detect optimal exit points
- **Profit_Protect_Exit**: Exit logic that triggers on volume shrinkage and bearish bars after minimum profit threshold
- **Hard_Exit**: Immediate trade exit with no additional monitoring (current PNF behavior)
- **Warning_Zone**: Price level that activates monitoring rather than forcing immediate exit
- **Giveback**: The difference between maximum profit achieved and current profit (max_profit - current_profit)
- **Entry_Tag**: Trade classification identifier (e.g., 'wyckoff_axis_support', 'wyckoff_sos_entry')
- **Exit_Priority**: Numerical ranking determining which exit logic executes first (lower number = higher priority)
- **TR_Boundaries**: Trading Range boundaries (high/low) used for PNF target calculation
- **Strategy**: The WyckoffV7_TrueMethod trading strategy implementation

## Requirements

### Requirement 1: PNF Target Warning Zone Activation

**User Story:** As a trader, I want the PNF target to activate profit protection instead of forcing immediate exit, so that I can capture additional profit when momentum continues.

#### Acceptance Criteria

1. WHEN current_rate >= PNF_Target AND trade is long, THE Strategy SHALL activate Shadow_Protect monitoring instead of returning 'pnf_target_1'
2. WHEN current_rate <= PNF_Target_Short AND trade is short, THE Strategy SHALL activate Shadow_Protect monitoring instead of returning 'pnf_target_short_1'
3. THE Strategy SHALL record PNF_Target activation timestamp in trade metadata
4. THE Strategy SHALL log PNF_Target warning zone activation with current profit and target price
5. FOR ALL trades where PNF_Target is reached, activation SHALL occur exactly once per trade

### Requirement 2: Shadow Protect Activation Conditions

**User Story:** As a trader, I want Shadow Protect to activate with appropriate thresholds when PNF target is reached, so that profit protection is neither too aggressive nor too loose.

#### Acceptance Criteria

1. WHEN PNF_Target warning zone is activated, THE Strategy SHALL enable Shadow_Protect with modified thresholds
2. THE Shadow_Protect SHALL require minimum current_profit >= 2.0% (reduced from 3.0%)
3. THE Shadow_Protect SHALL require minimum max_profit >= 4.0% (reduced from 6.0%)
4. THE Shadow_Protect SHALL require minimum giveback >= 2.0% (reduced from 2.5%)
5. THE Strategy SHALL apply these thresholds only to trades where PNF_Target activated Shadow_Protect
6. THE Strategy SHALL maintain original thresholds (6.0%, 2.5%, 3.0%) for non-PNF-activated Shadow_Protect

### Requirement 3: Entry Type Scope

**User Story:** As a trader, I want PNF Shadow Protect integration to apply to appropriate entry types, so that the mechanism is used where it provides value.

#### Acceptance Criteria

1. THE Strategy SHALL apply PNF Shadow Protect integration to 'wyckoff_axis_support' entries
2. THE Strategy SHALL apply PNF Shadow Protect integration to 'wyckoff_sos_entry' entries
3. WHERE entry_tag is NOT 'wyckoff_axis_support' OR 'wyckoff_sos_entry', THE Strategy SHALL maintain current hard exit behavior at PNF_Target
4. THE Strategy SHALL log entry_tag when PNF_Target warning zone is activated
5. THE Strategy SHALL record entry_tag eligibility in trade metadata

### Requirement 4: Exit Priority Reordering

**User Story:** As a trader, I want Shadow Protect to execute before down_wave exit after PNF target is reached, so that profit protection takes precedence over trend-following exits.

#### Acceptance Criteria

1. WHEN PNF_Target warning zone is NOT activated, THE Strategy SHALL maintain current exit priority order (PNF at 3.8, down_wave at 4.0)
2. WHEN PNF_Target warning zone IS activated, THE Strategy SHALL evaluate Shadow_Protect before down_wave exit
3. THE Strategy SHALL evaluate Shadow_Protect after stop_loss and trailing_stop exits (priorities 1-2)
4. THE Strategy SHALL evaluate down_wave exit after Shadow_Protect when PNF warning zone is active
5. THE Strategy SHALL maintain exit priority ordering consistency across all trade evaluations

### Requirement 5: Maximum Hold Time After PNF Target

**User Story:** As a trader, I want a maximum hold time after PNF target is reached, so that trades don't remain open indefinitely waiting for Shadow Protect conditions.

#### Acceptance Criteria

1. WHEN PNF_Target warning zone is activated, THE Strategy SHALL start a hold timer
2. WHEN hold time exceeds 48 hours after PNF_Target activation AND Shadow_Protect has not triggered, THE Strategy SHALL exit with 'pnf_timeout_exit'
3. WHEN Shadow_Protect triggers before timeout, THE Strategy SHALL exit with 'shadow_protect_exit' and ignore timeout
4. THE Strategy SHALL log timeout exits with activation timestamp, current profit, and max profit achieved
5. THE Strategy SHALL record timeout duration in trade metadata

### Requirement 6: Profit Protect Exit Integration

**User Story:** As a trader, I want Profit_Protect_Exit to remain available as a fallback mechanism, so that volume-based exit signals are not ignored.

#### Acceptance Criteria

1. WHEN PNF_Target warning zone is activated, THE Strategy SHALL keep Profit_Protect_Exit logic available
2. THE Profit_Protect_Exit SHALL evaluate after Shadow_Protect in exit priority order
3. WHEN Profit_Protect_Exit conditions are met (profit >= 2.5%, hold >= 12h, volume shrink, bearish bar), THE Strategy SHALL exit with 'profit_protect_exit'
4. THE Strategy SHALL NOT require WY_PROFIT_PROTECT environment variable to be set when PNF_Target warning zone is active
5. THE Strategy SHALL maintain WY_PROFIT_PROTECT=0 default behavior for trades where PNF_Target is not reached

### Requirement 7: Metadata and Forensics

**User Story:** As a strategy analyst, I want comprehensive metadata about PNF Shadow Protect behavior, so that I can analyze effectiveness and tune parameters.

#### Acceptance Criteria

1. THE Strategy SHALL record 'pnf_warning_zone_activated' boolean in trade metadata
2. THE Strategy SHALL record 'pnf_activation_timestamp' when warning zone is activated
3. THE Strategy SHALL record 'pnf_activation_profit' (current profit at activation time)
4. THE Strategy SHALL record 'pnf_activation_price' (current rate at activation time)
5. THE Strategy SHALL record 'shadow_protect_pnf_mode' boolean indicating PNF-activated Shadow Protect
6. THE Strategy SHALL record 'max_profit_after_pnf' (maximum profit achieved after PNF activation)
7. THE Strategy SHALL record 'exit_reason_detail' explaining which mechanism triggered the exit
8. THE Strategy SHALL include all PNF Shadow Protect metadata in custom_exit return metadata

### Requirement 8: Interaction with Down Wave Exit

**User Story:** As a trader, I want clear rules for how Shadow Protect interacts with down_wave exit, so that exit logic is predictable and consistent.

#### Acceptance Criteria

1. WHEN PNF_Target warning zone is NOT activated, THE Strategy SHALL allow down_wave exit to execute at priority 4.0
2. WHEN PNF_Target warning zone IS activated AND Shadow_Protect has not triggered, THE Strategy SHALL evaluate Shadow_Protect before down_wave
3. WHEN Shadow_Protect conditions are met, THE Strategy SHALL exit with 'shadow_protect_exit' and skip down_wave evaluation
4. WHEN Shadow_Protect conditions are NOT met, THE Strategy SHALL continue to down_wave evaluation
5. WHEN timeout occurs (48h after PNF activation), THE Strategy SHALL exit with 'pnf_timeout_exit' and skip down_wave evaluation

### Requirement 9: Logging and Observability

**User Story:** As a strategy operator, I want detailed logging of PNF Shadow Protect behavior, so that I can monitor and debug the mechanism in real-time.

#### Acceptance Criteria

1. WHEN PNF_Target warning zone is activated, THE Strategy SHALL log activation with pair, current_rate, target, current_profit, entry_tag
2. WHEN Shadow_Protect evaluates after PNF activation, THE Strategy SHALL log evaluation with max_profit, giveback, current_profit, thresholds
3. WHEN Shadow_Protect triggers exit, THE Strategy SHALL log trigger with all condition values and profit metrics
4. WHEN timeout exit occurs, THE Strategy SHALL log timeout with hold duration, max_profit, current_profit
5. WHEN Profit_Protect_Exit triggers after PNF activation, THE Strategy SHALL log trigger with volume metrics and profit
6. THE Strategy SHALL use INFO level for activation and exit events
7. THE Strategy SHALL use DEBUG level for evaluation checks that don't trigger exits

### Requirement 10: Backward Compatibility

**User Story:** As a strategy maintainer, I want the PNF Shadow Protect integration to be backward compatible, so that existing behavior is preserved for non-eligible trades.

#### Acceptance Criteria

1. WHEN entry_tag is NOT 'wyckoff_axis_support' OR 'wyckoff_sos_entry', THE Strategy SHALL maintain current hard exit behavior
2. WHEN PNF_Target is 0.0 or invalid, THE Strategy SHALL skip PNF exit logic entirely (existing behavior)
3. WHEN WY_PROFIT_PROTECT environment variable is explicitly set to '1', THE Strategy SHALL honor that setting regardless of PNF activation
4. THE Strategy SHALL maintain all existing exit priorities for non-PNF-activated trades
5. THE Strategy SHALL maintain all existing metadata fields for backward compatibility

### Requirement 11: A/B Test Compatibility

**User Story:** As a strategy researcher, I want the ability to A/B test PNF Shadow Protect integration, so that I can validate effectiveness before full deployment.

#### Acceptance Criteria

1. THE Strategy SHALL support WY_PNF_SHADOW_PROTECT environment variable ('0' or '1')
2. WHEN WY_PNF_SHADOW_PROTECT='0', THE Strategy SHALL maintain current hard exit behavior at PNF_Target
3. WHEN WY_PNF_SHADOW_PROTECT='1', THE Strategy SHALL activate Shadow_Protect at PNF_Target
4. THE Strategy SHALL default to WY_PNF_SHADOW_PROTECT='1' (new behavior enabled)
5. THE Strategy SHALL log the WY_PNF_SHADOW_PROTECT setting at strategy initialization

## Parser and Serializer Requirements

This feature does not introduce new parsers or serializers. All data structures use existing Python dictionaries and JSON serialization provided by Freqtrade's metadata system.

## Notes

### Design Considerations

1. **Threshold Reduction Rationale**: The reduced Shadow_Protect thresholds (4.0%, 2.0%, 2.0%) when activated by PNF target are designed to be more sensitive than the original forensic-only thresholds (6.0%, 2.5%, 3.0%), since PNF target already indicates a significant price level has been reached.

2. **Entry Type Scope**: Limited to 'wyckoff_axis_support' and 'wyckoff_sos_entry' because these are the primary entry types where PNF targets are most reliable and where the A/B test data showed profit_protect had negative impact.

3. **48-Hour Timeout**: Chosen to prevent indefinite holds while allowing sufficient time for market structure to develop. This is 4x the current profit_protect minimum hold time (12h).

4. **Exit Priority**: Shadow_Protect must execute before down_wave (priority 4.0) to prevent trend-following exits from overriding profit protection.

5. **Backward Compatibility**: Critical for safe deployment. Non-eligible trades maintain exact current behavior.

### Related Specs

- `.kiro/specs/pnf-target-bug-fix.md` - Fixed PNF target calculation bug (completed)

### Implementation Files

- `user_data/strategies/WyckoffV7_TrueMethod.py` - Main strategy file
  - Lines 5072-5100: `_build_axis_shadow_profit_protect_metadata` (Shadow Protect logic)
  - Lines 5203-5220: `profit_protect_exit` logic
  - Lines 5284-5288: PNF target hard exit logic (to be modified)
- `wyckoff/pnf_target_calculator.py` - PNF target calculation (no changes needed)

### Success Metrics

The effectiveness of this feature will be measured by:
1. **Profit Capture**: Increase in average profit per trade for PNF-activated exits
2. **Exit Quality**: Reduction in premature exits (exiting before momentum exhaustion)
3. **Giveback Protection**: Reduction in profit giveback after PNF target is reached
4. **Timeout Rate**: Percentage of trades that hit 48h timeout (should be < 10%)
5. **Comparative Performance**: Backtest comparison of WY_PNF_SHADOW_PROTECT='0' vs '1'
