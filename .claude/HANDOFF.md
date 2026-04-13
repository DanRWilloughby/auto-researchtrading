# Session Handoff - 2026-04-13

## What We Did

### Coinbase Backtest Infrastructure
- Added `--source coinbase` to backtest pipeline — downloads candles from CB perps API, stores as separate parquet files, defaults to 3bps taker fee
- CB baseline backtest: Sharpe 26.11, win rate 73.2% — params transfer well from HL (23.8 on same test split)

### Conviction Analysis
- Added signal metadata (vote counts) to trade records
- 4/5 votes is the sweet spot ($18,751 avg net), 5/5 underperforms (late-to-the-party effect)
- Hybrid maker/taker routing NOT viable — all conviction levels carry positive EV

### Fee-Reduction Variants
- 1h candles: best fee efficiency (12.6% fee/gross) but 30m wins on compounding (700x vs 220x over 165 days, 4.05%/day vs 3.32%/day)
- MIN_VOTES=4: hurt more than helped (lost alpha > fee savings)
- No-trade 02-08 UTC: worst performer (forced closes created churn)
- **Decision: stay on 30m, eat the fees — compounding advantage dominates**

### Dual-Feed Strategies (HL + CB combined)
- Consensus (both agree): -$12.9M vs baseline, over-filters
- 10-vote pool 7/10: catastrophic (-$48.6M), too restrictive
- 10-vote pool 6/10: near-parity (-$6.3M), mostly recreates baseline
- HL signal, CB exec: best variant (win rate 73.5%, PF 8.53) but still -$3.4M
- **None beat the single-feed CB baseline. The directional disagreement between exchanges is signal, not noise.**

## Current State
- All code committed and pushed to `autotrader/30m-exp1` (2 commits)
- Live strategy on Coinbase is UNCHANGED (30m, current parameters, single-feed)
- Four research reports in `strategies/30m-concentrated/`:
  - `COINBASE_BACKTEST_REPORT.md` — CB vs HL comparison
  - `CONVICTION_ANALYSIS_REPORT.md` — vote count vs return
  - `FEE_REDUCTION_REPORT.md` — 3 fee variants
  - `DUAL_FEED_REPORT.md` — 3 dual-feed variants
- Analysis scripts in `scripts/`: `analyze_conviction.py`, `compare_fee_variants.py`
- Strategy variants created (for reference, not live use): `strategy_min4.py`, `strategy_notrade_hours.py`, `strategy_consensus.py`, `strategy_10vote.py`, `strategy_hl_signal.py`
- Shared dual-feed infra: `dual_feed_loader.py`, `dual_feed_helpers.py`

## Next Steps
- [ ] Monitor live CB P&L over coming weeks to validate backtest findings
- [ ] Negotiate better taker rates with Coinbase as volume scales
- [ ] Investigate cross-exchange disagreement as a *signal* (when HL bullish + CB flat → CB catch-up?)
- [ ] Position sizing by conviction (larger at 4/5, smaller at 5/5) as research thread

## Quick Context
Comprehensive research session: CB backtest validates params, conviction analysis shows non-monotonic pattern, fee reduction analysis proves compounding > fee savings, dual-feed analysis proves single-feed is optimal. The strategy is well-tuned — the winning move is to stay on 30m single-feed CB and let it compound. No changes to live needed.
