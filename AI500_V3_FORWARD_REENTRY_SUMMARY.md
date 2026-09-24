# AI500 V3 Forward OOS Re-entry Summary

**生成时间**: 2026-09-24T16:36:12.608413+00:00  
**数据范围**: score 2026-09-05 06:32:20.099449+00:00 → 2026-09-24 05:30:54.870987+00:00 | 最新平仓 2026-09-21 09:07:32.674000+00:00  
**OOS 起点（冻结）**: 2026-09-24 05:33:00+00:00 —— 此前交易为假设生成样本，不计入 P9 门槛  
**episode 主口径**: 接口信号标识 (symbol, start_time)

## A. 分段累计（已平仓，段间不得混池）

|                     |   trades |   reentry |   reentry_symbols |   reentry_signals | largest_symbol   |   largest_share |   largest_signal_trades |   sum_pnl_pct |   sum_pnl_usdt |
|:--------------------|---------:|----------:|------------------:|------------------:|:-----------------|----------------:|------------------------:|--------------:|---------------:|
| S0_pre_gate3C       |        2 |         0 |                 0 |                 0 | nan              |               0 |                       1 |        -10.61 |         -28.09 |
| S1_gate3C_old_exit  |        1 |         0 |                 0 |                 0 | nan              |               0 |                       1 |         -6.28 |         -16.25 |
| S2_restart_insample |        8 |         8 |                 1 |                 1 | BR               |             100 |                       8 |         -8.92 |         -24.15 |
| S3_oos              |        0 |         0 |                 0 |                 0 | nan              |               0 |                       0 |          0    |           0    |

- 未平仓交易: **0**（不计入任何统计）

## B. PREREG GATE3-C 主判据（自 2026-09-19 起前 30 笔已平仓，含手续费算术和）

- 进度 **8/30**，累计 **-8.92%**，状态 **ACCUMULATING**（提前中止闸 ≤ -20%）

## C. P9 门槛（仅 OOS 段）

- ❌ re-entry ≥ 30
- ❌ unique symbols ≥ 10
- ❌ unique signals ≥ 5
- ❌ 单一 symbol 占比 < 50%

- 当前: re-entry=0, symbols=0, signals=0, 最大占比=0%（None）
- 判定: **INSUFFICIENT_OOS_SAMPLE**

## D. OOS 段分层（描述性，n<30 不作推断）

### D1. Re-entry interval（小时；注意 Auto lock 使其≈距下一根 4h 开盘的时间）

_（无样本）_

### D2. Previous exit reason

_（无样本）_

### D3. Price recovery（相对上一笔入场价，边界 0%）

_（无样本）_

### D4. Signal 内交易序号

_（无样本）_

### D5. Symbol 集中度

_（无样本）_

---

## 状态

```
V3 FROZEN
NO PARAMETER CHANGE
NO COOLDOWN IMPLEMENTED
OOS ACCUMULATION ONLY
```

> 本文件全部由脚本从数据生成；结论分级见 AI500_NEXT_PHASE_RESEARCH.md，不在此处手写。
