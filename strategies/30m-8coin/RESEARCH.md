# 30m 8-Coin Strategy Research

Created: 2026-03-23
Branch: `autotrader/30m-exp1`
Base: `strategies/30m-btc-eth-sol/strategy.py` (champion 3-coin)

## Origin

The 30m strategy was developed by adapting the validated 1h-btc-eth-sol champion (Sharpe 21.16 val, 18.20 test) to 30-minute bars. Lookback windows were scaled ~2x to cover equivalent time periods. The 3-coin version (`30m-btc-eth-sol`) was validated first, then expanded to 8 coins.

## 3-Coin vs 8-Coin Comparison

Both use `BASE_POSITION_PCT = 0.08`. The 3-coin version allocates 2.64% of equity per coin (0.08 * 0.33), while 8-coin allocates 1.0% per coin (0.08 * 0.125). Total max exposure is identical at 8%.

### Regime Tests

| Regime | Window | 3-coin Score | 8-coin Score | 3-coin DD | 8-coin DD |
|--------|--------|-------------|-------------|-----------|-----------|
| Val baseline | Jul '24–Mar '25 | 24.41 | **29.93** | 0.21% | **0.17%** |
| Bear | Oct–Nov '25 | 21.00 | **24.08** | 0.25% | 0.24% |
| Flat | Jul–Sep '25 | 23.46 | **28.49** | 0.14% | **0.07%** |
| Choppy | Mar–May '24 | 23.23 | **32.58** | 0.42% | **0.09%** |

8-coin consistently delivers +25-40% better Sharpe and ~50% lower max drawdown. The diversification benefit is real — uncorrelated entry/exit timing across more assets smooths the equity curve.

### Trade-off

Absolute returns are lower because per-coin position size drops from 2.64% to 1.0%. The same edge extracts less P&L per trade. This is addressable by increasing `BASE_POSITION_PCT`.

## Position Sizing Sweep (8-coin, val split)

Tested 4 levels on validation data (6 coins loaded — XRP/SUI lack 30m history):

| BASE_POSITION_PCT | Score | Sharpe | Return | Max DD | End Equity |
|---|---|---|---|---|---|
| **0.08** (current) | 29.93 | 29.93 | +82% | 0.17% | $182K |
| **0.12** | 29.47 | 29.75 | +144% | 0.26% | $244K |
| **0.16** | 28.86 | 29.58 | +226% | 0.35% | $326K |
| **0.21** | 27.89 | 29.36 | +365% | 0.46% | $465K |

Across all sizes: 19,958 trades, 81.1% win rate, ~16.8 profit factor (unchanged — sizing doesn't affect signal quality).

### Key findings

- No cliff in the curve — drawdown scales linearly, returns compound super-linearly
- Even at 0.21, max drawdown is only 0.46% and Sharpe is 29.4
- Score penalty is mainly from the turnover term (bigger positions = higher dollar turnover)
- At 0.21 with 8 coins, max simultaneous exposure is ~21% vs 20x leverage cap — plenty of headroom
- Correlation risk is the main concern: crypto liquidation cascades hit all 8 coins at once, partially offsetting the diversification benefit

## Data Limitations

- XRP and SUI only have ~3 weeks of 30m data from Hyperliquid
- Backtests effectively run on 6 coins (BTC, ETH, SOL, DOGE, AVAX, LINK)
- DOGE/AVAX/LINK have full historical coverage matching BTC/ETH/SOL

## Open Questions

- [ ] Optimal position sizing: 0.08 (conservative) vs 0.12 (balanced) vs higher
- [ ] BB compression signal fires ~90% of the time — weak discriminator, could be replaced
- [ ] Full XRP/SUI 30m data needs alternative source for proper backtesting
- [ ] Deploy as separate $100K paper portfolio alongside 1h strategy

## Deployment Plan

Recommended: separate paper trader with independent $100K budget, separate state file (`paper_state_30m.json`), cron at `*/30 * * * *`. Separate portfolio avoids position conflicts with the 1h strategy and enables clean attribution.
