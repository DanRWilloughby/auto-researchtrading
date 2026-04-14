# Planned Fixes — Live Trading Infrastructure

## Status snapshot (as of 2026-04-14 21:00 UTC)

| Fix | Status | Branch | Deployed to VM |
|---|---|---|---|
| Fix 1 — SKIP tolerance | ✅ DEPLOYED | `fixes/phase1-infrastructure` | 2026-04-14 ~20:43 UTC |
| Fix 2 — DD threshold widening | ✅ DEPLOYED (24h DD: 15% → 10%) | `fixes/phase1-infrastructure` | 2026-04-14 ~20:43 UTC |
| Fix 3 — halt close/reduce only | ✅ DEPLOYED | `fixes/phase1-infrastructure` | 2026-04-14 ~20:43 UTC |
| Fix 4 — auto-reset cooldown | ✅ DEPLOYED | `fixes/phase1-infrastructure` | 2026-04-14 ~20:43 UTC |
| Fix 5 — HWM realized-only | ✅ DEPLOYED | `fixes/phase1-infrastructure` | 2026-04-14 ~20:43 UTC |
| Per-fix attribution logging | ✅ DEPLOYED | `fixes/phase1-infrastructure` | 2026-04-14 ~20:43 UTC |
| Phase 1 monitoring aggregator | ✅ DEPLOYED | `fixes/phase1-infrastructure` | 2026-04-14 ~20:43 UTC |
| Fix 6 — BTC paired maker pilot | 🟡 BUILT, NOT DEPLOYED | `fixes/phase2-maker-pilot` | — |
| Fix 7 — Dashboard tab | 📋 SPEC ONLY (handoff) | n/a | n/a |

**Phase 1 deploy notes (2026-04-14):**
- All Phase 1 fixes shipped together to VM as planned
- `peak_equity` reset to $10,000 in live, paper-cb-early, paper-175x state files
- 69 phantom equity_curve points (above $10K) clamped on live state file
- Existing kill flag from cascade events deleted manually after deploy
- First post-deploy live tick (20:44 UTC): clean trader startup, `Risk state restored: high_water=$10,000.00`, strategy fired 3 SELL orders (BTC/ETH/SOL), HWM dual-track logging confirmed working in `/home/openclaw/auto-researchtrading/live/logs/hwm_track_2026-04-14.jsonl`
- Backup of pre-deploy files at `/home/openclaw/auto-researchtrading/.backup-pre-phase1/`

**Phase 2 status:**
- Built and tested locally on `fixes/phase2-maker-pilot` branch
- 122 unit tests + 22 Phase 2 smoke checks all pass
- DISABLED BY DEFAULT in config (`maker_pilot.enabled_symbols: []`)
- Recommend waiting 3+ days for Phase 1 to stabilize before deploying Phase 2 — see "Suggested order of operations" below

---

This document captures infrastructure fixes identified during the Apr 11-14 live trading analysis. Each item has: what to change, why, evidence, implementation notes, risks, and validation criteria.

Session context: `30m-concentrated` strategy went live Apr 11 19:08 UTC on Coinbase perps with $10K. After 3 days: net -$428. Analysis revealed ~$840 of the loss is attributable to operational bugs (not strategy-level losses). These fixes address those bugs.

## Confirmed Coinbase fee structure (from account screen, verified 2026-04-14)

**Account tier: VIP 4**

| Fee component | Taker | Maker | Difference |
|---|---:|---:|---:|
| CB commission (derivatives) | 3.0 bps | 2.5 bps | **0.5 bps savings** |
| NFA + exchange + clearing | $0.15/contract | $0.15/contract | applies to both sides |
| Coinbase One | 25% rebate on commission | 25% rebate on commission | — |

**Effective per-coin rates (taker, no rebate assumed in fills):**

| Coin | Contract notional | CB variable | Fixed passthrough | Total observed |
|---|---:|---:|---:|---:|
| BTC | ~$748 | 3.0 bps | ~2.0 bps | ~5.08 bps |
| ETH | ~$238 | 3.0 bps | ~6.3 bps | ~9.65 bps |
| SOL | ~$430 | 3.0 bps | ~3.5 bps | ~6.59 bps |

**Maker savings per trade = 0.5 bps.** Does NOT vary by coin — the $0.15 fixed passthrough applies to both maker and taker (it's NFA/exchange/clearing, not CB commission). ETH remains the most expensive coin post-maker because its effective rate is dominated by the fixed fee on small contract notional.

**Not a 4 bps savings** as earlier analyses assumed (those were using spot rates 6.5→2.5 bps, not derivatives 3.0→2.5 bps).

**VIP tier note:** User qualifies for VIP 4 via combined spot+derivatives volume or asset balance, not derivatives volume alone (derivatives-only at $846K/30d would be the $500K-$1M tier at 5.5/6.0 bps). Further tier reductions require asset balance growth or spot volume — not driven by derivatives trading.

---

## Fix 1 — SKIP bug in live market order path

**What to change:** Add a notional-delta tolerance check to `place_market_order` so it skips orders when the position is already close to target, matching the paper simulator's behavior.

**Why:** Current code compares *integer contracts* only (delta_contracts == 0 → SKIP). Paper simulator compares *notional delta* with a $200 tolerance. As a result, live fires orders when paper would skip — paying fees on essentially-no-change trades.

**Evidence:**
- Live cron log (3-day window): 139 STARTUPs, 88 actioned signals, **0 "already at target, skip" events**
- Paper-CB-early cron log (same window): 137 STARTUPs, 94 actioned signals, **77 "already at target, skip" events**
- Cost estimate: 77 extra fills × ~$3/fill = **~$231 over 3 days** (~$77/day, ~$28K/year at current capital)

**Files to touch:**
- `exchanges/coinbase_client.py:496-585` — `place_market_order()`. Add notional tolerance check BEFORE computing integer contracts.
- Reference paper's equivalent check: `live/trader.py:340-347` in `_execute_paper_sim()`.

**Implementation sketch:**
```python
# In place_market_order(), after getting current_price and current_contracts:
delta_notional = abs(target_notional_usd - (current_contracts * spec.contract_size * current_price))
if delta_notional < SKIP_TOLERANCE_USD:  # pull from shared config, not hardcoded
    return OrderResult(success=True, side="SKIP", ...)
```

**Hygiene:** pull `SKIP_TOLERANCE_USD` from a shared config so paper and live cannot drift apart. Both should read from the same source of truth.

**Risks:**
- If threshold too high, live's position drifts from target. Bounded by threshold itself — drift can never exceed `SKIP_TOLERANCE_USD` because it then triggers a trade.
- Verify the existing paper $200 value is appropriate for current contract sizes (BTC $750/contract, ETH $240, SOL $430). $200 may be slightly below one ETH or SOL contract — check behavior.

**Do NOT add:** a "force trade after N consecutive skips" counter. Drift is already bounded by the threshold; adding a counter introduces state and rules that can force unwanted trades. Paper doesn't have it.

**Validation:**
- After deploy, live cron log should show "already at target, skip" events appearing
- Fill count per bar should drop toward paper's rate
- Per-coin fees should drop proportionally
- Strategy P&L (gross, pre-fee) should not change — only fees drop

**Expected impact:** +$77/day net by removing the fee leak. Does NOT fix execution edge gap with paper.

---

## Fix 2 — Drawdown threshold recalibration

**What to change:** Raise the 24h drawdown kill threshold from 5% to 10-12% on the daily check. Align with the existing 10% global kill switch so there is no double-trigger zone.

**Why:** The strategy's natural drawdown distribution (4% backtest max, 5-8% in live conditions) is colliding with the 5% threshold. The CB fires on normal strategy volatility rather than on true tail events. Each trigger has created a cascade (halt → kill flag persistence → forced flatten-style losses).

**Evidence:**
- Strategy backtest max DD over full test split: ~4%
- Live DD events over 3-day window:
  - Apr 13 18:44: "24h drawdown 8.45% from peak $10,880.58" → cascade -$145
  - Apr 14 09:44: "24h drawdown 5.65% from peak $10,233.95" → cascade **-$463** (biggest single loss)
- Paper HL strategy has had comparable drawdowns over 18 days without a kill-flag and recovered fully (ending +1540%)

**Files to touch:**
- Risk manager configuration (search for `24h`, `drawdown`, `0.05`, `5%` in `risk/` directory and `live/trader.py`)
- Cron log strings to grep: `"TRADING HALTED: 24h drawdown"`

**Risks:**
- Higher threshold means larger potential loss before protection triggers. However, 10-12% is still well below any catastrophic scenario (flash crash / exchange failure, which are 20%+).
- If the strategy genuinely degrades and drawdowns increase, the wider threshold delays detection. Mitigation: keep the existing 10% global kill as a backstop.

**Validation:**
- Monitor DD events after deploy. Under normal conditions, should be rare (<1/month).
- If DD events still fire at 10%+, that's genuine strategy problem worth investigating, not misfire.

**Expected impact:** Prevents Apr 14 09:44 cascade entirely (5.65% DD would not trigger). Saves ~$463 per event of that magnitude.

---

## Fix 3 — Halt behavior: stop new entries only, do not force flatten

**What to change:** On kill-flag trigger, halt NEW position entries but let existing positions ride to their natural TP / SL / strategy-driven exits. Do not force flatten on trigger.

**Why:** Force-flatten locks in the loss at the worst possible moment (peak drawdown). The strategy's edge on mean-reverting moves comes precisely from letting positions complete. Halting new entries preserves safety (prevents runaway strategy from compounding bad signals) while keeping the exit mechanics that capture the strategy's edge.

**Evidence:**
- Apr 14 09:44 event: equity dropped $463 at peak DD. Positions that would have recovered on the subsequent mean-reversion were flattened instead.
- Paper HL let similar drawdowns ride across the 18-day run and recovered each time.
- The backtest's low max-DD (4%) implicitly assumes positions complete — the engine doesn't force flatten on a running DD.

**Files to touch:**
- Risk manager halt action (search for `halt`, `flatten`, `close_all_positions` in `risk/` directory)
- Identify whether current code calls `close_all_positions()` or similar on trigger

**First step: verify current behavior.** Read the code to confirm whether the kill-flag currently forces flatten or only halts new entries. If it only halts new entries, this fix is not needed.

**Risks:**
- Open positions can continue to move against you. But this is bounded by the strategy's own TP / SL logic (which would fire anyway).
- If the strategy is genuinely broken (e.g., bad signals), open positions could compound losses. Mitigation: combine with Fix 2 (10% hard limit) — at 10% actual DD, force flatten is appropriate.

**Validation:**
- After deploy, simulate or replay a DD trigger event. Confirm open positions are NOT closed, new signals are ignored.
- Check equity curve recovery post-trigger vs pre-fix events.

**Expected impact:** Biggest single lever of the three risk changes. Apr 14 09:44 cascade would have stayed around -$100 instead of -$463.

---

## Fix 4 — Auto-reset cooldown on kill flag

**What to change:** Replace manual kill-flag clearing with automatic cooldown. After the flag is set, every cron fire checks: if no new DD breach in the past 2 hours, auto-clear the flag.

**Why:** Current kill-flag persists indefinitely until a human removes it. Every cron fire during persistence logs "manual kill flag detected" and creates a cascade entry. Automation prevents the cascade + removes the human-in-the-loop failure mode (e.g., if user is asleep when the flag fires, the flag persists across many bars unnecessarily).

**Evidence:**
- Apr 14 cron log shows many consecutive "manual kill flag detected" ERROR entries after the initial trigger
- Each cascade entry was 30 min of no trading — lost opportunity even after conditions improved

**Files to touch:**
- Kill flag creation/read logic (grep `kill.flag`, `write_kill_flag`, `manual_kill_flag`)
- `live/trader.py` kill flag handling in the tick function

**Implementation notes:**
- Track last_dd_breach_ts in state
- On each tick: if kill_flag_exists AND now - last_dd_breach_ts > 2_hours AND current_dd < threshold: remove flag
- If a new DD breach occurs inside the cooldown, reset the timer and require the FULL cooldown period to clear

**Risks:**
- Auto-reset could re-enter into still-deteriorating conditions. Mitigation: require current DD to be BELOW threshold at clear time, not just time-based.
- Re-entry after cooldown might hit the same condition immediately. Mitigation: if DD breaches again within N hours of auto-clear, require manual intervention.

**Validation:**
- After deploy, simulate a halt event.
- Confirm: (a) cooldown clears after 2 hours if DD stays below threshold, (b) re-breach during cooldown resets timer, (c) trading resumes cleanly post-clear.

**Expected impact:** Prevents the cascade log spam and restores trading automatically after transient blips. Works synergistically with Fix 2 (fewer triggers) and Fix 3 (less destructive trigger).

---

## Fix 5 — High watermark calculation (CONFIRMED ROOT CAUSE)

**Status:** Root cause confirmed by code trace. **This is priority #1 — it likely caused both cascade events this week.**

**What's wrong:** The circuit breaker ratchets its high-water-mark on *mark-to-market* equity, so transient unrealized gains become permanent peaks. When positions later close at lower fill prices, the HWM stays inflated, and subsequent drawdown calculations measure against phantom peaks.

**Exact mechanism:**

1. `live/trader.py:475-477`:
   ```python
   unrealized_sum = sum(p.unrealized_pnl_usd for p in positions.values())
   equity = cash + unrealized_sum    # mark-to-market, not settled
   ```
2. `risk_mgr.update_account_state(equity=equity, ...)` passes MTM to the circuit breaker.
3. `risk/circuit_breaker.py:80-83`:
   ```python
   def update_equity(self, equity: float) -> None:
       if equity > self.high_water:
           self.high_water = equity    # ratchets on MTM spikes
   ```
4. Any tick where a position has a momentary unrealized gain locks that spike into HWM permanently.

**Evidence:**
- Apr 13 18:44 trigger: "24h drawdown 8.45% from peak $10,880.58" — the peak likely never existed as settled equity; it was a transient MTM spike from open positions
- Apr 14 09:44 trigger: "24h drawdown 5.65% from peak $10,233.95" — same pattern
- User observation: on one event, peak was inflated ~$800 by a position close that hadn't settled yet
- Real settled drawdowns during this week were probably 2-4% — well within the 5% threshold if measured correctly

**The fee accounting thing is SEPARATE** — it's a dashboard/reporting bug, not the HWM root cause:
- `live/trader.py:263` (live branch): `cumulative_realized_pnl_total = available_margin - initial_equity`, `cumulative_fees_total` is never written
- `live/trader.py:293` (paper branch): both fields written from trade log sums
- Net: on live instances, `cumulative_fees_total` in state is stale/unset. This affects dashboard numbers but NOT the circuit breaker.
- Fix this separately for clean reporting, but don't conflate it with the HWM fix.

**Recommended fix — hybrid approach:**

Use realized equity for HWM ratcheting, keep MTM equity for current-equity DD check. Asymmetric on purpose.

```python
# In circuit_breaker.update_equity — take two values:
def update_equity(self, mtm_equity: float, realized_equity: float) -> None:
    # HWM only ratchets on SETTLED gains. Transient MTM spikes do nothing.
    if realized_equity > self.high_water:
        self.high_water = realized_equity
    # Store MTM for current-equity DD check
    self.equity_history.append(EquityPoint(time.time(), mtm_equity))
    ...

# In check() — DD uses MTM equity vs realized HWM:
dd_from_hw_pct = (self.high_water - mtm_equity) / self.high_water * 100
```

**Why this asymmetry is correct:**
- Unrealized GAINS shouldn't count toward HWM (transient; may evaporate at close)
- Unrealized LOSSES should count in the DD check (real risk; protect against runaway positions)
- HWM becomes a monotonic ratchet of settled peaks
- Positions closing profitably naturally ratchet HWM up (realized_equity increases)

**Also fix the 24h rolling peak:**

```python
# In check():
peak_24h = max(p.equity for p in self.equity_history)  # currently uses MTM
```

Either store realized-only in history OR store both and use realized for the peak. Same principle.

**Files to touch:**
- `risk/circuit_breaker.py` — `update_equity()` and `check()` signatures, equity_history storage
- `risk/manager.py:152-167` — `update_account_state()` signature to accept both MTM and realized
- `live/trader.py:460-505` — compute `realized_equity` alongside `equity` and pass both

**Realized equity calculation:**

What is "realized equity" exactly? Probably:
```python
realized_equity = initial_equity + cumulative_realized_pnl - cumulative_fees
```
OR equivalently, since CB's cash balance reflects all realized flows:
```python
realized_equity = cash  # if cash already excludes open position margin
```
Verify which interpretation matches CB's `get_cash_balance_usd()` semantics before committing.

**Risks:**
- If realized_equity is computed incorrectly, HWM could drift the wrong way. Validate with backtest replay.
- Slight behavior change: HWM no longer updates on unrealized gains. Users watching the peak number may see it lag actual equity. This is correct behavior but worth noting.

**Validation:**
- Replay Apr 13-14 tick sequence with fixed logic. Confirm both cascade triggers don't fire under corrected HWM.
- Confirm HWM trajectory is monotonic on realized P&L, ignores MTM noise.
- Verify DD calculation still catches real drawdowns (unrealized losses still contribute to current_equity in the ratio).

**Expected impact:** Likely prevents BOTH cascade events this week. Saves ~$608. Makes Fix 2 (DD threshold widening) optional.

**Separate reporting fix (lower priority):**
- In `live/trader.py:263-264`, also write `cumulative_fees_total` for the live branch. Compute it from trade log sums or from CB's fee API. This is purely dashboard correctness — doesn't affect trading.

---

## Fix 6 — Maker pilot on BTC (paired order-split A/B)

**Status:** R&D / infrastructure investment. Small P&L upside at current $10K capital, material upside when capital scales to $100K+.

**What to implement:** For BTC signals only, split each order into two equal legs — one fires as market (taker), one as limit (maker with taker-fallback). Same signal, same wall-clock moment. Produces paired observations for direct execution-method A/B. ETH and SOL remain on current taker execution.

**Why:** Two distinct purposes.

1. **R&D:** Build and battle-test maker infrastructure at small scale so it's ready when capital scales. At $10K with 0.5 bps per-trade savings + ~3 bps typical price improvement, expected P&L benefit is ~$50-80 over a 3-4 day window. Treated as infrastructure investment, not P&L capture.

2. **Measurement:** Paired (maker, taker) observations on the same signal at the same moment let us measure execution-method effect cleanly. Serial comparison (Week A taker vs Week B maker) confounds timing with method. Paired analysis has much higher statistical power — 40-60 paired trades can detect meaningful differences.

**Why BTC specifically:**
- BTC live slippage is favorable (-1.75 bps measured). Safest coin to test.
- Price-improvement upside is real without execution-friction downside masking the signal.
- If pilot succeeds on BTC, extend to ETH next (biggest absolute fee burden).

**Why NOT allocation rewrite:** Strategy already uses `SYMBOL_WEIGHTS = {"BTC": 0.333, "ETH": 0.333, "SOL": 0.334}`. The BTC maker/taker split is within the existing 1/3 BTC allocation. ETH and SOL stay at their current 1/3 taker weights. This is an **execution A/B, not an allocation change.**

**Evidence (with confirmed CB fee structure):**

| Component | Expected value (3-4 days) | Notes |
|---|---:|---|
| Fee savings | +$15-20 | 0.5 bps × ~$10K total BTC notional over window |
| Price improvement on maker fills | +$80-110 | ~3 bps avg on filled limit orders (at 70-85% fill rate) |
| Fallback penalty | -$15-25 | ~20% fallback rate × $2-4/fallback mean |
| Tail event buffer (unmodeled risk) | -$10-20 | Safety margin for vol regimes not in sample |
| **Net maker benefit over 3-4 days** | **+$50-85** | On BTC leg only |

**Files to touch:**
- `live/trader.py:640-700` — order execution flow. Add BTC-specific branching: for BTC signals, call new `place_paired_maker_taker_order()` instead of `place_market_order()`.
- `exchanges/coinbase_client.py` — new method `place_limit_order_with_fallback()`. Places a passive limit at bid (BUY) or ask (SELL), polls fill status, cancels + market-fills at timeout.
- `risk/circuit_breaker.py` — add the `maker_disabled_on_dd_approach` check (if current DD within 20% of trigger threshold, route BTC signals to pure taker until DD clears).
- New config fields in `risk/config.yaml`: `maker_enabled_symbols`, `maker_timeout_sec`, `maker_vol_threshold`, `maker_fallback_max_bps`.

**Implementation sketch:**

```python
# In the BTC signal branch of live/trader.py:
if signal.symbol == "BTC" and maker_config.enabled:
    # Split target notional 50/50
    half_target = signal.target_position / 2
    taker_result = client.place_market_order(symbol="BTC", target_notional_usd=half_target)
    maker_result = client.place_limit_order_with_fallback(
        symbol="BTC",
        target_notional_usd=half_target,
        timeout_sec=maker_config.timeout_sec,  # dynamic: shorter if high vol
        fallback_max_bps=maker_config.fallback_max_bps,  # cancel if price moved >20 bps
    )
    # Log both fills side-by-side as paired observation
else:
    client.place_market_order(...)  # existing behavior for ETH, SOL
```

**Required tail protections (do NOT skip):**

1. **Per-trade fallback circuit breaker.** If price has moved more than `fallback_max_bps` (default 20 bps) adversely during the maker timeout window, CANCEL the maker leg entirely rather than falling back to taker. Caps the worst-case fallback cost.

2. **Vol-aware timeout.** When BTC's trailing 15-min realized vol is above threshold (config: `maker_vol_threshold`), shorten the timeout (e.g., from 300s to 60s). Protects against regime-dependent tail events.

3. **Maker disabled on DD-approach.** If current drawdown is within 20% of the kill-flag threshold, route BTC signals to pure taker until DD clears. A maker miss during a cascade-approaching condition would trigger the CB at the wrong moment.

4. **Sanity check on maker fills.** If maker fill rate drops below 65% over any trailing 3-day window in the pilot, halt the pilot and investigate before continuing.

**Risks:**

- Simulation predicted 95.6% fill rate using 1-min candle touch; real production fill rate likely 67-85% due to queue priority / touch≠fill gap. Pilot measures actual rate.
- 4-day shadow sample doesn't cover FOMC / flash-crash / sustained-drift tail events. A single bad fallback during a 2% bar could cost 50-100 bps ($30-50 in absolute terms). Tail protections above mitigate but don't eliminate this risk.
- Code complexity increases: new order path, polling logic, timeout handling. More surface area for bugs. Mitigated by starting BTC-only at small size.
- $10K scale means sample sizes are small. Expect 40-60 paired BTC trades over 2-3 week pilot. Enough for sign-of-effect, not tight magnitude estimation.

**Validation:**

- After deploy, every BTC signal produces a `(maker_fill_price, taker_fill_price, maker_pnl, taker_pnl)` tuple in logs
- Measure actual maker fill rate vs simulated 70% expectation
- Measure actual fallback penalty distribution vs simulated mean $2.50
- Paired t-test on (maker_pnl - taker_pnl) per trade — target positive mean with statistical power over 40+ trades
- Kill switch: halt pilot if actual fill rate < 65% for 3+ days OR if any single fallback exceeds $30 OR if net maker vs taker diverges from simulation by >50%

**Expected impact:**

- Per-trade: 0.5 bps fee + 3 bps price improvement − ~1 bps fallback cost = ~2-3 bps net per BTC trade
- Over 3-4 days at $10K: ~$50-85 net
- Over a month at $10K: ~$400-650 net
- At $100K scale: 10x larger in absolute terms (~$4K-6.5K/month on BTC alone)
- Primary value at current scale is **R&D**: infrastructure and measurement for when capital scales, and clean paired data on whether maker actually works for this strategy on CB

**Scope expansion path:**

- If BTC pilot succeeds (2-3 week measurement), extend to ETH next
- ETH has highest absolute fee burden ($252 of the $569 total live fees). Even 0.5 bps savings + 3 bps price improvement on ETH is meaningful at any scale.
- SOL last. Worst live slippage means price improvement analysis is harder to validate cleanly.

---

## Data logging requirements for Fix 6 (REQUIRED for pilot to be useful)

The pilot's value is not the P&L — it's the ground-truth measurement. Without structured logging, the pilot produces anecdotes instead of analyzable data. Implement all three layers below before launching.

### 1. Per-trade paired record

Every BTC signal during the pilot MUST emit a record with this schema:

```python
{
    "signal_ts": int,                    # ms UTC, when strategy fired
    "signal_size": float,                # intended total notional (pre-split)
    "taker_fill_px": float,              # actual market fill
    "taker_fill_time": int,              # ms UTC, when taker filled
    "maker_limit_px": float,             # limit price placed
    "maker_fill_px": float | None,       # None if fallback fired
    "maker_fill_time": int | None,       # ms UTC, None if fallback fired
    "maker_fallback_bool": bool,         # True if timeout → market
    "maker_fallback_penalty_bps": float, # (fallback_px - limit_px) / limit_px * 10000, signed
    "realized_vol_15m_at_signal": float  # trailing 15-min BTC realized vol at signal time
}
```

Store to a dedicated append-only log: `live/logs/maker_pilot_<YYYY-MM-DD>.jsonl`. Never overwrite.

### 2. Market context at signal time

For each BTC signal, capture:
- `bid`, `ask`, `spread_bps`
- `book_depth_plus_5bps_usd`, `book_depth_minus_5bps_usd` (notional available within ±5 bps)
- `recent_trade_velocity` (trade count + volume in the 60s before signal)

This lets future analysis correlate fill behavior with market conditions — *when* does maker work, and when doesn't it? Without this, you'll have fill stats but no predictive model for when to route maker vs taker.

### 3. Post-trade P&L attribution

Track each leg (maker and taker) through its full round-trip to close. At close time, emit:

```python
{
    "signal_ts": int,                    # links back to entry record
    "close_ts": int,
    "taker_entry_px": float,
    "taker_exit_px": float,
    "taker_total_pnl": float,
    "maker_entry_px": float,
    "maker_exit_px": float,
    "maker_total_pnl": float,
    "pnl_delta_maker_vs_taker": float    # positive = maker won
}
```

**Why this matters:** you want to know if maker's better entry prices actually translate to better total round-trip P&L, or if the benefit gets eroded by worse exits (or by state divergence, if the legs somehow diverge mid-trade). Entry-price improvement is necessary but not sufficient — total round-trip P&L is the real test.

### The invaluable part: ground truth replaces projection

Current state: every maker-vs-taker projection rests on one of:
- Historical backtests with simulated fills (touch ≠ fill, no queue priority)
- Shadow instances with methodology bugs (we already found the 30-min candle blind spot)
- Theoretical CB fee math (0.5 bps savings, smaller than originally projected)

Three weeks of paired BTC data at $10K gives 40-60 real paired observations. That replaces:

> "We think maker works because [theory]."

with:

> "On our specific strategy, on Coinbase, maker produces X bps improvement at Y% fill rate, with Z distribution of fallback penalties. Here's the data."

That ground truth is the asset. When capital scales to $100K+ and ETH extension becomes the main lever, you deploy from a foundation of measured reality instead of rerunning the entire uncertainty exercise. The 3 weeks of learning are worth more than whatever dollar figure the pilot itself produces.

**If the logging isn't comprehensive, the pilot isn't worth running.** Treat the three logging layers as the primary deliverable and the P&L as noise around zero.

---

## Fix 6 — Analysis workflow (closes the loop from data → decision)

Logging data is only useful if you have a defined plan for consuming it. This section specifies what to verify, what to compute, and what decisions the data drives.

### Stage 0 — Pre-flight validation (first 48 hours)

Before trusting any analysis, confirm logging is working:

- [ ] Every BTC signal in `live/logs/trades_<date>.jsonl` has a matching entry in `live/logs/maker_pilot_<date>.jsonl`. If counts diverge, logging is broken.
- [ ] No paired records have null fields except `maker_fill_px`/`maker_fill_time` (which should be null IFF `maker_fallback_bool=True`).
- [ ] All timestamps are UTC, monotonic within a record (`signal_ts <= taker_fill_time`, `signal_ts <= maker_fill_time` or fallback timestamp).
- [ ] Fill prices within 50 bps of the bar close at `signal_ts`. Wider = data corruption or extreme slippage, either way investigate.
- [ ] `spread_bps`, `book_depth_*`, and `realized_vol_15m_at_signal` are populated on every record (not zero, not null).

**If any check fails, halt the pilot and fix logging before continuing.** Partial data is worse than no data.

### Stage 1 — Weekly health check (every Monday during pilot)

Run these queries on the trailing 7 days of paired records:

| Metric | Formula | Healthy range | Kill-switch threshold |
|---|---|---|---|
| Fill rate | `count(maker_fill_px != null) / count(all)` | 70-90% | <65% for 3+ consecutive days |
| Fallback rate | `count(maker_fallback_bool=True) / count(all)` | 10-30% | >40% |
| Mean fill time | `mean(maker_fill_time - signal_ts)` on filled only | 30-120s | n/a (informational) |
| Mean price improvement | `mean((maker_fill_px - taker_fill_px) × side_sign / taker_fill_px × 10000)` | +1 to +5 bps | <0 bps |
| Max single fallback penalty | `max(abs(maker_fallback_penalty_bps))` | <30 bps | >50 bps single event |

If any kill-switch threshold is breached, halt pilot immediately and investigate.

### Stage 2 — End-of-pilot analysis (after 2-3 weeks / 40+ paired observations)

**Primary question:** does maker produce better total round-trip P&L than taker, on matched pairs?

**Statistical test:** paired sign test or paired t-test on `pnl_delta_maker_vs_taker` across all closed trades.

- Null hypothesis: maker and taker produce equivalent P&L (delta ~ 0)
- Alternative: maker produces higher P&L (delta > 0)
- Power check: at N=40, a ~2 bps/trade mean improvement with ~10 bps stdev gives roughly 70% power at α=0.05. Below that, the test is underpowered — report the point estimate with confidence interval, don't claim significance.

**Secondary analyses:**

1. **Fill-rate vs realized volatility.** Bin paired records by `realized_vol_15m_at_signal` (low/mid/high). Compute fill rate per bin. If fill rate degrades sharply in the high-vol bin, that informs the vol-aware timeout calibration.

2. **Price improvement vs spread.** Scatter `(spread_bps, maker_price_improvement)`. Tight spreads → small improvement (maker fills close to taker price). Wide spreads → larger improvement. Use this to model expected improvement in different conditions.

3. **Fallback penalty distribution.** Plot the distribution of `maker_fallback_penalty_bps`. Compute p50, p95, p99, max. If the tail is fatter than simulated (p99 > 30 bps), the vol-aware timeout needs tightening.

4. **Regime-conditional P&L.** Compare maker vs taker delta in high-vol vs low-vol regimes. If maker wins in low-vol but loses in high-vol, the right deployment is "maker only when vol is below threshold" rather than "always maker."

### Stage 3 — Decision criteria at end of pilot

| Outcome | Extend to ETH? | Action |
|---|:---:|---|
| Mean P&L delta positive AND significant (p < 0.1) AND no kill-switches tripped | Yes | Ship ETH maker pilot using same architecture, calibrated with BTC-measured parameters |
| Mean P&L delta positive but underpowered (p > 0.1) | Continue BTC pilot | Run another 2 weeks to accumulate power before deciding |
| Mean P&L delta near zero or negative | No | Halt maker pilot. Document why fallback penalty or fill rate ate the benefit. Consider re-piloting after exchange conditions change. |
| Any kill-switch tripped mid-pilot | No | Halt, root-cause, decide whether to resume after fix |

### Stage 4 — Extrapolation to ETH/SOL (if BTC succeeds)

BTC data calibrates ETH expectations but does not replace measurement. Adjustments:

- **Fill rate on ETH:** expect 5-10 pp lower than BTC (thinner book). If BTC fills 85%, model ETH at 75-80%.
- **Price improvement on ETH:** likely similar in bps terms (correlated with spread, not depth).
- **Fallback penalty on ETH:** expect tail to be ~20-30% larger than BTC.

Build ETH pilot with same paired-logging architecture. Do not assume BTC numbers transfer directly — the pilot is the measurement, not the projection.

### Deliverables at end of pilot

Produce a short report (max 2 pages) answering:

1. What was the observed fill rate, overall and by vol regime?
2. What was the observed price improvement distribution, overall and by spread condition?
3. Did maker beat taker on paired round-trip P&L? Point estimate, confidence interval, statistical significance.
4. What's the recommendation for ETH extension?
5. What would need to change if deploying at 10x capital scale?

Write the report for the version of you 6 months from now who will have forgotten all of this context. Include the data file paths, the queries run, and the exact thresholds used.

---

## Phase 1 logging and analysis workflow (Fixes 1, 3, 4, 5)

Phase 1 ships 4 fixes together. True isolation is impossible — we can only compare combined behavior against pre-fix baseline. But per-fix attribution is recoverable IF each fix logs the event it affects with a counterfactual ("what would have happened under old behavior"). Implement the logging below so the combined Phase 1 change can be decomposed to individual fixes.

### Pre-flight: snapshot the pre-fix baseline (BEFORE shipping any fix)

Capture the following from the current live state file and trade log, covering at minimum the Apr 11-14 window:

```python
baseline = {
    "window_start": "2026-04-11T19:08Z",
    "window_end": "2026-04-14T15:00Z",
    "window_days": 3.0,
    "fills_per_day": 188 / 3.0,                # ~63 fills/day pre-fix
    "avg_fee_per_fill_usd": 569 / 188,         # ~$3.03
    "total_fees_usd": 569.03,
    "gross_pnl_usd": 140.90,
    "net_pnl_usd": -428.13,
    "cb_trigger_events": 2,                    # Apr 13 18:44 + Apr 14 09:44
    "cb_trigger_losses_usd": 608,              # estimated cascade attribution
    "manual_kill_cascades": 10,                # count of "manual kill flag detected" persistence events
}
```

Save to `live/logs/baseline_pre_phase1.json`. This is the reference point every post-fix measurement compares against.

### Fix 1 (SKIP bug) — logging requirements

Every invocation of `place_market_order()` that takes the new SKIP path must emit a record:

```python
{
    "ts": int,
    "symbol": str,
    "target_notional_usd": float,
    "current_notional_usd": float,
    "delta_notional_usd": float,               # should be abs() < SKIP_TOLERANCE_USD
    "implied_fee_avoided_usd": float,          # abs(delta) * taker_fee_rate(symbol)
    "tolerance_used_usd": float,               # should equal config SKIP_TOLERANCE_USD
}
```

Append to `live/logs/skip_events_<date>.jsonl`.

**Attribution metric:** `sum(implied_fee_avoided_usd)` over a window = dollar savings from Fix 1.

### Fix 5 (HWM) — dual-track logging requirements

During the transition period (first 2-4 weeks post-fix), log BOTH the new realized-HWM and the old MTM-HWM at every tick. This lets you confirm the new method prevents phantom peaks AND measure how many CB triggers the old method would have fired.

At every risk manager tick, emit:

```python
{
    "ts": int,
    "mtm_equity": float,                       # cash + unrealized_sum
    "realized_equity": float,                  # cash only, or initial + cum_realized - cum_fees
    "hwm_new_realized": float,                 # what new code tracks
    "hwm_old_mtm": float,                      # what old code would have tracked (shadow-compute for comparison)
    "dd_new_pct": float,                       # (hwm_new - mtm_equity) / hwm_new * 100
    "dd_old_pct": float,                       # (hwm_old - mtm_equity) / hwm_old * 100
    "would_old_trigger": bool,                 # dd_old_pct >= threshold
    "did_new_trigger": bool,                   # dd_new_pct >= threshold
}
```

Append to `live/logs/hwm_track_<date>.jsonl`.

**Attribution metric:** `count(would_old_trigger=True AND did_new_trigger=False)` = spurious triggers prevented by Fix 5. Multiply by average cascade loss (~$300 baseline estimate) for dollar attribution.

### Fix 3 (halt behavior) — logging requirements

When the circuit breaker DOES fire under the new halt-new-entries-only logic, record the counterfactual — what positions would have been force-flattened under the old logic:

```python
{
    "ts": int,
    "trigger_reason": str,
    "hwm_at_trigger": float,
    "equity_at_trigger": float,
    "open_positions": {sym: notional, ...},    # positions that would have been flattened under old halt
    "marks_at_trigger": {sym: price, ...},     # mark prices at trigger time
    "marks_at_reentry": {sym: price, ...},     # prices when trading resumed
    "counterfactual_flatten_cost_usd": float,  # (mark_at_reentry - mark_at_trigger) * position, signed
}
```

Append to `live/logs/halt_events_<date>.jsonl`.

**Attribution metric:** `sum(counterfactual_flatten_cost_usd)` = losses avoided by Fix 3 when CB fires.

### Fix 4 (auto-reset cooldown) — logging requirements

Every auto-clear event emits:

```python
{
    "ts_trigger": int,                         # when kill flag was set
    "ts_cleared": int,                         # when cooldown auto-cleared it
    "cooldown_duration_sec": int,
    "dd_at_clear_pct": float,                  # should be below threshold
    "missed_ticks_avoided": int,               # count of cron fires that would have logged "manual kill flag detected" under old manual-only logic
}
```

Append to `live/logs/cooldown_events_<date>.jsonl`.

**Attribution metric:** `sum(missed_ticks_avoided × avg_signal_value)` = value of trading opportunities recovered by Fix 4. Hard to price directly; track as operational metric.

### Pre-flight validation (first 24 hours after ship)

Before trusting any analysis, confirm:

- [ ] `baseline_pre_phase1.json` exists and contains all expected fields
- [ ] `skip_events_<date>.jsonl` has at least 1 SKIP event per day (if zero, SKIP check isn't firing — logging or logic bug)
- [ ] `hwm_track_<date>.jsonl` has entries at every tick, and `hwm_old_mtm >= hwm_new_realized` always (new HWM should be equal or lower than old)
- [ ] If no halt events, `halt_events_<date>.jsonl` may be empty — that's expected (and desirable)
- [ ] Existing `trades_<date>.jsonl` still being written and unchanged in format

If any check fails, the fix is suspect. Halt and investigate before continuing.

### Weekly attribution report (end of each week)

Compile per-fix impact from the logs:

| Fix | Metric | Week 1 | Week 2 | Week 3 | Cumulative | vs baseline |
|---|---|---:|---:|---:|---:|---:|
| Fix 1 | fees avoided | $X | $X | $X | $X | -$X/day |
| Fix 5 | spurious triggers prevented | N events | N | N | N | -$X |
| Fix 3 | flatten cost avoided (if triggers fired) | $X | $X | $X | $X | -$X |
| Fix 4 | bars recovered from cooldown | N | N | N | N | — |

Compare the cumulative attributed savings to the actual P&L delta vs baseline:

- If `sum(Fix 1 + Fix 5 + Fix 3) ≈ actual P&L improvement`, attribution is trustworthy.
- If `sum(fixes) >> actual improvement`, something else is hurting (strategy drift, unfavorable market). Flag for investigation.
- If `sum(fixes) << actual improvement`, something else is helping. Also worth investigating — may indicate a secondary benefit we didn't anticipate.

### End-of-Phase-1 report (after 2-3 weeks)

Produce a 1-2 page report answering:

1. What was the actual P&L delta vs `baseline_pre_phase1.json`?
2. How much of the delta is attributable to each fix individually?
3. Does the sum of attributions match the observed change?
4. Any surprises — fixes that over- or under-delivered vs projection?
5. Is Phase 1 stable enough to start Phase 2 (maker pilot)?

Decision: **proceed to Phase 2 only if attribution shows Fix 1 + Fix 5 are both working as designed AND there are no unexpected negative effects.** A messy Phase 1 attribution means adding Phase 2 complexity on top will compound measurement difficulty.

### Why this matters

Without per-fix logging, Phase 1 lands as "we fixed some bugs and P&L improved." That's unsatisfying and means we can't diagnose if one fix regresses later. With the logging specified here, you can point at each fix and say: "this one saved $X, this one prevented Y events, here's the data." That's what makes the doc a reproducible artifact rather than a collection of intuitions.

---

## Fix 7 — Dashboard monitoring tab ("Phase 1/2 Health")

**Goal:** Visual dashboard tab that displays Phase 1 attribution + Phase 2 maker pilot health, refreshed on the existing dashboard sync schedule.

**Dashboard URL:** https://dashboard-green-nu-53.vercel.app/

**Approach:** The dashboard is a separate static deploy on Vercel that reads JSON files from `dashboard/public/data/`. Phase 1/2 monitoring follows the same pattern — the VM writes status JSONs, existing sync process pushes them, dashboard tab renders them. No new infrastructure required.

### Data files the VM produces (spec — not yet implemented)

All paths relative to VM repo root: `/home/openclaw/auto-researchtrading/`.

#### `monitoring/baseline.json` — the pre-fix reference snapshot

Written ONCE before shipping Phase 1. Never updated after that. Contains the baseline metrics from `live/logs/baseline_pre_phase1.json`:

```json
{
  "captured_at": "2026-04-14T15:00:00Z",
  "window_start": "2026-04-11T19:08Z",
  "window_end": "2026-04-14T15:00Z",
  "window_days": 3.0,
  "fills_per_day": 62.7,
  "avg_fee_per_fill_usd": 3.03,
  "total_fees_usd": 569.03,
  "gross_pnl_usd": 140.90,
  "net_pnl_usd": -428.13,
  "cb_trigger_events": 2,
  "cb_trigger_losses_usd": 608,
  "manual_kill_cascades": 10
}
```

#### `monitoring/phase1_attribution.json` — updated every cron tick

Current running counters for each Fix 1/3/4/5 attribution metric. Cumulative from ship date.

```json
{
  "updated_at": "2026-04-21T14:00:00Z",
  "phase1_ship_date": "2026-04-15T18:00:00Z",
  "days_live": 6.0,
  "fix_1_skip_bug": {
    "skip_events_fired": 287,
    "fees_avoided_usd": 823.50,
    "per_day_rate_usd": 137.25
  },
  "fix_5_hwm": {
    "ticks_logged": 288,
    "hwm_new_current": 10250.40,
    "hwm_old_current": 10520.80,
    "phantom_triggers_prevented": 3,
    "dollar_attribution_usd": 900
  },
  "fix_3_halt_behavior": {
    "cb_trigger_events_since_ship": 1,
    "flatten_cost_avoided_usd": 145
  },
  "fix_4_cooldown": {
    "auto_clear_events": 1,
    "manual_intervention_events": 0,
    "bars_recovered": 8
  },
  "sum_attributed_savings_usd": 1868.50,
  "actual_pnl_delta_vs_baseline_usd": 1742.10,
  "attribution_match_pct": 93.2
}
```

#### `monitoring/phase2_maker_pilot.json` — updated every cron tick when Phase 2 is active

Current state of the maker pilot on BTC:

```json
{
  "updated_at": "2026-04-28T14:00:00Z",
  "pilot_active": true,
  "pilot_ship_date": "2026-04-22T18:00:00Z",
  "days_live": 6.0,
  "paired_observations_total": 52,
  "fill_rate_pct": 82.7,
  "fallback_rate_pct": 17.3,
  "mean_fill_time_sec": 47,
  "mean_price_improvement_bps": 2.8,
  "p95_fallback_penalty_bps": 12.4,
  "max_single_fallback_usd": 14.20,
  "paired_pnl_delta_mean_usd": 1.85,
  "paired_pnl_delta_total_usd": 96.20,
  "statistical_power": {
    "n": 52,
    "p_value": 0.08,
    "significant_at_0_1": true,
    "confidence_interval_95": [-0.20, 3.90]
  }
}
```

#### `monitoring/kill_switch_status.json` — updated every cron tick

Traffic-light status for each kill-switch. Dashboard renders as colored indicators.

```json
{
  "updated_at": "2026-04-28T14:00:00Z",
  "switches": [
    {
      "name": "maker_fill_rate",
      "status": "green",
      "current_value": "82.7%",
      "threshold": "65% for 3 consecutive days",
      "distance_to_trigger": "17.7pp",
      "days_in_warning": 0
    },
    {
      "name": "maker_fallback_penalty_single_event",
      "status": "green",
      "current_value": "$14.20 max",
      "threshold": "$30 single event",
      "distance_to_trigger": "$15.80"
    },
    {
      "name": "hwm_drift_detection",
      "status": "green",
      "current_value": "0 ticks with hwm_new > hwm_old",
      "threshold": "0 (invariant)",
      "invariant_holds": true
    },
    {
      "name": "dd_approach",
      "status": "green",
      "current_value": "2.1%",
      "threshold": "10%",
      "distance_to_trigger": "7.9pp"
    }
  ]
}
```

#### `monitoring/recent_events.json` — rolling 7-day event log

Last 50 notable events for the "recent activity" feed on the tab.

```json
{
  "updated_at": "2026-04-28T14:00:00Z",
  "events": [
    {"ts": "2026-04-28T13:44:00Z", "type": "skip_fired", "symbol": "ETH", "fee_avoided_usd": 2.45},
    {"ts": "2026-04-28T13:14:00Z", "type": "maker_fill", "symbol": "BTC", "price_improvement_bps": 3.8},
    {"ts": "2026-04-27T22:14:00Z", "type": "maker_fallback", "symbol": "BTC", "penalty_bps": 8.2, "penalty_usd": 4.10},
    {"ts": "2026-04-26T09:44:00Z", "type": "hwm_phantom_prevented", "dd_old_pct": 6.4, "dd_new_pct": 2.1}
  ]
}
```

### Dashboard tab layout (design spec for whoever builds it)

Tab name: **"Phase 1/2 Health"** or **"Live Fix Monitoring"**.

**Top row — KPI tiles:**

1. **Cumulative $ saved vs baseline** (big number + trend sparkline)
2. **Attribution match %** (green if 85%+, yellow 70-85%, red <70%)
3. **Kill-switch status summary** (e.g., "4/4 green" or "2 green, 1 yellow, 0 red")
4. **Days Phase 1 live** / **Days Phase 2 live**

**Middle section — per-fix attribution cards:**

Four cards, one per Fix 1/3/4/5. Each shows:
- Cumulative $ saved
- $/day rate
- Event count (e.g., "287 SKIPs fired, 3 phantom triggers prevented")
- Mini sparkline of daily contribution

**Maker pilot section (shown only when Phase 2 active):**

- Fill rate (current vs 65% threshold, with color indicator)
- Fallback rate
- Price improvement distribution (histogram, optional)
- Paired P&L delta (current + p-value)
- Statistical power indicator: "48/60 observations needed for significance"

**Kill-switch status panel:**

Table or card grid of 4-6 switches, each showing: name, current value, threshold, distance to trigger, days in warning state. Color coded green/yellow/red.

**Recent events feed:**

Scrollable list of last 20-50 events from `recent_events.json`. Chronological. Filterable by event type.

### VM-side sync

Add one step to the existing dashboard sync script (wherever `results.tsv` is pushed):

```bash
# Append to existing sync routine
scp -r /home/openclaw/auto-researchtrading/monitoring/ \
    dashboard-host:/path/to/dashboard/public/data/
```

### Refresh schedule

Matches the existing dashboard refresh schedule (presumably every 30m cron tick after trading). Dashboard reads the JSON files on page load — no websocket / live updates needed. If user wants to see fresher data, they refresh the page.

### What to build vs defer

**Minimum viable version (build first):**
- `monitoring/phase1_attribution.json` + tab card that shows per-fix $ saved
- `monitoring/kill_switch_status.json` + traffic-light panel
- `monitoring/baseline.json` snapshot

**Add when Phase 2 launches:**
- `monitoring/phase2_maker_pilot.json` + pilot section
- Paired P&L delta chart

**Nice-to-have, defer:**
- `monitoring/recent_events.json` + event feed
- Mini sparklines on cards (static rate display first)

### Validation

Before the tab is "done":

- [ ] All JSON files exist on the dashboard host
- [ ] Tab renders without errors when any file is empty or missing
- [ ] Kill-switch traffic lights correctly reflect the underlying thresholds
- [ ] Attribution match % matches a manual calculation (spot-check the math)
- [ ] Refresh on page reload pulls latest data

### Why this matters

Right now, all the per-fix attribution data lives in JSONL logs on the VM. Checking status requires SSH + a grep or Python query. A dashboard tab turns passive collection into active monitoring — you see the state every time you glance at the dashboard, not when you remember to query. This is especially important for kill-switches (maker fill rate, fallback penalty) where detecting a drift early matters.

---

## Deploy timeline + remaining steps

**✅ Phase 1 — DEPLOYED 2026-04-14 ~20:43 UTC**
- Fix 1, Fix 2 (DD threshold), Fix 3, Fix 4, Fix 5 all live on `live/trader.py` instance
- peak_equity reset to $10,000; equity_curve clamped; pre-existing kill flag deleted
- First post-deploy tick (20:44) traded cleanly, JSONL emitters writing
- See "Phase 1 deploy notes" at top of doc

**🟡 Phase 2 — BUILT, awaiting Phase 1 stability before deploy**
- Branch: `fixes/phase2-maker-pilot` (off `fixes/phase1-infrastructure`)
- 122 unit tests + 22 Phase 2 smoke checks passing
- DISABLED BY DEFAULT (`maker_pilot.enabled_symbols: []`)
- Recommended deploy timing: 2026-04-17 or later (3+ days post Phase 1)
- Activation steps when ready:
  1. `scp` the changed files to VM (similar to Phase 1 deploy pattern)
  2. Edit `risk/config.yaml`: `maker_pilot.enabled_symbols: [BTC]`
  3. Watch first BTC trade — should see paired entry in `live/logs/maker_pilot_<date>.jsonl`
  4. Run `uv run python -m monitoring.aggregate` to refresh `phase2_maker_pilot.json`

**📋 Fix 7 — Dashboard tab (handed off to UI team)**
- See `DASHBOARD_HANDOFF.md` for the standalone build spec
- All data files are now produced by the deployed aggregator
- VM-to-dashboard sync is the only outstanding infrastructure piece

**Phase 3 — Optional follow-ons (not on critical path):**
- Maker extension to ETH — only after BTC pilot proves out (2-3 weeks of paired data)
- Maker extension to SOL — last (worst slippage; harder to validate)

---

## Open items to add later

(Reserved for user to append additional planned fixes not yet discussed.)

-
