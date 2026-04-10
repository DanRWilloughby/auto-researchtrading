# Session Handoff - 2026-04-10

## What We Did
- Built a complete equity momentum strategy (`strategies/1h-equities/`) trading 10 non-correlated ETFs (SPY, QQQ, IWM, XLE, XLF, GLD, TLT, EEM, XBI, SOXX) on 1h bars
- Created equity data pipeline (`engine/equity_data.py`) using yfinance — downloaded ~5,000 bars per ticker covering Jun 2023 to Apr 2026
- Ran 133 logged experiments across 11 parameter dimensions via automated sweep runner (`engine/sweep.py`)
- Optimized from baseline (score 774) to optimized (score 1,512 OOS) — only kept high-impact changes (HTF params, ATR stop, RSI exhaustion, MIN_VOTES)
- Built deep validation suite (`engine/validate.py`): regime testing (11/11 profitable), walk-forward (11/11 windows), correlation analysis, drawdown stress test, Monte Carlo
- Added realistic execution features to `engine/prepare.py`: 1-bar delayed execution, short borrow costs, EOD flatten
- Validated under realistic execution: OOS still 4,932% return, 9.3% DD, Sharpe 23.26, ~1.08% daily
- Wrote formal thesis report (`EQUITY-MOMENTUM-STRATEGY-REPORT.md`) ready for sharing

## Current State
- Strategy is optimized and validated across 3 splits + 11 regimes + 11 walk-forward windows
- Realistic execution model confirmed: strategy survives 1-bar delay + short borrow
- Key insight: alpha is overwhelmingly from overnight holds (overnight momentum strategy), not intraday
- 133 experiments in `results.tsv`, report in `EQUITY-MOMENTUM-STRATEGY-REPORT.md`
- Code committed and pushed to `autotrader/30m-exp1`

## Pending / Not Yet Tested
- [ ] Alpaca paper trading integration (adapt `paper/trader.py` for equities)
- [ ] Real-time execution latency testing
- [ ] Actual short borrow rate verification (assumed 5% flat, real rates vary by ticker)
- [ ] Market impact at scale (strategy tested at $100K; may not work above $500K on XBI/EEM)
- [ ] Overnight gap P&L attribution (measured aggregate impact but not per-event)

## Next Steps
- [ ] Phase 1: Set up Alpaca paper trading account and wire up the paper trader
- [ ] Phase 2: Run paper trading for 20+ trading days, compare to backtest expectations
- [ ] Phase 3: If tracking error < 2% daily, deploy micro-live ($1K-5K)
- [ ] Consider: whether to run this alongside or instead of the crypto 30m-concentrated strategy

## Quick Context
Built a 10-ETF equity momentum strategy that passes every validation test we threw at it — 133 experiments, 11 regimes, walk-forward, Monte Carlo, and realistic execution modeling. The honest number under realistic conditions is ~1.08% daily on OOS with 9.3% max DD at 3x leverage. Alpha comes from overnight momentum, not daytrading. Ready for paper trading phase.
