# Maker/Taker Hybrid Research — Session Memo

**Date:** 2026-04-15
**Strategy:** 30m-concentrated (BTC, ETH, SOL perps on Coinbase)
**Branch:** fixes/phase2-maker-pilot
**Status:** Shadow instrumentation complete; 2-4 weeks of data collection before any live change.

---

## TL;DR

Spent the day asking whether a maker-then-taker hybrid execution could reduce fee drag on the 30m-concentrated strategy. Multiple iterations on methodology, finding and fixing bugs in our production shadow along the way. Landed at a **specific, testable hypothesis**:

> **On close signals, passive maker with a 4-minute fallback-to-taker window is net-positive P&L across BTC, ETH, and SOL. On open signals, the same hybrid is marginal (ETH/BTC) to strongly negative (SOL). The right policy is hybrid maker on CLOSES only; keep OPENS as pure taker.**

The shadow is now instrumented to validate this split on live data. Do NOT deploy any execution change until 2-4 weeks of the new shadow confirm the backtest finding.

---

## What we did (work log)

### 1. Live health check (after overnight reset)

- Live trader had been reset at 00:27 UTC to HWM=$9,500 after cascade-halt incident the prior night
- 12.78h of clean trading: 42 trades, equity $9,500 → $9,253.87 (–2.59%)
- Realized P&L –$44.65, fees $121.38 — **73% of the loss was fees, 27% was price action**
- ETH alone accounted for –$102 of the drawdown

### 2. Fee model verified directly from CB order receipts

Three receipts at 13:14 UTC reconciled exactly with our model:

| Coin | Contracts | Price | CB fee | Reg fee | Total |
|---|---|---|---|---|---|
| BTC | 5 | $74,350 | $1.12 | $0.75 | $1.87 |
| ETH | 16 | $2,332 | $1.12 | $2.40 | $3.52 |
| SOL | 9 | $83.74 | $1.13 | $1.35 | $2.48 |

**Confirmed formula: `3.0 bps × notional + $0.15 × contracts` per side.** Contract multipliers: BTC 0.01, ETH 0.1, SOL 5. Round-trip costs: BTC 10.1 bps, ETH 18.9 bps, SOL 13.2 bps.

### 3. Side-channel bugs identified and fixed

| Bug | Cause | Fix | Deploy |
|---|---|---|---|
| 28 halt events logged on live since reset despite no actual halts | Shared `halt_events_{date}.jsonl` between live and paper-175x instances | `monitoring/event_log.py` now namespaces files by instance; live/trader.py passes `args.instance` | ✅ deployed |
| `hwm_new_realized: 10000.0` on 27/28 ticks instead of $9,505 post-reset | Same root cause — shared `hwm_track_{date}.jsonl` mixing instance streams | Same fix (instance namespacing) | ✅ deployed |
| Shadow's `maker_fee` omitted 2.5 bps base rate, inflating reported savings ~6× | Code bug: `maker_fee = contracts * FEE_PER_CONTRACT` | Added bps component: `maker_fee = notional * MAKER_BPS + contracts * FEE_PER_CONTRACT` | ✅ deployed |
| Shadow counted pre-placement price action as fills (using 30m OHLC) | Filter `candle_ts_sec < placed_ts - 60` checked bar-OPEN, but the bar's range includes time before placement | Switched to 1-min candles + strict filter: `candle_ts_sec >= placed_ts` and `<= placed_ts + max_wait` | ✅ deployed |
| Shadow's `placed_ts` was shadow cron time (XX:16), not live trade time (XX:14) | Systematically 2 min late, degrading fill rate estimate | `placed_ts = trade_ts / 1000` (uses actual live trade time) | ✅ deployed |

### 4. Filter battery (13 experiments on 944 days of CB data)

Tested vol-based, time-of-day, size-boost, ETH-drop, and funding-based filters. **All underperformed pure-taker baseline.** Logged to `strategies/30m-concentrated/results.tsv` with labels `filter-BASE-control` through `filter-E12-1.25x-1323-UTC`. Key takeaways:

- Strategy edge is regime-invariant over 944 days; external filters only remove alpha
- The 19-day HL paper patterns (hour-of-day, flat-day underperformance on ETH) did **not** replicate at 944 days
- `BASE_POSITION_PCT=1.20` × 3 coins produces absurd compounded returns in backtest — the **relative** comparison is valid but absolute numbers are inflated ~240×

Logging infrastructure:
- Created `engine/run_experiment.py` wrapper requiring `label` + `notes` and enforcing `log_result()` for all direct `run_backtest()` calls
- Updated `.claude/CLAUDE.md` with rule and example

### 5. Deep-limit experiments (noise-capture strategy)

Dan proposed: place a deep limit 50+ bps better than current price, catch counter-move reversals. Tested thresholds 10-150 bps × timeouts 5-30 min on HL paper.

**Finding:** at shallow thresholds, adverse selection dominates (fills correlate with wrong-direction continuation). At very deep thresholds (1.5%+), fills are rare but net-positive — but volume is too low to matter.

**Verdict on deep-limit:** Not practical in isolation. Signal is real but noise-dominated at useful thresholds.

### 6. Entry-timing optimization (cron move)

Measured what would happen if we moved the cron to different minute-of-bar-close offsets:

- Current XX:14 cron is **near-optimal**; XX:16 would gain ~$0.34/trade on HL paper
- Confirmed both live and HL-paper traders read the **last-closed 30m bar** and compute signal from its close — the 14-min delay is purely cron scheduling, not signal freshness
- Strategy alpha IS concentrated in first ~14 min after bar close, which the cron already captures

**No cron move recommended.** The 2-min improvement doesn't justify operational churn.

### 7. The maker-taker hybrid analyses (iterative, converged on answer)

Walked through several methodology iterations, culminating in the definitive test:

| Iteration | Finding | Why superseded |
|---|---|---|
| v1 — production shadow stats | "81% fill rate, $216 savings" | Two bugs (fee + filter) inflated both numbers ~6× |
| v2 — shadow event-based sim | "Hybrid slightly positive at 2-3 min" | Used stale XX:16 book data for maker_price |
| v3 — corrected maker_price at XX:14 | "Hybrid net-negative on all coins" | Used half-spread instead of full-spread for passive-level |
| v4 — full-spread + round-trip P&L | "ETH losses $6.56/trade at 3m timeout" | 19-day HL paper sample too small; regime-specific |
| **v5 — 9-month CB backtest, per-action split** | **"Hybrid is +EV on closes, –EV on opens"** | **Current working answer** |

### 8. The canonical finding (9-month backtest, 9,549 trades)

**Passive maker at XX:14, 4-min timeout, fallback taker, both legs:**

Per coin × per action:

| Coin | Action | Fill% | BE% | Margin | $/trade (backtest-scale) |
|---|---|---|---|---|---|
| ETH | close | 79.0% | 63.1% | **+15.9 pp** | +$397 |
| BTC | close | 82.9% | 72.1% | **+10.9 pp** | +$122 |
| SOL | close | 77.5% | 74.9% | +2.6 pp | +$61 |
| BTC | open | 80.2% | 78.1% | +2.1 pp | +$31 |
| ETH | open | 74.7% | 75.3% | –0.5 pp | –$19 |
| **SOL** | **open** | **72.6%** | **82.0%** | **–9.4 pp** | **–$292** |

Policy total (9 months):
- Hybrid on both opens + closes: **+$379,744**
- **Hybrid on CLOSES only: +$805,148** ← best
- Pure taker: $0 baseline

### 9. Why closes beat opens (the intuition)

- **Opens fire on fresh signal momentum.** Price is actively drifting in the signal direction. A passive maker order gets filled only if price reverses past our level — meaning the fill is adverse-selected toward "signal was wrong." When signal is right, we miss the fill and eat the drift cost on fallback.
- **Closes fire on momentum exhaustion** (stop loss, take profit, signal reversal). Price is oscillating around the exit level rather than trending. Maker fills are NOT adverse-selected; they represent normal two-sided market activity. Fallbacks don't drift materially because drift has stalled.

### 10. Shadow reconfigured to validate the hypothesis

Shadow updated so it captures exactly what we need:

| Parameter | Value | Why |
|---|---|---|
| Cron alignment | :14 and :44 UTC (aligned with live cron) | Same timing as production hybrid would run |
| Startup sleep | 90 sec | Lets live trader complete trade+log before shadow reads |
| Placement time | `trade_ts` (actual live trade moment) | Removes 2-min stale-book bias |
| Timeout | 240 sec (4 min) | Matches proposed hybrid spec |
| Candle source | 1-min CB with strict `[placed_ts, placed_ts+240]` filter | No pre-placement leakage |
| Fee model | 2.5 bps × notional + $0.15 × contracts | Correct maker fee |
| Log namespacing | Per-instance files + `instance` field in records | No cross-pollution between live and paper-175x |
| **Per-action split** | **`action_type` ∈ {open, close, unknown}** via `target_pos` | **Validates the closes-only hypothesis** |

---

## Files changed today (all deployed to VM)

| File | Change | Backup |
|---|---|---|
| `monitoring/event_log.py` | Instance namespacing + per-record `instance` field | `.bak-pre-instance-fix` |
| `live/trader.py` | Passes `args.instance` to `set_log_dir()` | `.bak-pre-instance-fix` |
| `live/maker_shadow.py` | Fee bug fix + candle filter fix + 4-min timeout + true `trade_ts` + open/close split | `.bak-pre-*` (multiple layers) |
| `paper/run-maker-shadow.sh` | 90-sec sleep | `.bak-pre-align-2026-04-15` |
| Crontab (openclaw) | Shadow moved to `14,44 * * * *` | `/tmp/crontab.bak-2026-04-15` |
| `strategies/30m-concentrated/results.tsv` | Appended 14 filter experiment rows | — |
| `engine/run_experiment.py` | New logging-wrapper | — |
| `.claude/CLAUDE.md` | Added rule requiring wrapper for direct `run_backtest()` calls | — |
| `tests/test_monitoring_event_log.py` | Added 5 instance-namespacing tests (all 15 pass) | — |

### Commits landed

- `d98df0f` — feat(engine): enforced-logging wrapper for direct run_backtest calls
- `744e556` — fix(monitoring): namespace event_log JSONL files by instance
- (Shadow fixes + action-split NOT yet committed; currently live on VM only as rsync'd files)

---

## The hypothesis we're now tracking

**H1 (primary):** Hybrid passive-maker with 4-minute taker fallback, applied to CLOSE signals only, is net-positive P&L vs pure-taker on the 30m-concentrated strategy.

**H1a (per-coin):** ETH closes show the strongest positive margin (+15.9 pp above break-even in backtest); BTC closes solidly positive (+10.9 pp); SOL closes marginally positive (+2.6 pp).

**H1b (opens exclusion):** Hybrid on open signals does NOT help — it's marginal on BTC/ETH and strongly negative on SOL. The closes-only split is the win.

**H2 (regime-dependent):** The margin above break-even varies by market regime (volatility, trend). Extended calm periods should push all three coins further above BE; trending or high-vol regimes may compress margins.

**H3 (queue-position risk):** The backtest assumes 100% fill when price touches maker level. Real fill rates may be 10-20 pp lower due to queue position at bid/ask. BTC opens (+2.1 pp margin) would flip negative under a 10 pp haircut; ETH closes (+15.9 pp) would remain positive.

---

## What the shadow is now recording (to verify the hypothesis)

For every live trade at XX:14/XX:44:

**At placement:**
- `shadow_comparison` entries (one per level: passive/quarter/mid/aggressive) with:
  - `action_type` ∈ {open, close, unknown}
  - `target_pos` (raw classification input)
  - `placed_ts` = actual live trade time
  - `maker_limit_price` for each level
  - `book_bid`, `book_ask`, `book_spread_bps`
- `shadow_multi` summary log with same action metadata

**At resolution (30 min later on next cron):**
- Whether the 4-min maker window fills (per level)
- Hypothetical maker fee (correct 2.5 bps model)
- Per-action counters update: `by_action.{open,close,unknown}.{fills, misses}`

**In per-coin state file `maker_shadow_{level}_state.json`:**
```json
"stats": {
  "total_trades_shadowed": N,
  "maker_would_have_filled": F,
  "maker_would_have_missed": M,
  "by_action": {
    "open":  {"shadowed": .., "fills": .., "misses": ..},
    "close": {"shadowed": .., "fills": .., "misses": ..},
    "unknown": ...
  }
}
```

**In per-run cron log:**
- Standard Summary table (all levels, aggregate)
- **New Per-action split table** (all levels × {open, close, unknown})

## Success criteria for hypothesis validation

After 2-4 weeks of shadow data (≈ 100+ closes and 100+ opens per coin):

| Criterion | Pass | Fail |
|---|---|---|
| ETH close fill rate | ≥ 65% | < 60% |
| BTC close fill rate | ≥ 75% | < 70% |
| SOL close fill rate | ≥ 75% | < 70% |
| ETH close above BE margin | ≥ +5 pp | < 0 pp |
| BTC close above BE margin | ≥ +5 pp | < 0 pp |
| Open fill rates (any coin) | ≤ 5 pp above BE | > 10 pp above BE (then we'd re-examine whether to include opens) |

If all closes pass, we'd activate **Phase 2 closes-only hybrid** on live with an initial small-notional pilot.

If anything fails, we stop and investigate — do NOT force deployment.

---

## Shadow is correctly configured — final verification (21:45 UTC)

```
=== Cron alignment ===
14,44 * * * * /home/openclaw/auto-researchtrading/paper/run-cron-30m-concentrated-live.sh
14,44 * * * * /home/openclaw/auto-researchtrading/paper/run-maker-shadow.sh

=== Shadow script has sleep ===
sleep 90
/home/openclaw/.local/bin/uv run live/maker_shadow.py >> live/logs/maker-shadow-cron.log 2>&1

=== Key changes in maker_shadow.py (on VM) ===
max_wait = 240 (4 min)
placed_ts = trade_ts / 1000.0 (actual trade time)
action_type classification (open/close/unknown via target_pos)
by_action stats tracking

=== Shadow run at 21:45 UTC ===
...
=== Per-action split (validates the 9-mo backtest finding) ===
Level        | Action   | Shadow |  Fills | Misses |  Fill %
-----------------------------------------------------------------
=== Multi-Level Maker Shadow complete ===
```

Per-action headers are printing. Rows are currently empty because the 21:44 live cron produced 0 new trades. Starting with the next trade at XX:14/XX:44, trades will be classified by action_type and populate the by_action buckets. **The shadow will collect the data we need.**

---

## Remaining caveats (don't trust the backtest blindly)

1. **Queue position is not modeled.** The 1-min-candle "did price touch level" test assumes we always fill. Realistic queue effects could reduce fill rates by 10-20 pp.

2. **Spread is a static per-coin estimate** (BTC 1.0, ETH 4.22, SOL 2.5 bps) based on observed CB book. Real spreads vary by market state.

3. **9-month sample is one regime.** Different regimes (trending, range-bound, high/low vol) may invert the result.

4. **Backtest uses `execute_delay=0` (bar-close fills)** which has look-ahead bias for absolute returns but should be fine for relative comparison between taker and hybrid.

5. **Signal accuracy drives everything.** If the strategy's win rate drops (signal degrades), maker-on-closes economics could shift.

**The shadow exists specifically to check these caveats against real live fills.**

---

## Next session checklist

When Dan returns:

1. Check `live/logs/maker-shadow-cron.log` for per-action data accumulating
2. Check `live/state/maker_shadow_{level}_state.json` — the `by_action` dict should be populating
3. After 1 week of data: pull stats, compute real fill rates per (coin, action, level), compare to backtest predictions
4. Decide: continue accumulating, or identify a surprise early

## Other open items

- **Bitnomial fee inquiry** — still the highest-EV research task. Contact their sales for perpetual fee schedule; if flat-bps without per-contract, reshapes whole analysis.
- **ETH funding-rate predictor** — tested as standalone filter; weak signal (ρ=0.011 for ETH specifically). Not worth pursuing.
- **CME 24/7 crypto futures launch May 29, 2026** — monitor for venue diversification opportunity.
- **Commit the shadow changes** (still uncommitted, live only as rsync'd files). Two planned commits:
  - `fix(shadow): 4-min timeout + trade_ts placement + 1-min candle precision`
  - `feat(shadow): open/close split for hybrid-policy validation`

---

## Shadow data dictionary (for future analysis scripts)

### `maker_shadow_{level}_state.json` structure (post-2026-04-15)

```json
{
  "level_id": "passive",
  "last_processed_ts": 1776xxxxxxxxx,
  "pending_maker_orders": [/* shadow_comparison entries waiting for resolution */],
  "stats": {
    "total_trades_shadowed": N,
    "maker_would_have_filled": F,
    "maker_would_have_missed": M,
    "maker_pending": P,
    "total_taker_fees_paid": $$,
    "total_maker_fees_hypothetical": $$,
    "taker_fallback_count": N,
    "by_action": {
      "open":  {"shadowed": N, "fills": F, "misses": M, "pending": P},
      "close": {"shadowed": N, "fills": F, "misses": M, "pending": P},
      "unknown": {...}
    }
  },
  "sim": {
    "initial_equity": 10000,
    "cash": ...,
    "positions": {...},
    "trade_log": [...]  // includes fill_mode: "maker" | "taker_fallback"
  }
}
```

### `maker_shadow_{date}.jsonl` events

Each line is a `shadow_multi` event:
```json
{
  "type": "shadow_multi",
  "ts": 1776xxxxxxxxx.xx,
  "trade_ts": 1776xxxxxxxxx,
  "symbol": "ETH",
  "side": "BUY",
  "action_type": "close",         // NEW 2026-04-15
  "target_pos": 0.0,              // NEW 2026-04-15 — raw source for classification
  "contracts": 16,
  "actual_fill": 2332.0,
  "book_bid": 2331.5,
  "book_ask": 2332.5,
  "levels": {"passive": 2331.5, "quarter": 2331.75, "mid": 2332.0, "aggressive": 2332.0}
}
```

### Per-level pending order (inside `pending_maker_orders`)

```json
{
  "type": "shadow_comparison",
  "level_id": "passive",
  "action_type": "close",         // NEW 2026-04-15
  "ts": 1776xxxxxxxxx.xx,
  "trade_ts": 1776xxxxxxxxx,
  "symbol": "ETH",
  "side": "BUY",
  "contracts": 16,
  "notional_usd": 3731.20,
  "actual_fill_price": 2332.0,
  "maker_limit_price": 2331.5,
  "maker_fee": 0.9328,
  "book_best_bid": 2331.5,
  "book_best_ask": 2332.5,
  "book_spread_bps": 4.2,
  "spread_frac": 0.0,
  "placed_ts": 1776xxxxxxxxx.xxx, // = trade_ts / 1000 (actual live trade time)
  "maker_filled": null            // set to True/False on resolution
}
```

---

**End of session memo.**
