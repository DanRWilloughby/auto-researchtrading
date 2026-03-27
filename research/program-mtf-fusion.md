# Auto-Research: Multi-Timeframe Fusion (30m)

## Objective
Optimize the multi-timeframe fusion strategy. Baseline scores 14.18 — already strong! Goal: push past the existing 30m champion (24.41) and 1h champion (20.63).

## Strategy Location
`strategies/30m-mtf-fusion/strategy.py`

## Run Command
```
uv run engine/backtest.py --strategy strategies/30m-mtf-fusion/strategy.py --interval 30m --all-symbols --label EXP_NAME --notes "DESCRIPTION"
```

## IMPORTANT: Backtest Runtime
30m with 8 symbols takes ~690 seconds. Look for opportunities to optimize code performance. Faster = more experiments.

## Results Tracking
Every backtest auto-appends to `strategies/30m-mtf-fusion/results.tsv`. Use `--label` and `--notes` for every run.

## Experiment Protocol
1. Read current strategy.py
2. Plan a single modification
3. Edit strategy.py
4. Run backtest with --label and --notes
5. Check score vs best. If improved: keep. If worse: revert.
6. LOOP

## CRITICAL: Save Best Version after each improvement.

## Context: What Made the 1h Champion Score 20.63
The 1h champion evolved through 104 experiments. Key lessons:
- Simplification won every time (removing pyramiding, funding boost, correlation filter, etc.)
- 6 signals with 4/6 voting: momentum, short momentum, EMA crossover, RSI, MACD, BB compression
- RSI 50/50 bull/bear threshold with 69/31 exit
- ATR 5.5 trailing stop
- Cooldown 2 bars
- Position size 0.08, equal weights
- No vol scaling, no strength scaling

## Research Directions

### Phase 1: HTF Filter Tuning
- **HTF_MIN_VOTES**: Baseline 2/3. Try 1/3 (loose filter) and 3/3 (strict).
- **HTF window sizes**: HTF_EMA_FAST=14, SLOW=52. Try 10/40, 12/48, 16/56, 20/60.
- **HTF_MOM_WINDOW**: Baseline 24 (30m bars). Try 12, 18, 36, 48.
- **HTF_MOM_THRESHOLD**: Baseline 0.012. Try 0.008, 0.010, 0.015, 0.020.
- **Remove HTF MACD**: Maybe EMA + momentum alone is sufficient (simpler = better pattern).
- **Allow neutral HTF**: Currently blocks trades when HTF=0. Try allowing entries when HTF is neutral but 30m signal is very strong (5/6 or 6/6 votes).

### Phase 2: Entry Signal Tuning (30m level)
- **MIN_VOTES**: Baseline 4/6. Try 3/6, 5/6.
- **RSI thresholds**: RSI_BULL/BEAR at 50/50. Try 51/49, 52/48 (marginal gains from 1h research).
- **RSI exit**: 69/31. Try 70/30, 72/28, 75/25.
- **BB percentile**: Currently < 90. Try 80, 85, 95.
- **Dynamic threshold**: Baseline fixed 0.012. Try vol-adjusted like the 1h strategy.

### Phase 3: Risk Management
- **ATR_STOP_MULT**: Baseline 4.5. Try 3.5, 4.0, 5.0, 5.5, 6.0. (1h champion used 5.5)
- **Cooldown**: Baseline 2. Try 1, 3, 4 (the 1h champion went through extensive cooldown testing).
- **Position size**: Baseline 0.08. Try 0.06, 0.10, 0.12.
- **Remove position flipping**: The 1h champion kept flipping. Try removing it.
- **Take profit**: Try adding TP at 2.5% (the 30m-8coin champion uses this).

### Phase 4: Simplification (HIGH PRIORITY — pattern from 1h research)
- **Remove features one at a time**: The 1h research showed removing complexity IMPROVED scores. Try:
  - Remove BB compression signal (it fires ~90% of the time)
  - Remove vshort momentum (keep only MED_WINDOW momentum)
  - Remove MACD (keep RSI + EMA + momentum only)
  - Simplify to 4 signals with 3/4 voting
- **Equal weights**: Already using equal weights (good).
- **Remove symbol_weights entirely**: Use flat position sizing.

### Phase 5: Novel MTF Approaches
- **HTF as position sizer**: Instead of binary filter, use HTF strength to scale position size.
- **HTF trend strength**: Stronger 1h trend = more aggressive 30m entries.
- **Conflicting timeframe = sit out**: If 1h says up but 30m is ambiguous, don't trade.
- **Regime-aware HTF**: Use different HTF filter strictness in high-vol vs low-vol.

## NEVER STOP. Run experiments continuously.
