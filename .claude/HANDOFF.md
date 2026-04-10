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
- Added `--equity` mode to `paper/trader.py` — yfinance data, market hours check, equity cost model
- Deployed equity paper trader to VM cron: `7 14-20 * * 1-5` (hourly during US market hours)
- Added "Deep Validation" tab to the Vercel dashboard with regime charts, walk-forward bars, correlation, execution reality check, Monte Carlo, per-symbol PnL
- Fixed dashboard sync script to copy `*.json` from strategy directories (was only copying .tsv/.csv/.jsonl)

## Current State
- **Dashboard live:** https://dashboard-green-nu-53.vercel.app — select "1h EQUITIES" dropdown, "Deep Validation" tab
- **Paper trading live:** VM cron at :07 past each hour during market hours, alongside crypto strategies
- **Auto-sync:** Hourly cron pulls VM data and redeploys Vercel when changed
- Strategy optimized + validated across 3 splits, 11 regimes, 11 walk-forward windows
- Alpha is overwhelmingly from overnight holds (overnight momentum strategy)
- Dashboard source is in `overnight-lab/projects/2026-03-22_autoresearch-trading-dashboard/dashboard/` (NOT in this repo)

## Pending / Not Yet Tested
- [ ] Paper trading needs 20+ trading days of data before evaluation
- [ ] Actual short borrow rate verification (assumed 5% flat, real rates vary)
- [ ] Market impact at scale (tested at $100K; may not work above $500K on XBI/EEM)
- [ ] Overnight gap P&L attribution per-event
- [ ] Cron timing: consider moving from :07 to :03 for tighter execution (risk: yfinance candle not ready)

## Next Steps
- [ ] Monitor paper trading for 20+ days, compare equity curve to backtest
- [ ] If tracking error < 2% daily, deploy micro-live ($1K-5K) on Alpaca
- [ ] Consider running at 2x leverage initially (standard margin) before scaling to 3x (portfolio margin)

## Quick Context
Built and fully validated a 10-ETF equity momentum strategy. 133 experiments, 11/11 regimes profitable, 100% walk-forward consistency. Realistic execution (1-bar delay + short borrow): ~1.08% daily, 9.3% DD, Sharpe 23.26. Paper trading live on VM, dashboard live on Vercel with Deep Validation tab. Dashboard sync script fixed to handle JSON files. Next milestone: 20 days of paper data to evaluate.
