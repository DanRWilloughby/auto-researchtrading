# Auto-Research: Funding Rate Mean-Reversion (1h)

## Objective
Optimize the funding rate mean-reversion strategy. Baseline scores -0.76 — close to breakeven, needs parameter tuning to turn profitable.

## Strategy Location
`strategies/1h-funding-mr/strategy.py`

## Run Command
```
uv run engine/backtest.py --strategy strategies/1h-funding-mr/strategy.py --all-symbols --label EXP_NAME --notes "DESCRIPTION"
```

## Results Tracking
Every backtest auto-appends to `strategies/1h-funding-mr/results.tsv`. Use `--label` and `--notes` for every run.

## Experiment Protocol
1. Read current strategy.py
2. Plan a single modification
3. Edit strategy.py
4. Run backtest with descriptive --label and --notes
5. Check score vs best so far
6. If improved: keep. If worse: revert to best version.
7. LOOP — never stop

## CRITICAL: Save Best Version
After each improvement, save the strategy.py content. Always be able to restore the best-scoring version.

## Research Directions

### Phase 1: Threshold Tuning (most likely to work)
- **FUNDING_ZSCORE_ENTRY**: Baseline 1.5. Try 1.0, 1.2, 1.8, 2.0, 2.5, 3.0. Lower = more trades but lower conviction. Higher = fewer but better.
- **FUNDING_ZSCORE_EXIT**: Baseline 0.3. Try 0.0, 0.1, 0.5, 0.8. Where to take profit.
- **FUNDING_LOOKBACK**: Baseline 72 bars (3 days). Try 24, 48, 96, 120, 168. How much history for "normal" funding.
- **MAX_POSITIONS**: Baseline 4. Try 2, 3, 6, 8. Concentration vs diversification.
- **POSITION_SIZE_PCT**: Baseline 0.08. Try 0.04, 0.06, 0.10, 0.12.

### Phase 2: Signal Refinement
- **Remove momentum filter**: It might be blocking good entries. Try MOMENTUM_FILTER = False.
- **Weaker momentum filter**: Increase MOMENTUM_THRESHOLD from 0.03 to 0.05, 0.08.
- **Add RSI confirmation**: Only enter contrarian funding trades when RSI supports the direction.
- **Funding rate level** instead of z-score: Enter when absolute funding > X (e.g., 0.01% per 8h).
- **Funding acceleration**: Enter when funding is extreme AND getting more extreme (momentum of funding).
- **Cross-coin funding signal**: When BTC funding is extreme, trade alts instead (higher beta).

### Phase 3: Risk Management
- **ATR_STOP_MULT**: Baseline 4.0. Try 3.0, 3.5, 5.0, 6.0.
- **Time-based exit**: Force exit after N bars regardless (funding mean-reverts, don't hold forever).
- **Cooldown**: Baseline 6 bars. Try 2, 4, 8, 12.
- **Disable trailing stop entirely**: Use only funding normalization for exits.
- **Take profit**: Add a fixed TP at 1%, 2%, 3%.

### Phase 4: Advanced
- **Funding carry P&L**: The strategy benefits from carry (collecting funding while waiting for mean-reversion). Model this explicitly.
- **Regime filter**: Only trade funding when market is ranging (not trending). Use ADX or BB width.
- **Multi-timeframe funding**: Use 8h funding rate combined with hourly price action.
- **Scale into positions**: Start small, add if funding gets more extreme.
- **Relative funding**: Compare a coin's funding vs the 8-coin average. Trade the outliers.

## NEVER STOP. Run experiments continuously.
