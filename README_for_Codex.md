# README_for_Codex

## Start here
Read files in this order:
1. `README_for_Codex.md`
2. `AGENTS.md`
3. `docs/agent-workflow.md`
4. `docs/agent-checklists.md`
5. `docs/agent-prompts.md`

## Current execution order
Follow this order unless the TASKS file explicitly says otherwise:
1. P1 — parameter system engineering
2. P3 — swing-level Effort/Result
3. P4 — archived conclusion only, do not execute as mainline work
4. P5 / P5-WS — future platformization and strategy intelligence layer

## Core rules
- Make the smallest verifiable change first.
- Preserve the stable baseline unless TASKS explicitly authorizes a new experiment.
- Never expand into wide parameter sweeps without permission from TASKS.
- Treat P4 as a future regime-aware optimization hook, not a current implementation target.
- Treat P5 / P5-WS as architecture and governance work, not direct production auto-optimization.

## P1 rules
- Research mode: `soft`
- Deployment default: `off`
- Run baseline first
- Test single-parameter changes only
- Use elimination, not combinational explosion
- Judge in this order:
  1. gate distribution changed or not
  2. trading behavior improved or not
  3. performance improved or not

## P3 rules
- Start only after P1 conclusion is written back
- Build on existing `WaveAnalyzer`
- Use swing-level E/R as an additive layer
- Do not replace bar-level E/R
- Prioritize holding management, reduce/exit logic, and Spring confidence enhancement

## P4 rules
- P4 is reviewed and archived
- Do not wire broad regime switching into the mainline now
- Re-open only if TASKS explicitly shows persistent regime mismatch evidence

## P5 / P5-WS rules
- Build ledgers, monitoring, diagnostics, proposal flow, and controlled evolution gates
- AI may analyze, summarize, diagnose, and propose
- AI must not directly modify production strategy logic without explicit approval and validation gates

## Validation gates
Every meaningful change must preserve these gates:
- full-range backtest
- three-segment validation
- dry-run compatibility / field checks
- result write-back to TASKS
- rollback-ready commit discipline

## If uncertain
When a choice is ambiguous:
- prefer the stable baseline
- prefer narrower scope
- prefer explicit write-back before next change
- prefer evidence over intuition
