# Session Handoff - 2026-04-13

## What We Did

### Symbol Subset Backtests
- Tested BTC only, BTC+ETH, BTC+ETH+SOL (baseline) on val+test splits
- Each additional asset improves Sharpe: BTC=11.9, BTC+ETH=19.4, BTC+ETH+SOL=23.8 (test)
- SOL contributes +4.4 Sharpe over BTC+ETH — not just correlation, real signal diversity
- Max DD tradeoff: 1.28% (BTC) → 2.89% (BTC+ETH) → 4.00% (all 3)

### XRP / Multi-Asset Expansion
- Fixed strategy to accept dynamic symbols (was hardcoded to BTC/ETH/SOL, reverted after testing)
- XRP adds almost nothing: BTC+ETH+XRP = 19.60 Sharpe vs BTC+ETH = 19.42 (+0.18, noise)
- Adding XRP to base 3: 23.87 vs 23.82 (+0.05 Sharpe)
- XRP has worse per-trade quality (lower PF) than SOL in every comparison
- All 8 symbols: Sharpe 28.68 but DD jumps to 5.92% and win rate drops 2.6pts
- **Decision: BTC/ETH/SOL is the right basket. No change needed.**

### Take Profit Re-Optimization at Size 1.20
- Original TP=1.2% was optimized at size 1.50 (Phase 2, Mar 27)
- Full sweep 0.8%–2.0% at current deployed size 1.20, both splits
- Pattern is monotonic: lower TP = higher Sharpe, higher return, lower DD
- TP=0.8%: Sharpe 24.23, return 4,357,487%, DD 3.86% (test)
- TP=1.2% (current): Sharpe 23.82, return 3,689,345%, DD 4.00% (test)
- TP=0.8% wins all three axes: +0.41 Sharpe, +18% return, -0.14% DD
- **Not yet deployed. Tradeoff: 264 more trades = more live fee exposure.**

## Current State
- Strategy file is UNCHANGED (TP still 1.2%, symbols still BTC/ETH/SOL)
- All experiments logged to `strategies/30m-concentrated/results.tsv` (rows 98–117)
- No new code committed this session — research only via CLI experiments

## Next Steps
- [ ] Consider deploying TP=0.8% after paper validation
- [ ] Monitor live CB P&L to validate backtest findings
- [ ] Negotiate better taker rates with Coinbase as volume scales
- [ ] Position sizing by conviction (larger at 4/5, smaller at 5/5) as research thread

## Quick Context
Research session testing symbol composition and TP re-optimization. BTC/ETH/SOL confirmed as optimal basket (XRP adds nothing, 8 symbols dilutes quality). TP should likely come down to 0.8% at current 1.20 sizing — wins on Sharpe, return, and DD — but not deployed yet pending live validation.
