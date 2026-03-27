# Auto-Research: Pairs/Statistical Arbitrage (1h)

## Objective
Optimize the pairs arbitrage strategy to achieve the highest possible score. The baseline scores -5.12 — your first goal is to get it positive, then maximize.

## Strategy Location
`strategies/1h-pairs-arb/strategy.py`

## Run Command
```
uv run engine/backtest.py --strategy strategies/1h-pairs-arb/strategy.py --all-symbols --label EXP_NAME --notes "DESCRIPTION"
```

## Scoring
Higher score is better. Score = sharpe * sqrt(trade_count_factor) - drawdown_penalty - turnover_penalty.
Hard cutoffs: <10 trades → -999, >50% drawdown → -999, lost >50% → -999.

## Results Tracking
Every backtest auto-appends to `strategies/1h-pairs-arb/results.tsv`. Use `--label` to name each experiment. Use `--notes` for a description of what changed.

## Experiment Protocol
1. Read current strategy.py
2. Plan a single modification
3. Edit strategy.py
4. Run backtest with descriptive --label and --notes
5. Check score vs best so far
6. If improved: keep and note as new best. If worse: revert strategy.py to the best version.
7. LOOP — never stop until interrupted

## CRITICAL: Save Best Version
After each improvement, save the current strategy.py content. If a later experiment fails, restore from the saved best version. Never lose a good result.

## Research Directions (prioritized)

### Phase 1: Fix the Basics (get score positive)
- **Pair selection**: Try different pairs. Maybe BTC/ETH is too correlated (moves together). Try cross-sector: BTC/DOGE, SOL/AVAX
- **Z-score thresholds**: Baseline uses 2.0 entry / 0.5 exit. Try wider entry (2.5, 3.0) for higher conviction. Try tighter exit (0.2, 0.1)
- **Lookback window**: 48 bars may be too short or too long for spread mean-reversion. Sweep 24, 36, 48, 72, 96, 144
- **Position sizing**: 6% per leg may be too aggressive. Try 3%, 4%, 5%
- **Stop-loss**: Z-score stop at 4.0 may be too wide. Try 3.0, 3.5

### Phase 2: Signal Improvement
- **EMA spread** instead of SMA: faster reaction to spread changes
- **Bollinger Bands on spread**: enter when spread touches BB, exit at mid
- **Volume confirmation**: only enter when spread move has volume behind it
- **Trend filter**: don't fade a spread that's trending (add momentum on spread)
- **Asymmetric pairs**: weight legs differently (BTC leg smaller than altcoin leg)

### Phase 3: Risk & Structure
- **Maximum holding period**: force exit after N bars even if z-score hasn't reverted
- **Dynamic lookback**: shorter in high-vol, longer in low-vol
- **Multi-pair portfolio limits**: max total exposure across all pairs
- **Cointegration test**: select pairs dynamically based on rolling cointegration

### Phase 4: Novel Approaches
- **Ratio mean-reversion**: trade the price ratio directly instead of log spread
- **Residual pairs**: regress ETH on BTC, trade the residual
- **3-leg trades**: long A, short B, short C (triangular arb)
- **Sector rotation**: long strongest pair, short weakest

## Data Available
8 coins: BTC, ETH, SOL, XRP, SUI, DOGE, AVAX, LINK
Fields: OHLCV + funding_rate, 500-bar history buffer
Val period: Jul 2024 - Mar 2025

## NEVER STOP. Run experiments continuously. Every experiment must use --label and --notes.
