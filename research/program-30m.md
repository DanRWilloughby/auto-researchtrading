# autotrader — 30-minute timeframe

Autonomous trading strategy research on Hyperliquid perpetual futures at **30-minute intervals**.

## Context

This project adapts Karpathy's autoresearch pattern for trading strategy discovery.
A 1-hour strategy already exists that scored 21.16 (Sharpe) on val and 18.20 on test.
Your job: **discover the best 30-minute strategy** for BTC/ETH/SOL.

**Key differences from 1h:**
- 2x more bars → more data points, potentially more trading opportunities
- Moderate fee impact — less than 15m but more than 1h
- Signal-to-noise ratio is between 1h and 15m — sweet spot for many strategies
- The 1h strategy's core logic may partially transfer but parameters need re-optimization
- 30m is widely used by professional crypto traders — well-studied timeframe

## Current Leaderboard

```
RANK  STRATEGY             VAL       TEST      INTERVAL  NOTES
1.    30m-robust (exp83r)  24.408    21.864    30m       6-signal + 2.5% TP, robustness-tuned
2.    exp32 (6-signal)     21.158    18.200    1h        ← reference (different timeframe)
```

Start from a simple momentum strategy and evolve. **Beat score 0.0 first, then optimize.**

## Setup

1. **Branch**: `git checkout -b autotrader/30m-<tag>` from main
2. **Verify data**: `ls ~/.cache/autotrader/data/BTC_30m.parquet`
3. **Strategy file**: `strategies/30m-btc-eth-sol/strategy.py`
4. **Run backtest**: `uv run engine/backtest.py --interval 30m --strategy strategies/30m-btc-eth-sol/strategy.py`
5. **Results**: record in `strategies/30m-btc-eth-sol/results.tsv`

## Rules

**What you CAN do:**
- Modify `strategies/30m-btc-eth-sol/strategy.py` — this is the only file you edit
- Use numpy, pandas, scipy, and standard library only

**What you CANNOT do:**
- Modify anything in `engine/`
- Install new packages
- Use test set data

## Strategy Research Directions

### Sweet Spot Advantages
- Enough bars for statistical significance without drowning in noise
- Fees are manageable (2x fewer round-trips than 15m)
- Can capture intra-hour momentum that 1h bars miss
- Good for breakout strategies — 30m consolidation patterns are tradeable

### High-Probability Approaches
- **Adapted 1h strategy** — start with the 6-signal ensemble, adjust lookback windows (halve them)
- **Momentum + volume filter** — only trade momentum signals with above-average volume
- **EMA ribbon** — multiple EMAs (5/10/20/40 bars) alignment as trend strength indicator
- **VWAP reversion** — mean-revert to volume-weighted average price
- **Breakout with retest** — wait for breakout, then enter on the pullback to breakout level

### Worth Exploring
- **Dual momentum** — absolute momentum (is it trending?) + relative momentum (which coin trends strongest?)
- **Candle pattern recognition** — engulfing, hammer, doji patterns on 30m
- **Correlation-based pair switching** — trade the least-correlated coin in the current regime
- **Adaptive lookback** — use recent volatility to set lookback window length dynamically

### Parameter Hints (from 1h → 30m)
- If 1h uses 12-bar lookback (12 hours), try 16-24 bars at 30m (8-12 hours)
- If 1h uses RSI(8), try RSI(12-16) at 30m
- ATR stops may need wider multipliers (more noise per bar)
- Min votes threshold might need to increase (more false signals)

## Data Available

- BTC, ETH, SOL 30-minute OHLCV + funding rates
- Val period: 2024-07-01 to 2025-03-31 (~13,000 bars per symbol)
- History buffer: last 500 bars via `bar_data[symbol].history` DataFrame

## Scoring Formula

```
score = sharpe * sqrt(trade_count_factor) - drawdown_penalty - turnover_penalty
trade_count_factor = min(num_trades / 50, 1.0)
drawdown_penalty = max(0, max_drawdown_pct - 15) * 0.05
turnover_penalty = max(0, annual_turnover/capital - 500) * 0.001
Hard cutoffs: <10 trades → -999, >50% drawdown → -999, lost >50% → -999
```

## The Experiment Loop

LOOP FOREVER:

1. Modify `strategies/30m-btc-eth-sol/strategy.py`
2. git commit
3. `uv run engine/backtest.py --interval 30m --strategy strategies/30m-btc-eth-sol/strategy.py > run.log 2>&1`
4. `grep "^score:\|^sharpe:\|^max_drawdown_pct:" run.log`
5. If empty → crashed. Fix or skip.
6. Record in `strategies/30m-btc-eth-sol/results.tsv`
7. If score IMPROVED: keep
8. If score equal or worse: `git reset --hard HEAD~1`

## NEVER STOP

Once the experiment loop has begun, do NOT pause. You are autonomous.
