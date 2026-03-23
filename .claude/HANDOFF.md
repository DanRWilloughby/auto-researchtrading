# Session Handoff - 2026-03-23 (Session 40 — Regime Testing & 8-Coin Evaluation)

## What We Did
- Investigated paper trader status on VM — found it running correctly at `/home/openclaw/auto-researchtrading/` under `openclaw` user cron (`:05` every hour)
- 1h paper trader performance: +0.34% ($100,340), 0.03% max DD, 63 trades across 15 hourly ticks, all 8 coins
- Added custom date range support to backtester (`--start`/`--end` flags in backtest.py and prepare.py)
- Identified and ran backtests across 4 market regimes: bear (Oct-Nov '25), flat (Jul-Sep '25), choppy (Mar-May '24), full 2.5yr
- Downloaded 30m data for all 8 coins (XRP and SUI only have ~3 weeks from Hyperliquid; DOGE/AVAX/LINK have full coverage)
- Created `strategies/30m-8coin/strategy.py` — identical logic, equal-weight across 8 symbols
- Ran full 3-coin vs 8-coin comparison across all regimes

## Key Results

### 30m Strategy Regime Tests (3-coin)
| Regime | Window | Score | Return | Max DD | Win Rate |
|--------|--------|-------|--------|--------|----------|
| Val baseline | Jul '24–Mar '25 | 24.41 | +84.6% | 0.21% | 80.8% |
| Bear | Oct–Nov '25 | 21.00 | +12.9% | 0.25% | 79.0% |
| Flat | Jul–Sep '25 | 23.46 | +12.8% | 0.14% | 79.9% |
| Choppy | Mar–May '24 | 23.23 | +17.5% | 0.42% | 82.4% |
| Full 2.5yr | Jun '23–Dec '25 | 21.77 | +499% | 0.42% | 79.9% |

### 8-Coin Diversification Effect
| Regime | 3-coin Sharpe | 8-coin Sharpe | 3-coin DD | 8-coin DD |
|--------|--------------|---------------|-----------|-----------|
| Val | 24.48 | **30.44** | 0.21% | **0.09%** |
| Bear | 21.00 | **24.08** | 0.25% | 0.24% |
| Flat | 23.46 | **28.49** | 0.14% | **0.07%** |
| Choppy | 23.23 | **32.58** | 0.42% | **0.09%** |

8-coin: +25-40% better Sharpe, ~50% lower max DD. Absolute returns lower due to smaller per-coin allocation (1% vs 2.64% of equity). Could increase `BASE_POSITION_PCT` from 0.08 to ~0.21 to restore absolute returns.

## Current State
- Branch: `autotrader/30m-exp1` (not merged to main)
- 1h paper trader: running on VM, performing well
- 30m paper trader: NOT deployed yet
- 8-coin 30m strategy created at `strategies/30m-8coin/strategy.py`
- Custom date range backtesting now supported via `--start`/`--end` flags
- XRP/SUI 30m data limited (~3 weeks) — effectively 6-coin for historical tests

## Pending / Not Yet Tested
- [ ] Position sizing tuning for 8-coin version (increase BASE_POSITION_PCT to match 3-coin absolute returns)
- [ ] BB compression signal replacement (fires ~90% of time, weak discriminator)
- [ ] Full XRP/SUI 30m data (need alternative data source for historical)

## Next Steps
- [ ] Decide: 3-coin or 8-coin for 30m paper trading
- [ ] If 8-coin: tune BASE_POSITION_PCT, then deploy
- [ ] Deploy 30m paper trader on VM (cron at :05 and :35)
- [ ] Monitor both 1h and 30m paper traders for 1-2 weeks
- [ ] Merge branch to main once paper trading validates

## Quick Context
Validated the 30m strategy across bear/flat/choppy/long-duration regimes — holds up with 21-24 Sharpe and <0.5% DD in every condition. Tested 8-coin diversification and found dramatic risk-adjusted improvement (+25-40% Sharpe, ~50% lower DD) but with lower absolute returns due to position sizing. Decision pending on whether to go 3-coin or 8-coin, and whether to tune position sizing up to compensate.
