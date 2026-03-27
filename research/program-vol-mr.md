# Auto-Research: Volatility Mean-Reversion (1h)

## Objective
Optimize the volatility mean-reversion strategy. Baseline scores 3.07 with +46.6% return but 12.1% max drawdown. Primary goal: reduce drawdown while preserving returns.

## Strategy Location
`strategies/1h-vol-mr/strategy.py`

## Run Command
```
uv run engine/backtest.py --strategy strategies/1h-vol-mr/strategy.py --all-symbols --label EXP_NAME --notes "DESCRIPTION"
```

## IMPORTANT: Backtest Runtime
This strategy takes ~690 seconds per backtest due to complex rolling calculations. Optimize the code for speed where possible (vectorize numpy operations, reduce nested loops). Faster backtests = more experiments = better final strategy.

## Results Tracking
Every backtest auto-appends to `strategies/1h-vol-mr/results.tsv`. Use `--label` and `--notes` for every run.

## Experiment Protocol
1. Read current strategy.py
2. Plan a modification (consider both logic AND performance)
3. Edit strategy.py
4. Run backtest with --label and --notes
5. Check score vs best. If improved: keep. If worse: revert.
6. LOOP

## CRITICAL: Save Best Version after each improvement.

## Research Directions

### Phase 0: Speed Optimization (DO THIS FIRST)
The _bb_width_percentile and _vol_zscore methods are O(n²) with Python loops. Vectorize them:
- Use numpy rolling operations instead of Python for-loops
- Pre-compute arrays instead of recalculating per bar
- Cache intermediate results across bars
- Target: get backtest under 120 seconds

### Phase 1: Drawdown Reduction
- **Tighter ATR stops in high-vol**: Baseline ATR_STOP_MULT = 4.0. Try 3.0, 2.5.
- **Position sizing**: Baseline 0.08. Try 0.04, 0.06. Smaller positions = less DD.
- **MAX_POSITIONS**: Baseline 6. Try 3, 4. Less correlated exposure.
- **High-vol RSI thresholds**: Baseline 70/30. Try 75/25 (more extreme = higher conviction).
- **BTC regime filter weight**: Currently uses global BTC regime. Try per-coin only.

### Phase 2: Regime Detection
- **BB percentile thresholds**: HIGH_VOL_PCTILE=80, LOW_VOL_PCTILE=20. Try 70/30, 75/25, 85/15.
- **ATR ratio** instead of BB: Compare short ATR to long ATR for regime.
- **Vol-of-vol**: Standard deviation of vol itself as regime indicator.
- **Hysteresis**: Don't flip regimes on single bars. Require N consecutive bars in new regime.
- **Three-state machine**: high-vol / normal / low-vol with explicit transitions.

### Phase 3: Entry Signals
- **High-vol entries**: Add MACD or momentum confirmation to RSI mean-reversion.
- **Low-vol entries**: Add BB squeeze detection (width below Nth percentile) for breakout timing.
- **Voting system**: Combine multiple signals (RSI + EMA + momentum) like the champion strategy.
- **Time-in-regime**: Entries are better after regime has persisted for N bars (not on first bar of new regime).

### Phase 4: Novel Ideas
- **Asymmetric behavior**: Different logic for "vol expanding from low" vs "vol compressing from high".
- **Cross-coin vol regime**: When BTC is in high-vol but SOL is in low-vol, prioritize SOL breakouts.
- **Vol term structure**: Compare short-lookback vol to long-lookback vol (contango/backwardation analog).
- **Mean-reversion exit targets**: In high-vol, target the 20-bar SMA as exit instead of RSI normalization.

## NEVER STOP. Run experiments continuously.
