# Session Handoff - 2026-03-23 (Session 41 — Data Persistence, Infrastructure & 30m Deployment)

## What We Did
- **Position sizing sweep** for 30m-8coin: tested BASE_POSITION_PCT at 0.08/0.12/0.16/0.21. No cliff — drawdown scales linearly (0.17%→0.46%), returns compound super-linearly (+82%→+365%). Sharpe stays 29-30 across all sizes.
- **Auto-logging added** to `engine/backtest.py` — every backtest now auto-appends to `results.tsv` in the strategy directory. New flags: `--label`, `--notes`, `--no-log`.
- **Reconstructed full 30m experiment history** — re-ran all 23 kept experiments (exp0→champion) from git commits, recovered scores. Evolution: 21.33→24.58 peak, champion at 24.41 (deliberately stepped back for robustness). Full results in `strategies/30m-btc-eth-sol/results.tsv`.
- **Paper trader refactored** to strategy-scoped state — state now writes to `strategies/{id}/paper_state.json`. Trade logs go to `strategies/{id}/logs/`.
- **VM migrated** — 1h state moved to `strategies/1h-8coin/paper_state.json`, updated trader.py deployed.
- **30m paper trader deployed** on VM — separate $100K budget, cron at `:05,:35`, all 8 coins, BASE_POSITION_PCT=0.08.
- **CLAUDE.md rule added** for strategy-scoped data convention.
- **RESEARCH.md written** for 30m-8coin with full narrative.

## Current State — Two Paper Traders Running
| | 1h-8coin | 30m-8coin |
|---|---|---|
| Cron | `:05` every hour | `:05,:35` every hour |
| Script | `run-cron.sh` | `run-cron-30m.sh` |
| State | `strategies/1h-8coin/paper_state.json` | `strategies/30m-8coin/paper_state.json` |
| Logs | `strategies/1h-8coin/logs/` | `strategies/30m-8coin/logs/` |
| Capital | $100K | $100K (fresh start) |
| Status | +0.40%, 106 trades | Just deployed, first tick at next :35 |

- Branch: `autotrader/30m-exp1` (not merged to main)
- Dashboard session building against `strategies/{id}/paper_state.json` in parallel
- Old `paper/state/paper_state_1h.json` still on VM as backup

## Pending
- [ ] Monitor both strategies for 1-2 weeks
- [ ] Remove old `paper/state/` backup after confirming new path stable
- [ ] BB compression signal replacement (fires ~90% of time, weak discriminator)
- [ ] Full XRP/SUI 30m data (need alternative source for backtesting)
- [ ] Merge branch to main once paper trading validates

## Quick Context
Major infrastructure session. Fixed silent data loss: backtest results now auto-log, 30m experiment history reconstructed from git, paper state moved to strategy-scoped paths. Deployed 30m-8coin as second paper trader alongside the 1h. Decision: kept 0.08 position size (conservative, apples-to-apples comparison) and all 8 coins (XRP/SUI will build forward track record paper trading can't backtest).
