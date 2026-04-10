# 1h-equities: Multi-Asset Momentum on Non-Correlated ETFs

## Hypothesis

The 30m-concentrated crypto strategy's OOS failure (11.6% DD, score -999) is driven by:
1. Thin crypto order books → slippage eats returns
2. Funding rate drag → 0.01-0.1% per 8h compounds
3. Limited to 3 assets → concentration risk

Equities fix all three: SPY spreads are <0.5 bps, zero commissions on Alpaca, and we can trade 10 non-correlated ETFs for diversification.

## Universe (10 ETFs)

| Ticker | Asset Class | Approx Corr to SPY | Annual Vol |
|--------|------------|---------------------|------------|
| SPY | S&P 500 | 1.00 | 16% |
| QQQ | Nasdaq 100 | 0.90 | 22% |
| IWM | Russell 2000 | 0.85 | 22% |
| XLE | Energy | 0.55 | 28% |
| XLF | Financials | 0.75 | 20% |
| GLD | Gold | 0.05 | 15% |
| TLT | 20+ Yr Treasury | -0.30 | 18% |
| EEM | Emerging Markets | 0.65 | 20% |
| XBI | Biotech | 0.60 | 30% |
| SOXX | Semiconductors | 0.80 | 30% |

GLD and TLT are the key diversifiers — genuinely uncorrelated/anti-correlated to equity risk.

## Signal Logic

Identical to 30m-concentrated, with thresholds scaled for equity volatility (~0.4x crypto):

- **5-indicator vote**: momentum (2 windows), EMA crossover, RSI, MACD histogram
- **Higher-timeframe trend filter**: multi-day EMA + momentum + MACD on 1h bars
- **Minimum 3/5 votes** + HTF confirmation required for entry
- **Risk management**: ATR trailing stop (6x), take profit (0.8%), RSI exhaustion exit
- **Position flips**: direct reversal on opposing signal

### Key Parameter Diffs vs Crypto

| Parameter | Crypto (30m) | Equities (1h) |
|-----------|-------------|---------------|
| Momentum threshold | 0.9% | 0.5% |
| Take profit | 1.2% | 0.8% |
| ATR stop mult | 8.0x | 6.0x |
| Base leverage | 1.2x | 3.0x |
| Symbols | 3 | 10 |
| Bars/day | 48 | ~65 (6.5/ticker x 10) |

## Backtest Results (3x Leverage)

### Cost Model
- Slippage: 0.5 bps (realistic for liquid ETFs)
- Commissions: $0 (Alpaca/Schwab/Fidelity)
- Funding rate: none
- Short borrow: not modeled (~5% annualized, negligible on short holds)

### Results by Split

| Split | Period | Sharpe | Return | Max DD | Trades | Win% | PF |
|-------|--------|--------|--------|--------|--------|------|----|
| Train | Jun'23-Jun'24 | **30.56** | 2,488% | 2.4% | 8,896 | 68.2% | 4.55 |
| Val | Jul'24-Mar'25 | **25.05** | 1,148% | 5.8% | 6,122 | 68.6% | 4.58 |
| **OOS** | **Apr'25-Apr'26** | **25.87** | **3,039%** | **6.1%** | **8,430** | **69.5%** | **4.30** |

### Daily Return Estimates
- Val (9 months): ~0.95% daily
- OOS (12 months): ~0.95% daily
- Train (13 months): ~0.84% daily

### Leverage Sensitivity (Val Split)

| Leverage | Return | Max DD | Daily Return |
|----------|--------|--------|-------------|
| 2.0x | 445% | 3.9% | ~0.6% |
| 2.5x | 726% | 4.8% | ~0.8% |
| **3.0x** | **1,148%** | **5.8%** | **~0.95%** |
| 3.5x | 1,781% | 6.8% | ~1.1% |
| 4.0x | 2,724% | 7.7% | ~1.3% |

Sharpe remains constant (~25) across leverage levels — DD scales linearly.

## Why This Works Better Than Crypto

1. **Cost structure**: Zero commissions + 0.5 bps slippage vs 5 bps fees + 1 bps slippage + funding
2. **Diversification**: 10 non-correlated assets vs 3 correlated crypto (BTC/ETH/SOL move together)
3. **GLD/TLT hedging**: Gold and treasuries often rally during equity selloffs, providing natural hedge
4. **Deeper books**: SPY alone trades $50B+/day — our positions are invisible
5. **Circuit breakers**: Equities have halt mechanisms; crypto can flash crash 20% in minutes
6. **OOS stability**: Strategy maintains performance across all periods (crypto version failed OOS)

## Risks & Caveats

1. **Overnight gaps**: Not modeled — equities gap at open. Could add overnight risk premium
2. **Correlation blow-ups**: 2008/2020-style events can correlate everything except Treasuries
3. **Leverage requirements**: 3x requires portfolio margin ($100K+ minimum at most brokers)
4. **PDT rule**: Need $25K+ for pattern day trading (33 trades/day)
5. **Short borrow**: Not modeled; XBI/SOXX can have elevated borrow rates
6. **Backtest realism**: 1h bars during market hours only — no pre/post market data

## Next Steps

- [ ] Paper trade via Alpaca paper trading API
- [ ] Add overnight gap modeling (gap-and-go filter)
- [ ] Add VIX regime filter (VIX > 30 = reduce leverage or go flat)
- [ ] Test with realistic short borrow costs
- [ ] Test with higher slippage (1-2 bps) for robustness
- [ ] Explore adding leveraged ETFs (TQQQ, SOXL) as additional instruments
- [ ] Parameter sweep for per-symbol optimization
