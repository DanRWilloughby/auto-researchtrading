> **DEPRECATED 2026-04-16** — Numbers in this document were produced by an engine with a flip-accounting bug. See `LIVE_RECONCILIATION.md` for details.

# Conviction (Vote Count) vs Trade Return Analysis

**Date:** 2026-04-12
**Data:** Coinbase perp candles, TEST split (Jul–Dec 2025)
**Strategy:** 30m-concentrated, unmodified parameters
**Script:** `scripts/analyze_conviction.py`

## Background

The 30m-concentrated strategy uses 5 technical indicators that vote on direction:

1. Medium-term momentum (14-bar)
2. Very-short momentum (8-bar)
3. EMA crossover (3/12)
4. RSI (5-period)
5. MACD (14/26/9)

A trade requires `MIN_VOTES >= 3` out of 5 plus a confirming higher-timeframe trend filter. So entry conviction ranges from 3/5 to 5/5.

**Question:** Does higher conviction (4/5 or 5/5) predict better trade returns than lower conviction (3/5)? If so, a hybrid order routing strategy is viable — use taker orders for high-conviction trades (don't miss them) and maker orders for low-conviction trades (save fees on weaker signals).

## Results

### Conviction vs Trade Return

| Votes | Trades | Win Rate | Avg Gross PnL | Avg Fee | Avg Net PnL | Total Net PnL | Avg Hold |
|-------|--------|----------|---------------|---------|-------------|---------------|----------|
| 3/5 | 2,181 | **62.5%** | $14,695 | $3,918 | $10,778 | $23,505,799 | 2.8 bars |
| 4/5 | 1,538 | **62.2%** | $22,200 | $3,449 | **$18,751** | **$28,839,677** | 2.6 bars |
| 5/5 | 2,374 | **54.1%** | $8,926 | $2,750 | $6,176 | $14,661,457 | 2.5 bars |
| ALL | 6,093 | 59.1% | $14,342 | $3,344 | $10,997 | $67,006,933 | 2.6 bars |

### Key Finding: Conviction is Non-Monotonic

The relationship between conviction and return is **not what we expected:**

```
4/5 > 3/5 > 5/5
```

- **4/5 is the sweet spot.** Highest avg net PnL ($18,751) and highest total contribution ($28.8M). Win rate comparable to 3/5.
- **3/5 is middle.** Respectable 62.5% win rate and $10.8K avg net. Not the weak signal we assumed.
- **5/5 is the worst.** Only 54.1% win rate and $6.2K avg net — significantly underperforms both other tiers.

The likely explanation: when all 5 indicators agree, the move has already largely happened. The signal fires at the end of the momentum, not the beginning. 4/5 captures a sweet spot where the trend is strong but one indicator diverges, indicating the move still has room to run.

### Direction Breakdown

| Votes | Dir | Trades | Win Rate | Avg Net PnL | Total Net PnL |
|-------|-------|--------|----------|-------------|---------------|
| 3/5 | LONG | 1,097 | 65.0% | $11,549 | $12,668,917 |
| 3/5 | SHORT | 1,084 | 60.0% | $9,997 | $10,836,882 |
| 4/5 | LONG | 799 | 63.5% | $18,013 | $14,392,179 |
| 4/5 | SHORT | 739 | 60.9% | $19,550 | $14,447,498 |
| 5/5 | LONG | 1,172 | 53.0% | $5,161 | $6,048,377 |
| 5/5 | SHORT | 1,202 | 55.2% | $7,166 | $8,613,081 |

The pattern holds in both directions. 4/5 dominates, 5/5 underperforms. Long trades have slightly better win rates than short at every conviction level.

## Hybrid Maker/Taker Routing Analysis

Given the conviction data, we modeled three order routing scenarios:

**Assumptions:**
- Taker fee: 3 bps
- Maker fee: 0 bps
- Maker fill rate: 68% (from live shadow data)
- Regulatory fee ($0.15/contract): applies equally to both, excluded from analysis

### Scenario Results

| Scenario | Filled (expected) | Missed | Total Fees | Net PnL | vs Baseline |
|----------|-------------------|--------|------------|---------|-------------|
| **A: All Taker (current)** | 6,093 | 0 | $20,382,916 | **$67,001,411** | — |
| **B: Hybrid** (3/5 maker, 4-5/5 taker) | 5,395 | 698 | $11,676,597 | $65,451,670 | **-$1,549,741** |
| **C: All Maker** | 4,143 | 1,950 | $0 | $59,421,342 | **-$7,580,069** |

### Hybrid Routing is Not Viable

Even Scenario B (the most conservative hybrid) **loses $1.55M** compared to all-taker. The math:

- Fee savings from routing 3/5 trades as maker: ~$8.7M
- Lost PnL from the 32% of 3/5 trades that don't fill: ~$10.3M
- **Net effect: -$1.55M**

The 3/5 trades still carry meaningful positive expected value ($10.8K avg net), so missing 32% of them costs more than the fee savings. This is true even though 3/5 trades are the "lowest conviction" tier.

## Conclusions

### 1. Conviction does not monotonically predict return

The 5/5 unanimous-agreement trades are actually the weakest performers. This is a classic **late-to-the-party** signal — when every indicator agrees, the move is likely extended and the risk/reward is worse.

### 2. Hybrid maker/taker routing is not viable

Because all conviction levels contribute positive expected value, missing any trades to save fees is net negative. The strategy's edge comes from volume and consistency, not from filtering.

### 3. Keep current all-taker routing

The all-taker approach maximizes total PnL. Fee optimization should focus on:
- Negotiating better taker rates with Coinbase as volume scales
- Reducing trade frequency only if signal quality can be improved (not routing-based)

### 4. Potential 4/5 conviction insight for future research

The 4/5 outperformance is notable and could inform future strategy refinement:
- Consider weighting position size by conviction: larger at 4/5, smaller at 5/5
- Investigate which single-indicator dissent at 4/5 is most predictive
- This is a research thread, not an operational change — needs validation on out-of-sample data

## Reproduction

```bash
uv run scripts/analyze_conviction.py
```

Requires Coinbase candle data already downloaded (`--source coinbase` from previous backtest run).
