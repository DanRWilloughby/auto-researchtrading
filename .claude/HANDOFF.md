# Session Handoff - 2026-03-26 (Evening)

## What We Did
- Checked both paper traders — both healthy, 1h at +0.88%, 30m at +0.44%
- Deep-dived 1h-8coin: full parameter breakdown, hold time analysis (66% = 1hr), fee analysis (5bps, 25% of gross)
- Built Hourly P&L chart component (recharts, cumulative + per-hour toggle)
- Built Simulated Return Projector (8 toggleable assumptions, 3/6/12mo projections, leverage integration)
- Deployed both to Vercel dashboard (merged feature branch to main first to preserve existing features)
- Extrapolated returns vs S&P 500: ~137% annualized compound (3.6 days sample, very noisy)
- **Designed 4 new strategy concepts** for auto-research:
  1. **Pairs/Stat Arb** (1h) — market-neutral spread trading between correlated pairs
  2. **Funding Rate Mean-Reversion** (1h) — contrarian funding as primary signal
  3. **Volatility Mean-Reversion** (1h) — regime-based: MR in high-vol, breakout in low-vol
  4. **Multi-Timeframe Fusion** (30m) — 1h trend filter on 30m entries
- Ran baselines: Pairs -5.12, Funding -0.76, Vol MR +3.07, MTF Fusion +14.18
- **Launched 4 parallel auto-research agents** running overnight in isolated worktrees

## OVERNIGHT RESEARCH RUNNING
| Agent | Strategy | Baseline | ~Time/Run | Worktree |
|---|---|---|---|---|
| pairs-arb-researcher | 1h-pairs-arb | -5.12 | ~23s | isolated |
| funding-mr-researcher | 1h-funding-mr | -0.76 | ~24s | isolated |
| vol-mr-researcher | 1h-vol-mr | 3.07 | ~690s→optimizing | isolated |
| mtf-fusion-researcher | 30m-mtf-fusion | 14.18 | ~690s | isolated |

Research agents are already iterating — vol-mr agent vectorized code, pairs-arb tuning thresholds, funding-mr adjusting entry/exit levels. All results auto-logged to each strategy's results.tsv.

## Morning Review Checklist
- [ ] Check `strategies/1h-pairs-arb/results.tsv` — how many experiments, best score
- [ ] Check `strategies/1h-funding-mr/results.tsv` — did it turn positive?
- [ ] Check `strategies/1h-vol-mr/results.tsv` — did drawdown come down from 12%?
- [ ] Check `strategies/30m-mtf-fusion/results.tsv` — did it beat 24.41 (30m champion)?
- [ ] Review the best strategy.py from each worktree
- [ ] Sync results to dashboard
- [ ] Consider deploying best performers as paper traders

## Current State
- Dashboard: https://dashboard-green-nu-53.vercel.app (hourly PnL + return projector live)
- Paper traders: 1h-8coin and 30m-8coin running on VM
- Vercel: CLI-only deploys, no GitHub integration
- overnight-lab main branch has full feature set

## Quick Context
Major research expansion session. Built dashboard analytics (hourly PnL, return projector), then designed and launched 4 new strategy concepts for overnight auto-research. The MTF Fusion baseline (14.18) is already competitive. 4 agents are running in parallel worktrees, each with a detailed research program. Check results.tsv files in the morning for full experiment logs.
