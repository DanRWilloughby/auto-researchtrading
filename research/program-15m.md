# autotrader — 15-minute timeframe

Autonomous trading strategy research on Hyperliquid perpetual futures at **15-minute intervals**.

## Context

This project adapts Karpathy's autoresearch pattern for trading strategy discovery.
A 1-hour strategy already exists that scored 21.16 (Sharpe) on val and 18.20 on test.
Your job: **discover the best 15-minute strategy** for BTC/ETH/SOL.

**Key differences from 1h:**
- 4x more bars → 4x more data, 4x more trades
- Higher fee impact — 2bps maker + 5bps taker adds up fast with more trades
- More noise — 15m candles are noisier than 1h, signals need to be stronger
- Faster mean reversion — shorter-term patterns may dominate
- The 1h strategy's parameters (windows, thresholds) will NOT directly transfer

## Current Leaderboard

```
RANK  STRATEGY             SCORE     INTERVAL  NOTES
1.    exp32 (6-signal)     21.158    1h        ← reference (different timeframe)
```

Your baseline: start from a simple momentum strategy and evolve. **Beat score 0.0 first, then optimize.**

## Setup

1. **Branch**: `git checkout -b autotrader/15m-<tag>` from main
2. **Verify data**: `ls ~/.cache/autotrader/data/BTC_15m.parquet`
3. **Strategy file**: `strategies/15m-btc-eth-sol/strategy.py`
4. **Run backtest**: `uv run engine/backtest.py --interval 15m --strategy strategies/15m-btc-eth-sol/strategy.py`
5. **Results**: record in `strategies/15m-btc-eth-sol/results.tsv`

## Rules

**What you CAN do:**
- Modify `strategies/15m-btc-eth-sol/strategy.py` — this is the only file you edit
- Use numpy, pandas, scipy, and standard library only

**What you CANNOT do:**
- Modify anything in `engine/`
- Install new packages
- Use test set data

## Strategy Research Directions

### Fee-Aware Design (CRITICAL at 15m)
- The strategy must account for 7bps round-trip cost (2bps maker + 5bps taker + slippage)
- At 15m with ~35,000 bars/year, even 100 trades/month costs ~7% annually in fees
- **High win rate + tight stops** is essential — can't afford many losers
- Consider wider entry thresholds than 1h to reduce false signals

### High-Probability Approaches
- **Momentum with aggressive filtering** — require very strong signals before entry
- **Vol compression breakout** — Bollinger Band squeeze → breakout with volume confirmation
- **RSI extremes only** — trade only when RSI hits genuine extremes (>80 or <20)
- **Multi-bar confirmation** — require 2-3 consecutive confirming bars before entry
- **Wider ATR stops** — give trades more room, reduce whipsaw exits

### Worth Exploring
- **Time-of-session effects** — crypto has intraday patterns (US open, Asia open)
- **Volume profile** — trade only during high-volume periods where signals are more reliable
- **Shorter lookback windows** — 15m data has more recency bias
- **Adaptive position sizing** — smaller positions when signal confidence is low

### Pitfalls to Avoid
- Don't port 1h parameters directly — the dynamics are different
- Don't over-trade — more bars ≠ more good trades
- Watch the turnover penalty — excessive trading kills the score
- Don't ignore fees in your mental model — they compound

## Data Available

- BTC, ETH, SOL 15-minute OHLCV + funding rates
- Val period: 2024-07-01 to 2025-03-31 (~26,000 bars per symbol)
- History buffer: last 500 bars via `bar_data[symbol].history` DataFrame
- Columns: timestamp, open, high, low, close, volume, funding_rate

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

1. Look at git state
2. Modify `strategies/15m-btc-eth-sol/strategy.py` with an experimental idea
3. git commit
4. `uv run engine/backtest.py --interval 15m --strategy strategies/15m-btc-eth-sol/strategy.py > run.log 2>&1`
5. `grep "^score:\|^sharpe:\|^max_drawdown_pct:" run.log`
6. If empty → crashed. `tail -n 50 run.log`, fix or skip.
7. Record in `strategies/15m-btc-eth-sol/results.tsv`
8. If score IMPROVED: keep
9. If score equal or worse: `git reset --hard HEAD~1`

## NEVER STOP

Once the experiment loop has begun, do NOT pause to ask the human if you should continue. You are autonomous. If you run out of ideas, think harder.
