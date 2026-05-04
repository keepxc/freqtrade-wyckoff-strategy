# Session Index

This is the session index for the past 24 hours.

## Freqtrade Sessions

* None

# README_for_Claude

## What this package is
This package is the implementation starter for P5 / P5-WS, the Strategy Intelligence Layer.
It is not a trading strategy module. Freqtrade remains the execution engine.

## Read this first
1. `docs/P5-Data-Design.md`
2. `docs/P5-Event-Spec.md`
3. `docs/P5-V1-Implementation-Plan.md`
4. `db/schema.sql`
5. `db/views.sql`

## Implementation intent
- Keep trading execution inside Freqtrade.
- Write structured runtime events to JSONL.
- Ingest those events into SQLite first.
- Build summaries and diagnostics from DB views.
- AI reads summaries and facts; AI does not mutate production logic directly.

## V1 scope
- SQLite only
- Core tables only
- Core views only
- No auto-upgrade of baseline
- No auto-parameter mutation

## Current execution order context
The wider project order is:
- P1 complete
- P3 complete
- P4 archived as future regime-aware optimization seed
- P5 / P5-WS is the platformization stage

## What to implement first
1. `db/schema.sql`
2. `db/views.sql`
3. JSONL event emitter contract in strategy-side code
4. Ingest script
5. Weekly summary script


## New implementation files added
- `ingest/ingest_jsonl.py`: import structured JSONL events into SQLite
- `reports/build_weekly_report.py`: build and optionally store a minimal weekly markdown report
- `examples/sample_events.jsonl`: sample event stream for local validation
- `events/sample/README.md`: quickstart commands

## Suggested first validation
1. Create a fresh SQLite DB using `db/schema.sql` and `db/views.sql`.
2. Ingest `examples/sample_events.jsonl` with `ingest/ingest_jsonl.py`.
3. Generate a weekly report with `reports/build_weekly_report.py`.
4. Confirm data appears in `experiment_runs`, `signals`, `trades`, `detector_events`, and `regime_snapshots`.


推荐回测命令：
```bash
docker compose run --rm freqtrade backtesting --config user_data/config_backtest.json --strategy WyckoffV7_TrueMethod --timerange 20230101-20260330 --cache none
```

Dry-run 检查：
```bash
python check_dryrun_fields.py
```