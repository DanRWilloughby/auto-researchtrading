# Auto-Research: Real Estate Deal Sourcing Tool
## For Veloce Capital + Forte Investment Fund — Paterson, NJ

---

## The Clients

### Veloce Capital (Operator)
- Value-add developer focused on **Paterson, NJ**
- Strategy: acquire underutilized/historic properties → adaptive reuse, mixed-use, residential conversion
- Known projects: 180-186 Cedar St, 146 Hamilton Ave, Paterson Armory, 285-287 Fulton Place
- They source, develop, and manage the properties

### Forte Investment Fund (Capital)
- Reg A Tier II fund ($1,000 minimum) that makes Veloce's deals accessible to retail investors
- Evaluates opportunities, structures offerings, manages investor experience
- Shared team: Surf Dinani (Veloce) advises Forte; Ryan Yates is VP Finance at both
- Pre-launch — building their pipeline now

### What They Need
A **deal sourcing and underwriting research tool** that continuously scans Paterson for properties matching Veloce's value-add thesis, scores them by return potential, and surfaces the best acquisition targets — so Veloce can move fast on deals and Forte can present data-backed opportunities to investors.

---

## What the Tool Does

### Core Function
Automatically identify **undervalued, repositionable properties** in Paterson, NJ that fit a value-add / adaptive reuse strategy, ranked by estimated return after renovation.

### Target Property Profile (Based on Veloce's Portfolio)
- Multifamily (2-20+ units)
- Mixed-use (residential + ground floor commercial)
- Historic / industrial buildings suitable for adaptive reuse
- Underperforming properties (high vacancy, deferred maintenance, tax delinquent)
- Properties in or near Opportunity Zones

### Key Outputs
1. **Ranked Deal Pipeline** — Top properties scored by projected return after value-add
2. **Distressed Property Alerts** — New foreclosures, sheriff sales, tax lien auctions
3. **Neighborhood Heat Maps** — Where rent growth, permits, and demographics signal upside
4. **Underwriting Packets** — Per-property data sheets for investor presentations (Forte)
5. **Market Trends Dashboard** — Rent trends, vacancy, absorption, new supply

---

## Phase 1: Data Acquisition (All Public / Free)

### Property-Level Data
| Source | Data | Access | Priority |
|--------|------|--------|----------|
| **NJ MOD-IV Tax Records** | Every parcel: assessed value, land/improvement split, property class, owner, last sale price/date, lot size | NJ Treasury bulk CSV | **P0** |
| **Passaic County Tax Sale Certificates** | Tax-delinquent properties — distressed acquisition targets | County tax collector / OPRA | **P0** |
| **NJ Sheriff Sales (Passaic County)** | Foreclosure auctions — below-market pricing | NJCourtsOnline + county sheriff site | **P0** |
| **NJ OPRA (Open Public Records Act)** | Code violations, vacant building registry, demolition orders | Municipal records request | **P1** |
| **Paterson Building Permits** | Recent permits signal active renovation; absence signals neglect | City of Paterson / NJ DCA | **P1** |
| **Zillow / Redfin Listings** | Current asking prices, DOM, price history | Public listings / Redfin download | **P1** |
| **NJHMFA Qualified Census Tracts** | Opportunity Zone and LIHTC eligibility — tax advantages for investors | NJHMFA published maps | **P1** |

### Rental Market Data
| Source | Data | Access | Priority |
|--------|------|--------|----------|
| **HUD Fair Market Rents** | FMR by zip and bedroom count — baseline rent estimate | huduser.gov CSV | **P0** |
| **Zillow ZORI** | Observed rent index by zip, monthly time series | Zillow Research CSV | **P0** |
| **Census ACS B25064** | Median gross rent by census tract | Census API | **P0** |
| **NJ DCA Rent Control Registry** | Paterson has rent control — need to know which properties are regulated | Municipal records | **P1** |

### Neighborhood & Risk Data
| Source | Data | Access | Priority |
|--------|------|--------|----------|
| **Census ACS 5-Year** | Population, income, poverty, vacancy, housing age, rent burden by tract | Census API (free) | **P0** |
| **BLS QCEW** | Employment growth, major employers, wage trends | BLS API | **P1** |
| **Paterson PD / NJ UCR** | Crime rates by area — affects insurance, tenant quality | UCR API / NJ State Police | **P1** |
| **FEMA NFHL** | Flood zone designation — Passaic River flooding is real in Paterson | FEMA API | **P1** |
| **NJ DEP Known Contaminated Sites** | Environmental liabilities on brownfield conversions | NJ DEP DataMiner | **P1** |
| **GreatSchools** | School quality ratings — demand driver for family renters | GreatSchools API | **P2** |

---

## Phase 2: Scoring Model

### Value-Add Opportunity Score

The key insight for Veloce's strategy: the best deals are where **current value is low but post-renovation value is high**. We estimate both.

#### As-Is Analysis
```
Current Rent     = actual (if listed) or estimated from HUD FMR / ZORI / ACS
Vacancy Adj      = Current Rent × tract vacancy rate
Effective Rent   = Current Rent - Vacancy Loss
Operating Exp    = Actual Tax + Insurance (est) + Mgmt (8%) + Maintenance (10%)
NOI_current      = Effective Rent - Operating Exp
Cap Rate_current = NOI_current / (Assessed Value or Last Sale Price)
```

#### Post-Renovation Projection
```
Target Rent      = ZORI_zip × (1 + renovation_premium)   # 15-30% lift for value-add
Vacancy Adj      = 5% (stabilized)
Effective Rent   = Target Rent × units × 0.95
Operating Exp    = New Tax Assessment (est) + Insurance + Mgmt (8%) + Maint (5%)
NOI_stabilized   = Effective Rent - Operating Exp
Cap Rate_exit    = market cap rate for stabilized multifamily in Paterson
Projected Value  = NOI_stabilized / Cap Rate_exit
```

#### Return Metrics
```
Total Cost       = Acquisition + Renovation Estimate + Closing + Carry
Equity Multiple  = Projected Value / Total Cost
IRR              = f(hold period, cash flows during renovation, exit value)
Cash-on-Cash     = NOI_stabilized / Total Equity Invested
Spread           = Cap Rate_current - Cap Rate_exit  (value-add upside)
```

### Distress Signals (Bonus Points)
Properties get boosted in ranking if they show:
- Tax delinquency (2+ years)
- Code violations / vacant building registry
- Owner is out-of-state (absentee → motivated seller)
- Listed well below assessed value
- Assessed improvement value near $0 (land value play)
- In sheriff sale / pre-foreclosure pipeline
- No recent building permits (deferred maintenance)

### Risk Discounts
- Flood zone (FEMA Zone A/AE) → insurance cost penalty
- Environmental contamination → remediation cost penalty
- Rent control designation → capped upside on rents
- High crime tract → higher vacancy / turnover assumption
- Historic designation → higher renovation costs but tax credits available

### Composite Score
```
Score = w1 × equity_multiple_rank        (0.30)  — total return potential
      + w2 × cap_rate_spread_rank        (0.25)  — value-add upside
      + w3 × distress_signal_count       (0.20)  — acquisition leverage
      + w4 × neighborhood_trend_rank     (0.15)  — appreciation tailwind
      - w5 × risk_score                  (0.10)  — downside protection
```

---

## Phase 3: Pipeline Architecture

```
┌──────────────────────────────────────────────────────────┐
│                    DATA INGESTION (weekly)                │
│                                                          │
│  NJ MOD-IV ──────┐                                       │
│  Tax Liens ──────┤                                       │
│  Sheriff Sales ──┤──→  Property DB (SQLite/Postgres)     │
│  Permits ────────┤     ~30,000 parcels in Paterson       │
│  Listings ───────┘                                       │
│                                                          │
│  HUD FMR ────────┐                                       │
│  ZORI ───────────┤──→  Rent Estimates DB                 │
│  ACS Rents ──────┘                                       │
│                                                          │
│  Census ACS ─────┐                                       │
│  BLS / Crime ────┤──→  Neighborhood Context DB           │
│  FEMA / DEP ─────┘                                       │
└──────────────────────────┬───────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────┐
│              ENRICHMENT & SCORING (on refresh)           │
│                                                          │
│  1. Join parcel → zip/tract → neighborhood context       │
│  2. Classify property type (MF, mixed-use, industrial)   │
│  3. Estimate as-is NOI and cap rate                      │
│  4. Project post-renovation NOI and exit value           │
│  5. Flag distress signals                                │
│  6. Apply risk discounts                                 │
│  7. Compute composite score and rank                     │
└──────────────────────────┬───────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────┐
│                    OUTPUTS                                │
│                                                          │
│  📋 Ranked Pipeline    — Top 50 deals, updated weekly    │
│  🚨 Distress Alerts   — New foreclosures, tax sales      │
│  🗺️  Heat Map          — Tracts ranked by opportunity    │
│  📊 Underwriting Sheet — Per-property PDF for Forte      │
│  📈 Market Report      — Monthly trends for investors    │
└──────────────────────────────────────────────────────────┘
```

---

## Phase 4: Build Plan

### Sprint 1 — Data Foundation (MVP)
- [ ] Download NJ MOD-IV for Passaic County, parse and filter to Paterson
- [ ] Pull HUD FMR + Zillow ZORI for rent estimation
- [ ] Pull Census ACS tract-level data (vacancy, income, population, rent)
- [ ] Build SQLite database joining parcels to rent estimates and neighborhood data
- [ ] Basic scoring: cap rate, rent/price ratio, distress flags
- [ ] Output: ranked CSV of top 100 properties

### Sprint 2 — Value-Add Modeling
- [ ] Post-renovation rent projection (renovation premium by property class)
- [ ] Exit value estimation using market cap rates
- [ ] Equity multiple and IRR calculation
- [ ] Tax lien and sheriff sale data integration
- [ ] Absentee owner flagging (owner address ≠ property address)
- [ ] Output: enriched pipeline with value-add projections

### Sprint 3 — Risk & Context Layer
- [ ] FEMA flood zone overlay
- [ ] NJ DEP contamination check
- [ ] Crime data by tract
- [ ] Opportunity Zone / LIHTC qualification flags
- [ ] Rent control registry check
- [ ] Building permit history (renovation activity signal)
- [ ] Output: full composite score with risk adjustments

### Sprint 4 — Automation & Presentation
- [ ] Weekly automated data refresh (cron on VM)
- [ ] Distress alert emails (new sheriff sales, new tax liens)
- [ ] Property underwriting PDF generator (for Forte investor packets)
- [ ] Web dashboard (Dash/Plotly or integrate into existing Vercel setup)
- [ ] Historical tracking — watch how scores change over time

### Sprint 5 — Geography Expansion
- [ ] Abstract data sources into pluggable adapters per state
- [ ] Add Newark, Jersey City, or other NJ cities
- [ ] Expand to other East Coast markets (Forte's stated focus)

---

## Paterson-Specific Intel

### Why Paterson for Value-Add
- **Affordable basis**: Multifamily buildings available at $300K-$800K range
- **Strong rents relative to price**: HUD FMR for Passaic County is solid
- **Aging housing stock**: Many pre-war buildings ripe for renovation
- **Opportunity Zones**: Several census tracts qualify for OZ tax benefits
- **NYC spillover**: 20 miles from Manhattan, NJ Transit access
- **Veloce already operates here**: Local knowledge, contractor relationships, municipal relationships

### Watch-Outs
- **Rent control**: Paterson has rent control ordinance — must check property-level applicability
- **Passaic River flooding**: Properties near the river need FEMA flood zone check
- **Environmental**: Historical industrial use means brownfield risk in some areas
- **Property tax increases**: NJ has highest property taxes in the US — reassessment after renovation is real

---

## Deliverable for Veloce + Forte

The tool would be positioned as:

> **"Paterson Deal Intelligence Platform"**
> Automated property research that continuously scans Paterson's ~30,000 parcels, identifies the highest-return value-add opportunities, and generates investor-ready underwriting — so Veloce can source faster and Forte can present data-backed deals to their investors.

### Value to Veloce (Operator)
- Never miss a distressed deal (sheriff sale, tax lien, code violation)
- Quantified ranking replaces gut-feel deal sourcing
- Faster underwriting = faster offers = more deals won

### Value to Forte (Fund)
- Data-backed deal selection builds investor confidence
- Automated underwriting sheets for Reg A offering materials
- Market trend reports for quarterly investor updates
- Demonstrates institutional-grade process despite retail investor base
