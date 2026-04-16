# Live vs Paper Reconciliation — 30m-concentrated

Investigation of the gap between HL paper's gross edge and live Coinbase execution. Open as of 2026-04-15.

## Why this document exists

Two independent concerns surfaced on 2026-04-15 while looking at the live dashboard:

1. A recurring ~once-per-day "big red bar" in the hourly P&L chart that didn't correspond to visible trade activity.
2. A large gap between the strategy's paper-measured gross edge (+13.6 bps) and the live-measured gross edge (+1.9 bps) over the same window.

Item 1 is **resolved** (see §1). Item 2 is **open** with ~8.6 bps unexplained (see §2).

---

## §1 — Daily settlement event [RESOLVED]

### Observation

`total_usd_balance` (queried via `GET /api/v3/brokerage/cfm/balance_summary`) was stable for 24+ hours then dropped in a single step, twice since live trading began:

| Date/time UTC | total_usd_balance | Δ | Hours stable before |
|---|---:|---:|---:|
| 2026-04-11 18:35 | $10,000.00 | — | (inception) |
| 2026-04-14 09:44 | $9,645.66 | **−$354.34** | 63.1 |
| 2026-04-15 09:44 | $9,363.77 | **−$281.89** | 24.0 |

### Concierge response (2026-04-15)

- CFM runs a **Start-of-Day (SOD) reconciliation** once per US business day
- Stated trigger time: **04:30 AM ET / 08:30 UTC**
- Components rolled in: realized P&L, fees, funding, ledger adjustments
- Intraday fees are accrued per fill but **not ledgered to `total_usd_balance` until SOD**
- Resets intraday stats after posting
- Coinbase-specific operational clearing, not CFTC-mandated variation margin
- **No weekend settlement** — Fri/Sat/Sun activity batches into Monday's SOD posting. Explains the 63h window on our first observed settlement (04/14 covered Fri inception + Sat + Sun + Mon morning).
- Statement endpoints:
  - `https://www.coinbase.com/advanced-portfolio?tab=transactions`
  - `https://accounts.coinbase.com/statements/futures`
- Cost basis: **FIFO**
- Intraday realized P&L returned by `cfm/balance_summary` is a **running estimate**; the authoritative per-trade numbers only appear in the statement after SOD reconciliation.

### Timing — fully resolved

Concierge follow-up (2026-04-15) confirmed:

- **08:30 UTC**: clearinghouse reconciliation begins (SOD trigger)
- **~1 hour lag** to customer-visible `total_usd_balance` update, due to ledger processing and reconciliation workflows
- **09:14–09:44 UTC window** for customer-visible posting is expected, matching our observations exactly on both 04/14 and 04/15

Section closed. No remaining anomaly on settlement mechanics.

### Implication for the dashboard

The hourly P&L chart's "big red bars" are the daily SOD posting, not trading losses in that hour. Chart fix: either relabel the 09:00–10:00 UTC bucket as "daily settlement" on days it fires, or base the chart on trade-log realized P&L instead of equity_curve deltas so SOD posting doesn't show as an hourly P&L event.

---

## §2 — Gross edge gap [RESOLVED — engine flip-accounting bug, NOT price-feed]

**TL;DR**: My earlier "10 bps price-feed divergence" finding was real but NOT the root cause. The real cause is a **bug in `engine/prepare.py` and `paper/trader.py`** where position reversals (sign flips) silently do not realize P&L. Every time the strategy flips from long to short (or vice versa) at equal magnitude, the code leaves the stale entry price attached to the new-sign position and never books the losing close. This compounds across thousands of trades into fictional equity growth. With the bug fixed, the strategy shows −95% return over 9 months on Coinbase data — roughly matching what we see in live money.

### What the bug looks like

`engine/prepare.py:1009-1025` and `paper/trader.py:410-427` (before fix):
```python
else:  # current_pos != 0, target != 0
    if abs(sig.target_position) < abs(current_pos):   # reduce (same-side assumed)
        ...
    elif abs(sig.target_position) > abs(current_pos): # add (same-side assumed)
        ...
    # NO BRANCH for abs(target) == abs(current) with OPPOSITE SIGN (pure flip)
    portfolio.positions[sig.symbol] = sig.target_position  # silent sign flip
```

And `strategies/30m-concentrated/strategy.py:216-225` deliberately triggers the bug:
```python
if current_pos > 0 and bearish and not in_cooldown:
    signals.append(Signal(symbol=symbol, target_position=-size))  # single-signal flip
```

Since `size` is computed the same way on reversal as on original open, `abs(target) == abs(current)` exactly. Neither reduce nor add branch fires. The flip happens but with:
- No P&L realization
- Stale entry_price carried forward
- No cash movement
- Trade logged as `"modify"`, excluded from win-rate calculation

### Honest numbers vs buggy numbers (30m-concentrated, 9mo Coinbase data, same strategy, same params)

| Metric | Before fix (broken) | **After fix (honest)** |
|--------|--------------------:|----------------------:|
| Sharpe | 21.38 | **−7.02** |
| Total return | +4,848,808% | **−95.20%** |
| Max drawdown | 4.15% | **95.29%** |
| Win rate | 72.5% | 52.9% |
| Profit factor | 7.28 | 0.95 |

### Reconciliation across all measurement sources

| Source | Number | Status |
|--------|-------:|--------|
| Prior cb-baseline (buggy engine) | +70,000% return | **ARTIFACT** |
| HL paper (same bug) | +65% return | **ARTIFACT** |
| "Realistic execution" projection | +44% return | **ARTIFACT** (derived from HL paper) |
| Custom backtest with flip (honest) | −11.65 bps net | Honest |
| Custom backtest no-flip variant | −11.56 bps net | Honest (no meaningful improvement) |
| **Fixed engine** | **−95% return** | **Honest ground truth** |
| Live (4 days) | −$587 net | Honest, matches direction |

### Why the price-feed finding was real but misleading

The 10.78 bps HL-vs-Coinbase close divergence on CLOSE trades IS real (4.8σ, statistically robust). But it sits ON TOP of the flip bug. Both HL paper and the engine benefit from the flip bug equally. The price-feed divergence is a secondary effect; the engine bug is the primary driver of the phantom edge.

### Earlier "timing tax" finding still holds

The bar-close vs :14 fill-price measurement (~1 bps VW across live fills) remains accurate. It's just not the dominant factor.



### Observation

Over the same 4-day window (2026-04-11 18:35 → 2026-04-15 21:15 UTC):

| | HL paper | Live (Coinbase) | Gap |
|---|---:|---:|---:|
| Trades / fills | 308 | 432 | — |
| Notional volume | $21.66M | $1.13M | — |
| Gross realized P&L | +$29,476 | +$217 | — |
| **Gross bps (one-way, of notional)** | **+13.61** | **+1.91** | **−11.70** |
| Fee cost bps | 5.00 (model) | 7.10 (actual) | +2.10 |
| **Net bps** | **+8.61** | **−5.19** | **−13.80** |

(HL paper notional is ~19× live because paper was running at 100k initial equity vs 10k live.)

The strategy has measurable edge. Live is only realizing ~14% of that edge (1.91/13.61). Fees alone don't close the gap — 7.1 bps live fees against 13.6 bps paper gross would still be +6.5 bps net, yet live is −5.19 bps net.

### What we've ruled out

**Fees.** Concierge confirmed (2026-04-15): no hidden fee categories, no double-posting, $804.52 commissions across 432 fills matches Coinbase's aggregate exactly. Blended rate 7.10 bps one-way = 3.0 bps exchange (VIP 4 taker) + ~4 bps regulatory passthrough ($0.15/contract). Fees are not a hypothesis for the edge gap.

**Bar-close vs fill-price timing.** Live fills ~14.3 min after the 30-min bar close; HL paper attributes the fill at the bar-close price. Measured volume-weighted cost across all 432 live fills: **+0.99 bps**. Not the main driver.

Per-symbol timing cost vs HL paper gross edge:

| Symbol | Fills | Timing cost | HL gross edge |
|---|---:|---:|---:|
| BTC | 129 | −2.02 bps | +11.46 bps |
| ETH | 108 | +2.77 bps | +14.03 bps |
| SOL | 195 | +2.29 bps | +14.61 bps |

BUY vs SELL: BUYs −0.57 bps (favorable), SELLs +2.61 bps (unfavorable).

**Fee over-counting in the dashboard.** Investigated and ruled out — live state's trade_log contains only 60 clean live entries today ($171.55 in fees, matches Coinbase API within unflushed-fills gap).

### Accounted for (final)

| Source | bps |
|---|---:|
| Price-feed divergence on closes (HL phantom advantage) | ~10.8 |
| Bar-close vs fill-price timing tax | 1.0 |
| Fee differential (7.1 live vs 5.0 HL model) | 2.1 |
| **Total accounted** | **~13.9** |
| **HL paper measured gross edge** | **+13.6** |
| **Implied true Coinbase-based gross edge** | **~−0.3 to +2 bps** |

Matches observed live gross edge of +1.91 bps within noise. Gap is fully accounted for.

### Price-feed divergence measurement (2026-04-15)

Compared HL paper's logged fill price to Coinbase's 30-min candle close at the same bar boundary for all 307 HL paper trades in the live overlap window (04/11 18:35 → 04/15 21:15 UTC).

| Metric | Value |
|---|---:|
| Raw delta (HL − CB), VW | −10.49 bps |
| Raw delta, EW | −10.30 bps |
| Median delta | −9.58 bps |
| |Absolute delta| VW | 25.31 bps |
| Stdev | 38.54 bps |

Per-action directional advantage (positive = HL paper getting better price than Coinbase for its trade direction):

| Action | N | Raw (HL − CB) VW | HL directional advantage VW |
|---|--:|---:|---:|
| OPEN_LONG | 71 | −2.83 | +2.83 |
| OPEN_SHORT | 66 | −13.64 | −13.64 |
| **CLOSE** | **138** | **−11.40** | **+10.78** |
| MODIFY | 32 | −13.85 | −24.85 |

In HL paper, 100% of realized P&L flows through CLOSE trades (opens and modifies are $0 realized). The +10.78 bps advantage on CLOSEs is the phantom-edge source.

### Implications

1. **The strategy is likely structurally unprofitable as a taker on Coinbase.** True gross edge ~2-3 bps vs 7.1 bps one-way fees = net negative.
2. **Maker pilot is not optional** — it's required for viability. Even if maker execution gets fees to ~6.5 bps blended plus 1-3 bps spread capture (net ~3-5 bps cost), that's the range where ~2-3 bps gross edge can clear.
3. **Every HL-paper-backed strategy may have this artifact.** Before running more strategies live, re-evaluate against Coinbase bar data.
4. **Research priority shifts** from signal tuning to execution — maker-only, lower turnover, or different venue matters more than better signals at this point.
5. **Cross-check**: the "realistic execution" simulator showing +44.67% vs HL paper's +65.3% over Mar 27-Apr 14 (per earlier memory note) was already detecting this. The ratio (44.67 / 65.3) ≈ 68% is consistent with losing ~30% of edge to feed divergence over that window.

### Hypotheses — disposition

1. **Price-feed divergence.** ✅ **CONFIRMED as primary cause.** Measured 10.78 bps phantom advantage on CLOSE trades (see measurement above).
2. **Contract quantization on entries.** Not tested — likely a smaller contributor, in the sub-bps range given the large close-divergence finding.
3. **Position-tracking drift.** Unresolved curiosity (FIFO reconstruction gives BTC +15/ETH 0/SOL +25 vs Coinbase's BTC +5/ETH +16/SOL +9) but doesn't affect P&L magnitude — the aggregate balance reconciles cleanly via the daily settlements. Worth investigating separately if we need accurate per-trade attribution.
4. **Fill-side sequencing on flips.** Partially captured in the timing/divergence measurements. Not pursued further.
5. **Spread crossing.** Partially captured in the measurements. Taker-side baseline is ~1-3 bps of the timing tax.

---

## §3 — Reconciliation ground truth

### Positions (as of 2026-04-15 ~21:40 UTC)

- **Inception** (2026-04-11 18:35 UTC): $10,000.00
- **Current** `total_usd_balance`: $9,363.77
- **Current** `unrealized_pnl`: +$48.55
- **Effective account value**: $9,412.32
- **Total P&L since inception**: **−$587.68**

### Trading aggregates since inception

- **Fills**: 432 (via Coinbase `/orders/historical/fills`)
- **Commissions paid**: $804.52
- **Implied gross P&L (before fees)**: +$216.84
- **Blended commission rate**: 7.10 bps one-way (3.0 bps exchange + ~4 bps regulatory passthrough)

### Fee-tier verification (VIP 4 derivatives)

- Exchange: 3.0 bps taker / 2.5 bps maker
- Regulatory passthrough: $0.15 per contract
- Current 30-day US derivatives volume: $10M–20M tier

### Settlement events to date

| UTC | Amount | Window | Fills | Commissions in window |
|---|---:|---|---:|---:|
| 2026-04-14 09:44 | −$354.34 | inception → 04/14 09:44 (63.1h) | 273 | $572.83 |
| 2026-04-15 09:44 | −$281.89 | 04/14 09:44 → 04/15 09:44 (24h) | 98 | $137.40 |
| 2026-04-16 09:44 (projected) | ~−$10 | 04/15 09:44 → now | 57 | $92.82 |

---

## §4 — Related known issues (not blocking but worth tracking)

- `state['current_utc_date']` stuck at `2026-04-12` since that date; daily rollover never fired.
- `state['cumulative_fees_total']` stuck at $57.31 (correct value would be ~$805 across 4 days).
- `state['cash']` field shows wildly implausible values (e.g., −$2,016) — not used for user-facing display but suggests internal bookkeeping is broken.
- `state['prior_days_realized_pnl']` and `prior_days_fees` never populated.
- `live/trader.py:703` crash on `risk_mgr.config.maker_pilot` after the 18:14 UTC tick on 2026-04-15 — trader crashes after snapshotting but before executing, so signals from 18:14 onwards aren't getting through. Related to phase-2 maker-pilot revert.

These are bookkeeping-only; the account reconciles cleanly against Coinbase. But they mean the state file is not a reliable source for "how am I doing overall" — go to Coinbase API or statements directly.

---

## §5 — Next actions

CRITICAL (immediate):
- [x] Patch `engine/prepare.py` flip logic to properly realize P&L on sign changes
- [x] Patch `paper/trader.py` with the same fix (HL paper currently running the bug)
- [ ] **Halt 30m-concentrated live trading** — fixed-engine backtest shows −95% expected return over 9 months; no basis to continue at taker rates
- [ ] Audit every other strategy's backtest results — all were produced by the buggy engine

Audit list (strategies confirmed to emit single-signal flips that trigger the bug):
- [ ] `30m-concentrated` — shown here, −95% after fix
- [ ] `15m-scalper` (strategy.py:215-216) — flip pattern
- [ ] `1h-equities` (strategy.py:261-262) — flip pattern, 133 logged experiments
- [ ] `1h-funding-mr` (strategy.py:250-257) — flip pattern
- [ ] `1h-pairs-arb` (strategy.py:96-101) — flip pattern
- [ ] `1h-vol-mr` (strategy.py:266-294) — flip pattern, multiple flip signals
- [ ] `30m-highoctane` (strategy.py:213-214) — flip pattern
- [ ] `30m-mtf-fusion` (strategy.py:258-259) — flip pattern
- [ ] `30m-btc-eth-sol` (strategy.py:217-219) — flip pattern
- [ ] `1h-btc-eth-sol` (strategy.py:292-294) — flip pattern, 103 logged experiments
- [ ] `30m-8coin` (strategy.py:217-219) — flip pattern
- [ ] `1h-8coin` (strategy.py:292-294) — flip pattern
- [ ] `15m-btc-eth-sol` (strategy.py:165-167) — flip pattern
- [ ] `30m-voltarget` (strategy.py:245-247) — flip pattern

Every strategy above had its backtest `results.tsv` produced with the buggy engine and must be rerun to get honest numbers before any decision.

Operational:
- [ ] Pull a Coinbase futures statement from `accounts.coinbase.com/statements/futures` for 04/11–04/15 and reconcile settlement amounts (sanity check)
- [ ] Fix chart labeling so the daily SOD posting doesn't look like "worst hour"
- [ ] Fix the state bookkeeping bugs (`current_utc_date` rollover stuck at 04/12, cumulative totals frozen, invalid `cash` field)
- [ ] Fix the `maker_pilot` crash so the trader runs clean

Memory / documentation cleanup:
- [ ] Quarantine/update these memory notes — they cite numbers from the buggy engine:
  - `project_30m_concentrated_phase1_deployed.md`
  - `project_realistic_execution_baseline.md`
  - `project_filter_research_closed.md`
- [ ] Update `REALISTIC_EXECUTION_PROJECTION.md` in this directory — its numbers are derived from the same flawed HL paper source

Investigation (lower priority):
- [ ] Resolve the FIFO position-reconstruction discrepancy (likely a partial-fill handling issue)

---

## Appendix — How to regenerate the numbers

All analysis ran against the VM at `100.109.85.37` (root SSH). Key scripts live inline in this session's conversation; core queries:

- **All fills since inception**: paginate `GET /api/v3/brokerage/orders/historical/fills` (250/page, filter `product_id` to `BIP-20DEC30-CDE`, `ETP-20DEC30-CDE`, `SLP-20DEC30-CDE`)
- **CFM balance summary**: `GET /api/v3/brokerage/cfm/balance_summary` (composition: `available_margin ≈ total_usd_balance + unrealized_pnl + daily_realized_pnl`)
- **total_usd_balance timeline**: grep `Initial equity:` from `/home/openclaw/auto-researchtrading/live/logs/30m-concentrated-live-cron.log`, walk forward tracking date rollover at HH:MM:SS wraparound
- **HL paper comparison**: `/home/openclaw/auto-researchtrading/strategies/30m-concentrated/paper_state.json` trade_log, sum `pnl`, `fee`, abs(`size`)
- **Contract sizes**: BTC 0.01 (BIP), ETH 0.1 (ETP), SOL 5.0 (SLP) — verified via `get_product()`
