# P&F Target Bug Fix Spec

## Problem Statement

P&F (Point & Figure) targets are being saved as 0.0 in the metadata chain at entry time, causing the P&F exit logic to never trigger. This affects all trades, particularly `wyckoff_axis_support` entries.

### Evidence
- Backtest logs show: `metadata_found=True, tgt_cons=0.00, tgt_std=0.00`
- Trades reach 3-4% profit but P&F exit never triggers
- Trade #9 in dry-run exited at $77,910 instead of waiting for $78,000+ target

### Root Cause
P&F target calculation returns 0.0 when:
1. No SC (Selling Climax) anchor point identified (`climatic_type != 'SC'`)
2. Phase Context TR boundaries invalid (`phase_tr_valid=False`)
3. 60-bar fallback window insufficient (not enough price movement)

## Requirements

### R1: Ensure Valid TR Boundaries at Entry
- [ ] P&F targets must be > 0 for all entries
- [ ] If TR boundaries cannot be determined, entry should be blocked
- [ ] Add validation in `confirm_trade_entry` to reject entries with invalid P&F targets

### R2: Improve TR Boundary Detection
- [ ] Enhance fallback logic when SC anchor is missing
- [ ] Use longer lookback window (e.g., 120 bars instead of 60)
- [ ] Consider using recent swing high/low as TR boundaries

### R3: Add Comprehensive Logging
- [ ] Log P&F target values at entry time (in `confirm_trade_entry`)
- [ ] Log TR boundary sources (SC anchor / Phase Context / fallback)
- [ ] Log why targets are 0 if that occurs

## Design

### Option A: Block Entries with Invalid Targets (Recommended)
**Pros:**
- Prevents systematic bug from affecting trades
- Forces proper TR identification before entry
- Clean and safe

**Cons:**
- May reduce entry opportunities
- Requires robust TR detection

**Implementation:**
```python
# In confirm_trade_entry()
if pnf_meta['target_conservative'] <= 0:
    logger.warning(f'❌ Entry blocked: Invalid P&F target for {pair}')
    return False
```

### Option B: Use Dynamic Fallback Targets
**Pros:**
- Allows entries even without perfect TR boundaries
- More flexible

**Cons:**
- Targets may be less accurate
- Could lead to premature exits

**Implementation:**
```python
# In PNFTargetCalculator
if target_conservative <= 0:
    # Fallback: use ATR-based target
    target_conservative = current_price + (atr * 3.0)
```

## Tasks

### Task 1: Add Entry Validation in confirm_trade_entry (✅ COMPLETED)
- [x] 1.1: Add P&F target validation in `confirm_trade_entry`
- [x] 1.2: Block entries when `target_conservative <= 0`
- [x] 1.3: Add WARNING log explaining why entry was blocked

**Status**: Implemented but only works in live/dry-run mode, not in backtest mode.

### Task 1B: Add Entry Validation in populate_entry_trend (✅ COMPLETED)
- [x] 1B.1: Add P&F target validation before `final_entry` assignment
- [x] 1B.2: Filter out long entries when `target_conservative <= 0`
- [x] 1B.3: Filter out short entries when `target_short_conservative <= 0`
- [x] 1B.4: Add WARNING log showing how many entries were blocked

**Status**: Implemented in `populate_entry_trend` (lines 2895-2920 for long, lines 3299-3316 for short).

### Task 2: Enhance TR Boundary Detection
- [ ] 2.1: Increase fallback lookback from 60 to 120 bars
- [ ] 2.2: Add swing high/low detection as secondary fallback
- [ ] 2.3: Test with 20260419-20260502 data

### Task 3: Improve Logging
- [ ] 3.1: Add detailed P&F logging in `confirm_trade_entry`
- [ ] 3.2: Log TR boundary source (SC / Phase / fallback / swing)
- [ ] 3.3: Change log level to WARNING for visibility

### Task 4: Backtest Validation
- [ ] 4.1: Run backtest 20260419-20260502 with fixes
- [ ] 4.2: Verify all entries have valid P&F targets (> 0)
- [ ] 4.3: Verify Trade #9-like scenarios exit at correct target
- [ ] 4.4: Run full backtest 20220101-20260502 to ensure no regression

## Acceptance Criteria

1. ✅ All backtest entries have `target_conservative > 0`
2. ✅ No entries are allowed with invalid P&F targets
3. ✅ Backtest logs show P&F target values at entry time
4. ✅ Trade #9-like scenarios exit at P&F target (not down_wave)
5. ✅ Full backtest performance remains stable (±2% tolerance)

## Notes

- This is a P0 bug affecting all trades
- Fix should be conservative (block invalid entries rather than use bad targets)
- Consider adding this validation to dry-run and live trading as well

## Implementation Status (2026-05-02)

### Discovery
- ✅ P&F validation logic已在 `confirm_trade_entry` 中实现（第 5371-5412 行）
- ❌ 回测模式下 `confirm_trade_entry` 不被调用（Freqtrade 回测引擎设计）
- ❌ 因此验证逻辑在回测中不生效，导致无效目标的交易仍然执行

### Root Cause
Freqtrade 回测引擎为了性能优化，跳过了某些只在实盘/模拟盘中使用的回调方法，包括：
- `confirm_trade_entry`
- `confirm_trade_exit`
- 其他确认类回调

这意味着任何在这些方法中的验证逻辑在回测中都不会生效。

### Solution
需要在 `populate_entry_trend` 中添加 P&F 目标验证作为备用方案：
1. 在设置 `final_entry` 之前，添加 P&F 目标验证
2. 过滤掉 `target_conservative <= 0` 的多头信号
3. 过滤掉 `target_short_conservative <= 0` 的空头信号
4. 保留 `confirm_trade_entry` 中的验证逻辑（用于实盘/模拟盘）
