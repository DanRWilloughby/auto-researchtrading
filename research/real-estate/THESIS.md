# Investment Thesis Validation — Paterson, NJ
## For Veloce Capital + Forte Investment Fund

---

## The Core Thesis

Paterson multifamily properties are systematically undervalued relative to their income potential. A data-driven value-add operator (Veloce) acquiring distressed or absentee-owned properties, renovating them, and stabilizing rents can generate **1.5-2.5x equity multiples** with manageable risk — and a Reg A fund (Forte) can democratize access to these returns.

---

## How to Validate (The "Backtest")

Real estate doesn't have a replay button like markets. Instead, we validate using:

### 1. Historical Transaction Analysis (Retrospective)

**What:** Pull 3-5 years of Passaic County deed transfers and compare properties that were sold, renovated, and resold.

**Data source:** NJ SR1A records (deed transfers) are public. We can download historical sales from the same ArcGIS MOD-IV service, filtered by sale date ranges.

**Method:**
```
For each property that sold twice in 2020-2026:
  - First sale price (acquisition)
  - Building permits filed between sales (renovation proxy)
  - Second sale price (exit)
  - Time between sales
  - Calculate: actual equity multiple, actual IRR
  - Compare to what our model WOULD HAVE predicted at time of first sale
```

**This tells us:** How accurate are our return projections? If we predicted 2.0x and actuals cluster around 1.8x, we have a reliable but slightly optimistic model. If actuals are 3.0x, our model is conservative.

### 2. Rent Growth Backtest

**What:** Compare historical rent estimates (HUD FMR, ZORI) to actual achieved rents for renovated vs unrenovated properties.

**Data source:**
- Historical ZORI (Zillow publishes monthly data back to 2015)
- Historical HUD FMR (published annually, archives available)
- Paterson rent control registry (caps on regulated units)

**Method:**
```
For each ZIP in Paterson:
  - Plot ZORI trend 2015-2026
  - Plot HUD FMR trend 2015-2026
  - Calculate: compound annual rent growth rate
  - Compare renovated rent premium to our 20% assumption
```

**This tells us:** Is our 20% renovation premium realistic? Is Paterson rent growth accelerating or decelerating?

### 3. Comparable Deal Analysis (Veloce's Own Portfolio)

**What:** Analyze Veloce's actual completed projects to calibrate the model.

**Data needed from Veloce:**
- Acquisition price for each project
- Renovation cost (actual)
- Renovation timeline
- Stabilized NOI
- Current appraised value or exit price
- Vacancy during and after renovation

**Method:**
```
For each Veloce project:
  - What would our model have predicted?
  - How close was the prediction to actual?
  - Where did the model over/underestimate?
  - Calibrate: adjustment factors for renovation cost, rent premium, timeline
```

**This is the most valuable validation** — it calibrates the model against Veloce's actual execution capability.

### 4. Market Timing Analysis

**What:** Would buying at different entry points (2018, 2020, 2022, 2024) have produced different outcomes?

**Data source:** Historical ZHVI (Zillow Home Value Index), historical cap rates, historical interest rates.

**Method:**
```
Simulate buying the "top 20 deals" from each year:
  - Use historical assessed values and rents at that time
  - Apply same value-add model
  - Project forward to 2026
  - Compare projected vs actual outcomes
```

**This tells us:** Are we buying at the top, or is there still upside? How sensitive are returns to entry timing?

### 5. Stress Testing

**What:** How do returns hold up under adverse scenarios?

**Scenarios:**
| Scenario | Impact |
|----------|--------|
| Rent decline 10% | Model with 10% lower rents across the board |
| Vacancy spike to 15% | Double the vacancy assumption |
| Interest rates +200bps | Higher carry costs during renovation |
| Renovation cost +30% | Materials/labor inflation |
| Assessment reassessment | NJ county-wide revaluation → higher taxes |
| Rent control expansion | Cap rent increases at 3% annually |

**Method:**
```
For each scenario, re-run the scoring pipeline with modified assumptions.
Track: how many of the top 100 deals are still >1.0x equity multiple?
```

---

## Building the Investment Thesis

### Data Points We Can Prove

1. **Supply/demand imbalance**: Paterson is 74% renter-occupied. 485 active rental listings (Zillow) vs high demand.

2. **NYC spillover pricing**: Paterson median rent ($2,000/2BR) is 40% below Fair Lawn ($3,330) and 30% below Bergen County FMR. Gap creates natural demand floor.

3. **Assessment arbitrage**: Median assessed value ($196K) is 55% of market value ($356K). NJ assessments lag market by years — informed buyers who can estimate true value have an edge.

4. **Absentee owner concentration**: 75% of Paterson properties are absentee-owned. This creates a large pool of potentially motivated sellers who may accept below-market pricing, especially out-of-state owners (4% = 712 properties).

5. **Distress pipeline**: 27 active sheriff sales, 6,135 properties with distress score ≥ 3. This is an active foreclosure market with regular deal flow.

6. **Value-add spread**: Median as-is cap rate 5.1% vs 6.5% exit cap rate assumption. The spread exists because current rents are suppressed by deferred maintenance and suboptimal management.

### What Veloce Needs to Prove

1. **Renovation execution**: That they can consistently renovate at $55/sqft or below
2. **Rent achievement**: That renovated units actually command the 20% premium
3. **Timeline**: That renovations complete in 6-9 months, not 12-18
4. **Tenant quality**: That renovated units attract stable tenants at market rents
5. **Scale capacity**: That they can manage 5-10 simultaneous projects

### What Forte Needs to Present to Investors

1. **Track record**: Veloce's actual deal-by-deal returns
2. **Market data**: This pipeline's output — showing 19,572 scored properties, ongoing deal flow
3. **Risk factors**: Flood zones, rent control, NJ tax burden
4. **Differentiation**: Data-driven sourcing vs gut feel. "We score every parcel in Paterson, not just what brokers show us."

---

## Implementation: Backtest Script

### Phase 1: Historical Sales Analysis
Pull 5 years of deed transfers, calculate actual returns for properties that flipped.

### Phase 2: Model Calibration
Compare predictions to actuals, compute error distribution, adjust weights.

### Phase 3: Stress Test Suite
Run pipeline under 6 adverse scenarios, produce sensitivity table.

### Phase 4: Monthly Reporting
Track model predictions vs realized outcomes over time. Build a track record of prediction accuracy.

---

## Key Metrics to Track Over Time

| Metric | Current | Update Frequency |
|--------|---------|-----------------|
| Median market value | $356K | Weekly |
| Median cap rate | 5.1% | Weekly |
| Active sheriff sales | 27 | Weekly |
| ZORI rent trend | $2,000/2BR | Monthly |
| Vacancy rate | 7% | Annually (ACS) |
| New building permits | TBD | Monthly |
| Properties scored | 19,572 | Weekly |
| Equity multiple > 1.5x | 4,367 | Weekly |
