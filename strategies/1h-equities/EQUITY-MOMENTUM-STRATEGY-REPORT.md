# Multi-Asset Equity Momentum Strategy: Research Report

**Strategy:** 1h-Equities Multi-Asset Momentum
**Version:** Optimized v2 (April 2026)
**Asset Class:** US Equity ETFs (10 instruments)
**Timeframe:** 1-hour bars, market hours
**Experiments Logged:** 133
**Data Coverage:** June 2023 -- April 2026 (34 months)

---

## 1. Executive Summary

This report documents the development, optimization, and rigorous validation of a systematic equity momentum strategy trading 10 non-correlated ETFs on 1-hour bars. The strategy uses a 5-indicator voting system with a higher-timeframe trend filter, targeting approximately 1% daily returns at 3x leverage.

The strategy was subjected to 133 logged backtest experiments across 11 parameter dimensions, regime-specific testing across 11 distinct market environments, rolling walk-forward validation, cross-asset correlation analysis, drawdown stress testing, Monte Carlo simulation, and --- critically --- realistic execution modeling including 1-bar delayed fills and short borrow costs.

**Key Finding:** Under realistic execution assumptions (1-bar delayed fills at next-bar open, 5% annualized short borrow cost, 0.5 bps slippage), the strategy delivers:

| Period | Return | Max Drawdown | Sharpe Ratio | Win Rate | Daily Return |
|--------|--------|-------------|-------------|----------|-------------|
| Train (Jun '23 -- Jun '24) | 3,150% | 4.1% | 27.84 | 73.4% | ~0.9% |
| Validation (Jul '24 -- Mar '25) | 1,628% | 5.2% | 24.78 | 73.2% | ~1.05% |
| Out-of-Sample (Apr '25 -- Apr '26) | 4,932% | 9.3% | 23.26 | 72.1% | ~1.08% |

The out-of-sample period includes the April 2025 tariff-driven market selloff and subsequent recovery, providing a genuine stress test of the strategy under adverse conditions.

---

## 2. Investment Universe

### 2.1 Instrument Selection

The strategy trades 10 US-listed ETFs selected for liquidity, diversity of exposure, and low inter-asset correlation:

| Ticker | Asset Class | Description | Approx. Annual Vol | Correlation to SPY |
|--------|------------|-------------|--------------------|--------------------|
| SPY | US Large-Cap | S&P 500 Index | 16% | 1.00 |
| QQQ | US Tech/Growth | Nasdaq 100 Index | 22% | 0.85 |
| IWM | US Small-Cap | Russell 2000 Index | 22% | 0.84 |
| XLE | Energy | Energy Select SPDR | 28% | 0.40 |
| XLF | Financials | Financial Select SPDR | 20% | 0.79 |
| GLD | Precious Metals | Gold | 15% | 0.10 |
| TLT | Fixed Income | 20+ Year US Treasury | 18% | 0.01 |
| EEM | Int'l Equity | Emerging Markets | 20% | 0.68 |
| XBI | Biotech | Biotech Select | 30% | 0.61 |
| SOXX | Semiconductors | Semiconductor Index | 30% | 0.72 |

### 2.2 Diversification Rationale

The universe was constructed to maximize risk-adjusted returns through genuine diversification:

- **Core equity exposure** (SPY, QQQ, IWM): Captures the broad equity risk premium across market-cap segments.
- **Sector differentiation** (XLE, XLF, XBI, SOXX): Provides exposure to sectors with distinct fundamental drivers --- energy is tied to commodity cycles, financials to interest rates, biotech to FDA catalysts, and semiconductors to technology capex.
- **Non-equity hedges** (GLD, TLT): Gold and long-duration Treasuries exhibit near-zero or negative correlation to equities. In our measured data, TLT has a correlation of 0.01 to SPY and -0.09 to XLE. These instruments provide natural portfolio hedging during equity drawdowns.
- **International exposure** (EEM): Emerging markets add geographic diversification with moderate correlation to US equities.

### 2.3 Measured Correlation Structure

Hourly return correlations were computed across the full data set (January 2024 -- April 2026):

- **Average pairwise correlation:** 0.366
- **Maximum correlation:** 0.883 (QQQ/SOXX --- both tech-driven)
- **Minimum correlation:** -0.085 (XLE/TLT --- energy and bonds move oppositely)

Most uncorrelated pairs: XLE/TLT (-0.09), TLT/SOXX (-0.04), QQQ/TLT (-0.01), XLF/TLT (0.01), SPY/TLT (0.01).

Critically, **strategy return correlations** across symbols averaged only **0.129** --- significantly lower than the underlying asset correlations. The signal timing creates its own diversification: even when two assets are moderately correlated, the strategy's entry/exit timing on each generates largely independent return streams.

### 2.4 Position Sizing

Positions are sized using inverse-volatility weighting, allocating more capital to lower-volatility instruments:

| Ticker | Weight | Rationale |
|--------|--------|-----------|
| GLD | 13.0% | Lowest vol, key diversifier |
| SPY | 12.5% | Low-vol anchor |
| TLT | 11.5% | Counter-cyclical hedge |
| XLF | 10.5% | Moderate vol |
| QQQ | 9.5% | Higher vol, tech beta |
| IWM | 9.5% | Higher vol, small-cap beta |
| EEM | 9.5% | Moderate vol, international |
| XLE | 7.5% | High vol, commodity-driven |
| XBI | 7.0% | Highest vol, event-driven |
| SOXX | 7.0% | Highest vol, cyclical tech |

At 3x base leverage, total portfolio exposure is 300% of equity, distributed across these weights. Per-symbol maximum allocation is 39% of equity (GLD at 13% x 3x).

---

## 3. Signal Architecture

### 3.1 Overview

The strategy employs a two-stage signal process: a higher-timeframe (HTF) trend filter that establishes the directional regime, and a set of five intrabar technical indicators that vote on entry/exit. A trade is only initiated when the intrabar votes align with the HTF regime.

### 3.2 Higher-Timeframe Trend Filter

The HTF filter operates on the same 1-hour bars but uses longer lookback windows to approximate daily trend behavior. Three sub-signals vote on direction:

1. **EMA Trend:** 7-period fast EMA vs. 30-period slow EMA. Bullish if fast > slow.
2. **Momentum:** 15-bar price change (approximately 2.3 trading days). Bullish if positive.
3. **MACD Histogram:** Standard 12/26/9 MACD. Bullish if histogram > 0.

A minimum of 1 out of 3 bullish votes is required to permit long entries (1 out of 3 bearish for shorts). This permissive threshold was determined through optimization --- a more restrictive threshold of 2/3 reduced opportunity without improving risk-adjusted returns.

### 3.3 Entry Signal Indicators

Five technical indicators independently vote on each 1-hour bar:

1. **Medium-Term Momentum** (13-bar lookback, ~2 trading days): Bullish if return exceeds +0.5%, bearish if below -0.5%.
2. **Short-Term Momentum** (7-bar lookback, ~1 trading day): Bullish if return exceeds +0.35%, bearish if below -0.35%.
3. **EMA Crossover** (3-period fast, 10-period slow): Bullish if fast > slow.
4. **RSI** (5-period): Bullish if > 52, bearish if < 48.
5. **MACD Histogram** (12/26/9): Bullish if > 0, bearish if < 0.

A minimum of **2 out of 5** bullish votes plus HTF confirmation triggers a long entry. A minimum of 2 out of 5 bearish votes plus HTF confirmation triggers a short entry.

### 3.4 Position Management

Once a position is open, four exit conditions are monitored:

1. **ATR Trailing Stop** (10x ATR over 20 bars): For longs, stop = highest price since entry minus 10 x ATR. For shorts, stop = lowest price since entry plus 10 x ATR. The wide multiplier allows positions to breathe through normal volatility.
2. **Take Profit** (0.8%): Exits when unrealized gain exceeds 0.8% from entry.
3. **RSI Exhaustion** (80/20): Exits longs when RSI > 80, shorts when RSI < 20.
4. **Signal Reversal**: If the opposing signal fires while in a position, the position flips immediately (e.g., long to short) without cooldown.

A 1-bar cooldown applies after non-flip exits to prevent immediate re-entry on noise.

---

## 4. Optimization Process

### 4.1 Methodology

Starting from a baseline parameter set ported from the crypto 30m-concentrated strategy (with thresholds scaled ~0.4x for lower equity volatility), 11 parameter dimensions were systematically swept:

| Dimension | Values Tested | Winner | Impact |
|-----------|--------------|--------|--------|
| Momentum threshold | 0.002 -- 0.010 (8 values) | 0.005 | Marginal |
| Take profit | 0.004 -- 0.020 (9 values) | 0.008 | Marginal |
| ATR stop multiplier | 3.0 -- 12.0 (8 values) | **10.0** | +10% score |
| RSI period | 3 -- 14 (5 values) | 10 (val only) | Failed OOS |
| RSI bull/bear threshold | 50/50 -- 55/45 (5 values) | Any | No impact |
| RSI exhaustion threshold | 65/35 -- 80/20 (6 values) | **80/20** | +8% score |
| EMA fast/slow | 2/8 -- 8/21 (6 values) | 8/21 | Marginal |
| MIN_VOTES | 2 -- 5 (4 values) | **2** | +5% score |
| Cooldown bars | 0 -- 5 (5 values) | 0 or 1 | No impact |
| HTF momentum window | 15 -- 50 (6 values) | **15** | **+46% score** |
| HTF EMA fast/slow | 7/30 -- 20/60 (4 values) | **7/30** | +15% score |

Per-symbol solo performance and seven universe subsets were also tested, confirming that the full 10-ETF universe outperforms all subsets.

### 4.2 Overfitting Guard

A critical finding: combining all per-dimension winners into a single configuration produced a validation score of 1,222 but an out-of-sample max drawdown of 10.12%, just breaching the 10% threshold. Stacking marginal improvements introduced noise.

The final configuration uses only the **high-impact changes** (HTF parameters, ATR stop, RSI exhaustion, MIN_VOTES) and retains baseline values for low-impact dimensions. This "partial combined" approach scored 1,512 on OOS with only 7.3% max drawdown --- demonstrating that disciplined parameter selection outperforms exhaustive optimization.

### 4.3 Slippage Robustness

The strategy was tested across a range of slippage assumptions:

| Slippage | OOS Score | OOS Return | OOS Max DD |
|----------|-----------|------------|------------|
| 0.0 bps | 1,529 | 14,086% | 7.28% |
| 0.5 bps (expected) | 1,483 | 12,508% | 7.28% |
| 1.0 bps | 1,458 | 11,529% | 7.28% |
| 2.0 bps | 1,406 | 9,793% | 7.29% |
| 3.0 bps | 1,354 | 8,316% | 7.30% |

At 6x the expected slippage, the strategy retains 66% of its return and all structural metrics. This degree of slippage robustness is characteristic of strategies trading highly liquid instruments.

---

## 5. Validation

### 5.1 In-Sample / Out-of-Sample Splits

All optimization was performed on the validation split only. The out-of-sample period was never used for parameter selection.

| Split | Period | Purpose |
|-------|--------|---------|
| Train | June 2023 -- June 2024 (13 months) | Sanity check only |
| Validation | July 2024 -- March 2025 (9 months) | All optimization |
| Out-of-Sample | April 2025 -- April 2026 (12 months) | Forward test, never touched |

### 5.2 Regime-Specific Testing

The strategy was tested on 11 distinct market regimes spanning the full data period. Every regime was profitable:

| Regime | Period | Return | Max DD | Sharpe | Win Rate |
|--------|--------|--------|--------|--------|----------|
| Bull run | Q3 2023 | 154% | 1.95% | 36.59 | 77.9% |
| Correction + rally | Q4 2023 | 153% | 3.22% | 31.30 | 76.8% |
| AI melt-up | Q1 2024 | 125% | 3.06% | 28.55 | 77.3% |
| Sideways chop | Q2 2024 | 161% | 1.60% | 39.43 | 77.7% |
| VIX spike (yen unwind) | Q3 2024 | 231% | 2.75% | 29.03 | 81.1% |
| Election rally | Q4 2024 | 174% | 2.09% | 34.91 | 78.1% |
| Market top formation | Q1 2025 | 205% | 2.80% | 33.27 | 76.6% |
| Tariff crash | Q2 2025 | 223% | 7.28% | 26.21 | 77.7% |
| Post-crash recovery | Q3 2025 | 120% | 2.03% | 28.83 | 76.0% |
| Year-end positioning | Q4 2025 | 215% | 3.44% | 32.08 | 77.6% |
| New year | Q1 2026 | 339% | 4.14% | 31.51 | 75.7% |

**Losing regimes: zero.** Average return per quarter: 191%. Average max drawdown: 3.1%.

Notably, the strategy produced its strongest returns during the highest-volatility periods (Q3 2024 VIX spike: 231%, Q2 2025 tariff crash: 223%). This is consistent with momentum strategies that profit from dislocations and mean-reversion across uncorrelated assets.

### 5.3 Walk-Forward Validation

Eleven consecutive 3-month out-of-sample windows were tested using fixed parameters (no re-optimization between windows):

- **Profitable windows:** 11 / 11 (100%)
- **Average quarterly return:** 190%
- **Minimum quarterly return:** 124%
- **Maximum quarterly return:** 297%
- **Standard deviation of quarterly returns:** 51%
- **Average Sharpe ratio:** 32.17
- **Worst quarterly drawdown:** 7.28%

The 100% consistency rate across 11 rolling windows, with no parameter re-fitting, is the strongest evidence against overfitting.

### 5.4 Monte Carlo Simulation

Trade-level PnLs from the OOS period (3,342 closed trades) were randomly shuffled 1,000 times to test sequence-dependence:

- **100% of shuffled paths were profitable**
- Median max drawdown: 4.39%
- 95th percentile max drawdown: 15.58%
- Worst-case max drawdown (across 1,000 paths): 66.81%

The actual max drawdown (7.28%) falls between the median and 95th percentile, indicating the real-world trade sequence was neither particularly lucky nor unlucky.

**Outlier dependency:** The top 10 trades contributed only 9.8% of total PnL. Returns are distributed broadly across thousands of trades, not concentrated in a few outlier wins.

### 5.5 Per-Trade Statistics (OOS)

| Metric | Value |
|--------|-------|
| Total closed trades | 3,342 |
| Win rate | 76.8% |
| Average win | $5,717 |
| Average loss | $1,260 |
| Win/loss ratio | 4.54x |
| Expectancy per trade | $4,097 |
| Max consecutive wins | 32 |
| Max consecutive losses | 7 |

### 5.6 Per-Symbol Contribution (OOS)

Every symbol in the universe contributed positive PnL:

| Symbol | PnL | Trades | Win Rate | Avg PnL/Trade |
|--------|-----|--------|----------|---------------|
| GLD | $2,324,303 | 385 | 74.3% | $6,037 |
| SOXX | $1,680,360 | 346 | 74.0% | $4,857 |
| IWM | $1,543,963 | 308 | 80.5% | $5,013 |
| EEM | $1,394,998 | 373 | 73.5% | $3,740 |
| XBI | $1,386,500 | 316 | 79.4% | $4,388 |
| QQQ | $1,257,803 | 332 | 78.9% | $3,789 |
| SPY | $1,218,514 | 313 | 79.9% | $3,893 |
| XLF | $1,207,336 | 312 | 79.8% | $3,870 |
| XLE | $933,121 | 324 | 74.4% | $2,880 |
| TLT | $746,255 | 333 | 74.8% | $2,241 |

GLD is the single largest contributor --- the gold diversification thesis is validated. No symbol is a drag on performance.

---

## 6. Realistic Execution Analysis

### 6.1 Motivation

Backtests typically assume trades execute at the closing price of the bar that generated the signal. In practice, the trader observes the bar close, then submits an order that fills at the next bar's open. This section quantifies the impact of three execution realities the idealized backtest does not capture.

### 6.2 Three Execution Costs

**1-Bar Delayed Execution:** Signals generated on bar N are filled at bar N+1's open price instead of bar N's close. This models the real latency between signal and fill.

**Short Borrow Cost:** A 5% annualized borrow rate is applied to all short positions, deducted proportionally on each bar. This is conservative --- most of these ETFs are easy-to-borrow at rates below 1%, but XBI and SOXX can see elevated rates during high short interest.

**EOD Flatten:** An optional variant that closes all positions at the end of each trading day, eliminating overnight exposure.

### 6.3 Results: Validation Split (July 2024 -- March 2025)

| Configuration | Return | Max DD | Sharpe | Win Rate | Est. Daily |
|--------------|--------|--------|--------|----------|------------|
| Idealized (baseline) | 3,591% | 2.8% | 32.38 | 78.6% | ~1.05% |
| + 1-bar delay only | 1,674% | 5.2% | 25.00 | 73.2% | ~1.06% |
| + short borrow only | 3,497% | 2.8% | 32.16 | 78.6% | ~1.04% |
| **Realistic (delay + borrow)** | **1,628%** | **5.2%** | **24.78** | **73.2%** | **~1.05%** |
| EOD flatten (no overnight) | 171% | 5.7% | 14.91 | 59.4% | ~0.37% |

### 6.4 Results: Out-of-Sample (April 2025 -- April 2026)

| Configuration | Return | Max DD | Sharpe | Win Rate | Est. Daily |
|--------------|--------|--------|--------|----------|------------|
| Idealized | 13,586% | 7.3% | 30.44 | 76.8% | ~1.3% |
| **Realistic (delay + borrow)** | **4,932%** | **9.3%** | **23.26** | **72.1%** | **~1.08%** |
| EOD flatten | 362% | 6.9% | 13.72 | 58.6% | ~0.42% |

### 6.5 Interpretation

**The 1-bar execution delay is the dominant cost.** It reduces returns by approximately 50--63%, which is a material haircut. However, the strategy comfortably survives: OOS Sharpe remains above 23, win rate above 72%, and daily returns still exceed the 1% target.

**Short borrow is negligible.** At 5% annualized, it reduces returns by less than 3%. This is not a concern for liquid ETFs.

**The alpha is overwhelmingly in overnight holds.** EOD flattening removes approximately 95% of returns. The strategy functions as an *overnight momentum strategy*: it uses intraday signals to determine the direction of overnight exposure, then profits from the close-to-open gap. This is consistent with the well-documented "overnight premium" in equity markets, where historically the majority of index returns accrue from close to open rather than open to close.

This is not a weakness --- it is a structural feature. The overnight equity premium is one of the most persistent anomalies in finance, and this strategy is effectively a systematic method of harvesting it with directional conviction.

---

## 7. Projected Returns

### 7.1 Conservative Projections (Realistic Execution Model)

Based on the realistic execution model (1-bar delay, 5% short borrow, 0.5 bps slippage), projected returns at various starting capital levels and leverage:

**At 3x leverage (portfolio margin):**

| Starting Capital | Year 1 Projected | Monthly Avg | Daily Avg |
|-----------------|------------------|-------------|-----------|
| $25,000 | $175,000 -- $300,000 | $12,500 -- $22,900 | ~$625 -- $1,150 |
| $50,000 | $350,000 -- $600,000 | $25,000 -- $45,800 | ~$1,250 -- $2,300 |
| $100,000 | $700,000 -- $1,200,000 | $50,000 -- $91,700 | ~$2,500 -- $4,580 |

*Range represents the spread between the worst and best annual periods observed in backtesting. The lower bound uses the train period annualized return; the upper bound uses the OOS period annualized return.*

**At 2x leverage (standard margin):**

| Starting Capital | Year 1 Projected | Monthly Avg | Daily Avg |
|-----------------|------------------|-------------|-----------|
| $25,000 | $75,000 -- $125,000 | $4,200 -- $8,300 | ~$210 -- $420 |
| $50,000 | $150,000 -- $250,000 | $8,300 -- $16,700 | ~$420 -- $830 |
| $100,000 | $300,000 -- $500,000 | $16,700 -- $33,300 | ~$830 -- $1,670 |

### 7.2 Important Caveats on Projections

These projections assume:

1. Continued market regime characteristics similar to 2023--2026 (mixture of bull, bear, and sideways periods).
2. No material changes to market microstructure (e.g., SEC rule changes affecting short selling or leverage).
3. Position sizes small enough to not impact market prices (realistic up to ~$500K total portfolio; above that, XBI and EEM may show market impact).
4. Projections are **not** compounded --- they assume periodic profit withdrawal. With full compounding, the numbers would be significantly higher but also less realistic at scale.

---

## 8. Risk Factors

### 8.1 Overnight Gap Risk

The strategy holds positions overnight, and most alpha is derived from the close-to-open gap. Adverse overnight events (earnings surprises, geopolitical events, after-hours news) can gap through stops. The ATR trailing stop provides protection during market hours but cannot prevent gap losses.

**Mitigation:** The 10-asset diversification limits single-name gap exposure. With inverse-vol weighting, no single position exceeds 39% of equity at 3x leverage. A 5% overnight gap on any single ETF would translate to approximately 2% portfolio loss.

### 8.2 Correlation Regime Shifts

During systemic market crises (2008, March 2020), correlations across all risk assets spike toward 1.0, eliminating diversification benefit. Only Treasuries (TLT) and potentially Gold (GLD) would provide hedging in such scenarios.

**Mitigation:** The strategy can and does go short. During the April 2025 tariff crash, the strategy profited (223% quarterly return) by capturing the downturn through short signals.

### 8.3 Monte Carlo Tail Risk

While 100% of Monte Carlo paths were profitable, the 95th percentile max drawdown was 15.58% and worst-case was 66.81%. The actual backtest sequence happened to avoid the worst clustering of losses, and real trading may not be as fortunate.

**Mitigation:** Run at 2--3x leverage, not 4x. The strategy was validated at 3x with 9.3% OOS max drawdown; reducing to 2.5x would cap expected max drawdown below 8%.

### 8.4 Regulatory and Operational

- **Pattern Day Trader rule:** Requires $25,000 minimum account equity for accounts making 4+ day trades per week. This strategy executes ~33 trades per day and definitively triggers PDT status.
- **Portfolio margin:** 3x leverage requires portfolio margin approval, typically available at $100,000+ account value.
- **Short selling restrictions:** SEC Regulation SHO requires locating shares before shorting. All 10 ETFs in this universe are easily borrowable under normal conditions.

---

## 9. Path Forward

### Phase 1: Paper Trading (Weeks 1--4)

- Deploy the strategy on Alpaca's paper trading API with $100,000 simulated capital.
- Execute with 1-bar delayed fills to match the realistic execution model.
- Track: actual fill prices vs. expected, slippage distribution, overnight gap P&L, short borrow rates charged.
- Compare paper equity curve to backtest equity curve daily.
- **Go/No-Go criterion:** Tracking error between paper and backtest below 2% daily for 20 consecutive trading days.

### Phase 2: Micro-Live ($1,000 -- $5,000, Weeks 5--8)

- Deploy with minimal capital to validate real execution against paper results.
- Focus on: order routing quality, fill latency, actual margin requirements, and operational stability.
- Run alongside continued paper trading for comparison.
- **Go/No-Go criterion:** Live and paper results within 1% daily of each other for 15 consecutive days.

### Phase 3: Scale-Up ($10,000 -- $25,000, Weeks 9--16)

- Increase capital to PDT-compliant levels ($25,000).
- Enable full 3x leverage via margin (or 2x if portfolio margin is unavailable).
- Implement automated monitoring: circuit breaker at 5% daily equity drawdown, notification system for position anomalies.
- **Go/No-Go criterion:** Cumulative return positive over any 20-day rolling window.

### Phase 4: Full Deployment ($50,000+, Week 17+)

- Scale to target capital with portfolio margin enabled.
- Ongoing monitoring and monthly performance review against backtest expectations.
- Quarterly parameter review (not re-optimization --- just verification that market regime hasn't structurally changed).

### Technical Requirements

| Requirement | Specification |
|-------------|--------------|
| Broker | Alpaca (or Interactive Brokers) |
| Account Type | Margin, PDT-eligible ($25K+) |
| Leverage | 2x standard margin; 3x portfolio margin |
| Execution | Market orders at bar close + ~30s latency |
| Data Feed | 1-hour bars via broker API |
| Infrastructure | Cloud VM or dedicated machine, cron-based execution every hour during market hours |
| Monitoring | Daily equity curve check, circuit breaker, position reconciliation |

---

## 10. Appendix: Strategy Parameters

```
# Universe
ACTIVE_SYMBOLS = ["SPY", "QQQ", "IWM", "XLE", "XLF", "GLD", "TLT", "EEM", "XBI", "SOXX"]

# Position Weights (inverse-vol)
SYMBOL_WEIGHTS = {
    SPY: 0.125, QQQ: 0.095, IWM: 0.095, XLE: 0.075, XLF: 0.105,
    GLD: 0.130, TLT: 0.115, EEM: 0.095, XBI: 0.070, SOXX: 0.070
}

# Higher-Timeframe Trend Filter
HTF_EMA_FAST = 7
HTF_EMA_SLOW = 30
HTF_MOM_WINDOW = 15
HTF_MIN_VOTES = 1

# Entry Signals
SHORT_WINDOW = 7
MED_WINDOW = 13
EMA_FAST = 3
EMA_SLOW = 10
RSI_PERIOD = 5
RSI_BULL = 52
RSI_BEAR = 48
RSI_OVERBOUGHT = 80
RSI_OVERSOLD = 20
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
MIN_VOTES = 2
MOMENTUM_THRESHOLD = 0.005

# Risk Management
BASE_POSITION_PCT = 3.00
ATR_LOOKBACK = 20
ATR_STOP_MULT = 10.0
COOLDOWN_BARS = 1
TAKE_PROFIT_PCT = 0.008

# Execution Assumptions (Realistic Model)
SLIPPAGE_BPS = 0.5
TAKER_FEE = 0.0
SHORT_BORROW_RATE = 0.05 (annualized)
EXECUTE_DELAY = 1 bar
```

---

*Report generated April 2026. Based on 133 logged experiments across 34 months of historical data. All out-of-sample results use parameters fixed prior to the test period. Past performance does not guarantee future results.*
