> **DEPRECATED 2026-04-16** — All numbers in this document are derived from HL paper data produced by an engine with a flip-accounting bug that silently hid realized losses on position reversals. The +44.67% "realistic" taker baseline and all maker-pilot projections are unreliable. See `LIVE_RECONCILIATION.md` in this directory for the full post-mortem. Fixed-engine backtest on 9 months of Coinbase data shows the strategy at **-95% return**, not +44%.

# Realistic Execution Projection — 30m-Concentrated

**Date:** 2026-04-14
**Strategy:** 30m-concentrated (HL Paper at `strategies/30m-concentrated/paper_state.json`)
**Period:** 2026-03-27 → 2026-04-14 (~18.5 days, 1,297 trades, 875 equity-curve points)
**HL paper headline:** +65.27% ($100K → $165,267)

## Purpose

HL paper assumes a flat 5 bps fee and zero slippage. That's optimistic vs real
Coinbase execution, and the headline +65.3% overstates what the same strategy
would have earned on CB. This doc projects more realistic outcomes using
execution cost data we've actually measured from the live CB deployment.

## Measured inputs (from VM live-paper + live cron logs)

Pulled from the CB live-paper logs (`live/logs/trades_*.jsonl`), the live-money
reconciliation log (`30m-concentrated-live-cron.log`), and the maker shadow
book snapshots (`live/logs/maker_shadow_*.jsonl`).

| Coin | Taker fee (observed) | Maker fee¹ | Adverse slippage² | Half-spread³ |
|---|---:|---:|---:|---:|
| BTC | 5.06 bps | 4.56 bps | 0.34 bps | 1.59 bps |
| ETH | 9.58 bps | 9.08 bps | 0.00 bps (favorable avg) | 2.69 bps |
| SOL | 6.57 bps | 6.07 bps | 0.00 bps (favorable avg) | 2.38 bps |

Sample sizes: 386 trades for fees (~128/coin), 128 reconciliation lines for
slippage (~42/coin), 198 maker-shadow book snapshots for spreads (~66/coin).

¹ CB VIP-4 schedule: maker = taker − 0.5 bps (base rate delta). Per-contract
regulatory passthrough is the same for maker/taker, so it cancels out.
Round-trip savings = 1 bp on fees alone.

² **Adverse slippage only** — computed as `(actual - intended) * direction`.
The raw `|actual − intended|` was ~5–7 bps per coin but decomposes into
cross-venue basis (same sign for BUY and SELL → cancels in round-trips) plus
true adverse slippage. Only the adverse component costs real money. Initial
simulation mistakenly used `abs()` and double-counted basis; this doc uses
the corrected adverse-only component.

³ Captured as `(ask − bid) / mid * 10000 / 2`. This is the theoretical maker
price improvement per fill vs taker crossing the spread.

## Scenarios — HL paper re-simulated with compounding preserved

Method: walked all 1,297 HL trades in timestamp order. For each trade,
scale trade size and realized PnL by `new_running_equity / hl_equity_at_t`
(strategy is fraction-of-equity sized, so as new equity diverges, position
sizes diverge proportionally). Deduct the scenario's cost in bps of scaled
notional. Compound forward.

| Scenario | Final equity | Growth | Δ vs HL |
|---|---:|---:|---:|
| **A**) HL paper original (flat 5 bps, no slippage) | $165,267 | **+65.27%** | — |
| **B**) + CB taker fees (observed bps/coin) | $145,439 | +45.44% | −19.83pp |
| **C**) + taker fees + adverse slippage | $144,670 | **+44.67%** | **−20.60pp** |
| **D**) Pure maker (100% fill, upper bound) | $170,666 | +70.67% | +5.40pp |
| **E-60**) Blended 60% maker / 40% taker fallback | $159,750 | +59.75% | −5.52pp |
| **E-75**) Blended 75% / 25% | $163,760 | +63.76% | −1.51pp |
| **E-85**) Blended 85% / 15% | $166,488 | **+66.49%** | +1.22pp |

## Interpretation

**Scenario C (+44.67%) is the realistic taker baseline.** The HL paper headline
overstates returns by ~20pp over this period due to under-modeled fees (ETH in
particular is ~2× HL's 5bps assumption). Slippage turns out to be a near-zero
add, despite the raw signal looking like 5-7 bps — that signal was basis, not
execution cost.

**Scenario D (+70.67%) is a theoretical ceiling, not a target.** It assumes
100% maker fill and full half-spread capture. Not achievable in production
for two reasons:
1. Maker orders don't always fill in a bar (HL's 30min bars give time, but
   fast-moving markets will leave orders stranded).
2. Adverse selection — maker fills occur preferentially when the market is
   moving against you, so realized fill quality is worse than mid-at-fill-time.

**Scenarios E-60/75/85 bracket the Phase 2 maker pilot's actual value.** The
pilot's kill-switch threshold of 65% fill rate is well-calibrated: below ~60%,
blended execution underperforms pure-taker with slippage; above 75%, it
approaches the HL paper headline.

## Cross-check: maker shadow sims on the VM

Four live maker-aggressiveness sims are running against the same signal stream
(`live/state/maker_shadow_{passive,quarter,mid,aggressive}_paper_state.json`).
Over Apr 10–14 (~85 trades):

| Aggressiveness | 5-day return |
|---|---:|
| Aggressive | −6.4% |
| Mid | −6.0% |
| Passive | −5.2% |
| Quarter | −5.2% |
| *Live taker (same period, comparable sample)* | *−4.8%* |

All four maker sims underperform the live taker. This contradicts scenarios
D/E above. Hypothesis per Dan: the maker shadow is skipping fills entirely
when orders don't execute in the time window — so "maker" performance is
really "maker attempts with no fallback," which systematically misses the
trades that made money. The production Phase 2 pilot uses a hybrid
maker-then-taker-fallback path specifically to avoid this.

**This is the open question to investigate separately (see follow-up doc).**
If confirmed, the E scenarios understate the real-world value because they
assume the blend happens trade-by-trade, not signal-by-signal; a proper
hybrid sim would fill at maker price OR taker price but never miss.

## Action items

1. **Stop citing +65.3% unqualified.** All external reporting (board decks,
   marketing, investor comms) should use +44.67% (scenario C) as the
   realistic baseline for this period.
2. **Calibrate kill switches to scenario C, not A.** The 10% drawdown halt
   threshold should be anchored to realistic equity, not paper equity.
3. **Phase 2 pilot is the lever.** At 75%+ fill rate the realistic return
   approaches the HL paper headline. Below 60%, it hurts. The kill-switch
   threshold at 65% is correctly placed.
4. **Investigate maker shadow underperformance** (next doc). Does the hybrid
   fallback recover the performance the shadow sims lose?
5. **ETH-drop sensitivity test.** ETH carries ~16 bps round-trip execution
   cost (9.58 fee + ~0 slip + 5.39 spread for any maker fallback) vs BTC's
   ~8. Worth testing whether dropping ETH preserves most alpha at lower cost.

## Raw inputs & reproducibility

- HL trade log: `strategies/30m-concentrated/paper_state.json` (`trade_log`
  field, 1,297 entries). Local snapshot at `/tmp/hl_paper_state.json`.
- CB live-paper trades: `live/logs/trades_{2026-04-11..14}.jsonl` (473 total,
  of which 386 have fee > 0). Local snapshot at `/tmp/cb_trades.jsonl`.
- Reconciliation lines: 128 from `live/logs/30m-concentrated-live-cron.log`.
  Local snapshot at `/tmp/reconcile.log`.
- Maker shadow book snapshots: 198 from `live/logs/maker_shadow_*.jsonl`.
  Local snapshot at `/tmp/maker_shadow.jsonl`.
- Simulation code: `/tmp/simulate_v2.py` (corrected adverse-only slippage).

## Known limitations

- **18-day sample.** HL paper period (Mar 27–Apr 14) is a single regime.
  Scenario C = +44.67% is what that regime produced with realistic fees.
  A different regime may have very different fee/slippage sensitivity.
- **Funding rates not modeled.** CB perps charge/pay funding on open
  positions across funding windows. Strategy holds ~30min, so most trades
  won't cross a funding boundary, but some do. Likely <1 bps amortized, but
  unverified.
- **Size-scaling of market impact not modeled.** Current notionals (~$4K)
  are trivial vs CB book depth. If equity compounds to $500K+, impact at
  larger notionals becomes non-linear. Extrapolation may under-count cost.
- **Cross-venue basis treated as free.** The persistent ~2 bps drift
  between the strategy's internal feed and CB fills cancels in round-trips,
  but in regimes where positions are held across basis inversions, it could
  matter. Unverified.
