# Dashboard Tab Handoff — Phase 1/2 Live Trading Health

This is a self-contained spec for adding a monitoring tab to the existing AutoResearch Trading Dashboard. Hand this entire document to whoever builds it; they should not need to ask follow-up questions about scope.

## Background (you can skim)

We trade `30m-concentrated`, a perpetual-futures strategy on Coinbase. After Apr 11-14 2026 live trading exposed several infrastructure bugs (phantom HWM peaks, fee leaks from missing skip checks, force-flatten halts), we shipped a Phase 1 fix package on Apr 14. A Phase 2 maker pilot is built and pending activation.

Each fix emits attribution data to JSONL log files. An aggregator on the VM rolls those into JSON files. Your job is to read those JSON files and render a tab.

## What you are building

**Add a new tab to https://dashboard-green-nu-53.vercel.app/** — name it **"Live Fix Monitoring"** (or similar). It displays Phase 1 attribution, Phase 2 maker pilot status, kill-switch health, and a recent-events feed.

Refresh: same cadence as the existing dashboard data sync (no live websocket needed; user refreshes page).

---

## Data contract (4 JSON files)

The VM produces these 4 files. They will be synced to the dashboard's static asset path (whatever path your existing `data/config.json` lives at — see "VM sync" below).

All files contain `updated_at` ISO timestamp. Render that somewhere visible so user knows data freshness.

### 1. `phase1_attribution.json`

Per-fix counters for Phase 1 bug fixes. Cumulative since Phase 1 ship date.

```json
{
  "updated_at": "2026-04-21T14:00:00+00:00",
  "phase1_ship_date": "2026-04-14T20:43:00+00:00",
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
    "phantom_triggers_prevented": 3
  },
  "fix_3_halt_behavior": {
    "cb_trigger_events_since_ship": 1
  },
  "fix_4_cooldown": {
    "auto_clear_events": 2,
    "avg_cooldown_duration_sec": 7240.5
  },
  "sum_attributed_savings_usd": 823.50
}
```

Some fields may be missing or zero on early days. Treat all numeric fields as optional with default 0; treat all string/object fields as optional with default null.

### 2. `phase2_maker_pilot.json`

Maker pilot stats. May show `pilot_active: false` when the pilot hasn't been turned on yet (this is the current state as of 2026-04-14).

```json
{
  "updated_at": "2026-04-28T14:00:00+00:00",
  "pilot_active": true,
  "paired_observations_total": 52,
  "skipped_signals": 3,
  "fill_rate_pct": 82.7,
  "fallback_rate_pct": 17.3,
  "mean_fill_time_sec": 47.3,
  "mean_price_improvement_bps": 2.85,
  "p95_fallback_penalty_bps": 12.40,
  "max_single_fallback_usd": 14.20
}
```

When `pilot_active: false`, the entire maker pilot section of the tab should show "Maker pilot not yet active" placeholder. Don't render misleading zeros as if data were present.

### 3. `kill_switch_status.json`

Traffic-light status for protective kill switches. Status is one of `"green"`, `"yellow"`, `"red"`. Render with corresponding visual indicator.

```json
{
  "updated_at": "2026-04-21T14:00:00+00:00",
  "switches": [
    {
      "name": "dd_approach",
      "status": "green",
      "current_value": "2.10%",
      "threshold": "10.0%",
      "distance_to_trigger": "7.90pp"
    },
    {
      "name": "hwm_drift_detection",
      "status": "green",
      "current_value": "0 ticks with hwm_new > hwm_old",
      "threshold": "0 (invariant)",
      "invariant_holds": true
    }
  ]
}
```

The `switches` array is variable-length — render whatever switches the file contains. Handle unknown switch names gracefully (just show name + status + current_value).

### 4. `recent_events.json`

Rolling event feed. Most recent first. Up to 50 events.

```json
{
  "updated_at": "2026-04-21T14:00:00+00:00",
  "events": [
    {"ts": 1776239040000, "type": "skip_fired", "symbol": "ETH", "fee_avoided_usd": 2.45},
    {"ts": 1776238800000, "type": "maker_fill", "symbol": "BTC", "price_improvement_bps": 3.8},
    {"ts": 1776235200000, "type": "hwm_phantom_prevented", "dd_old_pct": 6.4, "dd_new_pct": 2.1},
    {"ts": 1776220000000, "type": "halt_triggered", "reason": "DD 11.2% from HWM $10500"}
  ]
}
```

Event types you'll encounter:
- `skip_fired` — Fix 1 skipped a redundant order
- `maker_fill` — paired BTC trade landed on maker (good)
- `maker_fallback` — maker timed out, fell back to taker
- `hwm_phantom_prevented` — old (buggy) HWM logic would have triggered, new logic didn't
- `halt_triggered` — circuit breaker fired
- `cooldown_cleared` — auto-reset cleared the kill flag

Format `ts` as a relative time ("3m ago", "2h ago") in the user's local timezone.

---

## Tab layout

### Top row — KPI tiles (4 wide)

| # | Tile | Source | Notes |
|---|---|---|---|
| 1 | **$ Saved vs Baseline** (big number, sparkline below) | `phase1_attribution.fees_avoided_usd` | Sparkline shows daily run-rate over time if you're tracking historically |
| 2 | **Days Live (Phase 1)** | `phase1_attribution.days_live` | Just the number |
| 3 | **Kill-Switch Status** (text summary like "4/4 green") | Aggregate of `kill_switch_status.switches` colors | Color the whole tile by worst status |
| 4 | **Maker Pilot Status** | `phase2_maker_pilot.pilot_active` + `paired_observations_total` | "Not active" or "47 trades" |

### Per-fix attribution cards (2x2 grid)

Four cards, one per fix. Each card displays:

**Fix 1 — SKIP fee leak**
- Skip events fired: `phase1_attribution.fix_1_skip_bug.skip_events_fired`
- $ saved: `fees_avoided_usd`
- Per-day rate: `per_day_rate_usd` ("$137/day")

**Fix 5 — HWM phantom peaks**
- Phantom triggers prevented: `phase1_attribution.fix_5_hwm.phantom_triggers_prevented`
- New HWM: `hwm_new_current`
- Old (shadow) HWM: `hwm_old_current`
- Render the gap between them with subtle visual

**Fix 3 — Halt behavior**
- CB events since ship: `phase1_attribution.fix_3_halt_behavior.cb_trigger_events_since_ship`
- If 0: green "No halts triggered"
- If >0: yellow with count (worth investigating)

**Fix 4 — Auto-reset cooldown**
- Auto-clear events: `phase1_attribution.fix_4_cooldown.auto_clear_events`
- Average duration: `avg_cooldown_duration_sec` (format as "2h 1m")

### Maker pilot section (only when `pilot_active: true`)

Card or wider panel showing:
- **Fill rate** — big % number with 65% kill-switch threshold marker
- **Fallback rate** — % with 40% warning marker
- **Mean price improvement** — bps with sign indicator (+ green, - red)
- **Mean fill time** — seconds, with comparison to base 300s timeout
- **p95 fallback penalty** — bps; red if >30
- **Max single fallback** — $; red if >$30 (kill switch trigger condition)

Render the underlying paired-observation count too: "Based on N paired observations".

### Kill-switch panel

Table or card grid. One row per switch from `kill_switch_status.switches`:

| Switch name | Status badge | Current value | Threshold | Distance |
|---|---|---|---|---|
| `dd_approach` | 🟢 GREEN | 2.10% | 10.0% | 7.90pp |
| `hwm_drift_detection` | 🟢 GREEN | 0 ticks | invariant | — |

Color rules:
- green: status === "green"
- yellow: status === "yellow"
- red: status === "red"
- unknown status: render gray with status text raw

### Recent events feed

Scrollable list, last 50 events from `recent_events.json`. Each row:
- Relative time ("3m ago")
- Event type (icon + label)
- Inline detail ("ETH, $2.45 saved")

Allow filtering by event type (multi-select chip filter).

---

## Visual style guidance

Match the existing dashboard style — I haven't seen it but assume you're consistent with the rest of the app. Some specifics:
- Use the same color palette as the other tabs
- KPI tiles should be the same size/proportion as existing tabs
- Tables should use existing table component
- Sparklines / mini charts: optional, only if cheap to add

If your dashboard already has a chart library (recharts, victory, etc.), reuse it. Don't introduce a new dependency for one sparkline.

---

## Empty / error states

The tab should NOT crash if any JSON file is missing or empty. Specific behaviors:

| Condition | Render |
|---|---|
| `phase1_attribution.json` missing | Show "Phase 1 monitoring data not available — check sync" warning, hide attribution cards |
| `phase2_maker_pilot.pilot_active: false` | Show "Maker pilot not yet active. Will populate once enabled in `risk/config.yaml`." |
| `kill_switch_status.switches` empty array | Show "No kill-switch data available" placeholder, hide the panel |
| `recent_events.events` empty array | Show "No recent events" |
| Any field missing | Render "—" or "0" (never crash) |

Cache + fall back gracefully on stale data — show last successful fetch timestamp prominently.

---

## VM sync (where the data files come from)

### Source location on VM

The trading machine produces these files:
- VM: `root@100.109.85.37:/home/openclaw/auto-researchtrading/`
  - JSONL event logs in: `live/logs/{skip_events,hwm_track,halt_events,cooldown_events,maker_pilot}_<date>.jsonl`
  - Aggregated JSON files in: `monitoring/{phase1_attribution,phase2_maker_pilot,kill_switch_status,recent_events}.json`

The aggregator script reads JSONL and writes JSON:

```bash
cd /home/openclaw/auto-researchtrading
uv run python -m monitoring.aggregate
```

This needs to run on a schedule (cron, every 15-30 min). It reads cheap and produces the 4 JSON files in `monitoring/`.

### Sync to dashboard

The dashboard needs these 4 JSON files served as static assets. You have two options:

**Option A (simplest) — push the JSONs into the dashboard's static path**

If your dashboard is built from a Git repo with a `public/data/` (or similar) folder, set up a sync from VM to that folder. Pattern matches the existing pattern that already pushes `results.tsv` (per `engine/backtest.py`'s `sync_to_vm` function in this repo, but in reverse).

Suggested approach: a cron job on the VM that scp's the 4 files to your dashboard host or commits them to the dashboard repo.

**Option B — serve from VM directly via HTTP**

Stand up a tiny HTTP server on the VM serving `monitoring/*.json`. Dashboard fetches from that URL at page load.

Pro: instant updates without needing a sync step.
Con: adds infra (HTTP server, public access, possibly auth).

Pick Option A unless you have a strong reason for B.

### Aggregator schedule (run on VM)

Add to crontab (as `openclaw` user):

```
*/15 * * * * cd /home/openclaw/auto-researchtrading && /home/openclaw/.local/bin/uv run python -m monitoring.aggregate >> live/logs/aggregator.log 2>&1
```

Runs every 15 minutes, takes <1 second, writes 4 JSON files atomically.

---

## Acceptance / done criteria

Before calling the tab "done":

- [ ] All 4 JSON files load correctly (test by manually populating each with valid data; tab should render)
- [ ] Tab handles every empty/error state without crashing
- [ ] Kill-switch traffic lights match underlying values (red when status="red", etc.)
- [ ] Recent events feed is sorted newest-first
- [ ] Refresh on page reload pulls latest data
- [ ] Existing dashboard tabs unaffected
- [ ] Mobile / narrow viewport doesn't break layout (degrade gracefully)

---

## Test data files

For local development, here are realistic sample JSONs you can put in your dashboard's `data/` folder and verify the UI renders:

**phase1_attribution.json (sample with realistic data after a few days):**
```json
{
  "updated_at": "2026-04-21T14:00:00+00:00",
  "phase1_ship_date": "2026-04-14T20:43:00+00:00",
  "days_live": 6.8,
  "fix_1_skip_bug": {"skip_events_fired": 412, "fees_avoided_usd": 654.30, "per_day_rate_usd": 96.22},
  "fix_5_hwm": {"ticks_logged": 1956, "hwm_new_current": 10180.50, "hwm_old_current": 10420.30, "phantom_triggers_prevented": 5},
  "fix_3_halt_behavior": {"cb_trigger_events_since_ship": 0},
  "fix_4_cooldown": {"auto_clear_events": 0, "avg_cooldown_duration_sec": 0},
  "sum_attributed_savings_usd": 654.30
}
```

**phase2_maker_pilot.json (when not yet active):**
```json
{"updated_at": "2026-04-21T14:00:00+00:00", "pilot_active": false, "paired_observations_total": 0}
```

**phase2_maker_pilot.json (active, after 1 week):**
```json
{
  "updated_at": "2026-04-28T14:00:00+00:00",
  "pilot_active": true,
  "paired_observations_total": 47,
  "skipped_signals": 3,
  "fill_rate_pct": 83.0,
  "fallback_rate_pct": 17.0,
  "mean_fill_time_sec": 52.4,
  "mean_price_improvement_bps": 2.91,
  "p95_fallback_penalty_bps": 11.20,
  "max_single_fallback_usd": 8.45
}
```

**kill_switch_status.json:**
```json
{
  "updated_at": "2026-04-21T14:00:00+00:00",
  "switches": [
    {"name": "dd_approach", "status": "green", "current_value": "1.85%", "threshold": "10.0%", "distance_to_trigger": "8.15pp"},
    {"name": "hwm_drift_detection", "status": "green", "current_value": "0 ticks", "threshold": "0 (invariant)", "invariant_holds": true}
  ]
}
```

**recent_events.json:**
```json
{
  "updated_at": "2026-04-21T14:00:00+00:00",
  "events": [
    {"ts": 1776239040000, "type": "skip_fired", "symbol": "ETH", "fee_avoided_usd": 2.45},
    {"ts": 1776238800000, "type": "skip_fired", "symbol": "BTC", "fee_avoided_usd": 1.82},
    {"ts": 1776235200000, "type": "hwm_phantom_prevented", "dd_old_pct": 6.4, "dd_new_pct": 2.1},
    {"ts": 1776220000000, "type": "skip_fired", "symbol": "SOL", "fee_avoided_usd": 1.30}
  ]
}
```

---

## Questions or blockers

If you hit something this doc doesn't cover, fall back to the source: `PLANNED_FIXES.md` in the trading repo has the full design rationale for each fix and the underlying logging schema. Specifically the "Fix 7" section has the original Vercel-side spec.

Anything genuinely unclear: ask the trading team. Don't guess — getting kill-switch colors wrong could mask a real incident.
