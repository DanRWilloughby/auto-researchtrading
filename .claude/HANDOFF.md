# Session Handoff — 2026-04-14 (evening)

## What We Did

### Dashboard pipeline (full VM-side rebuild)
- Retired the laptop `com.overnight-lab.trading-sync` launchd agent (backed up to `~/Library/LaunchAgents/.retired/`)
- Built VM-side `~/bin/sync-dashboard.sh` that replaces both `sync-monitoring.sh` (5 JSONs only) and the laptop `cron-sync.sh` + `sync-state.sh` chain. Handles strategy data + live pseudo-strategies + paper state + monitoring JSONs + `manifest.json` regen + git push in one pass.
- Connected Vercel `dashboard` project → `DanRWilloughby/overnight-lab` via API (was NOT git-connected before despite appearances; laptop CLI had been doing the deploys)
  - Root directory: `projects/2026-03-22_autoresearch-trading-dashboard/dashboard`
  - Ignored build step: `git diff --quiet HEAD^ HEAD -- :/projects/2026-03-22_autoresearch-trading-dashboard/dashboard` (the `:/` anchor was needed — Vercel runs the command from rootDirectory, not repo root; plain path failed silently)
  - Commit author identity on VM set to `DanRWilloughby` via noreply email `8854211+DanRWilloughby@users.noreply.github.com` so Vercel doesn't block deploys
- Two crons on VM (openclaw):
  - `*/15 * * * *` — aggregator (`python -m monitoring.aggregate`)
  - `2,17,32,47 * * * *` — unified sync (`~/bin/sync-dashboard.sh`)
- All 16 strategies now appear correctly on dashboard with proper paper/experiment flags

### Monitoring invariant fix
- `monitoring/aggregate.py`: corrected the `hwm_drift_detection` kill-switch invariant. The old check `hwm_new_realized > hwm_old_mtm` assumed unrealized P&L ≥ 0 and fired RED (11/12 ticks) when strategy was underwater from start. Now checks the actual Fix 5 property: "hwm_new only moves on realized gains, never on unrealized noise." Dashboard switch flipped to green.
- Committed as `2995e91`.

### Execution cost analysis (30m-concentrated HL Paper)
- Pulled 1,297 HL paper trades + 473 CB live-paper trades + 128 reconciliation events + 198 maker shadow book snapshots
- Ran compounding re-simulation on HL paper with realistic CB execution costs
- Headline result: **HL paper +65.27% → CB-taker realistic +44.67%** over same Mar 27 – Apr 14 period
- Saved to `strategies/30m-concentrated/REALISTIC_EXECUTION_PROJECTION.md`
- Key finding: ETH is the high-cost coin (9.58 bps taker vs BTC 5.06, SOL 6.57). Big methodology correction mid-session: first pass double-counted cross-venue basis as slippage; corrected adverse-only slippage is ~0 bps.

### Maker shadow rebuild (pure → hybrid)
- Diagnosed maker shadow underperformance: shadows took only 84-89 trades vs taker's 120 (30% miss rate, concentrated in OPEN_LONG: 3-5 vs 23, and CLOSE: 11-14 vs 33). Structural failure — pure maker drops signals in trending markets.
- Rebuilt `live/maker_shadow.py`: on maker miss, fall back to taker at live `actual_fill_price` with taker fee. Trades tagged `_MAKER_FILL` or `_TAKER_FALLBACK`.
- Backed up pre-hybrid state + code to `~/auto-researchtrading/.backup-pre-hybrid-shadow/` on VM, reset shadow state files for clean baseline.
- Committed as `cd70e10`.
- Built `scripts/measure_hybrid.py` diagnostic — pulls VM state, reports per-level fill rate / fee bps / return / vs-taker. Run with `--remote` flag.

## Current State

- **Live trader:** healthy, cron firing on 14,44 schedule. 3 shorts held (BTC/ETH/SOL opened at 22:14), last 3 crons returned no signals (expected — strategy doesn't trade every bar). MTM ~$9,543, cash $9,545, daily P&L flat at -$203.
- **Dashboard:** fully live at `https://dashboard-green-nu-53.vercel.app/`, reading from VM sync every 15 min, all 16 strategies populated. Kill switches both green.
- **Maker shadows:** hybrid mode live since 23:28 UTC. First cron resolution at 23:46 showed artificially high 100% fill rate (backfill artifact from 2-day lookback candles). Real fill rates will emerge over next 24h.
- **Branch:** `fixes/phase2-maker-pilot`, 2 new commits this session (`2995e91`, `cd70e10`) — not pushed.

## Pending / Not Yet Tested

- [ ] Wait 24h, rerun `scripts/measure_hybrid.py --remote` — by then shadows will have fresh (non-backfill) fill data and the level comparison becomes meaningful
- [ ] Confirm unified `sync-dashboard.sh` has been running on cron at :02/:17/:32/:47 without incident (first run done manually at 22:52, next scheduled runs happen automatically)
- [ ] Consider pushing `fixes/phase2-maker-pilot` branch (or not — Dan's call)
- [ ] Clean up legacy scripts in overnight-lab repo when convenient: `cron-sync.sh`, `install-cron.sh`, `sync-state.sh` in `projects/.../dashboard/scripts/` (dead but harmless)

## Next Steps

- [ ] In ~24h, rerun measure_hybrid.py, review each maker aggressiveness level's fill rate + vs-taker delta, pick production config for Phase 2 pilot
- [ ] Run ETH-drop sensitivity test on scenarios C and E-75 (ETH carries ~16 bps round-trip; worth testing whether dropping it preserves alpha)
- [ ] The +65.3% HL paper headline should never appear unqualified again — use +44.67% (scenario C) as the realistic baseline in any external reporting

## Quick Context

Two-hour session to (1) wire the dashboard properly after the laptop cron retirement, (2) correct a false-alarm kill switch, and (3) build a defensible realistic-execution projection for the 30m-concentrated strategy. Major pivot mid-session when Dan caught that initial slippage numbers were bogus (cross-venue basis double-counted via abs()). Finished by rebuilding the maker shadow sims to use hybrid fallback so they actually model production Phase 2 behavior. All changes committed, not pushed.

## Files Changed (this session)

- `monitoring/aggregate.py` — kill-switch invariant fix
- `live/maker_shadow.py` — hybrid fallback (newly tracked in git)
- `strategies/30m-concentrated/REALISTIC_EXECUTION_PROJECTION.md` — execution cost analysis
- `scripts/measure_hybrid.py` — hybrid performance diagnostic

## VM-side changes (not in local repo)

- `~/bin/sync-dashboard.sh` — unified sync (source of truth is this VM file)
- `~/bin/sync-monitoring.sh` — superseded, still on disk, not in cron
- `~/dashboard-repo/` — fresh clone of overnight-lab used only for sync commits
- `~/.ssh/dashboard_sync_ed25519` — deploy key registered on github.com/DanRWilloughby/overnight-lab with write access
- `~/Library/LaunchAgents/.retired/com.overnight-lab.trading-sync.plist.20260414` — retired laptop agent backup
- Crontab: added aggregator + sync-dashboard; removed sync-monitoring

## Surprising findings worth remembering

1. HL paper's flat 5 bps fee assumption is wildly wrong for ETH on CB (real = 9.58 bps). ETH per-contract regulatory passthrough is disproportionate on small-notional contracts.
2. Vercel's Ignored Build Step runs from the project root directory, not repo root. Path args need `:/` pathspec anchor to hit the right location.
3. The "slippage" on reconciled-fill logs is mostly CB-vs-internal-feed basis, which cancels in round-trips. Using abs() double-counts. True adverse slippage is ~0 bps.
4. Pure maker without taker fallback systematically drops signals in trending markets — specifically OPEN_LONG and CLOSE actions. This is structural, not noise.
5. Vercel CAN'T associate commits to users if the email doesn't match a GitHub user. The no-reply format `<numeric-id>+<username>@users.noreply.github.com` works.
