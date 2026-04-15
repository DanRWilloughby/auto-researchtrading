# Session Handoff — 2026-04-14 / 15 (overnight)

## What We Did

### Dashboard pipeline (full VM-side rebuild)
- Retired the laptop `com.overnight-lab.trading-sync` launchd agent (backed up to `~/Library/LaunchAgents/.retired/`)
- Built VM-side `~/bin/sync-dashboard.sh` that replaces both `sync-monitoring.sh` (5 JSONs only) and the laptop `cron-sync.sh` + `sync-state.sh` chain. Handles strategy data + live pseudo-strategies + paper state + monitoring JSONs + `manifest.json` regen + git push in one pass.
- Connected Vercel `dashboard` project → `DanRWilloughby/overnight-lab` via API (was NOT git-connected before; laptop CLI had been doing the deploys)
  - Root directory: `projects/2026-03-22_autoresearch-trading-dashboard/dashboard`
  - Ignored build step: `git diff --quiet HEAD^ HEAD -- :/projects/2026-03-22_autoresearch-trading-dashboard/dashboard` (the `:/` anchor is critical — Vercel runs the command from rootDirectory, not repo root)
  - Commit author on VM set to `DanRWilloughby` via noreply email `8854211+DanRWilloughby@users.noreply.github.com` so Vercel doesn't block deploys
- Two crons on VM (openclaw): aggregator `*/15`, unified sync `2,17,32,47`

### Monitoring invariant fix
- `monitoring/aggregate.py`: corrected the `hwm_drift_detection` kill-switch invariant. Old check assumed unrealized ≥ 0 and fired RED (11/12 ticks) when strategy was underwater from start. Now checks the real Fix 5 property: "hwm_new only moves on realized gains." Dashboard switch green. Commit `2995e91`.

### Execution cost analysis (30m-concentrated HL Paper)
- 1,297 HL paper trades × realistic CB execution costs → **HL paper +65.27% → realistic taker +44.67%** over Mar 27–Apr 14
- Saved to `strategies/30m-concentrated/REALISTIC_EXECUTION_PROJECTION.md`
- Key finding: ETH is the high-cost coin (9.58 bps taker vs BTC 5.06, SOL 6.57). Major methodology correction mid-session — first pass double-counted cross-venue basis as slippage via `abs()`; corrected adverse-only slippage is ~0 bps.

### Maker shadow rebuild (pure → hybrid)
- Diagnosed: pure-maker shadows dropped 30% of trades (80% of OPEN_LONG). Structural — pure maker misses fills in trending markets.
- Rebuilt `live/maker_shadow.py`: on maker miss, fall back to taker at live `actual_fill_price` with taker fee. Trades tagged `_MAKER_FILL` / `_TAKER_FALLBACK`.
- Backed up pre-hybrid state + code to `~/auto-researchtrading/.backup-pre-hybrid-shadow/` on VM, reset state for clean baseline.
- Built `scripts/measure_hybrid.py` diagnostic. Commit `cd70e10`.

### Circuit breaker halt diagnosis + clean reset (00:14–00:27 UTC)
- **Live trader halted at 00:14:03 UTC**. Cron log said "manual kill flag detected" but the *real* first trigger (in `halt_events_2026-04-15.jsonl`) was: `"24h drawdown 5.03% from peak $10,000.00 exceeds 5.0% limit"`.
- **Root cause: shared `state/kill.flag` between instances.** Paper-175x instance (correctly configured with 5% DD threshold for 1.75× leverage) halted on its own threshold and touched the shared flag. Live instance (10% threshold per Phase 1 fix) inherited the halt 1 second later.
- **Fixes applied:**
  - `risk/config.yaml`: `kill_flag_file: state/kill-live.flag`
  - `risk/config-175x.yaml`: `kill_flag_file: state/kill-175x.flag`
  - Live state `peak_equity`: $10,000 → **$9,500** (per Dan's request — reset DD clock to current baseline)
  - Live state `equity_curve`: trimmed to 1 fresh point at reset ts
  - `trade_log` preserved (202 entries)
  - All kill flags cleared
- **Phase 2 pilot NOT activated tonight** — explicit decision to wait for real hybrid shadow data (24h) before deploying. Rationale: no live smoke test post-Phase-1, stacking 5 unvalidated changes is high risk, strategy was flat overnight anyway. Commit `00516f0`.

## Current State

- **Live trader:** clean reset. Next cron 00:44 UTC. HWM=$9,500, 24h window=1 point, kill flag cleared, CB positions flat (Dan manually flattened before reset).
- **Dashboard:** fully live at `https://dashboard-green-nu-53.vercel.app/`, 16 strategies populated, kill switches green.
- **Maker shadows:** hybrid mode live since 23:28 UTC. 114 historical orders queued; first resolution at 23:46 showed 100% fill rate (backfill artifact). Real fill rates emerge over next 24h.
- **Branch:** `fixes/phase2-maker-pilot`. Commits this session: `2995e91`, `cd70e10`, `0ceeda4`, `00516f0`. Not pushed.

## Pending / Not Yet Tested

- [ ] Confirm 00:44 live cron shows `high_water=$9,500.00` and NO `TRADING HALTED` line
- [ ] Wait 24h, rerun `scripts/measure_hybrid.py --remote` for real hybrid performance data per level
- [ ] Decide Phase 2 production config (which maker aggressiveness) based on evidence, then deploy in daylight with reduced pilot size ($2K notional initially)
- [ ] Consider pushing `fixes/phase2-maker-pilot` branch (Dan's call)
- [ ] Clean up legacy scripts in overnight-lab repo: `cron-sync.sh`, `install-cron.sh`, `sync-state.sh` (inert without launchd agent)

## Next Steps (priority order)

1. **Tomorrow morning:** check 00:44 + subsequent crons in `live/logs/30m-concentrated-live-cron.log` — confirm clean HWM restore + no halts
2. **After ~24h of hybrid data:** rerun `python3 scripts/measure_hybrid.py --remote` — pick best aggressiveness level based on fill rate + vs-taker delta
3. **Phase 2 activation:** deploy chosen level to BTC paired maker/taker pilot on live, **daylight hours**, **reduced pilot notional**, **with live observer**
4. **ETH-drop sensitivity test** on scenarios C and E-75 (ETH carries ~16 bps round-trip; dropping it may preserve alpha at half cost)
5. **Stop citing +65.3%** unqualified — use +44.67% as the realistic 30m-concentrated baseline in external reporting

## Quick Context

Dense session: (1) wired dashboard VM-side after laptop retirement, (2) corrected false-alarm kill switch, (3) built defensible realistic-execution projection, (4) rebuilt maker shadows to hybrid with taker fallback, (5) diagnosed + fixed a cross-instance kill.flag cascade that halted live money overnight, (6) reset HWM to $9,500 for clean start. Dan caught a major methodology error mid-session (slippage `abs()` double-counting basis). Ended with explicit decision to NOT rush Phase 2 activation overnight — wait for evidence.

## Files Changed (this session)

- `monitoring/aggregate.py` — kill-switch invariant fix
- `live/maker_shadow.py` — hybrid fallback (newly tracked in git)
- `strategies/30m-concentrated/REALISTIC_EXECUTION_PROJECTION.md` — execution cost analysis
- `scripts/measure_hybrid.py` — hybrid performance diagnostic
- `risk/config.yaml` — `kill_flag_file: state/kill-live.flag`
- `risk/config-175x.yaml` — `kill_flag_file: state/kill-175x.flag` (newly tracked)

## VM-side changes (not all in local repo)

- `~/bin/sync-dashboard.sh` — unified sync
- `~/bin/sync-monitoring.sh` — superseded, not in cron
- `~/dashboard-repo/` — fresh clone of overnight-lab used only for sync commits
- `~/.ssh/dashboard_sync_ed25519` — deploy key registered on overnight-lab with write access
- `~/Library/LaunchAgents/.retired/com.overnight-lab.trading-sync.plist.*` — retired laptop agent backup
- `live/state/30m-concentrated_live_state.json.bak-reset-*` — pre-HWM-reset backup
- `risk/config.yaml.bak-pre-split-*`, `risk/config-175x.yaml.bak-pre-split-*` — pre-split backups
- Crontab: aggregator + sync-dashboard; sync-monitoring removed

## Surprising findings worth remembering

1. **Vercel's Ignored Build Step runs from rootDirectory, not repo root.** Path args need `:/` pathspec anchor.
2. **Vercel blocks deploys from committers it can't map to a GitHub user.** Use the `<id>+<login>@users.noreply.github.com` format.
3. **HL paper's 5 bps flat fee is wildly wrong for ETH on CB (9.58 bps real).** Per-contract regulatory passthrough is brutal on small-notional contracts.
4. **"Slippage" from reconciled-fill logs is mostly cross-venue basis, not execution cost.** `abs()` double-counts. True adverse slippage ~0 bps.
5. **Pure maker drops OPEN_LONG and CLOSE signals in trending markets.** Structural, not noise. Hybrid with taker fallback is the only way to model Phase 2 correctly.
6. **Shared kill.flag between trading instances is a cross-contamination bug.** Per-instance paths are required when configs have different DD thresholds.
