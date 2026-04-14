# Session Handoff - 2026-04-14

## What We Did

### Diagnosed live trading underperformance
- Live $10K instance (started Apr 11 19:08 UTC) was at -$428 net after 3 days while HL paper made +$13.6K on same days
- Built progressively more accurate analyses across ~30 iterations:
  - Per-symbol fee verification (BTC 5.08 / ETH 9.65 / SOL 6.59 bps effective)
  - Fee structure decomposed into CB commission (3.0 bps taker) + $0.15/contract fixed regulatory passthrough
  - Verified directly from CB account screen: VIP 4 derivatives 2.5 maker / 3.0 taker, $0.15 applies BOTH sides
  - Maker savings is **0.5 bps per trade**, not the 4 bps the prior shadow analysis assumed
- HL→CB price drift measured: −10 bps constant basis, 3-6 bps stdev. Self-cancels over round-trips.
- Cascade root-cause traced to circuit breaker code (`live/trader.py:477` + `risk/circuit_breaker.py:80-83`):
  `equity = cash + unrealized_sum` → ratchets HWM on transient MTM spikes during settlement → phantom peaks → spurious cascade triggers
- ~$840 of -$428 loss attributable to operational bugs (cascade events $608 + SKIP fee leak $231); strategy itself was ~breakeven

### Built Phase 1 fixes (5 commits, deployed live)
Branch: `fixes/phase1-infrastructure`
- **Fix 1** — SKIP tolerance check ($200 in `place_market_order`, matches paper sim) — `exchanges/coinbase_client.py`
- **Fix 5** — HWM ratchets on `realized_equity` only; DD ratio still uses MTM — `risk/circuit_breaker.py`, `risk/manager.py`, `live/trader.py`
- **Fix 3** — Halt allows close/reduce signals, blocks open/scale-up/flip — `risk/manager.py`, `live/trader.py`
- **Fix 4** — Auto-reset cooldown (2hr) replaces manual kill-flag deletion — `risk/circuit_breaker.py`, `risk/config.py`
- **Fix 2** — DD threshold widened 24h: 15% → 10% (aligned with HWM kill switch) — `risk/config.yaml`
- Per-fix attribution logging (JSONL emitters) — `monitoring/event_log.py`
- Dashboard JSON aggregator — `monitoring/aggregate.py`
- Smoke test (27 end-to-end checks) — `scripts/smoke_test_phase1.py`
- 66 unit tests added

### Deployed Phase 1 to VM (2026-04-14 ~20:43 UTC)
- Backed up all replaced files to `/home/openclaw/auto-researchtrading/.backup-pre-phase1/`
- Reset `peak_equity` to $10,000 in live, paper-cb-early, paper-175x state files
- Clamped 69 phantom equity_curve points (above $10K) on live state
- Deleted pre-existing kill flag manually
- First post-deploy tick (20:44): trader started clean, HWM dual-track logging confirmed working
- Strategy fired 3 SELL orders (BTC/ETH/SOL) — first live trades since Apr 14 09:44 cascade

### Built Phase 2 — BTC paired maker/taker pilot (2 commits, NOT deployed)
Branch: `fixes/phase2-maker-pilot` (off Phase 1)
- Pure decision logic in `exchanges/maker_logic.py` (vol-aware timeout, DD-disable, fallback decisions, splitting, state machine)
- SDK wrappers in `coinbase_client.py` (book fetch, post-only limit, status poll, cancel, orchestration)
- BTC split branching in `live/trader.py`
- `MakerPilotConfig` in `risk/config.{py,yaml}` — DISABLED BY DEFAULT (`enabled_symbols: []`)
- Paired-trade emitter + Phase 2 aggregator
- Phase 2 smoke test (22 checks)
- 56 new unit tests (122 total)

### Documentation
- `PLANNED_FIXES.md` — comprehensive fix catalog with status snapshot, deploy log, evidence
- `DASHBOARD_HANDOFF.md` — standalone build doc for whoever creates the dashboard tab
- All 4 monitoring JSON schemas + sample data + tab layout + acceptance criteria

## Current State

- **Phase 1 LIVE** on VM. Trader resumed trading at 20:44 UTC. HWM correctly reset to $10K via realized-equity ratcheting. Per-fix JSONL emitters writing to `live/logs/`.
- **Phase 2 BUILT** locally on `fixes/phase2-maker-pilot` branch, all tests pass, NOT pushed to VM yet. Disabled-by-default config means deploying it changes nothing until `enabled_symbols: [BTC]` is set.
- **Backup** of pre-Phase-1 production files at `/home/openclaw/auto-researchtrading/.backup-pre-phase1/` for rollback.
- **Two open branches:** `fixes/phase1-infrastructure` (deployed, 6 commits ahead of `autotrader/30m-exp1`) and `fixes/phase2-maker-pilot` (built, 2 additional commits).

## Pending / Not Yet Tested

- [ ] Phase 1 has NOT yet had a real cascade trigger to test Fix 5 in production. Need to wait for next volatile period to confirm phantom peaks don't trigger.
- [ ] Phase 2 maker pilot — `place_limit_order_with_fallback` orchestration tested via mocked SDK only. First real CB SDK call happens when activated.
- [ ] No `baseline_pre_phase1.json` snapshot file exists yet on VM — should be generated before extended attribution analysis (instructions in `PLANNED_FIXES.md` "Phase 1 logging requirements").
- [ ] `monitoring/aggregate.py` is not on a cron schedule yet on VM — needs to be added per `DASHBOARD_HANDOFF.md` instructions.
- [ ] Dashboard tab not yet built — handoff doc complete, awaiting UI team.

## Next Steps

- [ ] **Tomorrow:** monitor Phase 1 health. Check `live/logs/hwm_track_*.jsonl` for any `would_old_trigger=true AND did_new_trigger=false` events (phantom triggers prevented = direct attribution to Fix 5).
- [ ] **Day 3+ post-deploy (~Apr 17):** if Phase 1 stable, deploy Phase 2 (`scp` files + edit config) per the activation steps in `PLANNED_FIXES.md`.
- [ ] **Hand `DASHBOARD_HANDOFF.md` to UI team** when ready. They need to: (a) set up cron for `python -m monitoring.aggregate`, (b) sync the 4 JSONs to the dashboard host, (c) build the tab.
- [ ] **Generate `baseline_pre_phase1.json`** from the pre-Phase-1 trade log + state for clean attribution math.
- [ ] After 1-2 weeks of Phase 2 maker data on BTC, decide whether to extend to ETH (biggest absolute fee burden).

## Quick Context

The maker pilot's $50-80/4-day projection at $10K is small but the infrastructure is the deliverable — paired BTC observations under matched conditions give us ground truth on fill rates, price improvement, and tail behavior that can't be measured from sim alone. Real value is when capital scales or when applied to ETH (where the fee burden is highest). Don't budget the pilot as P&L; budget it as R&D.
