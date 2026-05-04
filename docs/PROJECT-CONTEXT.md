# Project Context (Agent Reference)

> 非正式文档，给 AI agent 快速了解项目状态用。正式文档见 DECISIONS.md / ARCHITECTURE.md。

## Environment
- User location: Zhengzhou, China
- Proxy: `http://127.0.0.1:7890/` (Clash/V2Ray)
- HTTP_PROXY / HTTPS_PROXY / ALL_PROXY should all point to 127.0.0.1:7890

## Freqtrade Service
- systemd: `~/.config/systemd/user/freqtrade-dryrun.service`
- `Restart=always` (not `on-failure`, because strategy can exit cleanly via WY_EXIT_TRACE/WY_AXIS_FAILED_EXIT)
- Service sets proxy env vars
- User may also start freqtrade manually in a separate terminal — check PID if logs appear from unexpected timeframe

## P5 Data
- DB: `user_data/market.db` — dry_run only, no backtest data
- CWD: `/home/gyan/freqtrade`
- Harness Engineering adopted (2026-04-15): lint_p5.py, DECISIONS.md, ARCHITECTURE.md, gc_market_db.py, GOLDEN_RULES.md

## Wyckoff Knowledge
- Full guide: `威科夫交易方法完整学习指南.md` (4580 lines)
- Skill: `wyckoff-trading-knowledge` (core concepts, auto-loaded)
- Strategy: `user_data/strategies/WyckoffV7_TrueMethod.py`

## Obsidian Vault
- Path: `/media/gyan/INFO/information/AITrader`
- Synced via SSH to `github.com:keepxc/keepnote`
- Uses Obsidian Git plugin for auto-sync

## Related External Resources
- NotebookLM notebook with Wyckoff content (requires Google login, not accessible by agent)
