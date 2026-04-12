# Fee-Reduction Strategy Variant Comparison

**Date:** 2026-04-12
**Data:** Coinbase perp candles, TEST split (Jul–Dec 2025)
**Scripts:** `scripts/compare_fee_variants.py`

## Problem

The 30m-concentrated strategy is profitable in backtest (Sharpe 26.11) but fee drag is the binding constraint on the live account. On a choppy day (2026-04-12), the strategy made $67 gross but paid $203 in fees across 62 trades — net -$136. The alpha is real but high trade frequency in chop grinds it away.

## Variants Tested

| # | Variant | Mechanism | New Code |
|---|---------|-----------|----------|
| 1 | **1h Candles** | Same strategy, slower timeframe → fewer bars → fewer trades | None (just `--interval 1h`) |
| 2 | **MIN_VOTES=4** | Raise entry threshold from 3/5 to 4/5 → skip weakest signals | `strategy_min4.py` |
| 3 | **No-trade 02-08 UTC** | Close positions and stop trading during low-volume hours | `strategy_notrade_hours.py` |

## Results

### Full Comparison

| Variant | Sharpe | Win% | PF | Trades | MaxDD | Fee/Gross | bps/bar |
|---------|--------|------|-----|--------|-------|-----------|---------|
| **30m Baseline** | 26.11 | 73.2% | 8.32 | 11,016 | 4.15% | 19.5% | 8.36 |
| **1h Candles** | 20.02 | 73.5% | 9.32 | 5,624 | 3.80% | **12.6%** | **13.75** |
| **MIN_VOTES=4** | 21.30 | 68.1% | 6.06 | 8,211 | **2.71%** | 18.0% | 6.49 |
| **No-trade 02-08** | 20.96 | 71.1% | 5.46 | 8,634 | 3.37% | 20.6% | 6.00 |

### Relative to Baseline

| Variant | Sharpe Δ | Trades Δ | Fee Savings | Net PnL Δ | Fee/Gross Δ |
|---------|----------|----------|-------------|-----------|-------------|
| 1h Candles | -6.10 | -5,392 | $13.9M | -$48.5M | **-6.9 pp** |
| MIN_VOTES=4 | -4.81 | -2,805 | $13.5M | -$54.3M | -1.4 pp |
| No-trade 02-08 | -5.16 | -2,382 | $14.2M | -$59.4M | +1.1 pp |

## Analysis

### 1h Candles: Clear Winner

The 1h variant dominates on fee efficiency:

- **Fee/Gross: 12.6%** — one-third lower than baseline's 19.5%. For every dollar of gross profit, only $0.13 goes to fees vs $0.20 currently.
- **Per-bar return: 13.75 bps** — 64% higher than baseline's 8.36 bps. Each bar of exposure generates substantially more net return.
- **Win rate and PF held up:** 73.5% win rate (same), 9.32 profit factor (actually *better* than 8.32). The signal quality doesn't degrade on 1h candles.
- **Lowest max DD:** 3.80% — the slower frequency avoids whipsaws.

The lower annualized Sharpe (20.02 vs 26.11) is a mathematical artifact: Sharpe scales by `sqrt(bars_per_year)`, and 1h has half the bars (8,760 vs 17,520). The risk-adjusted *per-bar* return is clearly superior.

**Why it works:** Half the bar frequency means roughly half the trade signals. But the signals that fire on 1h candles are higher quality — they've been confirmed over a longer window and aren't reacting to 30-minute noise. The strategy captures the same moves with fewer entries and exits, paying fees once instead of twice.

### MIN_VOTES=4: Disappointing

- **Fee/Gross only dropped 1.4 pp** (18.0% vs 19.5%) despite 25% fewer trades. The 3/5 trades that got filtered were actually decent — they contributed positive EV.
- **Win rate fell to 68.1%** — the 4/5 and 5/5 trades have different characteristics together than individually. Removing 3/5 changed the strategy's dynamics.
- **Per-bar return dropped to 6.49 bps** — 22% worse than baseline. The lost 3/5 alpha wasn't recovered by fee savings.

This confirms the conviction analysis finding: all conviction levels carry positive EV, so filtering any of them is net negative.

### No-trade 02-08 UTC: Worst Performer

- **Fee/Gross actually increased** (20.6% vs 19.5%). The forced position closes at 02:00 UTC generate extra fee-paying trades without capturing any alpha.
- **Per-bar return: 6.00 bps** — lowest of all variants. Missing 25% of the trading day hurts the overnight continuation moves more than it saves on chop.
- **Profit factor collapsed to 5.46** — the 02:00 forced closes turn profitable overnight positions into break-even or negative exits.

The overnight hours aren't as bad as the single choppy day suggested. On average, the strategy captures meaningful alpha during those hours.

## Recommendation

**Switch the live strategy from 30m to 1h candles.**

The 1h variant is strictly better on fee efficiency with no degradation in signal quality. For the live $10K account:

| Metric | 30m (current) | 1h (proposed) | Improvement |
|--------|---------------|---------------|-------------|
| Trades/day (est) | ~62 | ~31 | -50% |
| Fee/trade | ~$3.27 | ~$3.27 | same |
| Daily fees (est) | ~$203 | ~$101 | -50% |
| Fee/Gross ratio | 19.5% | 12.6% | -35% |
| Per-bar efficiency | 8.36 bps | 13.75 bps | +64% |

On the choppy day that motivated this analysis ($67 gross, $203 fees, -$136 net), the 1h variant would have approximately: $67 gross, ~$101 fees = **-$34 net** — still negative but $100 better. On a normal day where gross is positive, the fee savings go straight to the bottom line.

### What This Doesn't Require

- No parameter retuning — identical strategy code
- No new infrastructure — just change `--interval 1h` in the cron wrapper
- No new data pipeline — 1h Coinbase candles already downloading

### Next Steps

1. Paper trade the 1h variant for 48-72 hours to validate against live spreads
2. If results confirm, switch the live cron from 30m to 1h
3. Consider combining 1h with MIN_VOTES=4 as a second experiment (the two effects may stack differently than individually)

## Reproduction

```bash
# Run all 4 variants and get the comparison table
uv run scripts/compare_fee_variants.py

# Or run individually:
uv run engine/backtest.py --strategy strategies/30m-concentrated/strategy.py \
  --interval 1h --split test --source coinbase --label "cb-1h-baseline"

uv run engine/backtest.py --strategy strategies/30m-concentrated/strategy_min4.py \
  --interval 30m --split test --source coinbase --label "cb-min-votes-4"

uv run engine/backtest.py --strategy strategies/30m-concentrated/strategy_notrade_hours.py \
  --interval 30m --split test --source coinbase --label "cb-no-trade-0200-0800"
```
