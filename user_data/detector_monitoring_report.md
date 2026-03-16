# Detector Monitoring Report
Total Bars Analyzed: 1593

## 1. Activation Rates

| Detector/Signal | Count | Rate (%) |
|-----------------|-------|----------|
| cm_intent_accumulating | 173 | 10.86% |
| cm_intent_distributing | 383 | 24.04% |
| cm_intent_testing | 457 | 28.69% |
| cm_intent_trending_down | 166 | 10.42% |
| cm_intent_trending_up | 169 | 10.61% |
| cm_intent_unclear | 245 | 15.38% |
| market_env_markup | 1394 | 87.51% |
| market_env_transition | 199 | 12.49% |
| spring_bar | 25 | 1.57% |

## 2. Conflict Analysis

| Conflict Type | Count | Rate (%) | Severity |
|---------------|-------|----------|----------|
| cm_intent_rapid_changes | 6 | 0.38% | low |
| daily_gate_conflicts | 0 | 0.0% | low |
| markdown_with_markup_phase | 0 | 0.0% | none |

## 3. Fallback Analysis

| Fallback Type | Count | Rate (%) | Description |
|---------------|-------|----------|-------------|
| cm_intent_neutral | 0 | 0.0% | Composite man intent unclear |
| daily_gate_soft | 0 | 0.0% | Daily gate soft mode (no hard block) |
| market_env_confidence_low | 0 | 0.0% | market_env_confidence below 0.5 threshold |
| market_env_transition | 199 | 12.49% | Market env unclear, fallback to EMA |

## 4. Phase Distribution

| Phase | Count | Rate (%) |
|-------|-------|----------|
| phase_dist_phase_c | 489 | 30.7% |
| phase_phase_a | 114 | 7.16% |
| phase_phase_c | 149 | 9.35% |
| phase_unknown | 841 | 52.79% |

## 5. Signal Quality

- market_env_confidence_mean: 0.8
- market_env_confidence_std: 0.0
