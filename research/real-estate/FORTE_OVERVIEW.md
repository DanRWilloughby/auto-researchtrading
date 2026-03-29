# Paterson Deal Intelligence Platform
## Overview for Forte Investment Fund

---

## What It Is

An automated research platform that continuously scans every property in Paterson, NJ — over 20,000 parcels — and identifies the highest-return value-add investment opportunities. It pulls data from 7 public sources, scores each property on return potential and acquisition likelihood, and surfaces the best deals daily.

Think of it as the quantitative research layer behind Veloce's deal sourcing. Instead of waiting for brokers to show you deals, the platform finds them before they hit the market.

---

## What It Does

### 1. Scores Every Property in Paterson

Every day, the platform evaluates 20,551 investable properties across five dimensions:

- **Return potential** — Estimated cap rate, equity multiple, and cash-on-cash yield based on current market rents and realistic renovation costs
- **Value-add spread** — The gap between what a property is worth today (as-is) versus what it could be worth stabilized after renovation
- **Acquisition likelihood** — How motivated is the seller? Absentee owners, out-of-state owners, tax-delinquent properties, and foreclosures score higher
- **Risk factors** — Flood zone exposure, property age, environmental flags, rent control applicability
- **Tax advantages** — Opportunity Zone designation for capital gains benefits

Each property gets a composite score from 0 to 1. The platform ranks all 20,551 and surfaces the top opportunities.

### 2. Monitors Distressed Deal Flow

The platform tracks real-time distress signals:

- **Sheriff sales** — 27 active foreclosures in Paterson right now, with upset prices, sale dates, and case details. 16 of these are matched to properties in our database with full underwriting.
- **Absentee owners** — 75% of Paterson properties are owned by someone who doesn't live there. 914 are owned by people in other states (New York, Pennsylvania, Maryland, California). These are the most likely to accept off-market offers.
- **High distress properties** — 6,168 properties showing multiple distress signals (deferred maintenance, below-assessed sales, tax issues).

### 3. Generates Investor-Ready Analysis

For any property in the pipeline, the platform produces:

- **Underwriting sheets** — One-page property analysis with valuation, income projection, risk assessment, and composite score rating
- **Comparable sales analysis** — The 10 most similar recent sales, with comp-implied valuation range and confidence level
- **Portfolio optimization** — Given a fund size, the optimal mix of properties that maximizes blended return while diversifying across ZIP codes and property types

### 4. Validates the Thesis with Data

The platform doesn't just project returns — it backtests them against 4,461 historical transactions in Paterson spanning 2001-2026:

- Properties transact at approximately 1.05x assessed value for off-market deals
- Renovated and stabilized multifamily commands 2.5x assessed on the open market
- The value-add spread (buy at 1.1x, exit at 2.5x) represents operational alpha — not market appreciation
- Stress tests show the model stays above breakeven even with 20% rent decline or 50% cost overruns

---

## Where the Data Comes From

All public, all free, all automated:

| Source | What It Provides | Update Frequency |
|--------|------------------|-----------------|
| NJ MOD-IV (ArcGIS) | Every parcel: assessed value, owner, sale history, units, year built | Weekly |
| HUD Fair Market Rents | Rent benchmarks by ZIP code and bedroom count | Annual |
| Zillow ZORI | Observed market rents by ZIP, monthly time series | Monthly |
| Census ACS | Vacancy rates, income, population by census tract | Annual |
| CivilView Sheriff Sales | Active foreclosures with upset prices and case details | Weekly |
| FEMA Flood Maps | Flood zone designations for risk scoring | Static |
| HUD Opportunity Zones | Tax-advantaged census tracts | Static |

---

## What It Found Right Now

### Market Snapshot (March 29, 2026)

| Metric | Value |
|--------|-------|
| Properties scored | 20,551 |
| Median estimated market value | $250,000 |
| Median cap rate (as-is) | 7.5% |
| Median equity multiple | 1.72x |
| Active sheriff sales | 27 (16 matched to database) |
| Absentee-owned | 75% |
| Out-of-state owners | 914 |
| Opportunity Zone properties | 8,085 (39%) |
| High distress (score 3+) | 6,168 |
| Equity multiple > 1.5x | 13,878 |

### Sample Sheriff Sale Opportunity

**307 Van Houten Street** — 3-unit residential, absentee NJ owner (LLC defendant)
- Upset price: $316,915 | Sale date: April 7, 2026
- Comparable sales median: $525,000 (10 comps, high confidence)
- Estimated NOI (stabilized): $55,650/yr
- Projected equity multiple: 2.25x
- Composite score: 0.797 (BUY rating)

### Sample Portfolio ($5M Fund)

Using the portfolio optimizer with $5M budget:
- 20 properties selected | 84 total units
- Total deployed: $3.13M
- Blended cap rate: 68% (as-is) → 89% (stabilized)
- Blended equity multiple: 8.18x
- Diversified across 7 ZIP codes and 2 property classes
- 14 of 20 properties are in Opportunity Zones

---

## How It Helps Forte Specifically

### For Deal Selection
The platform replaces gut-feel deal sourcing with quantified, ranked opportunities. Every property Forte presents to investors has a data-backed underwriting sheet, comparable sales analysis, and composite score. This is the institutional-grade process that builds investor confidence at the $1,000 minimum investment level.

### For Investor Communications
The platform generates the data behind quarterly investor updates:
- Market trend reports (rent growth, vacancy, cap rate trends)
- Portfolio performance tracking (predicted vs actual)
- New deal pipeline status (how many opportunities are in the funnel)

### For Deal Flow
The direct mail targeting module identifies the most motivated sellers in Paterson:
- Tier 1: 200 out-of-state owners with distress signals — includes actual mailing addresses for direct outreach
- Tier 2: 300 absentee NJ owners with distress
- Tier 3: 500 all absentee owners
- Mail merge-ready CSVs with property metrics and owner contact information

### For Due Diligence
Every property in the pipeline has:
- Comparable sales with valuation range
- Flood zone and environmental risk flags
- Opportunity Zone eligibility
- Historical transaction data
- Distress signal breakdown

### For Regulatory Compliance
Reg A Tier II requires demonstrating sound investment methodology to the SEC. A data-driven, backtested, multi-source scoring platform — with documented methodology and historical validation — is significantly stronger than "our operator has experience in this market."

---

## What's Next

The platform currently covers Paterson. The architecture is modular — expanding to Newark, Jersey City, or any NJ city requires changing one parameter (the municipality name in the ArcGIS query). All other data sources (HUD, Census, FEMA, Zillow) automatically adjust by geography.

Forte's stated focus is East Coast multifamily. This platform can scale to cover every target market with the same depth of analysis.

---

## Technical Details

- Runs autonomously on a cloud server (daily scoring, weekly data refresh)
- Web dashboard for browsing and filtering all 20,551 properties
- Command-line tools for deep analysis (comps, portfolio optimization, underwriting)
- All data stored locally — no dependency on paid data vendors
- Python-based, open architecture, fully documented
